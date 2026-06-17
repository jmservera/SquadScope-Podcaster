"""Tests for pipeline_lock module (#268)."""

from __future__ import annotations

import json

import pytest

from podcaster.pipeline_lock import PIPELINE_AUDIO, PIPELINE_VIDEO, claim_pipeline


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

        @dataclass
        class _Artifact:
            path: str = path
            url: str = f"http://test/{path}"
            size_bytes: int = len(updated)
            content_type: str = content_type

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
        initial = json.dumps({"request": {"title": "test"}, "generation": {"validation": {}}}).encode()
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
