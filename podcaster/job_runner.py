"""Queue-consuming synthesis job runner for the ACA Job (#67/#78, ADR 0001).

This runs *out-of-band* from the thin ``/api/generate`` Functions front door:
a message carrying a ``job_id`` lands on the synthesis Storage Queue, KEDA
starts this job, and the runner drives the **existing** :mod:`podcaster.episode`
pipeline (parse -> gated synthesis -> stitch -> ``loudnorm`` -> ``ffprobe``
validate) to replace the staged placeholder MP3 with real two-voice audio.

Safety / gating invariants (unchanged by this runner):

* The human editorial-review gate is preserved: the runner **never** marks an
  episode publication-eligible. ``publishing.eligible`` stays ``False`` and
  ``human_review`` remains in ``publishing.blocked_by`` no matter what.
* Identity-only data plane (Blob + Queue + Azure OpenAI). No keys, tokens, SAS
  URLs, or untrusted article text are ever logged.
* Idempotent on duplicate queue delivery: a job already marked synthesized is
  skipped instead of re-synthesized.
* Scripts that are not in the two-voice synthesis format (e.g. the deterministic
  placeholder staged before production generation is wired) are skipped, not
  failed, so the message is consumed and the episode stays ``review_pending``.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from podcaster.audio import MusicMixSpec
from podcaster.config import MusicMixConfig, PodcastConfig
from podcaster.episode import (
    operator_review_decision,
    parse_script_segments,
    synthesize_episode,
)
from podcaster.generation import checksum, manifest_bytes
from podcaster.queue import QueueBackend, QueueMessage, create_queue_backend, parse_job_id
from podcaster.storage import StorageBackend, create_storage_backend
from podcaster.tts import PROVIDER, TtsConfig, load_tts_config

logger = logging.getLogger("podcaster.job_runner")

SYNTHESIS_SCHEMA_VERSION = "squadscope-podcaster-synthesis-runner-v1"

STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

REASON_ALREADY_SYNTHESIZED = "already_synthesized"
REASON_NOT_TWO_VOICE = "script_not_two_voice_format"
REASON_TTS_NOT_CONFIGURED = "tts_not_configured"
REASON_RETRY_EXHAUSTED = "retry_exhausted"

MAX_DEQUEUE_COUNT = 5


@dataclass(frozen=True)
class SynthesisOutcome:
    """Result of attempting synthesis for one ``job_id``."""

    job_id: str
    status: str
    reason: str | None = None
    audio_sha256: str | None = None
    segment_count: int | None = None
    validation_status: str | None = None


class TransientSynthesisError(RuntimeError):
    """A failure that should leave the queue message for retry."""


def manifest_path(job_id: str) -> str:
    return f"jobs/{job_id}/manifest.json"


def script_path(job_id: str) -> str:
    return f"jobs/{job_id}/script.txt"


def run_synthesis(
    job_id: str,
    storage: StorageBackend,
    config: TtsConfig,
    *,
    now: datetime | None = None,
    transport=None,
    token_provider=None,
    runner=None,
) -> SynthesisOutcome:
    """Synthesize real audio for a staged ``job_id`` and update its manifest.

    Reuses :func:`podcaster.episode.synthesize_episode` unchanged. ``transport``,
    ``token_provider``, and ``runner`` are injectable for tests; in production
    they default to the managed-identity HTTP/ffmpeg paths.
    """

    current = now or datetime.now(timezone.utc)
    raw_manifest = storage.get_bytes(manifest_path(job_id))
    if raw_manifest is None:
        raise TransientSynthesisError(f"no staged manifest for job_id={job_id}")

    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TransientSynthesisError(f"staged manifest for job_id={job_id} is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise TransientSynthesisError(f"staged manifest for job_id={job_id} is not a JSON object")

    if _already_synthesized(manifest):
        logger.info("synthesis skipped job_id=%s reason=%s", job_id, REASON_ALREADY_SYNTHESIZED)
        return SynthesisOutcome(job_id, STATUS_SKIPPED, reason=REASON_ALREADY_SYNTHESIZED)

    if not config.production_ready:
        logger.warning("synthesis skipped job_id=%s reason=%s", job_id, REASON_TTS_NOT_CONFIGURED)
        _record_runner_state(
            storage,
            job_id,
            {"status": STATUS_SKIPPED, "reason": REASON_TTS_NOT_CONFIGURED, "at": _iso(current)},
        )
        return SynthesisOutcome(job_id, STATUS_SKIPPED, reason=REASON_TTS_NOT_CONFIGURED)

    raw_script = storage.get_bytes(script_path(job_id))
    if raw_script is None:
        raise TransientSynthesisError(f"no staged script for job_id={job_id}")
    script = raw_script.decode("utf-8")

    if not parse_script_segments(script):
        logger.info("synthesis skipped job_id=%s reason=%s", job_id, REASON_NOT_TWO_VOICE)
        _record_runner_state(
            storage,
            job_id,
            {"status": STATUS_SKIPPED, "reason": REASON_NOT_TWO_VOICE, "at": _iso(current)},
        )
        return SynthesisOutcome(job_id, STATUS_SKIPPED, reason=REASON_NOT_TWO_VOICE)

    decision = operator_review_decision(config)
    if not decision.get("allowed"):
        logger.warning("synthesis skipped job_id=%s reason=%s", job_id, REASON_TTS_NOT_CONFIGURED)
        _record_runner_state(
            storage,
            job_id,
            {"status": STATUS_SKIPPED, "reason": REASON_TTS_NOT_CONFIGURED, "at": _iso(current)},
        )
        return SynthesisOutcome(job_id, STATUS_SKIPPED, reason=REASON_TTS_NOT_CONFIGURED)

    mp3_blob_path = _mp3_artifact_path(manifest, job_id)
    request_podcast_config = _request_podcast_config(manifest)
    podcast_config = PodcastConfig.from_payload(request_podcast_config) if request_podcast_config else None
    music_mix_config = _request_music_mix(manifest)
    mix_spec = _build_mix_spec(music_mix_config)
    # TODO(#169): music_mix_config may specify a track name but no intro_music/outro_music
    # file paths are resolved or passed to synthesize_episode(). Until file resolution
    # is implemented, music mixing is effectively a no-op. Log a warning so callers
    # know the track was requested but not applied.
    if mix_spec:
        logger.warning(
            "music_mix_config specifies track=%r but no music file paths are available; "
            "music mixing will be skipped for job_id=%s",
            music_mix_config.track,
            job_id,
        )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / f"{job_id}.mp3"
            episode_audio = synthesize_episode(
                script,
                config,
                decision,
                output_path,
                podcast_config=podcast_config,
                token_provider=token_provider,
                transport=transport,
                runner=runner,
                music_mix_spec=mix_spec,
            )
            audio_bytes = output_path.read_bytes()
    except Exception as exc:
        logger.exception("synthesis failed job_id=%s error=%s", job_id, type(exc).__name__)
        _record_runner_state(
            storage,
            job_id,
            {"status": STATUS_FAILED, "reason": type(exc).__name__, "at": _iso(current)},
        )
        raise TransientSynthesisError(f"synthesis failed for job_id={job_id}") from exc

    stored = storage.put_bytes(mp3_blob_path, audio_bytes, "audio/mpeg")
    audio_sha256 = checksum(audio_bytes)
    voices = sorted({voice for voice in episode_audio.voices if voice})

    def _apply(content: bytes | None) -> bytes:
        document = json.loads(content.decode("utf-8")) if content else manifest
        if not isinstance(document, dict):
            document = manifest
        _apply_completion(
            document,
            job_id=job_id,
            audio_path=stored.path,
            audio_sha256=audio_sha256,
            audio_size=stored.size_bytes,
            segment_count=episode_audio.segment_count,
            voices=voices,
            validation=episode_audio.validation.to_manifest(),
            validation_ready=episode_audio.validation.ready,
            config_summary=config.safe_summary(),
            completed_at=_iso(current),
        )
        return manifest_bytes(document)

    storage.update_bytes(manifest_path(job_id), "application/json; charset=utf-8", _apply)

    logger.info(
        "synthesis completed job_id=%s segments=%s validation=%s real_audio=true publishable=false",
        job_id,
        episode_audio.segment_count,
        episode_audio.validation.status,
    )
    return SynthesisOutcome(
        job_id,
        STATUS_COMPLETED,
        audio_sha256=audio_sha256,
        segment_count=episode_audio.segment_count,
        validation_status=episode_audio.validation.status,
    )


def _request_podcast_config(manifest: dict[str, Any]) -> dict[str, Any] | None:
    request = manifest.get("request")
    if not isinstance(request, dict):
        return None
    podcast_config = request.get("podcast_config")
    if not isinstance(podcast_config, dict):
        return None
    return {"podcast_config": podcast_config}


def _request_music_mix(manifest: dict[str, Any]) -> MusicMixConfig:
    """Extract the ``music_mix`` config from the manifest's request payload."""

    request = manifest.get("request")
    if not isinstance(request, dict):
        return MusicMixConfig()
    return MusicMixConfig.from_payload(request)


def _build_mix_spec(config: MusicMixConfig) -> MusicMixSpec | None:
    """Convert a :class:`MusicMixConfig` to a :class:`MusicMixSpec`, or None if no track."""

    if not config.has_track:
        return None
    return MusicMixSpec(**config.to_mix_spec_kwargs())


def process_message(
    message: QueueMessage,
    *,
    storage: StorageBackend,
    queue: QueueBackend,
    config: TtsConfig,
    now: datetime | None = None,
) -> SynthesisOutcome:
    """Process one queue message: synthesize, then delete on a terminal outcome.

    Completed and skipped outcomes delete the message (work is done / not
    retryable). Transient failures leave the message for redelivery until
    ``MAX_DEQUEUE_COUNT`` is reached, then the message is treated as poison and
    deleted. A message that cannot yield a ``job_id`` is poison and is deleted
    after logging.
    """

    try:
        job_id = parse_job_id(message.body)
    except ValueError:
        logger.error(
            "discarding malformed synthesis message message_id=%s dequeue_count=%s",
            message.message_id,
            message.dequeue_count,
        )
        queue.delete_message(message)
        return SynthesisOutcome("", STATUS_FAILED, reason="malformed_message")

    logger.info(
        "processing synthesis message job_id=%s dequeue_count=%s",
        job_id,
        message.dequeue_count,
    )
    logger.info(
        "synthesis audit event=start job_id=%s message_id=%s dequeue_count=%s",
        job_id,
        message.message_id,
        message.dequeue_count,
    )
    try:
        outcome = run_synthesis(job_id, storage, config, now=now)
    except TransientSynthesisError:
        if message.dequeue_count >= MAX_DEQUEUE_COUNT:
            logger.error(
                "synthesis audit event=failure job_id=%s reason=%s dequeue_count=%s terminal=true",
                job_id,
                REASON_RETRY_EXHAUSTED,
                message.dequeue_count,
            )
            queue.delete_message(message)
            return SynthesisOutcome(job_id, STATUS_FAILED, reason=REASON_RETRY_EXHAUSTED)
        logger.warning(
            "synthesis audit event=failure job_id=%s reason=transient dequeue_count=%s terminal=false",
            job_id,
            message.dequeue_count,
        )
        logger.warning(
            "leaving synthesis message for retry job_id=%s dequeue_count=%s",
            job_id,
            message.dequeue_count,
        )
        return SynthesisOutcome(job_id, STATUS_FAILED, reason="transient")

    logger.info(
        "synthesis audit event=%s job_id=%s status=%s reason=%s terminal=true",
        "success" if outcome.status == STATUS_COMPLETED else "skipped",
        job_id,
        outcome.status,
        outcome.reason,
    )
    queue.delete_message(message)
    return outcome


def drain(
    queue: QueueBackend,
    storage: StorageBackend,
    config: TtsConfig,
    *,
    max_messages: int = 32,
) -> list[SynthesisOutcome]:
    """Process queued synthesis messages until the queue is empty or capped."""

    outcomes: list[SynthesisOutcome] = []
    for _ in range(max_messages):
        messages = queue.receive_messages(max_messages=1)
        if not messages:
            break
        for message in messages:
            outcomes.append(process_message(message, storage=storage, queue=queue, config=config))
    return outcomes


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    queue = create_queue_backend()
    if queue is None:
        logger.error("PODCASTER_STORAGE_QUEUE_URL is not configured; cannot consume synthesis queue")
        return 2
    storage = create_storage_backend()
    config = load_tts_config()
    outcomes = drain(queue, storage, config)
    completed = sum(1 for outcome in outcomes if outcome.status == STATUS_COMPLETED)
    skipped = sum(1 for outcome in outcomes if outcome.status == STATUS_SKIPPED)
    failed = sum(1 for outcome in outcomes if outcome.status == STATUS_FAILED)
    logger.info(
        "synthesis run finished processed=%s completed=%s skipped=%s failed=%s",
        len(outcomes),
        completed,
        skipped,
        failed,
    )
    return 1 if failed else 0


def _already_synthesized(manifest: dict[str, Any]) -> bool:
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        return False
    state = generation.get("synthesis_runner")
    return isinstance(state, dict) and state.get("status") == STATUS_COMPLETED


def _mp3_artifact_path(manifest: dict[str, Any], job_id: str) -> str:
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict):
        for path in artifacts:
            if isinstance(path, str) and path.endswith(".mp3"):
                return path
    return f"jobs/{job_id}/audio/{job_id}.mp3"


def _apply_completion(
    manifest: dict[str, Any],
    *,
    job_id: str,
    audio_path: str,
    audio_sha256: str,
    audio_size: int,
    segment_count: int,
    voices: list[str],
    validation: dict[str, Any],
    validation_ready: bool,
    config_summary: dict[str, Any],
    completed_at: str,
) -> None:
    generation = manifest.setdefault("generation", {})
    if not isinstance(generation, dict):
        generation = manifest["generation"] = {}
    generation["engine"] = "azure-openai-tts-aca-job"
    generation["deterministic"] = False
    generation["audio_mode"] = "synthesized"
    generation["tts_provider"] = PROVIDER
    generation["tts_voice"] = voices
    generation["tts_synthesis"] = {
        "status": "completed",
        "allowed": True,
        "blocked_by": ["human_review"],
        "dry_run_bypass_allowed": False,
    }
    generation["audio_validation"] = validation
    generation["synthesis_runner"] = {
        "schema_version": SYNTHESIS_SCHEMA_VERSION,
        "status": STATUS_COMPLETED,
        "job_id": job_id,
        "completed_at": completed_at,
        "segment_count": segment_count,
        "voices": voices,
        "provider": PROVIDER,
        "config": config_summary,
        "audio": {
            "path": audio_path,
            "sha256": audio_sha256,
            "size_bytes": audio_size,
        },
    }

    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict) and isinstance(artifacts.get(audio_path), dict):
        entry = artifacts[audio_path]
        entry["sha256"] = audio_sha256
        entry["size_bytes"] = audio_size
        entry["content_type"] = "audio/mpeg"
        entry["publicly_accessible"] = False

    publishing = manifest.setdefault("publishing", {})
    if isinstance(publishing, dict):
        # The human-review gate is unchanged: synthesized audio is never
        # publication-eligible from the runner.
        publishing["eligible"] = False
        blocked = publishing.get("blocked_by")
        blocked_set = set(blocked) if isinstance(blocked, list) else set()
        blocked_set.discard("real_tts_not_implemented")
        blocked_set.add("human_review")
        if validation_ready:
            blocked_set.discard("audio_validation_not_passed")
        else:
            blocked_set.add("audio_validation_not_passed")
        publishing["blocked_by"] = sorted(blocked_set)
        checks = publishing.get("readiness_checks")
        if isinstance(checks, dict):
            checks["real_audio_available"] = True

    lifecycle = manifest.get("lifecycle")
    if isinstance(lifecycle, dict):
        transitions = lifecycle.get("transitions")
        if isinstance(transitions, list):
            transitions.append(
                {"at": completed_at, "to": "review_pending", "reason": "audio_synthesized"}
            )
        revision = lifecycle.get("revision")
        lifecycle["revision"] = (revision + 1) if isinstance(revision, int) else 2


def _record_runner_state(storage: StorageBackend, job_id: str, state: dict[str, Any]) -> None:
    """Record a non-completion runner state without ever making output publishable."""

    def _apply(content: bytes | None) -> bytes:
        if content is None:
            raise TransientSynthesisError(f"no staged manifest for job_id={job_id}")
        document = json.loads(content.decode("utf-8"))
        if not isinstance(document, dict):
            raise TransientSynthesisError(f"staged manifest for job_id={job_id} is not a JSON object")
        generation = document.setdefault("generation", {})
        if isinstance(generation, dict):
            generation["synthesis_runner"] = {"schema_version": SYNTHESIS_SCHEMA_VERSION, **state}
        publishing = document.get("publishing")
        if isinstance(publishing, dict):
            publishing["eligible"] = False
        return manifest_bytes(document)

    try:
        storage.update_bytes(manifest_path(job_id), "application/json; charset=utf-8", _apply)
    except Exception:  # noqa: BLE001 - recording a marker must never mask the real outcome
        logger.warning("could not record synthesis runner state job_id=%s", job_id)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
