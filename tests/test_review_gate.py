from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from podcaster.jobs import run_generation_job
from podcaster.storage import LocalStorageBackend
from scripts.record_review_approval import apply_review_decision


ROOT = Path(__file__).resolve().parents[1]
REVIEW_WORKFLOW = ROOT / ".github/workflows/podcast-review-gate.yml"
SECURITY_DOC = ROOT / "docs/SECURITY.md"


def test_review_workflow_uses_podcast_review_environment_and_uploads_record() -> None:
    workflow = REVIEW_WORKFLOW.read_text(encoding="utf-8")
    record_step = workflow.split("- name: Record review decision", 1)[1].split("run: |", 1)[0]

    assert "workflow_dispatch:" in workflow
    assert "environment: podcast-review" in workflow
    assert "github.actor" in workflow
    assert "actions/upload-artifact@" in workflow
    assert "must not contain credentials, query strings, or fragments" in workflow
    assert "/api/review" in workflow
    assert "PODCASTER_API_KEY" in workflow
    assert "MANIFEST_URL:" not in record_step
    assert "PACKET_URL:" not in record_step


def test_review_approval_records_actor_time_and_preserves_provider_tts_gate(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    result = run_generation_job(
        {"week": "2026-W23", "article_url": "https://example.com/article"},
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    reviewed = apply_review_decision(
        result.manifest,
        reviewer="leela",
        reviewed_at="2026-06-08T22:00:00Z",
        decision="approved",
        notes="Approved after checklist review.",
        run_url="https://github.com/jmservera/SquadScope-Podcaster/actions/runs/1",
    )

    assert reviewed["status"] == "review_approved"
    assert reviewed["review"]["status"] == "approved"
    assert reviewed["review"]["approved_by"] == "leela"
    assert reviewed["review"]["approved_at"] == "2026-06-08T22:00:00Z"
    assert reviewed["review"]["audit_trail"][-1]["actor"] == "leela"
    assert reviewed["generation"]["tts_synthesis"]["allowed"] is False
    assert reviewed["generation"]["tts_synthesis"]["status"] == "blocked"
    assert reviewed["generation"]["tts_synthesis"]["blocked_by"] == [
        "provider_not_selected",
        "provider_privacy_review_required",
        "rai_security_signoff_required",
    ]
    assert reviewed["publishing"]["packet_ready"] is False
    assert reviewed["publishing"]["readiness_checks"]["cost_ledger_complete"] is True
    assert reviewed["publishing"]["readiness_checks"]["editorial_review_complete"] is True
    assert "human_review" not in reviewed["publishing"]["blocked_by"]
    assert reviewed["publishing"]["blocked_by"] == [
        "synthesis_not_completed",
        "audio_validation_not_passed",
    ]
    assert reviewed["publishing"]["eligible"] is False
    assert reviewed["publishing"]["packet_ready"] is False
    assert reviewed["generation"]["audio_mode"] == "placeholder"
    assert reviewed["generation"]["audio_validation"]["ready"] is False
    assert reviewed["generation"]["audio_validation"]["status"] == "blocked"


def test_review_approval_reinstates_provider_gate_when_manifest_only_had_human_review() -> None:
    reviewed = apply_review_decision(
        {
            "job_id": "podcast-2026-W23-test",
            "review": {"status": "pending", "audit_trail": [], "gate": {"status": "blocked"}},
            "generation": {
                "audio_mode": "placeholder",
                "tts_provider": None,
                "tts_synthesis": {"status": "blocked", "allowed": False, "blocked_by": ["human_review"]},
                "audio_validation": {"status": "blocked", "ready": False},
            },
            "publishing": {"eligible": True, "packet_ready": True, "blocked_by": ["human_review"]},
            "lifecycle": {"status": "review_pending", "transitions": []},
        },
        reviewer="leela",
        reviewed_at="2026-06-08T22:00:00Z",
        decision="approved",
    )

    tts_synthesis = reviewed["generation"]["tts_synthesis"]
    assert tts_synthesis["allowed"] is False
    assert tts_synthesis["status"] == "blocked"
    assert tts_synthesis["blocked_by"] == [
        "provider_not_selected",
        "provider_privacy_review_required",
        "rai_security_signoff_required",
        "cost_ledger_missing",
    ]
    assert reviewed["publishing"]["eligible"] is False
    assert reviewed["publishing"]["packet_ready"] is False
    assert reviewed["publishing"]["blocked_by"] == ["synthesis_not_completed", "audio_validation_not_passed", "cost_ledger_missing"]


def test_review_approval_preserves_privacy_and_rai_gates_after_provider_selection() -> None:
    base_manifest = {
        "job_id": "podcast-2026-W23-real-audio",
        "review": {"status": "pending", "audit_trail": [], "gate": {"status": "blocked"}},
        "generation": {
            "tts_provider": "azure-speech",
            "tts_synthesis": {
                "status": "blocked",
                "allowed": False,
                "blocked_by": ["human_review", "provider_not_selected"],
            },
            "audio_validation": {"status": "passed", "ready": True},
        },
        "publishing": {"eligible": False, "packet_ready": False, "blocked_by": ["human_review"]},
        "lifecycle": {"status": "review_pending", "transitions": []},
    }

    missing_fallback = apply_review_decision(
        base_manifest,
        reviewer="leela",
        reviewed_at="2026-06-08T22:00:00Z",
        decision="approved",
    )
    assert missing_fallback["generation"]["tts_synthesis"]["allowed"] is False
    assert missing_fallback["generation"]["tts_synthesis"]["blocked_by"] == [
        "provider_not_selected",
        "provider_privacy_review_required",
        "rai_security_signoff_required",
        "cost_ledger_missing",
    ]

    complete_provider_selection = json.loads(json.dumps(base_manifest))
    complete_provider_selection["generation"]["tts_fallback_provider"] = "openai-tts"
    reviewed = apply_review_decision(
        complete_provider_selection,
        reviewer="leela",
        reviewed_at="2026-06-08T22:00:00Z",
        decision="approved",
    )
    assert reviewed["generation"]["tts_synthesis"]["allowed"] is False
    assert reviewed["generation"]["tts_synthesis"]["blocked_by"] == [
        "provider_privacy_review_required",
        "rai_security_signoff_required",
        "cost_ledger_missing",
    ]


def test_review_approval_fails_closed_when_cost_ledger_is_missing() -> None:
    reviewed = apply_review_decision(
        {
            "job_id": "podcast-2026-W23-test",
            "review": {"status": "pending", "audit_trail": [], "gate": {"status": "blocked"}},
            "generation": {"tts_synthesis": {"status": "blocked", "allowed": False, "blocked_by": ["human_review"]}},
            "publishing": {"eligible": False, "blocked_by": ["human_review", "synthesis_not_completed"]},
            "lifecycle": {"status": "review_pending", "transitions": []},
        },
        reviewer="leela",
        reviewed_at="2026-06-08T22:00:00Z",
        decision="approved",
    )

    assert reviewed["review"]["status"] == "approved"
    assert reviewed["generation"]["tts_synthesis"]["allowed"] is False
    assert reviewed["generation"]["tts_synthesis"]["blocked_by"] == [
        "provider_not_selected",
        "provider_privacy_review_required",
        "rai_security_signoff_required",
        "cost_ledger_missing",
    ]
    assert reviewed["publishing"]["blocked_by"] == [
        "synthesis_not_completed",
        "audio_validation_not_passed",
        "cost_ledger_missing",
    ]
    assert reviewed["publishing"]["packet_ready"] is False


def test_record_review_approval_cli_writes_reviewed_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "reviewed.json"
    manifest_path.write_text(
        json.dumps(
            {
                "job_id": "podcast-2026-W23-test",
                "review": {"status": "pending", "audit_trail": [], "gate": {"status": "blocked"}},
                "generation": {
                    "tts_synthesis": {
                        "status": "blocked",
                        "allowed": False,
                        "blocked_by": [
                            "human_review",
                            "provider_not_selected",
                            "provider_privacy_review_required",
                            "rai_security_signoff_required",
                        ],
                    }
                },
                "publishing": {"eligible": False, "blocked_by": ["human_review", "synthesis_not_completed"]},
                "lifecycle": {"status": "review_pending", "transitions": []},
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/record_review_approval.py"),
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--reviewer",
            "farnsworth",
            "--decision",
            "changes_requested",
            "--notes",
            "Claim ledger has unverified placeholders.",
            "--reviewed-at",
            "2026-06-08T22:30:00Z",
        ],
        check=True,
    )

    reviewed = json.loads(output_path.read_text(encoding="utf-8"))
    assert reviewed["review_status"] == "changes_requested"
    assert reviewed["review"]["approved_by"] is None
    assert reviewed["generation"]["tts_synthesis"]["allowed"] is False
    assert reviewed["generation"]["tts_synthesis"]["blocked_by"] == ["human_review", "provider_not_selected", "provider_privacy_review_required", "rai_security_signoff_required", "cost_ledger_missing"]
    assert reviewed["review"]["audit_trail"][-1]["notes"] == "Claim ledger has unverified placeholders."


def test_security_doc_discloses_tts_provider_and_staging_privacy_gates() -> None:
    security_doc = SECURITY_DOC.read_text(encoding="utf-8")

    assert "Selected production provider:** none yet" in security_doc
    assert "Azure AI Speech Standard voices" in security_doc
    assert "OpenAI `tts-1` or `gpt-4o-mini-tts`" in security_doc
    assert "No `PODCASTER_API_KEY`" in security_doc
    assert "Non-dry-run TTS is blocked" in security_doc
    assert "Temporary Azure Blob Staging Disclosure" in security_doc
    assert "Retention is 7 days" in security_doc
    assert "SquadScope privacy changes are limited" in security_doc
