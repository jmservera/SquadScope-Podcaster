from datetime import datetime, timezone

from podcaster.distribution_telemetry import (
    PROVIDER_STATE_EVENT,
    WEEKLY_ALERT_METRICS,
    signal_rows,
    signals_for_outbox,
)


def _document(state, native_state=None):
    return {
        "outbox_id": "a" * 64,
        "publication_identity": {"accepted_job_id": "secret-job-id"},
        "artifact": {"media_kind": "video"},
        "enqueue": {"at": "2026-09-21T19:00:00Z"},
        "providers": {
            "youtube": {
                "result": state,
                "intent": {"consumed_at": "2026-09-21T19:30:00Z"},
                "verification": (
                    {"native_state": native_state} if native_state is not None else None
                ),
            }
        },
    }


def test_pending_unknown_and_verification_lag_fire_at_reviewed_thresholds():
    now = datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc)
    signals = signals_for_outbox(_document("publication_unknown"), now=now)
    by_name = {signal.name: signal for signal in signals}
    assert by_name["distribution_pending_age_seconds"].severity == "critical"
    assert by_name["distribution_provider_unknown"].value == 1
    assert by_name["distribution_public_verification_lag_seconds"].severity == "critical"


def test_youtube_non_public_and_spotify_draft_are_provider_specific():
    now = datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc)
    youtube = signals_for_outbox(_document("pending_provider", "unlisted"), now=now)
    assert any(signal.name == "distribution_youtube_non_public" for signal in youtube)
    spotify = _document("manual_handoff_required", "draft")
    spotify["providers"] = {"spotify": spotify["providers"]["youtube"]}
    names = {signal.name for signal in signals_for_outbox(spotify, now=now)}
    assert "distribution_spotify_draft" in names
    assert "distribution_manual_handoff" in names


def test_public_readback_clears_actionable_signals_and_rows_exclude_identity():
    document = _document("externally_verified_public", "public")
    rows = list(signal_rows([document], now=datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc)))
    assert rows == []
    assert "secret-job-id" not in str(rows)


def test_expired_claim_and_poison_emit_critical_signals():
    document = _document("poisoned")
    document["claim"] = {
        "claimed_at": "2026-09-21T19:10:00Z",
        "lease_expires_at": "2026-09-21T19:20:00Z",
    }
    signals = signals_for_outbox(
        document,
        now=datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc),
    )
    names = {signal.name for signal in signals if signal.severity == "critical"}
    assert "distribution_lease_loss" in names
    assert "distribution_poisoned" in names


def test_identity_conflict_and_non_green_weekly_state_are_alertable():
    document = _document("identity_conflict")
    document["weekly_aggregation"] = {"state": "identity_conflict"}
    names = {
        signal.name
        for signal in signals_for_outbox(
            document,
            now=datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc),
        )
    }
    assert "distribution_identity_conflict" in names
    assert "distribution_weekly_non_green" in names
    rows = list(
        signal_rows(
            [document],
            now=datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc),
        )
    )
    weekly_rows = [row for row in rows if row["metric"] in WEEKLY_ALERT_METRICS]
    assert weekly_rows
    assert {row["event"] for row in weekly_rows} == {PROVIDER_STATE_EVENT}
