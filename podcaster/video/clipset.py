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

from podcaster.video.budget import BudgetProjection
from podcaster.video.sync_plan import RepoReference, VideoSegment

#: Schema markers for the serialised fan-out plan. V1 always serialized
#: ``video_budget`` as either an object or null; V2 requires an object.
LEGACY_CLIPSET_SCHEMA_VERSION = "squadscope-podcaster-clipset-v1"
CLIPSET_SCHEMA_VERSION = "squadscope-podcaster-clipset-v2"


class ClipsetJobMismatchError(ValueError):
    """Raised when a clipset is loaded from another job's storage path."""


class ClipsetSchemaVersionError(ValueError):
    """Raised when a persisted clipset uses an unsupported schema."""


class ClipsetBudgetError(ValueError):
    """Raised when a budget-bearing clipset has an invalid parent budget."""


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


def clip_admission_blob_path(job_id: str, clip_index: int) -> str:
    """Durable first-admission timing facts for one clip."""
    return f"{clips_prefix(job_id)}{_index(clip_index):03d}.admission.json"


def clip_attempts_blob_path(job_id: str, clip_index: int) -> str:
    """Durable dequeued-execution history for one clip."""
    return f"{clips_prefix(job_id)}{_index(clip_index):03d}.attempts.json"


def clip_content_blob_path(job_id: str, clip_index: int, sha256: str) -> str:
    """Immutable content-addressed media path for one terminal clip."""
    digest = str(sha256).strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("sha256 must be a lowercase 64-character hex digest")
    return f"{clips_prefix(job_id)}{_index(clip_index):03d}/{digest}.webm"


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
    budget: BudgetProjection | None = None
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
    def from_segments(
        cls,
        job_id: str,
        segments: Sequence[VideoSegment],
        *,
        budget: BudgetProjection | None = None,
    ) -> "Clipset":
        clips = tuple(
            ClipPlanEntry.from_segment(index, segment) for index, segment in enumerate(segments)
        )
        return cls(
            job_id=_clean_job_id(job_id),
            clips=clips,
            budget=budget,
            schema_version=(
                CLIPSET_SCHEMA_VERSION if budget is not None else LEGACY_CLIPSET_SCHEMA_VERSION
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "count": self.count,
            "clips": [c.to_dict() for c in self.clips],
        }
        if self.schema_version == CLIPSET_SCHEMA_VERSION:
            if self.budget is None:
                raise ClipsetBudgetError("current clipset schema requires video_budget")
            data["video_budget"] = self.budget.to_dict()
        elif self.schema_version == LEGACY_CLIPSET_SCHEMA_VERSION:
            data["video_budget"] = self.budget.to_dict() if self.budget is not None else None
        else:
            raise ClipsetSchemaVersionError(
                f"unsupported clipset schema version {self.schema_version!r}"
            )
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        expected_job_id: str,
    ) -> "Clipset":
        if not isinstance(data, dict):
            raise ValueError("clipset payload must be a JSON object")
        schema_version = data.get("schema_version")
        if not isinstance(schema_version, str):
            raise ClipsetSchemaVersionError(
                f"unsupported clipset schema version {schema_version!r}"
            )
        if schema_version not in {
            LEGACY_CLIPSET_SCHEMA_VERSION,
            CLIPSET_SCHEMA_VERSION,
        }:
            raise ClipsetSchemaVersionError(
                f"unsupported clipset schema version {schema_version!r}"
            )
        if "video_budget" not in data:
            raise ClipsetBudgetError("clipset video_budget is missing")
        raw_budget = data["video_budget"]
        if schema_version == LEGACY_CLIPSET_SCHEMA_VERSION and raw_budget is None:
            budget = None
        elif not isinstance(raw_budget, dict):
            requirement = (
                "current clipset schema requires object video_budget"
                if schema_version == CLIPSET_SCHEMA_VERSION
                else "legacy clipset video_budget must be an object or null"
            )
            raise ClipsetBudgetError(requirement)
        else:
            try:
                budget = BudgetProjection.from_dict(raw_budget)
            except (KeyError, TypeError, ValueError) as exc:
                raise ClipsetBudgetError("clipset video_budget is invalid") from exc
        return cls._from_validated_dict(
            data,
            expected_job_id=expected_job_id,
            budget=budget,
            schema_version=str(schema_version),
        )

    @classmethod
    def from_legacy_v1_dict(
        cls,
        data: dict[str, Any],
        *,
        expected_job_id: str,
    ) -> "Clipset":
        """Parse an authentic v1 document with an object or null budget."""
        if not isinstance(data, dict):
            raise ValueError("clipset payload must be a JSON object")
        if data.get("schema_version") != LEGACY_CLIPSET_SCHEMA_VERSION:
            raise ClipsetSchemaVersionError("clipset is not a legacy v1 document")
        return cls.from_dict(data, expected_job_id=expected_job_id)

    @classmethod
    def _from_validated_dict(
        cls,
        data: dict[str, Any],
        *,
        expected_job_id: str,
        budget: BudgetProjection | None,
        schema_version: str,
    ) -> "Clipset":
        job_id = _clean_job_id(str(data["job_id"]))
        expected = _clean_job_id(expected_job_id)
        if job_id != expected:
            raise ClipsetJobMismatchError(
                f"clipset job_id {job_id!r} does not match expected job_id {expected!r}"
            )
        clips = tuple(ClipPlanEntry.from_dict(c) for c in data.get("clips", []))
        declared = data.get("count")
        if declared is not None and int(declared) != len(clips):
            raise ValueError(f"clipset count {declared} does not match {len(clips)} clip entries")
        return cls(
            job_id=job_id,
            clips=clips,
            budget=budget,
            schema_version=schema_version,
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_bytes(
        cls,
        payload: bytes | None,
        *,
        expected_job_id: str,
    ) -> "Clipset":
        if not payload:
            raise ValueError("clipset.json was empty or missing")
        return cls.from_dict(
            json.loads(payload.decode("utf-8")),
            expected_job_id=expected_job_id,
        )

    @classmethod
    def from_legacy_v1_bytes(
        cls,
        payload: bytes | None,
        *,
        expected_job_id: str,
    ) -> "Clipset":
        if not payload:
            raise ValueError("clipset.json was empty or missing")
        return cls.from_legacy_v1_dict(
            json.loads(payload.decode("utf-8")),
            expected_job_id=expected_job_id,
        )


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
