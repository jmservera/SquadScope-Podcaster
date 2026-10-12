"""Read-only projection of accepted jobs and identity-bound provider receipts."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from podcaster.progress import PROGRESS_SCHEMA_VERSION, read_progress
from podcaster.publication_state import (
    CANONICAL_OUTCOMES,
    PROVIDER_STATUSES,
    VERIFICATION_STATES,
    PublicationIdentity,
    PublicationStateError,
    _provider_status,
    latest_outcomes,
    publication_identity,
    read_evidence,
)
from podcaster.storage import StorageBackend
from podcaster.video.youtube_reconcile import youtube_identity_tag

STATUS_SCHEMA_VERSION = "podcast_publication_status_v1"
MAX_RECEIPT_AGE = timedelta(hours=24)
_JOB = re.compile(r"^podcast-\d{4}-W\d{2}-[0-9a-f]{12}(?:-(?:en|es|fr))?$")
_CORRELATION = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_IDENTITY_FIELDS = ("week", "publish_run_id", "article_sha256", "manifest_sha256")


class PublicationStatusError(ValueError):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(timezone.utc) if moment.tzinfo else None


def _fresh(value: Any, now: datetime) -> bool:
    moment = _timestamp(value)
    return moment is not None and -timedelta(minutes=5) <= now - moment <= MAX_RECEIPT_AGE


def read_youtube_status(identity: PublicationIdentity, video_id: str) -> Mapping[str, Any] | None:
    """Read only the bound video using the existing OAuth and videos.list helpers."""
    from podcaster.video.distribution import (
        VideoDistributionConfig,
        YouTubeDeliveryError,
        _DefaultTransport,
        _get_youtube_access_token,
        _load_youtube_refresh_token,
    )
    from podcaster.video.youtube_publish import get_video_snippet

    # Do not load Spotify credentials or a complete distribution configuration.
    client_id = os.environ.get("VIDEO_YOUTUBE_CLIENT_ID", "")
    client_secret = os.environ.get("VIDEO_YOUTUBE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None
    config = VideoDistributionConfig(
        youtube_client_id=client_id,
        youtube_client_secret=client_secret,
        youtube_refresh_token=_load_youtube_refresh_token(),
    )
    if not all(
        (config.youtube_client_id, config.youtube_client_secret, config.youtube_refresh_token)
    ):
        return None
    transport = _DefaultTransport(timeout=3)
    try:
        token = _get_youtube_access_token(config, transport, notify_reauthorization=False)
    except YouTubeDeliveryError:
        return None
    snippet = get_video_snippet(video_id, token, transport=transport, require_exact_id=True)
    tags = snippet.get("tags") if snippet is not None else None
    if not isinstance(tags, list) or youtube_identity_tag(identity) not in tags:
        return None
    return snippet


def _stage(state: Any, *, synthesis: bool = False) -> str:
    if state is None:
        return "pending"
    if not isinstance(state, dict):
        return "unknown"
    status = state.get("status")
    if status == "completed":
        return "succeeded"
    if status in {"failed", "partial", "skipped"}:
        return "failed"
    if status in {"pending", "queued", "running", "started"}:
        return "started" if synthesis and status in {"running", "started"} else "pending"
    return "unknown"


def publication_status(
    storage: StorageBackend,
    query: Mapping[str, str],
    *,
    now: datetime | None = None,
    youtube_readback: Callable[
        [PublicationIdentity, str], Mapping[str, Any] | None
    ] = read_youtube_status,
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    job_id = query.get("job_id", "")
    correlation_id = query.get("correlation_id", "")
    language = query.get("language", "en")
    if (
        not _JOB.fullmatch(job_id)
        or not _CORRELATION.fullmatch(correlation_id)
        or language not in {"en", "es", "fr"}
        or set(query) - {*_IDENTITY_FIELDS, "job_id", "correlation_id", "language"}
        or any(not query.get(field) for field in _IDENTITY_FIELDS)
    ):
        raise PublicationStatusError("invalid_query", 400)
    raw = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    if raw is None:
        raise PublicationStatusError("job_not_found", 404)
    try:
        manifest = json.loads(raw)
        if not isinstance(manifest, dict):
            raise PublicationStateError("manifest is not an object")
        identity = publication_identity(manifest, job_id, query["publish_run_id"])
    except (PublicationStateError, ValueError, UnicodeDecodeError) as exc:
        raise PublicationStatusError("invalid_job_identity", 409) from exc
    request = manifest["request"]
    expected = {field: getattr(identity, field) for field in _IDENTITY_FIELDS}
    year, week = identity.week.split("-W")
    try:
        date.fromisocalendar(int(year), int(week), 1)
    except ValueError as exc:
        raise PublicationStatusError("invalid_job_identity", 409) from exc
    if not job_id.startswith(f"podcast-{identity.week}-"):
        raise PublicationStatusError("identity_mismatch", 409)
    if expected != {field: query[field] for field in _IDENTITY_FIELDS}:
        raise PublicationStatusError("identity_mismatch", 409)
    if request.get("language", "en") != language:
        raise PublicationStatusError("language_mismatch", 409)
    # Generation's response correlation is the accepted job, not a caller-selected path.
    observability = manifest.get("observability")
    if (
        not isinstance(observability, dict)
        or observability.get("correlation_id") != job_id
        or correlation_id != observability["correlation_id"]
    ):
        raise PublicationStatusError("correlation_mismatch", 409)
    generation = manifest.get("generation")
    if not isinstance(generation, dict):
        raise PublicationStatusError("invalid_job_state", 409)
    synthesis = _stage(generation.get("synthesis_runner"), synthesis=True)
    video = _stage(generation.get("video_runner"))
    queue = generation.get("synthesis_queue")
    if synthesis == "pending" and isinstance(queue, dict) and queue.get("status") == "failed":
        synthesis = "failed"
    if synthesis == "pending":
        progress = read_progress(storage, job_id)
        if progress is not None:
            if (
                progress.get("schema_version") != PROGRESS_SCHEMA_VERSION
                or progress.get("job_id") != job_id
            ):
                raise PublicationStatusError("invalid_progress", 409)
            current = progress.get("current", {})
            if not isinstance(current, dict):
                raise PublicationStatusError("invalid_progress", 409)
            if current.get("stage") == "synthesis":
                synthesis = "started" if _fresh(current.get("at"), moment) else "unknown"
    evidence = read_evidence(storage, job_id)
    if evidence is not None and evidence.get("corrupt"):
        raise PublicationStatusError("invalid_provider_evidence", 409)
    if evidence is not None:
        previous_seq = 0
        for record in evidence["records"]:
            if not isinstance(record, dict) or any(
                record.get(field) != value
                for field, value in {**expected, "job_id": job_id}.items()
            ):
                raise PublicationStatusError("provider_identity_mismatch", 409)
            if (
                record.get("outcome") not in CANONICAL_OUTCOMES
                or record.get("status") not in PROVIDER_STATUSES
                or record.get("verification") not in VERIFICATION_STATES
                or type(record.get("seq")) is not int
                or record["seq"] <= previous_seq
                or _timestamp(record.get("at")) is None
                or (
                    record.get("native_state") is not None
                    and not isinstance(record["native_state"], str)
                )
            ):
                raise PublicationStatusError("invalid_provider_evidence", 409)
            if record["status"] != _provider_status(
                record["outcome"],
                verification=record["verification"],
                native_state=record.get("native_state"),
            ):
                raise PublicationStatusError("invalid_provider_evidence", 409)
            previous_seq = record["seq"]
    latest = latest_outcomes(evidence)
    providers: dict[str, dict[str, Any]] = {}
    for provider in ("youtube", "spotify"):
        record = latest.get(f"{provider}:video")
        state, code, checked_at = "pending", "receipt_missing", None
        if record is not None:
            checked_at = record.get("checked_at") or record.get("at")
            provider_id = record.get("provider_id") or record.get("provider_artifact_id")
            outcome = record.get("outcome")
            if record.get("status") == "failed":
                state, code = "failed", "provider_failed"
            elif (
                record.get("operation") == "upload_intent"
                and record.get("code") == "mutation_intent"
                and record.get("mutation_attempted") is False
                and video == "pending"
                and _fresh(checked_at, moment)
            ):
                state, code = "pending", "upload_in_progress"
            elif outcome in {"publication_unknown", "manual_handoff_required"}:
                state, code = "unknown", "provider_unconfirmed"
            elif not provider_id:
                state, code = "unknown", "provider_id_missing"
            elif not _fresh(checked_at, moment):
                state, code = "unknown", "receipt_stale"
            elif provider == "spotify":
                if (
                    outcome == "published"
                    and record.get("verification") in {"provider_readback", "external_verified"}
                    and record.get("native_state") in {"published", "public", "already_published"}
                    and record.get("evidence_source") == "spotify_episode_readback"
                    and isinstance(provider_id, str)
                    and re.fullmatch(r"(?:[0-9]+|[A-Za-z0-9]{22})", provider_id)
                ):
                    state, code = "published", "video_published_readback"
                else:
                    state, code = "pending", "video_not_verified"
            elif outcome in {"uploaded", "draft_created", "published"}:
                if not isinstance(provider_id, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{11}", provider_id
                ):
                    state, code = "unknown", "provider_id_invalid"
                else:
                    readback = youtube_readback(identity, provider_id)
                    if readback is None:
                        state, code = "unknown", "readback_unavailable"
                    elif (
                        readback.get("uploadStatus") == "processed"
                        and readback.get("processingStatus") == "succeeded"
                        and readback.get("privacyStatus") in {"unlisted", "public"}
                    ):
                        state, code = "published", "processed_upload_readback"
                        checked_at = moment.isoformat()
                    elif readback.get("uploadStatus") in {
                        "failed",
                        "rejected",
                        "deleted",
                    } or readback.get("processingStatus") in {"failed", "terminated"}:
                        state, code = "failed", "upload_failed"
                    else:
                        state, code = "pending", "upload_not_ready"
            else:
                state, code = "unknown", "receipt_invalid"
        providers[provider] = {"state": state, "code": code, "checked_at": checked_at}
    states = {entry["state"] for entry in providers.values()}
    provider_state = (
        "failed"
        if "failed" in states
        else "unknown"
        if "unknown" in states
        else "published"
        if states == {"published"}
        else "pending"
    )
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "identity": expected,
        "job_id": job_id,
        "correlation_id": correlation_id,
        "language": language,
        "observed_at": moment.isoformat(),
        "synthesis": {"state": synthesis},
        "video": {"state": video},
        "provider": {
            "state": provider_state,
            "external_verified": provider_state == "published",
            "providers": providers,
        },
    }
