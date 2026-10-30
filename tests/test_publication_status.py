from __future__ import annotations

import copy
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection, HTTPResponse
from types import MappingProxyType
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import pytest

from podcaster.api import GenerateHandler
from podcaster.distribution_outbox import (
    ArtifactReference,
    DistributionOutboxError,
    DistributionOutboxRepository,
    aggregate_exit_code,
    exact_recovery_authorization_evidence,
    exact_verification_proof,
    outbox_path,
    weekly_state_from_attempts,
)
from podcaster.publication_state import (
    append_evidence,
    evidence_path,
    publication_identity,
)
from podcaster.publication_status import (
    PublicationStatusError,
    publication_status,
    read_youtube_status,
)
from podcaster.storage import AzureBlobStorageBackend, StorageUnavailableError
from podcaster.video.youtube_reconcile import youtube_identity_tag
from tests.test_api import FakeHandler

NOW = datetime(2026, 10, 12, 20, 25, tzinfo=timezone.utc)
JOB = "podcast-2026-W42-84c67c525182"


class ReadOnlyStorage:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def get_bytes(self, path):
        return self.data.get(path)

    def update_bytes(self, path, content_type, update):
        self.data[path] = update(self.data.get(path))

    def blob_size(self, path):
        return len(self.data[path])

    def download_file(self, path, target):
        target.write_bytes(self.data[path])
        return True


@pytest.fixture
def accepted():
    storage = ReadOnlyStorage()
    manifest = {
        "job_id": JOB,
        "request": {
            "week": "2026-W42",
            "publish_run_id": "38270494467",
            "article_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "publication_identity_mode": "canonical",
            "language": "en",
        },
        "lifecycle": {"transitions": [{"to": "accepted", "at": NOW.isoformat()}]},
        "generation": {"synthesis_queue": {"status": "enqueued"}},
        "observability": {"correlation_id": JOB},
    }
    storage.data[f"jobs/{JOB}/manifest.json"] = json.dumps(manifest).encode()
    query = {
        key: manifest["request"][key]
        for key in ("week", "publish_run_id", "article_sha256", "manifest_sha256")
    }
    query.update(job_id=JOB, correlation_id=JOB)
    return storage, manifest, query


def receipts(storage, manifest, *, at=NOW):
    identity = publication_identity(manifest, JOB, "38270494467")
    for provider, provider_id, outcome, native, verification in (
        ("youtube", "_ZD9E_zTjtM", "draft_created", "unlisted", "none"),
        ("spotify", "127359743", "published", "published", "provider_readback"),
    ):
        append_evidence(
            storage,
            identity,
            platform=provider,
            media_kind="video",
            operation="distribution",
            outcome=outcome,
            provider_artifact_id=provider_id,
            native_state=native,
            verification=verification,
            evidence_source=f"{provider}_episode_readback",
            at=at,
        )
    return identity


def ready(identity, video_id):
    assert video_id == "_ZD9E_zTjtM"
    return {
        "uploadStatus": "processed",
        "processingStatus": "succeeded",
        "privacyStatus": "unlisted",
        "tags": [youtube_identity_tag(identity)],
    }


def save(storage, manifest):
    storage.data[f"jobs/{JOB}/manifest.json"] = json.dumps(manifest).encode()


@pytest.fixture
def canonical(accepted):
    storage, manifest, query = accepted
    identity = publication_identity(manifest, JOB, query["publish_run_id"])
    content = b"reviewed-video"
    artifact = ArtifactReference(
        "immutable/video.mp4", hashlib.sha256(content).hexdigest(), len(content), "video"
    )
    storage.data[artifact.path] = content
    repository = DistributionOutboxRepository(storage, now=lambda: NOW)
    document, _ = repository.enqueue(
        identity,
        artifact,
        provider_objectives={"youtube": "public", "spotify": "public"},
        provider_context={"youtube": {"locale": "en"}},
        enqueue_source="video_runner",
        enqueue_version="v1",
    )
    manifest["review"] = {"snapshot": {}}
    manifest["generation"].update(
        synthesis_runner={"status": "completed"},
        video_runner={"status": "prepared", "distribution": {"outbox_id": document["outbox_id"]}},
    )
    save(storage, manifest)
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="one", lease_seconds=300
    )
    for provider, provider_id, operation in (
        ("youtube", "_ZD9E_zTjtM", "upload"),
        ("spotify", "127359743", "create"),
    ):
        for current_operation in (operation, "public_promotion"):
            repository.persist_intent(claim, provider=provider, operation=current_operation)
            repository.consume_intent(
                claim, provider=provider, provider_timeout_seconds=30, receipt_margin_seconds=10
            )
            repository.record_receipt(
                claim,
                provider=provider,
                transport_class="accepted",
                provider_item_id=provider_id,
                native_state="public",
            )
        repository.record_verification(
            claim,
            provider=provider,
            result="externally_verified_public",
            source="provider_readback",
            provider_item_id=provider_id,
            native_state="public",
            proof=exact_verification_proof(document, provider_item_id=provider_id),
        )
    document = repository.release(claim)
    assert aggregate_exit_code([document]) == 0
    return storage, manifest, query, document


def test_current_outbox_completed_public_is_visible_without_legacy_or_mutation(canonical):
    storage, _, query, document = canonical
    before = copy.deepcopy(storage.data)
    response = publication_status(
        storage,
        query,
        now=NOW,
        youtube_readback=MagicMock(side_effect=AssertionError("use durable terminal readback")),
    )
    assert response["provider"]["state"] == "published"
    assert response["provider"]["external_verified"] is True
    assert response["provider"]["semantics"] == "canonical_public_outbox"
    assert response["provider"]["weekly"]["state"] == "published_verified"
    assert response["provider"]["attempts"][0]["terminal_outcome"] == "published_verified"
    assert response["video"]["state"] == "succeeded"
    assert evidence_path(JOB) not in storage.data
    assert storage.data == before
    assert document["aggregate"]["result"] == "completed_public"


@pytest.fixture
def recovered(accepted):
    storage, manifest, query = accepted
    repository = DistributionOutboxRepository(storage, now=lambda: NOW)
    identity = publication_identity(manifest, JOB, query["publish_run_id"])
    content = b"reviewed-recovery-video"
    artifact = ArtifactReference(
        "immutable/recovery.mp4", hashlib.sha256(content).hexdigest(), len(content), "video"
    )
    storage.data[artifact.path] = content
    document, _ = repository.enqueue(
        identity,
        artifact,
        provider_objectives={"youtube": "public", "spotify": "public"},
        provider_context={"youtube": {"locale": "en"}},
        enqueue_source="video_runner",
        enqueue_version="v1",
    )
    manifest["review"] = {"snapshot": {}}
    manifest["generation"]["video_runner"] = {
        "status": "prepared",
        "distribution": {"outbox_id": document["outbox_id"]},
    }
    save(storage, manifest)
    first = repository.claim(
        document["outbox_id"], owner="first", execution_id="failed", lease_seconds=300
    )
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            first,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_readback",
            provider_item_id=f"{provider}-failed",
            native_state="failed",
        )
    failed = repository.release(first)
    failed_attempt = copy.deepcopy(failed["attempts"][0])
    repository.authorize_recovery(
        document["outbox_id"],
        predecessor_attempt_id=first.attempt_id,
        source="operator",
        reason="no_mutation_proven",
        evidence=exact_recovery_authorization_evidence(
            failed,
            predecessor_attempt_id=first.attempt_id,
            expected_provider_item_ids={
                provider: f"{provider}-recovered" for provider in ("youtube", "spotify")
            },
        ),
    )
    claim = repository.claim(
        document["outbox_id"], owner="recovery", execution_id="readback", lease_seconds=300
    )
    for provider in ("youtube", "spotify"):
        repository.persist_intent(
            claim,
            provider=provider,
            operation="recovery_readback",
            expected_provider_item_id=f"{provider}-recovered",
        )
        repository.record_verification(
            claim,
            provider=provider,
            result="externally_verified_public",
            source=f"{provider}_readback",
            provider_item_id=f"{provider}-recovered",
            native_state="public" if provider == "youtube" else "published",
            proof=exact_verification_proof(
                repository.read(document["outbox_id"]),
                provider_item_id=f"{provider}-recovered",
            ),
        )
    recovered = repository.release(claim)
    assert aggregate_exit_code([recovered]) == 0
    assert recovered["attempts"][0] == failed_attempt
    assert all(leg["receipts"] == [] for leg in recovered["providers"].values())
    return storage, query, recovered


def test_authorized_read_only_recovery_preserves_failed_attempt_and_weekly_truth(recovered):
    storage, query, document = recovered
    before = copy.deepcopy(storage.data)
    result = publication_status(
        storage, query, now=NOW, youtube_readback=MagicMock(side_effect=AssertionError)
    )["provider"]
    assert result["state"] == "published"
    assert result["external_verified"] is True
    assert result["weekly"]["state"] == "published_verified_recovered"
    assert [attempt["terminal_outcome"] for attempt in result["attempts"]] == [
        "failed_terminal",
        "published_verified",
    ]
    assert all(leg["state"] == "published" for leg in result["providers"].values())
    assert storage.data == before

    # Neither a missing separate authorization nor a mismatched expected ID can
    # turn recovery readback into mutation-free success.
    for fault in ("authorization", "expected_id", "proof"):
        invalid = copy.deepcopy(document)
        if fault == "authorization":
            invalid["recovery_authz"] = []
        elif fault == "expected_id":
            invalid["providers"]["youtube"]["intent"]["expected_provider_item_id"] = "different"
        else:
            invalid["providers"]["youtube"]["verification"]["proof"]["evidence"][
                "article_sha256"
            ] = "c" * 64
        storage.data[outbox_path(document["outbox_id"])] = json.dumps(invalid).encode()
        try:
            result = publication_status(storage, query, now=NOW)["provider"]
        except PublicationStatusError as exc:
            assert exc.status == 409
        else:
            assert result["state"] != "published"
            assert result["external_verified"] is False


@pytest.fixture(params=["canonical", "recovered"])
def terminal_publication(request):
    fixture = request.getfixturevalue(request.param)
    storage, query, document = fixture[0], fixture[-2], fixture[-1]
    return storage, query, DistributionOutboxRepository(storage).read(document["outbox_id"])


def _assert_terminal_evidence_rejected(terminal_publication, monkeypatch):
    storage, query, document = terminal_publication
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    monkeypatch.setattr(storage, "update_bytes", MagicMock(side_effect=AssertionError))
    assert aggregate_exit_code([document]) == 1
    assert weekly_state_from_attempts(document["attempts"], weekly_record=document) not in {
        "published_verified",
        "published_verified_recovered",
    }
    result = publication_status(
        storage, query, now=NOW, youtube_readback=MagicMock(side_effect=AssertionError)
    )["provider"]
    assert result["state"] != "published"
    assert result["external_verified"] is False
    assert all(leg["state"] != "published" for leg in result["providers"].values())
    assert storage.data == before


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize("fence", ["absent", None, 0, "1", True, 999999])
def test_terminal_verification_requires_bound_typed_fence(
    terminal_publication, monkeypatch, provider, fence
):
    _, _, document = terminal_publication
    verification = document["providers"][provider]["verification"]
    if fence == "absent":
        verification.pop("fencing_token")
    else:
        verification["fencing_token"] = fence
    _assert_terminal_evidence_rejected(terminal_publication, monkeypatch)


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize("receipts", ["absent", None, {}, "", 1, True, [None], ["receipt"]])
def test_terminal_proof_fails_closed_for_malformed_receipt_collection(
    terminal_publication, monkeypatch, provider, receipts
):
    _, _, document = terminal_publication
    if receipts == "absent":
        document["providers"][provider].pop("receipts")
    else:
        document["providers"][provider]["receipts"] = receipts
    _assert_terminal_evidence_rejected(terminal_publication, monkeypatch)


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
def test_terminal_mutation_requires_nonempty_receipts(canonical, monkeypatch, provider):
    storage, _, query, document = canonical
    document["providers"][provider]["receipts"] = []
    _assert_terminal_evidence_rejected((storage, query, document), monkeypatch)


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize(
    "fault",
    [
        "snapshot",
        "claim_fence",
        "document_fence",
        "owner",
        "claim_id",
        "execution_id",
        "event_time",
    ],
)
def test_terminal_proof_requires_immutable_attempt_and_typed_claim_identity(
    terminal_publication, monkeypatch, provider, fault
):
    _, _, document = terminal_publication
    attempt = document["attempts"][-1]
    if fault == "snapshot":
        attempt["provider_evidence"][provider]["verification"]["source"] = "other_readback"
    else:
        claim_event = next(
            event for event in attempt["events"] if event["state"] in {"claimed", "reconciling"}
        )
        if fault == "claim_fence":
            # Even symmetrical top-level/snapshot corruption cannot invent a claim.
            document["fencing_token"] = 999999
            document["providers"][provider]["verification"]["fencing_token"] = 999999
            attempt["provider_evidence"][provider]["verification"]["fencing_token"] = 999999
        elif fault == "event_time":
            claim_event["at"] = None
        elif fault == "document_fence":
            document["fencing_token"] += 1
        else:
            claim_event[fault] = True
    _assert_terminal_evidence_rejected(terminal_publication, monkeypatch)


def test_later_readback_uses_its_claim_not_mutation_fence_and_preserves_history(
    terminal_publication, monkeypatch
):
    storage, query, document = terminal_publication
    original = copy.deepcopy(document["attempts"][-1]["provider_evidence"])
    repository = DistributionOutboxRepository(storage, now=lambda: NOW + timedelta(minutes=10))
    claim = repository.claim(
        document["outbox_id"], owner="reconciler", execution_id="later", lease_seconds=300
    )
    assert claim.read_only is True
    assert claim.fencing_token > document["fencing_token"]
    for provider in ("youtube", "spotify"):
        leg = document["providers"][provider]
        repository.record_verification(
            claim,
            provider=provider,
            result="externally_verified_public",
            source="provider_readback",
            provider_item_id=leg["verification"]["provider_item_id"],
            native_state="public",
            proof=exact_verification_proof(
                document, provider_item_id=leg["verification"]["provider_item_id"]
            ),
        )
        in_flight = repository.read(document["outbox_id"])
        assert aggregate_exit_code([in_flight]) == 0
    released = repository.release(claim)
    assert released["attempts"][-1]["provider_evidence"] == original
    assert released["attempts"][:-1] == document["attempts"][:-1]
    assert all(
        receipt["fencing_token"] < leg["verification"]["fencing_token"]
        for leg in released["providers"].values()
        for receipt in leg["receipts"]
    )
    before = copy.deepcopy(storage.data)
    monkeypatch.setattr(storage, "update_bytes", MagicMock(side_effect=AssertionError))
    result = publication_status(
        storage, query, now=NOW, youtube_readback=MagicMock(side_effect=AssertionError)
    )["provider"]
    assert aggregate_exit_code([released]) == 0
    assert result["state"] == "published"
    assert result["external_verified"] is True
    assert storage.data == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", "other-owner"),
        ("claim_id", "other-claim"),
        ("execution_id", "other-execution"),
        ("attempt_id", "other-attempt"),
        ("fencing_token", True),
        ("fencing_token", "2"),
    ],
)
def test_current_claim_identity_must_match_durable_winning_attempt(
    canonical, monkeypatch, field, value
):
    storage, _, query, document = canonical
    repository = DistributionOutboxRepository(storage, now=lambda: NOW)
    repository.claim(
        document["outbox_id"], owner="reconciler", execution_id="later", lease_seconds=300
    )
    document = repository.read(document["outbox_id"])
    document["claim"][field] = value
    _assert_terminal_evidence_rejected((storage, query, document), monkeypatch)


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize(
    "value", ["absent", None, "", "failed_terminal", "pending_provider", 7, True, {}]
)
def test_recovery_requires_matching_public_verification_result(recovered, provider, value):
    storage, query, document = recovered
    verification = document["providers"][provider]["verification"]
    if value == "absent":
        verification.pop("result")
    else:
        verification["result"] = value
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    result = publication_status(storage, query, now=NOW)["provider"]
    assert result["state"] != "published"
    assert result["external_verified"] is False
    assert all(item["state"] != "published" for item in result["providers"].values())
    assert storage.data == before


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize("target", ["intent", "receipt", "both"])
@pytest.mark.parametrize("field", ["operation", "operation_type"])
@pytest.mark.parametrize("value", ["absent", None, "", "unsupported", 7, True, {}])
def test_mutation_requires_valid_bound_operation(canonical, provider, target, field, value):
    storage, _, query, document = canonical
    leg = document["providers"][provider]
    records = []
    if target in {"intent", "both"}:
        records.extend([*leg["intent_history"], leg["intent"]])
    if target in {"receipt", "both"}:
        records.extend(leg["receipts"])
    for record in records:
        if value == "absent":
            record.pop(field)
        else:
            record[field] = value
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    result = publication_status(storage, query, now=NOW)["provider"]
    assert result["state"] != "published"
    assert result["external_verified"] is False
    assert all(item["state"] != "published" for item in result["providers"].values())
    assert result["providers"][provider]["code"] == "receipt_ambiguous"
    assert storage.data == before


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize("fault", ["wrong_type", "wrong_provider_operation"])
def test_mutation_rejects_symmetric_valid_but_mismatched_operation(canonical, provider, fault):
    storage, _, query, document = canonical
    leg = document["providers"][provider]
    for record in [*leg["intent_history"], leg["intent"], *leg["receipts"]]:
        record.update(
            operation="upload" if provider == "spotify" else "create",
            operation_type="create",
        )
        if fault == "wrong_type":
            record.update(operation="public_promotion", operation_type="create")
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    result = publication_status(storage, query, now=NOW)["provider"]
    assert result["external_verified"] is False
    assert result["providers"][provider]["code"] == "receipt_ambiguous"


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize("field", ["intent_id", "receipt_id"])
@pytest.mark.parametrize("value", [7, True, {}, []])
def test_mutation_requires_token_shaped_evidence_ids(canonical, provider, field, value):
    storage, _, query, document = canonical
    leg = document["providers"][provider]
    for record in [*leg["intent_history"], leg["intent"], *leg["receipts"]]:
        if field in record:
            record[field] = value
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    result = publication_status(storage, query, now=NOW)["provider"]
    assert result["state"] != "published"
    assert result["external_verified"] is False
    assert result["providers"][provider]["code"] == "receipt_ambiguous"
    assert storage.data == before


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize("value", ["absent", None, "", 7, True, {}, []])
def test_recovery_requires_token_shaped_intent_id(recovered, provider, value):
    storage, query, document = recovered
    intent = document["providers"][provider]["intent"]
    if value == "absent":
        intent.pop("intent_id")
    else:
        intent["intent_id"] = value
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    result = publication_status(storage, query, now=NOW)["provider"]
    assert result["state"] != "published"
    assert result["external_verified"] is False
    assert storage.data == before


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize("target", ["intent", "receipt", "both"])
@pytest.mark.parametrize(
    "intent_field,receipt_field,value",
    [
        ("consumed_owner", "consumed_owner", "absent"),
        ("consumed_owner", "consumed_owner", None),
        ("consumed_owner", "consumed_owner", ""),
        ("consumed_owner", "consumed_owner", 7),
        ("consumed_fence", "fencing_token", "absent"),
        ("consumed_fence", "fencing_token", None),
        ("consumed_fence", "fencing_token", "1"),
        ("consumed_fence", "fencing_token", True),
        ("consumed_fence", "fencing_token", 0),
        ("consumed_fence", "fencing_token", -1),
    ],
)
def test_mutation_requires_typed_consumed_owner_and_fence(
    canonical, provider, target, intent_field, receipt_field, value
):
    storage, _, query, document = canonical
    leg = document["providers"][provider]
    fields = []
    if target in {"intent", "both"}:
        fields.extend((intent, intent_field) for intent in [*leg["intent_history"], leg["intent"]])
    if target in {"receipt", "both"}:
        fields.extend((receipt, receipt_field) for receipt in leg["receipts"])
    for record, field in fields:
        if value == "absent":
            record.pop(field, None)
        else:
            record[field] = value
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    result = publication_status(storage, query, now=NOW)["provider"]
    assert result["state"] != "published"
    assert result["external_verified"] is False
    assert all(item["state"] != "published" for item in result["providers"].values())
    assert result["providers"][provider]["code"] == "receipt_ambiguous"
    assert storage.data == before


@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize(
    "fault",
    ["owner", "fence", "operation", "created_fence", "receipt_id", "consume_event", "snapshot"],
)
def test_symmetric_mutation_pair_is_bound_to_durable_origin(
    canonical, monkeypatch, provider, fault
):
    storage, _, query, original = canonical
    document = DistributionOutboxRepository(storage).read(original["outbox_id"])
    leg = document["providers"][provider]
    intent = leg["intent_history"][0]
    receipt = leg["receipts"][0]
    attempt = document["attempts"][-1]
    if fault in {"owner", "fence"}:
        intent_field, receipt_field, value = (
            ("consumed_owner", "consumed_owner", "never-claimed")
            if fault == "owner"
            else ("consumed_fence", "fencing_token", 999999)
        )
        intent[intent_field] = receipt[receipt_field] = value
        # Agreement with retained mutation evidence still cannot invent a claim.
        attempt["provider_evidence"][provider]["receipts"][0][receipt_field] = value
        for event in attempt["events"]:
            if event.get("intent_id") == intent["intent_id"] and "intent" in event:
                event["intent"][intent_field] = value
            if event.get("receipt_id") == receipt["receipt_id"] and "receipt" in event:
                event["receipt"][receipt_field] = value
    elif fault == "operation":
        for record in (intent, receipt):
            record.update(operation="public_promotion", operation_type="publish")
    elif fault == "created_fence":
        intent["created_fence"] = True
    elif fault == "receipt_id":
        receipt["receipt_id"] = "never-recorded"
    elif fault == "consume_event":
        attempt["events"] = [
            event
            for event in attempt["events"]
            if not (event["state"] == "mutating" and event.get("intent_id") == intent["intent_id"])
        ]
        for sequence, event in enumerate(attempt["events"], 1):
            event["sequence"] = sequence
    else:
        attempt["provider_evidence"][provider]["receipts"][0]["operation"] = "public_promotion"
    _assert_terminal_evidence_rejected((storage, query, document), monkeypatch)


def test_existing_terminal_snapshots_bind_mutations_without_new_event_fields(canonical):
    storage, _, query, original = canonical
    document = DistributionOutboxRepository(storage).read(original["outbox_id"])
    for event in document["attempts"][-1]["events"]:
        event.pop("intent", None)
        event.pop("receipt", None)
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    assert aggregate_exit_code([document]) == 0
    result = publication_status(storage, query, now=NOW)["provider"]
    assert result["state"] == "published"
    assert result["external_verified"] is True
    assert storage.data == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing_outbox",
        "missing_receipt",
        "ambiguous",
        "duplicate_receipt",
        "wrong_provider",
        "wrong_attempt",
        "wrong_proof",
        "missing_provider",
        "weekly_conflict",
    ],
)
def test_canonical_evidence_failures_cannot_fall_back_to_green_legacy(canonical, fault):
    storage, manifest, query, document = canonical
    receipts(storage, manifest)  # Deliberately green legacy evidence is not a fallback.
    leg = document["providers"]["youtube"]
    if fault == "missing_outbox":
        storage.data.pop(outbox_path(document["outbox_id"]))
    else:
        if fault == "missing_receipt":
            leg["receipts"] = []
        elif fault == "ambiguous":
            leg["receipts"][0]["ambiguous"] = True
        elif fault == "duplicate_receipt":
            leg["receipts"].append(copy.deepcopy(leg["receipts"][0]))
        elif fault == "wrong_provider":
            leg["receipts"][0]["provider"] = "spotify"
        elif fault == "wrong_attempt":
            leg["receipts"][0]["attempt_id"] = "another"
        elif fault == "wrong_proof":
            leg["verification"]["proof"]["evidence"]["article_sha256"] = "c" * 64
        elif fault == "missing_provider":
            document["providers"].pop("spotify")
        else:
            document["weekly_aggregation"]["unresolved_conditions"] = ["identity_conflict"]
        storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    try:
        result = publication_status(storage, query, now=NOW, youtube_readback=ready)
    except PublicationStatusError as exc:
        assert exc.status == 409
    else:
        assert result["provider"]["external_verified"] is False
        assert result["provider"]["state"] != "published"
    assert storage.data == before


@pytest.mark.parametrize(
    "field",
    ["week", "publish_run_id", "article_sha256", "manifest_sha256", "accepted_job_id", "locale"],
)
def test_outbox_identity_and_locale_are_bound_to_accepted_job(canonical, field):
    storage, _, query, document = canonical
    if field == "locale":
        document["providers"]["youtube"]["context"]["locale"] = "es"
    else:
        document["publication_identity"][field] = "different"
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    with pytest.raises(PublicationStatusError) as error:
        publication_status(storage, query, now=NOW)
    assert error.value.status == 409


def test_outbox_failed_attempt_truth_is_not_weekly_success(canonical):
    storage, _, query, document = canonical
    for leg in document["providers"].values():
        leg["result"] = leg["verification"]["result"] = "failed_terminal"
    document["attempts"][0]["terminal_outcome"] = "failed_terminal"
    document["weekly_aggregation"]["state"] = "failed_terminal"
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    response = publication_status(storage, query, now=NOW)
    assert response["provider"]["state"] == "failed"
    assert response["provider"]["external_verified"] is False
    assert response["provider"]["weekly"]["state"] == "failed_terminal"
    assert response["provider"]["attempts"][0]["terminal_outcome"] == "failed_terminal"


def test_green_latest_attempt_cannot_erase_prior_weekly_identity_uncertainty(canonical):
    storage, _, query, document = canonical
    prior = copy.deepcopy(document["attempts"][0])
    prior.update(attempt_id="earlier-attempt", terminal_outcome="provider_unknown")
    document["attempts"].insert(0, prior)
    document["weekly_aggregation"]["evaluated_attempt_ids"].insert(0, prior["attempt_id"])
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    result = publication_status(storage, query, now=NOW)["provider"]
    assert result["state"] == "unknown"
    assert result["external_verified"] is False
    assert result["weekly"]["state"] == "provider_unknown"
    assert [attempt["terminal_outcome"] for attempt in result["attempts"]] == [
        "provider_unknown",
        "published_verified",
    ]


def test_exact_script_without_outbox_does_not_use_legacy_delivery(accepted):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    manifest["review"] = {"snapshot": {}}
    save(storage, manifest)
    response = publication_status(storage, query, now=NOW, youtube_readback=ready)
    assert response["provider"]["state"] == "unknown"
    assert response["provider"]["semantics"] == "canonical_public_outbox"


def test_outbox_storage_unavailable_is_not_absent_or_legacy(canonical, monkeypatch):
    storage, manifest, query, _ = canonical
    receipts(storage, manifest)
    get_bytes = storage.get_bytes

    def unavailable(path):
        if path.startswith("distribution-outbox/"):
            raise StorageUnavailableError("unavailable")
        return get_bytes(path)

    monkeypatch.setattr(storage, "get_bytes", unavailable)
    with pytest.raises(StorageUnavailableError):
        publication_status(storage, query, now=NOW)


def _http_response(message):
    class Socket:
        def makefile(self, *args):
            return io.BytesIO(message)

    response = HTTPResponse(Socket())
    response.begin()
    return response


def _offline_status_backend(monkeypatch, storage):
    backend = AzureBlobStorageBackend("https://offline.blob.core.windows.net", "artifacts")
    monkeypatch.setattr(backend._credential, "get_token", lambda _: "offline-token")

    def read(request, **kwargs):
        path = unquote(urlsplit(request.full_url).path).removeprefix("/artifacts/")
        body = storage.data.get(path)
        if body is None:
            raise HTTPError(request.full_url, 404, "absent", {}, io.BytesIO())
        return _http_response(
            b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )

    monkeypatch.setattr("podcaster.storage.urlopen", read)
    return backend


def _status_handler_response(monkeypatch, backend, query, handler):
    from fastapi.testclient import TestClient

    from podcaster import monitoring

    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    headers = {"x-podcaster-api-key": "test-status-key"}
    url = "/api/publication-status?" + urlencode(query)
    if handler == "stdlib":
        monkeypatch.setattr("podcaster.api.create_storage_backend", lambda: backend)
        request = FakeHandler("GET", url, headers=headers)
        GenerateHandler.do_GET(request)
        return request.response_code, request.get_response_json()
    monkeypatch.setattr(monitoring, "get_storage", lambda: backend)
    with TestClient(monitoring.app, raise_server_exceptions=False) as client:
        response = client.get(url, headers=headers)
    return response.status_code, response.json()


def _encoded_provider_request(request):
    class CapturingSocket:
        def __init__(self):
            self.data = b""

        def sendall(self, data):
            self.data += data

    connection = HTTPConnection("offline.invalid")
    connection.sock = socket = CapturingSocket()
    url = urlsplit(request.full_url)
    connection.request(
        request.get_method(),
        url.path + ("?" + url.query if url.query else ""),
        headers=dict(request.header_items()),
    )
    return socket.data


def _managed_identity_status_backend(monkeypatch, storage, token):
    backend = AzureBlobStorageBackend("https://offline.blob.core.windows.net", "artifacts")
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://offline.invalid/identity")
    monkeypatch.setenv("IDENTITY_HEADER", "offline-identity")
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    calls = []

    def read(request, **kwargs):
        assert request.get_method() == "GET"
        calls.append(request.full_url)
        if urlsplit(request.full_url).netloc == "offline.invalid":
            body = json.dumps({"access_token": token, "expires_on": "9999999999"}).encode()
        else:
            encoded = _encoded_provider_request(request)
            assert b"Authorization: Bearer " + token.encode("ascii") + b"\r\n" in encoded
            path = unquote(urlsplit(request.full_url).path).removeprefix("/artifacts/")
            body = storage.data.get(path)
            if body is None:
                raise HTTPError(request.full_url, 404, "absent", {}, io.BytesIO())
        return _http_response(
            b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )

    monkeypatch.setattr("podcaster.storage.urlopen", read)
    return backend, calls


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize(
    ("token", "valid"),
    [
        ("offline-access", True),
        ("AZaz09-._~+/==", True),
        ("offline.header.payload.signature", True),
        ("offline-\u6f22", False),
        ("offline-\u00e9", False),
        ("offline-\u200b", False),
        ("offline-\x00", False),
        ("offline-\x1f", False),
        ("offline-\x7f", False),
        ("offline-\r\n", False),
        ("offline- token", False),
        ("offline-\t", False),
    ],
    ids=[
        "opaque-ascii",
        "bearer-alphabet",
        "dotted-ascii",
        "non-latin1",
        "latin1",
        "zero-width",
        "nul",
        "unit-separator",
        "del",
        "crlf",
        "space",
        "tab",
    ],
)
def test_actual_managed_identity_status_boundary(
    accepted, monkeypatch, caplog, handler, token, valid
):
    storage, _, query = accepted
    before = copy.deepcopy(storage.data)
    backend, calls = _managed_identity_status_backend(monkeypatch, storage, token)
    path = f"jobs/{JOB}/manifest.json"

    if valid:
        assert backend.get_bytes(path) == storage.data[path]
    else:
        with pytest.raises(StorageUnavailableError) as error:
            backend.get_bytes(path)
        assert (
            str(error.value) == "managed identity token response included an invalid access token"
        )
        assert error.value.__context__ is None
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    if valid:
        assert status == 200
        assert body["job_id"] == JOB
        assert body["provider"]["external_verified"] is False
        assert any(urlsplit(url).hostname == "offline.blob.core.windows.net" for url in calls)
    else:
        assert status == 503
        assert body == {"error": "status_unavailable"}
        assert len(calls) == 2
        assert all(urlsplit(url).netloc == "offline.invalid" for url in calls)
    assert token not in json.dumps(body) + caplog.text
    assert "UnicodeEncodeError" not in json.dumps(body) + caplog.text
    assert storage.data == before


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
def test_actual_managed_identity_absent_blob_remains_404(accepted, monkeypatch, handler):
    storage, _, query = accepted
    del storage.data[f"jobs/{JOB}/manifest.json"]
    backend, _ = _managed_identity_status_backend(monkeypatch, storage, "offline-access")

    assert backend.get_bytes(f"jobs/{JOB}/manifest.json") is None
    status, body = _status_handler_response(monkeypatch, backend, query, handler)

    assert status == 404
    assert body == {"error": "job_not_found"}
    assert storage.data == {}


@pytest.mark.parametrize(
    ("token", "encodable"),
    [("offline-\u6f22", False), ("offline-\u00e9", True)],
    ids=["non-latin1", "latin1"],
)
def test_real_provider_header_encoding_distinguishes_non_ascii_tokens(token, encodable):
    from urllib.request import Request

    request = Request(
        "https://www.googleapis.com/youtube/v3/videos",
        headers={"Authorization": f"Bearer {token}"},
    )
    if encodable:
        assert b"Authorization: Bearer " + token.encode("latin-1") in _encoded_provider_request(
            request
        )
    else:
        with pytest.raises(UnicodeEncodeError):
            _encoded_provider_request(request)


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize(
    ("token", "valid"),
    [
        ("offline-access", True),
        ("AZaz09-._~+/==", True),
        ("offline.header.payload.signature", True),
        ("offline-\u6f22", False),
        ("offline-\u00e9", False),
        ("offline-\u200b", False),
        ("offline-\x00", False),
        ("offline-\x1f", False),
        ("offline-\x7f", False),
        ("offline-\r\n", False),
        ("offline- token", False),
        ("offline-\t", False),
    ],
    ids=[
        "opaque-ascii",
        "bearer-alphabet",
        "dotted-ascii",
        "non-latin1",
        "latin1",
        "zero-width",
        "nul",
        "unit-separator",
        "del",
        "crlf",
        "space",
        "tab",
    ],
)
def test_actual_oauth_bearer_token_boundary(accepted, monkeypatch, caplog, handler, token, valid):
    from functools import partial

    from podcaster.video.distribution import (
        VideoDistributionConfig,
        YouTubeDeliveryError,
        _DefaultTransport,
        _get_youtube_access_token,
    )

    storage, manifest, query = accepted
    identity = receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    backend = _offline_status_backend(monkeypatch, storage)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "offline-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "offline-secret")
    monkeypatch.setattr(
        "podcaster.video.distribution._load_youtube_refresh_token", lambda: "offline-refresh"
    )
    notify = MagicMock(side_effect=AssertionError("readback must not notify"))
    monkeypatch.setattr("podcaster.video.distribution._notify_youtube_reauthorization", notify)
    for module in ("podcaster.api", "podcaster.monitoring"):
        monkeypatch.setattr(module + ".publication_status", partial(publication_status, now=NOW))
    calls = []

    def provider_response(request, **kwargs):
        oauth = request.full_url == "https://oauth2.googleapis.com/token"
        assert request.method == ("POST" if oauth else "GET")
        calls.append("oauth" if oauth else "videos_list")
        if oauth:
            body = json.dumps({"access_token": token}).encode()
        else:
            assert valid
            url = urlsplit(request.full_url)
            assert (url.scheme, url.netloc, url.path) == (
                "https",
                "www.googleapis.com",
                "/youtube/v3/videos",
            )
            assert parse_qs(url.query) == {
                "id": ["_ZD9E_zTjtM"],
                "part": ["snippet,status,processingDetails"],
            }
            encoded = _encoded_provider_request(request)
            assert b"Authorization: Bearer " + token.encode("ascii") + b"\r\n" in encoded
            body = json.dumps(
                {
                    "items": [
                        {
                            "id": "_ZD9E_zTjtM",
                            "snippet": {"tags": [youtube_identity_tag(identity)]},
                            "status": {"uploadStatus": "processed", "privacyStatus": "unlisted"},
                            "processingDetails": {"processingStatus": "succeeded"},
                        }
                    ]
                }
            ).encode()
        return _http_response(
            b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )

    monkeypatch.setattr("podcaster.video.distribution.urlopen", provider_response)
    if valid:
        assert _get_youtube_access_token(VideoDistributionConfig(), _DefaultTransport()) == token
    else:
        with pytest.raises(YouTubeDeliveryError) as raised:
            _get_youtube_access_token(VideoDistributionConfig(), _DefaultTransport())
        assert raised.value.code == "youtube_oauth_missing_access_token"
        assert raised.value.stage == "oauth_token"
        assert raised.value.retryable is False
        assert str(raised.value) == "YouTube token response missing access_token"
        assert raised.value.__context__ is None
    assert (read_youtube_status(identity, "_ZD9E_zTjtM") is not None) is valid
    projected = publication_status(backend, query, now=NOW)
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert status == 200
    assert body == projected
    assert body["provider"]["state"] == ("published" if valid else "unknown")
    assert body["provider"]["external_verified"] is valid
    assert body["provider"]["providers"]["youtube"]["code"] == (
        "processed_upload_readback" if valid else "readback_unavailable"
    )
    assert calls == ["oauth"] + (["oauth", "videos_list"] if valid else ["oauth"]) * 3
    notify.assert_not_called()
    assert storage.data == before
    assert not any(
        private in json.dumps(body) + caplog.text
        for private in (token, "offline-refresh", "offline-secret", "UnicodeEncodeError")
    )


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize("provider", ["youtube", "spotify"])
@pytest.mark.parametrize("value", [[], {}, None, False, 7, 1.5, "", "published", "partial "])
def test_invalid_persisted_provider_result_is_typed_409(
    canonical, monkeypatch, handler, provider, value
):
    storage, _, query, document = canonical
    document["providers"][provider]["result"] = value
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    backend = _offline_status_backend(monkeypatch, storage)
    with pytest.raises(DistributionOutboxError, match="provider result is invalid"):
        DistributionOutboxRepository(backend).read(document["outbox_id"])
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert (status, body) == (409, {"error": "invalid_outbox_evidence"})
    assert storage.data == before


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize("fixture_name", ["canonical", "recovered"])
def test_valid_terminal_provider_results_through_both_handlers(
    request, monkeypatch, handler, fixture_name
):
    fixture = request.getfixturevalue(fixture_name)
    storage, query = fixture[0], fixture[-2]
    before = copy.deepcopy(storage.data)
    backend = _offline_status_backend(monkeypatch, storage)
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert status == 200
    assert body["provider"]["state"] == "published"
    assert body["provider"]["external_verified"] is True
    assert body["provider"]["weekly"]["state"] == (
        "published_verified_recovered" if fixture_name == "recovered" else "published_verified"
    )
    assert storage.data == before


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize("dependency", ["oauth", "videos_list"])
@pytest.mark.parametrize(
    "fault",
    [
        "truncated",
        "chunk",
        "status_line",
        "http404",
        "error_read",
        "urlerror",
        "invalid_json",
        "invalid_utf8",
        "invalid_shape",
        "valid",
    ],
)
def test_actual_provider_http_readback_contract(
    accepted, monkeypatch, caplog, handler, dependency, fault
):
    from functools import partial

    storage, manifest, query = accepted
    identity = receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    backend = _offline_status_backend(monkeypatch, storage)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "offline-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "offline-secret")
    monkeypatch.setattr(
        "podcaster.video.distribution._load_youtube_refresh_token", lambda: "offline-refresh"
    )
    notify = MagicMock(side_effect=AssertionError("readback must not notify"))
    monkeypatch.setattr("podcaster.video.distribution._notify_youtube_reauthorization", notify)
    for module in ("podcaster.api", "podcaster.monitoring"):
        monkeypatch.setattr(module + ".publication_status", partial(publication_status, now=NOW))
    calls = []

    def provider_response(request, **kwargs):
        oauth = request.full_url == "https://oauth2.googleapis.com/token"
        assert request.method == ("POST" if oauth else "GET")
        if not oauth:
            assert request.full_url.startswith("https://www.googleapis.com/youtube/v3/videos?")
            assert "id=_ZD9E_zTjtM" in request.full_url
        calls.append("oauth" if oauth else "videos_list")
        body = (
            b'{"access_token":"offline-access"}'
            if oauth
            else json.dumps(
                {
                    "items": [
                        {
                            "id": "_ZD9E_zTjtM",
                            "snippet": {"tags": [youtube_identity_tag(identity)]},
                            "status": {"uploadStatus": "processed", "privacyStatus": "unlisted"},
                            "processingDetails": {"processingStatus": "succeeded"},
                        }
                    ]
                }
            ).encode()
        )
        if ("oauth" if oauth else "videos_list") == dependency:
            if fault == "truncated":
                return _http_response(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nprivate-fragment"
                )
            if fault == "chunk":
                return _http_response(
                    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nprivate-chunk\r\n"
                )
            if fault == "status_line":
                return _http_response(b"private-invalid-status\r\n\r\n")
            if fault == "http404":
                raise HTTPError(
                    request.full_url, 404, "private-error", {}, io.BytesIO(b"private-body")
                )
            if fault == "error_read":
                raise HTTPError(
                    request.full_url,
                    503,
                    "private-error",
                    {},
                    _http_response(
                        b"HTTP/1.1 503 Unavailable\r\nContent-Length: 100\r\n\r\nprivate-fragment"
                    ),
                )
            if fault == "urlerror":
                raise URLError("private-transport")
            if fault in {"invalid_json", "invalid_utf8", "invalid_shape"}:
                body = {
                    "invalid_json": b"private-invalid-json",
                    "invalid_utf8": b"\xff",
                    "invalid_shape": b'{"access_token":[],"items":{}}',
                }[fault]
        return _http_response(
            b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )

    monkeypatch.setattr("podcaster.video.distribution.urlopen", provider_response)
    snippet = read_youtube_status(identity, "_ZD9E_zTjtM")
    assert (snippet is not None) == (fault == "valid")
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert status == 200
    provider = body["provider"]
    assert provider["external_verified"] is (fault == "valid")
    assert provider["state"] == ("published" if fault == "valid" else "unknown")
    assert provider["providers"]["youtube"]["code"] == (
        "processed_upload_readback" if fault == "valid" else "readback_unavailable"
    )
    expected_calls = (
        ["oauth"]
        if dependency == "oauth" and fault != "valid"
        else [
            "oauth",
            "videos_list",
        ]
    )
    assert calls == expected_calls * 2
    notify.assert_not_called()
    assert storage.data == before
    assert not any(
        private in json.dumps(body) + caplog.text
        for private in ("private-", "offline-access", "offline-refresh", "offline-secret")
    )


def _check_youtube_json_readback(
    accepted,
    monkeypatch,
    handler,
    change,
    *,
    available=False,
    state="unknown",
    code="readback_unavailable",
):
    from functools import partial

    storage, manifest, query = accepted
    identity = receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    backend = _offline_status_backend(monkeypatch, storage)
    payload = {
        "items": [
            {
                "id": "_ZD9E_zTjtM",
                "snippet": {"tags": [youtube_identity_tag(identity)]},
                "status": {"uploadStatus": "processed", "privacyStatus": "unlisted"},
                "processingDetails": {"processingStatus": "succeeded"},
            }
        ]
    }
    payload = change(payload)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "offline-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "offline-secret")
    monkeypatch.setattr(
        "podcaster.video.distribution._load_youtube_refresh_token", lambda: "offline-refresh"
    )
    notify = MagicMock(side_effect=AssertionError("readback must not notify"))
    monkeypatch.setattr("podcaster.video.distribution._notify_youtube_reauthorization", notify)
    for module in ("podcaster.api", "podcaster.monitoring"):
        monkeypatch.setattr(module + ".publication_status", partial(publication_status, now=NOW))
    calls = []

    def provider_response(request, **kwargs):
        oauth = request.full_url == "https://oauth2.googleapis.com/token"
        assert request.method == ("POST" if oauth else "GET")
        if not oauth:
            url = urlsplit(request.full_url)
            assert (url.scheme, url.netloc, url.path) == (
                "https",
                "www.googleapis.com",
                "/youtube/v3/videos",
            )
            assert parse_qs(url.query) == {
                "id": ["_ZD9E_zTjtM"],
                "part": ["snippet,status,processingDetails"],
            }
        calls.append("oauth" if oauth else "videos_list")
        body = json.dumps({"access_token": "offline-access"} if oauth else payload).encode()
        return _http_response(
            b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )

    monkeypatch.setattr("podcaster.video.distribution.urlopen", provider_response)
    assert (read_youtube_status(identity, "_ZD9E_zTjtM") is not None) is available
    projected = publication_status(backend, query, now=NOW)
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert status == 200
    assert body == projected
    assert body["provider"]["state"] == state
    assert body["provider"]["external_verified"] is (state == "published")
    assert body["provider"]["providers"]["youtube"]["code"] == code
    assert body["provider"]["semantics"] == "legacy_delivery"
    assert calls == ["oauth", "videos_list"] * 3
    notify.assert_not_called()
    assert storage.data == before
    assert not any(
        private in json.dumps(body)
        for private in ("offline-access", "offline-refresh", "offline-secret")
    )


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("status", "privacyStatus"),
        ("status", "uploadStatus"),
        ("processingDetails", "processingStatus"),
    ],
)
@pytest.mark.parametrize("value", [[], {}, None, False, 7, 1.5])
def test_actual_provider_nested_status_types(accepted, monkeypatch, handler, section, field, value):
    def change(payload):
        payload["items"][0][section][field] = value
        return payload

    _check_youtube_json_readback(accepted, monkeypatch, handler, change)


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize(
    "case",
    [
        "top_list",
        "items_object",
        "item_list",
        "status_list",
        "processing_null",
        "snippet_bool",
        "tags_object",
        "tags_mixed",
        "empty_items",
        "wrong_id",
        "wrong_tag",
        "public",
        "unlisted",
        "private",
        "missing_upload",
        "missing_processing",
        "missing_privacy",
        "missing_status",
        "missing_details",
        "future_status",
        "failed",
        "empty_status",
    ],
)
def test_actual_provider_json_shape_and_legacy_controls(accepted, monkeypatch, handler, case):
    def change(payload):
        item = payload["items"][0]
        if case == "top_list":
            return []
        if case == "items_object":
            payload["items"] = {}
        elif case == "item_list":
            payload["items"] = [[]]
        elif case == "status_list":
            item["status"] = []
        elif case == "processing_null":
            item["processingDetails"] = None
        elif case == "snippet_bool":
            item["snippet"] = False
        elif case == "tags_object":
            item["snippet"]["tags"] = {}
        elif case == "tags_mixed":
            item["snippet"]["tags"].append({})
        elif case == "empty_items":
            payload["items"] = []
        elif case == "wrong_id":
            item["id"] = "other_video"
        elif case == "wrong_tag":
            item["snippet"]["tags"] = ["unrelated"]
        elif case in {"public", "unlisted", "private"}:
            item["status"]["privacyStatus"] = case
        elif case == "missing_upload":
            del item["status"]["uploadStatus"]
        elif case == "missing_processing":
            del item["processingDetails"]["processingStatus"]
        elif case == "missing_privacy":
            del item["status"]["privacyStatus"]
        elif case == "missing_status":
            del item["status"]
        elif case == "missing_details":
            del item["processingDetails"]
        elif case == "future_status":
            item["status"]["uploadStatus"] = "future-provider-state"
        elif case == "failed":
            item["status"]["uploadStatus"] = "failed"
        elif case == "empty_status":
            item["status"]["privacyStatus"] = ""
        return payload

    unavailable = {
        "top_list",
        "items_object",
        "item_list",
        "status_list",
        "processing_null",
        "snippet_bool",
        "tags_object",
        "tags_mixed",
        "empty_items",
        "wrong_id",
        "wrong_tag",
    }
    state = (
        "unknown"
        if case in unavailable
        else "published"
        if case in {"public", "unlisted"}
        else "failed"
        if case == "failed"
        else "pending"
    )
    code = {
        "unknown": "readback_unavailable",
        "published": "processed_upload_readback",
        "failed": "upload_failed",
        "pending": "upload_not_ready",
    }[state]
    _check_youtube_json_readback(
        accepted,
        monkeypatch,
        handler,
        change,
        available=case not in unavailable,
        state=state,
        code=code,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field in ("privacyStatus", "uploadStatus", "processingStatus")
        for value in ([], {}, None, False, 7, 1.5)
    ]
    + [(None, value) for value in ([], None, False, 7, 1.5, "invalid")],
)
def test_status_callback_rejects_malformed_readback_shapes(accepted, field, value):
    storage, manifest, query = accepted
    identity = receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    readback = ready(identity, "_ZD9E_zTjtM")
    if field is None:
        readback = value
    else:
        readback[field] = value
    body = publication_status(storage, query, now=NOW, youtube_readback=lambda *_: readback)
    assert body["provider"]["state"] == "unknown"
    assert body["provider"]["external_verified"] is False
    assert body["provider"]["providers"]["youtube"]["code"] == "readback_unavailable"
    assert storage.data == before


def test_status_callback_programming_error_is_not_readback_unavailability(accepted):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    before = copy.deepcopy(storage.data)

    def broken(*_):
        raise TypeError("broken callback implementation")

    with pytest.raises(TypeError, match="broken callback implementation"):
        publication_status(storage, query, now=NOW, youtube_readback=broken)
    assert storage.data == before


def test_status_callback_accepts_mapping_without_optional_metadata(accepted):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    readback = MappingProxyType(
        {"uploadStatus": "processed", "processingStatus": "succeeded", "privacyStatus": "unlisted"}
    )
    body = publication_status(storage, query, now=NOW, youtube_readback=lambda *_: readback)
    assert body["provider"]["state"] == "published"
    assert body["provider"]["external_verified"] is True
    assert body["provider"]["semantics"] == "legacy_delivery"
    assert storage.data == before


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize("dependency", ["blob", "identity"])
@pytest.mark.parametrize(
    "message",
    [
        b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nprivate-fragment",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\ninvalid-chunk\r\n",
        b"private-invalid-status\r\n\r\n",
    ],
    ids=["truncated-content-length", "invalid-chunk", "invalid-status-line"],
)
def test_real_azure_http_read_failures_are_typed_and_sanitized(
    accepted, monkeypatch, handler, dependency, message
):
    _, _, query = accepted
    backend = AzureBlobStorageBackend("https://offline.blob.core.windows.net", "artifacts")
    if dependency == "blob":
        monkeypatch.setattr(backend._credential, "get_token", lambda _: "offline-token")
    monkeypatch.setattr("podcaster.storage.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "podcaster.storage.urlopen", lambda *args, **kwargs: _http_response(message)
    )
    with pytest.raises(StorageUnavailableError) as error:
        backend.get_bytes(f"jobs/{JOB}/manifest.json")
    assert str(error.value) in {"blob read failed", "managed identity token request failed"}
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert status == 503
    assert body == {"error": "status_unavailable"}


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("attempts", None),
        ("attempts", {}),
        ("attempts", "invalid"),
        ("attempts", 0),
        ("attempts", False),
        ("attempts", [None]),
        ("attempts", [[]]),
        ("attempts", ["invalid"]),
        ("attempts", [{}]),
        ("attempts", [{"attempt_id": []}]),
        ("recovery_authz", None),
        ("recovery_authz", {}),
        ("recovery_authz", [None]),
        ("enqueue", None),
        ("enqueue", []),
        ("weekly_aggregation", None),
        ("weekly_aggregation", []),
        ("canonical_artifact", None),
        ("canonical_artifact", []),
    ],
)
def test_malformed_current_outbox_collections_are_409_without_mutation(
    canonical, monkeypatch, handler, field, value
):
    storage, _, query, document = canonical
    document[field] = value
    storage.data[outbox_path(document["outbox_id"])] = json.dumps(document).encode()
    before = copy.deepcopy(storage.data)
    backend = _offline_status_backend(monkeypatch, storage)
    with pytest.raises(DistributionOutboxError):
        DistributionOutboxRepository(backend).read(document["outbox_id"])
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert status == 409
    assert body == {"error": "invalid_outbox_evidence"}
    assert storage.data == before


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize("stage", ["synthesis_runner", "video_runner"])
@pytest.mark.parametrize("value", [[], {}, None, 7, False])
def test_malformed_manifest_stage_status_is_unknown_without_mutation(
    canonical, monkeypatch, handler, stage, value
):
    storage, manifest, query, _ = canonical
    manifest["generation"][stage]["status"] = value
    save(storage, manifest)
    before = copy.deepcopy(storage.data)
    backend = _offline_status_backend(monkeypatch, storage)
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert status == 200
    assert body["synthesis" if stage == "synthesis_runner" else "video"]["state"] == "unknown"
    assert body["provider"]["state"] == "published"
    assert body["provider"]["external_verified"] is True
    assert storage.data == before


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
def test_real_azure_current_status_positive_and_missing_outbox_controls(
    canonical, monkeypatch, handler
):
    storage, _, query, document = canonical
    backend = _offline_status_backend(monkeypatch, storage)
    before = copy.deepcopy(storage.data)
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert status == 200
    assert body["provider"]["state"] == "published"
    assert body["provider"]["external_verified"] is True
    assert body["synthesis"]["state"] == body["video"]["state"] == "succeeded"
    assert storage.data == before
    del storage.data[outbox_path(document["outbox_id"])]
    before = copy.deepcopy(storage.data)
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    assert status == 200
    assert body["provider"]["state"] == "unknown"
    assert body["provider"]["external_verified"] is False
    assert body["provider"]["providers"]["youtube"]["code"] == "outbox_missing"
    assert storage.data == before


@pytest.mark.parametrize("handler", ["stdlib", "fastapi"])
@pytest.mark.parametrize("dependency", ["blob", "identity"])
@pytest.mark.parametrize("http_status", [404, 500, 503])
def test_real_azure_backend_transport_errors_are_sanitized_in_both_handlers(
    accepted, monkeypatch, handler, dependency, http_status
):
    _, _, query = accepted
    backend = AzureBlobStorageBackend("https://offline.blob.core.windows.net", "artifacts")
    if dependency == "blob":
        monkeypatch.setattr(backend._credential, "get_token", lambda _: "offline-token")
    monkeypatch.setattr("podcaster.storage.time.sleep", lambda _: None)

    def failed(request, **kwargs):
        raise HTTPError(
            request.full_url,
            http_status,
            "private-secret-detail",
            {},
            io.BytesIO(b"private-secret-body"),
        )

    monkeypatch.setattr("podcaster.storage.urlopen", failed)
    status, body = _status_handler_response(monkeypatch, backend, query, handler)
    absent = dependency == "blob" and http_status == 404
    assert status == (404 if absent else 503)
    assert body == {"error": "job_not_found" if absent else "status_unavailable"}


def test_known_w42_provider_ids_unlisted_and_spotify_video_are_terminal(accepted):
    storage, manifest, query = accepted
    manifest["generation"].update(
        synthesis_runner={"status": "completed"}, video_runner={"status": "completed"}
    )
    save(storage, manifest)
    receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    response = publication_status(storage, query, now=NOW, youtube_readback=ready)
    assert response["schema_version"] == "podcast_publication_status_v1"
    assert response["identity"] == {
        field: query[field]
        for field in ("week", "publish_run_id", "article_sha256", "manifest_sha256")
    }
    assert response["job_id"] == response["correlation_id"] == JOB
    assert response["synthesis"]["state"] == response["video"]["state"] == "succeeded"
    assert response["provider"]["state"] == "published"
    assert response["provider"]["external_verified"] is True
    assert storage.data == before  # no receipt, manifest, queue or provider mutation


@pytest.mark.parametrize("field", ["week", "publish_run_id", "article_sha256", "manifest_sha256"])
def test_exact_identity_conflicts_fail_closed(accepted, field):
    storage, _, query = accepted
    query[field] = {"week": "2026-W43", "publish_run_id": "1"}.get(field, "c" * 64)
    with pytest.raises(PublicationStatusError) as error:
        publication_status(storage, query, now=NOW)
    assert error.value.status == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_id", "../../secret"),
        ("correlation_id", "../secret"),
        ("language", "es"),
        ("correlation_id", "different"),
    ],
)
def test_path_language_and_correlation_guards(accepted, field, value):
    storage, _, query = accepted
    query[field] = value
    with pytest.raises(PublicationStatusError):
        publication_status(storage, query, now=NOW)


@pytest.mark.parametrize(
    "synthesis,video,expected",
    [
        (None, None, ("pending", "pending")),
        ({"status": "running"}, {"status": "running"}, ("started", "pending")),
        ({"status": "failed"}, None, ("failed", "pending")),
        ({"status": "completed"}, {"status": "failed"}, ("succeeded", "failed")),
        ({"status": "nonsense"}, None, ("unknown", "pending")),
        ({}, {}, ("unknown", "unknown")),
        ({"status": "prepared"}, {"status": "prepared"}, ("unknown", "succeeded")),
        ({"status": "queued"}, {"status": "started"}, ("pending", "pending")),
    ],
)
def test_stage_contract_states(accepted, synthesis, video, expected):
    storage, manifest, query = accepted
    manifest["generation"].update(synthesis_runner=synthesis, video_runner=video)
    save(storage, manifest)
    response = publication_status(storage, query, now=NOW)
    assert (response["synthesis"]["state"], response["video"]["state"]) == expected
    assert response["provider"]["state"] == "pending"
    assert response["provider"]["external_verified"] is False


def test_actual_progress_marks_synthesis_arrival(accepted):
    from podcaster.progress import emit_progress

    storage, _, query = accepted
    emit_progress(storage, JOB, stage="synthesis", phase="tts", at=NOW)
    response = publication_status(storage, query, now=NOW)
    assert response["synthesis"]["state"] == "started"


@pytest.mark.parametrize(
    "fault",
    [
        "spotify_missing",
        "audio_only",
        "spotify_draft",
        "spotify_no_readback",
        "stale",
        "future",
        "corrupt",
        "identity",
        "missing_id",
        "youtube_unknown",
    ],
)
def test_incomplete_stale_mismatched_receipts_never_green(accepted, fault):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    path = evidence_path(JOB)
    document = json.loads(storage.data[path])
    if fault == "spotify_missing":
        document["records"].pop()
    elif fault == "audio_only":
        document["records"][-1]["media_kind"] = "audio"
    elif fault == "spotify_draft":
        document["records"][-1].update(
            outcome="draft_created", native_state="draft", status="draft"
        )
    elif fault == "spotify_no_readback":
        document["records"][-1]["verification"] = "none"
    elif fault in {"stale", "future"}:
        delta = -timedelta(days=8) if fault == "stale" else timedelta(days=1)
        for record in document["records"]:
            record["checked_at"] = (NOW + delta).isoformat()
    elif fault == "identity":
        document["records"][-1]["article_sha256"] = "c" * 64
    elif fault == "missing_id":
        document["records"][-1].update(provider_id=None, provider_artifact_id=None)
    elif fault == "youtube_unknown":
        document["records"][0].update(outcome="publication_unknown", status="unknown")
    storage.data[path] = b"bad JSON" if fault == "corrupt" else json.dumps(document).encode()
    if fault in {"corrupt", "identity"}:
        with pytest.raises(PublicationStatusError):
            publication_status(storage, query, now=NOW, youtube_readback=ready)
    else:
        response = publication_status(storage, query, now=NOW, youtube_readback=ready)
        assert response["provider"]["external_verified"] is False
        assert response["provider"]["state"] != "published"


@pytest.mark.parametrize(
    "readback",
    [
        None,
        {"uploadStatus": "uploaded", "processingStatus": "processing", "privacyStatus": "unlisted"},
        {"uploadStatus": "processed", "processingStatus": "succeeded", "privacyStatus": "private"},
        {"uploadStatus": "failed"},
        {"uploadStatus": "uploaded", "processingStatus": "failed"},
    ],
)
def test_youtube_processing_and_visibility_truth(accepted, readback):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    response = publication_status(storage, query, now=NOW, youtube_readback=lambda *_: readback)
    assert response["provider"]["external_verified"] is False


@pytest.mark.parametrize("authorized", [False, True])
def test_authenticated_api_contract(accepted, monkeypatch, authorized):
    storage, _, query = accepted
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    factory = MagicMock(return_value=storage)
    monkeypatch.setattr("podcaster.api.create_storage_backend", factory)
    handler = FakeHandler(
        "GET",
        "/api/publication-status?" + urlencode(query),
        headers={"x-podcaster-api-key": "test-status-key" if authorized else "wrong"},
    )
    GenerateHandler.do_GET(handler)
    assert handler.response_code == (200 if authorized else 401)
    if authorized:
        assert handler.get_response_json()["schema_version"] == "podcast_publication_status_v1"
        assert handler.response_headers["Cache-Control"] == "no-store"
    else:
        factory.assert_not_called()


def test_api_duplicate_and_unknown_query_parameters_rejected(accepted, monkeypatch):
    storage, _, query = accepted
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    monkeypatch.setattr("podcaster.api.create_storage_backend", lambda: storage)
    for suffix in ("&job_id=second", "&path=secret"):
        handler = FakeHandler(
            "GET",
            "/api/publication-status?" + urlencode(query) + suffix,
            headers={"x-podcaster-api-key": "test-status-key"},
        )
        GenerateHandler.do_GET(handler)
        assert handler.response_code == 400


def test_api_unconfigured_auth_and_storage_errors_are_explicit(accepted, monkeypatch):
    from azure.core.exceptions import ServiceRequestError

    _, _, query = accepted
    factory = MagicMock(side_effect=ServiceRequestError("internal storage detail"))
    monkeypatch.setattr("podcaster.api.create_storage_backend", factory)
    headers = {"x-podcaster-api-key": "test-status-key"}
    handler = FakeHandler("GET", "/api/publication-status?" + urlencode(query), headers=headers)
    GenerateHandler.do_GET(handler)
    assert handler.response_code == 401
    factory.assert_not_called()
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    handler = FakeHandler("GET", "/api/publication-status?" + urlencode(query), headers=headers)
    GenerateHandler.do_GET(handler)
    assert handler.response_code == 503
    assert "internal" not in handler.get_response_json()["error"]


def test_status_oauth_failure_does_not_send_notifications(accepted, monkeypatch):
    storage, manifest, _ = accepted
    identity = receipts(storage, manifest)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "test-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("podcaster.video.distribution._load_youtube_refresh_token", lambda: "test")
    transport = MagicMock()
    transport.request.return_value = (400, b'{"error":"invalid_grant"}')
    notify = MagicMock(side_effect=AssertionError("status must not notify or write"))
    monkeypatch.setattr("podcaster.video.distribution._notify_youtube_reauthorization", notify)
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda **_: transport)
    assert read_youtube_status(identity, "_ZD9E_zTjtM") is None
    notify.assert_not_called()


@pytest.mark.parametrize("missing", ["client_id", "client_secret", "refresh_token"])
def test_missing_api_readback_credential_fails_closed_without_provider_request(
    accepted, monkeypatch, missing
):
    storage, manifest, _ = accepted
    identity = receipts(storage, manifest)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "" if missing == "client_id" else "test-client")
    monkeypatch.setenv(
        "VIDEO_YOUTUBE_CLIENT_SECRET", "" if missing == "client_secret" else "test-secret"
    )
    monkeypatch.setattr(
        "podcaster.video.distribution._load_youtube_refresh_token",
        lambda: None if missing == "refresh_token" else "test-refresh",
    )
    transport = MagicMock(side_effect=AssertionError("missing credentials must not issue requests"))
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", transport)
    assert read_youtube_status(identity, "_ZD9E_zTjtM") is None
    transport.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b'{"access_token":42}',
        b'{"access_token":"bad\\r\\nheader"}',
    ],
)
def test_malformed_oauth_readback_fails_closed(accepted, monkeypatch, body):
    storage, manifest, _ = accepted
    identity = receipts(storage, manifest)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "test-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("podcaster.video.distribution._load_youtube_refresh_token", lambda: "test")
    transport = MagicMock()
    transport.request.return_value = (200, body)
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda **_: transport)
    assert read_youtube_status(identity, "_ZD9E_zTjtM") is None
    assert transport.request.call_count == 1


@pytest.mark.parametrize(
    "item",
    [
        {"id": "other_video", "snippet": {}, "status": {}, "processingDetails": {}},
        {"id": "_ZD9E_zTjtM", "snippet": {"tags": ["unrelated"]}},
        {"id": "_ZD9E_zTjtM", "snippet": None},
    ],
)
def test_real_readback_rejects_wrong_id_tag_and_malformed_video(accepted, monkeypatch, item):
    storage, manifest, _ = accepted
    identity = receipts(storage, manifest)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "test-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("podcaster.video.distribution._load_youtube_refresh_token", lambda: "test")
    transport = MagicMock()
    transport.request.side_effect = [
        (200, b'{"access_token":"test-access"}'),
        (200, json.dumps({"items": [item]}).encode()),
    ]
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda **_: transport)
    assert read_youtube_status(identity, "_ZD9E_zTjtM") is None


def test_real_youtube_readback_uses_exact_video_and_identity_without_upload(accepted, monkeypatch):
    storage, manifest, _ = accepted
    identity = receipts(storage, manifest)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "test-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("podcaster.video.distribution._load_youtube_refresh_token", lambda: "test")
    transport = MagicMock()
    transport.request.side_effect = [
        (200, b'{"access_token":"test-access"}'),
        (
            200,
            json.dumps(
                {
                    "items": [
                        {
                            "id": "_ZD9E_zTjtM",
                            "snippet": {"tags": [youtube_identity_tag(identity)]},
                            "status": {"uploadStatus": "processed", "privacyStatus": "unlisted"},
                            "processingDetails": {"processingStatus": "succeeded"},
                        }
                    ]
                }
            ).encode(),
        ),
    ]
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda **_: transport)
    assert read_youtube_status(identity, "_ZD9E_zTjtM")["uploadStatus"] == "processed"
    assert len(transport.request.call_args_list) == 2
    assert transport.request.call_args_list[0].kwargs["method"] == "POST"  # OAuth only
    assert transport.request.call_args_list[1].kwargs["method"] == "GET"
    assert "/youtube/v3/videos?" in transport.request.call_args_list[1].args[0]


def test_legacy_oauth_failure_does_not_authorize_new_upload_claim(accepted, tmp_path, monkeypatch):
    from podcaster.publication_state import claim_evidence, latest_outcomes, read_evidence
    from podcaster.video.distribution import (
        VideoDistributionConfig,
        YouTubeDeliveryError,
        distribute_video,
    )
    from podcaster.video.job_runner import _record_video_publication
    from tests.test_video_distribution import FakeStorage

    storage, manifest, _ = accepted
    identity = publication_identity(manifest, JOB, "38270494467")
    append_evidence(
        storage,
        identity,
        platform="spotify",
        media_kind="video",
        operation="distribution",
        outcome="published",
        provider_artifact_id="127359743",
        native_state="published",
        verification="provider_readback",
        evidence_source="spotify_episode_readback",
        at=NOW,
    )
    spotify_receipt = latest_outcomes(read_evidence(storage, JOB))["spotify:video"].copy()
    claim_evidence(
        storage, identity, platform="youtube", media_kind="video", operation="upload_intent"
    )
    published = {
        "spotify_upload": {
            "status": "published",
            "outcome": "published",
            "episode_id": 127359743,
            "verification": "provider_readback",
            "native_state": "published",
            "retry_blocked": True,
        }
    }
    upload = MagicMock(
        side_effect=YouTubeDeliveryError(
            "transient OAuth failure",
            code="youtube_oauth_http_503",
            stage="oauth_token",
            retryable=True,
        )
    )
    spotify = MagicMock(side_effect=AssertionError("must never duplicate Spotify"))
    monkeypatch.setattr("podcaster.video.distribution.upload_to_youtube", upload)
    monkeypatch.setattr("podcaster.video.distribution.upload_to_spotify_episode", spotify)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"x" * 2048)
    config = VideoDistributionConfig(
        youtube_enabled=True, youtube_required=True, spotify_upload_enabled=True
    )

    def record(provider, receipt):
        published[provider] = receipt
        assert _record_video_publication(storage, JOB, identity, provider, receipt)

    first = distribute_video(
        video,
        JOB,
        "title | W42",
        "desc",
        120,
        config,
        storage=FakeStorage(),
        published=published,
        publish_run_id=identity.publish_run_id,
        on_published=record,
    )
    assert first.youtube_required_failed and first.youtube_failure_retryable
    assert first.spotify_upload_updated and first.blob_path
    failed = latest_outcomes(read_evidence(storage, JOB))["youtube:video"]
    assert failed["retry_blocked"] is True
    assert failed["mutation_attempted"] is False
    assert (
        claim_evidence(
            storage, identity, platform="youtube", media_kind="video", operation="upload_intent"
        )
        is None
    )
    assert upload.call_count == 1
    spotify.assert_not_called()
    assert latest_outcomes(read_evidence(storage, JOB))["spotify:video"] == spotify_receipt


def test_required_youtube_ambiguous_creation_blocks_remutation(accepted, tmp_path, monkeypatch):
    from podcaster.publication_state import claim_evidence, read_evidence, retry_is_blocked
    from podcaster.video.distribution import (
        VideoDistributionConfig,
        YouTubeDeliveryError,
        distribute_video,
    )
    from podcaster.video.job_runner import _record_video_publication
    from tests.test_video_distribution import FakeStorage

    storage, manifest, _ = accepted
    identity = publication_identity(manifest, JOB, "38270494467")
    claim_evidence(
        storage, identity, platform="youtube", media_kind="video", operation="upload_intent"
    )
    upload = MagicMock(
        side_effect=YouTubeDeliveryError(
            "response lost",
            code="youtube_resumable_init_ambiguous",
            stage="resumable_session_init",
            retryable=False,
            mutation_ambiguous=True,
        )
    )
    monkeypatch.setattr("podcaster.video.distribution.upload_to_youtube", upload)
    published = {
        "spotify_upload": {
            "status": "published",
            "outcome": "published",
            "episode_id": 127359743,
        }
    }
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"x" * 2048)
    config = VideoDistributionConfig(
        youtube_enabled=True, youtube_required=True, spotify_upload_enabled=True
    )

    def record(provider, receipt):
        published[provider] = receipt
        assert _record_video_publication(storage, JOB, identity, provider, receipt)

    for _ in range(2):
        result = distribute_video(
            video,
            JOB,
            "title | W42",
            "desc",
            120,
            config,
            storage=FakeStorage(),
            published=published,
            publish_run_id=identity.publish_run_id,
            on_published=record,
        )
        assert result.provider_outcomes["youtube"] == "publication_unknown"
    assert upload.call_count == 1
    assert retry_is_blocked(read_evidence(storage, JOB), platform="youtube", media_kind="video")
    assert (
        claim_evidence(
            storage, identity, platform="youtube", media_kind="video", operation="upload_intent"
        )
        is None
    )


def test_in_flight_upload_intent_is_pending_not_terminal_unknown(accepted):
    from podcaster.publication_state import claim_evidence

    storage, manifest, query = accepted
    identity = publication_identity(manifest, JOB, query["publish_run_id"])
    claim_evidence(
        storage,
        identity,
        platform="youtube",
        media_kind="video",
        operation="upload_intent",
        at=NOW,
    )
    response = publication_status(storage, query, now=NOW)
    assert response["provider"]["state"] == "pending"
    assert response["provider"]["providers"]["youtube"]["code"] == "upload_in_progress"


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "invented"),
        ("status", "gated"),
        ("outcome", "completed"),
        ("verification", "yes"),
        ("seq", True),
        ("at", "yesterday"),
        ("native_state", {"state": "published"}),
        ("provider_id", "999999999"),
    ],
)
def test_malformed_provider_receipts_are_explicit(accepted, field, value):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    path = evidence_path(JOB)
    document = json.loads(storage.data[path])
    document["records"][-1][field] = value
    storage.data[path] = json.dumps(document).encode()
    with pytest.raises(PublicationStatusError) as error:
        publication_status(storage, query, now=NOW, youtube_readback=ready)
    assert error.value.code == "invalid_provider_evidence"


@pytest.mark.parametrize("observability", [None, {}, {"correlation_id": "other"}, "malformed"])
def test_persisted_correlation_is_required_and_exact(accepted, observability):
    storage, manifest, query = accepted
    manifest["observability"] = observability
    save(storage, manifest)
    receipts(storage, manifest)
    with pytest.raises(PublicationStatusError) as error:
        publication_status(storage, query, now=NOW, youtube_readback=ready)
    assert error.value.code == "correlation_mismatch"


def test_out_of_order_provider_receipts_fail_closed(accepted):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    path = evidence_path(JOB)
    document = json.loads(storage.data[path])
    document["records"].reverse()
    storage.data[path] = json.dumps(document).encode()
    with pytest.raises(PublicationStatusError) as error:
        publication_status(storage, query, now=NOW, youtube_readback=ready)
    assert error.value.code == "invalid_provider_evidence"


@pytest.mark.parametrize("field", ["platform", "media_kind", "operation"])
@pytest.mark.parametrize("value", [None, "", 42, [], "bad\nfield"])
def test_malformed_newer_receipt_cannot_hide_behind_older_success(accepted, field, value):
    storage, manifest, query = accepted
    identity = receipts(storage, manifest)
    append_evidence(
        storage,
        identity,
        platform="youtube",
        media_kind="video",
        operation="distribution",
        outcome="publication_unknown",
        native_state="unlisted",
        at=NOW,
    )
    path = evidence_path(JOB)
    document = json.loads(storage.data[path])
    document["records"][-1][field] = value
    storage.data[path] = json.dumps(document).encode()
    with pytest.raises(PublicationStatusError) as error:
        publication_status(storage, query, now=NOW, youtube_readback=ready)
    assert error.value.code == "invalid_provider_evidence"


@pytest.mark.integration
def test_http_api_terminal_status_contract_is_read_only(accepted, monkeypatch):
    from functools import partial
    from http.server import HTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen

    storage, manifest, query = accepted
    manifest["generation"].update(
        synthesis_runner={"status": "completed"}, video_runner={"status": "completed"}
    )
    save(storage, manifest)
    receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    monkeypatch.setattr("podcaster.api.create_storage_backend", lambda: storage)
    monkeypatch.setattr(
        "podcaster.api.publication_status",
        partial(publication_status, now=NOW, youtube_readback=ready),
    )
    generation = MagicMock(side_effect=AssertionError("GET must never generate"))
    monkeypatch.setattr("podcaster.api.run_generation_job", generation)
    server = HTTPServer(("127.0.0.1", 0), GenerateHandler)
    worker = Thread(target=server.serve_forever)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/publication-status?"
        request = Request(
            url + urlencode(query), headers={"x-podcaster-api-key": "test-status-key"}
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == 200
            payload = json.load(response)
        # The exact enums and fields accepted by SquadScope's v1 terminal consumer.
        assert payload["schema_version"] == "podcast_publication_status_v1"
        assert payload["identity"] == {
            field: query[field]
            for field in ("week", "publish_run_id", "article_sha256", "manifest_sha256")
        }
        assert payload["job_id"] == query["job_id"]
        assert payload["correlation_id"] == query["correlation_id"]
        assert payload["synthesis"]["state"] in {"started", "succeeded"}
        assert payload["video"]["state"] == "succeeded"
        assert payload["provider"]["state"] == "published"
        assert payload["provider"]["external_verified"] is True
        assert storage.data == before
        generation.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


@pytest.mark.integration
@pytest.mark.parametrize("authorized", [False, True])
def test_production_fastapi_entrypoint_contract_and_auth(accepted, monkeypatch, authorized):
    from functools import partial

    from fastapi.testclient import TestClient

    from podcaster.monitoring import app

    storage, manifest, query = accepted
    manifest["generation"].update(
        synthesis_runner={"status": "completed"}, video_runner={"status": "completed"}
    )
    save(storage, manifest)
    receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    factory = MagicMock(return_value=storage)
    monkeypatch.setattr("podcaster.monitoring.get_storage", factory)
    monkeypatch.setattr(
        "podcaster.monitoring.publication_status",
        partial(publication_status, now=NOW, youtube_readback=ready),
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/publication-status",
            params=query,
            headers={"x-podcaster-api-key": "test-status-key" if authorized else "wrong"},
        )
    assert response.status_code == (200 if authorized else 401)
    if authorized:
        assert response.headers["Cache-Control"] == "no-store"
        payload = response.json()
        assert payload["schema_version"] == "podcast_publication_status_v1"
        assert payload["job_id"] == payload["correlation_id"] == JOB
        assert payload["provider"]["state"] == "published"
        assert payload["provider"]["external_verified"] is True
        assert payload["video"]["state"] == payload["synthesis"]["state"] == "succeeded"
        assert storage.data == before
    else:
        factory.assert_not_called()
