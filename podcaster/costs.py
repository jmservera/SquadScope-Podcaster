from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


MAX_EPISODES_PER_MONTH = 5
MAX_MONTHLY_SPEND_USD = Decimal("5.00")
USD_ZERO = Decimal("0.00")

COST_CATEGORIES = (
    "script_generation",
    "validation",
    "tts",
    "staging_storage",
    "egress_download",
    "platform_provider",
)


def build_cost_ledger(
    *,
    week: str,
    month: str,
    provider: str | None,
    voice: str | None,
    voice_config_hash: str | None,
    billable_characters: int,
    duration_seconds: int | None,
    audio_byte_length: int,
    staged_byte_length: int,
    prior_episode_count: int = 0,
    prior_monthly_spend_usd: Decimal = USD_ZERO,
    projected_episode_cost_usd: Decimal = USD_ZERO,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    budget = evaluate_monthly_guardrail(
        prior_episode_count=prior_episode_count,
        prior_monthly_spend_usd=prior_monthly_spend_usd,
        projected_episode_cost_usd=projected_episode_cost_usd,
        override=override,
    )
    ledger = {
        "schema_version": "squadscope-podcaster-cost-ledger-v1",
        "week": week,
        "month": month,
        "provider": provider,
        "voice": voice,
        "voice_config_hash": voice_config_hash,
        "billable_characters": billable_characters,
        "duration_seconds": duration_seconds,
        "audio_byte_length": audio_byte_length,
        "staged_byte_length": staged_byte_length,
        "costs": _cost_categories(projected_episode_cost_usd),
        "budget": budget,
        "privacy": {
            "secrets_recorded": False,
            "provider_credentials_recorded": False,
            "full_prompts_recorded": False,
        },
        "readiness": {
            "complete": True,
            "missing_fields": [],
            "status": "ready",
        },
    }
    missing = missing_cost_ledger_fields(ledger)
    ledger["readiness"]["missing_fields"] = missing
    ledger["readiness"]["complete"] = not missing and budget["status"] in {"within_budget", "override_recorded"}
    ledger["readiness"]["status"] = "ready" if ledger["readiness"]["complete"] else "blocked"
    return ledger


def evaluate_monthly_guardrail(
    *,
    prior_episode_count: int,
    prior_monthly_spend_usd: Decimal,
    projected_episode_cost_usd: Decimal,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if prior_episode_count < 0:
        raise ValueError("prior_episode_count must be non-negative")
    if prior_monthly_spend_usd < USD_ZERO:
        raise ValueError("prior_monthly_spend_usd must be non-negative")
    if projected_episode_cost_usd < USD_ZERO:
        raise ValueError("projected_episode_cost_usd must be non-negative")

    projected_episode_count = prior_episode_count + 1
    projected_spend = prior_monthly_spend_usd + projected_episode_cost_usd
    episode_limit_exceeded = projected_episode_count > MAX_EPISODES_PER_MONTH
    spend_limit_exceeded = projected_spend > MAX_MONTHLY_SPEND_USD
    limit_exceeded = episode_limit_exceeded or spend_limit_exceeded
    override_recorded = _valid_override(override)

    if limit_exceeded and not override_recorded:
        status = "over_budget"
    elif limit_exceeded and override_recorded:
        status = "override_recorded"
    else:
        status = "within_budget"

    return {
        "max_episodes_per_month": MAX_EPISODES_PER_MONTH,
        "max_monthly_spend_usd": _money(MAX_MONTHLY_SPEND_USD),
        "prior_episode_count": prior_episode_count,
        "projected_episode_count": projected_episode_count,
        "prior_monthly_spend_usd": _money(prior_monthly_spend_usd),
        "projected_episode_cost_usd": _money(projected_episode_cost_usd),
        "projected_monthly_spend_usd": _money(projected_spend),
        "episode_limit_exceeded": episode_limit_exceeded,
        "spend_limit_exceeded": spend_limit_exceeded,
        "override_required": limit_exceeded,
        "override": _redacted_override(override) if override_recorded else None,
        "status": status,
    }


def missing_cost_ledger_fields(ledger: dict[str, Any]) -> list[str]:
    required_paths = [
        ("week",),
        ("month",),
        ("billable_characters",),
        ("audio_byte_length",),
        ("staged_byte_length",),
        ("costs",),
        ("budget",),
        ("budget", "status"),
        ("budget", "projected_episode_count"),
        ("budget", "projected_monthly_spend_usd"),
        ("privacy", "secrets_recorded"),
        ("privacy", "provider_credentials_recorded"),
        ("privacy", "full_prompts_recorded"),
    ]
    missing = [".".join(path) for path in required_paths if _lookup(ledger, path) is None]
    costs = ledger.get("costs")
    if isinstance(costs, dict):
        for category in COST_CATEGORIES:
            if category not in costs:
                missing.append(f"costs.{category}")
            elif not isinstance(costs[category], dict) or "estimated_usd" not in costs[category]:
                missing.append(f"costs.{category}.estimated_usd")
    else:
        missing.extend(f"costs.{category}" for category in COST_CATEGORIES)
    return missing


def cost_gate_blockers(ledger: dict[str, Any] | None) -> list[str]:
    if not isinstance(ledger, dict):
        return ["cost_ledger_missing"]
    missing = missing_cost_ledger_fields(ledger)
    if missing:
        return ["cost_ledger_incomplete"]
    budget = ledger.get("budget")
    status = budget.get("status") if isinstance(budget, dict) else None
    if status == "over_budget":
        return ["monthly_budget_exceeded"]
    if status not in {"within_budget", "override_recorded"}:
        return ["cost_budget_unknown"]
    return []


def _cost_categories(projected_episode_cost_usd: Decimal) -> dict[str, dict[str, Any]]:
    categories = {category: _zero_cost_entry("estimated zero for current placeholder pipeline") for category in COST_CATEGORIES}
    categories["tts"] = {
        "estimated_usd": _money(projected_episode_cost_usd),
        "actual_usd": None,
        "basis": "blocked before provider call; no non-dry-run synthesis cost incurred",
    }
    return categories


def _zero_cost_entry(basis: str) -> dict[str, Any]:
    return {"estimated_usd": _money(USD_ZERO), "actual_usd": None, "basis": basis}


def _valid_override(override: dict[str, Any] | None) -> bool:
    if not isinstance(override, dict):
        return False
    actor = override.get("actor")
    reason = override.get("reason")
    recorded_at = override.get("recorded_at")
    return all(isinstance(value, str) and bool(value.strip()) for value in (actor, reason, recorded_at))


def _redacted_override(override: dict[str, Any] | None) -> dict[str, str] | None:
    if not _valid_override(override):
        return None
    assert override is not None
    return {
        "actor": str(override["actor"]),
        "reason": str(override["reason"]),
        "recorded_at": str(override["recorded_at"]),
    }


def _money(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _lookup(mapping: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = mapping
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
