"""Durable, operator-owned private video recovery for two reviewed weekly jobs."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from podcaster.publication_state import publication_identity, read_evidence
from podcaster.review import ReviewBindingError, is_exact_script_job, validate_private_media_inputs
from podcaster.video.budget import VideoStage, VideoStageBudget

_CONTENT_TYPE = "application/json; charset=utf-8"
_CANDIDATES = {
    "podcast-2026-W44-00bc06dfb6e2": (
        "2026-W44",
        "40269058395",
        "2a34575cd7be0d0a6ec36b4a808a1909ae7f793d00a8511da464ec063cf2bfa0",
        "5c1fc333deb75ea2bfff990c1222f97a12acb2c3234e19df26717a27e8ff6f56",
        11664239,
    ),
    "podcast-2026-W45-9798e93edc06": (
        "2026-W45",
        "41395629030",
        "fc4023679d8b2ba90481f783ebb9c57e61e059db3790c9c9d3c4a996b0242e34",
        "a64c9c664a510b94207a566c92c3cc0465af241a5304109b845d1317284f5d3b",
        12170805,
    ),
}
_ACTIVE = frozenset({"pending", "sent", "running"})


def is_candidate(job_id: str) -> bool:
    return job_id in _CANDIDATES


class RecoveryError(ValueError):
    """Recovery admission or ownership could not be proven."""


def recovery_path(job_id: str) -> str:
    return f"jobs/{job_id}/video/recovery-epochs.json"


def record_rejection(
    storage: Any, job_id: str, *, operator: str, request_id: str, reason: str
) -> None:
    """Audit a refused authorized request without modifying its original proof."""
    if job_id not in _CANDIDATES:
        return
    path = f"jobs/{job_id}/video/recovery-rejections.json"

    def apply(raw: bytes | None) -> bytes:
        document = json.loads(raw) if raw is not None else {"schema_version": 1, "events": []}
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != 1
            or not isinstance(document.get("events"), list)
        ):
            raise RecoveryError("recovery rejection audit is invalid")
        document["events"].append(
            {
                "at": _utc(datetime.now(timezone.utc)),
                "operator": operator,
                "request_digest": hashlib.sha256(request_id.encode()).hexdigest(),
                "reason": reason,
            }
        )
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()

    storage.update_bytes(path, _CONTENT_TYPE, apply)


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RecoveryError("recovery timestamp must be UTC-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime:
    if not isinstance(value, str):
        raise RecoveryError("recovery timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryError("recovery timestamp is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecoveryError("recovery timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _read(storage: Any, job_id: str) -> dict[str, Any]:
    raw = storage.get_bytes(recovery_path(job_id))
    if raw is None:
        return {"schema_version": 1, "job_id": job_id, "epochs": []}
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RecoveryError("recovery record is corrupt") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or document.get("job_id") != job_id
        or not isinstance(document.get("epochs"), list)
    ):
        raise RecoveryError("recovery record is invalid")
    return document


def _update(storage: Any, job_id: str, change: Callable[[dict[str, Any]], Any]) -> Any:
    result: dict[str, Any] = {}

    def apply(raw: bytes | None) -> bytes:
        document = (
            json.loads(raw)
            if raw is not None
            else {"schema_version": 1, "job_id": job_id, "epochs": []}
        )
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != 1
            or document.get("job_id") != job_id
            or not isinstance(document.get("epochs"), list)
        ):
            raise RecoveryError("recovery record is invalid")
        result["value"] = change(document)
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()

    storage.update_bytes(recovery_path(job_id), _CONTENT_TYPE, apply)
    return result["value"]


def _epoch(document: dict[str, Any], epoch_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in document["epochs"]
        if isinstance(item, dict) and item.get("epoch_id") == epoch_id
    ]
    if len(matches) != 1:
        raise RecoveryError("recovery epoch is missing or ambiguous")
    return matches[0]


def _expire(entry: dict[str, Any], now: datetime) -> None:
    if entry["state"] in _ACTIVE and _parse(entry["budget"]["deadline_at_utc"]) <= now:
        entry["state"] = "expired"
        entry["terminal_at"] = _utc(now)


def current_epoch(storage: Any, job_id: str) -> dict[str, Any] | None:
    epochs = _read(storage, job_id)["epochs"]
    if not epochs:
        return None
    entry = epochs[-1]
    if not isinstance(entry, dict) or not isinstance(entry.get("epoch_id"), str):
        raise RecoveryError("recovery epoch record is invalid")
    return entry


def assert_private_hold(
    storage: Any, job_id: str, *, epoch_id: str | None = None, media_sha256: str | None = None
) -> None:
    """Recovery media can be prepared, but cannot cross any provider-write edge."""
    if not is_candidate(job_id) and epoch_id is None:
        return
    entry = current_epoch(storage, job_id)
    if entry is None:
        if is_candidate(job_id) or epoch_id is not None:
            raise RecoveryError("recovery metadata disappeared")
        return
    if epoch_id is not None and entry.get("epoch_id") != epoch_id:
        raise RecoveryError("stale recovery epoch")
    if media_sha256 is not None and entry.get("audio_sha256") != media_sha256:
        raise RecoveryError("recovery media binding changed")
    raise RecoveryError("recovery provider upload is not authorized in this release")


def assert_provider_write_allowed(
    storage: Any, job_id: str, *, epoch_id: str | None = None
) -> None:
    """Re-read both durable recovery fences at the irreversible adapter edge."""
    assert_private_hold(storage, job_id, epoch_id=epoch_id)
    raw = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    if raw is None:
        if is_candidate(job_id) or epoch_id is not None:
            raise RecoveryError("provider-write manifest is missing")
        return
    try:
        manifest = json.loads(raw)
        marker = manifest.get("generation", {}).get("video_recovery_epoch_id")
    except (ValueError, AttributeError, TypeError) as exc:
        raise RecoveryError("provider-write manifest cannot be verified") from exc
    if marker is not None:
        raise RecoveryError("recovery provider upload is not authorized in this release")


def assert_manifest_writer(
    storage: Any,
    job_id: str,
    generation: dict[str, Any],
    *,
    epoch_id: str | None,
    owner: str | None,
    replace_runner: bool = False,
) -> None:
    """Fence a video attempt against the current epoch and CAS manifest image."""
    if not is_candidate(job_id) and epoch_id is None:
        return
    entry = current_epoch(storage, job_id)
    marker = generation.get("video_recovery_epoch_id")
    runner = generation.get("video_runner")
    if (
        isinstance(runner, dict)
        and runner.get("status") == "completed"
        and (epoch_id is None or replace_runner)
    ):
        raise RecoveryError("completed video runner cannot be replaced")
    if epoch_id is None:
        if entry is not None or marker is not None:
            raise RecoveryError("historical video attempt is fenced by recovery")
        return
    if (
        entry is None
        or entry.get("epoch_id") != epoch_id
        or entry.get("state") != "running"
        or entry.get("owner") != owner
        or marker != epoch_id
        or generation.get("video_recovery_fence") != owner
    ):
        raise RecoveryError("recovery manifest writer is stale")
    assert_owner(storage, job_id, epoch_id, owner)


def assert_reserved_inputs(
    entry: dict[str, Any], manifest: dict[str, Any], job_id: str, media: Any
) -> None:
    """Rebind current immutable inputs to the operator's original reservation."""
    identity = entry["identity"]
    try:
        if (
            any(manifest["request"].get(key) != value for key, value in identity.items())
            or publication_identity(manifest, job_id, identity["publish_run_id"]).week
            != identity["week"]
            or manifest["review"]["snapshot"]["script_sha256"] != entry["script_sha256"]
            or len(media.content) != _CANDIDATES[job_id][4]
            or hashlib.sha256(media.content).hexdigest() != entry["audio_sha256"]
        ):
            raise RecoveryError("recovery inputs changed after reservation")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError("recovery inputs cannot be bound to reservation") from exc


def _verify_original(
    storage: Any,
    job_id: str,
    identity: dict[str, str],
    script_sha256: str,
    mp3_sha256: str,
    *,
    existing_epoch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = _CANDIDATES.get(job_id)
    if candidate is None:
        raise RecoveryError("job is not an approved video-only recovery candidate")
    raw = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    if raw is None:
        raise RecoveryError("original manifest is missing")
    try:
        manifest = json.loads(raw)
        request = manifest["request"]
        expected = dict(
            zip(
                ("week", "publish_run_id", "article_sha256", "manifest_sha256"),
                candidate[:4],
                strict=True,
            )
        )
        if identity != expected or any(
            request.get(key) != value for key, value in expected.items()
        ):
            raise RecoveryError("original publication identity does not match")
        if (
            publication_identity(manifest, job_id, expected["publish_run_id"]).week
            != expected["week"]
            or not is_exact_script_job(manifest)
            or manifest["review"]["snapshot"]["script_sha256"] != script_sha256
        ):
            raise RecoveryError("original script review binding does not match")
        verified = validate_private_media_inputs(manifest, storage, job_id)
        if len(verified.content) != candidate[4]:
            raise RecoveryError("original MP3 byte size does not match")
        if hashlib.sha256(verified.content).hexdigest() != mp3_sha256:
            raise RecoveryError("original MP3 digest does not match")
        video_runner = manifest.get("generation", {}).get("video_runner")
        if not isinstance(video_runner, dict):
            video_runner = {}
        if video_runner.get("distribution"):
            outbox_id = video_runner["distribution"].get("outbox_id")
            if (
                existing_epoch is None
                or manifest["generation"].get("video_recovery_epoch_id")
                != existing_epoch["epoch_id"]
                or not isinstance(outbox_id, str)
            ):
                raise RecoveryError("prior video distribution requires reconciliation")
            from podcaster.distribution_outbox import DistributionOutboxRepository

            outbox = DistributionOutboxRepository(storage).read(outbox_id)
            if (
                not isinstance(outbox, dict)
                or outbox.get("publication_identity", {}).get("accepted_job_id") != job_id
                or outbox.get("artifact", {}).get("sha256") != existing_epoch.get("media_sha256")
                or outbox.get("media_review", {}).get("status") not in {"pending", "approved"}
                or not any(
                    leg.get("context", {}).get("private_recovery_epoch_id")
                    == existing_epoch["epoch_id"]
                    for leg in outbox.get("providers", {}).values()
                )
            ):
                raise RecoveryError("prior video distribution requires reconciliation")
        evidence = read_evidence(storage, job_id)
        if evidence and evidence.get("corrupt"):
            raise RecoveryError("prior video provider evidence is corrupt")
        if evidence and evidence.get("records"):
            if any(
                item.get("media_kind") == "video"
                for item in evidence["records"]
                if isinstance(item, dict)
            ):
                raise RecoveryError("prior video provider intent requires reconciliation")
    except (KeyError, TypeError, AttributeError, ValueError, ReviewBindingError) as exc:
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError("original review or media proof is incomplete") from exc
    return manifest


def reserve(
    storage: Any,
    job_id: str,
    *,
    identity: dict[str, str],
    script_sha256: str,
    mp3_sha256: str,
    request_id: str,
    operator: str,
    inventory_reference: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate proofs before CAS; same request resumes only the original epoch."""
    current = now or datetime.now(timezone.utc)
    if not re.fullmatch(r"[a-f0-9]{64}", script_sha256) or not re.fullmatch(
        r"[a-f0-9]{64}", mp3_sha256
    ):
        raise RecoveryError("exact script and MP3 digests are required")
    try:
        uuid.UUID(request_id)
    except (TypeError, ValueError) as exc:
        raise RecoveryError("a UUID idempotency key is required") from exc
    if (
        not operator.strip()
        or not inventory_reference.strip()
        or len(inventory_reference) > 256
        or any(ord(char) < 32 for char in inventory_reference)
    ):
        raise RecoveryError("named operator and provider inventory reference are required")
    previous = current_epoch(storage, job_id)
    existing_epoch = (
        previous if previous is not None and previous.get("request_id") == request_id else None
    )
    original_manifest = _verify_original(
        storage,
        job_id,
        identity,
        script_sha256,
        mp3_sha256,
        existing_epoch=existing_epoch,
    )
    old_raw = storage.get_bytes(f"jobs/{job_id}/video-budget.json")
    if old_raw is None:
        raise RecoveryError("historical video attempt budget is missing")
    try:
        old_budget = VideoStageBudget.from_dict(
            json.loads(old_raw), now_utc=current, utcnow=lambda: current
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise RecoveryError("historical video attempt budget is invalid") from exc
    if old_budget.admit(VideoStage.SHUTDOWN).allowed:
        raise RecoveryError("historical video attempt has not expired")
    epoch_id = hashlib.sha256(f"{job_id}:{request_id}".encode()).hexdigest()[:32]
    proposed_budget = VideoStageBudget.start(now_utc=current, utcnow=lambda: current).to_dict()

    def change(document: dict[str, Any]) -> dict[str, Any]:
        existing = document["epochs"]
        if existing:
            previous = existing[-1]
            _expire(previous, current)
            if any(
                item.get("request_id") == request_id
                for item in existing[:-1]
                if isinstance(item, dict)
            ):
                raise RecoveryError("idempotency key belongs to a prior epoch")
            if previous.get("request_id") == request_id:
                if (
                    previous.get("operator") != operator
                    or previous.get("inventory_reference") != inventory_reference
                    or previous.get("audio_sha256") != mp3_sha256
                    or previous.get("script_sha256") != script_sha256
                ):
                    raise RecoveryError("idempotency key conflicts with prior authorization")
                return dict(previous)
            if previous.get("state") in _ACTIVE:
                raise RecoveryError("another recovery epoch is still active")
            if any(
                item.get("inventory_reference") == inventory_reference
                for item in existing
                if isinstance(item, dict)
            ):
                raise RecoveryError("a further epoch requires fresh provider reconciliation")
        entry = {
            "epoch_id": epoch_id,
            "sequence": len(existing) + 1,
            "request_id": request_id,
            "operator": operator,
            "identity": identity,
            "inventory_reference": inventory_reference,
            "script_sha256": script_sha256,
            "audio_sha256": mp3_sha256,
            "budget": proposed_budget,
            "historical_budget": old_budget.to_dict(),
            "historical_video_runner": original_manifest.get("generation", {}).get("video_runner"),
            "state": "pending",
            "dispatch_intent": hashlib.sha256(f"{job_id}:{epoch_id}:video".encode()).hexdigest(),
            "send_attempts": 0,
            "owner": None,
            "lease_expires_at": None,
            "created_at": _utc(current),
        }
        existing.append(entry)
        return dict(entry)

    entry = _update(storage, job_id, change)

    def mark_manifest(raw: bytes | None) -> bytes:
        if raw is None:
            raise RecoveryError("original manifest disappeared")
        document = json.loads(raw)
        if document.get("job_id") != job_id:
            raise RecoveryError("original manifest changed during reservation")
        generation = document["generation"]
        previous_sequence = generation.get("video_recovery_epoch_sequence", 0)
        if not isinstance(previous_sequence, int) or previous_sequence > entry["sequence"]:
            raise RecoveryError("newer recovery epoch already marked the manifest")
        if (
            previous_sequence == entry["sequence"]
            and generation.get("video_recovery_epoch_id") != entry["epoch_id"]
        ):
            raise RecoveryError("recovery marker conflicts with reserved epoch")
        generation["video_recovery_epoch_sequence"] = entry["sequence"]
        generation["video_recovery_epoch_id"] = entry["epoch_id"]
        if previous_sequence != entry["sequence"]:
            generation.pop("video_recovery_fence", None)
            generation.pop("video_recovery_lease_expires_at", None)
        from podcaster.generation import manifest_bytes

        return manifest_bytes(document)

    storage.update_bytes(f"jobs/{job_id}/manifest.json", _CONTENT_TYPE, mark_manifest)
    return entry


def reconcile_dispatch(
    storage: Any, job_id: str, epoch_id: str, producer: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    """Replay a durable pending send; duplicate queue bodies share the epoch fence."""
    from podcaster.queue import encode_video_recovery_message

    current = now or datetime.now(timezone.utc)

    def prepare(document: dict[str, Any]) -> dict[str, Any]:
        entry = _epoch(document, epoch_id)
        _expire(entry, current)
        if entry["state"] == "expired":
            return dict(entry)
        if entry["state"] == "running":
            lease = entry.get("lease_expires_at")
            if lease is None or _parse(lease) > current:
                return dict(entry)
            entry["state"] = "pending"
            entry["owner"] = None
            entry["lease_expires_at"] = None
        if entry["state"] in {"sent", "completed"}:
            return dict(entry)
        if entry["state"] != "pending":
            raise RecoveryError("recovery dispatch is terminal")
        entry["send_attempts"] += 1
        return dict(entry)

    entry = _update(storage, job_id, prepare)
    if entry["state"] == "expired":
        raise RecoveryError("recovery epoch deadline has expired")
    if entry["state"] != "pending":
        return entry
    if producer is None:
        _update(
            storage,
            job_id,
            lambda doc: _terminalize_dispatch(_epoch(doc, epoch_id), current),
        )
        raise RecoveryError("recovery queue is not configured")
    producer.send_message(encode_video_recovery_message(job_id, epoch_id))

    def ack(document: dict[str, Any]) -> dict[str, Any]:
        settled = _epoch(document, epoch_id)
        _expire(settled, current)
        if settled["state"] == "pending":
            settled["state"] = "sent"
            settled["sent_at"] = _utc(current)
        return dict(settled)

    return _update(storage, job_id, ack)


def _terminalize_dispatch(entry: dict[str, Any], now: datetime) -> dict[str, Any]:
    if entry["state"] == "pending":
        entry["state"] = "dispatch_failed"
        entry["terminal_at"] = _utc(now)
    return dict(entry)


def fail_missing_visibility(storage: Any, job_id: str, epoch_id: str) -> None:
    now = datetime.now(timezone.utc)

    def change(document: dict[str, Any]) -> None:
        entry = _epoch(document, epoch_id)
        if entry["state"] == "sent":
            entry["state"] = "visibility_unknown"
            entry["terminal_at"] = _utc(now)
        elif entry["state"] != "running":
            raise RecoveryError("recovery message is no longer active")

    _update(storage, job_id, change)


def claim(
    storage: Any,
    job_id: str,
    epoch_id: str,
    owner: str,
    visibility_expires_at: datetime,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)

    def change(document: dict[str, Any]) -> dict[str, Any]:
        entry = _epoch(document, epoch_id)
        if document["epochs"][-1] is not entry:
            raise RecoveryError("recovery message is from a stale epoch")
        _expire(entry, current)
        if entry["state"] == "expired":
            return dict(entry)
        # A delivered message can outrun the sender's separate sent-ack CAS.
        if entry["state"] == "pending" and entry.get("send_attempts", 0) < 1:
            raise RecoveryError("recovery send has not been attempted")
        if entry["state"] not in {"pending", "sent", "running"}:
            raise RecoveryError("recovery epoch is not runnable")
        lease = min(_parse(entry["budget"]["deadline_at_utc"]), visibility_expires_at)
        if lease <= current + timedelta(seconds=5):
            raise RecoveryError("recovery lease or queue visibility is insufficient")
        previous = entry.get("lease_expires_at")
        if entry["state"] == "running" and entry.get("owner") == owner:
            raise RecoveryError("recovery claim requires a fresh owner token")
        if (
            entry["state"] == "running"
            and previous is not None
            and _parse(previous) > current
            and entry["owner"] != owner
        ):
            raise RecoveryError("recovery epoch is owned by another worker")
        entry["owner"] = owner
        entry["lease_expires_at"] = _utc(lease)
        entry["state"] = "running"
        return dict(entry)

    result = _update(storage, job_id, change)
    if result["state"] == "expired":
        raise RecoveryError("recovery epoch has expired")

    def fence_manifest(raw: bytes | None) -> bytes:
        if raw is None:
            raise RecoveryError("recovery manifest is missing")
        document = json.loads(raw)
        generation = document.get("generation")
        if (
            not isinstance(generation, dict)
            or generation.get("video_recovery_epoch_id") != epoch_id
        ):
            raise RecoveryError("recovery manifest epoch is stale")
        current_owner = generation.get("video_recovery_fence")
        previous_lease = generation.get("video_recovery_lease_expires_at")
        if current_owner == owner:
            raise RecoveryError("recovery claim requires a fresh owner token")
        if current_owner is not None:
            if previous_lease is None or _parse(previous_lease) > current:
                raise RecoveryError("recovery manifest is owned by another worker")
        if current_epoch(storage, job_id).get("owner") != owner:
            raise RecoveryError("recovery claim was superseded")
        generation["video_recovery_fence"] = owner
        generation["video_recovery_lease_expires_at"] = result["lease_expires_at"]
        from podcaster.generation import manifest_bytes

        return manifest_bytes(document)

    storage.update_bytes(f"jobs/{job_id}/manifest.json", _CONTENT_TYPE, fence_manifest)
    return result


def assert_owner(storage: Any, job_id: str, epoch_id: str, owner: str) -> None:
    entry = current_epoch(storage, job_id)
    raw = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    try:
        generation = json.loads(raw)["generation"] if raw is not None else {}
    except (ValueError, KeyError, TypeError) as exc:
        raise RecoveryError("recovery manifest fence is invalid") from exc
    if (
        entry is None
        or entry.get("epoch_id") != epoch_id
        or entry.get("state") != "running"
        or entry.get("owner") != owner
        or _parse(entry.get("lease_expires_at")) <= datetime.now(timezone.utc)
        or generation.get("video_recovery_epoch_id") != epoch_id
        or generation.get("video_recovery_fence") != owner
    ):
        raise RecoveryError("recovery owner or deadline is stale")


def bind_private_media(storage: Any, job_id: str, epoch_id: str, owner: str, sha256: str) -> None:
    if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise RecoveryError("completed private media digest is missing")

    def change(document: dict[str, Any]) -> None:
        entry = _epoch(document, epoch_id)
        if entry["state"] != "running" or entry.get("owner") != owner:
            raise RecoveryError("private media owner is stale")
        if entry.get("media_sha256") not in (None, sha256):
            raise RecoveryError("completed media conflicts with the original epoch")
        entry["media_sha256"] = sha256

    assert_owner(storage, job_id, epoch_id, owner)
    _update(storage, job_id, change)


def expire_epoch(storage: Any, job_id: str, epoch_id: str) -> None:
    now = datetime.now(timezone.utc)

    def change(document: dict[str, Any]) -> None:
        entry = _epoch(document, epoch_id)
        _expire(entry, now)
        if entry["state"] != "expired":
            raise RecoveryError("recovery epoch has not expired")

    _update(storage, job_id, change)


def fail_epoch(storage: Any, job_id: str, epoch_id: str, owner: str, reason: str) -> None:
    def change(document: dict[str, Any]) -> None:
        entry = _epoch(document, epoch_id)
        if entry["state"] != "running" or entry.get("owner") != owner:
            raise RecoveryError("recovery failure owner is stale")
        entry["state"] = "proof_rejected"
        entry["terminal_reason"] = reason
        entry["terminal_at"] = _utc(datetime.now(timezone.utc))

    _update(storage, job_id, change)


def _verified_completion_digest(storage: Any, job_id: str, epoch_id: str) -> str:
    from podcaster.distribution_outbox import DistributionOutboxRepository

    entry = current_epoch(storage, job_id)
    if entry is None or entry.get("epoch_id") != epoch_id:
        raise RecoveryError("recovery completion epoch is stale")
    try:
        manifest = json.loads(storage.get_bytes(f"jobs/{job_id}/manifest.json"))
        generation = manifest["generation"]
        runner = generation["video_runner"]
        outbox_id = runner["distribution"]["outbox_id"]
        outbox = DistributionOutboxRepository(storage).read(outbox_id)
        artifact = outbox["artifact"]
        media = storage.get_bytes(artifact["path"])
        if (
            generation.get("video_recovery_epoch_id") != epoch_id
            or runner.get("status") != "completed"
            or outbox["publication_identity"]["accepted_job_id"] != job_id
            or entry.get("media_sha256") != artifact["sha256"]
            or media is None
            or hashlib.sha256(media).hexdigest() != artifact["sha256"]
            or not any(
                provider.get("context", {}).get("private_recovery_epoch_id") == epoch_id
                and provider.get("context", {}).get("private_recovery_media_sha256")
                == artifact["sha256"]
                for provider in outbox["providers"].values()
            )
        ):
            raise RecoveryError("recovery completion evidence is incomplete")
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        raise RecoveryError("recovery completion evidence is unavailable") from exc
    return artifact["sha256"]


def complete_prepared_after_expiry(storage: Any, job_id: str, epoch_id: str) -> None:
    """Settle an already archived private MP4 without restarting an expired attempt."""
    digest = _verified_completion_digest(storage, job_id, epoch_id)

    def change(document: dict[str, Any]) -> None:
        entry = _epoch(document, epoch_id)
        if document["epochs"][-1] is not entry or entry["state"] != "expired":
            raise RecoveryError("expired recovery completion epoch is stale")
        if entry.get("media_sha256") != digest:
            raise RecoveryError("recovery completion media changed")
        entry["state"] = "completed"
        entry["terminal_at"] = _utc(datetime.now(timezone.utc))

    _update(storage, job_id, change)


def complete(storage: Any, job_id: str, epoch_id: str, owner: str) -> None:
    digest = _verified_completion_digest(storage, job_id, epoch_id)

    def change(document: dict[str, Any]) -> None:
        entry = _epoch(document, epoch_id)
        if entry["state"] != "running" or entry.get("owner") != owner:
            raise RecoveryError("recovery completion owner is stale")
        if entry.get("media_sha256") != digest:
            raise RecoveryError("recovery completion media changed")
        entry["state"] = "completed"
        entry["terminal_at"] = _utc(datetime.now(timezone.utc))

    _update(storage, job_id, change)
