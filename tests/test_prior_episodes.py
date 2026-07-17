from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

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
        lambda storage_backend, job_id, **kwargs: (
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


# ---------------------------------------------------------------------------
# Pinned-replay regression contract (issue #609)
# ---------------------------------------------------------------------------


def test_prior_job_ids_excludes_future_jobs() -> None:
    """Replaying an older fixture must never pull in themes from newer episodes.

    _prior_job_ids should only return jobs whose ID sorts *before* the current
    job_id. Job IDs embed an ISO week (podcast-YYYY-WNN-hash) so lexicographic
    comparison faithfully reflects creation order.
    """
    storage = _MockStorage(
        blobs=[
            "jobs/podcast-2026-W26-future/script.txt",  # newer → must be excluded
            "jobs/podcast-2026-W25-current/script.txt",  # current → discarded
            "jobs/podcast-2026-W24-past/script.txt",  # older → must be included
        ],
        scripts={
            "jobs/podcast-2026-W24-past/script.txt": (
                "Title: Past Episode\n"
                "Source URL: https://example.com/past\n"
                "---\n"
                "Theo: In this episode we will talk about: Past replay topic.\n"
            ).encode("utf-8"),
            "jobs/podcast-2026-W26-future/script.txt": (
                "Title: Future Episode\n"
                "Source URL: https://example.com/future\n"
                "---\n"
                "Theo: In this episode we will talk about: Future topic that must not appear.\n"
            ).encode("utf-8"),
        },
    )

    themes = fetch_prior_episode_themes(storage, "podcast-2026-W25-current")

    assert any("Past replay topic" in t for t in themes), "older episode theme should appear"
    assert not any("Future" in t for t in themes), "newer episode theme must be excluded"
    # The future script was never read (excluded before I/O)
    assert "jobs/podcast-2026-W26-future/script.txt" not in storage.read_paths


def test_same_week_prior_jobs_use_manifest_timestamps_not_hash_order() -> None:
    earlier_job = "podcast-2026-W25-zzzzzzzzzzzz"
    later_job = "podcast-2026-W25-aaaaaaaaaaaa"
    storage = _MockStorage(
        blobs=[
            f"jobs/{later_job}/script.txt",
            f"jobs/{earlier_job}/script.txt",
        ],
        scripts={
            f"jobs/{earlier_job}/manifest.json": (
                '{"status":"accepted","created_at":"2026-06-16T09:00:00Z"}'
            ).encode(),
            f"jobs/{later_job}/manifest.json": (
                '{"status":"accepted","created_at":"2026-06-16T11:00:00Z"}'
            ).encode(),
            f"jobs/{earlier_job}/script.txt": (
                "Title: Earlier Episode\n"
                "Source URL: https://example.com/earlier\n"
                "---\n"
                "Theo: In this episode we will talk about: Earlier same-week topic.\n"
            ).encode(),
            f"jobs/{later_job}/script.txt": (
                "Title: Later Episode\n"
                "Source URL: https://example.com/later\n"
                "---\n"
                "Theo: In this episode we will talk about: Later same-week topic.\n"
            ).encode(),
        },
    )

    themes = fetch_prior_episode_themes(
        storage,
        "podcast-2026-W25-mmmmmmmmmmmm",
        current_created_at=datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc),
    )

    assert any("Earlier same-week topic" in theme for theme in themes)
    assert not any("Later same-week topic" in theme for theme in themes)
    assert f"jobs/{later_job}/script.txt" not in storage.read_paths


def test_build_job_id_changes_with_content_and_config() -> None:
    """Changing content or replay-relevant config must change job identity;
    identical pinned inputs must produce the same job_id (idempotent)."""
    from podcaster.jobs import build_job_id

    base = {"week": "2026-W28", "article_url": "https://example.com/a"}
    with_sha_a = dict(base, article_sha256="a" * 64)
    with_sha_b = dict(base, article_sha256="b" * 64)
    with_config = dict(base, podcast_config={"name": "OtherShow"})
    with_title = dict(base, article_title="A distinct pinned title")
    with_breaking_news = dict(base, breaking_news="A distinct pinned update")

    # Same inputs → same job_id
    assert build_job_id(with_sha_a) == build_job_id(dict(with_sha_a))
    # Different content → different job_id
    assert build_job_id(with_sha_a) != build_job_id(with_sha_b)
    # Different config → different job_id
    assert build_job_id(base) != build_job_id(with_config)
    assert build_job_id(base) != build_job_id(with_title)
    assert build_job_id(base) != build_job_id(with_breaking_news)
    # All start with the expected week prefix
    for payload in (base, with_sha_a, with_sha_b, with_config, with_title, with_breaking_news):
        assert build_job_id(payload).startswith("podcast-2026-W28-")


def test_article_sha256_is_computed_from_content_bytes(monkeypatch, tmp_path: Path) -> None:
    """When article_content is provided the manifest's article_sha256 must cover
    the actual content bytes — not a caller-supplied hash of different bytes."""
    import hashlib

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")
    monkeypatch.setattr(
        jobs, "generate_script", lambda **kw: "Title: T\n---\nTheo: Hello.\nVera: Hello.\n"
    )

    content = VALID_ARTICLE_CONTENT
    expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # Caller supplies a wrong sha (e.g. hash of a summary, not the full article)
    wrong_sha = "w" * 64

    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    result = run_generation_job(
        {
            "week": "2026-W29",
            "article_url": "https://example.com/article",
            "article_title": "Test article",
            "article_content": content,
            "article_sha256": wrong_sha,  # deliberately mismatched
        },
        storage=storage,
        now=datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "accepted"
    assert result.manifest["request"]["article_sha256"] == expected_sha, (
        "manifest must store the hash computed from the actual content bytes, not the caller's"
    )


def test_replay_collision_is_refused(monkeypatch, tmp_path: Path) -> None:
    """Existing replay outputs must not be silently overwritten.

    Submitting the same pinned inputs a second time must raise ReplayCollisionError
    so callers are forced to acknowledge the collision rather than silently losing
    the original artifacts.
    """
    from podcaster.jobs import ReplayCollisionError

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")
    monkeypatch.setattr(
        jobs, "generate_script", lambda **kw: "Title: T\n---\nTheo: Hello.\nVera: Hello.\n"
    )

    payload = {
        "week": "2026-W30",
        "article_url": "https://example.com/replay-article",
        "article_title": "Replay test",
        "article_content": VALID_ARTICLE_CONTENT,
    }
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")

    # First submission succeeds
    result = run_generation_job(
        payload,
        storage=storage,
        now=datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert result.response["status"] == "accepted"

    # Second submission with identical inputs must be refused
    with pytest.raises(ReplayCollisionError) as exc_info:
        run_generation_job(
            payload,
            storage=storage,
            now=datetime(2026, 7, 14, 13, 0, 0, tzinfo=timezone.utc),
        )
    assert "replay collision" in str(exc_info.value).lower()
