"""Tests for podcaster.video.job_runner (#242)."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcaster.queue import QueueMessage
from podcaster.video.distribution import DistributionResult, VideoDistributionConfig
from podcaster.video.job_runner import (
    _DEFAULT_MUSIC_CREDITS,
    MAX_DEQUEUE_COUNT,
    REASON_ALREADY_PROCESSED,
    REASON_EDITOR_LEASE_HELD,
    REASON_NO_REPOS,
    REASON_REQUIRED_YOUTUBE_FAILURE,
    REASON_RETRY_EXHAUSTED,
    REASON_WATERMARK_UNAVAILABLE,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    PermanentVideoError,
    TransientVideoError,
    VideoOutcome,
    _already_processed,
    _build_section_cards,
    _build_video_description,
    _resolve_anchor_id,
    _resolve_dog_logo,
    _resolve_video_title,
    _section_card_duration_seconds,
    drain,
    manifest_path,
    process_message,
    removed_repos_notes_path,
    run_video_generation,
    script_path,
    show_notes_path,
    video_artifact_path,
)


@pytest.fixture(autouse=True)
def _no_repo_removal_probe():
    """Skip the network HEAD pre-flight so video-runner tests stay hermetic.

    The whisper forced-alignment path (and its transcription patch) is gone:
    timing now comes from the Layer 2 realized-audio metadata persisted at
    synthesis (#553), and these tests assert the no-metadata fallback plan.
    """
    with patch("podcaster.video.sync_plan.check_repo_removed", return_value=False):
        yield


def test_video_runner_does_not_import_audio_align():
    """The video runner must not depend on whisper forced alignment (#553/#551).

    Asserts the module exposes no ``audio_align`` reference and that importing
    the deleted module fails — proof the whisper path is fully removed.
    """
    import importlib

    from podcaster.video import job_runner as vjr

    source = Path(vjr.__file__).read_text(encoding="utf-8")
    assert "audio_align" not in source
    assert "plan_from_script_aligned" not in source
    assert not hasattr(vjr, "plan_from_script_aligned")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("podcaster.video.audio_align")


class FakeStorage:
    """In-memory storage backend for testing."""

    def __init__(self):
        self._data: dict[str, bytes] = {}
        self.puts: list[tuple[str, bytes, str]] = []

    def get_bytes(self, path: str) -> bytes | None:
        return self._data.get(path)

    def put_bytes(self, path: str, content: bytes, content_type: str):
        self._data[path] = content
        self.puts.append((path, content, content_type))
        return MagicMock(path=path, url=f"https://blob/{path}", size_bytes=len(content))

    def update_bytes(self, path: str, content_type: str, update):
        existing = self._data.get(path)
        self._data[path] = update(existing)

    def set_manifest(self, job_id: str, manifest: dict):
        self._data[manifest_path(job_id)] = json.dumps(manifest).encode()

    def set_script(self, job_id: str, script: str):
        self._data[script_path(job_id)] = script.encode()


class FakeQueue:
    """In-memory queue backend for testing."""

    def __init__(self, messages: list[QueueMessage] | None = None):
        self._messages = list(messages or [])
        self.deleted: list[QueueMessage] = []

    def receive_messages(self, max_messages: int = 1, *, visibility_timeout: int = 600):
        if self._messages:
            return [self._messages.pop(0)]
        return []

    def delete_message(self, message: QueueMessage):
        self.deleted.append(message)


def _make_message(job_id: str, dequeue_count: int = 1) -> QueueMessage:
    import base64

    body = base64.b64encode(
        json.dumps({"schema_version": "v1", "job_id": job_id}).encode()
    ).decode()
    return QueueMessage(
        message_id=f"msg-{job_id}",
        pop_receipt=f"pop-{job_id}",
        body=body,
        dequeue_count=dequeue_count,
    )


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture(autouse=True)
def _no_network_removed_check():
    """Default removed-repo pre-flight (issue #394) to a no-op so unit tests
    never make real HEAD requests to github.com.  Individual tests can still
    patch ``check_repo_removed`` to exercise the removed-repo path."""
    with patch("podcaster.video.sync_plan.check_repo_removed", return_value=False):
        yield


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def dry_config() -> VideoDistributionConfig:
    return VideoDistributionConfig(
        youtube_enabled=True,
        spotify_rss_enabled=True,
        blob_archive_enabled=True,
        dry_run=True,
    )


SAMPLE_SCRIPT = """\
# Weekly Podcast - 2026-W25

This week we feature some great repos:
- https://github.com/microsoft/vscode
- https://github.com/facebook/react

HOST_A: Welcome to the show!
HOST_B: Let's dive in.
HOST_A: First up, VS Code has a major update.
HOST_B: Amazing work by the team.
"""


# --- Path Helper Tests ---


class TestPaths:
    def test_manifest_path(self):
        assert manifest_path("j1") == "jobs/j1/manifest.json"

    def test_script_path(self):
        assert script_path("j1") == "jobs/j1/script.txt"

    def test_video_artifact_path(self):
        assert video_artifact_path("j1") == "jobs/j1/video/j1.mp4"

    def test_show_notes_path(self):
        assert show_notes_path("j1") == "jobs/j1/show-notes.md"


# --- Video Description Builder Tests (#363) ---


class TestVideoDescription:
    def _storage(self, job_id: str, notes: str | None):
        s = MagicMock()
        data = {show_notes_path(job_id): notes.encode("utf-8")} if notes is not None else {}
        s.get_bytes.side_effect = lambda path: data.get(path)
        return s

    def test_falls_back_when_no_show_notes(self):
        storage = self._storage("j", None)
        desc = _build_video_description(storage, "j", "fallback desc")
        # Fallback text is still present; music credits are always appended
        assert "fallback desc" in desc
        assert _DEFAULT_MUSIC_CREDITS in desc

    def test_includes_summary_and_credits_packaging_format(self):
        notes = (
            "# Claracle — Week W24\n\n## Title\n\n"
            "**Hosts:** Theo (fable) & Vera (alloy)\n\n"
            "### About this episode\n\nA dynamic AI conversation.\n\n"
            "### Links\n\n- x\n"
        )
        storage = self._storage("j", notes)
        desc = _build_video_description(storage, "j", "fallback")
        expected_credits = (
            "Credits: Hosts: Theo (fable) & Vera (alloy) · Claracle — www.claracle.com"
        )
        assert desc == "\n\n".join(
            ["A dynamic AI conversation.", expected_credits, _DEFAULT_MUSIC_CREDITS]
        )
        assert _DEFAULT_MUSIC_CREDITS in desc

    def test_includes_summary_generation_format(self):
        notes = (
            "# Claracle Podcast — Week W24\n\n"
            "**Hosts:** Two AI voices — Theo and Vera\n\n"
            "## Show notes\n\nClaracle is a weekly show about open source.\n\n"
            "### Segment 1\n\n- detail\n"
        )
        storage = self._storage("j", notes)
        desc = _build_video_description(storage, "j", "fallback")
        assert "Claracle is a weekly show about open source." in desc
        assert "Segment 1" not in desc
        assert "www.claracle.com" in desc
        assert _DEFAULT_MUSIC_CREDITS in desc

    def test_uses_fallback_summary_when_no_section(self):
        notes = "# Heading only\n\nsome stray text\n"
        storage = self._storage("j", notes)
        desc = _build_video_description(storage, "j", "fallback summary")
        assert desc.startswith("fallback summary")
        assert "www.claracle.com" in desc
        assert _DEFAULT_MUSIC_CREDITS in desc

    def test_custom_music_credits_override(self):
        """Custom music_credits parameter overrides the default attribution."""
        notes = "# Title\n\n### About this episode\n\nSummary text.\n"
        storage = self._storage("j", notes)
        desc = _build_video_description(storage, "j", "fallback", music_credits="My Custom Credits")
        assert "My Custom Credits" in desc
        # Default attribution must NOT appear when custom credits provided
        assert _DEFAULT_MUSIC_CREDITS not in desc

    def test_configured_show_name_in_credits(self):
        """Issue #545: the brand credit line honors the per-job podcast_config
        show name / spoken site instead of the hardcoded defaults."""
        notes = "# Title\n\n**Hosts:** Ada & Bo\n\n### About this episode\n\nSummary text.\n"
        storage = self._storage("j", notes)
        desc = _build_video_description(
            storage, "j", "fallback", show_name="My Show", spoken_site="myshow.example"
        )
        assert "My Show — myshow.example" in desc
        assert "Claracle — www.claracle.com" not in desc

    def test_blank_show_name_falls_back_to_default(self):
        """Empty config values fall back to the module brand defaults."""
        notes = "# Title\n\n### About this episode\n\nSummary text.\n"
        storage = self._storage("j", notes)
        desc = _build_video_description(storage, "j", "fallback", show_name="", spoken_site="  ")
        assert "Claracle — www.claracle.com" in desc

    def test_generic_show_notes_reuse_audio_publish_description(self):
        notes = (
            "# Claracle Podcast — Week 2026-W30\n\n"
            "**Hosts:** Theo & Vera\n\n"
            "## Show notes\n\n"
            "This episode covers key developments from the SquadScope curated articles "
            "for this week.\n\n"
            "### Segment 1: [Topic to be added from source article]\n\n"
            "- **Article:** [Title TBD](https://example.com) — Editorial synopsis pending\n"
        )
        storage = self._storage("j", notes)
        desc = _build_video_description(
            storage,
            "j",
            "generic fallback",
            preferred_description="<p>Claracle explores the week's agent tooling article.</p>",
        )
        assert "Claracle explores the week's agent tooling article." in desc
        assert "SquadScope curated articles" not in desc
        assert "Claracle — www.claracle.com" in desc

    def test_title_template_show_notes_reuse_audio_publish_description(self):
        notes = (
            "# Claracle Podcast — Week 2026-W30\n\n"
            "**Hosts:** Theo & Vera\n\n"
            "## Show notes\n\n"
            "This Claracle episode explores AI agents reshape delivery, highlighting the "
            "open-source developments, repo activity, and practical signals that matter "
            "this week.\n\n"
            "### Segment 1: AI agents reshape delivery\n"
        )
        storage = self._storage("j", notes)
        desc = _build_video_description(
            storage,
            "j",
            "generic fallback",
            preferred_description="Agent tooling kept hardening into products.",
        )
        assert "Agent tooling kept hardening into products." in desc
        assert "This Claracle episode explores" not in desc
        assert "Claracle — www.claracle.com" in desc

    def test_preferred_spotify_description_is_plain_text_and_capped(self):
        notes = (
            "# Claracle Podcast — Week 2026-W30\n\n"
            "## Show notes\n\n"
            "This Claracle episode explores AI agents reshape delivery, highlighting the "
            "open-source developments, repo activity, and practical signals that matter "
            "this week.\n"
        )
        storage = self._storage("j", notes)
        desc = _build_video_description(
            storage,
            "j",
            "generic fallback",
            preferred_description="<p>" + ("A" * 700) + "&lt;script&gt;</p>",
        )
        first_paragraph = desc.split("\n\n", 1)[0]
        assert len(first_paragraph) <= 600
        assert "<p>" not in desc
        assert "<script>" not in desc
        assert "&lt;script&gt;" not in desc

    def test_video_title_prefers_audio_publish_title(self):
        title, used_default = _resolve_video_title(
            {
                "article_title": "Request Article Title",
                "spotify_publish": {"title": "Audio Episode Title"},
            },
            brand_name="Claracle",
            job_id="job-1",
        )
        assert title == "Audio Episode Title"
        assert used_default is False

    # --- Already Processed Tests ---

    def test_preferred_description_overrides_real_show_notes_content(self):
        """preferred_description takes priority over show-notes summary content
        (decision 2026-07-25: video must reuse audio/Spotify publish description)."""
        notes = (
            "# Claracle Podcast — Week 2026-W35\n\n"
            "**Hosts:** Clarabel (nova) & Joracle (alloy)\n\n"
            "## Show notes\n\n"
            "Claracle is a weekly show. For every issue, extended write-ups, repo links, and\n"
            "commented articles, visit https://www.claracle.com.\n\n"
            "Agent skills spread into devices while trust became the bottleneck.\n"
            "Two AI hosts share a joyful, dynamic expert conversation on the most relevant and "
            "surprising parts of the article — they do not read it verbatim.\n"
        )
        storage = self._storage("w35", notes)
        desc = _build_video_description(
            storage,
            "w35",
            "generic fallback",
            preferred_description=(
                "Agent skills spread into design, security, and devices while trust, "
                "provenance, and review boundaries became the real bottleneck. "
                "Claracle is your tech weekly."
            ),
        )
        # Canonical audio description is used directly
        expected_credits = (
            "Credits: Hosts: Clarabel (nova) & Joracle (alloy) · Claracle — www.claracle.com"
        )
        assert desc == "\n\n".join(
            [
                "Agent skills spread into design, security, and devices while trust, "
                "provenance, and review boundaries became the real bottleneck. "
                "Claracle is your tech weekly.",
                expected_credits,
                _DEFAULT_MUSIC_CREDITS,
            ]
        )
        # Generic show-notes boilerplate must NOT appear in body
        assert "for every issue, extended write-ups" not in desc.lower()
        assert "two ai hosts share a joyful" not in desc.lower()
        assert _DEFAULT_MUSIC_CREDITS in desc

    def test_preferred_description_never_yields_empty(self):
        """When preferred_description is provided the result is always non-empty
        even when storage returns nothing (regression guard)."""
        storage = self._storage("j", None)
        desc = _build_video_description(
            storage,
            "j",
            "",
            preferred_description="Audio episode description.",
        )
        assert desc.strip() != ""
        assert "Audio episode description." in desc

    def test_generic_show_notes_new_patterns_caught_without_preferred(self):
        """New generic show-notes phrases are caught by _is_generic_episode_summary
        when no preferred_description is supplied; falls back to fallback text."""
        notes = (
            "## Show notes\n\n"
            "Claracle is a weekly show. For every issue, extended write-ups, repo links, and\n"
            "commented articles, visit https://www.claracle.com.\n\n"
            "Agent skills spread. Two AI hosts share a joyful, dynamic expert "
            "conversation on the most relevant and surprising parts of the "
            "article — they do not read it verbatim.\n"
        )
        storage = self._storage("j", notes)
        desc = _build_video_description(
            storage,
            "j",
            "Fallback canonical description used when generic.",
        )
        assert "Fallback canonical description used when generic." in desc
        assert "for every issue, extended write-ups" not in desc.lower()
        assert "two ai hosts share a joyful" not in desc.lower()

    def test_audio_video_description_parity_structure(self):
        """Video description built from preferred_description has same canonical
        body as the audio Spotify description (structural parity test)."""
        audio_body = "Agent skills: the week's signal. Claracle is your tech weekly."
        storage = self._storage("j", None)
        desc = _build_video_description(storage, "j", "fallback", preferred_description=audio_body)
        expected_credits = "Credits: Claracle — www.claracle.com"
        assert desc == "\n\n".join([audio_body, expected_credits, _DEFAULT_MUSIC_CREDITS])


class TestAlreadyProcessed:
    def test_not_processed(self):
        assert _already_processed({}) is False
        assert _already_processed({"generation": {}}) is False

    def test_completed(self):
        manifest = {"generation": {"video_runner": {"status": "completed"}}}
        assert _already_processed(manifest) is True

    def test_failed_not_blocking(self):
        manifest = {"generation": {"video_runner": {"status": "failed"}}}
        assert _already_processed(manifest) is False


# --- Run Video Generation Tests ---


class TestRunVideoGeneration:
    def test_no_manifest_raises_transient(self, storage, dry_config):
        with pytest.raises(TransientVideoError, match="no manifest"):
            run_video_generation("missing-job", storage, config=dry_config)

    def test_invalid_manifest_raises_transient(self, storage, dry_config):
        storage._data[manifest_path("bad")] = b"not json"
        with pytest.raises(TransientVideoError, match="invalid manifest"):
            run_video_generation("bad", storage, config=dry_config)

    def test_already_processed_skips(self, storage, dry_config):
        storage.set_manifest(
            "done-job",
            {
                "generation": {"video_runner": {"status": "completed"}},
            },
        )
        outcome = run_video_generation("done-job", storage, config=dry_config)
        assert outcome.status == STATUS_SKIPPED
        assert outcome.reason == REASON_ALREADY_PROCESSED

    def test_no_script_raises_transient(self, storage, dry_config):
        storage.set_manifest("no-script", {"generation": {}})
        with pytest.raises(TransientVideoError, match="no script"):
            run_video_generation("no-script", storage, config=dry_config)

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_no_repos_generates_generic_video(self, mock_compose, mock_record, storage, dry_config):
        """Scripts without GitHub repos still produce a video (issue #335)."""
        storage.set_manifest("no-repos", {"generation": {}})
        storage.set_script("no-repos", "Just a plain script with no GitHub URLs")

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=300.0,
                segment_count=1,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose

        outcome = run_video_generation("no-repos", storage, config=dry_config)
        assert outcome.status == STATUS_COMPLETED
        assert outcome.reason != REASON_NO_REPOS
        # Generic plan should have been recorded
        plan = mock_record.call_args.args[0]
        assert len(plan.segments) == 1
        assert plan.segments[0].is_generic

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_pinned_replay_article_drives_video_without_live_url_fetch(
        self,
        mock_compose,
        mock_record,
        storage,
        dry_config,
        monkeypatch,
    ):
        job_id = "podcast-2026-W25-pinned"
        article_path = f"jobs/{job_id}/inputs/article.txt"
        article_bytes = (
            b"Pinned article bytes referencing https://github.com/microsoft/vscode exactly."
        )
        storage.put_bytes(article_path, article_bytes, "text/plain; charset=utf-8")
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {
                    "article_title": "Pinned Episode",
                    "replay": {
                        "article_path": article_path,
                        "article_sha256": hashlib.sha256(article_bytes).hexdigest(),
                    },
                },
            },
        )
        storage.set_script(
            job_id,
            "Source URL: https://claracle.com/weekly/2026/w25/\n---\n"
            "HOST_A: This script intentionally has no repository URL.\n",
        )
        monkeypatch.setattr(
            "podcaster.video.sync_plan.fetch_repos_from_article",
            lambda _url: (_ for _ in ()).throw(AssertionError("live article fetched")),
        )

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=len(segments),
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose

        outcome = run_video_generation(job_id, storage, config=dry_config)

        assert outcome.status == STATUS_COMPLETED
        plan = mock_record.call_args.args[0]
        assert any(
            segment.repo is not None and segment.repo.url == "https://github.com/microsoft/vscode"
            for segment in plan.segments
        )
        # The opening weekly page is a deterministic public URL (derived from the
        # job_id) and is still shown for pinned/replay jobs — only the live
        # article-*content* fetch is suppressed for reproducibility (#612). The
        # weekly segment therefore carries the lowercase weekly URL; repo
        # segments carry none.
        weekly_url = "https://claracle.com/weekly/2026/w25/"
        assert plan.segments[0].source_url == weekly_url
        assert all(segment.source_url is None for segment in plan.segments[1:])
        # The ``record_episode(source_url=...)`` kwarg (distinct from the
        # per-segment ``source_url`` asserted above) is the article-content URL
        # used for live repo correction; it stays None for pinned jobs, so no
        # live article-content fetch occurs.
        assert mock_record.call_args.kwargs["source_url"] is None

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_successful_generation(self, mock_compose, mock_record, storage, dry_config, tmp_path):
        job_id = "video-ok"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "Test Episode"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        # Mock record_episode
        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        # Mock compose_video to create a fake output file
        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose

        outcome = run_video_generation(job_id, storage, config=dry_config)
        assert outcome.status == STATUS_COMPLETED
        assert outcome.segment_count == 2
        assert outcome.distribution is not None
        assert outcome.distribution.youtube_id == "dry-run-id"

        # Per-phase performance breakdown is persisted to the manifest (#396).
        manifest = json.loads(storage.get_bytes(manifest_path(job_id)).decode())
        perf = manifest["generation"]["video_runner"]["performance"]
        phase_names = {p["name"] for p in perf["phases"]}
        assert {"recording", "composition", "distribution"} <= phase_names
        assert perf["total_wall_seconds"] >= 0.0

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_required_youtube_transient_failure_is_retryable(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config
    ):
        job_id = "video-required-youtube-transient"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "Test Episode"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording
        mock_compose.side_effect = lambda *a, output_path=None, **k: (
            output_path.write_bytes(b"\x00" * 2048) if output_path else None,
            MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            ),
        )[1]
        mock_distribute.return_value = DistributionResult(
            status="failed",
            blob_path="https://blob/video.mp4",
            errors=["YouTube token refresh failed: HTTP 503"],
            youtube_required_failed=True,
            youtube_failure_retryable=True,
            youtube_failure_code="youtube_oauth_http_503",
            youtube_failure_stage="oauth_token",
            youtube_failure_http_status=503,
        )

        with pytest.raises(TransientVideoError, match="required YouTube delivery failed"):
            run_video_generation(job_id, storage, config=dry_config)
        manifest = json.loads(storage.get_bytes(manifest_path(job_id)).decode())
        state = manifest["generation"]["video_runner"]
        assert state["status"] == STATUS_FAILED
        assert state["reason"] == REASON_REQUIRED_YOUTUBE_FAILURE
        assert state["distribution"]["youtube_failure_retryable"] is True

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_required_youtube_oauth_failure_is_permanent(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config
    ):
        job_id = "video-required-youtube-permanent"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "Test Episode"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording
        mock_compose.side_effect = lambda *a, output_path=None, **k: (
            output_path.write_bytes(b"\x00" * 2048) if output_path else None,
            MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            ),
        )[1]
        mock_distribute.return_value = DistributionResult(
            status="failed",
            blob_path="https://blob/video.mp4",
            errors=["YouTube token refresh failed: HTTP 400"],
            youtube_required_failed=True,
            youtube_failure_retryable=False,
            youtube_failure_code="youtube_oauth_invalid_grant",
            youtube_failure_stage="oauth_token",
            youtube_failure_http_status=400,
            youtube_oauth_error="invalid_grant",
            youtube_oauth_error_subtype="invalid_rapt",
        )

        with pytest.raises(PermanentVideoError, match="required YouTube delivery failed"):
            run_video_generation(job_id, storage, config=dry_config)
        manifest = json.loads(storage.get_bytes(manifest_path(job_id)).decode())
        state = manifest["generation"]["video_runner"]
        assert state["status"] == STATUS_FAILED
        assert state["reason"] == REASON_REQUIRED_YOUTUBE_FAILURE
        assert state["distribution"]["youtube_oauth_error_subtype"] == "invalid_rapt"

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_removed_repo_annotated_and_notes_persisted(
        self, mock_compose, mock_record, storage, dry_config
    ):
        """Removed repos are flagged before recording and speaker cues persisted (#394)."""
        job_id = "video-removed"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "Test Episode"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose

        # facebook/react is "removed"; microsoft/vscode is present.
        def fake_removed(url, timeout=5.0):
            return "facebook/react" in url

        with patch("podcaster.video.sync_plan.check_repo_removed", side_effect=fake_removed):
            outcome = run_video_generation(job_id, storage, config=dry_config)

        assert outcome.status == STATUS_COMPLETED

        # The recorded plan carries the removed annotation, so the recorder
        # skips navigation for the dead repo.
        plan = mock_record.call_args.args[0]
        removed = [s for s in plan.segments if s.removed_reason is not None]
        assert len(removed) == 1
        assert removed[0].repo.name == "react"

        # Speaker cues for the removed repo are persisted as an artifact.
        notes = storage.get_bytes(removed_repos_notes_path(job_id))
        assert notes is not None
        text = notes.decode("utf-8")
        assert "facebook/react" in text
        assert "removed from GitHub" in text

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_no_removed_notes_when_all_present(
        self, mock_compose, mock_record, storage, dry_config
    ):
        """No removed-repo artifact is written when every repo is present (#394)."""
        job_id = "video-allpresent"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "Test Episode"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose

        outcome = run_video_generation(job_id, storage, config=dry_config)
        assert outcome.status == STATUS_COMPLETED
        assert storage.get_bytes(removed_repos_notes_path(job_id)) is None

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_description_from_show_notes(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config
    ):
        """The video description is built from show-notes with summary + credits (#363)."""
        job_id = "video-notes"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "Notes Episode"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)
        storage._data[show_notes_path(job_id)] = (
            "# Claracle — Week 2026-W24\n\n"
            "## My Episode\n\n"
            "**Hosts:** Theo (fable) & Vera (alloy)\n\n"
            "### About this episode\n\n"
            "A joyful conversation about open source.\n\n"
            "### Links\n\n- https://www.claracle.com\n"
        ).encode("utf-8")

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = DistributionResult(
            status="completed",
            youtube_id=None,
            blob_path=None,
            spotify_rss_updated=False,
            spotify_upload_updated=False,
        )

        run_video_generation(job_id, storage, config=dry_config)

        description = mock_distribute.call_args.args[3]
        assert "A joyful conversation about open source." in description
        assert "Hosts: Theo (fable) & Vera (alloy)" in description
        assert "Claracle" in description
        assert "www.claracle.com" in description
        # Music credits must be present (default attribution from TRACK_ATTRIBUTION)
        assert _DEFAULT_MUSIC_CREDITS in description

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_default_title_and_identity_warn_when_config_absent(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config, caplog
    ):
        """Issue #545: a render with no article_title/podcast_config falls back to
        defaults and logs both, so the operator can tell config was missing."""
        job_id = "video-no-config"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path, duration_seconds=60.0, segment_count=2, has_audio=False
            )

        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = DistributionResult(
            status="completed",
            youtube_id=None,
            blob_path=None,
            spotify_rss_updated=False,
            spotify_upload_updated=False,
        )

        with caplog.at_level(logging.WARNING):
            run_video_generation(job_id, storage, config=dry_config)

        title = mock_distribute.call_args.args[2]
        assert title == f"Claracle Podcast — {job_id}"
        assert any("article_title absent" in r.getMessage() for r in caplog.records)
        assert any("podcast_config identity absent" in r.getMessage() for r in caplog.records)

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_configured_title_and_identity_no_warning(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config, caplog
    ):
        """Issue #545: when the request supplies article_title + podcast_config
        identity, the configured title is used and no default-fallback warning."""
        job_id = "video-with-config"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {
                    "article_title": "Configured Episode Title",
                    "podcast_config": {
                        "name": "My Show",
                        "host_a": {"name": "Ada"},
                        "host_b": {"name": "Bo"},
                    },
                },
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path, duration_seconds=60.0, segment_count=2, has_audio=False
            )

        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = DistributionResult(
            status="completed",
            youtube_id=None,
            blob_path=None,
            spotify_rss_updated=False,
            spotify_upload_updated=False,
        )

        with caplog.at_level(logging.WARNING):
            run_video_generation(job_id, storage, config=dry_config)

        title = mock_distribute.call_args.args[2]
        assert title == "Configured Episode Title"
        assert not any("article_title absent" in r.getMessage() for r in caplog.records)
        assert not any("podcast_config identity absent" in r.getMessage() for r in caplog.records)

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_video_distribution_reuses_audio_publish_metadata(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config, caplog
    ):
        """Video metadata must follow the audio publish title/summary when present."""
        job_id = "video-audio-metadata"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {
                    "article_title": "Raw Article Title",
                    "spotify_publish": {
                        "title": "Audio Episode Title",
                        "description": (
                            "<p>Claracle analyzes the week's article about agent workflows.</p>"
                        ),
                    },
                },
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)
        storage._data[show_notes_path(job_id)] = (
            "# Claracle Podcast — Week 2026-W30\n\n"
            "## Show notes\n\n"
            "This episode covers key developments from the SquadScope curated articles "
            "for this week.\n"
        ).encode("utf-8")

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path, duration_seconds=60.0, segment_count=2, has_audio=False
            )

        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = DistributionResult(
            status="completed",
            youtube_id=None,
            blob_path=None,
            spotify_rss_updated=False,
            spotify_upload_updated=False,
        )

        with caplog.at_level(logging.WARNING):
            run_video_generation(job_id, storage, config=dry_config)

        title = mock_distribute.call_args.args[2]
        description = mock_distribute.call_args.args[3]
        assert title == "Audio Episode Title"
        assert "Claracle analyzes the week's article about agent workflows." in description
        assert "SquadScope curated articles" not in description
        assert not any("article_title absent" in r.getMessage() for r in caplog.records)

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_description_uses_description_template_as_music_credits(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config
    ):
        """description_template from the request is appended as music credits (#412)."""
        job_id = "video-template"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {
                    "article_title": "Template Episode",
                    "week": "2026-W24",
                    "description_template": "Custom music credit from SquadScope config",
                },
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = DistributionResult(
            status="completed",
            youtube_id=None,
            blob_path=None,
            spotify_rss_updated=False,
            spotify_upload_updated=False,
        )

        run_video_generation(job_id, storage, config=dry_config)

        description = mock_distribute.call_args.args[3]
        assert "Custom music credit from SquadScope config" in description

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_malformed_request_falls_back_to_default_credits(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config
    ):
        """A non-string description_template is ignored (no crash) and the default
        music attribution is used instead (#412 review hardening)."""
        job_id = "video-malformed"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {
                    "article_title": "Malformed Episode",
                    "week": "2026-W24",
                    "description_template": {"unexpected": "object"},
                },
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = DistributionResult(
            status="completed",
            youtube_id=None,
            blob_path=None,
            spotify_rss_updated=False,
            spotify_upload_updated=False,
        )

        run_video_generation(job_id, storage, config=dry_config)

        description = mock_distribute.call_args.args[3]
        assert _DEFAULT_MUSIC_CREDITS in description

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_season_episode_numbers_from_manifest_week(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config
    ):
        """Season (year) and episode (week) are resolved from manifest and
        passed to distribute_video (#412)."""
        job_id = "video-season"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {
                    "article_title": "Season Episode",
                    "week": "2026-W24",
                },
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = DistributionResult(
            status="completed",
            youtube_id=None,
            blob_path=None,
            spotify_rss_updated=False,
            spotify_upload_updated=False,
        )

        run_video_generation(job_id, storage, config=dry_config)

        kwargs = mock_distribute.call_args.kwargs
        assert kwargs.get("season_number") == 2026
        assert kwargs.get("episode_number") == 24

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_season_episode_none_when_no_week(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config
    ):
        """When no week is in the manifest, season/episode are None (#412)."""
        job_id = "video-noweek"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "No Week"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = DistributionResult(
            status="completed",
            youtube_id=None,
            blob_path=None,
            spotify_rss_updated=False,
            spotify_upload_updated=False,
        )

        run_video_generation(job_id, storage, config=dry_config)

        kwargs = mock_distribute.call_args.kwargs
        assert kwargs.get("season_number") is None
        assert kwargs.get("episode_number") is None

    @patch("podcaster.video.job_runner._probe_audio_duration")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_plan_driven_by_probed_audio_duration(
        self, mock_compose, mock_record, mock_probe, storage, dry_config
    ):
        """The segment plan uses the REAL MP3 duration, not the manifest value (#353)."""
        job_id = "video-dur"
        # Manifest says 300s, but the actual MP3 probes at 123s.
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 300.0}},
                "request": {"article_title": "Dur Test"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)
        # Provide the audio blob so _resolve_audio_path returns a path.
        storage._data[f"jobs/{job_id}/audio/{job_id}.mp3"] = b"\x00" * 16
        mock_probe.return_value = 123.0

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=123.0,
                segment_count=2,
                has_audio=True,
            )

        mock_compose.side_effect = fake_compose

        outcome = run_video_generation(job_id, storage, config=dry_config)
        assert outcome.status == STATUS_COMPLETED
        mock_probe.assert_called_once()
        # The plan total duration must reflect the probed 123s, not 300s.
        plan = mock_record.call_args.args[0]
        assert plan.total_duration_seconds == pytest.approx(123.0)

    @patch("podcaster.video.job_runner._probe_audio_duration")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_plan_falls_back_to_manifest_duration(
        self, mock_compose, mock_record, mock_probe, storage, dry_config
    ):
        """When the MP3 cannot be probed, the manifest duration is used (#353)."""
        job_id = "video-fallback"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 222.0}},
                "request": {"article_title": "Fallback"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)
        storage._data[f"jobs/{job_id}/audio/{job_id}.mp3"] = b"\x00" * 16
        mock_probe.return_value = None  # probe failed

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=222.0,
                segment_count=2,
                has_audio=True,
            )

        mock_compose.side_effect = fake_compose

        outcome = run_video_generation(job_id, storage, config=dry_config)
        assert outcome.status == STATUS_COMPLETED
        plan = mock_record.call_args.args[0]
        assert plan.total_duration_seconds == pytest.approx(222.0)

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_ffmpeg_failure_logs_stderr(
        self, mock_compose, mock_record, storage, dry_config, caplog
    ):
        """A CalledProcessError surfaces ffmpeg's stderr in the failure log (#blind-debug)."""
        import logging
        import subprocess

        job_id = "video-ffmpeg-fail"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "Test Episode"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        mock_compose.side_effect = subprocess.CalledProcessError(
            255,
            ["ffmpeg", "-i", "joined.mp4", "muxed.mp4"],
            output="",
            stderr="av_interleaved_write_frame(): No space left on device",
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(TransientVideoError):
                run_video_generation(job_id, storage, config=dry_config)

        assert "No space left on device" in caplog.text
        assert "rc=255" in caplog.text
        manifest = json.loads(storage.get_bytes(manifest_path(job_id)).decode())
        assert manifest["generation"]["video_runner"]["status"] == STATUS_FAILED

    @patch("podcaster.video.job_runner.create_intermediate_store")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_cleans_up_intermediates_after_publish(
        self, mock_compose, mock_record, mock_store_factory, storage, dry_config
    ):
        """Scratch intermediates are deleted once the episode publishes (#410)."""
        job_id = "video-cleanup"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "Test Episode"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=60.0,
                segment_count=2,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose

        fake_store = MagicMock()
        fake_store.enabled = True
        mock_store_factory.return_value = fake_store

        outcome = run_video_generation(job_id, storage, config=dry_config)
        assert outcome.status == STATUS_COMPLETED

        # The intermediates store is built for the job and cleaned up on success,
        # and is threaded into both pipeline stages.
        mock_store_factory.assert_called_once_with(job_id)
        fake_store.cleanup.assert_called_once()
        assert mock_record.call_args.kwargs["intermediates"] is fake_store
        assert mock_compose.call_args.kwargs["intermediates"] is fake_store


# --- Process Message Tests ---


class TestProcessMessage:
    def test_malformed_message_deleted(self, storage, queue):
        msg = QueueMessage(
            message_id="m1",
            pop_receipt="p1",
            body="garbage!!!",
            dequeue_count=1,
        )
        outcome = process_message(msg, storage=storage, queue=queue)
        assert outcome.status == STATUS_FAILED
        assert outcome.reason == "malformed_message"
        assert len(queue.deleted) == 1

    def test_transient_error_leaves_message(self, storage, queue, dry_config):
        msg = _make_message("no-manifest", dequeue_count=1)
        outcome = process_message(msg, storage=storage, queue=queue, config=dry_config)
        assert outcome.status == STATUS_FAILED
        assert outcome.reason == "transient"
        assert len(queue.deleted) == 0

    def test_retry_exhausted_deletes(self, storage, queue, dry_config):
        msg = _make_message("no-manifest", dequeue_count=MAX_DEQUEUE_COUNT)
        outcome = process_message(msg, storage=storage, queue=queue, config=dry_config)
        assert outcome.status == STATUS_FAILED
        assert outcome.reason == REASON_RETRY_EXHAUSTED
        assert len(queue.deleted) == 1

    def test_successful_processing_deletes(self, storage, queue, dry_config):
        job_id = "success-job"
        storage.set_manifest(
            job_id,
            {
                "generation": {"video_runner": {"status": "completed"}},
            },
        )
        msg = _make_message(job_id)
        outcome = process_message(msg, storage=storage, queue=queue, config=dry_config)
        assert outcome.status == STATUS_SKIPPED
        assert len(queue.deleted) == 1

    def test_editor_lease_held_leaves_message(self, storage, queue, dry_config):
        # A foreign editor lease must NOT delete the message: leave it for
        # redelivery so the job is not lost if that editor later crashes (#563).
        from podcaster.video.job_runner import REASON_EDITOR_LEASE_HELD

        msg = _make_message("leased-job")
        with patch(
            "podcaster.video.job_runner.run_video_generation",
            return_value=VideoOutcome(
                "leased-job", STATUS_SKIPPED, reason=REASON_EDITOR_LEASE_HELD
            ),
        ):
            outcome = process_message(msg, storage=storage, queue=queue, config=dry_config)
        assert outcome.status == STATUS_SKIPPED
        assert outcome.reason == REASON_EDITOR_LEASE_HELD
        assert len(queue.deleted) == 0

    def test_permanent_failure_deletes_message_without_retry(self, storage, queue, dry_config):
        msg = _make_message("permanent-youtube-failure", dequeue_count=1)
        with (
            patch("podcaster.video.job_runner.report_failure") as mock_report,
            patch(
                "podcaster.video.job_runner.run_video_generation",
                side_effect=PermanentVideoError(
                    "required YouTube delivery failed",
                    reason=REASON_REQUIRED_YOUTUBE_FAILURE,
                    details={
                        "youtube_failure_code": "youtube_oauth_invalid_grant",
                        "youtube_oauth_error_subtype": "invalid_rapt",
                    },
                ),
            ),
        ):
            outcome = process_message(msg, storage=storage, queue=queue, config=dry_config)
        assert outcome.status == STATUS_FAILED
        assert outcome.reason == REASON_REQUIRED_YOUTUBE_FAILURE
        assert len(queue.deleted) == 1
        assert (
            mock_report.call_args.kwargs["details"]["youtube_oauth_error_subtype"] == "invalid_rapt"
        )


# --- Watermark failure classification (W36 review follow-up) ---


class TestWatermarkFailureClassification:
    """A configured watermark that cannot be resolved is PERMANENT, not transient.

    ``WatermarkUnavailableError`` was a plain ``RuntimeError``, so it fell into
    the generic handler, was re-raised as ``TransientVideoError``, and cost five
    full record+compose reruns before ``process_message`` reported a
    ``RetryExhausted`` that named neither the watermark nor the fix.  Every
    redelivery re-reads the same manifest config and re-fetches the same URL, so
    a retry can never succeed — classify once, delete the message once, and
    report the actionable reason.
    """

    def _stage(self, storage, job_id: str) -> None:
        storage.set_manifest(job_id, {"generation": {}})
        storage.set_script(job_id, "Just a plain script with no GitHub URLs")

    def _watermark_error(self, reason: str):
        from podcaster.video.video_compose import WatermarkUnavailableError

        return WatermarkUnavailableError(
            "DOG watermark was configured but could not be resolved: "
            "the bundled Claracle logo is not packaged in this image "
            "(expected /app/assets/images/claracle.jpeg). Rebuild and redeploy "
            "the synthesis image, or remove podcast_config.dog_logo.",
            reason=reason,
            details={"logo_url": "https://www.claracle.com/images/claracle.jpeg"},
        )

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_asset_missing_raises_permanent_not_transient(
        self, mock_compose, mock_record, storage, dry_config
    ):
        from podcaster.video.video_compose import WATERMARK_REASON_ASSET_MISSING

        job_id = "watermark-asset-missing"
        self._stage(storage, job_id)
        mock_record.return_value = MagicMock(recorded=[])
        mock_compose.side_effect = self._watermark_error(WATERMARK_REASON_ASSET_MISSING)

        with pytest.raises(PermanentVideoError) as excinfo:
            run_video_generation(job_id, storage, config=dry_config)

        exc = excinfo.value
        assert not isinstance(exc, TransientVideoError)
        assert exc.reason == WATERMARK_REASON_ASSET_MISSING
        assert exc.details["failure_family"] == REASON_WATERMARK_UNAVAILABLE
        assert exc.details["job_id"] == job_id
        # Operator-visible remedy survives the re-raise.
        assert "Rebuild and redeploy" in str(exc)

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_third_party_fetch_failure_raises_permanent(
        self, mock_compose, mock_record, storage, dry_config
    ):
        from podcaster.video.video_compose import WATERMARK_REASON_FETCH_FAILED

        job_id = "watermark-third-party"
        self._stage(storage, job_id)
        mock_record.return_value = MagicMock(recorded=[])
        mock_compose.side_effect = self._watermark_error(WATERMARK_REASON_FETCH_FAILED)

        with pytest.raises(PermanentVideoError) as excinfo:
            run_video_generation(job_id, storage, config=dry_config)
        assert excinfo.value.reason == WATERMARK_REASON_FETCH_FAILED

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_failed_state_records_the_watermark_reason(
        self, mock_compose, mock_record, storage, dry_config
    ):
        """The manifest must name the watermark, not a bare exception type."""
        from podcaster.video.video_compose import WATERMARK_REASON_ASSET_MISSING

        job_id = "watermark-state"
        self._stage(storage, job_id)
        mock_record.return_value = MagicMock(recorded=[])
        mock_compose.side_effect = self._watermark_error(WATERMARK_REASON_ASSET_MISSING)

        with pytest.raises(PermanentVideoError):
            run_video_generation(job_id, storage, config=dry_config)

        manifest = json.loads(storage._data[manifest_path(job_id)])
        state = manifest["generation"]["video_runner"]
        assert state["status"] == STATUS_FAILED
        assert state["reason"] == WATERMARK_REASON_ASSET_MISSING
        assert state["reason"] != "WatermarkUnavailableError"
        assert "claracle" in state["error"].lower()


class TestProcessMessageWatermarkFailure:
    """End-to-end queue lifecycle: one attempt, message deleted, real reason."""

    def _watermark_error(self, reason: str):
        from podcaster.video.video_compose import WatermarkUnavailableError

        return WatermarkUnavailableError(
            "third-party logo could not be downloaded; the bundled Claracle logo "
            "is deliberately NOT substituted because that would misbrand the episode",
            reason=reason,
            details={"logo_url": "https://example.com/partner.png", "canonical": False},
        )

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_deletes_message_on_first_attempt(
        self, mock_compose, mock_record, storage, queue, dry_config
    ):
        from podcaster.video.video_compose import WATERMARK_REASON_FETCH_FAILED

        job_id = "watermark-queue"
        storage.set_manifest(job_id, {"generation": {}})
        storage.set_script(job_id, "Just a plain script with no GitHub URLs")
        mock_record.return_value = MagicMock(recorded=[])
        mock_compose.side_effect = self._watermark_error(WATERMARK_REASON_FETCH_FAILED)

        msg = _make_message(job_id, dequeue_count=1)
        with patch("podcaster.video.job_runner.report_failure") as mock_report:
            outcome = process_message(msg, storage=storage, queue=queue, config=dry_config)

        assert outcome.status == STATUS_FAILED
        assert outcome.reason == WATERMARK_REASON_FETCH_FAILED
        assert outcome.reason != REASON_RETRY_EXHAUSTED
        # Deleted on the FIRST attempt — no redelivery, no four more reruns.
        assert len(queue.deleted) == 1
        assert msg.dequeue_count == 1

        kwargs = mock_report.call_args.kwargs
        assert kwargs["error_type"] == "PermanentVideoFailure"
        assert kwargs["error_type"] != "RetryExhausted"
        assert kwargs["details"]["reason"] == WATERMARK_REASON_FETCH_FAILED
        assert kwargs["details"]["failure_family"] == REASON_WATERMARK_UNAVAILABLE
        assert kwargs["details"]["logo_url"] == "https://example.com/partner.png"
        assert "misbrand" in kwargs["error_message"]

    def test_generation_runs_only_once(self, storage, queue, dry_config):
        """The expensive pipeline must not be re-entered for this failure."""
        from podcaster.video.video_compose import WATERMARK_REASON_ASSET_MISSING

        job_id = "watermark-no-retry"
        msg = _make_message(job_id, dequeue_count=1)
        with (
            patch("podcaster.video.job_runner.report_failure"),
            patch(
                "podcaster.video.job_runner.run_video_generation",
                side_effect=PermanentVideoError(
                    "watermark unavailable",
                    reason=WATERMARK_REASON_ASSET_MISSING,
                    details={"failure_family": REASON_WATERMARK_UNAVAILABLE},
                ),
            ) as mock_run,
        ):
            outcome = process_message(msg, storage=storage, queue=queue, config=dry_config)

        assert mock_run.call_count == 1
        assert outcome.reason == WATERMARK_REASON_ASSET_MISSING
        assert len(queue.deleted) == 1

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_absent_dog_logo_config_is_still_optional(
        self, mock_compose, mock_record, storage, queue, dry_config
    ):
        """Optional-absence semantics are untouched: no dog_logo, no failure."""
        job_id = "no-watermark-configured"
        storage.set_manifest(job_id, {"generation": {}})
        storage.set_script(job_id, "Just a plain script with no GitHub URLs")
        mock_record.return_value = MagicMock(recorded=[])

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            assert kwargs.get("dog_logo") is None
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path,
                duration_seconds=300.0,
                segment_count=1,
                has_audio=False,
            )

        mock_compose.side_effect = fake_compose
        msg = _make_message(job_id)
        outcome = process_message(msg, storage=storage, queue=queue, config=dry_config)
        assert outcome.status == STATUS_COMPLETED
        assert len(queue.deleted) == 1


# --- Drain Tests ---


class TestDrain:
    def test_empty_queue(self, storage):
        queue = FakeQueue()
        outcomes = drain(queue, storage)
        assert outcomes == []

    def test_processes_multiple_messages(self, storage, dry_config):
        # Set up two jobs that will skip (already processed)
        for jid in ["j1", "j2"]:
            storage.set_manifest(
                jid,
                {
                    "generation": {"video_runner": {"status": "completed"}},
                },
            )

        queue = FakeQueue([_make_message("j1"), _make_message("j2")])
        outcomes = drain(queue, storage, dry_config)
        assert len(outcomes) == 2
        assert all(o.status == STATUS_SKIPPED for o in outcomes)

    def test_respects_max_messages(self, storage, dry_config):
        for i in range(10):
            jid = f"j{i}"
            storage.set_manifest(
                jid,
                {
                    "generation": {"video_runner": {"status": "completed"}},
                },
            )

        messages = [_make_message(f"j{i}") for i in range(10)]
        queue = FakeQueue(messages)
        outcomes = drain(queue, storage, dry_config, max_messages=3)
        assert len(outcomes) == 3


class TestResolveDogLogo:
    def test_present_config_builds_dog_logo(self):
        manifest = {
            "request": {
                "podcast_config": {
                    "dog_logo": {
                        "url": "https://example.com/x.png",
                        "position": "bottom-right",
                        "size": 100,
                        "opacity": 0.5,
                    }
                }
            }
        }
        cfg = _resolve_dog_logo(manifest)
        assert cfg is not None
        assert cfg.url == "https://example.com/x.png"
        assert cfg.position == "bottom-right"
        assert cfg.size == 100
        assert cfg.opacity == 0.5

    def test_missing_dog_logo_returns_none(self):
        assert _resolve_dog_logo({"request": {"podcast_config": {}}}) is None

    def test_no_request_returns_none(self):
        assert _resolve_dog_logo({}) is None
        assert _resolve_dog_logo({"request": "nope"}) is None


class TestResolveAnchorId:
    def test_generation_publish_result(self):
        manifest = {"generation": {"publish_result": {"anchor_id": 314}}}
        assert _resolve_anchor_id(manifest) == 314

    def test_falls_back_to_publishing_result(self):
        manifest = {"publishing": {"result": {"anchor_episode_id": 42}}}
        assert _resolve_anchor_id(manifest) == 42

    def test_prefers_generation_over_publishing(self):
        manifest = {
            "generation": {"publish_result": {"anchor_id": 1}},
            "publishing": {"result": {"anchor_episode_id": 2}},
        }
        assert _resolve_anchor_id(manifest) == 1

    def test_string_anchor_coerced(self):
        manifest = {"generation": {"publish_result": {"anchor_id": "777"}}}
        assert _resolve_anchor_id(manifest) == 777

    def test_missing_returns_none(self):
        assert _resolve_anchor_id({}) is None
        assert _resolve_anchor_id({"generation": {"publish_result": {}}}) is None
        assert _resolve_anchor_id({"generation": "nope"}) is None


class TestBuildSectionCards:
    """_build_section_cards wiring (issue #377)."""

    def _recorded(self, *urls):
        from podcaster.video.sync_plan import RepoReference, VideoSegment
        from podcaster.video.video_gen import RecordedSegment

        recs = []
        for i, url in enumerate(urls):
            repo = None
            if url is not None:
                owner, name = url.split("github.com/")[1].split("/")[:2]
                repo = RepoReference(owner=owner, name=name)
            seg = VideoSegment(repo=repo, start_seconds=float(i), duration_seconds=5.0)
            recs.append(RecordedSegment(segment=seg, video_path=Path(f"/tmp/s{i}.webm")))
        return recs

    def test_disabled_via_env_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VIDEO_SECTION_CARDS", "0")
        recs = self._recorded("https://github.com/o/r")
        assert _build_section_cards("## Trends\nx", recs, tmp_path) == []

    def test_no_sections_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VIDEO_SECTION_CARDS", raising=False)
        recs = self._recorded("https://github.com/o/r")
        script = "Title: X\n---\n\nAda: just dialogue here.\n"
        assert _build_section_cards(script, recs, tmp_path) == []

    def test_builds_inserts_when_sections_present(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VIDEO_SECTION_CARDS", raising=False)
        recs = self._recorded(
            "https://github.com/microsoft/vscode",
            "https://github.com/astral-sh/ruff",
        )
        script = (
            "Title: X\nSource: https://github.com/o/r\n---\n\n"
            "## Trends\nAda: https://github.com/microsoft/vscode\n\n"
            "## Signal & Noise\nBeto: https://github.com/astral-sh/ruff\n"
        )
        # Avoid invoking real ffmpeg: stub the card renderer.
        with (
            patch("podcaster.video.section_cards.generate_section_card") as gen,
            patch("podcaster.video.section_cards._get_drawtext_ffmpeg", return_value="ffmpeg"),
        ):
            inserts = _build_section_cards(
                script,
                recs,
                tmp_path,
                sections_metadata=[{"title_card": {"duration_seconds": 0.75}}],
            )
        assert [i.name for i in inserts] == ["Trends", "Signal & Noise"]
        assert [i.before_index for i in inserts] == [0, 1]
        assert [i.duration_seconds for i in inserts] == [0.75, 0.75]
        assert gen.call_count == 2

    def test_section_card_duration_clamped_to_issue_bounds(self):
        assert _section_card_duration_seconds([{"title_card": {"duration_seconds": 0.2}}]) == 0.5
        assert _section_card_duration_seconds([{"title_card": {"duration_seconds": 2.5}}]) == 1.0
        assert _section_card_duration_seconds([]) == 0.75

    def test_generation_failure_is_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VIDEO_SECTION_CARDS", raising=False)
        recs = self._recorded("https://github.com/microsoft/vscode")
        script = "Title: X\n---\n\n## Trends\nAda: https://github.com/microsoft/vscode\n"
        with patch(
            "podcaster.video.section_cards.build_section_card_inserts",
            side_effect=RuntimeError("boom"),
        ):
            # Must never raise — composition proceeds without cards.
            assert _build_section_cards(script, recs, tmp_path) == []


class _ScratchStorage:
    """Richer in-memory scratch backend for the fan-out integration tests."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def get_bytes(self, path):
        return self._data.get(path)

    def put_bytes(self, path, content, content_type):
        self._data[path] = content
        return MagicMock(path=path, size_bytes=len(content))

    def update_bytes(self, path, content_type, update):
        updated = update(self._data.get(path))
        self._data[path] = updated
        return MagicMock(path=path, size_bytes=len(updated))

    def blob_exists(self, path):
        return path in self._data

    def delete_blob(self, path):
        return self._data.pop(path, None) is not None

    def delete_prefix(self, prefix):
        keys = [k for k in self._data if k.startswith(prefix)]
        for k in keys:
            del self._data[k]
        return len(keys)


class _RecordingProducer:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, body):
        self.sent.append(body)


class TestFanoutGating:
    """run_video_generation editor fan-out path (#552/#563)."""

    def _seed(self, storage):
        job_id = "video-fanout"
        storage.set_manifest(
            job_id,
            {
                "generation": {"validation": {"duration_seconds": 60.0}},
                "request": {"article_title": "Fan-out Episode"},
            },
        )
        storage.set_script(job_id, SAMPLE_SCRIPT)
        return job_id

    @staticmethod
    def _fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
        if output_path:
            output_path.write_bytes(b"\x00" * 2048)
        return MagicMock(output_path=output_path, duration_seconds=60.0, segment_count=2)

    @patch("podcaster.video.editor.record_via_fanout")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_fanout_path_used_lease_acquired_and_cleaned_up(
        self, mock_compose, mock_record_episode, mock_fanout, storage, dry_config
    ):
        from podcaster.video.clipset import clip_blob_path
        from podcaster.video.editor import EditorLease, editor_lease_blob_path

        job_id = self._seed(storage)
        scratch = _ScratchStorage()
        producer = _RecordingProducer()
        # A leftover clip blob proves cleanup runs after a successful compose.
        scratch.put_bytes(clip_blob_path(job_id, 0), b"WEBM", "video/webm")

        mock_fanout.return_value = MagicMock(recorded=[], output_dir=Path("."))
        mock_compose.side_effect = self._fake_compose

        outcome = run_video_generation(
            job_id,
            storage,
            config=dry_config,
            fanout=True,
            fanout_scratch=scratch,
            clip_producer=producer,
        )

        assert outcome.status == STATUS_COMPLETED
        # Fan-out path was taken; the legacy inline recorder was not called.
        mock_fanout.assert_called_once()
        mock_record_episode.assert_not_called()
        # Lease released (reads as free) and clips cleaned on success.
        assert EditorLease.from_bytes(scratch.get_bytes(editor_lease_blob_path(job_id))) is None
        assert not scratch.blob_exists(clip_blob_path(job_id, 0))

    @patch("podcaster.video.editor.record_via_fanout")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_foreign_lease_skips_without_recording(
        self, mock_compose, mock_record_episode, mock_fanout, storage, dry_config
    ):
        from datetime import datetime, timedelta, timezone

        from podcaster.video.editor import EditorLease, editor_lease_blob_path

        job_id = self._seed(storage)
        scratch = _ScratchStorage()
        # Pre-write an unexpired lease owned by another editor run.
        now = datetime.now(timezone.utc)
        scratch.put_bytes(
            editor_lease_blob_path(job_id),
            EditorLease("other-run", now, now + timedelta(seconds=900)).to_bytes(),
            "application/json",
        )

        outcome = run_video_generation(
            job_id,
            storage,
            config=dry_config,
            fanout=True,
            fanout_scratch=scratch,
            clip_producer=_RecordingProducer(),
        )

        assert outcome.status == STATUS_SKIPPED
        assert outcome.reason == REASON_EDITOR_LEASE_HELD
        mock_fanout.assert_not_called()
        mock_record_episode.assert_not_called()

    @patch("podcaster.video.editor.record_via_fanout")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_lease_released_on_failure(
        self, mock_compose, mock_record_episode, mock_fanout, storage, dry_config
    ):
        # A failure after the lease is acquired must release it (reads as free)
        # so a retry is not blocked for the full lease TTL (#563).
        from podcaster.video.editor import EditorLease, editor_lease_blob_path

        job_id = self._seed(storage)
        scratch = _ScratchStorage()
        mock_fanout.return_value = MagicMock(recorded=[], output_dir=Path("."))
        mock_compose.side_effect = RuntimeError("compose boom")

        with pytest.raises(TransientVideoError):
            run_video_generation(
                job_id,
                storage,
                config=dry_config,
                fanout=True,
                fanout_scratch=scratch,
                clip_producer=_RecordingProducer(),
            )

        assert EditorLease.from_bytes(scratch.get_bytes(editor_lease_blob_path(job_id))) is None

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_legacy_path_when_fanout_unconfigured(
        self, mock_compose, mock_record_episode, storage, dry_config
    ):
        job_id = self._seed(storage)
        mock_record_episode.return_value = MagicMock(recorded=[], output_dir=Path("."))
        mock_compose.side_effect = self._fake_compose

        # No scratch / no producer → fan-out disabled, inline recorder used.
        outcome = run_video_generation(
            job_id, storage, config=dry_config, fanout_scratch=None, clip_producer=None
        )
        assert outcome.status == STATUS_COMPLETED
        mock_record_episode.assert_called_once()


class _VisibilityQueue:
    """Records the visibility_timeout each receive used."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.visibility_timeouts: list[int] = []
        self.deleted: list = []

    def receive_messages(self, max_messages=1, *, visibility_timeout=600):
        self.visibility_timeouts.append(visibility_timeout)
        if self._messages:
            return [self._messages.pop(0)]
        return []

    def delete_message(self, message):
        self.deleted.append(message)


class TestEditorVisibilityTimeout:
    def test_drain_uses_video_visibility_timeout(self, storage, dry_config, monkeypatch):
        from podcaster.video.job_runner import DEFAULT_VIDEO_VISIBILITY_TIMEOUT

        monkeypatch.delenv("PODCASTER_VIDEO_VISIBILITY_TIMEOUT", raising=False)
        storage.set_manifest("j1", {"generation": {"video_runner": {"status": "completed"}}})
        queue = _VisibilityQueue([_make_message("j1")])
        drain(queue, storage, dry_config)
        assert queue.visibility_timeouts[0] == DEFAULT_VIDEO_VISIBILITY_TIMEOUT

    def test_drain_honours_env_override(self, storage, dry_config, monkeypatch):
        monkeypatch.setenv("PODCASTER_VIDEO_VISIBILITY_TIMEOUT", "1234")
        storage.set_manifest("j1", {"generation": {"video_runner": {"status": "completed"}}})
        queue = _VisibilityQueue([_make_message("j1")])
        drain(queue, storage, dry_config)
        assert queue.visibility_timeouts[0] == 1234
