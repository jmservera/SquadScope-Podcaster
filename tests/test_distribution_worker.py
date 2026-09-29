from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from podcaster.distribution_outbox import DistributionOutboxRepository, commit_immutable_artifact
from podcaster.distribution_worker import process_message, provider_mutation_enabled
from podcaster.publication_state import PublicationIdentity
from podcaster.queue import QueueMessage, encode_distribution_message
from podcaster.storage import LocalStorageBackend


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class MemoryQueue:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.deleted: list[str] = []

    def receive_messages(self, max_messages=1, *, visibility_timeout=600):
        return self.messages[:max_messages]

    def delete_message(self, message):
        self.deleted.append(message.message_id)


def _message(body, *, message_id="m1", pop_receipt=None, dequeue_count=1):
    return QueueMessage(
        message_id=message_id,
        pop_receipt=pop_receipt or f"receipt-{message_id}",
        body=body,
        dequeue_count=dequeue_count,
    )


def _setup(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"safe-video")
    artifact = commit_immutable_artifact(
        storage,
        source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    identity = PublicationIdentity(
        accepted_job_id="podcast-2026-W40-comparative",
        week="2026-W40",
        publish_run_id="run-123",
        article_sha256="a" * 64,
        manifest_sha256="b" * 64,
    )
    clock = Clock()
    repository = DistributionOutboxRepository(storage, now=clock)
    document, _created = repository.enqueue(
        identity,
        artifact,
        provider_objectives={"youtube": "public", "spotify": "public"},
        enqueue_source="video_runner",
        enqueue_version="v1",
    )
    return storage, repository, clock, document


def test_worker_mutation_flag_defaults_disabled():
    assert provider_mutation_enabled({}) is False
    assert (
        provider_mutation_enabled({"DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED": "false"})
        is False
    )
    assert (
        provider_mutation_enabled({"DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED": "true"}) is True
    )


def test_malformed_message_deletes_without_claim_or_dispatch(tmp_path):
    storage, repository, _clock, document = _setup(tmp_path)
    queue = MemoryQueue()
    calls = 0

    def _dispatcher(*_args):
        nonlocal calls
        calls += 1
        return "called"

    outcome = process_message(
        _message(json.dumps({"schema_version": "wrong"})),
        storage=storage,
        queue=queue,
        repository=repository,
        dispatcher=_dispatcher,
    )

    assert outcome.status == "malformed"
    assert queue.deleted == ["m1"]
    assert calls == 0
    assert repository.read(document["outbox_id"])["claim"] is None


def test_worker_claims_heartbeats_releases_and_deletes_with_mutation_disabled(tmp_path):
    storage, repository, _clock, document = _setup(tmp_path)
    queue = MemoryQueue()
    calls = 0

    def _dispatcher(*_args):
        nonlocal calls
        calls += 1
        return "called"

    outcome = process_message(
        _message(encode_distribution_message(document["outbox_id"])),
        storage=storage,
        queue=queue,
        repository=repository,
        owner="worker",
        execution_id="exec-1",
        mutation_enabled=False,
        dispatcher=_dispatcher,
    )

    state = repository.read(document["outbox_id"])
    assert outcome.status == "handled"
    assert outcome.reason == "provider_mutation_disabled"
    assert state["claim"] is None
    assert state["attempt_count"] == 1
    assert queue.deleted == ["m1"]
    assert calls == 0


def test_worker_hashes_opaque_pop_receipts_for_claim_execution_id(tmp_path):
    storage, repository, _clock, document = _setup(tmp_path)
    queue = MemoryQueue()

    outcome = process_message(
        _message(
            encode_distribution_message(document["outbox_id"]),
            pop_receipt="AgAAAAMAAAAAAAAA9FvV1n7Z3AE=",
        ),
        storage=storage,
        queue=queue,
        repository=repository,
        owner="worker",
        mutation_enabled=False,
    )

    state = repository.read(document["outbox_id"])
    event = next(item for item in state["attempts"][0]["events"] if "execution_id" in item)
    assert outcome.status == "handled"
    assert event["execution_id"].startswith("queue-")
    assert "=" not in event["execution_id"]
    assert queue.deleted == ["m1"]


def test_duplicate_delivery_during_active_lease_is_transient(tmp_path):
    storage, repository, _clock, document = _setup(tmp_path)
    queue = MemoryQueue()
    repository.claim(
        document["outbox_id"],
        owner="first",
        execution_id="exec-first",
        lease_seconds=300,
    )

    outcome = process_message(
        _message(encode_distribution_message(document["outbox_id"]), message_id="m2"),
        storage=storage,
        queue=queue,
        repository=repository,
        owner="second",
        execution_id="exec-second",
    )

    assert outcome.status == "transient"
    assert outcome.reason == "active_claim"
    assert queue.deleted == []


def test_missing_outbox_remains_retryable_before_poison_threshold(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    queue = MemoryQueue()
    missing = "f" * 64

    outcome = process_message(
        _message(encode_distribution_message(missing), message_id="missing", dequeue_count=1),
        storage=storage,
        queue=queue,
        max_dequeue_count=5,
    )

    assert outcome.status == "transient"
    assert outcome.reason == "outbox_missing"
    assert queue.deleted == []


def test_exhausted_missing_outbox_hint_is_deleted(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    queue = MemoryQueue()
    missing = "f" * 64

    outcome = process_message(
        _message(encode_distribution_message(missing), message_id="missing", dequeue_count=5),
        storage=storage,
        queue=queue,
        max_dequeue_count=5,
    )

    assert outcome.status == "poisoned"
    assert outcome.reason == "orphan_hint_exhausted"
    assert queue.deleted == ["missing"]


def test_poison_message_marks_outbox_and_deletes(tmp_path):
    storage, repository, _clock, document = _setup(tmp_path)
    queue = MemoryQueue()

    outcome = process_message(
        _message(
            encode_distribution_message(document["outbox_id"]),
            message_id="poison",
            dequeue_count=5,
        ),
        storage=storage,
        queue=queue,
        repository=repository,
        owner="worker",
        execution_id="exec-poison",
        max_dequeue_count=5,
    )

    state = repository.read(document["outbox_id"])
    assert outcome.status == "poisoned"
    assert queue.deleted == ["poison"]
    assert state["claim"] is None
    assert state["aggregate"]["result"] == "poisoned"
    assert {leg["result"] for leg in state["providers"].values()} == {"poisoned"}


def test_poison_message_preserves_ambiguous_provider_truth(tmp_path):
    storage, repository, clock, document = _setup(tmp_path)
    first = repository.claim(
        document["outbox_id"],
        owner="first",
        execution_id="exec-first",
        lease_seconds=300,
    )
    repository.record_verification(
        first,
        provider="youtube",
        result="publication_unknown",
        source="youtube_identity_unprovable",
    )
    repository.release(first)
    clock.advance(301)
    queue = MemoryQueue()

    outcome = process_message(
        _message(
            encode_distribution_message(document["outbox_id"]),
            message_id="poison",
            dequeue_count=5,
        ),
        storage=storage,
        queue=queue,
        repository=repository,
        owner="worker",
        execution_id="exec-poison",
        max_dequeue_count=5,
    )

    state = repository.read(document["outbox_id"])
    assert outcome.status == "poisoned"
    assert queue.deleted == ["poison"]
    assert state["providers"]["youtube"]["result"] == "publication_unknown"
    assert state["providers"]["spotify"]["result"] == "poisoned"
    assert state["weekly_aggregation"]["state"] == "provider_unknown"


def test_enabled_mutation_path_uses_injected_dispatcher(tmp_path):
    storage, repository, _clock, document = _setup(tmp_path)
    queue = MemoryQueue()
    calls = []

    def _dispatcher(_repository, claim, state):
        calls.append((claim.outbox_id, state["outbox_id"]))
        return "injected_dispatch"

    outcome = process_message(
        _message(encode_distribution_message(document["outbox_id"])),
        storage=storage,
        queue=queue,
        repository=repository,
        mutation_enabled=True,
        dispatcher=_dispatcher,
    )

    assert outcome.status == "handled"
    assert outcome.reason == "injected_dispatch"
    assert calls == [(document["outbox_id"], document["outbox_id"])]
    assert queue.deleted == ["m1"]


def test_enabled_dispatch_renews_lease_while_dispatcher_runs(tmp_path):
    storage, repository, clock, document = _setup(tmp_path)
    queue = MemoryQueue()
    heartbeats = []

    def _dispatcher(_repository, _claim, _state):
        time.sleep(0.4)
        return "injected_dispatch"

    original_heartbeat = repository.heartbeat

    def _heartbeat(claim, *, lease_seconds):
        heartbeats.append(clock.value)
        return original_heartbeat(claim, lease_seconds=lease_seconds)

    repository.heartbeat = _heartbeat  # type: ignore[method-assign]
    outcome = process_message(
        _message(encode_distribution_message(document["outbox_id"])),
        storage=storage,
        queue=queue,
        repository=repository,
        lease_seconds=1,
        mutation_enabled=True,
        dispatcher=_dispatcher,
    )

    assert outcome.status == "handled"
    assert outcome.reason == "injected_dispatch"
    assert len(heartbeats) > 1
    assert queue.deleted == ["m1"]
