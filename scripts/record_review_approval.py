from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APPROVED = "approved"
CHANGES_REQUESTED = "changes_requested"
REJECTED = "rejected"
VALID_DECISIONS = {APPROVED, CHANGES_REQUESTED, REJECTED}


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
    tts_blockers = _blocked_by(tts_synthesis)
    if decision == APPROVED:
        tts_blockers = [reason for reason in tts_blockers if reason != "human_review"]
        if _provider_selection_complete(updated):
            tts_blockers = [reason for reason in tts_blockers if reason != "provider_not_selected"]
        else:
            tts_blockers = _append_blocker(tts_blockers, "provider_not_selected")
    else:
        tts_blockers = [reason for reason in tts_blockers if reason != "human_review"]
        tts_blockers.insert(0, "human_review")
    tts_synthesis["blocked_by"] = tts_blockers
    tts_synthesis["allowed"] = decision == APPROVED and not tts_blockers
    tts_synthesis["status"] = "review_approved" if not tts_blockers and decision == APPROVED else "blocked"

    publishing = updated.setdefault("publishing", {})
    blocked_by = [reason for reason in _blocked_by(publishing) if reason != "human_review"]
    if decision != APPROVED and "human_review" not in blocked_by:
        blocked_by.insert(0, "human_review")
    if _uses_placeholder_audio(updated):
        blocked_by = _append_blocker(blocked_by, "real_tts_not_implemented")
        blocked_by = _append_blocker(blocked_by, "audio_validation_not_passed")
    elif not _audio_validation_ready(updated):
        blocked_by = _append_blocker(blocked_by, "audio_validation_not_passed")
    publishing["blocked_by"] = blocked_by
    publishing["eligible"] = False
    publishing["packet_ready"] = False

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
    tts_synthesis = generation.get("tts_synthesis") if isinstance(generation.get("tts_synthesis"), dict) else {}
    provider_selection = generation.get("provider_selection") or tts_synthesis.get("provider_selection")
    if isinstance(provider_selection, dict):
        status = provider_selection.get("status")
        if status in {"complete", "completed", "configured", "selected"}:
            return True
    if tts_synthesis.get("provider_selection_complete") is True or generation.get("provider_selection_complete") is True:
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


def _uses_placeholder_audio(manifest: dict[str, Any]) -> bool:
    generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
    return (
        generation.get("audio_mode") == "placeholder"
        or generation.get("audio_placeholder") is True
        or manifest.get("audio_placeholder") is True
    )


def _audio_validation_ready(manifest: dict[str, Any]) -> bool:
    generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else {}
    validation = generation.get("audio_validation")
    return isinstance(validation, dict) and validation.get("ready") is True and validation.get("status") == "passed"


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a Podcaster human review decision in an episode manifest.")
    parser.add_argument("--manifest", required=True, type=Path, help="Path to the existing episode manifest JSON.")
    parser.add_argument("--output", required=True, type=Path, help="Path to write the reviewed manifest JSON.")
    parser.add_argument("--reviewer", required=True, help="GitHub actor or reviewer identity.")
    parser.add_argument("--decision", required=True, choices=sorted(VALID_DECISIONS))
    parser.add_argument("--notes", default="")
    parser.add_argument("--reviewed-at", default=None, help="ISO 8601 UTC timestamp. Defaults to now.")
    parser.add_argument("--run-url", default=None)
    args = parser.parse_args()

    reviewed_at = args.reviewed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    updated = apply_review_decision(
        manifest,
        reviewer=args.reviewer,
        reviewed_at=reviewed_at,
        decision=args.decision,
        notes=args.notes,
        run_url=args.run_url,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(updated, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
