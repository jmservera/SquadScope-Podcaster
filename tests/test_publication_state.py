from __future__ import annotations

import json
import pickle

import pytest

from podcaster.publication_state import (
    CANONICAL_OUTCOMES,
    DRAFT_CREATED,
    EVIDENCE_SCHEMA_VERSION,
    MANUAL_HANDOFF_REQUIRED,
    MIN_EVIDENCE_RETENTION_DAYS,
    PUBLICATION_UNKNOWN,
    PUBLISHED,
    UPLOADED,
    CreateIntentProvenance,
    CreateSafetyState,
    MutationPossibility,
    ProviderSnapshot,
    PublicationStateError,
    SnapshotCompleteness,
    SnapshotEvidenceSource,
    append_evidence,
    claim_evidence,
    create_safety_state_from_record,
    emit_publication_signal,
    evidence_path,
    latest_outcomes,
    legacy_evidence_path,
    outcome_from_distribution_status,
    outcome_from_publish_status,
    outcome_from_spotify_terminal_state,
    publication_identity,
    read_evidence,
    retry_is_blocked,
    spotify_video_retry_is_blocked,
    validate_outcome,
)


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def get_bytes(self, path):
        return self.data.get(path)

    def update_bytes(self, path, content_type, update):
        value = update(self.data.get(path))
        self.data[path] = value
        return None


def manifest(*, job_id="podcast-2026-W37-abc", dry_run=False):
    return {
        "job_id": job_id,
        "request": {
            "week": "2026-W37",
            "publish_run_id": "1",
            "article_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "dry_run": dry_run,
        },
        "lifecycle": {"transitions": [{"to": "accepted"}]},
    }


def identity(storage=None):
    return publication_identity(manifest(), "podcast-2026-W37-abc", "1")


INVALID_SNAPSHOT_EVIDENCE_SOURCES = (
    "   ",
    "\u200b",
    "\u200c",
    "\u200d",
    "\ufeff",
    "\u2060",
    "\x1f",
)


def test_canonical_outcomes_are_exact_and_distinct():
    assert CANONICAL_OUTCOMES == (
        "uploaded",
        "draft_created",
        "manual_handoff_required",
        "published",
        "publication_unknown",
    )
    assert len(set(CANONICAL_OUTCOMES)) == 5


def test_unknown_outcome_is_rejected():
    with pytest.raises(PublicationStateError):
        validate_outcome("completed")


def test_legacy_publish_status_mapping_is_additive():
    assert outcome_from_publish_status("draft") == DRAFT_CREATED
    assert outcome_from_publish_status("scheduled") == DRAFT_CREATED
    assert outcome_from_publish_status("published") == PUBLICATION_UNKNOWN
    assert outcome_from_publish_status("published", confirmed=True) == PUBLISHED
    assert outcome_from_publish_status("failed") == MANUAL_HANDOFF_REQUIRED


def test_legacy_distribution_status_mapping_is_unchanged():
    for status in ("pending", "completed", "partial", "failed"):
        assert outcome_from_distribution_status(status) is None


def test_legacy_spotify_publication_state_unknown_maps_to_publication_unknown():
    assert outcome_from_spotify_terminal_state("publication_state_unknown") == PUBLICATION_UNKNOWN


def test_legacy_manifest_without_outcome_remains_readable():
    assert latest_outcomes(None) == {}
    assert latest_outcomes({"records": []}) == {}


def test_identity_uses_week_publish_run_article_hash_and_accepted_job_id():
    value = identity()
    assert value.accepted_job_id == "podcast-2026-W37-abc"
    assert value.week == "2026-W37"
    assert value.publish_run_id == "1"
    assert value.article_sha256 == "a" * 64
    assert value.manifest_sha256 == "b" * 64


def test_identity_rejects_manifest_job_id_mismatch():
    with pytest.raises(PublicationStateError):
        publication_identity(manifest(), "other", "1")


def test_identity_rejects_conflicting_publish_run_id():
    with pytest.raises(PublicationStateError, match="conflicts with manifest identity"):
        publication_identity(manifest(), manifest()["job_id"], "999")


def test_identity_rejects_missing_manifest_publish_run_id():
    value = manifest()
    del value["request"]["publish_run_id"]
    with pytest.raises(PublicationStateError, match="publish_run_id is missing"):
        publication_identity(value, value["job_id"], "1")


def test_explicit_legacy_request_cannot_create_canonical_evidence():
    value = manifest()
    value["request"]["publication_identity_mode"] = "legacy"
    with pytest.raises(PublicationStateError, match="legacy requests"):
        publication_identity(value, value["job_id"], "1")


def test_identity_rejects_nonaccepted_or_dry_run_manifest():
    with pytest.raises(PublicationStateError):
        publication_identity(manifest(dry_run=True), "podcast-2026-W37-abc", "1")
    value = manifest()
    value["lifecycle"]["transitions"] = []
    with pytest.raises(PublicationStateError):
        publication_identity(value, value["job_id"], "1")


def test_evidence_uses_durable_prefix_and_migrates_legacy_records_on_append():
    storage = MemoryStorage()
    job_id = manifest()["job_id"]
    legacy_document = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "job_id": job_id,
        "minimum_retention_days": 28,
        "retention_policy": "append_only_no_count_eviction",
        "updated_at": None,
        "records": [],
    }
    storage.data[legacy_evidence_path(job_id)] = json.dumps(legacy_document).encode()

    assert evidence_path(job_id) == f"publication-evidence/{job_id}.json"
    assert read_evidence(storage, job_id) == legacy_document

    append_evidence(
        storage,
        identity(),
        platform="youtube",
        media_kind="video",
        operation="upload",
        outcome=DRAFT_CREATED,
    )
    assert evidence_path(job_id) in storage.data
    assert len(read_evidence(storage, job_id)["records"]) == 1


def test_evidence_append_is_monotonic_and_preserves_prior_records():
    storage = MemoryStorage()
    ident = identity()
    first = append_evidence(
        storage,
        ident,
        platform="spotify",
        media_kind="audio",
        operation="upload",
        outcome=UPLOADED,
    )
    second = append_evidence(
        storage,
        ident,
        platform="spotify",
        media_kind="audio",
        operation="metadata",
        outcome=DRAFT_CREATED,
    )
    assert (first.seq, second.seq) == (1, 2)
    assert [r["outcome"] for r in read_evidence(storage, ident.accepted_job_id)["records"]] == [
        UPLOADED,
        DRAFT_CREATED,
    ]


def test_evidence_duplicate_key_is_a_noop():
    storage = MemoryStorage()
    kwargs = dict(platform="youtube", media_kind="video", operation="upload", outcome=DRAFT_CREATED)
    assert append_evidence(storage, identity(), **kwargs) is not None
    assert append_evidence(storage, identity(), **kwargs) is None
    assert len(read_evidence(storage, identity().accepted_job_id)["records"]) == 1


def test_evidence_retains_more_than_four_weeks_without_eviction():
    storage = MemoryStorage()
    for number in range(120):
        append_evidence(
            storage,
            publication_identity(
                {
                    **manifest(),
                    "request": {
                        **manifest()["request"],
                        "publish_run_id": str(number + 1),
                    },
                },
                manifest()["job_id"],
                str(number + 1),
            ),
            platform="youtube",
            media_kind="video",
            operation="upload",
            outcome=DRAFT_CREATED,
        )
    document = read_evidence(storage, manifest()["job_id"])
    records = document["records"]
    assert MIN_EVIDENCE_RETENTION_DAYS >= 28
    assert len(records) == 120
    assert records[0]["seq"] == 1
    assert records[-1]["seq"] == 120
    assert document["minimum_retention_days"] == 28
    assert document["retention_policy"] == "append_only_no_count_eviction"
    assert evidence_path(manifest()["job_id"]) == ("publication-evidence/podcast-2026-W37-abc.json")


def test_read_evidence_accepts_legacy_job_scoped_path():
    storage = MemoryStorage()
    job_id = manifest()["job_id"]
    storage.data[f"jobs/{job_id}/publication-evidence.json"] = json.dumps(
        {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "job_id": job_id,
            "records": [],
        }
    ).encode()

    assert read_evidence(storage, job_id)["job_id"] == job_id


def test_duplicate_legacy_record_migrates_without_writing_empty_canonical_blob():
    storage = MemoryStorage()
    ident = identity()
    legacy_document = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "job_id": ident.accepted_job_id,
        "records": [
            {
                "seq": 1,
                "at": "2026-09-15T00:00:00Z",
                "week": ident.week,
                "publish_run_id": ident.publish_run_id,
                "article_sha256": ident.article_sha256,
                "manifest_sha256": ident.manifest_sha256,
                "job_id": ident.accepted_job_id,
                "platform": "youtube",
                "media_kind": "video",
                "operation": "upload",
                "outcome": DRAFT_CREATED,
                "status": "unlisted",
                "transport_status": "accepted",
                "verification": "none",
                "retry_blocked": True,
            }
        ],
    }
    storage.data[f"jobs/{ident.accepted_job_id}/publication-evidence.json"] = json.dumps(
        legacy_document
    ).encode()

    assert (
        append_evidence(
            storage,
            ident,
            platform="youtube",
            media_kind="video",
            operation="upload",
            outcome=DRAFT_CREATED,
        )
        is None
    )
    canonical = json.loads(storage.data[evidence_path(ident.accepted_job_id)])
    assert canonical["records"] == legacy_document["records"]


def test_provider_record_distinguishes_readback_from_external_visibility():
    storage = MemoryStorage()
    record = append_evidence(
        storage,
        identity(),
        platform="youtube",
        media_kind="video",
        operation="publish_readback",
        outcome=PUBLISHED,
        provider_artifact_id="video-1",
        confirmation_source="youtube_api",
        native_state="public",
        transport_status="accepted",
        retry_blocked=True,
    )
    assert record.status == "pending"
    assert record.verification == "provider_readback"
    assert record.provider_id == "video-1"
    assert record.evidence_source == "youtube_api"
    assert record.checked_at == record.at
    assert record.last_error_code is None

    externally_verified = append_evidence(
        storage,
        identity(),
        platform="youtube",
        media_kind="video",
        operation="anonymous_verification",
        outcome=PUBLISHED,
        provider_artifact_id="video-1",
        verification="external_verified",
        native_state="public",
        transport_status="verified",
        evidence_source="anonymous_watch_page",
        retry_blocked=True,
    )
    assert externally_verified.status == "public"
    assert externally_verified.verification == "external_verified"


def test_corrupt_evidence_fails_closed_before_mutation():
    storage = MemoryStorage()
    storage.data[evidence_path(manifest()["job_id"])] = b"{broken"
    with pytest.raises(PublicationStateError):
        append_evidence(
            storage,
            identity(),
            platform="spotify",
            media_kind="audio",
            operation="create",
            outcome=PUBLICATION_UNKNOWN,
        )


def test_evidence_never_contains_article_content_credentials_or_signed_urls():
    storage = MemoryStorage()
    append_evidence(
        storage,
        identity(),
        platform="spotify",
        media_kind="audio",
        operation="create",
        outcome=PUBLICATION_UNKNOWN,
        details={
            "article_content": "secret article",
            "authorization": "Bearer secret",
            "signed_url": "https://example.test/?sig=secret",
            "reason": "transport_lost",
        },
    )
    raw = storage.data[evidence_path(manifest()["job_id"])]
    assert b"secret article" not in raw
    assert b"Bearer secret" not in raw
    assert b"example.test" not in raw
    assert b"transport_lost" in raw


def test_evidence_schema_and_latest_projection():
    storage = MemoryStorage()
    append_evidence(
        storage,
        identity(),
        platform="youtube",
        media_kind="video",
        operation="upload",
        outcome=DRAFT_CREATED,
    )
    document = read_evidence(storage, manifest()["job_id"])
    assert document["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert latest_outcomes(document)["youtube:video"]["outcome"] == DRAFT_CREATED


def test_create_episode_claim_rearms_only_after_retryable_failure():
    storage = MemoryStorage()
    first = claim_evidence(
        storage,
        identity(),
        platform="spotify",
        media_kind="video",
        operation="create_episode_intent",
    )
    duplicate = claim_evidence(
        storage,
        identity(),
        platform="spotify",
        media_kind="video",
        operation="create_episode_intent",
    )

    append_evidence(
        storage,
        identity(),
        platform="spotify",
        media_kind="video",
        operation="credential_failure",
        outcome=PUBLICATION_UNKNOWN,
        mutation_attempted=False,
        retry_blocked=False,
        code="create_outcome_unknown",
    )
    rearmed = claim_evidence(
        storage,
        identity(),
        platform="spotify",
        media_kind="video",
        operation="create_episode_intent",
    )
    second_duplicate = claim_evidence(
        storage,
        identity(),
        platform="spotify",
        media_kind="video",
        operation="create_episode_intent",
    )

    assert first is not None
    assert duplicate is None
    assert rearmed is not None
    assert second_duplicate is None
    records = read_evidence(storage, identity().accepted_job_id)["records"]
    assert [record["operation"] for record in records] == [
        "create_episode_intent",
        "credential_failure",
        "create_episode_intent",
    ]
    assert records[1]["retry_blocked"] is False


def test_publication_signal_dedupe_is_atomic():
    storage = MemoryStorage()
    assert (
        emit_publication_signal(
            storage,
            identity(),
            platform="spotify",
            media_kind="audio",
            outcome=PUBLICATION_UNKNOWN,
        )
        is not None
    )
    assert (
        emit_publication_signal(
            storage,
            identity(),
            platform="spotify",
            media_kind="audio",
            outcome=PUBLICATION_UNKNOWN,
        )
        is None
    )


def test_changed_outcome_emits_a_new_signal():
    storage = MemoryStorage()
    emit_publication_signal(
        storage,
        identity(),
        platform="spotify",
        media_kind="audio",
        outcome=UPLOADED,
    )
    emit_publication_signal(
        storage,
        identity(),
        platform="spotify",
        media_kind="audio",
        outcome=DRAFT_CREATED,
    )
    records = json.loads(storage.data[f"jobs/{manifest()['job_id']}/logs.json"])["records"]
    assert [record["context"]["publication_outcome"] for record in records] == [
        UPLOADED,
        DRAFT_CREATED,
    ]


def test_uploaded_evidence_blocks_blind_retry():
    storage = MemoryStorage()
    append_evidence(
        storage,
        identity(),
        platform="spotify",
        media_kind="audio",
        operation="process_upload",
        outcome=UPLOADED,
        mutation_attempted=True,
        retry_blocked=True,
    )
    assert retry_is_blocked(
        read_evidence(storage, identity().accepted_job_id),
        platform="spotify",
        media_kind="audio",
    )


def test_direct_spotify_video_create_intent_blocks_retry_without_reconciliation_snapshot():
    direct_publish_intent = {
        "platform": "spotify",
        "media_kind": "video",
        "operation": "create_episode_intent",
        "outcome": PUBLICATION_UNKNOWN,
        "mutation_attempted": False,
        "retry_blocked": True,
        "code": "mutation_intent",
        "details": {"show_id": "show1"},
    }

    assert "pre_create_episode_ids" not in direct_publish_intent["details"]
    assert spotify_video_retry_is_blocked({"records": [direct_publish_intent]})


def test_reconciliation_spotify_video_create_intent_snapshot_does_not_block_retry():
    safety = CreateSafetyState.reconciliation_backed(
        ProviderSnapshot.complete(
            [555], evidence_source=SnapshotEvidenceSource.SPOTIFY_EPISODE_LISTING
        )
    )
    reconciliation_intent = {
        "platform": "spotify",
        "media_kind": "video",
        "operation": "create_episode_intent",
        "outcome": PUBLICATION_UNKNOWN,
        "mutation_attempted": False,
        "retry_blocked": False,
        "code": "mutation_intent",
        "details": {
            "show_id": "show1",
            **safety.to_details(),
        },
    }

    assert isinstance(reconciliation_intent["details"]["pre_create_episode_ids"], list)
    assert reconciliation_intent["details"]["snapshot_completeness"] == "complete"
    assert spotify_video_retry_is_blocked({"records": [reconciliation_intent]}) is False


def test_pre_create_episode_ids_are_not_truncated_when_snapshot_is_complete():
    storage = MemoryStorage()
    pre_create_ids = list(range(150))
    safety = CreateSafetyState.reconciliation_backed(
        ProviderSnapshot.complete(
            pre_create_ids, evidence_source=SnapshotEvidenceSource.SPOTIFY_EPISODE_LISTING
        )
    )

    append_evidence(
        storage,
        identity(),
        platform="spotify",
        media_kind="video",
        operation="create_episode_intent",
        outcome=PUBLICATION_UNKNOWN,
        mutation_attempted=False,
        retry_blocked=False,
        code="mutation_intent",
        create_safety_state=safety,
    )

    records = read_evidence(storage, identity().accepted_job_id)["records"]
    details = records[0]["details"]
    assert details["snapshot_completeness"] == "complete"
    assert details["pre_create_episode_ids"] == pre_create_ids


@pytest.mark.parametrize("source", ("", *INVALID_SNAPSHOT_EVIDENCE_SOURCES))
def test_complete_provider_snapshot_rejects_non_enum_evidence_source(source):
    with pytest.raises(PublicationStateError):
        ProviderSnapshot.complete([1], evidence_source=source)


@pytest.mark.parametrize("source", INVALID_SNAPSHOT_EVIDENCE_SOURCES)
def test_complete_provider_snapshot_rejects_str_subclass_evidence_source(source):
    class EvidenceSource(str):
        pass

    with pytest.raises(PublicationStateError):
        ProviderSnapshot.complete([1], evidence_source=EvidenceSource(source))


def test_complete_provider_snapshot_rejects_truthy_non_string_evidence_source():
    class TruthySource:
        def __bool__(self):
            return True

    for source in (123, TruthySource()):
        with pytest.raises(PublicationStateError):
            ProviderSnapshot((1,), SnapshotCompleteness.COMPLETE, source)


def test_provider_snapshot_subclasses_cannot_widen_evidence_source_behavior():
    with pytest.raises(TypeError, match="ProviderSnapshot cannot be subclassed"):

        class SubSnapshot(ProviderSnapshot):
            pass


def test_complete_snapshot_deserialization_without_evidence_fails_closed_to_absent():
    state = create_safety_state_from_record(
        {
            "platform": "spotify",
            "media_kind": "video",
            "operation": "create_episode_intent",
            "outcome": PUBLICATION_UNKNOWN,
            "mutation_attempted": False,
            "retry_blocked": False,
            "code": "mutation_intent",
            "details": {
                "create_provenance": "reconciliation_backed",
                "mutation_possibility": "not_possible",
                "snapshot_completeness": "complete",
                "pre_create_episode_ids": [111222],
            },
        }
    )

    assert state is not None
    assert state.snapshot.completeness == SnapshotCompleteness.ABSENT
    assert state.snapshot.to_details() == {"snapshot_completeness": "absent"}


@pytest.mark.parametrize(
    "source", (None, "", *INVALID_SNAPSHOT_EVIDENCE_SOURCES, 123, "test_listing")
)
def test_explicit_observed_snapshot_deserialization_invalid_source_fails_closed_to_absent(source):
    state = create_safety_state_from_record(
        {
            "platform": "spotify",
            "media_kind": "video",
            "operation": "create_episode_intent",
            "outcome": PUBLICATION_UNKNOWN,
            "mutation_attempted": False,
            "retry_blocked": False,
            "code": "mutation_intent",
            "details": {
                "create_provenance": "reconciliation_backed",
                "mutation_possibility": "not_possible",
                "snapshot_completeness": "truncated",
                "snapshot_evidence_source": source,
                "pre_create_episode_ids": [111222],
            },
        }
    )

    assert state is not None
    assert state.snapshot.completeness == SnapshotCompleteness.ABSENT
    assert state.snapshot.to_details() == {"snapshot_completeness": "absent"}


def test_legacy_snapshot_parser_remains_explicitly_bounded():
    state = create_safety_state_from_record(
        {
            "platform": "spotify",
            "media_kind": "video",
            "operation": "create_episode_intent",
            "outcome": PUBLICATION_UNKNOWN,
            "mutation_attempted": False,
            "retry_blocked": False,
            "code": "mutation_intent",
            "details": {
                "pre_create_episode_ids": [111222],
                "pre_create_snapshot_complete": True,
            },
        }
    )

    assert state is not None
    assert state.snapshot.completeness == SnapshotCompleteness.COMPLETE
    assert (
        state.snapshot.require_evidence_source()
        == SnapshotEvidenceSource.LEGACY_PRE_CREATE_SNAPSHOT
    )


def test_complete_provider_snapshot_tampered_evidence_source_fails_closed():
    snapshot = ProviderSnapshot.complete(
        [1, 2, 3],
        evidence_source=SnapshotEvidenceSource.SPOTIFY_EPISODE_LISTING,
    )

    object.__setattr__(snapshot, "evidence_source", None)

    with pytest.raises(PublicationStateError, match="invalid stored evidence source"):
        snapshot.to_details()


@pytest.mark.parametrize("source", INVALID_SNAPSHOT_EVIDENCE_SOURCES)
def test_complete_provider_snapshot_evidence_source_mutation_rejects_without_writing(source):
    storage = MemoryStorage()
    snapshot = ProviderSnapshot.complete(
        [1],
        evidence_source=SnapshotEvidenceSource.SPOTIFY_EPISODE_LISTING,
    )

    object.__setattr__(snapshot, "evidence_source", source)

    with pytest.raises(PublicationStateError, match="invalid stored evidence source"):
        append_evidence(
            storage,
            identity(),
            platform="spotify",
            media_kind="video",
            operation="create_episode_intent",
            outcome=PUBLICATION_UNKNOWN,
            mutation_attempted=False,
            retry_blocked=False,
            code="mutation_intent",
            create_safety_state=CreateSafetyState.reconciliation_backed(snapshot),
        )

    assert storage.data == {}
    assert read_evidence(storage, identity().accepted_job_id) is None


def test_complete_provider_snapshot_rejects_format_only_evidence_source():
    with pytest.raises(PublicationStateError, match="requires an evidence source"):
        ProviderSnapshot.complete([1], evidence_source="\u200b")


@pytest.mark.parametrize("source", ["\u200b", None, 123])
def test_append_evidence_rejects_invalid_raw_observed_snapshot_without_writing(source):
    storage = MemoryStorage()

    with pytest.raises(PublicationStateError, match="through create_safety_state"):
        append_evidence(
            storage,
            identity(),
            platform="spotify",
            media_kind="video",
            operation="create_episode_intent",
            outcome=PUBLICATION_UNKNOWN,
            details={
                "snapshot_completeness": "complete",
                "snapshot_evidence_source": source,
                "pre_create_episode_ids": [1],
            },
        )

    assert storage.data == {}
    assert read_evidence(storage, identity().accepted_job_id) is None


def test_complete_provider_snapshot_pickle_round_trip_keeps_closed_source():
    snapshot = ProviderSnapshot.complete(
        [1],
        evidence_source=SnapshotEvidenceSource.SPOTIFY_EPISODE_LISTING,
    )

    restored = pickle.loads(pickle.dumps(snapshot))

    assert restored.to_details() == {
        "snapshot_completeness": "complete",
        "pre_create_episode_ids": [1],
        "snapshot_evidence_source": "spotify_episode_listing",
    }


@pytest.mark.parametrize("source", INVALID_SNAPSHOT_EVIDENCE_SOURCES)
def test_complete_provider_snapshot_pickle_tamper_fails_closed(source):
    snapshot = ProviderSnapshot.complete(
        [1],
        evidence_source=SnapshotEvidenceSource.SPOTIFY_EPISODE_LISTING,
    )
    object.__setattr__(snapshot, "evidence_source", source)

    restored = pickle.loads(pickle.dumps(snapshot))

    with pytest.raises(PublicationStateError, match="invalid stored evidence source"):
        restored.to_details()


@pytest.mark.parametrize("source", INVALID_SNAPSHOT_EVIDENCE_SOURCES)
def test_durable_serialization_rejects_bypassed_snapshot_evidence_source(source):
    storage = MemoryStorage()
    snapshot = ProviderSnapshot.complete(
        [1],
        evidence_source=SnapshotEvidenceSource.SPOTIFY_EPISODE_LISTING,
    )
    object.__setattr__(snapshot, "evidence_source", source)
    state = object.__new__(CreateSafetyState)
    object.__setattr__(state, "provenance", CreateIntentProvenance.RECONCILIATION_BACKED)
    object.__setattr__(state, "snapshot", snapshot)
    object.__setattr__(state, "mutation_possibility", MutationPossibility.NOT_POSSIBLE)

    with pytest.raises(PublicationStateError, match="invalid stored evidence source"):
        append_evidence(
            storage,
            identity(),
            platform="spotify",
            media_kind="video",
            operation="create_episode_intent",
            outcome=PUBLICATION_UNKNOWN,
            mutation_attempted=False,
            retry_blocked=False,
            code="mutation_intent",
            create_safety_state=state,
        )

    assert storage.data == {}
    assert read_evidence(storage, identity().accepted_job_id) is None


def test_absent_provider_snapshot_cannot_carry_episode_ids():
    with pytest.raises(PublicationStateError):
        ProviderSnapshot((1,), SnapshotCompleteness.ABSENT)


def test_create_safety_state_models_upload_dispatch_without_snapshot():
    state = create_safety_state_from_record(
        {
            "platform": "spotify",
            "media_kind": "video",
            "operation": "upload_intent",
            "outcome": PUBLICATION_UNKNOWN,
            "retry_blocked": True,
            "details": {},
        }
    )

    assert state is not None
    assert state.provenance == CreateIntentProvenance.UPLOAD_DISPATCH
    assert state.snapshot.completeness == SnapshotCompleteness.ABSENT
    assert state.mutation_possibility == MutationPossibility.POSSIBLE
