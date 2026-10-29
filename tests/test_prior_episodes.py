from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from review_support import approval_evidence_for

from podcaster import jobs
from podcaster.config import HistoricalContext
from podcaster.jobs import run_generation_job
from podcaster.orchestration import process_review_decision
from podcaster.prior_episodes import fetch_prior_episode_themes
from podcaster.storage import LocalStorageBackend

VALID_ARTICLE_CONTENT = (
    "This article covers a fresh platform release, the migration strategy behind it, the "
    "customer impact, and the operational lessons the hosts should react to on air this week."
)


def _themes_after_staged_episode(storage: LocalStorageBackend) -> tuple[str, ...]:
    return fetch_prior_episode_themes(
        storage,
        "podcast-2026-W45-current",
        current_week="2026-W45",
        current_created_at=datetime(2026, 11, 5, 11, tzinfo=timezone.utc),
    )


def test_prior_themes_include_real_review_pending_stage_without_authorizing_publish(
    reviewed_stage,
) -> None:
    storage, result, generator, producer, script = reviewed_stage
    job_id = result.response["job_id"]
    manifest_path = f"jobs/{job_id}/manifest.json"
    original_manifest = storage.get_bytes(manifest_path)

    assert result.manifest["status"] == "review_pending"
    assert result.manifest["publishing"]["eligible"] is False
    assert storage.get_bytes(f"jobs/{job_id}/script.txt") == script
    assert "The reviewed opening is unique to this staged episode" in _themes_after_staged_episode(
        storage
    )
    assert storage.get_bytes(manifest_path) == original_manifest
    assert result.manifest["generation"]["tts_synthesis"]["allowed"] is False
    assert producer.sent == []
    generator.assert_called_once()


def test_prior_themes_include_real_review_approved_stage(reviewed_stage) -> None:
    storage, result, generator, producer, script = reviewed_stage
    job_id = result.response["job_id"]
    reviewed = process_review_decision(
        job_id,
        storage=storage,
        reviewer="fry",
        decision="approved",
        reviewed_at="2026-10-29T11:05:00Z",
        **approval_evidence_for(result.manifest),
    ).manifest
    original_manifest = storage.get_bytes(f"jobs/{job_id}/manifest.json")

    assert reviewed["status"] == "review_approved"
    assert "The reviewed opening is unique to this staged episode" in _themes_after_staged_episode(
        storage
    )
    assert storage.get_bytes(f"jobs/{job_id}/manifest.json") == original_manifest
    assert storage.get_bytes(f"jobs/{job_id}/script.txt") == script
    assert reviewed["publishing"]["eligible"] is False
    assert len(producer.sent) == 1
    generator.assert_called_once()


@pytest.mark.parametrize(
    "status",
    [
        "accepted",
        "synthesized_review_ready",
        "synthesized_publish_ready",
        "publish_requested",
        "published",
        "scheduled",
        "draft",
    ],
)
def test_prior_themes_preserve_staged_script_at_supported_later_and_legacy_statuses(
    reviewed_stage, status: str
) -> None:
    storage, result, _generator, producer, script = reviewed_stage
    job_id = result.response["job_id"]
    manifest = json.loads(storage.get_bytes(f"jobs/{job_id}/manifest.json"))
    manifest["status"] = status
    manifest["lifecycle"]["status"] = status
    storage.put_bytes(
        f"jobs/{job_id}/manifest.json", json.dumps(manifest).encode(), "application/json"
    )

    assert "The reviewed opening is unique to this staged episode" in _themes_after_staged_episode(
        storage
    )
    assert storage.get_bytes(f"jobs/{job_id}/script.txt") == script
    assert producer.sent == []


@pytest.mark.parametrize("decision", ["rejected", "changes_requested"])
def test_prior_themes_exclude_actual_negative_review_with_canonical_script_intact(
    reviewed_stage, decision: str
) -> None:
    storage, result, generator, producer, script = reviewed_stage
    job_id = result.response["job_id"]
    reviewed = process_review_decision(
        job_id,
        storage=storage,
        reviewer="fry",
        decision=decision,
        reviewed_at="2026-10-29T11:05:00Z",
    ).manifest

    assert reviewed["status"] == decision
    assert reviewed["lifecycle"]["status"] == decision
    assert reviewed["review"]["synthesis_approval"]["status"] == decision
    assert storage.get_bytes(f"jobs/{job_id}/script.txt") == script
    assert _themes_after_staged_episode(storage) == ()
    assert reviewed["publishing"]["eligible"] is False
    assert reviewed["generation"]["tts_synthesis"]["allowed"] is False
    assert producer.sent == []
    generator.assert_called_once()


@pytest.mark.parametrize("status", ["failed", "rejected", "dry_run", "skipped", "unknown"])
def test_prior_themes_exclude_ineligible_staged_manifest_statuses(
    reviewed_stage, status: str
) -> None:
    storage, result, _generator, producer, script = reviewed_stage
    job_id = result.response["job_id"]
    manifest = json.loads(storage.get_bytes(f"jobs/{job_id}/manifest.json"))
    manifest["status"] = status
    manifest["lifecycle"]["status"] = status
    storage.put_bytes(
        f"jobs/{job_id}/manifest.json", json.dumps(manifest).encode(), "application/json"
    )

    assert storage.get_bytes(f"jobs/{job_id}/script.txt") == script
    assert _themes_after_staged_episode(storage) == ()
    assert producer.sent == []


def test_local_storage_backend_list_blobs_returns_prefix_matches(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    storage.put_bytes("jobs/podcast-2026-W24-aaaa/script.txt", b"script-a", "text/plain")
    storage.put_bytes("jobs/podcast-2026-W24-aaaa/audio/episode.mp3", b"mp3-a", "audio/mpeg")
    storage.put_bytes("jobs/podcast-2026-W23-bbbb/script.txt", b"script-b", "text/plain")
    storage.put_bytes("jobs/other-job/script.txt", b"ignore", "text/plain")

    assert storage.list_blobs("jobs/podcast-", limit=10) == [
        "jobs/podcast-2026-W23-bbbb/script.txt",
        "jobs/podcast-2026-W24-aaaa/audio/episode.mp3",
        "jobs/podcast-2026-W24-aaaa/script.txt",
    ]


class _MockStorage:
    def __init__(
        self, blobs: list[str], scripts: dict[str, bytes] | None = None, *, fail_list: bool = False
    ) -> None:
        self._blobs = blobs
        self._scripts = scripts or {}
        self._fail_list = fail_list
        self.read_paths: list[str] = []

    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        if self._fail_list:
            raise RuntimeError("blob listing unavailable")
        return self._blobs[:limit]

    def get_bytes(self, path: str) -> bytes | None:
        self.read_paths.append(path)
        return self._scripts.get(path)


def test_fetch_prior_episode_themes_reads_latest_scripts_and_caps_budget() -> None:
    def manifest(job_id: str, week: str, created_at: str) -> bytes:
        return json.dumps(
            {
                "job_id": job_id,
                "status": "accepted",
                "created_at": created_at,
                "request": {"week": week},
            }
        ).encode()

    storage = _MockStorage(
        blobs=[
            "jobs/podcast-2026-W27-dddd/script.txt",
            "jobs/podcast-2026-W26-cccc/script.txt",
            "jobs/podcast-2026-W25-bbbb/script.txt",
            "jobs/podcast-2026-W24-aaaa/script.txt",
        ],
        scripts={
            "jobs/podcast-2026-W26-cccc/manifest.json": manifest(
                "podcast-2026-W26-cccc", "2026-W26", "2026-06-25T12:00:00Z"
            ),
            "jobs/podcast-2026-W25-bbbb/manifest.json": manifest(
                "podcast-2026-W25-bbbb", "2026-W25", "2026-06-18T12:00:00Z"
            ),
            "jobs/podcast-2026-W24-aaaa/manifest.json": manifest(
                "podcast-2026-W24-aaaa", "2026-W24", "2026-06-11T12:00:00Z"
            ),
            "jobs/podcast-2026-W26-cccc/script.txt": (
                "Title: Claracle Podcast – Week 2026-W26\n"
                "Source URL: https://example.com/openai-agents-push-into-enterprise-it\n"
                "---\n"
                "Theo: In this episode we will talk about: OpenAI agents push into enterprise IT.\n"
                "Vera: Teams are comparing vendor agents against existing runbooks.\n"
                "Theo: Enterprises care about governance and deployment boundaries.\n"
            ).encode("utf-8"),
            "jobs/podcast-2026-W25-bbbb/script.txt": (
                "Title: Claracle Podcast – Week 2026-W25\n"
                "Source URL: https://example.com/evals-and-guardrails-become-standard\n"
                "---\n"
                "Theo: In this episode we will talk about: Eval loops and guardrails "
                "become standard.\n"
                "Vera: Platform teams are turning prompts into repeatable operating procedures.\n"
            ).encode("utf-8"),
            "jobs/podcast-2026-W24-aaaa/script.txt": (
                "Title: Claracle Podcast – Week 2026-W24\n"
                "Source URL: https://example.com/devops-budgets-tighten-around-inference-costs\n"
                "---\n"
                "Theo: In this episode we will talk about: DevOps budgets tighten "
                "around inference costs.\n"
            ).encode("utf-8"),
        },
    )

    themes = fetch_prior_episode_themes(
        storage,
        "podcast-2026-W27-dddd",
        current_week="2026-W27",
        current_created_at=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
    )

    assert themes == (
        "OpenAI agents push into enterprise IT",
        "openai agents push into enterprise it",
        "Teams are comparing vendor agents against existing runbooks",
        "Enterprises care about governance and deployment boundaries",
        "Eval loops and guardrails become standard",
        "evals and guardrails become standard",
        "Platform teams are turning prompts into repeatable operating procedures",
        "DevOps budgets tighten around inference costs",
    )
    assert [path for path in storage.read_paths if path.endswith("/script.txt")] == [
        "jobs/podcast-2026-W26-cccc/script.txt",
        "jobs/podcast-2026-W25-bbbb/script.txt",
        "jobs/podcast-2026-W24-aaaa/script.txt",
    ]


def test_fetch_prior_episode_themes_gracefully_handles_listing_failures() -> None:
    storage = _MockStorage(blobs=[], fail_list=True)

    assert fetch_prior_episode_themes(storage, "podcast-2026-W27-dddd") == ()
    assert storage.read_paths == []


def test_prior_selection_excludes_newer_week_and_uses_same_week_creation_time() -> None:
    def manifest(job_id: str, week: str, created_at: str) -> bytes:
        return json.dumps(
            {
                "job_id": job_id,
                "status": "accepted",
                "created_at": created_at,
                "request": {"week": week},
            }
        ).encode()

    storage = _MockStorage(
        blobs=[
            "jobs/podcast-2026-W26-aaaaaaaaaaaa/script.txt",
            "jobs/podcast-2026-W26-zzzzzzzzzzzz/script.txt",
            "jobs/podcast-2026-W27-newer/script.txt",
            "jobs/podcast-2026-W25-older/script.txt",
        ],
        scripts={
            "jobs/podcast-2026-W26-aaaaaaaaaaaa/manifest.json": manifest(
                "podcast-2026-W26-aaaaaaaaaaaa",
                "2026-W26",
                "2026-06-25T13:00:00Z",
            ),
            "jobs/podcast-2026-W26-zzzzzzzzzzzz/manifest.json": manifest(
                "podcast-2026-W26-zzzzzzzzzzzz",
                "2026-W26",
                "2026-06-25T11:00:00Z",
            ),
            "jobs/podcast-2026-W27-newer/manifest.json": manifest(
                "podcast-2026-W27-newer",
                "2026-W27",
                "2026-07-02T11:00:00Z",
            ),
            "jobs/podcast-2026-W25-older/manifest.json": manifest(
                "podcast-2026-W25-older",
                "2026-W25",
                "2026-06-18T11:00:00Z",
            ),
            "jobs/podcast-2026-W26-zzzzzzzzzzzz/script.txt": (
                "Source URL: https://example.com/historical-source\n---\n"
                "Theo: In this episode we will talk about: Historical pinned source.\n"
            ).encode(),
            "jobs/podcast-2026-W25-older/script.txt": (
                "Source URL: https://example.com/older-source\n---\n"
                "Theo: In this episode we will talk about: Older source.\n"
            ).encode(),
        },
    )

    themes = fetch_prior_episode_themes(
        storage,
        "podcast-2026-W26-current",
        current_week="2026-W26",
        current_created_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
    )

    assert "Historical pinned source" in themes
    assert "Older source" in themes
    assert not any("newer" in path for path in storage.read_paths if path.endswith("/script.txt"))
    assert "jobs/podcast-2026-W26-aaaaaaaaaaaa/script.txt" not in storage.read_paths


def test_run_generation_job_threads_auto_prior_episode_themes(monkeypatch, tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    captured: dict[str, object] = {}

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")
    monkeypatch.setattr(
        jobs,
        "fetch_prior_episode_themes",
        lambda storage_backend, job_id, **_kwargs: (
            "AI agents in enterprise",
            "Eval loops and guardrails",
        ),
    )

    def fake_generate_script(**kwargs) -> str:
        captured.update(kwargs)
        return "Title: Claracle Podcast – Week 2026-W28\n---\nTheo: Hello.\nVera: Hello.\n"

    monkeypatch.setattr(jobs, "generate_script", fake_generate_script)

    result = run_generation_job(
        {
            "week": "2026-W28",
            "article_url": "https://example.com/latest",
            "article_title": "Latest platform story",
            "article_content": VALID_ARTICLE_CONTENT,
            "script_directions": {
                "historical_context": {
                    "summary": "The hosts have tracked this market for weeks.",
                }
            },
        },
        storage=storage,
        now=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "accepted"
    assert captured["historical_context"] == HistoricalContext(
        summary="The hosts have tracked this market for weeks.",
        prior_episode_themes=("AI agents in enterprise", "Eval loops and guardrails"),
    )
