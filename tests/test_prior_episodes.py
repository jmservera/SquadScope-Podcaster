from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from podcaster import jobs
from podcaster.config import HistoricalContext
from podcaster.jobs import run_generation_job
from podcaster.prior_episodes import fetch_prior_episode_themes
from podcaster.storage import LocalStorageBackend

VALID_ARTICLE_CONTENT = (
    "This article covers a fresh platform release, the migration strategy behind it, the "
    "customer impact, and the operational lessons the hosts should react to on air this week."
)


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
    storage = _MockStorage(
        blobs=[
            "jobs/podcast-2026-W27-dddd/script.txt",
            "jobs/podcast-2026-W26-cccc/script.txt",
            "jobs/podcast-2026-W25-bbbb/script.txt",
            "jobs/podcast-2026-W24-aaaa/script.txt",
        ],
        scripts={
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

    themes = fetch_prior_episode_themes(storage, "podcast-2026-W27-dddd")

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
    assert storage.read_paths == [
        "jobs/podcast-2026-W26-cccc/script.txt",
        "jobs/podcast-2026-W25-bbbb/script.txt",
        "jobs/podcast-2026-W24-aaaa/script.txt",
    ]


def test_fetch_prior_episode_themes_gracefully_handles_listing_failures() -> None:
    storage = _MockStorage(blobs=[], fail_list=True)

    assert fetch_prior_episode_themes(storage, "podcast-2026-W27-dddd") == ()
    assert storage.read_paths == []


def test_run_generation_job_threads_auto_prior_episode_themes(monkeypatch, tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    captured: dict[str, object] = {}

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")
    monkeypatch.setattr(
        jobs,
        "fetch_prior_episode_themes",
        lambda storage_backend, job_id: ("AI agents in enterprise", "Eval loops and guardrails"),
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
