from __future__ import annotations

import json
from pathlib import Path

from podcaster.costs import build_cost_ledger
from podcaster.generation import manifest_bytes
from podcaster.orchestration import auto_publish_job, manifest_path, process_review_decision
from podcaster.publish import PublishResult
from podcaster.storage import LocalStorageBackend


def _job_id() -> str:
    return "podcast-2026-W24-orchestration"


def _synthesized_manifest() -> dict:
    job_id = _job_id()
    mp3 = f"jobs/{job_id}/audio/{job_id}.mp3"
    wav = f"jobs/{job_id}/audio/{job_id}.wav"
    return {
        "job_id": job_id,
        "status": "synthesized_review_ready",
        "request": {
            "week": "2026-W24",
            "article_url": "https://example.com/article",
            "article_title": "Weekly signal",
        },
        "review": {"status": "pending", "audit_trail": [], "gate": {"status": "blocked"}},
        "cost_ledger": build_cost_ledger(
            week="2026-W24",
            month="2026-06",
            provider="openai-tts",
            voice="fable,alloy",
            voice_config_hash="abc123",
            billable_characters=100,
            duration_seconds=300,
            audio_byte_length=3,
            staged_byte_length=6,
        ),
        "generation": {
            "audio_mode": "synthesized",
            "tts_provider": "openai-tts",
            "tts_voice": ["fable", "alloy"],
            "tts_synthesis": {"status": "completed", "allowed": True, "blocked_by": []},
            "audio_validation": {"status": "passed", "ready": True},
            "synthesis_runner": {
                "status": "completed",
                "audio": {
                    "path": mp3,
                    "artifacts": {
                        "mp3": {"path": mp3},
                        "wav": {"path": wav},
                    },
                },
            },
        },
        "publishing": {
            "mode": "review_gate",
            "eligible": False,
            "packet_ready": True,
            "blocked_by": ["human_review"],
            "readiness_checks": {
                "editorial_review_complete": False,
                "real_audio_available": True,
                "audio_validation_passed": True,
            },
        },
        "lifecycle": {"status": "synthesized_review_ready", "revision": 1, "transitions": []},
        "artifacts": {
            mp3: {"url": f"https://example.invalid/{mp3}"},
            wav: {"url": f"https://example.invalid/{wav}"},
        },
    }


def _stage(storage: LocalStorageBackend, manifest: dict) -> None:
    job_id = manifest["job_id"]
    storage.put_bytes(manifest_path(job_id), manifest_bytes(manifest), "application/json; charset=utf-8")
    storage.put_bytes(f"jobs/{job_id}/audio/{job_id}.mp3", b"mp3", "audio/mpeg")
    storage.put_bytes(f"jobs/{job_id}/audio/{job_id}.wav", b"wav", "audio/wav")


def test_review_approval_publishes_when_audio_is_ready(tmp_path: Path, monkeypatch) -> None:
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    _stage(storage, _synthesized_manifest())
    monkeypatch.setattr(
        "podcaster.orchestration.publish_episode",
        lambda *args, **kwargs: PublishResult(status="published", anchor_episode_id=42),
    )

    outcome = process_review_decision(
        _job_id(),
        reviewer="leela",
        decision="approved",
        reviewed_at="2026-06-15T12:00:00Z",
        storage=storage,
    )

    assert outcome.publish_result is not None
    assert outcome.publish_result.status == "published"
    assert outcome.manifest["status"] == "published"
    assert outcome.manifest["review"]["status"] == "approved"
    assert outcome.manifest["publishing"]["result"]["anchor_episode_id"] == 42


def test_changes_requested_does_not_publish(tmp_path: Path, monkeypatch) -> None:
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    _stage(storage, _synthesized_manifest())

    def fail_publish(*args, **kwargs):
        raise AssertionError("publish should not run")

    monkeypatch.setattr("podcaster.orchestration.publish_episode", fail_publish)
    outcome = process_review_decision(
        _job_id(),
        reviewer="leela",
        decision="changes_requested",
        reviewed_at="2026-06-15T12:00:00Z",
        storage=storage,
    )
    assert outcome.publish_result is None
    assert outcome.manifest["status"] == "changes_requested"


def test_auto_publish_job_records_system_review(tmp_path: Path, monkeypatch) -> None:
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    _stage(storage, _synthesized_manifest())
    monkeypatch.setattr(
        "podcaster.orchestration.publish_episode",
        lambda *args, **kwargs: PublishResult(status="published", anchor_episode_id=7),
    )

    outcome = auto_publish_job(_job_id(), storage=storage)
    persisted = json.loads(storage.get_bytes(manifest_path(_job_id())).decode("utf-8"))
    assert outcome.manifest["review"]["approved_by"] == "system:auto-publish"
    assert persisted["status"] == "published"
