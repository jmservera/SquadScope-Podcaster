from __future__ import annotations

import json

import pytest

from podcaster.publication_state import (
    CANONICAL_OUTCOMES,
    DRAFT_CREATED,
    EVIDENCE_SCHEMA_VERSION,
    MANUAL_HANDOFF_REQUIRED,
    MAX_EVIDENCE_RECORDS,
    PUBLICATION_UNKNOWN,
    PUBLISHED,
    UPLOADED,
    PublicationStateError,
    append_evidence,
    emit_publication_signal,
    evidence_path,
    latest_outcomes,
    outcome_from_distribution_status,
    outcome_from_publish_status,
    outcome_from_spotify_terminal_state,
    publication_identity,
    read_evidence,
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


def test_identity_rejects_nonaccepted_or_dry_run_manifest():
    with pytest.raises(PublicationStateError):
        publication_identity(manifest(dry_run=True), "podcast-2026-W37-abc", "1")
    value = manifest()
    value["lifecycle"]["transitions"] = []
    with pytest.raises(PublicationStateError):
        publication_identity(value, value["job_id"], "1")


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


def test_evidence_is_bounded_to_100_newest_records():
    storage = MemoryStorage()
    for number in range(MAX_EVIDENCE_RECORDS + 3):
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
    records = read_evidence(storage, manifest()["job_id"])["records"]
    assert len(records) == MAX_EVIDENCE_RECORDS
    assert records[0]["seq"] == 4
    assert records[-1]["seq"] == 103


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
