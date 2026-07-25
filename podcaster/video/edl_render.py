"""Layer 3 — ffmpeg filter_complex renderer that executes an EDL (#490).

This is the *execution* half of Layer 3 of the Phase 4 audio–video
synchronization architecture (jmservera/SquadScope-Coordinator#32). The
:mod:`podcaster.video.edl` planner (#488) produces a deterministic
:class:`~podcaster.video.edl.EditDecisionList`; this module translates that EDL
into a single ``ffmpeg`` ``filter_complex`` pipeline and renders the final video.

The graph is built segment-by-segment, then the segments are joined:

* **cuts / trims** — a clip segment's ``source_ranges`` are each ``trim``+``setpts``
  and (when there is more than one) ``concat``-enated, so the clip plays exactly
  the kept material;
* **intermission fills** — rendered from a ``color`` source of the segment length;
* **title cards** — a ``drawtext`` overlay enabled for the card's leading window;
* **normalisation** — every segment is ``scale``/``fps``/``format``/``setsar``-ed
  to identical parameters so they can be concatenated or cross-faded;
* **joining** — either hard-cut ``concat`` (default, timing-exact) or ``xfade``
  transitions (``enable_crossfades``) using the EDL's declared crossfade.

Determinism: identical inputs produce an identical ``filter_complex`` string and
identical ffmpeg arguments. With hard-cut concat the rendered duration equals the
EDL total exactly; with ``xfade`` it equals the EDL total minus the cross-fade
overlaps (the standard ``xfade`` behaviour), reported as ``expected_duration_ms``.

Only the graph construction is pure; :func:`render_edl` shells out to ffmpeg via
an injectable runner so the builder can be unit-tested without rendering.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping, Sequence

from podcaster.video.edl import (
    DEFAULT_FALLBACK_CHAIN,
    EditDecisionList,
    EdlSegment,
    EdlSegmentKind,
    default_card_text,
    resolve_fallback,
)

logger = logging.getLogger("podcaster.video.edl_render")

CommandRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

#: Default font used for title-card text when the config supplies none.
DEFAULT_FONT_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


class EdlRenderError(RuntimeError):
    """Raised when an EDL cannot be rendered."""


@dataclass(frozen=True)
class RenderConfig:
    """Encoding / styling parameters for the EDL renderer."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    pixel_format: str = "yuv420p"
    crf: int = 18
    preset: str = "medium"
    intermission_color: str = "black"
    font_file: str = DEFAULT_FONT_FILE
    title_font_size: int = 64
    title_font_color: str = "white"
    card_color: str = "0x1e1e2e"
    card_font_size: int = 72
    card_font_color: str = "white"
    enable_crossfades: bool = False
    intermission_video_path: str | None = None


@dataclass(frozen=True)
class FfmpegRenderPlan:
    """A fully-formed ffmpeg invocation for an EDL (pure, side-effect-free)."""

    argv: tuple[str, ...]
    filter_complex: str
    final_label: str
    inputs: tuple[str, ...]
    expected_duration_ms: int
    output_path: str

    @property
    def command(self) -> str:
        return " ".join(self.argv)


def _ms_to_s(ms: int) -> str:
    """Format milliseconds as fixed-precision seconds for ffmpeg expressions."""
    return f"{ms / 1000.0:.3f}"


def _escape_drawtext(text: str) -> str:
    """Escape text for an ffmpeg ``drawtext`` ``text=`` value."""
    out = text.replace("\\", "\\\\")
    out = out.replace(":", r"\:")
    out = out.replace("'", r"\'")
    out = out.replace(",", r"\,")
    out = out.replace("%", r"\%")
    return out


def _normalize_chain(config: RenderConfig) -> str:
    """Filter suffix making any segment match the canonical size/fps/format/sar."""
    return (
        f"scale={config.width}:{config.height}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={config.fps},format={config.pixel_format},setsar=1"
    )


def resolve_intermission_video_path(config: RenderConfig) -> Path | None:
    """Resolve the optional animated intermission asset.

    The production asset is uploaded as ``assets/video/intermission.mp4``. For
    local rendering/tests, the same file may exist in the repository. It is
    generated from ``scripts/intro-outro/compositions/intermission.html`` by
    ``scripts/intro-outro/render.sh``; if no local file exists, callers fall back
    to the historical solid-color fill.
    """
    candidates: list[Path] = []
    if config.intermission_video_path:
        candidates.append(Path(config.intermission_video_path))
    if env_path := os.getenv("PODCASTER_INTERMISSION_VIDEO_PATH"):
        candidates.append(Path(env_path))
    repo_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            repo_root / "assets" / "video" / "intermission.mp4",
            repo_root / "scripts" / "intro-outro" / "output" / "intermission.mp4",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _title_card_filter(segment: EdlSegment, config: RenderConfig) -> str | None:
    """Build a ``drawtext`` filter for a segment's title card, if any."""
    if segment.title_card is None or not segment.title_card.text:
        return None
    card = segment.title_card
    text = _escape_drawtext(card.text)
    end_s = _ms_to_s(card.duration_ms)
    return (
        f"drawtext=fontfile='{config.font_file}':text='{text}':"
        f"fontcolor={config.title_font_color}:fontsize={config.title_font_size}:"
        "x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.5:boxborderw=20:"
        f"enable='between(t,0,{end_s})'"
    )


def _card_text_filter(text: str, config: RenderConfig) -> str:
    """Build a full-duration centred ``drawtext`` filter for a fallback card."""
    return (
        f"drawtext=fontfile='{config.font_file}':text='{_escape_drawtext(text)}':"
        f"fontcolor={config.card_font_color}:fontsize={config.card_font_size}:"
        "x=(w-text_w)/2:y=(h-text_h)/2"
    )


def _segment_filtergraph(
    segment: EdlSegment,
    index: int,
    input_index: int | None,
    config: RenderConfig,
) -> list[str]:
    """Filter statements producing a normalised ``[seg{index}]`` label."""
    label = f"seg{index}"
    norm = _normalize_chain(config)
    title = _title_card_filter(segment, config)
    statements: list[str] = []

    if segment.kind is EdlSegmentKind.INTERMISSION:
        dur_s = _ms_to_s(segment.duration_ms)
        if input_index is None:
            chain = (
                f"color=c={config.intermission_color}:"
                f"s={config.width}x{config.height}:r={config.fps}:d={dur_s},"
                f"format={config.pixel_format},setsar=1"
            )
        else:
            chain = f"[{input_index}:v]trim=duration={dur_s},setpts=PTS-STARTPTS,{norm}"
        if title:
            chain += f",{title}"
        statements.append(f"{chain}[{label}]")
        return statements

    if segment.kind is EdlSegmentKind.CARD:
        dur_s = _ms_to_s(segment.duration_ms)
        text = segment.fallback_text or ""
        chain = (
            f"color=c={config.card_color}:"
            f"s={config.width}x{config.height}:r={config.fps}:d={dur_s},"
            f"format={config.pixel_format},setsar=1"
        )
        if text:
            chain += f",{_card_text_filter(text, config)}"
        statements.append(f"{chain}[{label}]")
        return statements

    if segment.kind is EdlSegmentKind.SCREENSHOT:
        if input_index is None:
            raise EdlRenderError(f"screenshot segment {index} has no resolved input index")
        # The still image input is already looped to the segment duration, so it
        # only needs normalising (and an optional title card).
        chain = f"[{input_index}:v]{norm}"
        if title:
            chain += f",{title}"
        statements.append(f"{chain}[{label}]")
        return statements

    if input_index is None:
        raise EdlRenderError(f"clip segment {index} has no resolved input index")

    if not segment.source_ranges:
        raise EdlRenderError(f"clip segment {index} has no source ranges to render")

    # Trim each kept source range, then concat when there is more than one.
    part_labels: list[str] = []
    for j, rng in enumerate(segment.source_ranges):
        part = f"s{index}_{j}"
        statements.append(
            f"[{input_index}:v]"
            f"trim=start={_ms_to_s(rng.start_ms)}:end={_ms_to_s(rng.end_ms)},"
            f"setpts=PTS-STARTPTS[{part}]"
        )
        part_labels.append(part)

    raw = f"seg{index}_raw"
    if len(part_labels) == 1:
        # Single range — relabel without a concat.
        statements[-1] = statements[-1].replace(f"[{part_labels[0]}]", f"[{raw}]")
    else:
        joined = "".join(f"[{p}]" for p in part_labels)
        statements.append(f"{joined}concat=n={len(part_labels)}:v=1:a=0[{raw}]")

    chain = f"[{raw}]{norm}"
    if title:
        chain += f",{title}"
    statements.append(f"{chain}[{label}]")
    return statements


def _join_filtergraph(
    segments: Sequence[EdlSegment],
    config: RenderConfig,
) -> tuple[list[str], str, int]:
    """Join per-segment labels into a final label; return (stmts, label, dur_ms)."""
    seg_labels = [f"seg{i}" for i in range(len(segments))]

    if len(seg_labels) == 1:
        return [], seg_labels[0], segments[0].duration_ms

    total = sum(s.duration_ms for s in segments)

    if not config.enable_crossfades:
        joined = "".join(f"[{label}]" for label in seg_labels)
        stmt = f"{joined}concat=n={len(seg_labels)}:v=1:a=0[vout]"
        return [stmt], "vout", total

    # xfade chain: each transition overlaps by the EDL's declared crossfade.
    statements: list[str] = []
    prev = seg_labels[0]
    acc_ms = segments[0].duration_ms
    for i in range(1, len(seg_labels)):
        crossfade_ms = min(
            segments[i].crossfade_in_ms,
            segments[i - 1].duration_ms,
            segments[i].duration_ms,
        )
        offset_ms = max(acc_ms - crossfade_ms, 0)
        out = "vout" if i == len(seg_labels) - 1 else f"vx{i}"
        statements.append(
            f"[{prev}][{seg_labels[i]}]xfade=transition=fade:"
            f"duration={_ms_to_s(crossfade_ms)}:offset={_ms_to_s(offset_ms)}[{out}]"
        )
        acc_ms = offset_ms + segments[i].duration_ms
        prev = out
    return statements, "vout", acc_ms


def build_render_plan(
    edl: EditDecisionList,
    clip_paths: Mapping[str, str | Path],
    output_path: str | Path,
    *,
    image_paths: Mapping[str, str | Path] | None = None,
    config: RenderConfig | None = None,
) -> FfmpegRenderPlan:
    """Build a deterministic :class:`FfmpegRenderPlan` for *edl*.

    Args:
        edl: The Layer 3 :class:`~podcaster.video.edl.EditDecisionList` to render.
        clip_paths: Map of ``clip_id`` → source video path for every clip segment.
        output_path: Destination video path.
        image_paths: Map of ``fallback_image_id`` → still-image path for every
            ``screenshot`` fallback segment (#489).
        config: Encoding / styling parameters.

    Raises:
        EdlRenderError: when the EDL is empty, a clip segment references a
            ``clip_id`` missing from *clip_paths*, or a screenshot segment
            references an image id missing from *image_paths*. Use
            :func:`degrade_for_render` first to turn unavailable clips into
            screenshot/card/intermission fills and avoid a hard failure.
    """
    config = config or RenderConfig()
    image_paths = image_paths or {}
    if not edl.segments:
        raise EdlRenderError("cannot render an empty EDL")
    intermission_video_path = resolve_intermission_video_path(config)

    # Assign a stable ffmpeg input index to each input, in first-use order, so the
    # argv and graph are deterministic. Clip files are shared across segments;
    # each screenshot still gets its own ``-loop 1 -t <dur>`` input (its hold
    # duration is baked into the input).
    input_files: list[str] = []
    input_pre_args: list[tuple[str, ...]] = []
    input_index_by_clip: dict[str, int] = {}
    seg_input_index: dict[int, int] = {}

    for i, seg in enumerate(edl.segments):
        if seg.kind is EdlSegmentKind.CLIP:
            if seg.clip_id is None or seg.clip_id not in clip_paths:
                raise EdlRenderError(f"clip segment references unknown clip_id {seg.clip_id!r}")
            if seg.clip_id not in input_index_by_clip:
                input_index_by_clip[seg.clip_id] = len(input_files)
                input_files.append(str(clip_paths[seg.clip_id]))
                input_pre_args.append(())
            seg_input_index[i] = input_index_by_clip[seg.clip_id]
        elif seg.kind is EdlSegmentKind.SCREENSHOT:
            if seg.fallback_image_id is None or seg.fallback_image_id not in image_paths:
                raise EdlRenderError(
                    f"screenshot segment references unknown image id {seg.fallback_image_id!r}"
                )
            seg_input_index[i] = len(input_files)
            input_files.append(str(image_paths[seg.fallback_image_id]))
            input_pre_args.append(("-loop", "1", "-t", _ms_to_s(seg.duration_ms)))
        elif seg.kind is EdlSegmentKind.INTERMISSION and intermission_video_path is not None:
            seg_input_index[i] = len(input_files)
            input_files.append(str(intermission_video_path))
            input_pre_args.append(("-stream_loop", "-1", "-t", _ms_to_s(seg.duration_ms)))

    statements: list[str] = []
    for i, seg in enumerate(edl.segments):
        statements.extend(_segment_filtergraph(seg, i, seg_input_index.get(i), config))

    join_stmts, final_label, expected_ms = _join_filtergraph(edl.segments, config)
    statements.extend(join_stmts)
    filter_complex = ";".join(statements)

    argv: list[str] = ["ffmpeg", "-hide_banner", "-y"]
    for path, pre_args in zip(input_files, input_pre_args):
        argv += list(pre_args)
        argv += ["-i", path]
    argv += [
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{final_label}]",
        "-c:v",
        "libx264",
        "-crf",
        str(config.crf),
        "-preset",
        config.preset,
        "-pix_fmt",
        config.pixel_format,
        "-r",
        str(config.fps),
        str(output_path),
    ]

    return FfmpegRenderPlan(
        argv=tuple(argv),
        filter_complex=filter_complex,
        final_label=final_label,
        inputs=tuple(input_files),
        expected_duration_ms=expected_ms,
        output_path=str(output_path),
    )


def _default_runner(command: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )


def _clip_available(
    segment: EdlSegment,
    clip_paths: Mapping[str, str | Path],
    *,
    check_files: bool,
) -> bool:
    """True when *segment*'s clip can actually be rendered."""
    if segment.clip_id is None or segment.clip_id not in clip_paths:
        return False
    if check_files and not Path(clip_paths[segment.clip_id]).exists():
        return False
    return True


def degrade_for_render(
    edl: EditDecisionList,
    clip_paths: Mapping[str, str | Path],
    *,
    image_paths: Mapping[str, str | Path] | None = None,
    screenshots: Mapping[str, str] | None = None,
    repo_labels: Mapping[str, str] | None = None,
    section_titles: Mapping[str, str] | None = None,
    fallback_chain: Sequence[EdlSegmentKind] | None = None,
    check_files: bool = False,
) -> EditDecisionList:
    """Rewrite segments whose source material is unavailable into fills (#489).

    A clip segment whose ``clip_id`` is missing from *clip_paths* (or, with
    ``check_files``, whose file does not exist on disk) — or a screenshot segment
    whose image is missing — is degraded through the fallback chain
    (``screenshot → card → intermission``) so a failed/missing clip never causes
    a hard render failure. Timeline bounds, crossfades, title cards and section
    grouping are preserved, so the result is still gap-free and the same length.
    """
    image_paths = image_paths or {}
    screenshots = screenshots or {}
    repo_labels = repo_labels or {}
    section_titles = section_titles or {}
    chain = tuple(fallback_chain) if fallback_chain is not None else DEFAULT_FALLBACK_CHAIN

    def _degrade(segment: EdlSegment) -> EdlSegment:
        screenshot_id = screenshots.get(segment.repo_url) if segment.repo_url else None
        if screenshot_id is not None and screenshot_id not in image_paths:
            screenshot_id = None  # no usable image → skip the screenshot step
        resolution = resolve_fallback(
            repo_url=segment.repo_url,
            screenshot_id=screenshot_id,
            card_text=default_card_text(
                segment.visual_mode,
                segment.repo_url,
                segment.section_id,
                repo_labels,
                section_titles,
            ),
            chain=chain,
        )
        logger.warning(
            "source unavailable for %s segment (repo_url=%s) — degrading to %s fallback",
            segment.visual_mode.value,
            segment.repo_url,
            resolution.kind.value,
        )
        return replace(
            segment,
            kind=resolution.kind,
            clip_id=None,
            source_ranges=(),
            looped=False,
            is_fallback=True,
            fallback_image_id=resolution.image_id,
            fallback_text=resolution.text,
        )

    new_segments: list[EdlSegment] = []
    changed = False
    for seg in edl.segments:
        if seg.kind is EdlSegmentKind.CLIP and not _clip_available(
            seg, clip_paths, check_files=check_files
        ):
            new_segments.append(_degrade(seg))
            changed = True
        elif seg.kind is EdlSegmentKind.SCREENSHOT and (
            seg.fallback_image_id is None or seg.fallback_image_id not in image_paths
        ):
            new_segments.append(_degrade(seg))
            changed = True
        else:
            new_segments.append(seg)

    if not changed:
        return edl
    return replace(edl, segments=tuple(new_segments))


def render_edl(
    edl: EditDecisionList,
    clip_paths: Mapping[str, str | Path],
    output_path: str | Path,
    *,
    image_paths: Mapping[str, str | Path] | None = None,
    screenshots: Mapping[str, str] | None = None,
    repo_labels: Mapping[str, str] | None = None,
    section_titles: Mapping[str, str] | None = None,
    config: RenderConfig | None = None,
    degrade_missing: bool = True,
    runner: CommandRunner | None = None,
) -> Path:
    """Render *edl* to ``output_path`` with ffmpeg; return the output path.

    The ffmpeg invocation is built by :func:`build_render_plan` and executed via
    *runner* (injectable for testing). Raises :class:`EdlRenderError` on a
    non-zero ffmpeg exit.

    When ``degrade_missing`` (the default), any clip/screenshot segment whose
    source material is unavailable is first rewritten by :func:`degrade_for_render`
    into a screenshot/card/intermission fill, so a missing or failed clip never
    causes a hard render failure (#489).
    """
    if runner is None and shutil.which("ffmpeg") is None:
        raise EdlRenderError("ffmpeg is not available on PATH")
    runner = runner or _default_runner

    if degrade_missing:
        edl = degrade_for_render(
            edl,
            clip_paths,
            image_paths=image_paths,
            screenshots=screenshots,
            repo_labels=repo_labels,
            section_titles=section_titles,
            check_files=runner is _default_runner,
        )

    plan = build_render_plan(edl, clip_paths, output_path, image_paths=image_paths, config=config)
    result = runner(plan.argv)
    if result.returncode != 0:
        stderr = (result.stderr or "")[-2000:]
        raise EdlRenderError(f"ffmpeg failed (exit {result.returncode}): {stderr}")
    out = Path(output_path)
    if not out.exists():
        raise EdlRenderError(f"ffmpeg reported success but {out} is missing")
    return out
