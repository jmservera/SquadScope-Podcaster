from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from podcaster.config import PodcastConfig
from podcaster.costs import cost_gate_blockers
from podcaster.generation import checksum, manifest_bytes
from podcaster.storage import StorageBackend
from podcaster.tts import TtsConfig
from podcaster.tts_pool import load_tts_pool_config

APPROVED = "approved"
CHANGES_REQUESTED = "changes_requested"
REJECTED = "rejected"
VALID_DECISIONS = {APPROVED, CHANGES_REQUESTED, REJECTED}
PROVIDER_TTS_BLOCKERS = [
    "provider_not_selected",
    "provider_privacy_review_required",
    "rai_security_signoff_required",
]
PROVIDER_AUTHORIZATION_FIELDS = (
    "selected_provider",
    "fallback_provider",
    "provider_selection_reference",
    "provider_privacy_review_reference",
    "rai_security_signoff_reference",
    "disclosure_reference",
    "region",
)


class ReviewBindingError(ValueError):
    """The staged content or dispatch no longer permits exact-content approval."""


def is_exact_script_job(manifest: dict[str, Any]) -> bool:
    """Recognize canonical jobs even when their snapshot is malformed."""
    review = manifest.get("review", {})
    generation = manifest.get("generation", {})
    return (
        not isinstance(review, dict)
        or not isinstance(generation, dict)
        or "snapshot" in review
        or "synthesis_approval" in review
        or "provider_settings_sha256" in generation
    )


def provider_settings_sha256(config: TtsConfig) -> str:
    """Fingerprint effective provider settings without persisting endpoint or credentials."""
    settings = asdict(config)
    settings.pop("chat_deployment")
    return checksum(manifest_bytes({"tts": settings, "pool": asdict(load_tts_pool_config())}))


def content_snapshot_sha256(manifest: dict[str, Any]) -> str:
    """Hash immutable review inputs, excluding mutable lifecycle and audit records."""
    generation = manifest["generation"]
    return checksum(
        manifest_bytes(
            {
                "job_id": manifest["job_id"],
                "created_at": manifest["created_at"],
                "expires_at": manifest["expires_at"],
                "request": manifest["request"],
                "artifacts": manifest["artifacts"],
                "cost_ledger": manifest["cost_ledger"],
                "engine": generation["engine"],
                "resolved_podcast_config": generation["resolved_podcast_config"],
                "provider_settings": generation["provider_settings"],
                "provider_settings_sha256": generation["provider_settings_sha256"],
            }
        )
    )


def provider_authorization_blockers(manifest: dict[str, Any]) -> list[str]:
    """Keep policy gates closed without recorded, configuration-bound human evidence."""
    generation = manifest.get("generation", {})
    review = manifest.get("review", {})
    approval = review.get("synthesis_approval", {})
    authorization = approval.get("provider_authorization", {})
    if not isinstance(authorization, dict):
        return list(PROVIDER_TTS_BLOCKERS)
    if (
        approval.get("status") != APPROVED
        or not approval.get("approved_by")
        or not approval.get("approved_at")
        or authorization.get("provider_settings_sha256")
        != generation.get("provider_settings_sha256")
        or not generation.get("provider_settings_sha256")
        or not all(
            isinstance(authorization.get(field), str) and authorization[field].strip()
            for field in PROVIDER_AUTHORIZATION_FIELDS
        )
        or set(authorization) != {*PROVIDER_AUTHORIZATION_FIELDS, "provider_settings_sha256"}
        or authorization.get("selected_provider")
        != generation.get("provider_settings", {}).get("provider")
    ):
        return list(PROVIDER_TTS_BLOCKERS)
    return []


def validate_review_snapshot(
    manifest: dict[str, Any],
    storage: StorageBackend,
    job_id: str,
    config: TtsConfig,
) -> bytes:
    """Fail closed on dry runs, input/config drift or missing immutable review inputs."""
    try:
        request = manifest["request"]
        generation = manifest["generation"]
        snapshot = manifest["review"]["snapshot"]
        script = storage.get_bytes(f"jobs/{job_id}/script.txt")
        if (
            manifest["job_id"] != job_id
            or request["dry_run"] is not False
            or job_id.endswith("-dry-run")
            or generation["engine"] != "llm-script-gen"
            or generation["cost_ledger_persisted"] is not True
            or not config.production_ready
            or not script
            or snapshot["script_sha256"] != checksum(script)
            or snapshot["manifest_sha256"] != content_snapshot_sha256(manifest)
            or generation["provider_settings_sha256"] != provider_settings_sha256(config)
            or manifest_bytes(generation["resolved_podcast_config"])
            != manifest_bytes(asdict(PodcastConfig.from_payload(request)))
        ):
            raise ReviewBindingError("review snapshot changed or production configuration missing")
        for path, artifact in manifest["artifacts"].items():
            if not path.startswith(f"jobs/{job_id}/"):
                raise ReviewBindingError("review artifact escapes the staged job")
            content = storage.get_bytes(path)
            if content is None or checksum(content) != artifact["sha256"]:
                raise ReviewBindingError("review artifact content changed or is missing")
        blockers = cost_gate_blockers(manifest.get("cost_ledger"))
        blockers += provider_authorization_blockers(manifest)
        blockers += [
            item
            for item in generation["tts_synthesis"].get("blocked_by", [])
            if item != "human_review"
        ]
        if blockers:
            raise ReviewBindingError("synthesis blocked: " + ", ".join(sorted(set(blockers))))
    except (KeyError, TypeError, AttributeError) as exc:
        raise ReviewBindingError("missing or malformed exact-content review snapshot") from exc
    return script


def validate_review_binding(manifest: dict[str, Any]) -> None:
    """Require a recorded human decision bound to both immutable content digests."""
    try:
        publication_review = manifest["review"]
        review = publication_review["synthesis_approval"]
        binding = review["binding"]
        if (
            review["status"] != APPROVED
            or not isinstance(review["approved_by"], str)
            or not review["approved_by"].strip()
            or review["approved_by"].strip().lower().startswith("system:")
            or not isinstance(review["approved_at"], str)
            or binding["reviewer"] != review["approved_by"]
            or binding["reviewed_at"] != review["approved_at"]
            or binding["decision"] != APPROVED
            or binding["scope"] != "pre_tts"
            or review["review_checks_confirmed"] is not True
            or binding["script_sha256"] != publication_review["snapshot"]["script_sha256"]
            or binding["manifest_sha256"] != publication_review["snapshot"]["manifest_sha256"]
            or binding["provider_authorization"] != review["provider_authorization"]
            or binding["review_checks_confirmed"] is not True
            or binding not in review["audit_trail"]
            or provider_authorization_blockers(manifest)
        ):
            raise ReviewBindingError("human approval is absent or not bound to staged content")
        timestamp = datetime.fromisoformat(review["approved_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("review timestamp requires timezone")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ReviewBindingError("missing or invalid exact-content human approval") from exc


def apply_review_decision(
    manifest: dict[str, Any],
    *,
    reviewer: str,
    reviewed_at: str,
    decision: str,
    notes: str = "",
    run_url: str | None = None,
    provider_authorization: dict[str, Any] | None = None,
    review_checks_confirmed: bool = False,
) -> dict[str, Any]:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(sorted(VALID_DECISIONS))}")
    if not reviewer.strip():
        raise ValueError("reviewer is required")

    updated = json.loads(json.dumps(manifest))
    publication_review = updated.setdefault("review", {})
    synthesized_audio = _has_synthesized_audio(updated)
    exact_script_job = isinstance(publication_review.get("snapshot"), dict)
    exact_script_review = exact_script_job and not synthesized_audio
    review = (
        publication_review.setdefault(
            "synthesis_approval" if exact_script_review else "audio_approval", {}
        )
        if exact_script_job
        else publication_review
    )
    if exact_script_job:
        publication_review["status"] = "pending"
        publication_review["approved_by"] = None
        publication_review["approved_at"] = None
        publication_review.setdefault("gate", {})["status"] = "blocked"
        updated["review_status"] = "pending"
    if exact_script_review:
        review["provider_authorization"] = json.loads(json.dumps(provider_authorization or {}))
        review["review_checks_confirmed"] = review_checks_confirmed is True
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
    if not exact_script_job:
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
        cost_blockers
        if synthesized_audio
        else [
            *(
                provider_authorization_blockers(updated)
                if exact_script_review
                else provider_blockers
            ),
            *cost_blockers,
        ]
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
    if decision != APPROVED or exact_script_job:
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
    readiness_checks["editorial_review_complete"] = decision == APPROVED and not exact_script_job
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
