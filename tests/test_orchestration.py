from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from podcaster.costs import build_cost_ledger
from podcaster.generation import manifest_bytes
from podcaster.orchestration import (
    _prepare_audio_files,
    auto_publish_enabled,
    auto_publish_job,
    manifest_path,
    persist_manifest,
    process_review_decision,
)
from podcaster.publish import PublishResult
from podcaster.storage import LocalStorageBackend, StoredArtifact


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


class _RemoteStorageStub:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        raise NotImplementedError

    def get_bytes(self, path: str) -> bytes | None:
        return self._blobs.get(path)

    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> StoredArtifact:
        raise NotImplementedError

    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        raise NotImplementedError

    def generate_download_url(self, path: str, *, expiry):
        raise NotImplementedError


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


def test_auto_publish_requires_spotify_publish_enable(monkeypatch) -> None:
    monkeypatch.setenv("PODCAST_AUTO_PUBLISH", "true")
    monkeypatch.delenv("SPOTIFY_PUBLISH_ENABLED", raising=False)
    assert auto_publish_enabled() is False

    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
    assert auto_publish_enabled() is True


def test_persist_manifest_uses_storage_update_bytes() -> None:
    class TrackingStorage(LocalStorageBackend):
        def __init__(self, root: Path, base_url: str) -> None:
            super().__init__(root, base_url)
            self.updated: list[str] = []
            self.puts: list[str] = []

        def put_bytes(self, path: str, content: bytes, content_type: str):
            self.puts.append(path)
            return super().put_bytes(path, content, content_type)

        def update_bytes(self, path: str, content_type: str, update):
            self.updated.append(path)
            return super().update_bytes(path, content_type, update)

    storage = TrackingStorage(Path(".test-orchestration-storage"), "https://example.invalid/artifacts")
    manifest = _synthesized_manifest()
    try:
        persist_manifest(storage, manifest["job_id"], manifest)
        assert storage.updated == [manifest_path(manifest["job_id"])]
        assert storage.puts == []
    finally:
        import shutil

        shutil.rmtree(storage.root, ignore_errors=True)


def test_prepare_audio_files_remote_storage_downloads_mp4_when_present(tmp_path: Path, monkeypatch) -> None:
    job_id = _job_id()
    manifest = _synthesized_manifest()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    storage = _RemoteStorageStub(
        {
            f"jobs/{job_id}/audio/{job_id}.mp3": b"mp3-bytes",
            f"jobs/{job_id}/audio/{job_id}.wav": b"wav-bytes",
            f"jobs/{job_id}/audio/{job_id}.mp4": b"mp4-bytes",
        }
    )

    (mp3_path, wav_path), cleanup_dir = _prepare_audio_files(storage, manifest, job_id)

    assert cleanup_dir == tmp_path / "podcaster-publish-work" / job_id
    assert mp3_path.read_bytes() == b"mp3-bytes"
    assert wav_path is not None
    assert wav_path.read_bytes() == b"wav-bytes"
    candidate_mp4 = mp3_path.with_suffix(".mp4")
    assert candidate_mp4 == cleanup_dir / f"{job_id}.mp4"
    assert candidate_mp4.read_bytes() == b"mp4-bytes"


def test_prepare_audio_files_remote_storage_skips_missing_mp4(tmp_path: Path, monkeypatch) -> None:
    job_id = _job_id()
    manifest = _synthesized_manifest()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    storage = _RemoteStorageStub(
        {
            f"jobs/{job_id}/audio/{job_id}.mp3": b"mp3-bytes",
            f"jobs/{job_id}/audio/{job_id}.wav": b"wav-bytes",
        }
    )

    (mp3_path, wav_path), cleanup_dir = _prepare_audio_files(storage, manifest, job_id)

    assert cleanup_dir == tmp_path / "podcaster-publish-work" / job_id
    assert wav_path is not None
    assert not mp3_path.with_suffix(".mp4").exists()


def test_prepare_audio_files_remote_storage_falls_back_to_video_mp4(tmp_path: Path, monkeypatch) -> None:
    job_id = _job_id()
    manifest = _synthesized_manifest()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    storage = _RemoteStorageStub(
        {
            f"jobs/{job_id}/audio/{job_id}.mp3": b"mp3-bytes",
            f"jobs/{job_id}/audio/{job_id}.wav": b"wav-bytes",
            f"jobs/{job_id}/video/{job_id}.mp4": b"video-mp4-bytes",
        }
    )

    (mp3_path, _wav_path), cleanup_dir = _prepare_audio_files(storage, manifest, job_id)

    assert cleanup_dir == tmp_path / "podcaster-publish-work" / job_id
    assert mp3_path.with_suffix(".mp4").read_bytes() == b"video-mp4-bytes"
