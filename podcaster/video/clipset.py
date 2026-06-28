"""Fan-out plan (``clipset.json``) for scale-out video recording (epic #552).

The scale-out design (``docs/scaleout-recorder-rfc.md`` §5) splits the video
pipeline into a **recorder** (records one clip per ``video-clip-jobs`` message)
and an **editor** (plans, fans out, then composes). The editor writes a single
immutable ``clipset.json`` to the ``video-scratch`` container describing the
expected clip set; each recorder loads **its** clip's plan slice from that file
so the queue message itself only needs to carry ``(job_id, clip_index)``.

This module owns the on-the-wire schema for that plan and the scratch blob-path
convention (reusing the existing ``video-jobs/{job_id}/…`` prefix from
:mod:`podcaster.video.intermediates`). It is intentionally free of any
recording / Playwright / compose logic so both roles can import it cheaply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from podcaster.video.sync_plan import RepoReference, VideoSegment

#: Schema marker for the serialised fan-out plan. Bump the minor for
#: backward-compatible additions, the major for breaking changes.
CLIPSET_SCHEMA_VERSION = "squadscope-podcaster-clipset-v1"

#: Root prefix for per-job scratch artifacts (matches
#: :data:`podcaster.video.intermediates.SCRATCH_ROOT`).
SCRATCH_ROOT = "video-jobs"

#: Sub-directory under a job prefix holding per-clip outputs.
CLIPS_DIR = "clips"


def job_prefix(job_id: str) -> str:
    """Return the ``video-jobs/{job_id}/`` scratch prefix for *job_id*."""
    return f"{SCRATCH_ROOT}/{_clean_job_id(job_id)}/"


def clipset_blob_path(job_id: str) -> str:
    """Blob path of the editor-written ``clipset.json`` fan-out plan."""
    return f"{job_prefix(job_id)}clipset.json"


def clips_prefix(job_id: str) -> str:
    """Prefix under which all per-clip blobs for *job_id* live."""
    return f"{job_prefix(job_id)}{CLIPS_DIR}/"


def clip_blob_path(job_id: str, clip_index: int) -> str:
    """Blob path of a recorder's raw ``.webm`` clip for *clip_index*."""
    return f"{clips_prefix(job_id)}{_index(clip_index):03d}.webm"


def clip_manifest_blob_path(job_id: str, clip_index: int) -> str:
    """Blob path of the per-clip terminal ``manifest.json`` for *clip_index*.

    The manifest is the **completion sentinel** (RFC §5): it is written strictly
    after the size-verified ``.webm`` and the fan-in barrier keys off its
    presence.
    """
    return f"{clips_prefix(job_id)}{_index(clip_index):03d}.manifest.json"


def _clean_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id is required")
    return job_id.strip()


def _index(clip_index: int) -> int:
    if isinstance(clip_index, bool) or not isinstance(clip_index, int):
        raise ValueError("clip_index must be an int")
    if clip_index < 0:
        raise ValueError("clip_index must be non-negative")
    return clip_index


@dataclass(frozen=True)
class ClipPlanEntry:
    """One clip's plan slice — enough to reconstruct its :class:`VideoSegment`.

    A recorder loads exactly one of these (by ``clip_index``) and replays it
    through the unchanged recording logic, so this carries every field a
    :class:`VideoSegment` needs but **no** secrets/PII (just public repo URLs
    and timings).
    """

    clip_index: int
    start_seconds: float
    duration_seconds: float
    repo_owner: str | None = None
    repo_name: str | None = None
    source_url: str | None = None
    removed_reason: str | None = None

    @property
    def repo_url(self) -> str | None:
        """Public repo URL for the clip, or ``None`` for a generic segment."""
        if self.repo_owner and self.repo_name:
            return f"https://github.com/{self.repo_owner}/{self.repo_name}"
        return None

    @classmethod
    def from_segment(cls, clip_index: int, segment: VideoSegment) -> "ClipPlanEntry":
        repo = segment.repo
        return cls(
            clip_index=_index(clip_index),
            start_seconds=float(segment.start_seconds),
            duration_seconds=float(segment.duration_seconds),
            repo_owner=repo.owner if repo is not None else None,
            repo_name=repo.name if repo is not None else None,
            source_url=segment.source_url,
            removed_reason=segment.removed_reason,
        )

    def to_segment(self) -> VideoSegment:
        repo = None
        if self.repo_owner and self.repo_name:
            repo = RepoReference(owner=self.repo_owner, name=self.repo_name)
        return VideoSegment(
            start_seconds=float(self.start_seconds),
            duration_seconds=float(self.duration_seconds),
            repo=repo,
            source_url=self.source_url,
            removed_reason=self.removed_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_index": self.clip_index,
            "start_seconds": self.start_seconds,
            "duration_seconds": self.duration_seconds,
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "source_url": self.source_url,
            "removed_reason": self.removed_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClipPlanEntry":
        return cls(
            clip_index=_index(int(data["clip_index"])),
            start_seconds=float(data["start_seconds"]),
            duration_seconds=float(data["duration_seconds"]),
            repo_owner=_opt_str(data.get("repo_owner")),
            repo_name=_opt_str(data.get("repo_name")),
            source_url=_opt_str(data.get("source_url")),
            removed_reason=_opt_str(data.get("removed_reason")),
        )


@dataclass(frozen=True)
class Clipset:
    """The editor's immutable fan-out plan: the expected clip set for a job."""

    job_id: str
    clips: tuple[ClipPlanEntry, ...]
    schema_version: str = CLIPSET_SCHEMA_VERSION

    @property
    def count(self) -> int:
        return len(self.clips)

    def indices(self) -> list[int]:
        """Expected ``clip_index`` values in plan order."""
        return [c.clip_index for c in self.clips]

    def entry(self, clip_index: int) -> ClipPlanEntry:
        """Return the plan slice for *clip_index*.

        Raises :class:`KeyError` when the index is not part of this plan so a
        recorder treats an out-of-plan message as a hard failure.
        """
        for clip in self.clips:
            if clip.clip_index == clip_index:
                return clip
        raise KeyError(f"clip_index {clip_index} is not in clipset for job {self.job_id}")

    @classmethod
    def from_segments(cls, job_id: str, segments: Sequence[VideoSegment]) -> "Clipset":
        clips = tuple(
            ClipPlanEntry.from_segment(index, segment) for index, segment in enumerate(segments)
        )
        return cls(job_id=_clean_job_id(job_id), clips=clips)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "count": self.count,
            "clips": [c.to_dict() for c in self.clips],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Clipset":
        if not isinstance(data, dict):
            raise ValueError("clipset payload must be a JSON object")
        clips = tuple(ClipPlanEntry.from_dict(c) for c in data.get("clips", []))
        declared = data.get("count")
        if declared is not None and int(declared) != len(clips):
            raise ValueError(f"clipset count {declared} does not match {len(clips)} clip entries")
        return cls(
            job_id=_clean_job_id(str(data["job_id"])),
            clips=clips,
            schema_version=str(data.get("schema_version", CLIPSET_SCHEMA_VERSION)),
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_bytes(cls, payload: bytes | None) -> "Clipset":
        if not payload:
            raise ValueError("clipset.json was empty or missing")
        return cls.from_dict(json.loads(payload.decode("utf-8")))


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
