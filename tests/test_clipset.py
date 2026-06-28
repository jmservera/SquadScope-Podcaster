"""Unit tests for the fan-out plan (``clipset.json``) schema (#562, RFC §5)."""

from __future__ import annotations

import pytest

from podcaster.video.clipset import (
    CLIPSET_SCHEMA_VERSION,
    ClipPlanEntry,
    Clipset,
    clip_blob_path,
    clip_manifest_blob_path,
    clips_prefix,
    clipset_blob_path,
    job_prefix,
)
from podcaster.video.sync_plan import RepoReference, VideoSegment


def _segments() -> list[VideoSegment]:
    return [
        VideoSegment(start_seconds=0.0, duration_seconds=30.0),  # generic
        VideoSegment(
            start_seconds=30.0,
            duration_seconds=45.5,
            repo=RepoReference(owner="octo", name="api"),
        ),
        VideoSegment(
            start_seconds=75.5,
            duration_seconds=20.0,
            repo=RepoReference(owner="octo", name="gone"),
            removed_reason="404",
        ),
    ]


def test_blob_path_helpers_use_zero_padded_index() -> None:
    assert job_prefix("job-1") == "video-jobs/job-1/"
    assert clipset_blob_path("job-1") == "video-jobs/job-1/clipset.json"
    assert clips_prefix("job-1") == "video-jobs/job-1/clips/"
    assert clip_blob_path("job-1", 7) == "video-jobs/job-1/clips/007.webm"
    assert clip_manifest_blob_path("job-1", 7) == "video-jobs/job-1/clips/007.manifest.json"


def test_clipset_round_trips_through_bytes() -> None:
    clipset = Clipset.from_segments("job-1", _segments())
    assert clipset.count == 3
    assert clipset.indices() == [0, 1, 2]
    assert clipset.schema_version == CLIPSET_SCHEMA_VERSION

    restored = Clipset.from_bytes(clipset.to_json_bytes())
    assert restored == clipset


def test_entry_to_segment_preserves_repo_and_timings() -> None:
    clipset = Clipset.from_segments("job-1", _segments())

    generic = clipset.entry(0).to_segment()
    assert generic.repo is None
    assert generic.start_seconds == 0.0
    assert generic.duration_seconds == 30.0

    repo_seg = clipset.entry(1).to_segment()
    assert repo_seg.repo == RepoReference(owner="octo", name="api")
    assert repo_seg.duration_seconds == 45.5

    removed_seg = clipset.entry(2).to_segment()
    assert removed_seg.removed_reason == "404"
    assert removed_seg.is_removed


def test_entry_repo_url() -> None:
    clipset = Clipset.from_segments("job-1", _segments())
    assert clipset.entry(0).repo_url is None
    assert clipset.entry(1).repo_url == "https://github.com/octo/api"


def test_entry_missing_index_raises_keyerror() -> None:
    clipset = Clipset.from_segments("job-1", _segments())
    with pytest.raises(KeyError):
        clipset.entry(99)


def test_from_bytes_rejects_empty() -> None:
    with pytest.raises(ValueError):
        Clipset.from_bytes(None)
    with pytest.raises(ValueError):
        Clipset.from_bytes(b"")


def test_from_dict_rejects_count_mismatch() -> None:
    data = Clipset.from_segments("job-1", _segments()).to_dict()
    data["count"] = 99
    with pytest.raises(ValueError):
        Clipset.from_dict(data)


def test_plan_entry_round_trip() -> None:
    entry = ClipPlanEntry(
        clip_index=4,
        start_seconds=1.0,
        duration_seconds=2.0,
        repo_owner="a",
        repo_name="b",
        source_url="https://example.com",
    )
    assert ClipPlanEntry.from_dict(entry.to_dict()) == entry


@pytest.mark.parametrize("bad", [-1, True, "3"])
def test_clip_blob_path_rejects_bad_index(bad) -> None:
    with pytest.raises(ValueError):
        clip_blob_path("job-1", bad)
