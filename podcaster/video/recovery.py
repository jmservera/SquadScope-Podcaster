"""Manifest-fenced, private-only continuation of an already reviewed video job."""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from podcaster.generation import manifest_bytes
from podcaster.publication_state import PublicationIdentity, publication_identity, read_evidence
from podcaster.review import ReviewBindingError, validate_private_media_inputs
from podcaster.storage import StorageBackend
from podcaster.video.budget import BudgetProjection, VideoStageBudget

CONTENT_TYPE = "application/json; charset=utf-8"
SCHEMA = "private-video-recovery-v1"
LEASE_SECONDS = 1800
RECOVERY_MESSAGE_SCHEMA = "private-video-recovery-message-v1"
Clock = Callable[[], datetime]
logger = logging.getLogger(__name__)
PRIVATE_ORIGINALS = {
    "podcast-2026-W44-00bc06dfb6e2": 11_664_239,
    "podcast-2026-W45-9798e93edc06": 12_170_805,
}
SCOPED_PRIVATE_DIRECTIVE = "owner-2026-11-05-w44-w45-original-private-only"
W44_JOB = "podcast-2026-W44-00bc06dfb6e2"
W44_PREDECESSOR = "29080a3a502d40729a59e7eb440b3681"
W44_SUCCESSOR_DISPOSITION = "coordinator-w44-single-successor-disposition-20261106-140213"
W44_AUDIO_SHA256 = "5933fdecc6c76d80c81e2c845206fd0655a983713c2b20f204377ac3823153c1"
SUCCESSOR_QUIESCENCE_PATH = f"jobs/{W44_JOB}/video-recovery/{W44_PREDECESSOR}/quiescence.json"
SCOPED_IDENTITIES = {
    "podcast-2026-W44-00bc06dfb6e2": (
        "2026-W44",
        "40269058395",
        "2a34575cd7be0d0a6ec36b4a808a1909ae7f793d00a8511da464ec063cf2bfa0",
        "5c1fc333deb75ea2bfff990c1222f97a12acb2c3234e19df26717a27e8ff6f56",
    ),
    "podcast-2026-W45-9798e93edc06": (
        "2026-W45",
        "41395629030",
        "fc4023679d8b2ba90481f783ebb9c57e61e059db3790c9c9d3c4a996b0242e34",
        "a64c9c664a510b94207a566c92c3cc0465af241a5304109b845d1317284f5d3b",
    ),
}


class RecoveryDenied(ValueError):
    """Authority, exact media, or immutable identity is unavailable."""


class PrivateSelectionUnverified(RecoveryDenied):
    """A selection write may have committed, but its durable proof is unavailable."""


@dataclass(frozen=True)
class RecoveryClaim:
    job_id: str
    epoch_id: str
    owner: str
    deadline_at_utc: str
    started_at_utc: str
    proof_sha256: str


def private_original(job_id: str, audio_size: int | None = None) -> bool:
    expected = PRIVATE_ORIGINALS.get(job_id)
    return expected is not None and (audio_size is None or expected == audio_size)


def scoped_identity(job_id: str) -> PublicationIdentity:
    fields = SCOPED_IDENTITIES.get(job_id)
    if fields is None:
        raise RecoveryDenied("job is not an owner-authorized original")
    return PublicationIdentity(job_id, *fields)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RecoveryDenied("recovery clock must be UTC-aware")
    return value.astimezone(timezone.utc)


def _parse(value: Any) -> datetime:
    if not isinstance(value, str):
        raise RecoveryDenied("missing recovery timestamp")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise RecoveryDenied("invalid recovery timestamp") from exc


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _read(raw: bytes | None, job_id: str) -> dict[str, Any]:
    if raw is None:
        raise RecoveryDenied("original job manifest is missing")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RecoveryDenied("original job manifest is invalid") from exc
    if not isinstance(document, dict) or document.get("job_id") != job_id:
        raise RecoveryDenied("original job identity is invalid")
    return document


def _state(document: Mapping[str, Any]) -> dict[str, Any]:
    generation = document.get("generation")
    if not isinstance(generation, dict):
        raise RecoveryDenied("generation state is missing")
    state = generation.get("video_recovery")
    if state is None:
        return {"schema_version": SCHEMA, "epochs": [], "selected_private_output": None}
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != SCHEMA
        or not isinstance(state.get("epochs"), list)
    ):
        raise RecoveryDenied("recovery state is invalid")
    epoch_ids = [item.get("epoch_id") for item in state["epochs"] if isinstance(item, dict)]
    if (
        len(epoch_ids) != len(state["epochs"])
        or any(not isinstance(item, str) for item in epoch_ids)
        or len(set(epoch_ids)) != len(epoch_ids)
    ):
        raise RecoveryDenied("recovery epoch ledger is malformed or ambiguous")
    return state


def _proof(
    document: dict[str, Any], storage: StorageBackend, job_id: str
) -> tuple[PublicationIdentity, str]:
    try:
        reader = (
            storage.get_verified_bytes
            if callable(getattr(type(storage), "get_verified_bytes", None))
            else None
        )
        audio = validate_private_media_inputs(document, storage, job_id, artifact_reader=reader)
        if not private_original(job_id, len(audio.content)):
            raise RecoveryDenied("job is not an eligible validated-MP3 original")
        request = document["request"]
        identity = publication_identity(document, job_id, request["publish_run_id"])
        snapshot = document["review"]["snapshot"]
        proof = {
            "identity": asdict(identity),
            "review_sha256": snapshot["manifest_sha256"],
            "script_sha256": snapshot["script_sha256"],
            "audio_path": audio.path,
            "audio_sha256": hashlib.sha256(audio.content).hexdigest(),
            "audio_size": len(audio.content),
        }
        return identity, hashlib.sha256(manifest_bytes(proof)).hexdigest()
    except RecoveryDenied:
        raise
    except (ReviewBindingError, KeyError, TypeError, ValueError) as exc:
        raise RecoveryDenied("original completed media proof is unavailable") from exc


def _epoch(state: Mapping[str, Any], epoch_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in state["epochs"]
        if isinstance(item, dict) and item.get("epoch_id") == epoch_id
    ]
    if len(matches) != 1:
        raise RecoveryDenied("epoch is missing or ambiguous")
    return matches[0]


def _same_proof(
    document: dict[str, Any], storage: StorageBackend, job_id: str, epoch: Mapping[str, Any]
) -> None:
    identity, digest = _proof(document, storage, job_id)
    if epoch.get("identity") != asdict(identity) or epoch.get("proof_sha256") != digest:
        raise RecoveryDenied("reviewed publication proof changed")


def _budget(storage: StorageBackend, job_id: str, at: datetime) -> VideoStageBudget:
    raw = storage.get_bytes(f"jobs/{job_id}/video-budget.json")
    if raw is None:
        raise RecoveryDenied("historical video attempt has no durable budget")
    try:
        budget = VideoStageBudget.from_dict(json.loads(raw), now_utc=at, utcnow=lambda: at)
    except (ValueError, TypeError, KeyError) as exc:
        raise RecoveryDenied("historical video budget is invalid") from exc
    if budget.remaining_seconds() > 0:
        raise RecoveryDenied("historical video attempt has not expired")
    return budget


def _assert_provider_quiet(
    document: Mapping[str, Any], storage: StorageBackend, job_id: str
) -> None:
    generation = document["generation"]
    published = generation.get("video_publish")
    if isinstance(published, dict) and any(
        published.get(provider)
        for provider in ("youtube", "spotify", "spotify_upload", "spotify_rss")
    ):
        raise RecoveryDenied("prior provider result requires identity-wide reconciliation")
    runner = generation.get("video_runner")
    if runner is not None and not isinstance(runner, dict):
        raise RecoveryDenied("prior runner state is malformed")
    distribution = runner.get("distribution") if isinstance(runner, dict) else None
    if distribution is not None and not isinstance(distribution, dict):
        raise RecoveryDenied("prior runner distribution is malformed")
    if isinstance(distribution, dict) and (
        distribution.get("outbox_id")
        or distribution.get("youtube_id")
        or distribution.get("spotify_upload_updated")
    ):
        raise RecoveryDenied("prior distribution attempt requires identity-wide reconciliation")
    if isinstance(distribution, dict) and (
        ("provider_outcomes" in distribution and distribution["provider_outcomes"] != {})
        or ("provider_records" in distribution and distribution["provider_records"] != {})
        or (
            "spotify_rss_updated" in distribution
            and distribution["spotify_rss_updated"] is not False
        )
    ):
        raise RecoveryDenied("prior or ambiguous runner provider state requires reconciliation")
    evidence = read_evidence(storage, job_id)
    if evidence is not None:
        records = evidence.get("records")
        if evidence.get("corrupt") is True or not isinstance(records, list):
            raise RecoveryDenied("provider evidence is malformed")
        if any(
            not isinstance(record, dict)
            or not isinstance(record.get("platform"), str)
            or record.get("platform") not in {"youtube", "spotify", "spotify_rss"}
            or record.get("media_kind") != "audio"
            for record in records
        ):
            raise RecoveryDenied("prior or unknown video provider intent blocks replay")
    from podcaster.distribution_outbox import outbox_id_for, outbox_path

    identity = publication_identity(document, job_id, document["request"]["publish_run_id"])
    providers = ("youtube", "spotify", "spotify_rss")
    for count in range(1, len(providers) + 1):
        for selected in itertools.combinations(providers, count):
            candidate_id = outbox_id_for(
                identity,
                media_kind="video",
                provider_objectives={name: "public" for name in selected},
            )
            if storage.get_bytes(outbox_path(candidate_id)) is not None:
                raise RecoveryDenied("identity-wide legacy outbox requires reconciliation")


def _successor_evidence(
    storage: StorageBackend, job_id: str, state: dict[str, Any], proof: str
) -> dict[str, Any]:
    """Read the fixed private operator channel; never accept an API-supplied attestation."""
    if job_id != W44_JOB or len(state["epochs"]) != 1:
        raise RecoveryDenied("only one exact W44 successor is authorized")
    prior = state["epochs"][0]
    if (
        prior.get("epoch_id") != W44_PREDECESSOR
        or prior.get("status") != "terminal"
        or prior.get("terminal_reason") != "deadline_elapsed"
        or prior.get("authority") != SCOPED_PRIVATE_DIRECTIVE
        or prior.get("inventory_reference") != SCOPED_PRIVATE_DIRECTIVE
        or prior.get("identity") != asdict(scoped_identity(job_id))
        or prior.get("proof_sha256") != proof
        or state.get("selected_private_output") is not None
    ):
        raise RecoveryDenied("exact predecessor is not proven terminal and unselected")
    raw = storage.get_bytes(SUCCESSOR_QUIESCENCE_PATH)
    try:
        evidence = json.loads(raw) if raw is not None else None
    except (ValueError, UnicodeDecodeError) as exc:
        raise RecoveryDenied("trusted worker quiescence evidence is invalid") from exc
    if not isinstance(evidence, dict):
        raise RecoveryDenied("fresh trusted worker quiescence evidence is required")
    observed = _parse(evidence.get("observed_at"))
    executions = evidence.get("executions")
    if (
        evidence.get("schema_version") != "w44-successor-quiescence-v1"
        or evidence.get("job_id") != W44_JOB
        or evidence.get("predecessor_epoch_id") != W44_PREDECESSOR
        or evidence.get("coordinator_disposition") != W44_SUCCESSOR_DISPOSITION
        or evidence.get("predecessor_sha256") != hashlib.sha256(manifest_bytes(prior)).hexdigest()
        or evidence.get("proof_sha256") != proof
        or evidence.get("historical_budget_sha256")
        != hashlib.sha256(storage.get_bytes(f"jobs/{job_id}/video-budget.json") or b"").hexdigest()
        or observed < _parse(prior["deadline_at_utc"])
        or evidence.get("execution_inventory_complete") is not True
        or evidence.get("native_redeliveries_accounted") is not True
        or evidence.get("competing_writer_absent") is not True
        or not isinstance(executions, list)
        or not executions
    ):
        raise RecoveryDenied("trusted prior-worker inventory is stale, incomplete or mismatched")
    names = set()
    for execution in executions:
        if (
            not isinstance(execution, dict)
            or not isinstance(execution.get("name"), str)
            or execution["name"] in names
            or execution.get("status") not in {"Failed", "Succeeded", "Stopped"}
            or execution.get("replica_inventory_complete") is not True
            or not isinstance(execution.get("replicas"), list)
            or not execution["replicas"]
        ):
            raise RecoveryDenied("prior execution termination is unknown")
        names.add(execution["name"])
        replicas = set()
        for replica in execution["replicas"]:
            if (
                not isinstance(replica, dict)
                or not isinstance(replica.get("name"), str)
                or replica["name"] in replicas
                or type(replica.get("exit_code")) is not int
                or not observed
                >= _parse(replica.get("finished_at"))
                >= _parse(prior["started_at_utc"])
            ):
                raise RecoveryDenied("prior replica termination is unknown")
            replicas.add(replica["name"])
        if execution["name"] == "podcaster-yqabcnkm2junu-video-98kpp" and not {
            "9rmfj",
            "9c562",
        }.issubset({name.rsplit("-", 1)[-1] for name in replicas}):
            raise RecoveryDenied("known prior replicas are missing from trusted inventory")
    if not {
        "podcaster-yqabcnkm2junu-video-98kpp",
        "podcaster-yqabcnkm2junu-video-zb8dm",
    }.issubset(names):
        raise RecoveryDenied("known prior executions are missing from trusted inventory")
    return evidence


def _scoped_ledger(state: dict[str, Any], job_id: str) -> list[dict[str, Any]]:
    epochs = state["epochs"]
    if len(epochs) == 1:
        return epochs
    if (
        job_id != W44_JOB
        or len(epochs) != 2
        or epochs[0].get("epoch_id") != W44_PREDECESSOR
        or epochs[0].get("status") != "terminal"
        or epochs[1].get("predecessor_epoch_id") != W44_PREDECESSOR
        or epochs[1].get("authority") != W44_SUCCESSOR_DISPOSITION
        or not isinstance(epochs[1].get("quiescence_evidence"), dict)
    ):
        raise RecoveryDenied("original epoch ledger is outside the authorized scope")
    return epochs


def reserve_epoch(
    storage: StorageBackend,
    job_id: str,
    *,
    expected_identity: PublicationIdentity,
    expected_audio_sha256: str,
    inventory_reference: str,
    scoped_private: bool = False,
    predecessor: str | None = None,
    now: Clock = _now,
) -> str:
    """Reserve one epoch with a durable send intent; no queue send occurs here."""
    at = _utc(now())
    if not isinstance(inventory_reference, str) or not inventory_reference.strip():
        raise RecoveryDenied("owner-visible provider inventory reference is required")
    if not isinstance(expected_audio_sha256, str) or len(expected_audio_sha256) != 64:
        raise RecoveryDenied("exact MP3 digest is required")
    historical_budget = _budget(storage, job_id, at)
    proposed = uuid.uuid4().hex
    captured: dict[str, str] = {}

    def update(raw: bytes | None) -> bytes:
        captured.clear()
        document = _read(raw, job_id)
        identity, proof = _proof(document, storage, job_id)
        _assert_provider_quiet(document, storage, job_id)
        if _budget(storage, job_id, at).to_dict() != historical_budget.to_dict():
            raise RecoveryDenied("historical budget changed before reservation")
        audio = document["generation"]["synthesis_runner"]["audio"]
        if identity != expected_identity or audio["sha256"] != expected_audio_sha256:
            raise RecoveryDenied("requested identity or completed MP3 does not match")
        state = _state(document)
        if state.get("selected_private_output") is not None:
            raise RecoveryDenied("private output already selected")
        if scoped_private and (
            identity != scoped_identity(job_id) or inventory_reference != SCOPED_PRIVATE_DIRECTIVE
        ):
            raise RecoveryDenied("owner-authorized private scope does not match")
        successor_evidence = None
        if predecessor is not None:
            if (
                not scoped_private
                or predecessor != W44_PREDECESSOR
                or expected_audio_sha256 != W44_AUDIO_SHA256
            ):
                raise RecoveryDenied("successor admission is outside the coordinator disposition")
            successor_evidence = _successor_evidence(storage, job_id, state, proof)
        elif state["epochs"]:
            raise RecoveryDenied("an epoch already exists; reconcile it instead")
        # Proof/storage reads may block, and CAS retries must not reuse request time.
        reserved_at = _utc(now())
        if successor_evidence is not None and not (
            timedelta(0)
            <= reserved_at - _parse(successor_evidence["observed_at"])
            <= timedelta(seconds=120)
        ):
            raise RecoveryDenied("trusted prior-worker inventory is stale or future-dated")
        document["generation"].setdefault(
            "video_attempt_terminal",
            {
                "status": "failed",
                "reason": "video_attempt_deadline_reached",
                "at": _iso(reserved_at),
                "deadline_at_utc": historical_budget.to_dict()["deadline_at_utc"],
            },
        )
        previous = state.pop("pending_selection_attempt", None)
        if previous is not None:
            abandoned = state.setdefault("abandoned_selection_attempts", [])
            if not isinstance(abandoned, list):
                raise RecoveryDenied("historical candidate intents are invalid")
            abandoned.append(previous)
        started = _iso(reserved_at)
        deadline = _iso(reserved_at + timedelta(seconds=5100))
        state["epochs"].append(
            {
                "epoch_id": proposed,
                "identity": asdict(identity),
                "proof_sha256": proof,
                "inventory_reference": inventory_reference,
                **(
                    {
                        "authority": (
                            W44_SUCCESSOR_DISPOSITION if predecessor else SCOPED_PRIVATE_DIRECTIVE
                        )
                    }
                    if scoped_private
                    else {}
                ),
                **(
                    {
                        "predecessor_epoch_id": predecessor,
                        "quiescence_evidence": successor_evidence,
                        "predecessor_audit": {
                            key: document["generation"][key]
                            for key in ("video_runner", "video_attempt_terminal", "video_timing")
                            if key in document["generation"]
                        },
                    }
                    if predecessor
                    else {}
                ),
                "started_at_utc": started,
                "deadline_at_utc": deadline,
                "status": "reserved",
                "dispatch": {"message_id": proposed, "status": "pending"},
                "owner": None,
                "lease_expires_at_utc": None,
            }
        )
        document["generation"]["video_recovery"] = state
        captured["epoch_id"] = proposed
        return manifest_bytes(document)

    storage.update_bytes(f"jobs/{job_id}/manifest.json", CONTENT_TYPE, update)
    if not captured:
        raise RecoveryDenied("epoch reservation did not commit")
    return captured["epoch_id"]


def scoped_epoch(storage: StorageBackend, job_id: str, *, now: Clock = _now) -> str:
    """Reserve once or return the existing pending epoch under the same immutable proof."""
    at = _utc(now())
    expected = scoped_identity(job_id)
    raw = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    document = _read(raw, job_id)
    identity, proof = _proof(document, storage, job_id)
    if identity != expected:
        raise RecoveryDenied("original publication identity does not match owner directive")
    _budget(storage, job_id, at)
    _assert_provider_quiet(document, storage, job_id)
    audio_sha = document["generation"]["synthesis_runner"]["audio"]["sha256"]
    state = _state(document)
    successor = (
        job_id == W44_JOB
        and len(state["epochs"]) == 1
        and state["epochs"][0].get("epoch_id") == W44_PREDECESSOR
        and state["epochs"][0].get("status") == "terminal"
    )
    if not state["epochs"] or successor:
        try:
            return reserve_epoch(
                storage,
                job_id,
                expected_identity=expected,
                expected_audio_sha256=audio_sha,
                inventory_reference=SCOPED_PRIVATE_DIRECTIVE,
                scoped_private=True,
                predecessor=W44_PREDECESSOR if successor else None,
                now=now,
            )
        except RecoveryDenied:
            # A concurrent CAS winner may have reserved the same immutable operation.
            document = _read(storage.get_bytes(f"jobs/{job_id}/manifest.json"), job_id)
            state = _state(document)
            if not state["epochs"]:
                raise
    epochs = _scoped_ledger(state, job_id)
    if state.get("selected_private_output") is not None:
        raise RecoveryDenied("original already has an epoch or selected output")
    epoch = epochs[-1]
    if (
        epoch.get("authority")
        != (W44_SUCCESSOR_DISPOSITION if len(epochs) == 2 else SCOPED_PRIVATE_DIRECTIVE)
        or epoch.get("inventory_reference") != SCOPED_PRIVATE_DIRECTIVE
        or epoch.get("identity") != asdict(expected)
        or epoch.get("proof_sha256") != proof
        or epoch.get("status") not in {"reserved", "dispatched", "running"}
        or _utc(now()) >= _parse(epoch["deadline_at_utc"])
    ):
        raise RecoveryDenied("existing epoch cannot be retried under owner directive")
    _same_proof(document, storage, job_id, epoch)
    _assert_provider_quiet(document, storage, job_id)
    return epoch["epoch_id"]


def message(job_id: str, epoch_id: str) -> str:
    body = json.dumps(
        {"schema_version": RECOVERY_MESSAGE_SCHEMA, "job_id": job_id, "epoch_id": epoch_id},
        separators=(",", ":"),
    )
    return base64.b64encode(body.encode()).decode("ascii")


def message_epoch(body: str) -> str | None:
    """Reject malformed recovery markers; ordinary messages remain distinguishable."""
    try:
        candidate = base64.b64decode(body, validate=True).decode()
        payload = json.loads(candidate)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or "epoch_id" not in payload:
        return None
    if (
        payload.get("schema_version") != RECOVERY_MESSAGE_SCHEMA
        or not isinstance(payload["epoch_id"], str)
        or len(payload["epoch_id"]) != 32
    ):
        raise RecoveryDenied("invalid recovery message")
    return payload["epoch_id"]


def reconcile_dispatch(
    storage: StorageBackend,
    job_id: str,
    epoch_id: str,
    send: Callable[[str], None],
    *,
    now: Clock = _now,
) -> None:
    """At-least-once queue send; every duplicate has the same fenced epoch ID."""

    def apply(status: str) -> None:
        def update(raw: bytes | None) -> bytes:
            document = _read(raw, job_id)
            state = _state(document)
            epoch = _epoch(state, epoch_id)
            _same_proof(document, storage, job_id, epoch)
            if epoch["status"] == "selected" and isinstance(
                state.get("selected_private_output"), dict
            ):
                epoch["dispatch"]["status"] = "accepted"
            elif _utc(now()) >= _parse(epoch["deadline_at_utc"]):
                epoch["status"] = "terminal"
                epoch["terminal_reason"] = "deadline_elapsed"
            elif epoch["status"] not in {"reserved", "dispatched", "running"}:
                raise RecoveryDenied("epoch is no longer dispatchable")
            elif status == "accepted":
                epoch["dispatch"]["status"] = "accepted"
                if epoch["status"] == "reserved":
                    epoch["status"] = "dispatched"
            return manifest_bytes(document)

        storage.update_bytes(f"jobs/{job_id}/manifest.json", CONTENT_TYPE, update)

    raw = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    document = _read(raw, job_id)
    epoch = _epoch(_state(document), epoch_id)
    if _utc(now()) >= _parse(epoch["deadline_at_utc"]):
        terminalize_expired_epoch(storage, job_id, epoch_id, now=now)
        if epoch["status"] != "selected":
            raise RecoveryDenied("epoch deadline elapsed before dispatch")
    _same_proof(document, storage, job_id, epoch)
    if epoch["status"] == "selected":
        apply("accepted")
        return
    if _utc(now()) >= _parse(epoch["deadline_at_utc"]):
        apply("terminal")
        raise RecoveryDenied("epoch deadline elapsed before dispatch")
    if epoch["status"] == "running":
        apply("accepted")
        return
    if epoch["status"] not in {"reserved", "dispatched"}:
        raise RecoveryDenied("epoch is no longer dispatchable")
    if epoch["dispatch"]["status"] == "accepted":
        return
    _assert_provider_quiet(document, storage, job_id)
    if _utc(now()) >= _parse(epoch["deadline_at_utc"]):
        terminalize_expired_epoch(storage, job_id, epoch_id, now=now)
        raise RecoveryDenied("epoch deadline elapsed before queue send")
    send(message(job_id, epoch_id))
    apply("accepted")


def terminalize_expired_epoch(
    storage: StorageBackend,
    job_id: str,
    epoch_id: str,
    *,
    now: Clock = _now,
) -> bool:
    """CAS-close only the observed expired epoch, without reading/replacing media."""
    path = f"jobs/{job_id}/manifest.json"
    observed = _read(storage.get_bytes(path), job_id)
    observed_state = _state(observed)
    expected = dict(_epoch(observed_state, epoch_id))
    try:
        projection = BudgetProjection.from_dict(
            {
                "schema_version": 1,
                "started_at_utc": expected["started_at_utc"],
                "deadline_at_utc": expected["deadline_at_utc"],
            }
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise RecoveryDenied("epoch expiry has no valid persisted budget") from exc
    if (
        _utc(now()) < projection.deadline_at_utc
        or expected.get("status") not in {"reserved", "dispatched", "running"}
        or observed_state.get("selected_private_output") is not None
    ):
        return False
    changed = False

    def expire(raw: bytes | None) -> bytes:
        nonlocal changed
        changed = False
        current = _read(raw, job_id)
        state = _state(current)
        epoch = _epoch(state, epoch_id)
        if (
            epoch != expected
            or state.get("selected_private_output") is not None
            or _utc(now()) < projection.deadline_at_utc
        ):
            return raw if raw is not None else b""
        epoch["status"] = "terminal"
        epoch["terminal_reason"] = "deadline_elapsed"
        changed = True
        return manifest_bytes(current)

    storage.update_bytes(path, CONTENT_TYPE, expire)
    return changed


def reconcile_pending(
    storage: StorageBackend,
    send: Callable[[str], None],
    *,
    now: Clock = _now,
    scoped_only: bool = False,
) -> None:
    """Revisit reserved intentions after API restarts; expiry becomes terminal."""
    at = _utc(now())
    for job_id in PRIVATE_ORIGINALS:
        try:
            raw = storage.get_bytes(f"jobs/{job_id}/manifest.json")
            if raw is None:
                continue
            epochs = _state(_read(raw, job_id))["epochs"]
        except Exception:
            logger.warning("private video dispatch original unreadable job_id=%s", job_id)
            continue
        for index, epoch in enumerate(epochs):
            epoch_id = epoch.get("epoch_id")
            try:
                if scoped_only and (
                    _scoped_ledger(_state(_read(raw, job_id)), job_id)[-1] != epoch
                    or epoch.get("authority")
                    != (W44_SUCCESSOR_DISPOSITION if index == 1 else SCOPED_PRIVATE_DIRECTIVE)
                    or epoch.get("inventory_reference") != SCOPED_PRIVATE_DIRECTIVE
                    or epoch.get("identity") != asdict(scoped_identity(job_id))
                ):
                    continue
                status = epoch.get("status")
                dispatch = epoch.get("dispatch")
                pending = isinstance(dispatch, dict) and dispatch.get("status") == "pending"
                expired = status != "selected" and at >= _parse(epoch["deadline_at_utc"])
                if status in {"reserved", "dispatched", "running", "selected"} and (
                    pending or expired
                ):
                    if expired:
                        terminalize_expired_epoch(storage, job_id, epoch["epoch_id"], now=now)
                    else:
                        reconcile_dispatch(storage, job_id, epoch["epoch_id"], send, now=now)
            except RecoveryDenied:
                logger.warning(
                    "private video dispatch denied job_id=%s epoch_id=%r", job_id, epoch_id
                )
            except Exception:
                logger.warning(
                    "private video dispatch retry failed job_id=%s epoch_id=%r", job_id, epoch_id
                )


def claim_epoch(
    storage: StorageBackend,
    job_id: str,
    epoch_id: str,
    owner: str,
    *,
    now: Clock = _now,
    visibility_expires_at: datetime | None = None,
) -> RecoveryClaim:
    """CAS reclaim the same epoch with an unchanged original deadline."""
    at = _utc(now())
    if terminalize_expired_epoch(storage, job_id, epoch_id, now=now):
        raise RecoveryDenied("epoch deadline elapsed")
    visibility = _utc(visibility_expires_at) if visibility_expires_at else None
    if visibility is None or visibility <= at + timedelta(seconds=5):
        raise RecoveryDenied("authoritative queue visibility is unavailable")
    captured: dict[str, str] = {}

    def update(raw: bytes | None) -> bytes:
        captured.clear()
        document = _read(raw, job_id)
        state = _state(document)
        epoch = _epoch(state, epoch_id)
        _same_proof(document, storage, job_id, epoch)
        _assert_provider_quiet(document, storage, job_id)
        if epoch["status"] == "selected" and isinstance(state.get("selected_private_output"), dict):
            raise RecoveryDenied("selected output must be reused without reclaim")
        deadline = _parse(epoch["deadline_at_utc"])
        current_at = _utc(now())
        if current_at >= deadline:
            epoch["status"] = "terminal"
            epoch["terminal_reason"] = "deadline_elapsed"
            return manifest_bytes(document)
        lease = epoch.get("lease_expires_at_utc")
        if visibility <= current_at + timedelta(seconds=5):
            raise RecoveryDenied("authoritative queue visibility expired during claim")
        if (
            epoch["status"] not in {"reserved", "dispatched", "running"}
            or (lease and _parse(lease) > current_at and epoch.get("owner") != owner)
            or state.get("selected_private_output") is not None
        ):
            raise RecoveryDenied("epoch is already owned, selected, or terminal")
        epoch["status"] = "running"
        epoch["owner"] = owner
        epoch["lease_expires_at_utc"] = _iso(
            min(deadline, visibility, current_at + timedelta(seconds=LEASE_SECONDS))
        )
        captured.update(epoch)
        return manifest_bytes(document)

    storage.update_bytes(f"jobs/{job_id}/manifest.json", CONTENT_TYPE, update)
    if not captured:
        raise RecoveryDenied("epoch deadline elapsed")
    return RecoveryClaim(
        job_id,
        epoch_id,
        owner,
        captured["deadline_at_utc"],
        captured["started_at_utc"],
        captured["proof_sha256"],
    )


def assert_current(
    storage: StorageBackend, claim: RecoveryClaim, *, now: Clock = _now
) -> dict[str, Any]:
    document = _read(storage.get_bytes(f"jobs/{claim.job_id}/manifest.json"), claim.job_id)
    epoch = _epoch(_state(document), claim.epoch_id)
    _same_proof(document, storage, claim.job_id, epoch)
    at = _utc(now())
    if (
        epoch.get("status") != "running"
        or epoch.get("owner") != claim.owner
        or epoch.get("proof_sha256") != claim.proof_sha256
        or epoch.get("deadline_at_utc") != claim.deadline_at_utc
        or at >= _parse(epoch["deadline_at_utc"])
        or at >= _parse(epoch["lease_expires_at_utc"])
    ):
        raise RecoveryDenied("recovery claim is stale")
    return document


def guard_manifest(
    document: dict[str, Any],
    *,
    claim: RecoveryClaim | None,
    attempt_id: str | None = None,
    now: Clock = _now,
) -> None:
    """Recheck the CAS callback's actual manifest, not an earlier readback."""
    state = _state(document)
    if claim is None:
        if state["epochs"]:
            raise RecoveryDenied("ordinary manifest writer fenced by recovery")
        selected = state.get("selected_private_output")
        if selected is not None and selected.get("attempt_id") != attempt_id:
            raise RecoveryDenied("ordinary manifest writer is not selected")
        return
    epoch = _epoch(state, claim.epoch_id)
    at = _utc(now())
    if (
        document.get("job_id") != claim.job_id
        or epoch.get("owner") != claim.owner
        or epoch.get("proof_sha256") != claim.proof_sha256
        or epoch.get("deadline_at_utc") != claim.deadline_at_utc
        or epoch.get("status") not in {"running", "selected"}
        or at >= _parse(epoch["lease_expires_at_utc"])
        or at >= _parse(epoch["deadline_at_utc"])
    ):
        raise RecoveryDenied("recovery manifest writer is stale")
    selected = state.get("selected_private_output")
    if selected is not None and (
        selected.get("epoch_id") != claim.epoch_id
        or (attempt_id is not None and selected.get("attempt_id") != attempt_id)
    ):
        raise RecoveryDenied("recovery manifest writer is not selected")


def selection_path(job_id: str, attempt_id: str, digest: str, kind: str) -> str:
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}", job_id):
        raise RecoveryDenied("candidate job path is unsafe")
    if kind not in {"mp4", "held.json"}:
        raise RecoveryDenied("candidate kind is unsafe")
    if not all(
        isinstance(item, str) and item and all(c.isascii() and c.isalnum() for c in item)
        for item in (attempt_id, digest)
    ):
        raise RecoveryDenied("candidate path is unsafe")
    return f"jobs/{job_id}/video/private-candidates/{attempt_id}/{digest}.{kind}"


def _file_sha256(path: Path) -> tuple[str, int]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            sha.update(block)
    return sha.hexdigest(), size


def _verify_candidate_archive(storage: StorageBackend, path: str, digest: str, size: int) -> None:
    if storage.blob_size(path) != size:
        raise RecoveryDenied("private candidate size mismatch")
    readback_dir = Path(".scratch") / "video-recovery"
    readback_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = readback_dir / f"{uuid.uuid4().hex}.mp4"
    try:
        if not storage.download_file(path, target) or _file_sha256(target) != (digest, size):
            raise RecoveryDenied("private candidate archive digest mismatch")
    finally:
        target.unlink(missing_ok=True)


def select_private_output(
    storage: StorageBackend,
    job_id: str,
    source: Path,
    *,
    attempt_id: str,
    claim: RecoveryClaim | None,
    expected_proof_sha256: str,
    now: Clock = _now,
) -> dict[str, Any]:
    """Persist inert immutable candidates, then select once by manifest CAS."""
    digest, size = _file_sha256(source)
    if size < 1024:
        raise RecoveryDenied("completed MP4 is invalid")
    archive = selection_path(job_id, attempt_id, digest, "mp4")
    outbox = selection_path(job_id, attempt_id, digest, "held.json")
    existing = storage.blob_size(archive)
    initial = _read(storage.get_bytes(f"jobs/{job_id}/manifest.json"), job_id)
    identity, proof = _proof(initial, storage, job_id)
    _assert_provider_quiet(initial, storage, job_id)
    if proof != expected_proof_sha256:
        raise RecoveryDenied("candidate original proof changed")
    pending_intent = {
        "attempt_id": attempt_id,
        "mp4_sha256": digest,
        "size_bytes": size,
        "epoch_id": claim.epoch_id if claim else None,
    }

    def stage(raw: bytes | None) -> bytes:
        current = _read(raw, job_id)
        current_identity, current_proof = _proof(current, storage, job_id)
        _assert_provider_quiet(current, storage, job_id)
        if current_identity != identity or current_proof != proof:
            raise RecoveryDenied("private candidate proof changed before staging")
        state = _state(current)
        if claim is not None:
            epoch = _epoch(state, claim.epoch_id)
            at = _utc(now())
            if (
                epoch.get("owner") != claim.owner
                or epoch.get("status") != "running"
                or at >= _parse(epoch["lease_expires_at_utc"])
                or at >= _parse(epoch["deadline_at_utc"])
            ):
                return raw if raw is not None else b""
        elif state["epochs"]:
            return raw if raw is not None else b""
        selected = state.get("selected_private_output")
        if selected is not None:
            return raw if raw is not None else b""
        pending = state.get("pending_selection_attempt")
        if pending is None:
            state["pending_selection_attempt"] = pending_intent
            current["generation"]["video_recovery"] = state
            return manifest_bytes(current)
        return raw if raw is not None else b""

    storage.update_bytes(f"jobs/{job_id}/manifest.json", CONTENT_TYPE, stage)
    if existing is None:
        storage.upload_file(archive, source, "video/mp4")
    elif existing != size:
        raise RecoveryDenied("candidate archive conflicts")
    _verify_candidate_archive(storage, archive, digest, size)
    doc = _read(storage.get_bytes(f"jobs/{job_id}/manifest.json"), job_id)
    verified_identity, verified_proof = _proof(doc, storage, job_id)
    _assert_provider_quiet(doc, storage, job_id)
    if verified_identity != identity or verified_proof != proof:
        raise RecoveryDenied("candidate original proof changed")
    candidate = {
        "job_id": job_id,
        "identity": asdict(identity),
        "epoch_id": claim.epoch_id if claim else None,
        "attempt_id": attempt_id,
        "proof_sha256": proof,
        "mp4_sha256": digest,
        "size_bytes": size,
        "archive_path": archive,
        "outbox_path": outbox,
        "status": "held_private",
        "provider_intent": {
            "youtube": "blocked",
            "spotify": "blocked",
            "spotify_rss": "blocked",
        },
    }
    encoded = manifest_bytes(candidate)

    def immutable(raw: bytes | None) -> bytes:
        if raw is not None and raw != encoded:
            raise RecoveryDenied("candidate outbox conflicts")
        return raw if raw is not None else encoded

    storage.update_bytes(outbox, CONTENT_TYPE, immutable)
    selected: dict[str, Any] = {}

    def choose(raw: bytes | None) -> bytes:
        selected.clear()
        current = _read(raw, job_id)
        current_identity, current_proof = _proof(current, storage, job_id)
        _assert_provider_quiet(current, storage, job_id)
        state = _state(current)
        if current_identity != identity or current_proof != proof:
            raise RecoveryDenied("candidate identity or proof changed")
        if claim:
            epoch = _epoch(state, claim.epoch_id)
            at = _utc(now())
            if (
                epoch.get("owner") != claim.owner
                or epoch.get("status") != "running"
                or at >= _parse(epoch["lease_expires_at_utc"])
                or at >= _parse(epoch["deadline_at_utc"])
            ):
                raise RecoveryDenied("recovery selector is stale")
        elif state["epochs"]:
            raise RecoveryDenied("ordinary selector fenced by recovery")
        existing_selection = state.get("selected_private_output")
        if existing_selection is not None and existing_selection != candidate:
            raise RecoveryDenied("another private candidate is selected")
        if existing_selection is None and state.get("pending_selection_attempt") != pending_intent:
            raise RecoveryDenied("candidate selection intent is stale or orphaned")
        state["selected_private_output"] = candidate
        state.pop("pending_selection_attempt", None)
        state["provider_intent"] = {
            "identity": asdict(identity),
            "youtube": "blocked_unreconciled",
            "spotify": "blocked_unreconciled",
            "spotify_rss": "blocked_unreconciled",
        }
        if claim:
            epoch["status"] = "selected"
        current["generation"]["video_recovery"] = state
        current["generation"]["video_runner"] = {
            "status": "completed",
            "at": _iso(_utc(now())),
            "distribution": {
                "status": "completed",
                "public_delivery_status": "awaiting_review",
                "blob_path": archive,
            },
        }
        selected.update(candidate)
        return manifest_bytes(current)

    storage.update_bytes(f"jobs/{job_id}/manifest.json", CONTENT_TYPE, choose)
    if not selected:
        raise RecoveryDenied("candidate was not selected")
    try:
        committed = read_selected(storage, job_id)
        if committed != candidate:
            raise RecoveryDenied("selected private candidate changed after commit")
    except Exception as exc:
        raise PrivateSelectionUnverified("selected private candidate readback failed") from exc
    return committed


def read_selected(storage: StorageBackend, job_id: str) -> dict[str, Any]:
    document = _read(storage.get_bytes(f"jobs/{job_id}/manifest.json"), job_id)
    state = _state(document)
    candidate = state.get("selected_private_output")
    if not isinstance(candidate, dict) or candidate.get("job_id") != job_id:
        raise RecoveryDenied("selected private output is unavailable")
    if (
        set(candidate)
        != {
            "job_id",
            "identity",
            "epoch_id",
            "attempt_id",
            "proof_sha256",
            "mp4_sha256",
            "size_bytes",
            "archive_path",
            "outbox_path",
            "status",
            "provider_intent",
        }
        or not isinstance(candidate.get("attempt_id"), str)
        or not isinstance(candidate.get("mp4_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", candidate["mp4_sha256"])
        or type(candidate.get("size_bytes")) is not int
        or candidate["size_bytes"] < 1024
        or candidate.get("status") != "held_private"
        or candidate.get("provider_intent")
        != {
            "youtube": "blocked",
            "spotify": "blocked",
            "spotify_rss": "blocked",
        }
        or (
            candidate.get("epoch_id") is not None
            and (
                not isinstance(candidate["epoch_id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", candidate["epoch_id"])
            )
        )
    ):
        raise RecoveryDenied("selected private output is malformed")
    identity, proof = _proof(document, storage, job_id)
    if candidate.get("identity") != asdict(identity) or candidate.get("proof_sha256") != proof:
        raise RecoveryDenied("selected private output proof changed")
    selected_epochs = [
        item
        for item in state["epochs"]
        if isinstance(item, dict) and item.get("status") == "selected"
    ]
    if candidate.get("epoch_id") is None:
        if state["epochs"] or selected_epochs:
            raise RecoveryDenied("ordinary selection conflicts with recovery history")
    elif (
        len(selected_epochs) != 1
        or selected_epochs[0].get("epoch_id") != candidate["epoch_id"]
        or selected_epochs[0].get("proof_sha256") != proof
    ):
        raise RecoveryDenied("selected recovery epoch is missing or ambiguous")
    if state.get("provider_intent") != {
        "identity": asdict(identity),
        "youtube": "blocked_unreconciled",
        "spotify": "blocked_unreconciled",
        "spotify_rss": "blocked_unreconciled",
    }:
        raise RecoveryDenied("identity-wide provider hold is missing or changed")
    expected_archive = selection_path(
        job_id, candidate["attempt_id"], candidate["mp4_sha256"], "mp4"
    )
    expected_outbox = selection_path(
        job_id, candidate["attempt_id"], candidate["mp4_sha256"], "held.json"
    )
    if (
        candidate.get("archive_path") != expected_archive
        or candidate.get("outbox_path") != expected_outbox
    ):
        raise RecoveryDenied("selected private candidate path is ambiguous")
    if storage.get_bytes(expected_outbox) != manifest_bytes(candidate):
        raise RecoveryDenied("selected private outbox is missing or changed")
    if storage.blob_size(expected_archive) != candidate["size_bytes"]:
        raise RecoveryDenied("selected private archive is missing or changed")
    _verify_candidate_archive(
        storage, expected_archive, candidate["mp4_sha256"], candidate["size_bytes"]
    )
    return candidate
