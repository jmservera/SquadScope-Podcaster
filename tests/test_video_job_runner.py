"""Tests for podcaster.video.job_runner (#242)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcaster.queue import QueueMessage
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
    _resolve_dog_logo,
    drain,
    manifest_path,
    process_message,
    run_video_generation,
    script_path,
    video_artifact_path,
)


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

    def test_no_repos_skips(self, storage, dry_config):
        storage.set_manifest("no-repos", {"generation": {}})
        storage.set_script("no-repos", "Just a plain script with no GitHub URLs")
        outcome = run_video_generation("no-repos", storage, config=dry_config)
        assert outcome.status == STATUS_SKIPPED
        assert outcome.reason == REASON_NO_REPOS

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
