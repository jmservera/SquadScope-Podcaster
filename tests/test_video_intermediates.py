"""Tests for blob-backed intermediate checkpoint/resume (issue #410).

Covers the new streaming/exists/delete methods on the storage backends, the
``create_scratch_storage_backend`` factory, and the ``IntermediateStore``
checkpoint/resume helper.
"""

from __future__ import annotations

import pytest

from podcaster.storage import (
    LocalStorageBackend,
    create_scratch_storage_backend,
)
from podcaster.video.intermediates import (
    IntermediateStore,
    create_intermediate_store,
)


@pytest.fixture
def backend(tmp_path) -> LocalStorageBackend:
    return LocalStorageBackend(root=tmp_path / "scratch", base_url="https://example.test/scratch")


# --- Storage backend streaming/exists/delete methods -------------------------


class TestLocalBackendFileMethods:
    def test_upload_then_exists_and_download(self, backend, tmp_path):
        src = tmp_path / "in.bin"
        src.write_bytes(b"hello-video-bytes")

        artifact = backend.upload_file("video-jobs/j1/intermediates/seg.mp4", src, "video/mp4")
        assert artifact.size_bytes == len(b"hello-video-bytes")
        assert backend.blob_exists("video-jobs/j1/intermediates/seg.mp4")

        dest = tmp_path / "out.bin"
        assert backend.download_file("video-jobs/j1/intermediates/seg.mp4", dest) is True
        assert dest.read_bytes() == b"hello-video-bytes"

    def test_exists_false_for_missing(self, backend):
        assert backend.blob_exists("video-jobs/j1/intermediates/missing.mp4") is False

    def test_download_missing_returns_false(self, backend, tmp_path):
        dest = tmp_path / "out.bin"
        assert backend.download_file("nope/missing.mp4", dest) is False
        assert not dest.exists()

    def test_delete_blob(self, backend, tmp_path):
        src = tmp_path / "in.bin"
        src.write_bytes(b"x")
        backend.upload_file("a/b/c.mp4", src, "video/mp4")
        assert backend.delete_blob("a/b/c.mp4") is True
        assert backend.blob_exists("a/b/c.mp4") is False
        # Deleting again is a no-op returning False.
        assert backend.delete_blob("a/b/c.mp4") is False

    def test_delete_prefix(self, backend, tmp_path):
        src = tmp_path / "in.bin"
        src.write_bytes(b"x")
        for name in ["video-jobs/j1/intermediates/a.mp4",
                     "video-jobs/j1/intermediates/sub/b.mp4",
                     "video-jobs/j2/intermediates/c.mp4"]:
            backend.upload_file(name, src, "video/mp4")

        deleted = backend.delete_prefix("video-jobs/j1/intermediates/")
        assert deleted == 2
        assert backend.blob_exists("video-jobs/j1/intermediates/a.mp4") is False
        assert backend.blob_exists("video-jobs/j1/intermediates/sub/b.mp4") is False
        # Unrelated job is untouched.
        assert backend.blob_exists("video-jobs/j2/intermediates/c.mp4") is True


# --- Scratch backend factory --------------------------------------------------


class TestCreateScratchBackend:
    def test_disabled_without_container_env(self, monkeypatch):
        monkeypatch.delenv("PODCASTER_VIDEO_SCRATCH_CONTAINER", raising=False)
        assert create_scratch_storage_backend() is None

    def test_local_backend_when_no_account_url(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PODCASTER_VIDEO_SCRATCH_CONTAINER", "video-scratch")
        monkeypatch.delenv("PODCASTER_STORAGE_ACCOUNT_URL", raising=False)
        monkeypatch.setenv("PODCASTER_LOCAL_SCRATCH_PATH", str(tmp_path / "s"))
        backend = create_scratch_storage_backend()
        assert isinstance(backend, LocalStorageBackend)

    def test_azure_backend_when_account_url_present(self, monkeypatch):
        monkeypatch.setenv("PODCASTER_VIDEO_SCRATCH_CONTAINER", "video-scratch")
        monkeypatch.setenv("PODCASTER_STORAGE_ACCOUNT_URL", "https://acct.blob.core.windows.net")
        from podcaster.storage import AzureBlobStorageBackend

        backend = create_scratch_storage_backend()
        assert isinstance(backend, AzureBlobStorageBackend)


# --- IntermediateStore --------------------------------------------------------


class TestIntermediateStoreDisabled:
    def test_disabled_is_noop(self, tmp_path):
        store = IntermediateStore(None, "job-1")
        assert store.enabled is False
        assert store.exists("x") is False
        assert store.download("x", tmp_path / "out") is False
        assert store.upload("x", tmp_path / "missing") is False
        assert store.cleanup() == 0
        # mark / write are silent no-ops.
        store.mark("stage")
        assert store.load_manifest() == {}

    def test_create_from_env_disabled(self, monkeypatch):
        monkeypatch.delenv("PODCASTER_VIDEO_SCRATCH_CONTAINER", raising=False)
        store = create_intermediate_store("job-x")
        assert store.enabled is False
        assert store.job_id == "job-x"


class TestIntermediateStoreEnabled:
    @pytest.fixture
    def store(self, backend) -> IntermediateStore:
        return IntermediateStore(backend, "job-42")

    def test_blob_path_layout(self, store):
        assert store.blob_path("recording_000.mp4") == (
            "video-jobs/job-42/intermediates/recording_000.mp4"
        )
        assert store.prefix() == "video-jobs/job-42/intermediates/"

    def test_blob_path_rejects_empty(self, store):
        with pytest.raises(ValueError):
            store.blob_path("   ")

    def test_upload_download_roundtrip(self, store, tmp_path):
        src = tmp_path / "seg.mp4"
        src.write_bytes(b"segment-bytes")
        assert store.upload("recording_000.mp4", src, "video/mp4") is True
        assert store.exists("recording_000.mp4") is True

        dest = tmp_path / "resumed.mp4"
        assert store.download("recording_000.mp4", dest) is True
        assert dest.read_bytes() == b"segment-bytes"

    def test_upload_missing_source_is_false(self, store, tmp_path):
        assert store.upload("x.mp4", tmp_path / "nope.mp4") is False

    def test_text_roundtrip(self, store):
        assert store.write_text("recording_000.json", '{"a": 1}') is True
        assert store.read_text("recording_000.json") == '{"a": 1}'

    def test_mark_records_manifest(self, store):
        store.mark("recording_000", recovery_path="direct")
        store.mark("composed_video", duration_seconds=12.5)
        manifest = store.load_manifest()
        assert manifest["job_id"] == "job-42"
        assert manifest["stages"]["recording_000"] == {
            "status": "complete", "recovery_path": "direct"
        }
        assert manifest["stages"]["composed_video"]["duration_seconds"] == 12.5

    def test_cleanup_removes_all_intermediates(self, store, tmp_path):
        src = tmp_path / "seg.mp4"
        src.write_bytes(b"x")
        store.upload("recording_000.mp4", src, "video/mp4")
        store.upload("normalized_000.mp4", src, "video/mp4")
        store.write_text("manifest.json", "{}")

        deleted = store.cleanup()
        assert deleted == 3
        assert store.exists("recording_000.mp4") is False

    def test_exists_swallows_backend_error(self, tmp_path):
        class _Broken:
            def blob_exists(self, path):
                raise RuntimeError("boom")

        store = IntermediateStore(_Broken(), "job-err")
        assert store.exists("x") is False


# --- Verified upload + disk budget (issue #410 upload safety) -----------------


class TestVerifiedUpload:
    def test_upload_rejects_size_mismatch_and_drops_blob(self, tmp_path):
        """A truncated upload (blob size != local size) is not trusted, and the
        unverified blob is deleted so resume never reuses it."""

        class _ShortBackend:
            def __init__(self):
                self.deleted: list[str] = []

            def upload_file(self, path, source, content_type):
                return None

            def blob_size(self, path):
                return 1  # lies: shorter than the real source

            def delete_blob(self, path):
                self.deleted.append(path)
                return True

        backend = _ShortBackend()
        store = IntermediateStore(backend, "job-v")
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"\x00" * 4096)

        assert store.upload("normalized_000.mp4", src, "video/mp4") is False
        # The unverified checkpoint was dropped.
        assert backend.deleted == ["video-jobs/job-v/intermediates/normalized_000.mp4"]

    def test_upload_succeeds_when_size_matches(self, backend, tmp_path):
        store = IntermediateStore(backend, "job-v")
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"\x00" * 4096)
        assert store.upload("normalized_000.mp4", src, "video/mp4") is True
        assert backend.blob_size("video-jobs/job-v/intermediates/normalized_000.mp4") == 4096

    def test_upload_passes_when_backend_cannot_report_size(self, tmp_path):
        class _NoSizeBackend:
            def upload_file(self, path, source, content_type):
                return None

        store = IntermediateStore(_NoSizeBackend(), "job-v")
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"x")
        # No blob_size method → best-effort: the upload is trusted.
        assert store.upload("x.mp4", src, "video/mp4") is True


class TestDiskBudget:
    def test_raises_when_insufficient(self, tmp_path):
        from podcaster.video.intermediates import (
            InsufficientDiskError,
            ensure_disk_budget,
        )

        with pytest.raises(InsufficientDiskError):
            ensure_disk_budget(tmp_path, 10**18)  # ~1 EB required

    def test_passes_with_small_requirement(self, tmp_path):
        from podcaster.video.intermediates import ensure_disk_budget

        # Plenty of headroom; should not raise.
        ensure_disk_budget(tmp_path, 1024, margin=0)


class TestBlobSize:
    def test_local_blob_size_roundtrip(self, backend, tmp_path):
        src = tmp_path / "in.bin"
        src.write_bytes(b"abcdef")
        backend.upload_file("a/b/c.mp4", src, "video/mp4")
        assert backend.blob_size("a/b/c.mp4") == 6
        assert backend.blob_size("a/b/missing.mp4") is None
