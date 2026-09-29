from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from podcaster.storage import LocalStorageBackend

StorageAction = Callable[[LocalStorageBackend, str, Path], object]


def _put_bytes(storage: LocalStorageBackend, path: str, tmp_path: Path) -> object:
    return storage.put_bytes(path, b"new", "text/plain")


def _get_bytes(storage: LocalStorageBackend, path: str, tmp_path: Path) -> object:
    return storage.get_bytes(path)


def _update_bytes(storage: LocalStorageBackend, path: str, tmp_path: Path) -> object:
    return storage.update_bytes(path, "text/plain", lambda _current: b"updated")


def _blob_size(storage: LocalStorageBackend, path: str, tmp_path: Path) -> object:
    return storage.blob_size(path)


def _upload_file(storage: LocalStorageBackend, path: str, tmp_path: Path) -> object:
    source = tmp_path / "source.txt"
    source.write_bytes(b"uploaded")
    return storage.upload_file(path, source, "text/plain")


def _download_file(storage: LocalStorageBackend, path: str, tmp_path: Path) -> object:
    return storage.download_file(path, tmp_path / "downloaded.txt")


def _delete_blob(storage: LocalStorageBackend, path: str, tmp_path: Path) -> object:
    return storage.delete_blob(path)


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("put_bytes", _put_bytes),
        ("get_bytes", _get_bytes),
        ("update_bytes", _update_bytes),
        ("blob_size", _blob_size),
        ("upload_file", _upload_file),
        ("download_file", _download_file),
        ("delete_blob", _delete_blob),
    ],
)
@pytest.mark.parametrize("unsafe_path", ["../outside/blob.txt", "/tmp/blob.txt"])
def test_local_storage_sinks_reject_traversal_and_absolute_paths(
    tmp_path: Path,
    name: str,
    action: StorageAction,
    unsafe_path: str,
) -> None:
    storage = LocalStorageBackend(tmp_path / "root", "https://example.invalid/artifacts")

    with pytest.raises(ValueError):
        action(storage, unsafe_path, tmp_path)


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("put_bytes", _put_bytes),
        ("get_bytes", _get_bytes),
        ("update_bytes", _update_bytes),
        ("blob_size", _blob_size),
        ("upload_file", _upload_file),
        ("download_file", _download_file),
        ("delete_blob", _delete_blob),
    ],
)
def test_local_storage_sinks_reject_symlink_escape(
    tmp_path: Path,
    name: str,
    action: StorageAction,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    outside_blob = outside / "blob.txt"
    outside_blob.write_bytes(b"outside")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    storage = LocalStorageBackend(root, "https://example.invalid/artifacts")

    with pytest.raises(ValueError):
        action(storage, "escape/blob.txt", tmp_path)

    assert outside_blob.read_bytes() == b"outside"


def test_upload_file_does_not_create_temp_file_after_parent_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    source = tmp_path / "source.txt"
    source.write_bytes(b"uploaded")
    target_parent = root / "nested"
    original_mkdir = Path.mkdir

    def mkdir_and_swap(self: Path, *args: object, **kwargs: object) -> None:
        original_mkdir(self, *args, **kwargs)
        if self == target_parent:
            self.rmdir()
            self.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(Path, "mkdir", mkdir_and_swap)
    storage = LocalStorageBackend(root, "https://example.invalid/artifacts")

    with pytest.raises(OSError):
        storage.upload_file("nested/blob.txt", source, "text/plain")

    assert list(outside.iterdir()) == []
