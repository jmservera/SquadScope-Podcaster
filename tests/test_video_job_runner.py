"""Tests for podcaster.video.job_runner (#242)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcaster.queue import QueueMessage
from podcaster.video.audio_align import TranscriptionUnavailable
from podcaster.video.distribution import VideoDistributionConfig
from podcaster.video.job_runner import (
    MAX_DEQUEUE_COUNT,
    REASON_ALREADY_PROCESSED,
    REASON_NO_REPOS,
    REASON_RETRY_EXHAUSTED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    TransientVideoError,
    VideoOutcome,
    _already_processed,
    _build_section_cards,
    _resolve_anchor_id,
    _resolve_dog_logo,
    drain,
    manifest_path,
    process_message,
    removed_repos_notes_path,
    run_video_generation,
    script_path,
    show_notes_path,
    video_artifact_path,
    _build_video_description,
)


@pytest.fixture(autouse=True)
def _no_audio_cue_transcription():
    """Force audio-cue sync (#374) to fall back to proportional timing.

    These tests provide placeholder audio bytes, not real speech, and assert on
    proportional/mention-based plans. Patching transcription to be unavailable
    keeps them hermetic and fast (no faster-whisper model load / download).
    """
    with patch(
        "podcaster.video.audio_align.transcribe_words",
        side_effect=TranscriptionUnavailable("disabled in tests"),
    ):
        yield


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
    with patch(
        "podcaster.video.sync_plan.check_repo_removed", return_value=False
    ):
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
        assert "AudioCoffee" in desc

    def test_includes_summary_and_credits_packaging_format(self):
        notes = (
            "# Claracle — Week W24\n\n## Title\n\n"
            "**Hosts:** Theo (fable) & Vera (alloy)\n\n"
            "### About this episode\n\nA dynamic AI conversation.\n\n"
            "### Links\n\n- x\n"
        )
        storage = self._storage("j", notes)
        desc = _build_video_description(storage, "j", "fallback")
        assert "A dynamic AI conversation." in desc
        assert "Hosts: Theo (fable) & Vera (alloy)" in desc
        assert "Claracle — www.claracle.com" in desc
        assert "AudioCoffee" in desc

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
        assert "AudioCoffee" in desc

    def test_uses_fallback_summary_when_no_section(self):
        notes = "# Heading only\n\nsome stray text\n"
        storage = self._storage("j", notes)
        desc = _build_video_description(storage, "j", "fallback summary")
        assert desc.startswith("fallback summary")
        assert "www.claracle.com" in desc
        assert "AudioCoffee" in desc

    def test_custom_music_credits_override(self):
        """Custom music_credits parameter overrides the default attribution."""
        notes = "# Title\n\n### About this episode\n\nSummary text.\n"
        storage = self._storage("j", notes)
        desc = _build_video_description(storage, "j", "fallback", music_credits="My Custom Credits")
        assert "My Custom Credits" in desc
        # Default attribution must NOT appear when custom credits provided
        assert "AudioCoffee" not in desc


# --- Already Processed Tests ---


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
        storage.set_manifest("done-job", {
            "generation": {"video_runner": {"status": "completed"}},
        })
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
    def test_successful_generation(self, mock_compose, mock_record, storage, dry_config, tmp_path):
        job_id = "video-ok"
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 60.0}},
            "request": {"article_title": "Test Episode"},
        })
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

    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_removed_repo_annotated_and_notes_persisted(
        self, mock_compose, mock_record, storage, dry_config
    ):
        """Removed repos are flagged before recording and speaker cues persisted (#394)."""
        job_id = "video-removed"
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 60.0}},
            "request": {"article_title": "Test Episode"},
        })
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path, duration_seconds=60.0,
                segment_count=2, has_audio=False,
            )
        mock_compose.side_effect = fake_compose

        # facebook/react is "removed"; microsoft/vscode is present.
        def fake_removed(url, timeout=5.0):
            return "facebook/react" in url

        with patch(
            "podcaster.video.sync_plan.check_repo_removed", side_effect=fake_removed
        ):
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
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 60.0}},
            "request": {"article_title": "Test Episode"},
        })
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path, duration_seconds=60.0,
                segment_count=2, has_audio=False,
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
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 60.0}},
            "request": {"article_title": "Notes Episode"},
        })
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
                output_path=output_path, duration_seconds=60.0,
                segment_count=2, has_audio=False,
            )
        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = MagicMock(
            status="completed", youtube_id=None, blob_path=None,
            spotify_rss_updated=False, spotify_upload_updated=False,
        )

        run_video_generation(job_id, storage, config=dry_config)

        description = mock_distribute.call_args.args[3]
        assert "A joyful conversation about open source." in description
        assert "Hosts: Theo (fable) & Vera (alloy)" in description
        assert "Claracle" in description
        assert "www.claracle.com" in description
        # Music credits must be present (default attribution from TRACK_ATTRIBUTION)
        assert "AudioCoffee" in description
        assert "https://www.audiocoffee.net/" in description

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_description_uses_description_template_as_music_credits(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config
    ):
        """description_template from the request is appended as music credits (#412)."""
        job_id = "video-template"
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 60.0}},
            "request": {
                "article_title": "Template Episode",
                "week": "2026-W24",
                "description_template": "Custom music credit from SquadScope config",
            },
        })
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path, duration_seconds=60.0,
                segment_count=2, has_audio=False,
            )
        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = MagicMock(
            status="completed", youtube_id=None, blob_path=None,
            spotify_rss_updated=False, spotify_upload_updated=False,
        )

        run_video_generation(job_id, storage, config=dry_config)

        description = mock_distribute.call_args.args[3]
        assert "Custom music credit from SquadScope config" in description

    @patch("podcaster.video.job_runner.distribute_video")
    @patch("podcaster.video.video_gen.record_episode")
    @patch("podcaster.video.video_compose.compose_video")
    def test_season_episode_numbers_from_manifest_week(
        self, mock_compose, mock_record, mock_distribute, storage, dry_config
    ):
        """Season (year) and episode (week) are resolved from manifest and passed to distribute_video (#412)."""
        job_id = "video-season"
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 60.0}},
            "request": {
                "article_title": "Season Episode",
                "week": "2026-W24",
            },
        })
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path, duration_seconds=60.0,
                segment_count=2, has_audio=False,
            )
        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = MagicMock(
            status="completed", youtube_id=None, blob_path=None,
            spotify_rss_updated=False, spotify_upload_updated=False,
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
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 60.0}},
            "request": {"article_title": "No Week"},
        })
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kwargs):
            if output_path:
                output_path.write_bytes(b"\x00" * 2048)
            return MagicMock(
                output_path=output_path, duration_seconds=60.0,
                segment_count=2, has_audio=False,
            )
        mock_compose.side_effect = fake_compose
        mock_distribute.return_value = MagicMock(
            status="completed", youtube_id=None, blob_path=None,
            spotify_rss_updated=False, spotify_upload_updated=False,
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
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 300.0}},
            "request": {"article_title": "Dur Test"},
        })
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
                output_path=output_path, duration_seconds=123.0,
                segment_count=2, has_audio=True,
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
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 222.0}},
            "request": {"article_title": "Fallback"},
        })
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
                output_path=output_path, duration_seconds=222.0,
                segment_count=2, has_audio=True,
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
        storage.set_manifest(job_id, {
            "generation": {"validation": {"duration_seconds": 60.0}},
            "request": {"article_title": "Test Episode"},
        })
        storage.set_script(job_id, SAMPLE_SCRIPT)

        mock_recording = MagicMock()
        mock_recording.recorded = []
        mock_record.return_value = mock_recording

        mock_compose.side_effect = subprocess.CalledProcessError(
            255, ["ffmpeg", "-i", "joined.mp4", "muxed.mp4"],
            output="", stderr="av_interleaved_write_frame(): No space left on device",
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(TransientVideoError):
                run_video_generation(job_id, storage, config=dry_config)

        assert "No space left on device" in caplog.text
        assert "rc=255" in caplog.text
        manifest = json.loads(storage.get_bytes(manifest_path(job_id)).decode())
        assert manifest["generation"]["video_runner"]["status"] == STATUS_FAILED


# --- Process Message Tests ---


class TestProcessMessage:
    def test_malformed_message_deleted(self, storage, queue):
        msg = QueueMessage(
            message_id="m1", pop_receipt="p1", body="garbage!!!", dequeue_count=1,
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
        storage.set_manifest(job_id, {
            "generation": {"video_runner": {"status": "completed"}},
        })
        msg = _make_message(job_id)
        outcome = process_message(msg, storage=storage, queue=queue, config=dry_config)
        assert outcome.status == STATUS_SKIPPED
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
            storage.set_manifest(jid, {
                "generation": {"video_runner": {"status": "completed"}},
            })

        queue = FakeQueue([_make_message("j1"), _make_message("j2")])
        outcomes = drain(queue, storage, dry_config)
        assert len(outcomes) == 2
        assert all(o.status == STATUS_SKIPPED for o in outcomes)

    def test_respects_max_messages(self, storage, dry_config):
        for i in range(10):
            jid = f"j{i}"
            storage.set_manifest(jid, {
                "generation": {"video_runner": {"status": "completed"}},
            })

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
        with patch(
            "podcaster.video.section_cards.generate_section_card"
        ) as gen, patch(
            "podcaster.video.section_cards._get_drawtext_ffmpeg", return_value="ffmpeg"
        ):
            inserts = _build_section_cards(script, recs, tmp_path)
        assert [i.name for i in inserts] == ["Trends", "Signal & Noise"]
        assert [i.before_index for i in inserts] == [0, 1]
        assert gen.call_count == 2

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
