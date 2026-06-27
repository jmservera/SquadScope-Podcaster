"""Ken Burns-style dynamic zoom/pan for video segments (#299).

Applies smooth ease-in/hold/ease-out zoom to a region of interest via the
ffmpeg ``zoompan`` filter.  The zoom target (focus region) is expressed as a
bounding box in original video coordinates.

**Available data at post-processing time**

Playwright captures a scrolling viewport but does **not** expose element
bounding boxes through the recording API.  Focus regions must therefore be
supplied externally — either from a separate Playwright interrogation step, a
future annotation layer, or manual configuration.  When no focus regions are
provided, all functions degrade gracefully to no-op (the segment passes
through unchanged).

The function :func:`find_focus_regions_from_script` is a placeholder that
documents the interface and always returns ``[]``.  Future work can replace it
with real Playwright inspection (query ``img``, ``video``, ``canvas``,
pre/code blocks by selector → ``element.bounding_box()``).
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from podcaster.video.sync_plan import VideoSegment
from podcaster.video.video_gen import RecordedSegment

logger = logging.getLogger(__name__)

# Default zoom / easing constants
DEFAULT_ZOOM_LEVEL = 2.0  # 2× zoom at peak
DEFAULT_EASE_IN_S = 0.5  # seconds to ramp up to peak zoom
DEFAULT_EASE_OUT_S = 0.5  # seconds to ramp back to full view
DEFAULT_VIDEO_W = 1920
DEFAULT_VIDEO_H = 1080
DEFAULT_FPS = 30


class CommandRunner(Protocol):
    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=True)


@dataclass(frozen=True)
class FocusRegion:
    """A rectangular region of interest in original video coordinates.

    Coordinates are in pixels relative to the top-left corner of the original
    video frame (before any scaling).

    Attributes:
        x: Left edge of the bounding box (pixels).
        y: Top edge of the bounding box (pixels).
        width: Width of the bounding box (pixels).
        height: Height of the bounding box (pixels).
        start_seconds: When to begin the zoom-in (seconds from segment start).
        duration_seconds: How long to hold the zoom before easing back out.
        label: Human-readable identifier (element type, CSS selector, etc.).
    """

    x: float
    y: float
    width: float
    height: float
    start_seconds: float
    duration_seconds: float
    label: str = ""

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass(frozen=True)
class ZoomSpec:
    """Parameters for a Ken Burns zoom applied to one focus region.

    Attributes:
        focus: The target bounding box + time range.
        zoom_level: Peak zoom level (1.0 = full frame, 2.0 = 2× zoom).
        ease_in_s: Duration of the zoom-in ramp (seconds).
        ease_out_s: Duration of the zoom-out ramp (seconds).
    """

    focus: FocusRegion
    zoom_level: float = DEFAULT_ZOOM_LEVEL
    ease_in_s: float = DEFAULT_EASE_IN_S
    ease_out_s: float = DEFAULT_EASE_OUT_S


def _zoompan_exprs(
    spec: ZoomSpec,
    video_w: int = DEFAULT_VIDEO_W,
    video_h: int = DEFAULT_VIDEO_H,
    fps: int = DEFAULT_FPS,
) -> tuple[str, str, str]:
    """Build (z_expr, x_expr, y_expr) strings for the ffmpeg zoompan filter.

    Generates smooth linear ease-in → hold → ease-out expressions:

    * **Ease-in** (0 … EI frames): zoom 1.0 → ``zoom_level``, pan
      from frame centre to focus centre.
    * **Hold** (EI … EI+HF frames): hold at ``zoom_level`` centred on focus.
    * **Ease-out** (EI+HF … EI+HF+EO frames): zoom back to 1.0, pan
      back to frame centre.
    * **After** (remaining frames, if any): full-frame view.

    All expressions reference the ``n`` (frame index) and ``zoom`` (current
    zoom after z evaluation) variables understood by ffmpeg's expression engine.

    Args:
        spec: Zoom specification.
        video_w: Video width in pixels.
        video_h: Video height in pixels.
        fps: Frames per second.

    Returns:
        Tuple of (z_expr, x_expr, y_expr) strings ready for
        ``zoompan=z='...':x='...':y='...'``.
    """
    ei = max(1, round(spec.ease_in_s * fps))
    eo = max(1, round(spec.ease_out_s * fps))

    # Hold starts after ease-in offset by the segment-relative start time
    start_frame = round(spec.focus.start_seconds * fps)
    hold_frames = max(1, round(spec.focus.duration_seconds * fps))
    ease_in_end = start_frame + ei
    hold_end = ease_in_end + hold_frames
    ease_out_end = hold_end + eo

    zt = f"{spec.zoom_level:.4f}"
    cx = f"{spec.focus.center_x:.2f}"
    cy = f"{spec.focus.center_y:.2f}"
    si = str(start_frame)
    eie = str(ease_in_end)
    he = str(hold_end)
    eoe = str(ease_out_end)
    ei_s = str(ei)
    eo_s = str(eo)

    # z: ramps from 1.0 at start_frame to zoom_level, holds, ramps back
    z_expr = (
        f"if(lt(n,{si}),1.0,"
        f"if(lte(n,{eie}),1.0+({zt}-1.0)*(n-{si})/{ei_s},"
        f"if(lte(n,{he}),{zt},"
        f"if(lte(n,{eoe}),{zt}+(1.0-{zt})*(n-{he})/{eo_s},"
        f"1.0))))"
    )

    # x_center interpolates from iw/2 → focus_cx → iw/2
    x_center = (
        f"if(lt(n,{si}),iw/2,"
        f"if(lte(n,{eie}),iw/2+({cx}-iw/2)*(n-{si})/{ei_s},"
        f"if(lte(n,{he}),{cx},"
        f"if(lte(n,{eoe}),{cx}+(iw/2-{cx})*(n-{he})/{eo_s},"
        f"iw/2))))"
    )
    x_expr = f"max(0,min(iw-iw/zoom,{x_center}-iw/zoom/2))"

    y_center = (
        f"if(lt(n,{si}),ih/2,"
        f"if(lte(n,{eie}),ih/2+({cy}-ih/2)*(n-{si})/{ei_s},"
        f"if(lte(n,{he}),{cy},"
        f"if(lte(n,{eoe}),{cy}+(ih/2-{cy})*(n-{he})/{eo_s},"
        f"ih/2))))"
    )
    y_expr = f"max(0,min(ih-ih/zoom,{y_center}-ih/zoom/2))"

    return z_expr, x_expr, y_expr


def build_zoompan_cmd(
    input_path: Path,
    output_path: Path,
    spec: ZoomSpec,
    video_w: int = DEFAULT_VIDEO_W,
    video_h: int = DEFAULT_VIDEO_H,
    fps: int = DEFAULT_FPS,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build an ffmpeg command applying Ken Burns zoom to a video segment.

    The output video has the same duration and frame rate as the input;
    one output frame is produced per input frame (``d=1``).

    Args:
        input_path: Source video file.
        output_path: Destination file (same codec/container as input).
        spec: Zoom parameters.
        video_w: Video width in pixels.
        video_h: Video height in pixels.
        fps: Frames per second (must match the input video).
        ffmpeg_bin: Path or name of the ffmpeg binary.

    Returns:
        Command list suitable for :func:`subprocess.run`.
    """
    z_expr, x_expr, y_expr = _zoompan_exprs(spec, video_w, video_h, fps)
    zoompan_filter = (
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s={video_w}x{video_h}:fps={fps}"
    )
    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        zoompan_filter,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output_path),
    ]


def apply_zoom_to_segment(
    recorded: RecordedSegment,
    zoom_specs: list[ZoomSpec],
    output_dir: Path,
    video_w: int = DEFAULT_VIDEO_W,
    video_h: int = DEFAULT_VIDEO_H,
    fps: int = DEFAULT_FPS,
    ffmpeg_bin: str = "ffmpeg",
    runner: CommandRunner | None = None,
) -> RecordedSegment:
    """Apply Ken Burns zoom to a recorded segment and return an updated segment.

    When *zoom_specs* is empty, returns *recorded* unchanged (graceful no-op).
    Multiple specs are applied sequentially in the order given; each spec
    operates on the output of the previous one.

    Args:
        recorded: Source recorded segment.
        zoom_specs: Zoom operations to apply. Empty list = no-op pass-through.
        output_dir: Directory for intermediate and final zoomed files.
        video_w: Video width in pixels.
        video_h: Video height in pixels.
        fps: Frames per second.
        ffmpeg_bin: ffmpeg binary to use.
        runner: Command runner for testing. Uses subprocess if None.

    Returns:
        :class:`RecordedSegment` with updated ``video_path`` pointing to the
        zoomed file, or the original *recorded* if *zoom_specs* is empty.
    """
    if not zoom_specs:
        return recorded

    run = runner or _default_runner
    output_dir.mkdir(parents=True, exist_ok=True)

    current_path = recorded.video_path
    suffix = recorded.video_path.suffix

    for i, spec in enumerate(zoom_specs):
        out_path = output_dir / f"{recorded.video_path.stem}_zoom_{i:02d}{suffix}"
        cmd = build_zoompan_cmd(current_path, out_path, spec, video_w, video_h, fps, ffmpeg_bin)
        label = spec.focus.label or f"focus_{i}"
        logger.info(
            "Applying zoom to %s: focus=%s zoom=%.1f×",
            recorded.video_path.name,
            label,
            spec.zoom_level,
        )
        run(cmd)
        current_path = out_path

    new_segment = VideoSegment(
        repo=recorded.segment.repo,
        start_seconds=recorded.segment.start_seconds,
        duration_seconds=recorded.segment.duration_seconds,
    )
    return RecordedSegment(
        segment=new_segment,
        video_path=current_path,
        is_fallback=recorded.is_fallback,
        has_pages=recorded.has_pages,
    )


def find_focus_regions_from_script(
    script: str,
    segment: VideoSegment,
) -> list[FocusRegion]:
    """Placeholder: extract focus regions from script/segment hints.

    **Current implementation always returns ``[]``** (no zoom applied).

    Playwright does not expose element bounding boxes through its video
    recording API; the ``record_video_dir`` context only produces a WebM
    file.  Bounding boxes would require a separate, non-recording Playwright
    pass to inspect the page and call ``element.bounding_box()`` for each
    interesting element (``img``, ``video``, ``canvas``, ``pre``/``code``
    blocks, ``[alt*='diagram']``, etc.).

    This function exists to document the intended interface.  Replace the
    body with real logic once the bbox-capture pass is available.

    Args:
        script: Full episode script text (may contain hints about which
            elements the hosts discuss).
        segment: The video segment whose page will be shown.

    Returns:
        Empty list (no-op).  Future: list of :class:`FocusRegion` objects.
    """
    return []
