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
from typing import Protocol

from podcaster.video.video_gen import RecordedSegment

logger = logging.getLogger(__name__)

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


def _build_xfade_filter(
    segment_durations: list[float],
    transition_duration: float = TRANSITION_DURATION,
) -> str:
    """Build the xfade filter chain for N segments.

    For N segments, creates N-1 xfade transitions chained together.
    Each xfade offset is calculated as cumulative duration minus transition overlap.
    """
    if len(segment_durations) < 2:
        return ""

    filters: list[str] = []
    # First transition: [0:v][1:v]xfade=...
    offset = segment_durations[0] - transition_duration
    filters.append(
        f"[0:v][1:v]xfade=transition=fadeblack:duration={transition_duration}"
        f":offset={offset:.3f}[v01]"
    )

    cumulative = segment_durations[0] + segment_durations[1] - transition_duration
    for i in range(2, len(segment_durations)):
        in_label = "v01" if i == 2 else f"vx{i-1}"
        out_label = f"vx{i}" if i < len(segment_durations) - 1 else "vout"

        offset = cumulative - transition_duration
        filters.append(
            f"[{in_label}][{i}:v]xfade=transition=fadeblack"
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
) -> ComposeResult:
    """Compose recorded segments into a single MP4 with transitions and overlays.

    Args:
        segments: Recorded video segments from video_gen.record_episode().
        audio_path: Optional episode audio track to mix in.
        output_path: Explicit output file path. Overrides output_dir.
        output_dir: Directory for output. Uses temp dir if neither path is given.
        runner: Command runner (for testing). Uses subprocess if None.
        transition_duration: Duration of crossfade transitions between segments.

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
        # Build xfade chain
        xfade_filter = _build_xfade_filter(durations, transition_duration)
        filter_complex_parts = [xfade_filter] if xfade_filter else []

        if len(segments) == 2:
            video_label = "v01"
        else:
            video_label = "vout"

    # Step 3: Add lower-third overlays
    lower_thirds = _compute_lower_thirds(segments, transition_duration)
    if lower_thirds:
        drawtext_filter = _build_drawtext_filter(lower_thirds, video_label)
        if drawtext_filter:
            filter_complex_parts.append(drawtext_filter)
            video_label = "final"

    # Step 4: Build final ffmpeg command
    cmd: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"]

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
