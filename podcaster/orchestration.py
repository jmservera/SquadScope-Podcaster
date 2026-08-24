from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from podcaster.config import SpotifyPublishConfig
from podcaster.costs import cost_gate_blockers
from podcaster.generation import manifest_bytes
from podcaster.music import TRACK_ATTRIBUTION
from podcaster.publish import PublishResult, publish_episode
from podcaster.review import APPROVED, apply_review_decision
from podcaster.sanitization import normalize_weekly_url
from podcaster.storage import LocalStorageBackend, StorageBackend, create_storage_backend

logger = logging.getLogger("podcaster.orchestration")

AUTO_REVIEWER = "system:auto-publish"


@dataclass(frozen=True)
class JobPublishOutcome:
    manifest: dict[str, Any]
    publish_result: PublishResult | None = None


def auto_publish_enabled() -> bool:
    return (
        os.environ.get("PODCAST_AUTO_PUBLISH", "").lower() == "true"
        and os.environ.get("SPOTIFY_PUBLISH_ENABLED", "").lower() == "true"
    )


def process_review_decision(
    job_id: str,
    *,
    reviewer: str,
    decision: str,
    reviewed_at: str,
    notes: str = "",
    run_url: str | None = None,
    storage: StorageBackend | None = None,
    publish_on_approval: bool = True,
) -> JobPublishOutcome:
    backend = storage or create_storage_backend()
    manifest = load_manifest(backend, job_id)
    updated = apply_review_decision(
        manifest,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        decision=decision,
        notes=notes,
        run_url=run_url,
    )
    persist_manifest(backend, job_id, updated)
    if decision != APPROVED or not publish_on_approval:
        return JobPublishOutcome(updated)
    return publish_staged_job(
        job_id,
        storage=backend,
        actor=reviewer,
        trigger="review_approval",
        requested_at=reviewed_at,
    )


def auto_publish_job(
    job_id: str,
    *,
    storage: StorageBackend | None = None,
    now: datetime | None = None,
) -> JobPublishOutcome:
    reviewed_at = _iso(now or datetime.now(timezone.utc))
    return process_review_decision(
        job_id,
        reviewer=AUTO_REVIEWER,
        decision=APPROVED,
        reviewed_at=reviewed_at,
        notes="Auto-publish approved by runtime because PODCAST_AUTO_PUBLISH=true.",
        run_url=None,
        storage=storage,
        publish_on_approval=True,
    )


def publish_staged_job(
    job_id: str,
    *,
    storage: StorageBackend | None = None,
    actor: str,
    trigger: str,
    requested_at: str | None = None,
) -> JobPublishOutcome:
    backend = storage or create_storage_backend()
    manifest = load_manifest(backend, job_id)
    timestamp = requested_at or _iso(datetime.now(timezone.utc))
    manifest = _mark_publish_requested(
        manifest,
        actor=actor,
        trigger=trigger,
        requested_at=timestamp,
    )
    persist_manifest(backend, job_id, manifest)
    publishing = manifest.get("publishing") if isinstance(manifest.get("publishing"), dict) else {}
    result_state = publishing.get("result") if isinstance(publishing, dict) else {}
    if isinstance(result_state, dict) and result_state.get("status") == "blocked":
        return JobPublishOutcome(manifest, None)

    try:
        audio_paths, cleanup_dir = _prepare_audio_files(backend, manifest, job_id)
        try:
            publish_result = _publish_from_manifest(audio_paths, manifest)
        finally:
            if cleanup_dir is not None:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
    except Exception as exc:
        publish_result = PublishResult(status="failed", error=str(exc))

    manifest = load_manifest(backend, job_id)
    manifest = _apply_publish_result(
        manifest,
        publish_result=publish_result,
        actor=actor,
        trigger=trigger,
        completed_at=_iso(datetime.now(timezone.utc)),
    )
    persist_manifest(backend, job_id, manifest)
    return JobPublishOutcome(manifest, publish_result)


def load_manifest(storage: StorageBackend, job_id: str) -> dict[str, Any]:
    raw = storage.get_bytes(manifest_path(job_id))
    if raw is None:
        raise ValueError(f"no manifest found for job_id={job_id}")
    manifest = json.loads(raw.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest for job_id={job_id} is not a JSON object")
    return manifest


def persist_manifest(storage: StorageBackend, job_id: str, manifest: dict[str, Any]) -> None:
    storage.update_bytes(
        manifest_path(job_id),
        "application/json; charset=utf-8",
        lambda _current: manifest_bytes(manifest),
    )


def manifest_path(job_id: str) -> str:
    return f"jobs/{job_id}/manifest.json"


def _prepare_audio_files(
    storage: StorageBackend,
    manifest: dict[str, Any],
    job_id: str,
) -> tuple[tuple[Path, Path | None], Path | None]:
    mp3_blob_path, wav_blob_path = _audio_artifact_paths(manifest, job_id)
    if isinstance(storage, LocalStorageBackend):
        mp3_path = storage.root / mp3_blob_path
        wav_path = (storage.root / wav_blob_path) if wav_blob_path else None
        return (mp3_path, wav_path), None

    import tempfile

    scratch_dir = Path(tempfile.gettempdir()) / "podcaster-publish-work" / job_id
    scratch_dir.mkdir(parents=True, exist_ok=True)
    mp3_bytes = storage.get_bytes(mp3_blob_path)
    if mp3_bytes is None:
        raise ValueError(f"missing synthesized MP3 for job_id={job_id}")
    mp3_path = scratch_dir / Path(mp3_blob_path).name
    mp3_path.write_bytes(mp3_bytes)

    wav_path: Path | None = None
    if wav_blob_path:
        wav_bytes = storage.get_bytes(wav_blob_path)
        if wav_bytes is not None:
            wav_path = scratch_dir / Path(wav_blob_path).name
            wav_path.write_bytes(wav_bytes)

    mp4_blob_path = f"jobs/{job_id}/audio/{job_id}.mp4"
    mp4_bytes = storage.get_bytes(mp4_blob_path)
    if mp4_bytes is None:
        mp4_blob_path = f"jobs/{job_id}/video/{job_id}.mp4"
        mp4_bytes = storage.get_bytes(mp4_blob_path)
    if mp4_bytes is not None:
        mp4_path = scratch_dir / f"{mp3_path.stem}.mp4"
        mp4_path.write_bytes(mp4_bytes)
    return (mp3_path, wav_path), scratch_dir


def _publish_from_manifest(
    audio_paths: tuple[Path, Path | None],
    manifest: dict[str, Any],
) -> PublishResult:
    mp3_path, wav_path = audio_paths
    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    spotify_publish = request.get("spotify_publish") if isinstance(request, dict) else None
    spotify_publish_config = (
        SpotifyPublishConfig.from_payload({"spotify_publish": spotify_publish})
        if isinstance(spotify_publish, dict)
        else None
    )
    title = str(
        request.get("article_title")
        or f"Claracle Podcast — Week {request.get('week') or manifest.get('job_id')}"
    )
    description = _show_notes_text(manifest, mp3_path, wav_path)
    year, week = _parse_week(str(request.get("week") or ""))
    return publish_episode(
        mp3_path,
        title,
        description,
        spotify_publish_config=spotify_publish_config,
        year=year,
        week=week,
        article_title=(
            request.get("article_title") if isinstance(request.get("article_title"), str) else None
        ),
        wav_path=wav_path,
        language=_request_language(manifest),
    )


def _show_notes_text(manifest: dict[str, Any], mp3_path: Path, wav_path: Path | None) -> str:
    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    week = request.get("week") or manifest.get("job_id")
    article_url = normalize_weekly_url(request.get("article_url") or "")
    audio_label = wav_path.name if wav_path is not None else mp3_path.name
    return (
        f"<p>Claracle week {week}.</p>"
        f"<p>Source article: {article_url}</p>"
        f"<p>Generated audio artifact: {audio_label}</p>"
        f"<p>Intro/outro music: {TRACK_ATTRIBUTION}</p>"
    )


def _audio_artifact_paths(manifest: dict[str, Any], job_id: str) -> tuple[str, str | None]:
    generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
    runner = (
        generation.get("synthesis_runner")
        if isinstance(generation.get("synthesis_runner"), dict)
        else {}
    )
    audio = runner.get("audio") if isinstance(runner.get("audio"), dict) else {}
    artifacts = audio.get("artifacts") if isinstance(audio.get("artifacts"), dict) else {}
    mp3_path = None
    wav_path = None
    mp3 = artifacts.get("mp3") if isinstance(artifacts.get("mp3"), dict) else {}
    wav = artifacts.get("wav") if isinstance(artifacts.get("wav"), dict) else {}
    if isinstance(mp3, dict):
        mp3_path = mp3.get("path")
    if isinstance(wav, dict):
        wav_path = wav.get("path")
    if not isinstance(mp3_path, str) or not mp3_path:
        mp3_path = audio.get("path") if isinstance(audio.get("path"), str) else None
    if not isinstance(mp3_path, str) or not mp3_path:
        mp3_path = _find_artifact_path(manifest, ".mp3") or f"jobs/{job_id}/audio/{job_id}.mp3"
    if not isinstance(wav_path, str) or not wav_path:
        wav_path = _find_artifact_path(manifest, ".wav")
    return mp3_path, wav_path


def _find_artifact_path(manifest: dict[str, Any], suffix: str) -> str | None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    for path in artifacts:
        if isinstance(path, str) and path.endswith(suffix):
            return path
    return None


def _mark_publish_requested(
    manifest: dict[str, Any],
    *,
    actor: str,
    trigger: str,
    requested_at: str,
) -> dict[str, Any]:
    updated = json.loads(json.dumps(manifest))
    publishing = updated.setdefault("publishing", {})
    readiness_checks = publishing.setdefault("readiness_checks", {})
    blocked_by = list(_publish_blockers(updated))
    publishing["blocked_by"] = blocked_by
    publishing["eligible"] = not blocked_by
    publishing["packet_ready"] = not _audio_pending(updated)
    publishing["mode"] = "auto" if actor == AUTO_REVIEWER else "review_gate"
    publishing["auto_publish_enabled"] = auto_publish_enabled()
    publishing["result"] = {
        "status": "requested" if not blocked_by else "blocked",
        "requested_at": requested_at,
        "requested_by": actor,
        "trigger": trigger,
        "anchor_episode_id": None,
        "dry_run": False,
        "error": None,
    }
    readiness_checks["editorial_review_complete"] = _review_approved(updated)
    readiness_checks["real_audio_available"] = not _audio_pending(updated)
    readiness_checks["audio_validation_passed"] = _audio_validation_ready(updated)

    lifecycle = updated.setdefault("lifecycle", {})
    if not blocked_by:
        updated["status"] = "publish_requested"
        lifecycle["status"] = "publish_requested"
        lifecycle.setdefault("transitions", []).append(
            {"at": requested_at, "to": "publish_requested", "reason": trigger, "actor": actor}
        )
    else:
        lifecycle["status"] = updated.get("status")
    return updated


def _apply_publish_result(
    manifest: dict[str, Any],
    *,
    publish_result: PublishResult,
    actor: str,
    trigger: str,
    completed_at: str,
) -> dict[str, Any]:
    updated = json.loads(json.dumps(manifest))
    publishing = updated.setdefault("publishing", {})
    blocked_by = list(_publish_blockers(updated))
    publishing["blocked_by"] = blocked_by
    publishing["eligible"] = publish_result.status == "failed" and not blocked_by
    publishing["packet_ready"] = not _audio_pending(updated)
    publishing["mode"] = "auto" if actor == AUTO_REVIEWER else "review_gate"
    publishing["auto_publish_enabled"] = auto_publish_enabled()
    publishing["result"] = {
        "status": publish_result.status,
        "completed_at": completed_at,
        "requested_by": actor,
        "trigger": trigger,
        "anchor_episode_id": publish_result.anchor_episode_id,
        "dry_run": publish_result.dry_run,
        "error": publish_result.error,
        "details": publish_result.details,
    }

    lifecycle = updated.setdefault("lifecycle", {})
    final_status = publish_result.status if publish_result.status != "failed" else "publish_failed"
    updated["status"] = final_status
    lifecycle["status"] = final_status
    lifecycle["revision"] = int(lifecycle.get("revision") or 1) + 1
    lifecycle.setdefault("transitions", []).append(
        {
            "at": completed_at,
            "to": final_status,
            "reason": (
                "spotify_publish_completed"
                if publish_result.status != "failed"
                else "spotify_publish_failed"
            ),
            "actor": actor,
        }
    )
    return updated


def _publish_blockers(manifest: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not _review_approved(manifest):
        blockers.append("human_review")
    if _audio_pending(manifest):
        blockers.append("synthesis_not_completed")
    if not _audio_validation_ready(manifest):
        blockers.append("audio_validation_not_passed")
    for blocker in cost_gate_blockers(manifest.get("cost_ledger")):
        if blocker not in blockers:
            blockers.append(blocker)
    return blockers


def _review_approved(manifest: dict[str, Any]) -> bool:
    review = manifest.get("review")
    return isinstance(review, dict) and review.get("status") == APPROVED


def _audio_pending(manifest: dict[str, Any]) -> bool:
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        return True
    if generation.get("audio_mode") == "synthesized":
        return False
    runner = generation.get("synthesis_runner")
    return not (isinstance(runner, dict) and runner.get("status") == "completed")


def _audio_validation_ready(manifest: dict[str, Any]) -> bool:
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        return False
    validation = generation.get("audio_validation")
    return (
        isinstance(validation, dict)
        and validation.get("ready") is True
        and validation.get("status") == "passed"
    )


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


def _parse_week(value: str) -> tuple[int | None, int | None]:
    if "-W" not in value:
        return None, None
    try:
        year_text, week_text = value.split("-W", 1)
        return int(year_text), int(week_text)
    except ValueError:
        return None, None


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
