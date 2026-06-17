from __future__ import annotations

from pathlib import Path

import pytest


def _write_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


@pytest.fixture
def fake_mp4(tmp_path: Path) -> Path:
    header = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41"
    return _write_bytes(tmp_path / "episode.mp4", header + b"\x00" * 2048)


@pytest.fixture
def fake_webm(tmp_path: Path) -> Path:
    return _write_bytes(tmp_path / "segment.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 512)


@pytest.fixture
def fake_mp3(tmp_path: Path) -> Path:
    return _write_bytes(tmp_path / "episode.mp3", b"\xff\xfb\x90\x64" + b"\x00" * 2048)


@pytest.fixture
def sample_script() -> str:
    return """\
HOST_A: Let's start with https://github.com/octocat/hello-world for the first segment.
HOST_B: Then we will cover https://github.com/pallets/flask before wrapping up.
"""
