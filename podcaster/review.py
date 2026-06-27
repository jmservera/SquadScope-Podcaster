from __future__ import annotations

import json
from typing import Any

from podcaster.costs import cost_gate_blockers

APPROVED = "approved"
CHANGES_REQUESTED = "changes_requested"
REJECTED = "rejected"
VALID_DECISIONS = {APPROVED, CHANGES_REQUESTED, REJECTED}
PROVIDER_TTS_BLOCKERS = [
    "provider_not_selected",
    "provider_privacy_review_required",
    "rai_security_signoff_required",
]


def apply_review_decision(
    manifest: dict[str, Any],
    *,
    reviewer: str,
    reviewed_at: str,
    decision: str,
    notes: str = "",
    run_url: str | None = None,
) -> dict[str, Any]:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(sorted(VALID_DECISIONS))}")
    if not reviewer.strip():
        raise ValueError("reviewer is required")

    updated = json.loads(json.dumps(manifest))
    review = updated.setdefault("review", {})
    audit_entry = {
        "at": reviewed_at,
        "actor": reviewer,
        "decision": decision,
        "notes": notes,
        "run_url": run_url,
    }
    review["status"] = decision
    review["approved_by"] = reviewer if decision == APPROVED else None
    review["approved_at"] = reviewed_at if decision == APPROVED else None
    review.setdefault("audit_trail", []).append(audit_entry)

    gate = review.setdefault("gate", {})
    gate["status"] = "approved" if decision == APPROVED else "blocked"

    updated["status"] = "review_approved" if decision == APPROVED else decision
    updated["reviewed_at"] = reviewed_at
    updated["reviewer"] = reviewer
    updated["review_status"] = decision

    lifecycle = updated.setdefault("lifecycle", {})
    lifecycle["status"] = updated["status"]
    lifecycle.setdefault("transitions", []).append(
        {
            "at": reviewed_at,
            "to": updated["status"],
            "reason": f"human_review_{decision}",
            "actor": reviewer,
        }
    )

    generation = updated.setdefault("generation", {})
    tts_synthesis = generation.setdefault("tts_synthesis", {})
    synthesized_audio = _has_synthesized_audio(updated)
    cost_blockers = cost_gate_blockers(updated.get("cost_ledger"))
    provider_selection_complete = _provider_selection_complete(updated)
    provider_blockers = (
        ["provider_privacy_review_required", "rai_security_signoff_required"]
        if provider_selection_complete
        else PROVIDER_TTS_BLOCKERS
    )
    existing_blockers = [
        blocker
        for blocker in _blocked_by(tts_synthesis)
        if blocker != "human_review"
        and blocker not in cost_blockers
        and blocker not in PROVIDER_TTS_BLOCKERS
    ]
    remaining_blockers = list(existing_blockers)
    synthesis_blockers = (
        cost_blockers if synthesized_audio else [*provider_blockers, *cost_blockers]
    )
    for blocker in synthesis_blockers:
        remaining_blockers = _append_blocker(remaining_blockers, blocker)
    if decision == APPROVED:
        if synthesized_audio:
            tts_synthesis["status"] = "completed"
            tts_synthesis["allowed"] = True
            tts_synthesis["blocked_by"] = []
        else:
            tts_synthesis["status"] = "review_approved" if not remaining_blockers else "blocked"
            tts_synthesis["allowed"] = not remaining_blockers
            tts_synthesis["blocked_by"] = remaining_blockers
    else:
        tts_synthesis["status"] = "blocked"
        tts_synthesis["allowed"] = False
        tts_synthesis["blocked_by"] = ["human_review", *remaining_blockers]

    publishing = updated.setdefault("publishing", {})
    blocked_by: list[str] = []
    if decision != APPROVED:
        blocked_by = _append_blocker(blocked_by, "human_review")
    if not synthesized_audio:
        blocked_by = _append_blocker(blocked_by, "synthesis_not_completed")
        blocked_by = _append_blocker(blocked_by, "audio_validation_not_passed")
    elif not _audio_validation_ready(updated):
        blocked_by = _append_blocker(blocked_by, "audio_validation_not_passed")
    for blocker in cost_blockers:
        blocked_by = _append_blocker(blocked_by, blocker)
    publishing["blocked_by"] = blocked_by
    publishing["packet_ready"] = decision == APPROVED and synthesized_audio
    publishing["eligible"] = decision == APPROVED and not blocked_by
    readiness_checks = publishing.setdefault("readiness_checks", {})
    readiness_checks["cost_ledger_complete"] = not cost_blockers
    readiness_checks["editorial_review_complete"] = decision == APPROVED
    readiness_checks["real_audio_available"] = synthesized_audio
    readiness_checks["audio_validation_passed"] = _audio_validation_ready(updated)

    return updated


def _blocked_by(section: dict[str, Any]) -> list[str]:
    blockers = section.get("blocked_by", [])
    if not isinstance(blockers, list):
        return []
    return [reason for reason in blockers if isinstance(reason, str) and reason]


def _append_blocker(blockers: list[str], reason: str) -> list[str]:
    if reason not in blockers:
        blockers.append(reason)
    return blockers


def _provider_selection_complete(manifest: dict[str, Any]) -> bool:
    generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
    tts_synthesis = (
        generation.get("tts_synthesis") if isinstance(generation.get("tts_synthesis"), dict) else {}
    )
    provider_selection = generation.get("provider_selection") or tts_synthesis.get(
        "provider_selection"
    )
    if isinstance(provider_selection, dict):
        status = provider_selection.get("status")
        if status in {"complete", "completed", "configured", "selected"}:
            return True
    if (
        tts_synthesis.get("provider_selection_complete") is True
        or generation.get("provider_selection_complete") is True
    ):
        return True

    provider = generation.get("tts_provider") or manifest.get("tts_provider")
    fallback = (
        generation.get("tts_fallback_provider")
        or generation.get("fallback_tts_provider")
        or manifest.get("tts_fallback_provider")
        or manifest.get("fallback_tts_provider")
    )
    fallbacks = generation.get("tts_fallbacks") or manifest.get("tts_fallbacks")
    has_fallback = _has_value(fallback) or (
        isinstance(fallbacks, list) and any(_has_value(item) for item in fallbacks)
    )
    return _has_value(provider) and has_fallback


def _has_synthesized_audio(manifest: dict[str, Any]) -> bool:
    generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
    if generation.get("audio_mode") == "synthesized":
        return True
    runner = generation.get("synthesis_runner")
    return isinstance(runner, dict) and runner.get("status") == "completed"


def _audio_validation_ready(manifest: dict[str, Any]) -> bool:
    generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
    validation = generation.get("audio_validation")
    return (
        isinstance(validation, dict)
        and validation.get("ready") is True
        and validation.get("status") == "passed"
    )


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None
