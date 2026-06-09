from __future__ import annotations

from decimal import Decimal

from podcaster.costs import build_cost_ledger, cost_gate_blockers, evaluate_monthly_guardrail, missing_cost_ledger_fields


def test_cost_ledger_records_required_episode_budget_and_privacy_fields() -> None:
    ledger = build_cost_ledger(
        week="2026-W23",
        month="2026-06",
        provider="not_selected",
        voice="not_selected",
        voice_config_hash="a" * 64,
        billable_characters=1200,
        duration_seconds=0,
        audio_byte_length=256,
        staged_byte_length=4096,
    )

    assert ledger["week"] == "2026-W23"
    assert ledger["month"] == "2026-06"
    assert ledger["provider"] == "not_selected"
    assert ledger["voice"] == "not_selected"
    assert ledger["voice_config_hash"] == "a" * 64
    assert ledger["billable_characters"] == 1200
    assert ledger["duration_seconds"] == 0
    assert ledger["audio_byte_length"] == 256
    assert ledger["staged_byte_length"] == 4096
    assert set(ledger["costs"]) == {
        "script_generation",
        "validation",
        "tts",
        "staging_storage",
        "egress_download",
        "platform_provider",
    }
    assert ledger["budget"]["status"] == "within_budget"
    assert ledger["privacy"] == {
        "secrets_recorded": False,
        "provider_credentials_recorded": False,
        "full_prompts_recorded": False,
    }
    assert missing_cost_ledger_fields(ledger) == []
    assert cost_gate_blockers(ledger) == []


def test_monthly_guardrail_blocks_over_episode_or_spend_limit() -> None:
    episode_limit = evaluate_monthly_guardrail(
        prior_episode_count=5,
        prior_monthly_spend_usd=Decimal("1.00"),
        projected_episode_cost_usd=Decimal("0.10"),
    )
    spend_limit = evaluate_monthly_guardrail(
        prior_episode_count=1,
        prior_monthly_spend_usd=Decimal("4.95"),
        projected_episode_cost_usd=Decimal("0.10"),
    )

    assert episode_limit["status"] == "over_budget"
    assert episode_limit["episode_limit_exceeded"] is True
    assert spend_limit["status"] == "over_budget"
    assert spend_limit["spend_limit_exceeded"] is True


def test_monthly_guardrail_allows_explicit_operator_override() -> None:
    budget = evaluate_monthly_guardrail(
        prior_episode_count=5,
        prior_monthly_spend_usd=Decimal("5.00"),
        projected_episode_cost_usd=Decimal("0.50"),
        override={"actor": "hermes", "reason": "approved launch exception", "recorded_at": "2026-06-09T11:00:00Z"},
    )

    assert budget["status"] == "override_recorded"
    assert budget["override_required"] is True
    assert budget["override"] == {
        "actor": "hermes",
        "reason": "approved launch exception",
        "recorded_at": "2026-06-09T11:00:00Z",
    }


def test_cost_gate_blocks_missing_fields_and_unknown_budget_status() -> None:
    incomplete = {
        "week": "2026-W23",
        "costs": {},
        "budget": {"status": "unknown"},
        "privacy": {"secrets_recorded": False, "provider_credentials_recorded": False, "full_prompts_recorded": False},
    }

    missing = missing_cost_ledger_fields(incomplete)
    assert "month" in missing
    assert "provider" in missing
    assert "costs.tts" in missing
    assert cost_gate_blockers(incomplete) == ["cost_ledger_incomplete"]
    assert cost_gate_blockers(None) == ["cost_ledger_missing"]


def test_cost_gate_blocks_unknown_or_invalid_cost_values() -> None:
    ledger = build_cost_ledger(
        week="2026-W23",
        month="2026-06",
        provider="not_selected",
        voice="not_selected",
        voice_config_hash="a" * 64,
        billable_characters=1200,
        duration_seconds=0,
        audio_byte_length=256,
        staged_byte_length=4096,
    )

    for invalid_money in ("unknown", "NaN", "Infinity", "-0.01"):
        ledger["costs"]["tts"]["estimated_usd"] = invalid_money

        assert "costs.tts.estimated_usd" in missing_cost_ledger_fields(ledger)
        assert cost_gate_blockers(ledger) == ["cost_ledger_incomplete"]
        ledger["costs"]["tts"]["estimated_usd"] = "0.00"
        ledger["budget"]["projected_monthly_spend_usd"] = invalid_money

        assert "budget.projected_monthly_spend_usd" in missing_cost_ledger_fields(ledger)
        assert cost_gate_blockers(ledger) == ["cost_ledger_incomplete"]
        ledger["budget"]["projected_monthly_spend_usd"] = "0.00"


def test_cost_gate_blocks_over_budget_without_override() -> None:
    ledger = build_cost_ledger(
        week="2026-W23",
        month="2026-06",
        provider="azure-speech",
        voice="en-US-test",
        voice_config_hash="a" * 64,
        billable_characters=1000,
        duration_seconds=300,
        audio_byte_length=1024,
        staged_byte_length=2048,
        prior_episode_count=5,
    )

    assert ledger["budget"]["status"] == "over_budget"
    assert ledger["readiness"]["complete"] is False
    assert cost_gate_blockers(ledger) == ["monthly_budget_exceeded"]
