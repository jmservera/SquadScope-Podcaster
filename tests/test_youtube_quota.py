"""Tests for podcaster.youtube_quota — quota monitoring & rate limiting (#447)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcaster import youtube_quota as q
from podcaster.storage import LocalStorageBackend


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")


# --- quota day / Pacific reset ----------------------------------------------


class TestQuotaDay:
    def test_utc_evening_maps_to_pacific_same_or_prior_day(self):
        # 2026-06-26 06:00 UTC is 2026-06-25 23:00 PDT → prior Pacific date.
        moment = datetime(2026, 6, 26, 6, 0, tzinfo=timezone.utc)
        assert q.quota_day(moment) == "2026-06-25"

    def test_utc_afternoon_is_pacific_same_day(self):
        moment = datetime(2026, 6, 26, 20, 0, tzinfo=timezone.utc)
        assert q.quota_day(moment) == "2026-06-26"

    def test_naive_datetime_treated_as_utc(self):
        moment = datetime(2026, 6, 26, 20, 0)  # naive
        assert q.quota_day(moment) == "2026-06-26"


# --- cost helpers ------------------------------------------------------------


class TestCosts:
    def test_operation_cost(self):
        assert q.operation_cost("upload") == 1600
        assert q.operation_cost("thumbnail_set", 2) == 100

    def test_unknown_operation_raises(self):
        with pytest.raises(ValueError, match="unknown YouTube operation"):
            q.operation_cost("nope")

    def test_negative_count_raises(self):
        with pytest.raises(ValueError):
            q.operation_cost("upload", -1)

    def test_estimate_single_language_full(self):
        # upload + thumbnail + playlist = 1600 + 50 + 50.
        assert q.estimate_episode_units(1) == 1700

    def test_estimate_three_languages_within_daily_quota(self):
        # 3 langs * 1700 = 5100; well under 10k/day.
        units = q.estimate_episode_units(3)
        assert units == 5100
        assert units < q.YOUTUBE_DAILY_QUOTA_UNITS

    def test_estimate_with_retries(self):
        assert q.estimate_episode_units(1, retries=1) == 3400

    def test_estimate_upload_only(self):
        assert q.estimate_episode_units(1, thumbnail=False, playlist=False) == 1600


# --- ledger load -------------------------------------------------------------


class TestLoadLedger:
    def test_initialises_empty(self):
        ledger = q.load_quota_ledger(None, day="2026-06-26")
        assert ledger["consumed_units"] == 0
        assert ledger["operations"] == []
        assert ledger["day"] == "2026-06-26"

    def test_day_mismatch_raises(self):
        content = b'{"day": "2026-06-25", "consumed_units": 0, "operations": []}'
        with pytest.raises(RuntimeError, match="day did not match"):
            q.load_quota_ledger(content, day="2026-06-26")

    def test_invalid_consumed_units_raises(self):
        content = b'{"day": "2026-06-26", "consumed_units": "x", "operations": []}'
        with pytest.raises(RuntimeError, match="consumed_units"):
            q.load_quota_ledger(content, day="2026-06-26")


# --- pre-flight --------------------------------------------------------------


class TestPreflight:
    def test_allows_within_quota(self):
        ledger = q.load_quota_ledger(None, day="d")
        decision = q.quota_preflight(ledger, 1600)
        assert decision["allowed"] is True
        assert decision["reason"] == "within_quota"
        assert decision["projected_units"] == 1600
        assert decision["remaining_units"] == q.YOUTUBE_DAILY_QUOTA_UNITS - q.QUOTA_SAFETY_RESERVE_UNITS - 1600

    def test_blocks_when_would_exceed(self):
        ledger = {"day": "d", "consumed_units": 9000, "operations": []}
        decision = q.quota_preflight(ledger, 1600)
        assert decision["allowed"] is False
        assert decision["reason"] == "would_exceed_quota"

    def test_reports_exhausted(self):
        ledger = {"day": "d", "consumed_units": 9900, "operations": []}
        decision = q.quota_preflight(ledger, 1600)
        assert decision["allowed"] is False
        assert decision["reason"] == "quota_exhausted"

    def test_reserve_is_respected(self):
        # usable = 10000 - 200 = 9800; 9700 consumed + 200 planned = 9900 > 9800.
        ledger = {"day": "d", "consumed_units": 9700, "operations": []}
        decision = q.quota_preflight(ledger, 200)
        assert decision["usable_units"] == 9800
        assert decision["allowed"] is False

    def test_negative_planned_raises(self):
        ledger = q.load_quota_ledger(None, day="d")
        with pytest.raises(ValueError):
            q.quota_preflight(ledger, -1)


# --- record + status ---------------------------------------------------------


class TestRecordAndStatus:
    def test_record_accumulates_and_logs_operation(self):
        ledger = q.load_quota_ledger(None, day="d")
        ledger = q.record_quota_usage(ledger, 1600, op="upload", job_id="j1")
        ledger = q.record_quota_usage(ledger, 50, op="thumbnail_set", job_id="j1")
        assert ledger["consumed_units"] == 1650
        assert len(ledger["operations"]) == 2
        assert ledger["operations"][0]["op"] == "upload"
        assert ledger["operations"][0]["job_id"] == "j1"
        assert ledger["operations"][1]["units"] == 50

    def test_record_does_not_mutate_input(self):
        ledger = q.load_quota_ledger(None, day="d")
        q.record_quota_usage(ledger, 1600, op="upload")
        assert ledger["consumed_units"] == 0  # original untouched

    def test_status_snapshot(self):
        ledger = {"day": "d", "consumed_units": 8600, "operations": [{"op": "upload"}]}
        status = q.quota_status(ledger)
        assert status["remaining_units"] == 1400
        assert status["utilization_pct"] == 86.0
        assert status["near_limit"] is True
        assert status["exhausted"] is False
        assert status["operation_count"] == 1

    def test_status_exhausted(self):
        ledger = {"day": "d", "consumed_units": 10000, "operations": []}
        status = q.quota_status(ledger)
        assert status["exhausted"] is True
        assert status["remaining_units"] == 0


# --- storage-backed reservation ---------------------------------------------


class TestReserveQuota:
    def test_reserve_persists_and_blocks_when_exhausted(self, storage):
        now = datetime(2026, 6, 26, 20, 0, tzinfo=timezone.utc)
        # First five uploads fit (5 * 1600 = 8000 <= 9800 usable).
        for i in range(5):
            decision = q.reserve_quota(storage, 1600, op="upload", job_id=f"j{i}", now=now)
            assert decision["allowed"] is True

        # Sixth (8000 + 1600 = 9600 <= 9800) still fits.
        q.reserve_quota(storage, 1600, op="upload", job_id="j5", now=now)
        # Seventh would be 9600 + 1600 = 11200 > 9800 → blocked.
        with pytest.raises(q.QuotaExceeded) as exc:
            q.reserve_quota(storage, 1600, op="upload", job_id="j6", now=now)
        assert exc.value.decision["allowed"] is False

    def test_blocked_reservation_does_not_consume(self, storage):
        now = datetime(2026, 6, 26, 20, 0, tzinfo=timezone.utc)
        q.reserve_quota(storage, 9600, op="upload", job_id="big", now=now)
        with pytest.raises(q.QuotaExceeded):
            q.reserve_quota(storage, 1600, op="upload", job_id="over", now=now)
        # Status must reflect only the successful 9600, not the blocked 1600.
        status = q.current_quota_status(storage, now=now)
        assert status["consumed_units"] == 9600

    def test_current_status_empty_day(self, storage):
        now = datetime(2026, 6, 26, 20, 0, tzinfo=timezone.utc)
        status = q.current_quota_status(storage, now=now)
        assert status["consumed_units"] == 0
        assert status["day"] == "2026-06-26"

    def test_reservations_isolated_per_day(self, storage):
        day1 = datetime(2026, 6, 26, 20, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)
        q.reserve_quota(storage, 1600, op="upload", now=day1)
        q.reserve_quota(storage, 1600, op="upload", now=day2)
        assert q.current_quota_status(storage, now=day1)["consumed_units"] == 1600
        assert q.current_quota_status(storage, now=day2)["consumed_units"] == 1600
