"""Tests for pipeline_lock module (#268)."""

from __future__ import annotations

import json

from podcaster.pipeline_lock import (
    PIPELINE_AUDIO,
    PIPELINE_VIDEO,
    claim_pipeline,
    release_pipeline,
)


class FakeStorageBackend:
    """Minimal in-memory storage for testing pipeline locks."""

    def __init__(self, initial_content: bytes | None = None):
        self._content = initial_content

    def get_bytes(self, path: str) -> bytes | None:
        return self._content

    def put_bytes(self, path: str, content: bytes, content_type: str):
        self._content = content

    def update_bytes(self, path, content_type, update):
        updated = update(self._content)
        self._content = updated
        from dataclasses import dataclass

        _p = path
        _ct = content_type

        @dataclass
        class _Artifact:
            path: str = _p
            url: str = f"http://test/{_p}"
            size_bytes: int = len(updated)
            content_type: str = _ct

        return _Artifact()

    @property
    def content(self) -> dict | None:
        if self._content is None:
            return None
        return json.loads(self._content.decode("utf-8"))


class TestClaimPipeline:
    def test_claim_on_empty_manifest(self):
        storage = FakeStorageBackend(None)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        doc = storage.content
        assert doc["generation"]["pipeline_lock"]["pipeline"] == "audio"

    def test_same_pipeline_re_claims(self):
        storage = FakeStorageBackend(None)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True

    def test_different_pipeline_conflicts(self):
        storage = FakeStorageBackend(None)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        assert claim_pipeline(storage, "job-1", PIPELINE_VIDEO) is False

    def test_claim_preserves_existing_manifest_data(self):
        initial = json.dumps(
            {"request": {"title": "test"}, "generation": {"validation": {}}}
        ).encode()
        storage = FakeStorageBackend(initial)
        assert claim_pipeline(storage, "job-1", PIPELINE_VIDEO) is True
        doc = storage.content
        assert doc["request"]["title"] == "test"
        assert doc["generation"]["validation"] == {}
        assert doc["generation"]["pipeline_lock"]["pipeline"] == "video"

    def test_claim_on_malformed_manifest(self):
        storage = FakeStorageBackend(b"not json")
        # Should still claim (treats as empty)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True

    def test_claim_when_generation_is_not_dict(self):
        """If manifest.generation is a non-dict value, it should be replaced safely."""
        initial = json.dumps({"generation": "corrupted_string"}).encode()
        storage = FakeStorageBackend(initial)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        doc = storage.content
        assert isinstance(doc["generation"], dict)
        assert doc["generation"]["pipeline_lock"]["pipeline"] == "audio"

    def test_storage_error_returns_false(self):
        """Storage errors should fail-closed (return False), not fail-open."""

        class FailingStorage(FakeStorageBackend):
            def update_bytes(self, path, content_type, update):
                raise OSError("storage unavailable")

        storage = FailingStorage(None)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is False

    def test_video_blocked_while_audio_synthesis_in_progress(self):
        """Video must NOT take over the lock while audio synthesis is unfinished."""
        initial = json.dumps({"generation": {"synthesis_runner": {"status": "running"}}}).encode()
        storage = FakeStorageBackend(initial)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        assert claim_pipeline(storage, "job-1", PIPELINE_VIDEO) is False
        assert storage.content["generation"]["pipeline_lock"]["pipeline"] == "audio"

    def test_video_takes_over_after_audio_synthesis_completed(self):
        """Once synthesis_runner.status == completed, video may claim the lock."""
        initial = json.dumps({"generation": {"synthesis_runner": {"status": "completed"}}}).encode()
        storage = FakeStorageBackend(initial)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        # Audio finished; video should now be able to take over.
        assert claim_pipeline(storage, "job-1", PIPELINE_VIDEO) is True
        assert storage.content["generation"]["pipeline_lock"]["pipeline"] == "video"

    def test_audio_cannot_take_over_video_lock(self):
        """The completed-synthesis handoff is one-directional (video over audio only)."""
        initial = json.dumps({"generation": {"synthesis_runner": {"status": "completed"}}}).encode()
        storage = FakeStorageBackend(initial)
        assert claim_pipeline(storage, "job-1", PIPELINE_VIDEO) is True
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is False

    def test_video_blocked_when_no_synthesis_runner(self):
        """No synthesis_runner means audio hasn't completed; video stays blocked."""
        storage = FakeStorageBackend(None)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        assert claim_pipeline(storage, "job-1", PIPELINE_VIDEO) is False


class TestReleasePipeline:
    def test_release_owned_lock(self):
        storage = FakeStorageBackend(None)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        assert release_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        assert "pipeline_lock" not in storage.content["generation"]

    def test_release_allows_other_pipeline_to_claim(self):
        storage = FakeStorageBackend(None)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        assert release_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        assert claim_pipeline(storage, "job-1", PIPELINE_VIDEO) is True

    def test_release_does_not_clobber_other_owner(self):
        storage = FakeStorageBackend(None)
        assert claim_pipeline(storage, "job-1", PIPELINE_AUDIO) is True
        # Releasing as video must not remove audio's lock.
        assert release_pipeline(storage, "job-1", PIPELINE_VIDEO) is True
        assert storage.content["generation"]["pipeline_lock"]["pipeline"] == "audio"

    def test_release_on_empty_manifest(self):
        storage = FakeStorageBackend(None)
        assert release_pipeline(storage, "job-1", PIPELINE_AUDIO) is True

    def test_release_storage_error_returns_false(self):
        class FailingStorage(FakeStorageBackend):
            def update_bytes(self, path, content_type, update):
                raise OSError("storage unavailable")

        storage = FailingStorage(None)
        assert release_pipeline(storage, "job-1", PIPELINE_AUDIO) is False
