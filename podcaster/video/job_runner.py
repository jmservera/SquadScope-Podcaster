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

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from podcaster.config import PodcastConfig, SpotifyPublishConfig
from podcaster.failure_reporting import report_failure
from podcaster.generation import PODCAST_NAME, PODCAST_SPOKEN_SITE, _plain_text_from_html
from podcaster.music import TRACK_ATTRIBUTION
from podcaster.pipeline_lock import PIPELINE_VIDEO, claim_pipeline
from podcaster.queue import (
    QueueBackend,
    QueueMessage,
    QueueProducer,
    create_clip_queue_backend,
    parse_job_id,
)
from podcaster.sanitization import neutralize
from podcaster.storage import (
    ManagedIdentityTokenCredential,
    StorageBackend,
    create_scratch_storage_backend,
    create_storage_backend,
)
from podcaster.video.distribution import (
    DistributionResult,
    VideoDistributionConfig,
    distribute_video,
)
from podcaster.video.intermediates import create_intermediate_store
from podcaster.video.perf import PipelineTimings
from podcaster.video.sync_plan import (
    annotate_removed_repos,
    extract_repo_urls,
    extract_source_url,
    generate_episode_plan,
    generate_generic_plan,
    plan_from_realized_metadata,
    plan_from_script_timed,
    prepend_weekly_segment,
    removed_repo_speaker_notes,
    weekly_url_from_job_id,
)

logger = logging.getLogger("podcaster.video.job_runner")

VIDEO_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-video-queue-v1"

STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

REASON_ALREADY_PROCESSED = "already_processed"
REASON_NO_REPOS = "no_repos_in_script"
REASON_INVALID_PLAN = "invalid_plan"
REASON_COMPOSITION_FAILED = "composition_failed"
REASON_RETRY_EXHAUSTED = "retry_exhausted"
REASON_PIPELINE_CONFLICT = "pipeline_locked_by_audio"
REASON_EDITOR_LEASE_HELD = "editor_lease_held"
REASON_REQUIRED_YOUTUBE_FAILURE = "required_youtube_delivery_failed"

MAX_DEQUEUE_COUNT = 5

#: Off-switch for the scale-out fan-out recording path (RFC §3). When unset the
#: editor fans recording out across the ``video-clip-jobs`` queue **iff** both the
#: scratch container and the clip queue are configured; set to a falsey value to
#: force the legacy in-process ``record_episode`` path even when they are.
ENV_FANOUT = "PODCASTER_VIDEO_FANOUT"

#: Visibility timeout (seconds) the editor applies to its own ``video-jobs``
#: message while it works, so the job is not redelivered to a second editor
#: mid-run (RFC §8). Must be >= the editor's worst-case runtime (fan-in wait +
#: compose + publish); the dedicated editor lease is the backstop if it is too low.
ENV_VIDEO_VISIBILITY_TIMEOUT = "PODCASTER_VIDEO_VISIBILITY_TIMEOUT"
DEFAULT_VIDEO_VISIBILITY_TIMEOUT = 5400

# Minimum valid MP4 byte size
_MIN_VALID_MP4_BYTES = 1024

# Fallback music credit appended to the video description when the caller's
# request payload does not supply a ``description_template``.
_DEFAULT_MUSIC_CREDITS = f"Intro and Outro: {TRACK_ATTRIBUTION}"


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


class PermanentVideoError(RuntimeError):
    """A terminal failure that should stop queue retries for the message."""

    def __init__(self, message: str, *, reason: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = details or {}


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


def _load_pinned_article(
    manifest: dict[str, Any],
    storage: StorageBackend,
    job_id: str,
) -> str | None:
    request = manifest.get("request")
    if not isinstance(request, dict):
        return None
    replay = request.get("replay")
    if not isinstance(replay, dict):
        return None
    article_path = replay.get("article_path")
    article_sha256 = replay.get("article_sha256")
    if article_path is None:
        return None
    if not isinstance(article_path, str) or not isinstance(article_sha256, str):
        raise TransientVideoError(f"invalid pinned article metadata for job_id={job_id}")
    article_bytes = storage.get_bytes(article_path)
    if article_bytes is None:
        raise TransientVideoError(f"missing pinned article for job_id={job_id}")
    if hashlib.sha256(article_bytes).hexdigest() != article_sha256:
        raise TransientVideoError(f"pinned article hash mismatch for job_id={job_id}")
    return article_bytes.decode("utf-8", errors="replace")


def video_artifact_path(job_id: str) -> str:
    return f"jobs/{job_id}/video/{job_id}.mp4"


def show_notes_path(job_id: str) -> str:
    return f"jobs/{job_id}/show-notes.md"


def removed_repos_notes_path(job_id: str) -> str:
    """Storage path for speaker cues about repos removed from GitHub (issue #394)."""
    return f"jobs/{job_id}/video/removed-repos.md"


def _extract_section(notes: str, *heading_names: str) -> str:
    """Return the paragraph text of the first matching markdown section.

    Headings are matched case-insensitively against their text (ignoring the
    leading ``#`` markers). Collection stops at the next heading of any level so
    only the section body is returned.
    """
    wanted = {name.lower() for name in heading_names}
    collecting = False
    body: list[str] = []
    for line in notes.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if collecting:
                break
            collecting = stripped.lstrip("#").strip().lower() in wanted
            continue
        if collecting:
            body.append(stripped)
    return "\n".join(body).strip()


def _extract_hosts(notes: str) -> str:
    """Return the host credit text from a ``**Hosts:**`` line, if present."""
    for line in notes.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("**hosts:**"):
            return stripped[len("**Hosts:**") :].strip()
    return ""


def _is_generic_episode_summary(summary: str) -> bool:
    normalized = " ".join(summary.split()).lower()
    return (
        "squadscope curated articles" in normalized
        or "this episode covers key developments" in normalized
        or "[topic to be added from source article]" in normalized
        or "editorial synopsis pending" in normalized
        or (
            normalized.startswith("this ")
            and " episode explores " in normalized
            and "highlighting the open-source developments, repo activity, "
            "and practical signals that matter this week"
            in normalized
        )
    )


def _resolve_spotify_publish_config(request: dict[str, Any]) -> SpotifyPublishConfig | None:
    spotify_publish = request.get("spotify_publish")
    if not isinstance(spotify_publish, dict):
        return None
    return SpotifyPublishConfig.from_payload({"spotify_publish": spotify_publish})


def _sanitize_preferred_description(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    description = _plain_text_from_html(value)
    return description or None


def _resolve_video_title(
    request: dict[str, Any],
    *,
    brand_name: str,
    job_id: str,
) -> tuple[str, bool]:
    """Resolve video title from the same publish metadata the audio path uses."""
    spotify_config = _resolve_spotify_publish_config(request)
    if spotify_config is not None and spotify_config.title.strip():
        return spotify_config.title.strip(), False

    article_title = request.get("article_title")
    if isinstance(article_title, str) and article_title.strip():
        return article_title.strip(), False

    return f"{brand_name} Podcast — {job_id}", True


def _build_video_description(
    storage: StorageBackend,
    job_id: str,
    fallback: str,
    music_credits: str | None = None,
    show_name: str | None = None,
    spoken_site: str | None = None,
    preferred_description: str | None = None,
) -> str:
    """Build the Spotify/YouTube video description from the episode show-notes.

    Reads ``jobs/{job_id}/show-notes.md`` (issue #363), extracts the episode
    summary and host credits, and appends the podcast name and website so the
    published video draft carries the same metadata as the audio episode.
    Falls back to ``fallback`` when show-notes are unavailable.

    ``show_name``/``spoken_site`` source the brand credit line from the per-job
    ``request.podcast_config`` (issue #545); when omitted the module defaults
    (``PODCAST_NAME``/``PODCAST_SPOKEN_SITE``) are used.

    ``preferred_description`` is the already-resolved audio publish description.
    It is used when show-notes are absent or contain an old deterministic
    placeholder, keeping the video episode metadata aligned with the audio
    episode. ``music_credits`` is appended after the credits line so the video
    description matches the audio episode structure (summary + credits + music
    attribution). When omitted, the default music attribution constant is used.
    """
    brand_name = (show_name or "").strip() or PODCAST_NAME
    brand_site = (spoken_site or "").strip() or PODCAST_SPOKEN_SITE
    attribution = (music_credits or _DEFAULT_MUSIC_CREDITS).strip()
    fallback_text = (
        _sanitize_preferred_description(preferred_description)
        or neutralize(fallback, limit=600).strip()
    )

    def _with_attribution(base: str) -> str:
        return f"{base}\n\n{attribution}" if attribution else base

    raw = storage.get_bytes(show_notes_path(job_id))
    if not raw:
        return _with_attribution(fallback_text)
    try:
        notes = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return _with_attribution(fallback_text)
    if not notes:
        return _with_attribution(fallback_text)

    summary = _extract_section(notes, "About this episode", "Show notes")
    if not summary or _is_generic_episode_summary(summary):
        summary = fallback_text

    credit_parts: list[str] = []
    hosts = _extract_hosts(notes)
    if hosts:
        credit_parts.append(f"Hosts: {hosts}")
    credit_parts.append(f"{brand_name} — {brand_site}")
    credits = "Credits: " + " · ".join(credit_parts)

    body = f"{summary}\n\n{credits}" if summary else credits
    return _with_attribution(body)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_week_str(week_str: str) -> tuple[int, int] | None:
    """Parse an ISO week string like ``2026-W24`` into ``(year, week)``.

    Returns ``None`` when the string is not in the expected format or the
    year-week combination is not a valid ISO calendar date.
    """
    if "-W" not in week_str:
        return None
    try:
        year_part, week_part = week_str.split("-W", 1)
        year, week = int(year_part), int(week_part)
        # Validate using the calendar — raises ValueError for invalid combos
        # (e.g. week 53 in a year that only has 52 weeks).
        import datetime as _dt

        _dt.date.fromisocalendar(year, week, 1)
    except ValueError:
        return None
    return year, week


def _manifest_week_str(manifest: dict[str, Any]) -> str:
    """Return the raw week string from ``manifest.request.week``, or ``""``."""
    request = manifest.get("request")
    if not isinstance(request, dict):
        return ""
    value = request.get("week")
    return value if isinstance(value, str) else ""


def _extract_year(manifest: dict[str, Any]) -> int | None:
    """Extract the ISO year from ``request.week`` (e.g. ``2026-W24`` → 2026)."""
    parsed = _parse_week_str(_manifest_week_str(manifest))
    return parsed[0] if parsed is not None else None


def _extract_week(manifest: dict[str, Any]) -> int | None:
    """Extract the ISO week number from ``request.week`` (e.g. ``2026-W24`` → 24)."""
    parsed = _parse_week_str(_manifest_week_str(manifest))
    return parsed[1] if parsed is not None else None


def _already_processed(manifest: dict[str, Any]) -> bool:
    """Check if video has already been generated for this job."""
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        return False
    video_state = generation.get("video_runner")
    return isinstance(video_state, dict) and video_state.get("status") == STATUS_COMPLETED


def _persist_removed_repo_notes(
    storage: StorageBackend,
    job_id: str,
    plan,
) -> list[str]:
    """Persist speaker cues for repos removed from GitHub (issue #394).

    Writes a markdown artifact of host cues (one per removed repo) so the hosts
    can comment on why each project was taken down.  No-op when no repo was
    flagged removed.  Best-effort: storage failures are logged, never raised, so
    they don't abort video generation.  Returns the notes (for logging/tests).
    """
    notes = removed_repo_speaker_notes(plan)
    if not notes:
        return []
    body = (
        "# Removed repos — speaker cues (issue #394)\n\n"
        + "\n".join(f"- {note}" for note in notes)
        + "\n"
    )
    try:
        storage.put_bytes(
            removed_repos_notes_path(job_id),
            body.encode("utf-8"),
            "text/markdown; charset=utf-8",
        )
    except Exception:
        logger.warning("failed to persist removed-repo notes for job_id=%s", job_id, exc_info=True)
    logger.info(
        "job_id=%s: %d repo(s) removed from GitHub — speaker cues generated",
        job_id,
        len(notes),
    )
    return notes


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


def _record_video_publish(
    storage: StorageBackend,
    job_id: str,
    platform: str,
    record: dict[str, Any],
) -> None:
    """Record one durable per-platform video publish result in the manifest."""
    from podcaster.generation import manifest_bytes

    def _apply(content: bytes | None) -> bytes:
        doc = json.loads(content.decode("utf-8")) if content else {}
        if not isinstance(doc, dict):
            doc = {}
        generation = doc.setdefault("generation", {})
        video_publish = generation.setdefault("video_publish", {})
        if not isinstance(video_publish, dict):
            video_publish = {}
            generation["video_publish"] = video_publish
        video_publish[platform] = record
        return manifest_bytes(doc)

    try:
        storage.update_bytes(manifest_path(job_id), "application/json; charset=utf-8", _apply)
    except Exception:
        logger.warning(
            "failed to record video publish state for job_id=%s platform=%s",
            job_id,
            platform,
            exc_info=True,
        )


def realized_audio_metadata_path(job_id: str) -> str:
    """Blob path for the Layer 2 realized-audio-metadata document (#553)."""
    return f"jobs/{job_id}/realized_audio_metadata.json"


def _load_realized_metadata(manifest: dict[str, Any], job_id: str, storage: StorageBackend):
    """Load Layer 2 realized audio metadata persisted at synthesis time (#553).

    Returns a :class:`~podcaster.audio_metadata.RealizedAudioMetadata` when the
    synthesis runner wrote one (deterministic repo/section timing derived from
    the measured TTS clip durations), else ``None`` so the caller falls back to
    mention-based proportional timing. Never raises — a missing/corrupt blob just
    degrades to the fallback planner.
    """
    path = None
    generation = manifest.get("generation")
    if isinstance(generation, dict):
        runner_state = generation.get("synthesis_runner")
        if isinstance(runner_state, dict):
            candidate = runner_state.get("realized_audio_metadata_path")
            if isinstance(candidate, str) and candidate:
                path = candidate
    if path is None:
        path = realized_audio_metadata_path(job_id)
    try:
        raw = storage.get_bytes(path)
    except Exception:  # noqa: BLE001 — metadata is best-effort
        logger.warning("failed to read realized metadata blob %s", path, exc_info=True)
        return None
    if raw is None:
        return None
    try:
        from podcaster.audio_metadata import RealizedAudioMetadata

        document = json.loads(raw.decode("utf-8"))
        payload = document.get("metadata") if isinstance(document, dict) else None
        if not isinstance(payload, dict):
            return None
        return RealizedAudioMetadata.from_dict(payload)
    except Exception:  # noqa: BLE001 — corrupt metadata must not abort video
        logger.warning("invalid realized metadata blob %s; using fallback timing", path)
        return None


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


def _probe_audio_duration(audio_path: Path) -> float | None:
    """Probe the duration (seconds) of an audio file via ffprobe.

    Returns ``None`` on any probe failure so callers fall back to the manifest
    value or the default. Reading the real MP3 duration here lets the segment
    plan match the actual podcast length (issue #353).
    """
    import subprocess

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        duration = float((proc.stdout or "").strip())
    except (OSError, ValueError, subprocess.CalledProcessError):
        logger.warning("failed to probe audio duration for %s", audio_path, exc_info=True)
        return None
    return duration if duration > 0 else None


def _env_flag(env_value: str | None, *, default: bool) -> bool:
    """Parse a truthy/falsey env string, falling back to *default* when unset."""
    if env_value is None or not env_value.strip():
        return default
    return env_value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_fanout(
    fanout: bool | None,
    scratch: StorageBackend | None,
    producer: QueueProducer | None,
) -> bool:
    """Decide whether the scale-out fan-out recording path is active.

    Fan-out needs both a scratch container (for clipset/clips) and a clip queue
    (to dispatch recorders). When both are present the explicit *fanout* override
    wins; otherwise the ``PODCASTER_VIDEO_FANOUT`` off-switch gates it (default on).
    """
    if scratch is None or producer is None:
        return False
    if fanout is not None:
        return bool(fanout)
    return _env_flag(os.environ.get(ENV_FANOUT), default=True)


def _release_editor_lease(scratch: StorageBackend | None, job_id: str, run_id: str | None) -> None:
    """Release the editor lease when fan-out is active (no-op otherwise)."""
    if scratch is None or run_id is None:
        return
    from podcaster.video.editor import release_lease

    release_lease(scratch, job_id, run_id)


def _video_visibility_timeout(env: dict[str, str] | None = None) -> int:
    """Editor ``video-jobs`` receive visibility timeout (seconds) (RFC §8)."""
    source = env if env is not None else os.environ
    raw = source.get(ENV_VIDEO_VISIBILITY_TIMEOUT, "")
    if not raw.strip():
        return DEFAULT_VIDEO_VISIBILITY_TIMEOUT
    try:
        value = int(raw.strip())
    except ValueError:
        return DEFAULT_VIDEO_VISIBILITY_TIMEOUT
    return value if value > 0 else DEFAULT_VIDEO_VISIBILITY_TIMEOUT


def run_video_generation(
    job_id: str,
    storage: StorageBackend,
    *,
    config: VideoDistributionConfig | None = None,
    now: datetime | None = None,
    compose_runner=None,
    fanout: bool | None = None,
    fanout_scratch: StorageBackend | None = None,
    clip_producer: QueueProducer | None = None,
) -> VideoOutcome:
    """Generate video for a staged job_id and distribute to configured targets.

    Pipeline: load manifest → parse script → plan segments → compose → distribute.

    When the scale-out fan-out is enabled (``fanout``; default: auto when a scratch
    container and clip queue are configured) the recording phase is fanned out
    across the ``video-clip-jobs`` queue and the editor blocks on the fan-in
    barrier instead of recording inline (RFC §3). The compose/distribute path is
    unchanged. ``fanout_scratch`` / ``clip_producer`` are injectable for tests.
    """
    current = now or datetime.now(timezone.utc)
    dist_config = config or VideoDistributionConfig.from_env()

    # Blob-backed checkpoint/resume store for intermediates (issue #410). When no
    # scratch container is configured (local dev / tests) this is disabled and
    # every operation is a no-op, preserving the legacy all-local-disk path.
    intermediates = create_intermediate_store(job_id)

    # Resolve the scale-out fan-out: enabled iff both the scratch container and
    # the clip queue are available and the off-switch is not set (RFC §3).
    scratch = fanout_scratch if fanout_scratch is not None else create_scratch_storage_backend()
    producer = clip_producer if clip_producer is not None else create_clip_queue_backend()
    fanout_enabled = _resolve_fanout(fanout, scratch, producer)
    run_id = uuid.uuid4().hex if fanout_enabled else None

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

    # Claim pipeline lock — prevent concurrent audio synthesis on same job
    if not claim_pipeline(storage, job_id, PIPELINE_VIDEO, now=current):
        logger.info("video skipped job_id=%s reason=%s", job_id, REASON_PIPELINE_CONFLICT)
        return VideoOutcome(job_id, STATUS_SKIPPED, reason=REASON_PIPELINE_CONFLICT)

    # Load script
    raw_script = storage.get_bytes(script_path(job_id))
    if raw_script is None:
        raise TransientVideoError(f"no script for job_id={job_id}")
    script = raw_script.decode("utf-8")
    pinned_article = _load_pinned_article(manifest, storage, job_id)
    sections_metadata = _load_sections_metadata(storage, job_id)

    # The target duration drives the segment plan. We prefer the REAL podcast
    # MP3 duration (probed below, inside the temp dir) so the video length
    # matches the audio; the manifest value is only a fallback (issue #353).
    manifest_audio_duration = _get_audio_duration(manifest)

    # Acquire the dedicated editor execution lease (RFC §6.2) immediately before
    # the expensive plan/record/compose work. ``pipeline_lock`` alone permits a
    # same-pipeline re-confirm, so a second editor for one job_id could otherwise
    # proceed; an unexpired foreign lease makes us no-op. Acquired here (rather
    # than earlier) so every exit path below runs through the ``try`` whose
    # handlers release the lease — a transient failure must not block a retry for
    # the full lease TTL.
    if fanout_enabled and run_id is not None:
        from podcaster.video.editor import acquire_or_renew_lease

        if not acquire_or_renew_lease(scratch, job_id, run_id, now=current):
            logger.info("video skipped job_id=%s reason=%s", job_id, REASON_EDITOR_LEASE_HELD)
            return VideoOutcome(job_id, STATUS_SKIPPED, reason=REASON_EDITOR_LEASE_HELD)

    # Compose video
    # TODO(#242): dispatch parallel ACA segment jobs instead of sequential local recording.
    # Current implementation records locally; production should fan out to container instances.
    try:
        with tempfile.TemporaryDirectory(prefix="video_job_") as tmp:
            from podcaster.progress import PipelineStage, make_task_reporter
            from podcaster.video.video_compose import compose_video
            from podcaster.video.video_gen import record_episode

            # Per-worker task progress for the parallel normalize stage (#482):
            # each segment surfaces as a norm_NNN task in the durable progress
            # document so overlapping workers stay individually observable.
            normalize_reporter = make_task_reporter(storage, job_id, stage=PipelineStage.COMPOSE)

            # Branded intro/outro (#586): seed the standard branded bumpers into
            # storage before composing so the real pipeline always uses them and
            # they are never hidden by a stale title-card clip (which also
            # swallows the pre-first-repo bridge, #588). Graceful no-op when the
            # branded assets are not staged in the asset dir.
            from podcaster.video.intro_outro import ensure_branded_intro_outro

            try:
                ensure_branded_intro_outro(storage)
            except Exception:  # noqa: BLE001 - seeding is best-effort, never break compose
                logger.warning(
                    "branded intro/outro seeding failed for job_id=%s; composing with "
                    "whatever bumpers are already stored/cached",
                    job_id,
                    exc_info=True,
                )

            output_dir = Path(tmp)

            # Per-phase timing/resource instrumentation for the performance
            # review (issue #396).  Persisted into the manifest so before/after
            # comparisons need no re-instrumentation.
            timings = PipelineTimings()

            # Resolve the podcast audio first so the segment plan is driven by
            # the actual MP3 duration rather than the manifest default.
            audio_path = _resolve_audio_path(manifest, job_id, storage, output_dir)
            audio_duration: float | None = None
            if audio_path is not None:
                audio_duration = _probe_audio_duration(audio_path)
            if audio_duration is None:
                audio_duration = manifest_audio_duration
            if audio_duration is None:
                audio_duration = 300.0  # Default 5 minutes if no audio info
                logger.warning(
                    "no audio duration available, defaulting to %.0fs for job_id=%s",
                    audio_duration,
                    job_id,
                )

            # Build the episode plan. The deterministic path consumes the Layer 2
            # realized audio metadata persisted at synthesis (#486/#553): repo and
            # section timing come from the measured TTS clip durations — each repo
            # appears when the hosts name it, with the intro-music speech offset
            # and inter-segment gap already baked in. This replaces the whisper
            # forced-alignment path (#374/#544) and its silent ACA cache
            # degradation (#551). When no metadata is present (legacy jobs), fall
            # back to mention-based proportional timing (issue #355) — never
            # whisper. Scripts without GitHub repo URLs produce a generic
            # background plan (issue #335) instead of being skipped.
            realized_metadata = _load_realized_metadata(manifest, job_id, storage)
            weekly_url = None if pinned_article is not None else weekly_url_from_job_id(job_id)
            used_metadata_plan = False
            try:
                if realized_metadata is not None and realized_metadata.topics:
                    plan = plan_from_realized_metadata(
                        realized_metadata,
                        audio_duration,
                        weekly_url=weekly_url,
                        source_url=extract_source_url(script) if pinned_article is None else None,
                    )
                    used_metadata_plan = True
                    logger.info(
                        "video plan from realized metadata job_id=%s topics=%d",
                        job_id,
                        len(realized_metadata.topics),
                    )
                else:
                    script_repos = extract_repo_urls(script)
                    pinned_repos = (
                        extract_repo_urls(pinned_article) if pinned_article is not None else []
                    )
                    if not script_repos and pinned_article is not None:
                        if pinned_repos:
                            plan = generate_episode_plan(pinned_repos, audio_duration)
                            logger.info(
                                "video plan from pinned replay article job_id=%s repos=%d",
                                job_id,
                                len(pinned_repos),
                            )
                        else:
                            plan = generate_generic_plan(audio_duration)
                            logger.info(
                                "video plan uses generic background for pinned replay job_id=%s",
                                job_id,
                            )
                    else:
                        plan = plan_from_script_timed(script, audio_duration)
                        logger.info(
                            "video plan from mention-based timing (no realized metadata) job_id=%s",
                            job_id,
                        )
            except ValueError as exc:
                logger.warning(
                    "video skipped job_id=%s reason=%s: %s",
                    job_id,
                    REASON_INVALID_PLAN,
                    exc,
                )
                _record_video_state(
                    storage,
                    job_id,
                    {
                        "status": STATUS_SKIPPED,
                        "reason": REASON_INVALID_PLAN,
                        "at": _iso(current),
                    },
                )
                _release_editor_lease(scratch, job_id, run_id)
                return VideoOutcome(job_id, STATUS_SKIPPED, reason=REASON_INVALID_PLAN)

            # Show the claracle.com weekly page (derived from the job_id) as the
            # first content segment, right after the intro and before any repo is
            # discussed (issue #382). On the metadata path the leading ``article``
            # topic already covers that pre-repo range (carrying the weekly URL),
            # so prepending again would double-insert it (#553) — skip it.
            if not used_metadata_plan:
                plan = prepend_weekly_segment(
                    plan,
                    job_id,
                    use_live_source=pinned_article is None,
                )

            # Pre-flight each repo URL (HEAD) so repos GitHub has removed (e.g. a
            # polymarket/spam bot like ``mktail``) are detected before recording.
            # Removed repos get a "Repo removed" card instead of a wasted
            # navigation, and speaker cues are persisted so the hosts can comment
            # on why the project is gone (issue #394).
            plan = annotate_removed_repos(plan)
            _persist_removed_repo_notes(storage, job_id, plan)

            # Record segments. Pass the script's Source URL so failed repo
            # navigations can be retried and corrected against the source
            # article before falling back to a generic screen (issue #378).
            # Resolve the configured show/site name up front so generic
            # background cards are branded with it, never the internal pipeline
            # name (issue #559).
            _brand_request = manifest.get("request")
            _brand_config = PodcastConfig.from_payload(
                _brand_request if isinstance(_brand_request, dict) else None
            )
            brand_name = _brand_config.name
            with timings.phase("recording"):
                if fanout_enabled and run_id is not None:
                    from podcaster.video.editor import (
                        acquire_or_renew_lease,
                        record_via_fanout,
                    )

                    def _heartbeat() -> None:
                        # Renew the editor lease while parked on the fan-in barrier
                        # so a redelivered editor keeps seeing an unexpired lease.
                        # Use a FRESH timestamp each beat (not the run-start time)
                        # so a barrier longer than the lease TTL keeps extending it.
                        # If renewal fails another editor now owns the lease — abort
                        # immediately (per the acquire_or_renew_lease contract) rather
                        # than risk a concurrent compose/publish for the same job_id.
                        # Raising TransientVideoError leaves the message for redelivery;
                        # our CAS release is a no-op since the successor owns the lease.
                        if not acquire_or_renew_lease(scratch, job_id, run_id):
                            logger.warning(
                                "editor lease lost during fan-in job_id=%s run_id=%s; "
                                "aborting (another editor took over)",
                                job_id,
                                run_id,
                            )
                            raise TransientVideoError(f"editor lease lost for job_id={job_id}")

                    logger.info(
                        "video fan-out recording job_id=%s segments=%d run_id=%s",
                        job_id,
                        len(plan.segments),
                        run_id,
                    )
                    recording = record_via_fanout(
                        job_id,
                        plan.segments,
                        output_dir,
                        scratch=scratch,
                        producer=producer,
                        heartbeat=_heartbeat,
                    )
                else:
                    recording = record_episode(
                        plan,
                        output_dir=output_dir,
                        headless=True,
                        source_url=extract_source_url(script) if pinned_article is None else None,
                        intermediates=intermediates,
                        brand_name=brand_name,
                    )
            # Compose final MP4
            output_path = output_dir / f"{job_id}.mp4"
            dog_logo_cfg = _resolve_dog_logo(manifest)

            # Section title cards between editorial sections (issue #377).
            # Dormant + graceful: when the script has no section headers (the
            # current default) no cards are produced and composition is
            # unchanged.  Disable explicitly with VIDEO_SECTION_CARDS=0.
            section_cards = _build_section_cards(
                script,
                recording.recorded,
                output_dir,
                sections_metadata=sections_metadata,
            )

            with timings.phase("composition"):
                compose_result = compose_video(
                    recording.recorded,
                    audio_path=audio_path,
                    output_path=output_path,
                    runner=compose_runner,
                    storage=storage,
                    generic_brand_name=brand_name,
                    dog_logo=dog_logo_cfg,
                    audio_duration=audio_duration,
                    section_cards=section_cards,
                    intermediates=intermediates,
                    task_reporter=normalize_reporter,
                )

            if not output_path.exists() or output_path.stat().st_size < _MIN_VALID_MP4_BYTES:
                raise RuntimeError(f"composition produced invalid output for job_id={job_id}")

            # Distribute
            request = manifest.get("request")
            if not isinstance(request, dict):
                request = {}
            # Source the episode title from the same publish metadata the audio
            # episode uses, then fall back to article_title/defaults. Log only
            # when all listener-facing title metadata is genuinely absent.
            title, used_default_title = _resolve_video_title(
                request,
                brand_name=brand_name,
                job_id=job_id,
            )
            if used_default_title:
                logger.warning(
                    "article_title absent in manifest request for job_id=%s; "
                    "using default title %r (supply request.article_title to "
                    "override, issue #545)",
                    job_id,
                    title,
                )
            # Resolve the podcast identity from the per-job config so the brand
            # credit line honors request.podcast_config rather than the module
            # defaults; absent fields fall back to defaults (logged above, #545).
            podcast_config = PodcastConfig.from_payload(request)
            if not PodcastConfig.payload_provides_identity(request):
                logger.warning(
                    "podcast_config identity absent in manifest request for "
                    "job_id=%s; the episode uses default show name %r and hosts "
                    "%r/%r — supply request.podcast_config (name/host_a/host_b) "
                    "to override (issue #545)",
                    job_id,
                    podcast_config.name,
                    podcast_config.host_a.name,
                    podcast_config.host_b.name,
                )
            spotify_publish_config = _resolve_spotify_publish_config(request)
            fallback_description = str(
                request.get("description", f"Video podcast episode {job_id}")
            )
            preferred_description = (
                _sanitize_preferred_description(spotify_publish_config.description)
                if spotify_publish_config is not None and spotify_publish_config.description.strip()
                else None
            )
            if preferred_description is None:
                article_summary = request.get("article_summary")
                preferred_description = _sanitize_preferred_description(article_summary)
            template = request.get("description_template")
            music_credits = template if isinstance(template, str) and template.strip() else None
            description = _build_video_description(
                storage,
                job_id,
                fallback_description,
                music_credits=music_credits,
                show_name=podcast_config.name,
                spoken_site=podcast_config.spoken_site,
                preferred_description=preferred_description,
            )

            season_number = _extract_year(manifest)
            episode_number = _extract_week(manifest)
            job_language = str(request.get("language", "en"))
            generation = manifest.get("generation")
            published = None
            if isinstance(generation, dict) and isinstance(generation.get("video_publish"), dict):
                published = generation["video_publish"]

            with timings.phase("distribution"):
                dist_result = distribute_video(
                    output_path,
                    job_id,
                    title,
                    description,
                    compose_result.duration_seconds,
                    dist_config,
                    storage=_StorageUploaderAdapter(storage),
                    spotify_anchor_id=_resolve_anchor_id(manifest),
                    season_number=season_number,
                    episode_number=episode_number,
                    language=job_language,
                    published=published,
                    on_published=lambda platform, record: _record_video_publish(
                        storage,
                        job_id,
                        platform,
                        record,
                    ),
                )

            if dist_result.youtube_required_failed:
                distribution_state = {
                    "status": dist_result.status,
                    "youtube_id": dist_result.youtube_id,
                    "blob_path": dist_result.blob_path,
                    "spotify_rss_updated": dist_result.spotify_rss_updated,
                    "spotify_upload_updated": dist_result.spotify_upload_updated,
                    "youtube_required_failed": dist_result.youtube_required_failed,
                    "youtube_failure_retryable": dist_result.youtube_failure_retryable,
                    "youtube_failure_code": dist_result.youtube_failure_code,
                    "youtube_failure_stage": dist_result.youtube_failure_stage,
                    "youtube_failure_http_status": dist_result.youtube_failure_http_status,
                    "youtube_oauth_error": dist_result.youtube_oauth_error,
                    "youtube_oauth_error_subtype": dist_result.youtube_oauth_error_subtype,
                }
                _record_video_state(
                    storage,
                    job_id,
                    {
                        "status": STATUS_FAILED,
                        "reason": REASON_REQUIRED_YOUTUBE_FAILURE,
                        "at": _iso(current),
                        "performance": timings.to_dict(),
                        "distribution": distribution_state,
                    },
                )
                message = (
                    f"required YouTube delivery failed for job_id={job_id} "
                    f"code={dist_result.youtube_failure_code or 'unknown'} "
                    f"stage={dist_result.youtube_failure_stage or 'unknown'} "
                    f"retryable={dist_result.youtube_failure_retryable}"
                )
                if dist_result.youtube_failure_retryable:
                    raise TransientVideoError(message)
                raise PermanentVideoError(
                    message,
                    reason=REASON_REQUIRED_YOUTUBE_FAILURE,
                    details={
                        "job_id": job_id,
                        "youtube_failure_code": dist_result.youtube_failure_code,
                        "youtube_failure_stage": dist_result.youtube_failure_stage,
                        "youtube_failure_http_status": dist_result.youtube_failure_http_status,
                        "youtube_oauth_error": dist_result.youtube_oauth_error,
                        "youtube_oauth_error_subtype": dist_result.youtube_oauth_error_subtype,
                    },
                )

            # Emit the per-phase timing/resource breakdown (issue #396).
            timings.log_summary(logger)

            # Record success in manifest
            _record_video_state(
                storage,
                job_id,
                {
                    "status": STATUS_COMPLETED,
                    "at": _iso(current),
                    "segment_count": compose_result.segment_count,
                    "duration_seconds": compose_result.duration_seconds,
                    "performance": timings.to_dict(),
                    "distribution": {
                        "status": dist_result.status,
                        "youtube_id": dist_result.youtube_id,
                        "blob_path": dist_result.blob_path,
                        "spotify_rss_updated": dist_result.spotify_rss_updated,
                        "spotify_upload_updated": dist_result.spotify_upload_updated,
                    },
                },
            )

            # Intermediates are no longer needed once the episode is published;
            # delete the job's scratch blobs (issue #410).  Best-effort — the
            # 7-day lifecycle policy on the scratch container is the safety net.
            intermediates.cleanup()

            # Fan-out scratch (clipset + per-clip blobs) is likewise spent once the
            # compose succeeded; delete it and release the editor lease (RFC §5).
            if fanout_enabled and run_id is not None:
                from podcaster.video.editor import cleanup_clips

                cleanup_clips(scratch, job_id)
            _release_editor_lease(scratch, job_id, run_id)

            return VideoOutcome(
                job_id=job_id,
                status=STATUS_COMPLETED,
                video_blob_path=dist_result.blob_path,
                segment_count=compose_result.segment_count,
                distribution=dist_result,
            )

    except TransientVideoError:
        _release_editor_lease(scratch, job_id, run_id)
        raise
    except PermanentVideoError:
        _release_editor_lease(scratch, job_id, run_id)
        raise
    except Exception as exc:
        # ffmpeg/ffprobe failures surface as CalledProcessError whose stderr
        # carries the real cause (e.g. "No space left on device").  The default
        # ``logger.exception`` only records the exception type, so that root
        # cause was being silently discarded — log the failing command and its
        # captured stderr so failures are diagnosable from the container logs.
        if isinstance(exc, subprocess.CalledProcessError):
            cmd = exc.cmd
            cmd_str = " ".join(map(str, cmd)) if isinstance(cmd, (list, tuple)) else str(cmd)
            stderr = exc.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            logger.error(
                "video subprocess failed job_id=%s rc=%s cmd=%s stderr=%s",
                job_id,
                exc.returncode,
                cmd_str,
                (stderr or "").strip(),
            )
        logger.exception("video generation failed job_id=%s error=%s", job_id, type(exc).__name__)
        _record_video_state(
            storage,
            job_id,
            {
                "status": STATUS_FAILED,
                "reason": type(exc).__name__,
                "at": _iso(current),
            },
        )
        _release_editor_lease(scratch, job_id, run_id)
        raise TransientVideoError(f"video generation failed for job_id={job_id}") from exc


def _resolve_anchor_id(manifest: dict[str, Any]) -> int | None:
    """Resolve the Spotify anchor episode id from the audio publish result (#337).

    Prefers ``generation.publish_result.anchor_id`` and falls back to the
    canonical publish location ``publishing.result.anchor_episode_id``.
    Returns ``None`` when no anchor id is recorded.
    """
    candidates: list[Any] = []
    generation = manifest.get("generation")
    if isinstance(generation, dict):
        publish_result = generation.get("publish_result")
        if isinstance(publish_result, dict):
            candidates.append(publish_result.get("anchor_id"))

    publishing = manifest.get("publishing")
    if isinstance(publishing, dict):
        result = publishing.get("result")
        if isinstance(result, dict):
            candidates.append(result.get("anchor_episode_id"))

    for value in candidates:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _resolve_dog_logo(manifest: dict[str, Any]):
    """Build a DogLogoConfig from ``request.podcast_config.dog_logo`` if present.

    Returns ``None`` when the manifest carries no ``dog_logo`` config so the
    composition skips the watermark (graceful degradation).
    """
    from podcaster.video.video_compose import DogLogoConfig

    request = manifest.get("request")
    if not isinstance(request, dict):
        return None
    podcast_config = request.get("podcast_config")
    if not isinstance(podcast_config, dict):
        return None
    return DogLogoConfig.from_dict(podcast_config.get("dog_logo"))


def _load_sections_metadata(storage: StorageBackend, job_id: str) -> list[dict[str, Any]]:
    """Load persisted ``sections.json`` metadata when present."""
    raw = storage.get_bytes(f"jobs/{job_id}/sections.json")
    if raw is None:
        return []
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        logger.warning("invalid sections metadata for job_id=%s; using script fallback", job_id)
        return []
    if isinstance(doc, dict) and isinstance(doc.get("sections"), list):
        return [item for item in doc["sections"] if isinstance(item, dict)]
    if isinstance(doc, list):
        return [item for item in doc if isinstance(item, dict)]
    return []


def _section_card_duration_seconds(sections_metadata: list[dict[str, Any]]) -> float:
    """Return section-card duration from metadata, clamped to issue #417 bounds."""
    from podcaster.sections import DEFAULT_TITLE_CARD_DURATION_SECONDS

    for section in sections_metadata:
        title_card = section.get("title_card")
        if not isinstance(title_card, dict):
            continue
        duration = title_card.get("duration_seconds")
        if isinstance(duration, (int, float)):
            return min(1.0, max(0.5, float(duration)))
    return DEFAULT_TITLE_CARD_DURATION_SECONDS


def _build_section_cards(
    script: str,
    recorded,
    output_dir: Path,
    *,
    sections_metadata: list[dict[str, Any]] | None = None,
):
    """Build section title card inserts for the recorded content (issue #377).

    Detects editorial section headers in *script*, maps each to the recorded
    segment that opens it, and renders a brief title card per section.  Fully
    graceful: returns an empty list when the feature is disabled, when no
    sections are detected (the current default for plain-dialogue scripts), or
    when card generation fails for any reason — composition then proceeds
    unchanged.

    Controlled by the ``VIDEO_SECTION_CARDS`` env var (default enabled; set to
    ``0``/``false``/``no`` to disable).
    """
    flag = os.environ.get("VIDEO_SECTION_CARDS", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return []

    try:
        from podcaster.video.section_cards import SectionCardConfig, build_section_card_inserts

        segment_repo_urls = [
            rec.segment.repo.url if rec.segment.repo is not None else None for rec in recorded
        ]
        duration_seconds = _section_card_duration_seconds(sections_metadata or [])
        config = SectionCardConfig(duration_ms=int(round(duration_seconds * 1000)))
        return build_section_card_inserts(
            script,
            segment_repo_urls,
            output_dir / "section_cards",
            config=config,
        )
    except Exception:  # pragma: no cover - defensive: cards must never block video
        logger.exception("section title card generation failed; continuing without cards")
        return []


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
            message.message_id,
            message.dequeue_count,
        )
        queue.delete_message(message)
        return VideoOutcome("", STATUS_FAILED, reason="malformed_message")

    logger.info(
        "processing video message job_id=%s dequeue_count=%s",
        job_id,
        message.dequeue_count,
    )

    try:
        outcome = run_video_generation(job_id, storage, config=config, now=now)
    except PermanentVideoError as exc:
        logger.error("terminal video failure job_id=%s reason=%s", job_id, exc.reason)
        details: dict[str, Any] = {"job_id": job_id, "reason": exc.reason}
        details.update(exc.details)
        report_failure(
            container="podcaster-video",
            error_type="PermanentVideoFailure",
            error_message=str(exc),
            details=details,
        )
        queue.delete_message(message)
        return VideoOutcome(job_id, STATUS_FAILED, reason=exc.reason)
    except TransientVideoError:
        if message.dequeue_count >= MAX_DEQUEUE_COUNT:
            logger.error("video retry exhausted job_id=%s", job_id)
            report_failure(
                container="podcaster-video",
                error_type="RetryExhausted",
                error_message=(
                    f"Video generation failed after {message.dequeue_count} attempts "
                    f"for job_id={job_id}"
                ),
                details={"job_id": job_id, "dequeue_count": message.dequeue_count},
            )
            queue.delete_message(message)
            return VideoOutcome(job_id, STATUS_FAILED, reason=REASON_RETRY_EXHAUSTED)
        logger.warning(
            "leaving video message for retry job_id=%s dequeue_count=%s",
            job_id,
            message.dequeue_count,
        )
        return VideoOutcome(job_id, STATUS_FAILED, reason="transient")

    # An unexpired foreign editor lease means another editor already owns this
    # job. Leave the message for redelivery (do NOT delete) so the job is not
    # lost if that editor later crashes — once its lease expires a retry can
    # proceed. The message stays invisible until its visibility timeout elapses.
    if outcome.status == STATUS_SKIPPED and outcome.reason == REASON_EDITOR_LEASE_HELD:
        logger.info(
            "leaving video message for redelivery job_id=%s reason=%s",
            job_id,
            REASON_EDITOR_LEASE_HELD,
        )
        return outcome

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
    # Hold each ``video-jobs`` message invisible for the editor's worst-case
    # runtime (fan-in wait + compose + publish) so the job is not redelivered to a
    # second editor mid-run; the dedicated editor lease is the backstop (RFC §8).
    visibility_timeout = _video_visibility_timeout()
    for _ in range(max_messages):
        messages = queue.receive_messages(max_messages=1, visibility_timeout=visibility_timeout)
        if not messages:
            break
        for message in messages:
            outcomes.append(process_message(message, storage=storage, queue=queue, config=config))
    return outcomes


def main() -> int:
    """Entry point for the video ACA container job."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from podcaster.queue import create_video_queue_backend

    queue = create_video_queue_backend()
    if queue is None:
        logger.error(
            "video queue not configured; set PODCASTER_STORAGE_QUEUE_URL or "
            "AZURE_STORAGE_CONNECTION_STRING to consume video queue"
        )
        return 2
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
        len(outcomes),
        completed,
        skipped,
        failed,
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


if __name__ == "__main__":
    raise SystemExit(main())
