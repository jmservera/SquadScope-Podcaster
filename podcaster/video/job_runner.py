"""Queue-consuming video generation job runner for ACA container jobs (#242).

Follows the same pattern as the audio synthesis job runner
(podcaster.job_runner): a Storage Queue message carries a job_id, KEDA starts
this container, and the runner drives video generation as parallel segment jobs
then composes and distributes the final MP4.

Video generation pipeline:
1. Dequeue message → extract job_id
2. Load manifest + script from blob storage
3. Parse script → extract repos → generate episode plan
4. Generate video segments (parallel ACA jobs or local)
5. Compose segments into final MP4 (ffmpeg)
6. Distribute to YouTube, Spotify RSS, and blob archive
7. Update manifest with video artifacts

Identity-only data plane (Blob + Queue). No keys, tokens, or secrets logged.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from podcaster.queue import (
    QueueBackend,
    QueueMessage,
    create_queue_backend,
    parse_job_id,
)
from podcaster.storage import (
    ManagedIdentityTokenCredential,
    StorageBackend,
    create_storage_backend,
)
from podcaster.failure_reporting import report_failure
from podcaster.video.distribution import (
    DistributionResult,
    StorageUploader,
    VideoDistributionConfig,
    distribute_video,
)
from podcaster.video.sync_plan import EpisodePlan, plan_from_script

logger = logging.getLogger("podcaster.video.job_runner")

VIDEO_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-video-queue-v1"

STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

REASON_ALREADY_PROCESSED = "already_processed"
REASON_NO_REPOS = "no_repos_in_script"
REASON_COMPOSITION_FAILED = "composition_failed"
REASON_RETRY_EXHAUSTED = "retry_exhausted"

MAX_DEQUEUE_COUNT = 5

# Minimum valid MP4 byte size
_MIN_VALID_MP4_BYTES = 1024


@dataclass(frozen=True)
class VideoOutcome:
    """Result of attempting video generation for one job_id."""

    job_id: str
    status: str
    reason: str | None = None
    video_blob_path: str | None = None
    segment_count: int | None = None
    distribution: DistributionResult | None = None


class TransientVideoError(RuntimeError):
    """A failure that should leave the queue message for retry."""


class _StorageUploaderAdapter:
    """Adapts StorageBackend (put_bytes → StoredArtifact) to StorageUploader (upload → URL)."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def upload(self, path: str, content: bytes, content_type: str) -> str:
        artifact = self._backend.put_bytes(path, content, content_type)
        return artifact.url


def manifest_path(job_id: str) -> str:
    return f"jobs/{job_id}/manifest.json"


def script_path(job_id: str) -> str:
    return f"jobs/{job_id}/script.txt"


def video_artifact_path(job_id: str) -> str:
    return f"jobs/{job_id}/video/{job_id}.mp4"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _already_processed(manifest: dict[str, Any]) -> bool:
    """Check if video has already been generated for this job."""
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        return False
    video_state = generation.get("video_runner")
    return isinstance(video_state, dict) and video_state.get("status") == STATUS_COMPLETED


def _record_video_state(
    storage: StorageBackend,
    job_id: str,
    state: dict[str, Any],
) -> None:
    """Record video runner state in the manifest."""
    from podcaster.generation import manifest_bytes

    def _apply(content: bytes | None) -> bytes:
        doc = json.loads(content.decode("utf-8")) if content else {}
        if not isinstance(doc, dict):
            doc = {}
        generation = doc.setdefault("generation", {})
        generation["video_runner"] = state
        return manifest_bytes(doc)

    try:
        storage.update_bytes(manifest_path(job_id), "application/json; charset=utf-8", _apply)
    except Exception:
        logger.warning("failed to record video state for job_id=%s", job_id, exc_info=True)


def _get_audio_duration(manifest: dict[str, Any]) -> float | None:
    """Extract audio duration from manifest if available."""
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        return None
    validation = generation.get("validation")
    if isinstance(validation, dict):
        duration = validation.get("duration_seconds")
        if isinstance(duration, (int, float)) and duration > 0:
            return float(duration)
    return None


def run_video_generation(
    job_id: str,
    storage: StorageBackend,
    *,
    config: VideoDistributionConfig | None = None,
    now: datetime | None = None,
    compose_runner=None,
) -> VideoOutcome:
    """Generate video for a staged job_id and distribute to configured targets.

    Pipeline: load manifest → parse script → plan segments → compose → distribute.
    """
    current = now or datetime.now(timezone.utc)
    dist_config = config or VideoDistributionConfig.from_env()

    # Load manifest
    raw_manifest = storage.get_bytes(manifest_path(job_id))
    if raw_manifest is None:
        raise TransientVideoError(f"no manifest for job_id={job_id}")

    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TransientVideoError(f"invalid manifest for job_id={job_id}") from exc

    if not isinstance(manifest, dict):
        raise TransientVideoError(f"manifest for job_id={job_id} is not a dict")

    # Check idempotency
    if _already_processed(manifest):
        logger.info("video skipped job_id=%s reason=%s", job_id, REASON_ALREADY_PROCESSED)
        return VideoOutcome(job_id, STATUS_SKIPPED, reason=REASON_ALREADY_PROCESSED)

    # Load script
    raw_script = storage.get_bytes(script_path(job_id))
    if raw_script is None:
        raise TransientVideoError(f"no script for job_id={job_id}")
    script = raw_script.decode("utf-8")

    # Determine duration
    audio_duration = _get_audio_duration(manifest)
    if audio_duration is None:
        audio_duration = 300.0  # Default 5 minutes if no audio info
        logger.warning("no audio duration in manifest, defaulting to %.0fs for job_id=%s",
                       audio_duration, job_id)

    # Parse script and generate plan
    try:
        plan = plan_from_script(script, audio_duration)
    except ValueError as exc:
        logger.warning("video skipped job_id=%s reason=%s: %s", job_id, REASON_NO_REPOS, exc)
        _record_video_state(storage, job_id, {
            "status": STATUS_SKIPPED, "reason": REASON_NO_REPOS, "at": _iso(current),
        })
        return VideoOutcome(job_id, STATUS_SKIPPED, reason=REASON_NO_REPOS)

    # Compose video
    # TODO(#242): dispatch parallel ACA segment jobs instead of sequential local recording.
    # Current implementation records locally; production should fan out to container instances.
    try:
        with tempfile.TemporaryDirectory(prefix="video_job_") as tmp:
            from podcaster.video.video_compose import compose_video
            from podcaster.video.video_gen import record_episode

            output_dir = Path(tmp)

            # Record segments
            recording = record_episode(plan, output_dir=output_dir, headless=True)

            # Get audio track path if available
            audio_path = _resolve_audio_path(manifest, job_id, storage, output_dir)

            # Compose final MP4
            output_path = output_dir / f"{job_id}.mp4"
            compose_result = compose_video(
                recording.recorded,
                audio_path=audio_path,
                output_path=output_path,
                runner=compose_runner,
            )

            if not output_path.exists() or output_path.stat().st_size < _MIN_VALID_MP4_BYTES:
                raise RuntimeError(f"composition produced invalid output for job_id={job_id}")

            # Distribute
            request = manifest.get("request", {})
            title = str(request.get("article_title", f"SquadScope Podcast — {job_id}"))
            description = str(request.get("description", f"Video podcast episode {job_id}"))

            dist_result = distribute_video(
                output_path,
                job_id,
                title,
                description,
                compose_result.duration_seconds,
                dist_config,
                storage=_StorageUploaderAdapter(storage),
            )

            # Record success in manifest
            _record_video_state(storage, job_id, {
                "status": STATUS_COMPLETED,
                "at": _iso(current),
                "segment_count": compose_result.segment_count,
                "duration_seconds": compose_result.duration_seconds,
                "distribution": {
                    "status": dist_result.status,
                    "youtube_id": dist_result.youtube_id,
                    "blob_path": dist_result.blob_path,
                    "spotify_rss_updated": dist_result.spotify_rss_updated,
                },
            })

            return VideoOutcome(
                job_id=job_id,
                status=STATUS_COMPLETED,
                video_blob_path=dist_result.blob_path,
                segment_count=compose_result.segment_count,
                distribution=dist_result,
            )

    except TransientVideoError:
        raise
    except Exception as exc:
        logger.exception("video generation failed job_id=%s error=%s", job_id, type(exc).__name__)
        _record_video_state(storage, job_id, {
            "status": STATUS_FAILED, "reason": type(exc).__name__, "at": _iso(current),
        })
        raise TransientVideoError(f"video generation failed for job_id={job_id}") from exc


def _resolve_audio_path(
    manifest: dict[str, Any],
    job_id: str,
    storage: StorageBackend,
    output_dir: Path,
) -> Path | None:
    """Download the episode audio from blob storage if available."""
    # Check manifest for MP3 path
    artifacts = manifest.get("artifacts")
    mp3_path = None
    if isinstance(artifacts, dict):
        for path in artifacts:
            if isinstance(path, str) and path.endswith(".mp3"):
                mp3_path = path
                break
    if mp3_path is None:
        mp3_path = f"jobs/{job_id}/audio/{job_id}.mp3"

    audio_bytes = storage.get_bytes(mp3_path)
    if audio_bytes is None:
        logger.info("no audio track available for video composition job_id=%s", job_id)
        return None

    local_audio = output_dir / "audio.mp3"
    local_audio.write_bytes(audio_bytes)
    return local_audio


def process_message(
    message: QueueMessage,
    *,
    storage: StorageBackend,
    queue: QueueBackend,
    config: VideoDistributionConfig | None = None,
    now: datetime | None = None,
) -> VideoOutcome:
    """Process one video queue message: generate, then delete on terminal outcome."""
    try:
        job_id = parse_job_id(message.body)
    except ValueError:
        logger.error(
            "discarding malformed video message message_id=%s dequeue_count=%s",
            message.message_id, message.dequeue_count,
        )
        queue.delete_message(message)
        return VideoOutcome("", STATUS_FAILED, reason="malformed_message")

    logger.info("processing video message job_id=%s dequeue_count=%s", job_id, message.dequeue_count)

    try:
        outcome = run_video_generation(job_id, storage, config=config, now=now)
    except TransientVideoError:
        if message.dequeue_count >= MAX_DEQUEUE_COUNT:
            logger.error("video retry exhausted job_id=%s", job_id)
            report_failure(
                container="podcaster-video",
                error_type="RetryExhausted",
                error_message=f"Video generation failed after {message.dequeue_count} attempts for job_id={job_id}",
                details={"job_id": job_id, "dequeue_count": message.dequeue_count},
            )
            queue.delete_message(message)
            return VideoOutcome(job_id, STATUS_FAILED, reason=REASON_RETRY_EXHAUSTED)
        logger.warning("leaving video message for retry job_id=%s dequeue_count=%s",
                       job_id, message.dequeue_count)
        return VideoOutcome(job_id, STATUS_FAILED, reason="transient")

    queue.delete_message(message)
    return outcome


def drain(
    queue: QueueBackend,
    storage: StorageBackend,
    config: VideoDistributionConfig | None = None,
    *,
    max_messages: int = 32,
) -> list[VideoOutcome]:
    """Process queued video messages until the queue is empty or capped."""
    outcomes: list[VideoOutcome] = []
    for _ in range(max_messages):
        messages = queue.receive_messages(max_messages=1)
        if not messages:
            break
        for message in messages:
            outcomes.append(process_message(message, storage=storage, queue=queue, config=config))
    return outcomes


def main() -> int:
    """Entry point for the video ACA container job."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    queue_url = os.environ.get("PODCASTER_STORAGE_QUEUE_URL")
    queue_name = os.environ.get("PODCASTER_VIDEO_QUEUE", "video-jobs")

    if not queue_url:
        logger.error("PODCASTER_STORAGE_QUEUE_URL is not configured; cannot consume video queue")
        return 2

    from podcaster.queue import AzureStorageQueueBackend
    queue = AzureStorageQueueBackend(queue_url, queue_name)
    storage = create_storage_backend()
    config = VideoDistributionConfig.from_env()

    # Health check managed identity
    if os.environ.get("PODCASTER_STORAGE_ACCOUNT_URL"):
        try:
            ManagedIdentityTokenCredential().get_token("https://storage.azure.com/.default")
        except Exception:
            logger.exception("managed identity token startup health check failed")
            return 3

    outcomes = drain(queue, storage, config)
    completed = sum(1 for o in outcomes if o.status == STATUS_COMPLETED)
    skipped = sum(1 for o in outcomes if o.status == STATUS_SKIPPED)
    failed = sum(1 for o in outcomes if o.status == STATUS_FAILED)

    logger.info(
        "video run finished processed=%s completed=%s skipped=%s failed=%s",
        len(outcomes), completed, skipped, failed,
    )

    if failed:
        failed_jobs = [o.job_id for o in outcomes if o.status == STATUS_FAILED and o.job_id]
        report_failure(
            container="podcaster-video",
            error_type="VideoRunFailure",
            error_message=f"{failed} of {len(outcomes)} video jobs failed",
            details={"failed_jobs": failed_jobs, "completed": completed, "skipped": skipped},
        )

    return 1 if failed else 0
