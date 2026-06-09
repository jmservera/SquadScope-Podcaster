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


def test_review_workflow_uses_podcast_review_environment_and_uploads_record() -> None:
    workflow = REVIEW_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "environment: podcast-review" in workflow
    assert "github.actor" in workflow
    assert "actions/upload-artifact@" in workflow
    assert "must not contain credentials, query strings, or fragments" in workflow
    assert "scripts/record_review_approval.py" in workflow


def test_review_approval_records_actor_time_and_opens_tts_gate(tmp_path: Path) -> None:
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
    assert reviewed["generation"]["tts_synthesis"]["allowed"] is True
    assert reviewed["generation"]["tts_synthesis"]["blocked_by"] == []
    assert "human_review" not in reviewed["publishing"]["blocked_by"]
    assert reviewed["publishing"]["eligible"] is False


def test_record_review_approval_cli_writes_reviewed_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "reviewed.json"
    manifest_path.write_text(
        json.dumps(
            {
                "job_id": "podcast-2026-W23-test",
                "review": {"status": "pending", "audit_trail": [], "gate": {"status": "blocked"}},
                "generation": {"tts_synthesis": {"status": "blocked", "allowed": False, "blocked_by": ["human_review"]}},
                "publishing": {"eligible": False, "blocked_by": ["human_review", "real_tts_not_implemented"]},
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
    assert reviewed["review"]["audit_trail"][-1]["notes"] == "Claim ledger has unverified placeholders."
