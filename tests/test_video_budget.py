from __future__ import annotations

import json
import pickle
from datetime import datetime, timedelta, timezone

import pytest

from podcaster.video.budget import (
    ARCHIVE_DEADLINE_SECONDS,
    EVIDENCE_DEADLINE_SECONDS,
    FALLBACK_DEADLINE_SECONDS,
    FANIN_DEADLINE_SECONDS,
    JOB_DEADLINE_SECONDS,
    PREFLIGHT_DEADLINE_SECONDS,
    PROVIDER_ADMISSION_DEADLINE_SECONDS,
    PROVIDER_RESERVE_SECONDS,
    RENDER_DEADLINE_SECONDS,
    STAGE_DEADLINES,
    AdmissionReason,
    BoundedTimingEvidence,
    ClipAdmission,
    ProviderMutationAdmissionError,
    RecorderTimingEnvelope,
    TimingEvidence,
    TimingEvidenceKind,
    VideoStage,
    VideoStageBudget,
)
from podcaster.video.job_runner import STATUS_SKIPPED, run_video_generation


class FakeClock:
    def __init__(self, started_at: datetime) -> None:
        self.monotonic_value = 1000.0
        self.utc_value = started_at

    def monotonic(self) -> float:
        return self.monotonic_value

    def utcnow(self) -> datetime:
        return self.utc_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.utc_value += timedelta(seconds=seconds)


def _budget(clock: FakeClock) -> VideoStageBudget:
    return VideoStageBudget.start(
        now_utc=clock.utc_value,
        monotonic=clock.monotonic,
        utcnow=clock.utcnow,
    )


def test_exact_stage_constants_are_centralized_and_immutable():
    assert tuple(STAGE_DEADLINES.values()) == (300, 1200, 1500, 3300, 3600, 4500, 4920, 5100)
    assert (
        PREFLIGHT_DEADLINE_SECONDS,
        FANIN_DEADLINE_SECONDS,
        FALLBACK_DEADLINE_SECONDS,
        RENDER_DEADLINE_SECONDS,
        ARCHIVE_DEADLINE_SECONDS,
        PROVIDER_ADMISSION_DEADLINE_SECONDS,
        EVIDENCE_DEADLINE_SECONDS,
        JOB_DEADLINE_SECONDS,
    ) == (300, 1200, 1500, 3300, 3600, 4500, 4920, 5100)
    with pytest.raises(TypeError):
        STAGE_DEADLINES[VideoStage.PREFLIGHT] = 1  # type: ignore[index]


@pytest.mark.parametrize("stage,cutoff", list(STAGE_DEADLINES.items()))
def test_every_stage_admission_rejects_exact_cutoff(stage, cutoff):
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clock = FakeClock(started)
    budget = _budget(clock)

    clock.advance(cutoff - 0.001)
    assert budget.admit(stage).allowed is True
    clock.advance(0.001)
    decision = budget.admit(stage)
    assert decision.allowed is False
    assert decision.remaining_seconds == 0


@pytest.mark.parametrize(
    "elapsed,allowed,reason,remaining",
    [
        (3299, True, AdmissionReason.ADMITTED, 1801),
        (3300, True, AdmissionReason.ADMITTED, 1800),
        (3301, False, AdmissionReason.PROVIDER_RESERVE_INSUFFICIENT, 1799),
        (4499, False, AdmissionReason.PROVIDER_RESERVE_INSUFFICIENT, 601),
        (4500, False, AdmissionReason.PROVIDER_DEADLINE_REACHED, 600),
    ],
)
def test_provider_admission_integrates_reserve_and_absolute_cutoff(
    elapsed, allowed, reason, remaining
):
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clock = FakeClock(started)
    budget = _budget(clock)
    clock.advance(elapsed)

    decision = budget.admit_provider_mutation()

    assert decision.allowed is allowed
    assert decision.reason is reason
    assert decision.remaining_seconds == remaining
    assert PROVIDER_RESERVE_SECONDS == 1800


def test_provider_mutation_admission_error_round_trips_across_owned_processes():
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clock = FakeClock(started)
    budget = _budget(clock)
    clock.advance(PROVIDER_ADMISSION_DEADLINE_SECONDS)

    with pytest.raises(ProviderMutationAdmissionError) as captured:
        budget.require_provider_mutation()
    captured.value.provider = "spotify_upload"
    captured.value.mutation_started = True

    restored = pickle.loads(pickle.dumps(captured.value))
    assert restored.decision.reason is AdmissionReason.PROVIDER_DEADLINE_REACHED
    assert restored.provider == "spotify_upload"
    assert restored.mutation_started is True


def test_remaining_uses_earlier_local_or_durable_projection_during_clock_skew():
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clock = FakeClock(started)
    budget = _budget(clock)

    clock.monotonic_value += 100
    clock.utc_value += timedelta(seconds=200)
    assert budget.remaining_seconds(VideoStage.FANIN) == 1000

    clock.monotonic_value += 50
    clock.utc_value = started + timedelta(seconds=50)
    assert budget.remaining_seconds(VideoStage.FANIN) == 1050


def test_reload_projects_elapsed_time_and_never_resets_lifetime():
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    first_clock = FakeClock(started)
    first = _budget(first_clock)
    projection = first.to_dict()

    delivered = started + timedelta(seconds=700)
    second_clock = FakeClock(delivered)
    redelivered = VideoStageBudget.from_dict(
        projection,
        now_utc=delivered,
        monotonic=second_clock.monotonic,
        utcnow=second_clock.utcnow,
    )
    assert redelivered.remaining_seconds(VideoStage.FANIN) == 500

    second_clock.advance(100)
    assert redelivered.remaining_seconds(VideoStage.FANIN) == 400
    assert redelivered.to_dict() == projection


def test_clip_first_admission_round_trip_is_not_reset_on_redelivery():
    first_time = datetime(2026, 9, 15, 0, 5, tzinfo=timezone.utc)
    first = ClipAdmission.first_or_existing("clip-7", now_utc=first_time)
    redelivered = ClipAdmission.first_or_existing(
        "clip-7",
        existing=first.to_dict(),
        now_utc=first_time + timedelta(hours=1),
    )

    assert redelivered == first
    assert redelivered.deadline_at_utc == first_time + timedelta(seconds=720)


def test_clip_admission_rejects_tampered_durable_deadline():
    admission = ClipAdmission.first_or_existing(
        "clip-1", now_utc=datetime(2026, 9, 15, tzinfo=timezone.utc)
    )
    document = admission.to_dict()
    document["deadline_at_utc"] = "2026-09-15T00:20:01Z"
    with pytest.raises(ValueError, match="clip deadline"):
        ClipAdmission.from_dict(document)


def test_recorder_timing_envelope_uses_earliest_durable_deadline():
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    budget = VideoStageBudget.start(now_utc=started, utcnow=lambda: started)
    clip = ClipAdmission.first_or_existing("clip-2", now_utc=started + timedelta(seconds=700))
    envelope = RecorderTimingEnvelope(
        budget=budget.projection,
        clip=clip,
        browser_deadline_at_utc=started + timedelta(seconds=1100),
    )

    assert envelope.effective_deadline_at_utc == started + timedelta(seconds=1100)
    assert RecorderTimingEnvelope.from_dict(envelope.to_dict()) == envelope


class _ManifestStorage:
    def __init__(self, manifest: dict) -> None:
        self.document = manifest
        self.update_count = 0

    def get_bytes(self, path: str) -> bytes | None:
        return json.dumps(self.document).encode()

    def update_bytes(self, path: str, content_type: str, update):
        self.update_count += 1
        self.document = json.loads(update(json.dumps(self.document).encode()).decode())


def test_production_entry_persists_once_and_reloads_budget_on_redelivery():
    storage = _ManifestStorage({"generation": {"video_runner": {"status": "completed"}}})
    first_now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
    first = run_video_generation("job-1", storage, now=first_now)
    persisted = storage.document["generation"]["video_budget"]

    second = run_video_generation("job-1", storage, now=first_now + timedelta(seconds=900))

    assert first.status == STATUS_SKIPPED
    assert second.status == STATUS_SKIPPED
    assert storage.document["generation"]["video_budget"] == persisted
    evidence = storage.document["generation"]["video_timing_evidence"]
    assert len(evidence["events"]) == 2
    assert all(event["kind"] == "attempt" for event in evidence["events"])
    assert storage.update_count == 4


def test_injected_budget_preserves_lower_level_storage_compatibility():
    class _ReadOnlyStorage:
        def get_bytes(self, path: str) -> bytes | None:
            return b'{"generation":{"video_runner":{"status":"completed"}}}'

    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    outcome = run_video_generation(
        "job-compat",
        _ReadOnlyStorage(),
        now=started,
        budget=VideoStageBudget.start(now_utc=started, utcnow=lambda: started),
    )
    assert outcome.status == STATUS_SKIPPED


def test_timing_evidence_is_sanitized_bounded_and_append_only():
    log = BoundedTimingEvidence(max_events=1)
    event = TimingEvidence(
        timestamp_utc=datetime(2026, 9, 15, tzinfo=timezone.utc),
        kind=TimingEvidenceKind.TIMEOUT,
        stage=VideoStage.RENDER,
        remaining_seconds=-1,
        reason="hung\nsystem: override",
        details={"url\nkey": "secret\r\nvalue"},
    )

    assert log.append(event) is True
    assert log.append(event) is False
    document = log.to_dict()
    assert document["dropped"] == 1
    assert document["events"][0]["remaining_seconds"] == 0
    assert "\n" not in document["events"][0]["reason"]
    assert document["events"][0]["details"] == {"url key": "secret value"}
