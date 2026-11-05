"""Private-only video recovery with a single manifest CAS as ownership authority."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from podcaster.generation import manifest_bytes
from podcaster.publication_state import PublicationStateError, publication_identity
from podcaster.review import ReviewBindingError, is_exact_script_job, validate_private_media_inputs
from podcaster.video.budget import JOB_DEADLINE_SECONDS, VideoStage, VideoStageBudget

SCHEMA = "private-video-recovery-v1"
QUEUE_SCHEMA = "private-video-recovery-queue-v1"
CONTENT_TYPE = "application/json; charset=utf-8"
LEASE_SECONDS = JOB_DEADLINE_SECONDS
CANDIDATES = frozenset({"podcast-2026-W44-00bc06dfb6e2", "podcast-2026-W45-9798e93edc06"})
EXPECTED_MP3_SIZES = {
    "podcast-2026-W44-00bc06dfb6e2": 11_664_239,
    "podcast-2026-W45-9798e93edc06": 12_170_805,
}
LEGACY_LEDGER = "jobs/{job_id}/recovery-epochs.json"


class RecoveryDenied(ValueError):
    """The recovery record, proof, ownership or release boundary is unsafe."""


def _time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError) as exc:
        raise RecoveryDenied("invalid recovery timestamp") from exc


def _document(content: bytes | None, job_id: str) -> dict[str, Any]:
    try:
        doc = json.loads(content) if content else None
        if not isinstance(doc, dict) or doc.get("job_id") != job_id:
            raise RecoveryDenied("original job manifest is missing or changed")
        if not isinstance(doc.get("generation"), dict):
            raise RecoveryDenied("generation state is missing")
        return doc
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise RecoveryDenied("invalid original job manifest") from exc


def _epochs(doc: dict[str, Any]) -> list[dict[str, Any]]:
    recovery = doc["generation"].get("video_recovery")
    if not isinstance(recovery, dict) or recovery.get("schema") != SCHEMA:
        raise RecoveryDenied("recovery authority is missing or malformed")
    entries = recovery.get("epochs")
    if (
        not isinstance(entries, list)
        or not entries
        or not all(isinstance(entry, dict) for entry in entries)
    ):
        raise RecoveryDenied("recovery history is malformed")
    seen: set[str] = set()
    for entry in entries:
        dispatch_state = entry.get("dispatch")
        if (
            not isinstance(entry.get("id"), str)
            or not entry["id"]
            or entry["id"] in seen
            or not isinstance(entry.get("request_id"), str)
            or not isinstance(entry.get("proof"), dict)
            or not isinstance(entry.get("identity"), dict)
            or not isinstance(entry.get("budget"), dict)
            or entry.get("status") not in {"reserved", "running", "failed", "expired", "completed"}
            or entry.get("upload") != "held"
            or not isinstance(dispatch_state, dict)
            or dispatch_state.get("state") not in {"pending", "sending", "acknowledged"}
            or type(dispatch_state.get("attempts")) is not int
            or dispatch_state["attempts"] < 0
        ):
            raise RecoveryDenied("recovery entry is incomplete or ambiguous")
        _time(entry.get("deadline_at"))
        try:
            VideoStageBudget.from_dict(entry["budget"])
        except (ValueError, KeyError, TypeError) as exc:
            raise RecoveryDenied("recovery entry budget is malformed") from exc
        seen.add(entry["id"])
    return entries


def latest(doc: dict[str, Any]) -> dict[str, Any] | None:
    if "video_recovery" not in doc["generation"]:
        return None
    return _epochs(doc)[-1]


def deny_candidate_upload(job_id: str | None) -> None:
    """No media-bound upload approval transition exists in this release."""
    if job_id in CANDIDATES:
        raise RecoveryDenied("recovered candidate provider upload is held")


def _proof(
    doc: dict[str, Any], storage: Any, job_id: str, *, allow_completed: bool = False
) -> dict[str, Any]:
    if job_id not in CANDIDATES or not is_exact_script_job(doc):
        raise RecoveryDenied("job is not an eligible exact-reviewed original")
    try:
        request = doc["request"]
        identity = publication_identity(doc, job_id, request["publish_run_id"])
        audio = validate_private_media_inputs(doc, storage, job_id)
        artifacts = doc["generation"]["synthesis_runner"]["audio"]["artifacts"]
        budget = doc["generation"]["video_budget"]
        VideoStageBudget.from_dict(budget)
        original_budget_bytes = storage.get_bytes(f"jobs/{job_id}/video-budget.json")
        if (
            original_budget_bytes is None
            or json.loads(original_budget_bytes) != budget
            or len(audio.content) != EXPECTED_MP3_SIZES[job_id]
        ):
            raise RecoveryDenied("historical budget or candidate MP3 size differs")
        if (
            not allow_completed
            and doc["generation"].get("video_runner", {}).get("status") == "completed"
        ):
            raise RecoveryDenied("already completed video cannot be restarted")
        if not isinstance(artifacts, dict) or not all(
            isinstance(artifacts.get(kind), dict) for kind in ("mp3", "wav")
        ):
            raise RecoveryDenied("audio artifacts are incomplete")
        return {
            "identity": asdict(identity),
            "script_sha256": doc["review"]["snapshot"]["script_sha256"],
            "mp3_sha256": hashlib.sha256(audio.content).hexdigest(),
            "mp3_size": len(audio.content),
            "wav_sha256": artifacts["wav"]["sha256"],
            "wav_size": artifacts["wav"]["size_bytes"],
            "original_budget": budget,
        }
    except (KeyError, TypeError, ReviewBindingError, PublicationStateError, ValueError) as exc:
        raise RecoveryDenied(
            "exact review, synthesis or historical budget proof is missing"
        ) from exc


def _check_identity(proof: dict[str, Any], identity: dict[str, str]) -> None:
    if not isinstance(identity, dict) or proof["identity"] != identity:
        raise RecoveryDenied("publication identity does not match original job")


def _check_legacy(storage: Any, job_id: str) -> None:
    if storage.get_bytes(LEGACY_LEDGER.format(job_id=job_id)) is not None:
        raise RecoveryDenied("legacy recovery ledger requires offline reconciliation")


def _validate_inventory(inventory: Any, identity: dict[str, str], now: datetime) -> str:
    if not isinstance(inventory, dict) or inventory.get("identity") != identity:
        raise RecoveryDenied("exact-identity owner inventory is required")
    checked_by = inventory.get("checked_by")
    checked_at = _time(inventory.get("checked_at"))
    youtube = inventory.get("youtube")
    spotify = inventory.get("spotify")
    youtube_states = youtube.get("states") if isinstance(youtube, dict) else None
    spotify_states = spotify.get("states") if isinstance(spotify, dict) else None
    if (
        not isinstance(checked_by, str)
        or not checked_by.strip()
        or checked_by.lower().startswith("system:")
        or not isinstance(youtube, dict)
        or youtube.get("status") != "reconciled_absent"
        or not isinstance(youtube.get("channel_id"), str)
        or not youtube["channel_id"].strip()
        or not isinstance(youtube_states, list)
        or not all(isinstance(state, str) for state in youtube_states)
        or sorted(youtube_states) != ["private", "processing", "unlisted"]
        or not isinstance(spotify, dict)
        or spotify.get("status") != "reconciled_absent"
        or not isinstance(spotify.get("show_ids"), list)
        or not spotify["show_ids"]
        or not all(isinstance(show, str) and show.strip() for show in spotify["show_ids"])
        or not isinstance(spotify_states, list)
        or not all(isinstance(state, str) for state in spotify_states)
        or sorted(spotify_states) != ["draft", "live", "scheduled"]
        or checked_at > now
        or checked_at < now - timedelta(hours=2)
    ):
        raise RecoveryDenied("current all-state YouTube and Spotify inventory is required")
    return checked_at.isoformat()


def _write(storage: Any, job_id: str, change: Callable[[dict[str, Any]], None]) -> None:
    def apply(content: bytes | None) -> bytes:
        doc = _document(content, job_id)
        try:
            change(doc)
        except (KeyError, TypeError, AttributeError) as exc:
            raise RecoveryDenied("recovery state is incomplete or malformed") from exc
        return manifest_bytes(doc)

    storage.update_bytes(f"jobs/{job_id}/manifest.json", CONTENT_TYPE, apply)


def reserve(
    storage: Any,
    job_id: str,
    *,
    request_id: str,
    identity: dict[str, str],
    operator: str,
    inventory: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reserve one operator-authorized epoch; the callback has no external side effects."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise RecoveryDenied("clock must be timezone aware")
    if (
        not isinstance(request_id, str)
        or not 8 <= len(request_id) <= 128
        or not request_id.isascii()
        or not all(c.isalnum() or c in "-_" for c in request_id)
        or not isinstance(operator, str)
        or not operator.strip()
        or operator.lower().startswith("system:")
    ):
        raise RecoveryDenied("human operator and stable request id are required")
    checked_at = _validate_inventory(inventory, identity, now)
    _check_legacy(storage, job_id)
    doc = _document(storage.get_bytes(f"jobs/{job_id}/manifest.json"), job_id)
    proof = _proof(doc, storage, job_id, allow_completed=True)
    _check_identity(proof, identity)
    if (
        VideoStageBudget.from_dict(proof["original_budget"], now_utc=now, utcnow=lambda: now)
        .admit(VideoStage.SHUTDOWN)
        .allowed
    ):
        raise RecoveryDenied("original video attempt still has a valid budget")
    epoch_id = uuid.uuid4().hex
    created = now.isoformat()
    expires = (now + timedelta(seconds=JOB_DEADLINE_SECONDS)).isoformat()
    inventory_digest = hashlib.sha256(manifest_bytes(inventory)).hexdigest()

    def change(current: dict[str, Any]) -> None:
        current_proof = _proof(current, storage, job_id, allow_completed=True)
        _check_identity(current_proof, identity)
        if current_proof != proof:
            raise RecoveryDenied("original proof changed during reservation")
        if _validate_inventory(inventory, identity, now) != checked_at:
            raise RecoveryDenied("owner inventory changed during reservation")
        if (
            VideoStageBudget.from_dict(
                current_proof["original_budget"], now_utc=now, utcnow=lambda: now
            )
            .admit(VideoStage.SHUTDOWN)
            .allowed
        ):
            raise RecoveryDenied("original video budget changed or remains active")
        if storage.get_bytes(LEGACY_LEDGER.format(job_id=job_id)) is not None:
            raise RecoveryDenied("legacy ledger appeared during reservation")
        generation = current["generation"]
        entries = _epochs(current) if "video_recovery" in generation else []
        if any(entry.get("request_id") == request_id for entry in entries):
            previous = next(entry for entry in entries if entry.get("request_id") == request_id)
            if (
                previous.get("identity") != identity
                or previous.get("proof") != proof
                or previous.get("operator") != operator.strip()
                or previous.get("inventory_sha256") != inventory_digest
            ):
                raise RecoveryDenied("request id conflicts with prior reservation")
            return
        if current["generation"].get("video_runner", {}).get("status") == "completed":
            raise RecoveryDenied("completed video cannot start another epoch")
        if entries and entries[-1].get("status") in {"reserved", "running"}:
            if _time(entries[-1]["deadline_at"]) <= now:
                entries[-1]["status"] = "expired"
                entries[-1]["terminal_reason"] = "deadline_expired"
                entries[-1]["terminal_at"] = created
        if entries and entries[-1].get("status") not in {"expired", "failed", "completed"}:
            raise RecoveryDenied("an active recovery epoch already exists")
        if entries and inventory_digest == entries[-1].get("inventory_sha256"):
            raise RecoveryDenied("new epoch requires fresh provider reconciliation")
        if entries and checked_at <= entries[-1].get("inventory_checked_at", ""):
            raise RecoveryDenied("new epoch requires later owner inventory")
        record = {
            "id": epoch_id,
            "request_id": request_id,
            "operator": operator.strip(),
            "identity": identity,
            "proof": proof,
            "prior_runner": generation.get("video_runner"),
            "budget": VideoStageBudget.start(now_utc=now, utcnow=lambda: now).to_dict(),
            "inventory_sha256": inventory_digest,
            "inventory_checked_at": checked_at,
            "created_at": created,
            "deadline_at": expires,
            "status": "reserved",
            "dispatch": {"state": "pending", "attempts": 0},
            "owner": None,
            "lease_until": None,
            "media_sha256": None,
            "upload": "held",
        }
        generation["video_recovery"] = {"schema": SCHEMA, "epochs": [*entries, record]}

    _write(storage, job_id, change)
    return next(
        entry
        for entry in _epochs(_document(storage.get_bytes(f"jobs/{job_id}/manifest.json"), job_id))
        if entry["request_id"] == request_id
    )


def encode_message(job_id: str, epoch_id: str) -> str:
    data = {"schema_version": QUEUE_SCHEMA, "job_id": job_id, "epoch_id": epoch_id}
    return base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def parse_message(body: str) -> tuple[str, str] | None:
    decoded: bytes | str = body
    try:
        try:
            decoded = base64.b64decode(body, validate=True)
            data = json.loads(decoded)
        except (ValueError, UnicodeDecodeError):
            data = json.loads(body)
        if isinstance(data, dict) and data.get("schema_version") == QUEUE_SCHEMA:
            job_id, epoch_id = data["job_id"], data["epoch_id"]
            if job_id not in CANDIDATES or not isinstance(epoch_id, str) or not epoch_id:
                raise ValueError("invalid recovery message")
            return job_id, epoch_id
        if isinstance(data, dict) and data.get("epoch_id") is not None:
            raise ValueError("unknown recovery queue schema")
        return None
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        if '"epoch_id"' in str(decoded) or '"epoch_id"' in body:
            raise RecoveryDenied("invalid recovery queue message") from None
        return None


def dispatch(storage: Any, producer: Any, job_id: str, epoch_id: str, *, now=None) -> None:
    """Persist send attempt first; duplicate deliveries only compete for this epoch."""
    now = now or datetime.now(timezone.utc)
    _check_legacy(storage, job_id)

    def mark_attempt(doc: dict[str, Any]) -> None:
        entry = latest(doc)
        if entry is None or entry["id"] != epoch_id or entry["status"] != "reserved":
            raise RecoveryDenied("reservation is not pending")
        if _time(entry["deadline_at"]) <= now:
            raise RecoveryDenied("recovery deadline passed before send")
        if entry["dispatch"]["state"] == "acknowledged":
            return
        entry["dispatch"]["attempts"] += 1
        entry["dispatch"]["state"] = "sending"

    existing = latest(_document(storage.get_bytes(f"jobs/{job_id}/manifest.json"), job_id))
    if existing is None or existing["id"] != epoch_id:
        raise RecoveryDenied("reservation not found")
    if existing.get("status") == "completed":
        return
    if _time(existing["deadline_at"]) <= now:
        terminalize(storage, job_id, epoch_id, reason="deadline_expired", now=now)
        raise RecoveryDenied("recovery deadline passed before dispatch")
    if existing.get("status") not in {"reserved", "running"}:
        raise RecoveryDenied("recovery epoch is terminal")
    if existing["dispatch"]["state"] == "acknowledged":
        return
    if existing["status"] == "running":
        raise RecoveryDenied("delivery already claimed; wait for its lease")
    _write(storage, job_id, mark_attempt)
    producer.send_message(encode_message(job_id, epoch_id))

    def acknowledge(doc: dict[str, Any]) -> None:
        entry = latest(doc)
        if entry is None or entry["id"] != epoch_id:
            raise RecoveryDenied("reservation changed before send acknowledgment")
        if entry["dispatch"]["state"] == "sending":
            entry["dispatch"]["state"] = "acknowledged"

    _write(storage, job_id, acknowledge)


@dataclass(frozen=True)
class Authority:
    storage: Any
    job_id: str
    epoch_id: str
    owner: str

    def check(self, doc: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        entry = latest(doc)
        instant = now or datetime.now(timezone.utc)
        if (
            entry is None
            or entry.get("id") != self.epoch_id
            or entry.get("status") != "running"
            or entry.get("owner") != self.owner
            or entry.get("upload") != "held"
            or _time(entry.get("lease_until")) <= instant
            or _time(entry.get("deadline_at")) <= instant
            or doc["generation"].get("video_runner", {}).get("status") == "completed"
        ):
            raise RecoveryDenied("recovery ownership expired or was superseded")
        _check_legacy(self.storage, self.job_id)
        if entry.get("proof") != _proof(doc, self.storage, self.job_id):
            raise RecoveryDenied("original media or review proof changed")
        return entry

    def assert_current(self) -> None:
        self.check(
            _document(self.storage.get_bytes(f"jobs/{self.job_id}/manifest.json"), self.job_id)
        )

    def renew(self, *, now: datetime | None = None) -> None:
        instant = now or datetime.now(timezone.utc)

        def change(doc: dict[str, Any]) -> None:
            entry = self.check(doc, now=instant)
            entry["lease_until"] = min(
                instant + timedelta(seconds=LEASE_SECONDS), _time(entry["deadline_at"])
            ).isoformat()

        _write(self.storage, self.job_id, change)


def guard_manifest(doc: dict[str, Any], authority: Authority | None) -> None:
    if authority is not None:
        authority.check(doc)
    elif "video_recovery" in doc.get("generation", {}):
        raise RecoveryDenied("old video delivery cannot write a recovery manifest")


def claim(
    storage: Any,
    job_id: str,
    epoch_id: str,
    *,
    visibility_until: datetime | None,
    owner: str | None = None,
    now: datetime | None = None,
) -> Authority:
    """One conditional manifest write fences the old runner and installs the new owner."""
    now = now or datetime.now(timezone.utc)
    owner = owner or uuid.uuid4().hex
    if (
        visibility_until is None
        or visibility_until.tzinfo is None
        or visibility_until.utcoffset() is None
        or visibility_until <= now
        or not owner
    ):
        raise RecoveryDenied("authoritative queue visibility and fresh owner are required")
    _check_legacy(storage, job_id)

    def change(doc: dict[str, Any]) -> None:
        entry = latest(doc)
        if entry is None or entry.get("id") != epoch_id:
            raise RecoveryDenied("recovery epoch is absent or stale")
        if entry.get("dispatch", {}).get("attempts", 0) < 1:
            raise RecoveryDenied("queue delivery has no durable send intent")
        if VideoStageBudget.from_dict(
            entry["budget"], now_utc=now, utcnow=lambda: now
        ).projection.deadline_at_utc != _time(entry["deadline_at"]):
            raise RecoveryDenied("recovery budget and deadline disagree")
        if entry.get("proof") != _proof(doc, storage, job_id):
            raise RecoveryDenied("original proof changed since reservation")
        if doc["generation"].get("video_runner", {}).get("status") == "completed":
            raise RecoveryDenied("completed runner can only be settled")
        if _time(entry["deadline_at"]) <= now:
            raise RecoveryDenied("recovery deadline passed")
        if visibility_until < _time(entry["deadline_at"]):
            raise RecoveryDenied("queue visibility does not cover the recovery deadline")
        if entry.get("status") == "running":
            if _time(entry.get("lease_until")) > now:
                raise RecoveryDenied("current recovery owner still holds the lease")
            if entry.get("owner") == owner:
                raise RecoveryDenied("redelivery requires a fresh ownership token")
        elif entry.get("status") != "reserved":
            raise RecoveryDenied("recovery epoch is not runnable")
        previous_runner = doc["generation"].get("video_runner")
        if isinstance(previous_runner, dict) and previous_runner.get("epoch_id") == epoch_id:
            entry.setdefault("runner_attempts", []).append(previous_runner)
        entry["status"] = "running"
        entry["owner"] = owner
        entry["lease_until"] = min(
            visibility_until, now + timedelta(seconds=LEASE_SECONDS), _time(entry["deadline_at"])
        ).isoformat()
        doc["generation"]["video_runner"] = {
            "status": "running",
            "epoch_id": epoch_id,
            "at": now.isoformat(),
        }

    _write(storage, job_id, change)
    return Authority(storage, job_id, epoch_id, owner)


def bind_outbox(
    storage: Any, job_id: str, authority: Authority, outbox_id: str, media_sha256: str
) -> None:
    if not outbox_id or not isinstance(media_sha256, str) or len(media_sha256) != 64:
        raise RecoveryDenied("private outbox media proof is incomplete")

    def change(doc: dict[str, Any]) -> None:
        entry = authority.check(doc)
        if entry.get("outbox_id") not in (None, outbox_id) or entry.get("media_sha256") not in (
            None,
            media_sha256,
        ):
            raise RecoveryDenied("another private media artifact already owns this epoch")
        entry["outbox_id"] = outbox_id
        entry["media_sha256"] = media_sha256

    _write(storage, job_id, change)


def terminalize(
    storage: Any, job_id: str, epoch_id: str, *, reason: str, now: datetime | None = None
) -> None:
    instant = now or datetime.now(timezone.utc)

    def change(doc: dict[str, Any]) -> None:
        entry = latest(doc)
        if entry is None or entry.get("id") != epoch_id:
            raise RecoveryDenied("cannot terminalize another epoch")
        if doc["generation"].get("video_runner", {}).get("status") == "completed":
            raise RecoveryDenied("completed runner must be settled, not expired")
        if entry["status"] in {"completed", "failed", "expired"}:
            return
        if _time(entry["deadline_at"]) > instant and reason == "deadline_expired":
            raise RecoveryDenied("epoch deadline has not expired")
        entry["status"] = "expired" if reason == "deadline_expired" else "failed"
        entry["terminal_reason"] = reason
        entry["terminal_at"] = instant.isoformat()

    _write(storage, job_id, change)


def settle(storage: Any, job_id: str, epoch_id: str, *, media_sha256: str, outbox_id: str) -> None:
    """Settlement only verifies an already committed completed runner; never rerenders."""
    if not isinstance(media_sha256, str) or len(media_sha256) != 64 or not outbox_id:
        raise RecoveryDenied("completed media and held outbox proofs are required")

    from podcaster.distribution_outbox import (
        ArtifactReference,
        DistributionOutboxError,
        DistributionOutboxRepository,
        verify_artifact,
    )

    try:
        outbox = DistributionOutboxRepository(storage).read(outbox_id)
    except (DistributionOutboxError, ValueError, TypeError) as exc:
        raise RecoveryDenied("held outbox proof is missing or corrupt") from exc
    if not isinstance(outbox, dict):
        raise RecoveryDenied("held outbox was not persisted")
    try:
        artifact = ArtifactReference(**outbox["artifact"])
        if (
            artifact.sha256 != media_sha256
            or artifact.media_kind != "video"
            or outbox.get("media_review", {}).get("status") != "pending"
        ):
            raise RecoveryDenied("outbox is not held for this exact completed video")
        verify_artifact(storage, artifact)
    except (KeyError, TypeError, ValueError, DistributionOutboxError) as exc:
        raise RecoveryDenied("held outbox media proof is invalid") from exc

    def change(doc: dict[str, Any]) -> None:
        entry = latest(doc)
        runner = doc["generation"].get("video_runner")
        if (
            entry is None
            or entry.get("id") != epoch_id
            or entry.get("upload") != "held"
            or entry.get("outbox_id") != outbox_id
            or entry.get("media_sha256") != media_sha256
            or entry.get("proof") != _proof(doc, storage, job_id, allow_completed=True)
            or outbox.get("publication_identity") != entry.get("identity")
            or not isinstance(runner, dict)
            or runner.get("status") != "completed"
            or runner.get("epoch_id") != epoch_id
            or runner.get("media_sha256") != media_sha256
            or runner.get("distribution", {}).get("outbox_id") != outbox_id
        ):
            raise RecoveryDenied("completed runner and held outbox proof do not match")
        entry["status"] = "completed"
        entry["media_sha256"] = media_sha256

    _write(storage, job_id, change)
