"""Composite video segments with ffmpeg: concat, transitions, lower-thirds.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Phase 3: takes recorded WebM segments from video_gen and produces a single
YouTube/Spotify-ready MP4 with crossfade transitions and lower-third overlays.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from podcaster.video.sync_plan import EpisodePlan, VideoSegment
from podcaster.video.video_gen import RecordedSegment

logger = logging.getLogger(__name__)

# --- drawtext binary detection (#282) ---

# Module-level cache: populated on first call to _find_drawtext_capable_ffmpeg().
_drawtext_cache: dict[str, str | None] = {}

# Preferred candidates: system ffmpeg first (has libfreetype on Ubuntu/Debian),
# then whatever 'ffmpeg' resolves on PATH (may be a static binary without drawtext).
_DRAWTEXT_CANDIDATES = ["/usr/bin/ffmpeg", "ffmpeg"]


def _probe_drawtext_ffmpeg(
    candidates: list[str] | None = None,
) -> str | None:
    """Return the first ffmpeg binary in *candidates* that supports drawtext.

    Probes each candidate via ``ffmpeg -hide_banner -filters`` and accepts a
    zero-exit probe whose output contains 'drawtext'.  Returns None if no
    candidate supports drawtext.
    Not cached — call :func:`_find_drawtext_capable_ffmpeg` for the cached version.
    """
    if candidates is None:
        candidates = _DRAWTEXT_CANDIDATES
    for binary in candidates:
        try:
            proc = subprocess.run(
                [binary, "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0 and (
                "drawtext" in proc.stdout or "drawtext" in proc.stderr
            ):
                return binary
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
    return None


def _find_drawtext_capable_ffmpeg() -> str | None:
    """Return a drawtext-capable ffmpeg binary path, or None if unavailable.

    Result is cached after the first call to avoid repeated subprocess probes.
    """
    if "binary" not in _drawtext_cache:
        _drawtext_cache["binary"] = _probe_drawtext_ffmpeg()
    return _drawtext_cache["binary"]


# --- Constants ---

OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
OUTPUT_FPS = 30
TRANSITION_DURATION = 1.0  # seconds
LOWER_THIRD_DURATION = 5.0  # seconds
LOWER_THIRD_FONT_SIZE = 36
LOWER_THIRD_BOX_OPACITY = 0.6
LOWER_THIRD_Y_POSITION = "h-h/6"
LOWER_THIRD_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Final encode settings (YouTube/Spotify-ready)
ENCODE_PRESET = "slow"
ENCODE_CRF = 18
ENCODE_PIX_FMT = "yuv420p"
ENCODE_AUDIO_BITRATE = "192k"

# --- Transition types (#298) ---
# Valid xfade transition names (subset chosen for quality and file-size impact)

TRANSITION_FADE = "fade"              # smooth crossfade — universal default
TRANSITION_FADE_BLACK = "fadeblack"   # fade through black — good for intro joins
TRANSITION_SLIDE_LEFT = "slideleft"   # slide incoming segment in from the right
TRANSITION_SLIDE_RIGHT = "slideright" # slide incoming segment in from the left
TRANSITION_WIPE_LEFT = "wipeleft"     # wipe — clean, good for content→outro
TRANSITION_WIPE_RIGHT = "wiperight"

# Boundary kind identifiers (caller classifies each N-1 boundary for N segments)
BOUNDARY_INTRO_TO_CONTENT = "intro_to_content"
BOUNDARY_CONTENT_TO_CONTENT = "content_to_content"
BOUNDARY_CONTENT_TO_OUTRO = "content_to_outro"

# Rotation used for content→content boundaries (cycles to add variety)
_CONTENT_ROTATION: list[str] = [
    TRANSITION_FADE,
    TRANSITION_SLIDE_LEFT,
    TRANSITION_WIPE_LEFT,
    TRANSITION_SLIDE_RIGHT,
]


class CommandRunner(Protocol):
    """Protocol for running shell commands (allows mocking)."""

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Default command runner using subprocess."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )


@dataclass
class LowerThird:
    """Lower-third text overlay metadata."""

    text: str
    url: str
    start_seconds: float
    end_seconds: float


@dataclass
class ComposeResult:
    """Result of compositing video segments."""

    output_path: Path
    duration_seconds: float
    segment_count: int
    has_audio: bool = False


def _build_normalize_cmd(
    input_path: Path,
    output_path: Path,
) -> list[str]:
    """Build ffmpeg command to normalize a clip to 1080p/30fps."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-i", str(input_path),
        "-vf", f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps={OUTPUT_FPS}",
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", str(ENCODE_CRF),
        "-pix_fmt", ENCODE_PIX_FMT,
        str(output_path),
    ]


def select_transitions(
    n_boundaries: int,
    boundary_kinds: list[str] | None = None,
) -> list[str]:
    """Return one transition type per segment boundary.

    Varies transitions to keep the video dynamic.  Per-boundary rules:

    * ``"intro_to_content"`` → :data:`TRANSITION_FADE_BLACK` (dramatic join)
    * ``"content_to_outro"``  → :data:`TRANSITION_WIPE_LEFT` (clean exit)
    * ``"content_to_content"`` (default) → cycles through
      :data:`_CONTENT_ROTATION` (fade → slideleft → wipeleft → slideright → …)

    Args:
        n_boundaries: Number of segment boundaries (= number of segments − 1).
        boundary_kinds: One kind string per boundary.  Defaults to all
            ``"content_to_content"`` when ``None``.

    Returns:
        List of xfade transition-type strings, length ``n_boundaries``.

    Raises:
        ValueError: If *boundary_kinds* length ≠ *n_boundaries*.
    """
    if n_boundaries <= 0:
        return []

    kinds = boundary_kinds or [BOUNDARY_CONTENT_TO_CONTENT] * n_boundaries
    if len(kinds) != n_boundaries:
        raise ValueError(
            f"boundary_kinds length ({len(kinds)}) must equal "
            f"n_boundaries ({n_boundaries})"
        )

    content_idx = 0
    result: list[str] = []
    for kind in kinds:
        if kind == BOUNDARY_INTRO_TO_CONTENT:
            result.append(TRANSITION_FADE_BLACK)
        elif kind == BOUNDARY_CONTENT_TO_OUTRO:
            result.append(TRANSITION_WIPE_LEFT)
        else:
            result.append(_CONTENT_ROTATION[content_idx % len(_CONTENT_ROTATION)])
            content_idx += 1
    return result


def _build_xfade_filter(
    segment_durations: list[float],
    transition_duration: float = TRANSITION_DURATION,
    transitions: list[str] | None = None,
) -> str:
    """Build the xfade filter chain for N segments.

    For N segments, creates N-1 xfade transitions chained together.
    Each xfade offset is calculated as cumulative duration minus transition overlap.

    Args:
        segment_durations: Duration of each normalised segment in seconds.
        transition_duration: Duration of each transition in seconds.
        transitions: One xfade transition-type string per boundary (length =
            ``len(segment_durations) - 1``).  When ``None``,
            :func:`select_transitions` is called with default boundary kinds so
            transitions are automatically varied.

    Returns:
        ffmpeg ``filter_complex`` fragment string, or ``""`` for < 2 segments.

    Raises:
        ValueError: If *transitions* length doesn't match the boundary count.
    """
    n = len(segment_durations)
    if n < 2:
        return ""

    n_boundaries = n - 1
    if transitions is None:
        transitions = select_transitions(n_boundaries)
    elif len(transitions) != n_boundaries:
        raise ValueError(
            f"transitions length ({len(transitions)}) must equal "
            f"n_boundaries ({n_boundaries})"
        )

    filters: list[str] = []
    # First transition: [0:v][1:v]xfade=...
    offset = segment_durations[0] - transition_duration
    filters.append(
        f"[0:v][1:v]xfade=transition={transitions[0]}:duration={transition_duration}"
        f":offset={offset:.3f}[v01]"
    )

    cumulative = segment_durations[0] + segment_durations[1] - transition_duration
    for i in range(2, n):
        in_label = "v01" if i == 2 else f"vx{i-1}"
        out_label = f"vx{i}" if i < n - 1 else "vout"

        offset = cumulative - transition_duration
        filters.append(
            f"[{in_label}][{i}:v]xfade=transition={transitions[i - 1]}"
            f":duration={transition_duration}:offset={offset:.3f}[{out_label}]"
        )
        cumulative += segment_durations[i] - transition_duration

    return ";".join(filters)


def _build_drawtext_filter(
    lower_thirds: list[LowerThird],
    input_label: str = "vout",
) -> str:
    """Build drawtext filter chain for lower-third overlays."""
    if not lower_thirds:
        return ""

    filters: list[str] = []
    current_label = input_label

    for i, lt in enumerate(lower_thirds):
        out_label = f"lt{i}" if i < len(lower_thirds) - 1 else "final"
        # Escape text for ffmpeg drawtext
        escaped_text = lt.text.replace(":", r"\:").replace("'", r"\'")
        escaped_url = lt.url.replace(":", r"\:").replace("'", r"\'")

        # Two-line lower third: repo name on top, URL below
        name_filter = (
            f"drawtext=fontfile={LOWER_THIRD_FONT}"
            f":text='{escaped_text}'"
            f":fontsize={LOWER_THIRD_FONT_SIZE}"
            f":fontcolor=white"
            f":box=1:boxcolor=black@{LOWER_THIRD_BOX_OPACITY}"
            f":boxborderw=10"
            f":x=40:y={LOWER_THIRD_Y_POSITION}"
            f":enable='between(t,{lt.start_seconds:.3f},{lt.end_seconds:.3f})'"
        )
        url_filter = (
            f"drawtext=fontfile={LOWER_THIRD_FONT}"
            f":text='{escaped_url}'"
            f":fontsize={LOWER_THIRD_FONT_SIZE - 8}"
            f":fontcolor=white@0.8"
            f":box=1:boxcolor=black@{LOWER_THIRD_BOX_OPACITY}"
            f":boxborderw=10"
            f":x=40:y={LOWER_THIRD_Y_POSITION}+{LOWER_THIRD_FONT_SIZE}+10"
            f":enable='between(t,{lt.start_seconds:.3f},{lt.end_seconds:.3f})'"
        )

        filters.append(
            f"[{current_label}]{name_filter},{url_filter}[{out_label}]"
        )
        current_label = out_label

    return ";".join(filters)


def _compute_lower_thirds(
    segments: list[RecordedSegment],
    transition_duration: float = TRANSITION_DURATION,
) -> list[LowerThird]:
    """Compute lower-third timings from recorded segments.

    Each lower-third appears at the start of its segment (after transition)
    and lasts LOWER_THIRD_DURATION seconds or until segment ends.
    """
    lower_thirds: list[LowerThird] = []
    cumulative_time = 0.0

    for i, rec in enumerate(segments):
        seg = rec.segment
        # After first segment, account for transition overlap
        if i > 0:
            start = cumulative_time
        else:
            start = 0.0

        # Lower third starts slightly after segment begins
        lt_start = start + 0.5
        lt_end = min(lt_start + LOWER_THIRD_DURATION, start + seg.duration_seconds - 0.5)

        if lt_end > lt_start:
            lower_thirds.append(LowerThird(
                text=f"{seg.repo.owner}/{seg.repo.name}",
                url=seg.repo.url,
                start_seconds=lt_start,
                end_seconds=lt_end,
            ))

        cumulative_time += seg.duration_seconds
        if i < len(segments) - 1:
            cumulative_time -= transition_duration

    return lower_thirds


def compose_video(
    segments: list[RecordedSegment],
    audio_path: Path | None = None,
    output_path: Path | None = None,
    output_dir: Path | None = None,
    runner: CommandRunner | None = None,
    transition_duration: float = TRANSITION_DURATION,
    boundary_kinds: list[str] | None = None,
) -> ComposeResult:
    """Compose recorded segments into a single MP4 with transitions and overlays.

    Args:
        segments: Recorded video segments from video_gen.record_episode().
        audio_path: Optional episode audio track to mix in.
        output_path: Explicit output file path. Overrides output_dir.
        output_dir: Directory for output. Uses temp dir if neither path is given.
        runner: Command runner (for testing). Uses subprocess if None.
        transition_duration: Duration of each xfade transition in seconds.
            Default 1.0 s.  Must be > 0 and < shortest segment duration.
        boundary_kinds: One boundary-kind string per segment boundary
            (length = ``len(segments) - 1``).  Classifies each transition as
            ``"intro_to_content"``, ``"content_to_content"`` (default), or
            ``"content_to_outro"`` so different transition types are applied
            per boundary.  Pass ``None`` (default) to use automatic cycling.

    Returns:
        ComposeResult with path to the final MP4.

    Raises:
        ValueError: If segments is empty.
        subprocess.CalledProcessError: If ffmpeg fails.
    """
    if not segments:
        raise ValueError("No segments provided for composition")

    if transition_duration <= 0:
        raise ValueError("transition_duration must be positive")

    min_seg = min(seg.segment.duration_seconds for seg in segments)
    if transition_duration >= min_seg:
        raise ValueError(
            f"transition_duration ({transition_duration}s) must be less than "
            f"the shortest segment duration ({min_seg}s)"
        )

    run = runner or _default_runner

    if output_path is None:
        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="video_compose_"))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "episode.mp4"

    # Step 1: Normalize all segments to 1080p/30fps
    normalized_paths: list[Path] = []
    norm_dir = output_path.parent / "normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)

    for i, rec in enumerate(segments):
        norm_path = norm_dir / f"seg_{i:03d}.mp4"
        cmd = _build_normalize_cmd(rec.video_path, norm_path)
        logger.info("Normalizing segment %d: %s", i, rec.video_path.name)
        run(cmd)
        normalized_paths.append(norm_path)

    # Step 2: Build the composition filter
    durations = [seg.segment.duration_seconds for seg in segments]

    if len(segments) == 1:
        # Single segment — no xfade needed
        video_label = "0:v"
        filter_complex_parts: list[str] = []
    else:
        # Build xfade chain with varied transitions
        n_boundaries = len(segments) - 1
        chosen_transitions = select_transitions(n_boundaries, boundary_kinds)
        xfade_filter = _build_xfade_filter(
            durations, transition_duration, chosen_transitions
        )
        filter_complex_parts = [xfade_filter] if xfade_filter else []

        if len(segments) == 2:
            video_label = "v01"
        else:
            video_label = "vout"

    # Step 3: Add lower-third overlays — only if a drawtext-capable ffmpeg is available.
    lower_thirds = _compute_lower_thirds(segments, transition_duration)
    compose_ffmpeg_bin = "ffmpeg"  # default; overridden below when drawtext is used
    if lower_thirds:
        drawtext_bin = _find_drawtext_capable_ffmpeg()
        if drawtext_bin:
            drawtext_filter = _build_drawtext_filter(lower_thirds, video_label)
            if drawtext_filter:
                filter_complex_parts.append(drawtext_filter)
                video_label = "final"
                compose_ffmpeg_bin = drawtext_bin
        else:
            logger.warning(
                "No ffmpeg binary with drawtext filter (libfreetype) found; "
                "skipping lower-third overlays.  Install system ffmpeg "
                "(e.g. apt install ffmpeg) or a build with libfreetype to enable overlays."
            )

    # Step 4: Build final ffmpeg command
    cmd: list[str] = [compose_ffmpeg_bin, "-hide_banner", "-loglevel", "warning", "-y"]

    # Add all normalized video inputs
    for norm_path in normalized_paths:
        cmd.extend(["-i", str(norm_path)])

    # Add audio input if provided
    audio_input_idx = len(normalized_paths)
    if audio_path is not None:
        cmd.extend(["-i", str(audio_path)])

    # Add filter_complex if we have filters
    if filter_complex_parts:
        cmd.extend(["-filter_complex", ";".join(filter_complex_parts)])
        cmd.extend(["-map", f"[{video_label}]"])
    else:
        cmd.extend(["-map", "0:v"])

    # Map audio
    if audio_path is not None:
        cmd.extend(["-map", f"{audio_input_idx}:a"])
        cmd.extend(["-c:a", "aac", "-b:a", ENCODE_AUDIO_BITRATE])

    # Output encoding
    cmd.extend([
        "-c:v", "libx264",
        "-preset", ENCODE_PRESET,
        "-crf", str(ENCODE_CRF),
        "-pix_fmt", ENCODE_PIX_FMT,
        "-movflags", "+faststart",
        str(output_path),
    ])

    logger.info("Composing %d segments into %s", len(segments), output_path)
    run(cmd)

    # Calculate output duration (accounting for transition overlaps)
    total_duration = sum(durations) - transition_duration * max(0, len(segments) - 1)

    return ComposeResult(
        output_path=output_path,
        duration_seconds=total_duration,
        segment_count=len(segments),
        has_audio=audio_path is not None,
    )


# --- Recording-to-plan sync utilities (#296) ---


@dataclass
class SyncedSegment:
    """A recorded segment paired with its target time window from the episode plan.

    Produced by :func:`build_sync_map`.  Pass a list of these to
    :func:`apply_sync` to trim recordings that exceed their window.
    """

    recorded: RecordedSegment
    target_start_seconds: float
    target_duration_seconds: float

    @property
    def needs_trim(self) -> bool:
        """True when the recording duration exceeds the target window by > 0.1 s."""
        return (
            self.recorded.segment.duration_seconds
            > self.target_duration_seconds + 0.1
        )


def build_sync_map(
    plan: EpisodePlan,
    recordings: Sequence[RecordedSegment],
) -> list[SyncedSegment]:
    """Match each recorded segment to its target time window from *plan*.

    Pairs recordings to plan segments by repo URL, preserving plan order.
    Recordings with no matching plan segment are ignored; plan segments with
    no matching recording raise ``ValueError``.

    Args:
        plan: Episode plan with target timing (e.g. from
            :func:`~podcaster.video.sync_plan.plan_from_script_timed`).
        recordings: Recorded segments produced by
            :func:`~podcaster.video.video_gen.record_episode`.

    Returns:
        Ordered list of :class:`SyncedSegment` following plan ordering.

    Raises:
        ValueError: If any plan segment has no matching recording.
    """
    by_url: dict[str, RecordedSegment] = {
        rec.segment.repo.url: rec for rec in recordings
    }
    result: list[SyncedSegment] = []
    for seg in plan.segments:
        url = seg.repo.url
        if url not in by_url:
            raise ValueError(
                f"No recording found for plan segment {url!r}. "
                f"Available: {sorted(by_url)}"
            )
        result.append(
            SyncedSegment(
                recorded=by_url[url],
                target_start_seconds=seg.start_seconds,
                target_duration_seconds=seg.duration_seconds,
            )
        )
    return result


def trim_recording_cmd(
    input_path: Path,
    start_seconds: float,
    duration_seconds: float,
    output_path: Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build an ffmpeg stream-copy command to trim a recording.

    Seeks to *start_seconds* and captures *duration_seconds* of video using
    ``-c copy`` (no re-encode).

    Args:
        input_path: Source video file.
        start_seconds: Seek offset within the source file (usually 0.0).
        duration_seconds: Duration to extract.
        output_path: Destination file (same format as input).
        ffmpeg_bin: Path or name of the ffmpeg binary.

    Returns:
        Command list suitable for :func:`subprocess.run`.
    """
    return [
        ffmpeg_bin, "-hide_banner", "-loglevel", "warning", "-y",
        "-ss", f"{start_seconds:.3f}",
        "-i", str(input_path),
        "-t", f"{duration_seconds:.3f}",
        "-c", "copy",
        str(output_path),
    ]


def apply_sync(
    sync_map: list[SyncedSegment],
    output_dir: Path,
    ffmpeg_bin: str = "ffmpeg",
    runner: CommandRunner | None = None,
) -> list[RecordedSegment]:
    """Trim recordings to their target windows and return updated segments.

    For each :class:`SyncedSegment` where :attr:`~SyncedSegment.needs_trim`
    is True, runs :func:`trim_recording_cmd` to shorten the file.  Recordings
    already within the target duration are used as-is (no copy, no re-encode).
    All returned :class:`RecordedSegment` objects have their
    ``segment.start_seconds`` updated to ``target_start_seconds`` so they are
    ready to pass directly to :func:`compose_video`.

    Args:
        sync_map: Synced segments from :func:`build_sync_map`.
        output_dir: Directory for trimmed output files.
        ffmpeg_bin: ffmpeg binary to use for trimming.
        runner: Command runner for testing. Uses :func:`subprocess.run` if None.

    Returns:
        List of :class:`RecordedSegment` instances with updated timing and
        (where necessary) trimmed video paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    run = runner or _default_runner
    result: list[RecordedSegment] = []

    for i, ss in enumerate(sync_map):
        if ss.needs_trim:
            stem = ss.recorded.video_path.stem
            suffix = ss.recorded.video_path.suffix
            trimmed_path = output_dir / f"{stem}_trimmed_{i:03d}{suffix}"
            cmd = trim_recording_cmd(
                input_path=ss.recorded.video_path,
                start_seconds=0.0,
                duration_seconds=ss.target_duration_seconds,
                output_path=trimmed_path,
                ffmpeg_bin=ffmpeg_bin,
            )
            logger.info(
                "Trimming %s: %.1fs → %.1fs",
                ss.recorded.video_path.name,
                ss.recorded.segment.duration_seconds,
                ss.target_duration_seconds,
            )
            run(cmd)
            video_path = trimmed_path
            duration = ss.target_duration_seconds
        else:
            video_path = ss.recorded.video_path
            duration = ss.recorded.segment.duration_seconds

        new_segment = VideoSegment(
            repo=ss.recorded.segment.repo,
            start_seconds=ss.target_start_seconds,
            duration_seconds=duration,
        )
        result.append(
            RecordedSegment(
                segment=new_segment,
                video_path=video_path,
                is_fallback=ss.recorded.is_fallback,
                has_pages=ss.recorded.has_pages,
            )
        )

    return result
