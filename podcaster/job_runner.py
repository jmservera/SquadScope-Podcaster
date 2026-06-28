"""Queue-consuming synthesis job runner for the ACA Job (#67/#78, ADR 0001).

This runs *out-of-band* from the thin ``/api/generate`` Functions front door:
a message carrying a ``job_id`` lands on the synthesis Storage Queue, KEDA
starts this job, and the runner drives the **existing** :mod:`podcaster.episode`
pipeline (parse -> gated synthesis -> stitch -> ``loudnorm`` -> ``ffprobe``
validate) to replace the staged placeholder MP3 with real two-voice audio.

* After successful synthesis with passing audio validation, the episode is
  published as a Spotify draft when ``spotify_publish`` config is present in
  the request payload. Publish failures never break the pipeline.
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
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from podcaster.audio import MusicMixSpec
from podcaster.config import BackchannelConfig, MusicMixConfig, PodcastConfig, SpotifyPublishConfig
from podcaster.episode import (
    operator_review_decision,
    parse_script_segments,
    synthesize_episode,
)
from podcaster.failure_reporting import report_failure
from podcaster.generation import checksum, manifest_bytes
from podcaster.job_logs import LogLevel, emit_log
from podcaster.notifications import notify_failure
from podcaster.orchestration import auto_publish_enabled, auto_publish_job
from podcaster.pipeline_lock import PIPELINE_AUDIO, claim_pipeline
from podcaster.progress import PipelineStage, emit_progress
from podcaster.publish import PublishResult, publish_episode
from podcaster.queue import (
    QueueBackend,
    QueueMessage,
    create_queue_backend,
    enqueue_video_job,
    parse_job_id,
)
from podcaster.storage import ManagedIdentityTokenCredential, StorageBackend, create_storage_backend
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
REASON_PIPELINE_CONFLICT = "pipeline_locked_by_video"

MAX_DEQUEUE_COUNT = 5

# Minimum byte size for a valid MP3 — an MP3 frame header alone is 4 bytes,
# but any real audio will be substantially larger. Use 256 bytes as a floor
# to catch empty or corrupt outputs from ffmpeg that exit 0 without error.
_MIN_VALID_MP3_BYTES = 256


def video_generation_enabled() -> bool:
    """Whether to enqueue a video job after successful synthesis.

    Defaults to ``True``. When enabled, the audio runner enqueues a video job
    *in addition to* publishing the MP3 immediately; the video pipeline composes
    the MP4 and publishes it to Spotify separately. Audio and video are always
    published independently. Set ``VIDEO_GENERATION_ENABLED`` to a falsey value
    (``false``/``0``/``no``) to skip video generation entirely.
    """

    raw = os.environ.get("VIDEO_GENERATION_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in ("false", "0", "no", "off", "")


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


def realized_audio_metadata_path(job_id: str) -> str:
    """Blob path for the Layer 2 realized-audio-metadata document (#553)."""
    return f"jobs/{job_id}/realized_audio_metadata.json"


def run_synthesis(
    job_id: str,
    storage: StorageBackend,
    config: TtsConfig,
    *,
    now: datetime | None = None,
    transport=None,
    token_provider=None,
    runner=None,
    enqueue_video=None,
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
        raise TransientSynthesisError(
            f"staged manifest for job_id={job_id} is not valid JSON"
        ) from exc
    if not isinstance(manifest, dict):
        raise TransientSynthesisError(f"staged manifest for job_id={job_id} is not a JSON object")

    if _already_synthesized(manifest):
        logger.info("synthesis skipped job_id=%s reason=%s", job_id, REASON_ALREADY_SYNTHESIZED)
        # Audio already exists, but a re-trigger should still kick off video
        # generation unless the video was already produced for this job. Without
        # this, re-triggering a podcast that already has audio would never start
        # the video pipeline (the enqueue below in the synthesis path is skipped).
        if video_generation_enabled():
            if _video_already_generated(manifest):
                logger.info(
                    "video enqueue skipped job_id=%s reason=video_already_generated",
                    job_id,
                )
            else:
                _enqueue_video(job_id, enqueue_video)
        return SynthesisOutcome(job_id, STATUS_SKIPPED, reason=REASON_ALREADY_SYNTHESIZED)

    # Claim the audio pipeline lock — skips only if the video pipeline already owns
    # this job's lock (cross-pipeline guard). It does not prevent two concurrent audio
    # runners, since claiming for an audio owner just re-confirms ownership.
    if not claim_pipeline(storage, job_id, PIPELINE_AUDIO, now=current):
        logger.info("synthesis skipped job_id=%s reason=%s", job_id, REASON_PIPELINE_CONFLICT)
        return SynthesisOutcome(job_id, STATUS_SKIPPED, reason=REASON_PIPELINE_CONFLICT)

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
    wav_blob_path = _wav_artifact_path(manifest, job_id)
    request_podcast_config = _request_podcast_config(manifest)
    podcast_config = (
        PodcastConfig.from_payload(request_podcast_config) if request_podcast_config else None
    )

    # Warn on voice config drift between request payload and environment.
    if podcast_config is not None:
        if config.voice_host_a and podcast_config.host_a.voice != config.voice_host_a:
            logger.warning(
                "voice config drift: podcast_config.host_a.voice=%r but env VOICE_HOST_A=%r "
                "(job_id=%s) — check ACA env vars match the caller config",
                podcast_config.host_a.voice,
                config.voice_host_a,
                job_id,
            )
        if config.voice_host_b and podcast_config.host_b.voice != config.voice_host_b:
            logger.warning(
                "voice config drift: podcast_config.host_b.voice=%r but env VOICE_HOST_B=%r "
                "(job_id=%s) — check ACA env vars match the caller config",
                podcast_config.host_b.voice,
                config.voice_host_b,
                job_id,
            )

    request_spotify_publish = _request_spotify_publish(manifest)
    spotify_publish_config = (
        SpotifyPublishConfig.from_payload(request_spotify_publish)
        if request_spotify_publish
        else None
    )
    upload_format = spotify_publish_config.upload_format if spotify_publish_config else "wav"
    music_mix_config = _request_music_mix(manifest)
    mix_spec = _build_mix_spec(music_mix_config)
    intro_music, outro_music = _resolve_music_paths(music_mix_config)
    backchannel_config = _request_backchannels(manifest)
    if mix_spec and not intro_music:
        logger.warning(
            "music_mix_config specifies track=%r but music file not found at expected path; "
            "music mixing will be skipped for job_id=%s",
            music_mix_config.track,
            job_id,
        )
        mix_spec = None
        emit_log(
            storage,
            job_id,
            level=LogLevel.WARNING,
            stage=PipelineStage.SYNTHESIS,
            message=(
                f"music_mix_config track={music_mix_config.track!r} requested but file not found; "
                "music mixing skipped"
            ),
            at=current,
        )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / f"{job_id}.mp3"
            segment_total = len(parse_script_segments(script, podcast_config))
            emit_progress(
                storage,
                job_id,
                stage=PipelineStage.SYNTHESIS,
                phase="recording",
                segment_total=segment_total or None,
                message=f"recording {segment_total} segments" if segment_total else "recording",
                at=current,
            )
            emit_log(
                storage,
                job_id,
                level=LogLevel.INFO,
                stage=PipelineStage.SYNTHESIS,
                message=(
                    f"recording {segment_total} segments" if segment_total else "recording started"
                ),
                context={"segment_total": segment_total} if segment_total else None,
                at=current,
            )

            # Per-segment in-flight progress (issue #470): each synthesized turn
            # advances the "recording N/M" counter so the stage-progress summary
            # and its ETA reflect real progress rather than a single coarse start
            # event.  Best-effort — emit_progress already swallows failures.
            def _on_segment(completed: int, total: int) -> None:
                emit_progress(
                    storage,
                    job_id,
                    stage=PipelineStage.SYNTHESIS,
                    phase="recording",
                    segment_index=completed,
                    segment_total=total,
                    message=f"recording {completed}/{total} segments",
                )

            episode_audio = synthesize_episode(
                script,
                config,
                decision,
                output_path,
                podcast_config=podcast_config,
                token_provider=token_provider,
                transport=transport,
                runner=runner,
                intro_music=intro_music,
                outro_music=outro_music,
                music_mix_spec=mix_spec,
                backchannel_config=backchannel_config,
                progress=_on_segment,
            )
            mp3_bytes = output_path.read_bytes()
            wav_bytes = episode_audio.wav_output_path.read_bytes()

            # Guard: never upload an empty or trivially small mp3 — treat as synthesis failure.
            if len(mp3_bytes) < _MIN_VALID_MP3_BYTES:
                logger.error(
                    "synthesis produced empty/trivial mp3 job_id=%s mp3_bytes=%s wav_bytes=%s",
                    job_id,
                    len(mp3_bytes),
                    len(wav_bytes),
                )
                _record_runner_state(
                    storage,
                    job_id,
                    {"status": STATUS_FAILED, "reason": "empty_audio_output", "at": _iso(current)},
                )
                emit_progress(
                    storage,
                    job_id,
                    stage=PipelineStage.FAILED,
                    phase="synthesis",
                    message="synthesis produced empty audio",
                    at=current,
                )
                emit_log(
                    storage,
                    job_id,
                    level=LogLevel.ERROR,
                    stage=PipelineStage.SYNTHESIS,
                    message="synthesis produced empty/trivial audio output",
                    context={"mp3_bytes": len(mp3_bytes), "wav_bytes": len(wav_bytes)},
                    at=current,
                )
                raise TransientSynthesisError(
                    f"synthesis produced empty audio for job_id={job_id} ({len(mp3_bytes)} bytes)"
                )

            stored_mp3 = storage.put_bytes(mp3_blob_path, mp3_bytes, "audio/mpeg")
            stored_wav = storage.put_bytes(wav_blob_path, wav_bytes, "audio/wav")
            audio_sha256 = checksum(mp3_bytes)
            voices = sorted({voice for voice in episode_audio.voices if voice})
            validation_ready = episode_audio.validation.ready

            # Persist Layer 2 realized audio metadata (#486/#553) as its own blob
            # so the video pipeline derives repo/section timing from the measured
            # TTS clip durations instead of whisper forced alignment. Best-effort:
            # never fail synthesis if the metadata could not be built/written.
            realized_metadata_blob_path: str | None = None
            plan_warnings = list(episode_audio.plan_warnings)
            if episode_audio.realized_metadata is not None:
                try:
                    metadata_doc = {
                        "schema_version": SYNTHESIS_SCHEMA_VERSION,
                        "job_id": job_id,
                        "segment_durations": list(episode_audio.segment_durations),
                        "metadata": episode_audio.realized_metadata.to_dict(),
                        "warnings": plan_warnings,
                    }
                    metadata_bytes = json.dumps(metadata_doc, ensure_ascii=False).encode("utf-8")
                    storage.put_bytes(
                        realized_audio_metadata_path(job_id),
                        metadata_bytes,
                        "application/json; charset=utf-8",
                    )
                    realized_metadata_blob_path = realized_audio_metadata_path(job_id)
                except Exception:  # noqa: BLE001 — metadata is non-fatal to audio
                    logger.warning(
                        "failed to persist realized audio metadata for job_id=%s",
                        job_id,
                        exc_info=True,
                    )
            else:
                logger.info(
                    "no realized audio metadata for job_id=%s (warnings=%s)",
                    job_id,
                    plan_warnings,
                )

            def _apply(content: bytes | None) -> bytes:
                document = json.loads(content.decode("utf-8")) if content else manifest
                if not isinstance(document, dict):
                    document = manifest
                _apply_completion(
                    document,
                    job_id=job_id,
                    mp3_path=stored_mp3.path,
                    mp3_url=stored_mp3.url,
                    mp3_sha256=episode_audio.sha256,
                    mp3_size=stored_mp3.size_bytes,
                    wav_path=stored_wav.path,
                    wav_url=stored_wav.url,
                    wav_sha256=episode_audio.wav_sha256,
                    wav_size=stored_wav.size_bytes,
                    upload_format=upload_format,
                    segment_count=episode_audio.segment_count,
                    voices=voices,
                    validation=episode_audio.validation.to_manifest(),
                    validation_ready=validation_ready,
                    config_summary=config.safe_summary(),
                    completed_at=_iso(current),
                    realized_metadata_path=realized_metadata_blob_path,
                    plan_warnings=plan_warnings,
                )
                return manifest_bytes(document)

            storage.update_bytes(manifest_path(job_id), "application/json; charset=utf-8", _apply)

            # Validate at least one listener-facing publish target is configured (#268)
            # spotify_publish_config being present is not enough — Spotify must also be
            # enabled via SPOTIFY_PUBLISH_ENABLED=true for actual publishing to occur.
            has_spotify = (
                spotify_publish_config is not None
                and os.environ.get("SPOTIFY_PUBLISH_ENABLED", "").lower() == "true"
            ) or auto_publish_enabled()
            has_youtube = os.environ.get("VIDEO_YOUTUBE_ENABLED", "").lower() == "true"
            if not has_spotify and not has_youtube:
                logger.warning(
                    "no listener-facing publish target configured for job_id=%s — "
                    "episode will not reach any audience (enable Spotify or YouTube)",
                    job_id,
                )

            # Publish the MP3 immediately. Audio is always published as soon as
            # synthesis completes, independently of video generation — we never
            # defer the audio publish to the video pipeline.
            auto_publish = auto_publish_enabled()
            if auto_publish:
                try:
                    auto_outcome = auto_publish_job(job_id, storage=storage, now=current)
                    logger.info(
                        "auto publish attempted job_id=%s status=%s",
                        job_id,
                        auto_outcome.manifest.get("status"),
                    )
                except Exception:
                    logger.warning(
                        "auto publish failed job_id=%s; continuing to video enqueue",
                        job_id,
                        exc_info=True,
                    )

            if spotify_publish_config is not None and validation_ready and not auto_publish:
                try:
                    request = (
                        manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
                    )
                    pub_title = str(
                        request.get("article_title")
                        or f"Claracle Podcast — Week {request.get('week') or job_id}"
                    )
                    pub_description = (
                        f"<p>Claracle week {request.get('week') or job_id}.</p>"
                        f"<p>Source article: {request.get('article_url') or ''}</p>"
                    )
                    pub_result: PublishResult = publish_episode(
                        output_path,
                        pub_title,
                        pub_description,
                        spotify_publish_config=spotify_publish_config,
                        year=_extract_year(manifest),
                        week=_extract_week(manifest),
                        article_title=request.get("article_title")
                        if isinstance(request.get("article_title"), str)
                        else None,
                        wav_path=episode_audio.wav_output_path,
                        language=_request_language(manifest),
                    )
                    logger.info(
                        "draft publish attempted job_id=%s status=%s error=%s",
                        job_id,
                        pub_result.status,
                        pub_result.error,
                    )
                except Exception:
                    logger.warning("draft publish failed job_id=%s", job_id, exc_info=True)

            # Additionally hand off to the video pipeline. It composes the MP4
            # and publishes it to Spotify separately (via
            # podcaster.video.distribution). This runs independently of the MP3
            # publish above — both audio and video are published on their own.
            # A failed or unconfigured enqueue never breaks synthesis completion.
            if video_generation_enabled():
                _enqueue_video(job_id, enqueue_video)

            logger.info(
                "synthesis completed job_id=%s segments=%s validation=%s real_audio=true "
                "publishable=%s",
                job_id,
                episode_audio.segment_count,
                episode_audio.validation.status,
                validation_ready,
            )
            emit_progress(
                storage,
                job_id,
                stage=PipelineStage.COMPLETED,
                phase="synthesis",
                segment_index=episode_audio.segment_count,
                segment_total=episode_audio.segment_count,
                percent=100.0,
                message="synthesis completed",
                at=current,
            )
            emit_log(
                storage,
                job_id,
                level=LogLevel.INFO,
                stage=PipelineStage.COMPLETED,
                message=f"synthesis completed ({episode_audio.segment_count} segments)",
                context={
                    "segment_count": episode_audio.segment_count,
                    "validation_status": episode_audio.validation.status,
                    "publishable": validation_ready,
                },
                at=current,
            )
            return SynthesisOutcome(
                job_id,
                STATUS_COMPLETED,
                audio_sha256=audio_sha256,
                segment_count=episode_audio.segment_count,
                validation_status=episode_audio.validation.status,
            )
    except TransientSynthesisError:
        raise
    except Exception as exc:
        logger.exception("synthesis failed job_id=%s error=%s", job_id, type(exc).__name__)
        emit_progress(
            storage,
            job_id,
            stage=PipelineStage.FAILED,
            phase="synthesis",
            message=f"synthesis failed: {type(exc).__name__}",
            at=current,
        )
        emit_log(
            storage,
            job_id,
            level=LogLevel.ERROR,
            stage=PipelineStage.FAILED,
            message=f"synthesis failed: {type(exc).__name__}",
            context={"error_type": type(exc).__name__},
            at=current,
        )
        _record_runner_state(
            storage,
            job_id,
            {"status": STATUS_FAILED, "reason": type(exc).__name__, "at": _iso(current)},
        )
        raise TransientSynthesisError(f"synthesis failed for job_id={job_id}") from exc


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


def _request_backchannels(manifest: dict[str, Any]) -> BackchannelConfig:
    """Extract the optional backchannel config from the manifest request."""

    request = manifest.get("request")
    if not isinstance(request, dict):
        return BackchannelConfig()
    return BackchannelConfig.from_payload(request)


def _request_spotify_publish(manifest: dict[str, Any]) -> dict[str, Any] | None:
    request = manifest.get("request")
    if not isinstance(request, dict):
        return None
    spotify_publish = request.get("spotify_publish")
    if not isinstance(spotify_publish, dict):
        return None
    return {"spotify_publish": spotify_publish}


def _request_language(manifest: dict[str, Any]) -> str:
    """Extract the target language from the manifest request payload.

    Reads ``request.language`` (set by per-language fanout, #439).
    Falls back to ``"en"`` so callers that pre-date the language field continue
    to publish to the English show as before.
    """
    request = manifest.get("request")
    if not isinstance(request, dict):
        return "en"
    lang = request.get("language")
    return str(lang).strip() if isinstance(lang, str) and lang.strip() else "en"


def _extract_year(manifest: dict[str, Any]) -> int | None:
    request = manifest.get("request")
    if not isinstance(request, dict):
        return None
    week_str = str(request.get("week") or "")
    if "-W" not in week_str:
        return None
    try:
        return int(week_str.split("-W", 1)[0])
    except ValueError:
        return None


def _extract_week(manifest: dict[str, Any]) -> int | None:
    request = manifest.get("request")
    if not isinstance(request, dict):
        return None
    week_str = str(request.get("week") or "")
    if "-W" not in week_str:
        return None
    try:
        return int(week_str.split("-W", 1)[1])
    except ValueError:
        return None


def _build_mix_spec(config: MusicMixConfig) -> MusicMixSpec | None:
    """Convert a :class:`MusicMixConfig` to a :class:`MusicMixSpec`, or None if no track."""

    if not config.has_track:
        return None
    return MusicMixSpec(**config.to_mix_spec_kwargs())


# Bundled assets directory (relative to package root in the container image).
_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# Track name → file path mapping. Names are normalized to lowercase with
# spaces replaced by hyphens.
_MUSIC_DIR = _ASSETS_DIR / "music"


def _resolve_music_paths(config: MusicMixConfig) -> tuple[Path | None, Path | None]:
    """Resolve intro and outro music file paths from bundled assets.

    Returns (intro_music, outro_music) paths if the track file exists,
    or (None, None) if no track is configured or the bundled file is missing.
    """
    if not config.track:
        return None, None

    # Normalize track name to filename: "Summer Sport" → "summer-sport.mp3"
    track_filename = config.track.lower().replace(" ", "-") + ".mp3"
    track_path = _MUSIC_DIR / track_filename

    if not track_path.is_file():
        return None, None

    # Use the full track for both intro and outro (mix_spec controls fading)
    return track_path, track_path


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
            report_failure(
                container="podcaster-synth",
                error_type="RetryExhausted",
                error_message=(
                    f"Synthesis failed after {message.dequeue_count} attempts for job_id={job_id}"
                ),
                details={"job_id": job_id, "dequeue_count": message.dequeue_count},
            )
            notify_failure(
                job_id=job_id,
                stage="synthesis",
                error_type="RetryExhausted",
                error_summary=(
                    f"Synthesis failed after {message.dequeue_count} attempts for job_id={job_id}"
                ),
            )
            queue.delete_message(message)
            return SynthesisOutcome(job_id, STATUS_FAILED, reason=REASON_RETRY_EXHAUSTED)
        logger.warning(
            "synthesis audit event=failure job_id=%s reason=transient dequeue_count=%s "
            "terminal=false",
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
        logger.error(
            "PODCASTER_STORAGE_QUEUE_URL is not configured; cannot consume synthesis queue"
        )
        return 2
    storage = create_storage_backend()
    config = load_tts_config()
    import os

    if os.environ.get("PODCASTER_STORAGE_ACCOUNT_URL"):
        try:
            ManagedIdentityTokenCredential().get_token("https://storage.azure.com/.default")
        except Exception:  # noqa: BLE001 - health check should not crash the runner
            logger.exception("managed identity token startup health check failed")
            return 3
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
    if failed:
        failed_jobs = [o.job_id for o in outcomes if o.status == STATUS_FAILED and o.job_id]
        report_failure(
            container="podcaster-synth",
            error_type="SynthesisRunFailure",
            error_message=f"{failed} of {len(outcomes)} jobs failed during synthesis run",
            details={"failed_jobs": failed_jobs, "completed": completed, "skipped": skipped},
        )
    return 1 if failed else 0


def _already_synthesized(manifest: dict[str, Any]) -> bool:
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        return False
    state = generation.get("synthesis_runner")
    return isinstance(state, dict) and state.get("status") == STATUS_COMPLETED


def _video_already_generated(manifest: dict[str, Any]) -> bool:
    """Whether the video pipeline already completed for this job.

    Mirrors the video runner's own idempotency check (``generation.video_runner``
    status). Used to avoid re-enqueuing video on a re-trigger when the MP4 has
    already been produced.
    """
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        return False
    video_state = generation.get("video_runner")
    return isinstance(video_state, dict) and video_state.get("status") == STATUS_COMPLETED


def _enqueue_video(job_id: str, enqueue_video=None) -> None:
    """Enqueue a video job, logging outcome. Never raises.

    A failed or unconfigured enqueue must never break synthesis completion or
    the skipped (already-synthesized) path.
    """
    enqueue = enqueue_video or enqueue_video_job
    logger.info(
        "_enqueue_video diagnostics job_id=%s video_generation_enabled=%s",
        job_id,
        video_generation_enabled(),
    )
    try:
        enqueued = enqueue(job_id)
        if enqueued:
            logger.info(
                "video generation enqueued job_id=%s; "
                "video will be published separately by the video pipeline",
                job_id,
            )
        else:
            logger.warning(
                "video generation enabled but video queue not configured "
                "for job_id=%s; video skipped",
                job_id,
            )
    except Exception:
        logger.warning(
            "failed to enqueue video job_id=%s; video skipped",
            job_id,
            exc_info=True,
        )


def _mp3_artifact_path(manifest: dict[str, Any], job_id: str) -> str:
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict):
        for path in artifacts:
            if isinstance(path, str) and path.endswith(".mp3"):
                return path
    return f"jobs/{job_id}/audio/{job_id}.mp3"


def _wav_artifact_path(manifest: dict[str, Any], job_id: str) -> str:
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict):
        for path in artifacts:
            if isinstance(path, str) and path.endswith(".wav"):
                return path
    return f"jobs/{job_id}/audio/{job_id}.wav"


def _apply_completion(
    manifest: dict[str, Any],
    *,
    job_id: str,
    mp3_path: str,
    mp3_url: str,
    mp3_sha256: str,
    mp3_size: int,
    wav_path: str,
    wav_url: str,
    wav_sha256: str,
    wav_size: int,
    upload_format: str,
    segment_count: int,
    voices: list[str],
    validation: dict[str, Any],
    validation_ready: bool,
    config_summary: dict[str, Any],
    completed_at: str,
    realized_metadata_path: str | None = None,
    plan_warnings: list[str] | None = None,
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
        "blocked_by": [],
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
            "path": mp3_path,
            "sha256": mp3_sha256,
            "size_bytes": mp3_size,
            "upload_format": upload_format,
            "artifacts": {
                "mp3": {"path": mp3_path, "sha256": mp3_sha256, "size_bytes": mp3_size},
                "wav": {"path": wav_path, "sha256": wav_sha256, "size_bytes": wav_size},
            },
        },
    }
    # Layer 2 realized audio metadata reference + soft plan warnings (#553) so the
    # video runner can derive deterministic repo/section timing and a Layer 1
    # marker regression stays visible in the manifest.
    generation["synthesis_runner"]["realized_audio_metadata_path"] = realized_metadata_path
    generation["synthesis_runner"]["plan_warnings"] = list(plan_warnings or [])

    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict):
        mp3_entry = artifacts.setdefault(mp3_path, {})
        if isinstance(mp3_entry, dict):
            mp3_entry["url"] = mp3_url
            mp3_entry["sha256"] = mp3_sha256
            mp3_entry["size_bytes"] = mp3_size
            mp3_entry["content_type"] = "audio/mpeg"
            mp3_entry["publicly_accessible"] = False
        wav_entry = artifacts.setdefault(wav_path, {})
        if isinstance(wav_entry, dict):
            wav_entry["url"] = wav_url
            wav_entry["sha256"] = wav_sha256
            wav_entry["size_bytes"] = wav_size
            wav_entry["content_type"] = "audio/wav"
            wav_entry["publicly_accessible"] = False
            if isinstance(mp3_entry, dict) and mp3_entry.get("access_model") is not None:
                wav_entry.setdefault("access_model", mp3_entry["access_model"])

    publishing = manifest.setdefault("publishing", {})
    if isinstance(publishing, dict):
        publishing["packet_ready"] = True
        publishing["eligible"] = validation_ready
        blocked = publishing.get("blocked_by")
        blocked_set = set(blocked) if isinstance(blocked, list) else set()
        blocked_set.discard("human_review")
        blocked_set.discard("synthesis_not_completed")
        if validation_ready:
            blocked_set.discard("audio_validation_not_passed")
        else:
            blocked_set.add("audio_validation_not_passed")
        publishing["blocked_by"] = sorted(blocked_set)
        checks = publishing.get("readiness_checks")
        if isinstance(checks, dict):
            checks["real_audio_available"] = True
            checks["audio_validation_passed"] = bool(validation_ready)

    lifecycle = manifest.get("lifecycle")
    if isinstance(lifecycle, dict):
        status = "synthesized_publish_ready" if validation_ready else "synthesized_review_ready"
        reason = "audio_synthesized_validation_passed" if validation_ready else "audio_synthesized"
        manifest["status"] = status
        lifecycle["status"] = status
        transitions = lifecycle.get("transitions")
        if isinstance(transitions, list):
            transitions.append({"at": completed_at, "to": status, "reason": reason})
        revision = lifecycle.get("revision")
        lifecycle["revision"] = (revision + 1) if isinstance(revision, int) else 2


def _record_runner_state(storage: StorageBackend, job_id: str, state: dict[str, Any]) -> None:
    """Record a non-completion runner state without ever making output publishable."""

    def _apply(content: bytes | None) -> bytes:
        if content is None:
            raise TransientSynthesisError(f"no staged manifest for job_id={job_id}")
        document = json.loads(content.decode("utf-8"))
        if not isinstance(document, dict):
            raise TransientSynthesisError(
                f"staged manifest for job_id={job_id} is not a JSON object"
            )
        generation = document.setdefault("generation", {})
        if isinstance(generation, dict):
            generation["synthesis_runner"] = {
                "schema_version": SYNTHESIS_SCHEMA_VERSION,
                **state,
            }
        publishing = document.get("publishing")
        if isinstance(publishing, dict):
            publishing["eligible"] = False
        lifecycle = document.setdefault("lifecycle", {})
        if isinstance(lifecycle, dict):
            status = (
                "synthesis_failed" if state.get("status") == STATUS_FAILED else "synthesis_skipped"
            )
            document["status"] = status
            lifecycle["status"] = status
            transitions = lifecycle.setdefault("transitions", [])
            if isinstance(transitions, list):
                transitions.append(
                    {
                        "at": state.get("at"),
                        "to": status,
                        "reason": state.get("reason") or "synthesis_runner_state",
                    }
                )
            lifecycle["revision"] = int(lifecycle.get("revision") or 1) + 1
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
