"""Composite video segments with ffmpeg: concat, transitions, lower-thirds.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Phase 3: takes recorded WebM segments from video_gen and produces a single
YouTube/Spotify-ready MP4 with crossfade transitions and lower-third overlays.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Sequence

from podcaster.video.sync_plan import EpisodePlan, VideoSegment
from podcaster.video.video_gen import RecordedSegment

if TYPE_CHECKING:
    from podcaster.storage import StorageBackend

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

# When the podcast audio outlasts the composed video, the final frame is held
# (via tpad) and then faded to black over this many seconds so the outro audio
# plays in full without an abrupt freeze.
OUTRO_VIDEO_FADE_SECONDS = 2.0

# --- Reusable intro/outro (#314, #319) ---
# Stored once in the artifacts container and prepended/appended to every episode.
INTRO_BLOB_PATH = "assets/video/intro.mp4"
OUTRO_BLOB_PATH = "assets/video/outro.mp4"
# Canonical audio params for the concat-join step.
CONCAT_AUDIO_SAMPLE_RATE = "48000"
CONCAT_AUDIO_CHANNELS = "2"


def _default_intro_outro_cache_dir() -> Path:
    """Default on-disk cache for downloaded intro/outro clips.

    A stable temp-dir location so repeated episode generations on the same
    host reuse the already-downloaded clips instead of re-fetching them.
    """
    return Path(tempfile.gettempdir()) / "podcaster-intro-outro-cache"


# --- DOG (Digital On-Screen Graphic) watermark (#config-driven) ---
# A small, semi-transparent logo overlaid on the MAIN content segments only
# (intro/outro carry their own branding and are joined afterwards).

DEFAULT_DOG_LOGO_URL = (
    "https://raw.githubusercontent.com/jmservera/SquadScope/main/assets/images/claracle.jpeg"
)
DOG_DEFAULT_POSITION = "top-right"
DOG_DEFAULT_SIZE = 80
DOG_DEFAULT_OPACITY = 0.3
# Pixel inset from the frame edge for the watermark.
DOG_MARGIN = 40

# Supported corner positions mapped to ffmpeg overlay x:y expressions.
# ``W``/``H`` are the main video dimensions, ``w``/``h`` the (scaled) overlay.
_DOG_POSITIONS: dict[str, str] = {
    "top-left": f"{DOG_MARGIN}:{DOG_MARGIN}",
    "top-right": f"W-w-{DOG_MARGIN}:{DOG_MARGIN}",
    "bottom-left": f"{DOG_MARGIN}:H-h-{DOG_MARGIN}",
    "bottom-right": f"W-w-{DOG_MARGIN}:H-h-{DOG_MARGIN}",
}


@dataclass
class DogLogoConfig:
    """Configuration for the DOG (Digital On-Screen Graphic) watermark.

    Supplied via the API payload (``request.podcast_config.dog_logo``).  When a
    config object is present the logo is downloaded from :attr:`url`, scaled to
    :attr:`size` pixels wide, made :attr:`opacity` transparent and overlaid in
    the configured :attr:`position` corner of the main content segments only.
    """

    url: str = DEFAULT_DOG_LOGO_URL
    position: str = DOG_DEFAULT_POSITION
    size: int = DOG_DEFAULT_SIZE
    opacity: float = DOG_DEFAULT_OPACITY

    @classmethod
    def from_dict(cls, data: dict | None) -> "DogLogoConfig | None":
        """Build a config from a payload dict, or ``None`` if absent/invalid.

        Missing keys fall back to defaults, so an empty dict yields a fully
        default config.  A non-dict input (e.g. ``None``) returns ``None`` so the
        caller skips the watermark (graceful degradation).
        """
        if not isinstance(data, dict):
            return None
        url = data.get("url", DEFAULT_DOG_LOGO_URL)
        if not isinstance(url, str) or not url.strip():
            url = DEFAULT_DOG_LOGO_URL
        position = data.get("position", DOG_DEFAULT_POSITION)
        if position not in _DOG_POSITIONS:
            position = DOG_DEFAULT_POSITION
        try:
            size = int(data.get("size", DOG_DEFAULT_SIZE))
        except (TypeError, ValueError):
            size = DOG_DEFAULT_SIZE
        if size <= 0:
            size = DOG_DEFAULT_SIZE
        try:
            opacity = float(data.get("opacity", DOG_DEFAULT_OPACITY))
        except (TypeError, ValueError):
            opacity = DOG_DEFAULT_OPACITY
        opacity = max(0.0, min(1.0, opacity))
        return cls(url=url, position=position, size=size, opacity=opacity)


def _default_dog_cache_dir() -> Path:
    """Default on-disk cache for downloaded DOG logo images."""
    return Path(tempfile.gettempdir()) / "podcaster-dog-logo-cache"


def _fetch_dog_logo(url: str, cache_dir: Path) -> Path | None:
    """Download (and cache) the DOG logo image from *url*.

    Caches by a hash of the URL so different logos coexist and re-runs reuse a
    prior download.  Returns the local path, or ``None`` on any failure so the
    caller composes without a watermark (graceful degradation).
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        logger.warning(
            "Skipping DOG logo fetch: unsupported URL scheme %r in %s; composing without watermark",
            scheme, url,
        )
        return None

    digest = sha256(url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(url.split("?", 1)[0]).suffix or ".img"
    cache_path = cache_dir / f"dog_{digest}{suffix}"

    if cache_path.exists() and cache_path.stat().st_size > 0:
        logger.info("Using cached DOG logo: %s", cache_path)
        return cache_path

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 — config-driven URL
            data = resp.read()
    except Exception as exc:  # noqa: BLE001 — never fail composition on fetch error
        logger.warning(
            "Failed to download DOG logo from %s: %s; composing without watermark",
            url, exc,
        )
        return None

    if not data:
        logger.warning("DOG logo at %s was empty; composing without watermark", url)
        return None

    cache_path.write_bytes(data)
    logger.info("Downloaded DOG logo (%d bytes) from %s to %s", len(data), url, cache_path)
    return cache_path


def _build_dog_overlay_filter(
    config: "DogLogoConfig",
    logo_input_idx: int,
    video_label: str,
    out_label: str = "dogout",
) -> str:
    """Build the ffmpeg filter fragment overlaying the DOG logo onto the video.

    Scales the logo input to ``config.size`` px wide (aspect preserved), applies
    ``config.opacity`` via the alpha channel, then overlays it in the configured
    corner.  ``video_label`` is the current video stream label (e.g. ``"vout"``
    or ``"0:v"``); the result is exposed as ``out_label``.
    """
    position = _DOG_POSITIONS.get(config.position, _DOG_POSITIONS[DOG_DEFAULT_POSITION])
    return (
        f"[{logo_input_idx}:v]scale={config.size}:-1,format=rgba,"
        f"colorchannelmixer=aa={config.opacity}[dog];"
        f"[{video_label}][dog]overlay={position}:format=auto[{out_label}]"
    )


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
        "-colorspace", "bt709",
        "-color_trc", "bt709",
        "-color_primaries", "bt709",
        "-color_range", "tv",
        str(output_path),
    ]


def _fetch_blob_cached(
    storage: "StorageBackend",
    blob_path: str,
    cache_path: Path,
    label: str,
) -> Path | None:
    """Fetch *blob_path* from storage into *cache_path*, reusing a prior cache.

    Returns the local path on success, or ``None`` if the blob is missing or
    the fetch fails (graceful degradation — the caller composes without it).
    """
    if cache_path.exists() and cache_path.stat().st_size > 0:
        logger.info("Using cached %s clip: %s", label, cache_path)
        return cache_path

    try:
        data = storage.get_bytes(blob_path)
    except Exception as exc:  # noqa: BLE001 — never fail composition on fetch error
        logger.warning(
            "Failed to fetch %s clip from storage (%s): %s; composing without it",
            label, blob_path, exc,
        )
        return None

    if not data:
        logger.warning(
            "No %s clip found in storage at %s; composing without it",
            label, blob_path,
        )
        return None

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    logger.info(
        "Downloaded %s clip (%d bytes) from %s to %s",
        label, len(data), blob_path, cache_path,
    )
    return cache_path


def _fetch_intro_outro(
    storage: "StorageBackend",
    cache_dir: Path,
) -> tuple[Path | None, Path | None]:
    """Download (and cache) the stored intro/outro clips.

    Returns ``(intro_path, outro_path)`` where either entry may be ``None`` when
    the corresponding blob is unavailable.
    """
    intro = _fetch_blob_cached(
        storage, INTRO_BLOB_PATH, cache_dir / "intro.mp4", "intro"
    )
    outro = _fetch_blob_cached(
        storage, OUTRO_BLOB_PATH, cache_dir / "outro.mp4", "outro"
    )
    return intro, outro


def _probe_media(path: Path, run: "CommandRunner") -> tuple[bool, float]:
    """Probe a media file for audio presence and duration via ffprobe.

    Returns ``(has_audio, duration_seconds)``.  On any probe failure returns
    ``(False, 0.0)`` so callers degrade gracefully.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type",
        "-of", "json",
        str(path),
    ]
    try:
        proc = run(cmd)
        info = json.loads(proc.stdout or "{}")
    except Exception:  # noqa: BLE001 — probing is best-effort
        return False, 0.0

    has_audio = any(
        stream.get("codec_type") == "audio"
        for stream in info.get("streams", [])
    )
    try:
        duration = float(info.get("format", {}).get("duration", 0.0) or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return has_audio, duration


def _build_canonical_av_cmd(
    input_path: Path,
    output_path: Path,
    *,
    has_audio: bool,
) -> list[str]:
    """Re-encode *input_path* to the canonical 1080p/30fps + stereo-AAC format.

    Guarantees both a video and an audio stream so every joined clip has an
    identical layout and the concat-demuxer copy step succeeds.  When the source
    has no audio, a silent stereo track is synthesised.
    """
    vf = (
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={OUTPUT_FPS},format={ENCODE_PIX_FMT},setsar=1"
    )
    cmd: list[str] = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(input_path),
    ]
    if not has_audio:
        cmd += [
            "-f", "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={CONCAT_AUDIO_SAMPLE_RATE}",
        ]
    audio_map = "0:a:0" if has_audio else "1:a"
    cmd += [
        "-filter_complex", f"[0:v]{vf}[v]",
        "-map", "[v]",
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", ENCODE_PRESET,
        "-crf", str(ENCODE_CRF),
        "-pix_fmt", ENCODE_PIX_FMT,
        "-colorspace", "bt709",
        "-color_trc", "bt709",
        "-color_primaries", "bt709",
        "-color_range", "tv",
        "-c:a", "aac",
        "-b:a", ENCODE_AUDIO_BITRATE,
        "-ar", CONCAT_AUDIO_SAMPLE_RATE,
        "-ac", CONCAT_AUDIO_CHANNELS,
    ]
    if not has_audio:
        cmd.append("-shortest")
    cmd.append(str(output_path))
    return cmd


def _build_concat_cmd(list_file: Path, output_path: Path) -> list[str]:
    """Build the concat-demuxer command joining canonicalised clips (stream copy)."""
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]


def _build_audio_overlay_cmd(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    video_duration: float = 0.0,
    audio_duration: float = 0.0,
) -> list[str]:
    """Overlay *audio_path* as the sole audio track on *video_path*.

    The podcast MP3 is re-encoded to AAC and mapped as the only audio stream
    (any audio the video carries is dropped).  Crucially, the muxed output is
    **never** truncated with ``-shortest``: the outro audio must always play in
    full.

    When the audio outlasts the video, the final video frame is held for the
    remaining ``audio_duration - video_duration`` seconds (ffmpeg ``tpad`` with
    ``stop_mode=clone``) and then faded to black over the last
    :data:`OUTRO_VIDEO_FADE_SECONDS` seconds.  This requires re-encoding the
    video stream.  When the video is at least as long as the audio, the video
    stream is copied unchanged and any trailing silence is left as-is.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
    ]

    extend_video = audio_duration > video_duration > 0
    if extend_video:
        pad_duration = audio_duration - video_duration
        fade_start = max(0.0, audio_duration - OUTRO_VIDEO_FADE_SECONDS)
        vf = (
            f"tpad=stop_mode=clone:stop_duration={pad_duration:.3f},"
            f"fade=t=out:st={fade_start:.3f}:d={OUTRO_VIDEO_FADE_SECONDS}"
        )
        cmd += [
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", ENCODE_PRESET,
            "-crf", str(ENCODE_CRF),
            "-pix_fmt", ENCODE_PIX_FMT,
            "-colorspace", "bt709",
            "-color_trc", "bt709",
            "-color_primaries", "bt709",
            "-color_range", "tv",
        ]
    else:
        cmd += ["-c:v", "copy"]

    cmd += [
        "-c:a", "aac",
        "-b:a", ENCODE_AUDIO_BITRATE,
        "-ar", CONCAT_AUDIO_SAMPLE_RATE,
        "-ac", CONCAT_AUDIO_CHANNELS,
        "-movflags", "+faststart",
        str(output_path),
    ]
    return cmd


def _join_intro_outro(
    content_path: Path,
    output_path: Path,
    intro_path: Path | None,
    outro_path: Path | None,
    *,
    run: "CommandRunner",
    work_dir: Path,
) -> float:
    """Prepend *intro_path* and append *outro_path* around *content_path*.

    Canonicalises each present clip to a uniform **video-only** AV format (a
    silent stereo track is synthesised so the concat-demuxer copy succeeds),
    then concatenates ``intro -> content -> outro`` into *output_path*.

    Source audio on the intro/outro clips is always stripped: the podcast MP3
    is overlaid as the sole audio track on the final joined video afterwards.

    Returns the total added duration (seconds) of the intro/outro clips so the
    caller can adjust the reported episode duration.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    canon_dir = work_dir / "join"
    canon_dir.mkdir(parents=True, exist_ok=True)

    ordered: list[tuple[str, Path]] = []
    added_duration = 0.0

    if intro_path is not None:
        _, intro_dur = _probe_media(intro_path, run)
        added_duration += intro_dur
        ordered.append(("intro", intro_path))

    ordered.append(("content", content_path))

    if outro_path is not None:
        _, outro_dur = _probe_media(outro_path, run)
        added_duration += outro_dur
        ordered.append(("outro", outro_path))

    canon_paths: list[Path] = []
    for label, src in ordered:
        canon_path = canon_dir / f"{label}.mp4"
        logger.info("Canonicalizing %s clip for concat: %s", label, src)
        run(_build_canonical_av_cmd(src, canon_path, has_audio=False))
        canon_paths.append(canon_path)

    list_file = canon_dir / "concat.txt"
    list_file.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in canon_paths),
        encoding="utf-8",
    )
    logger.info(
        "Joining %d clips (intro/content/outro) into %s",
        len(canon_paths), output_path,
    )
    run(_build_concat_cmd(list_file, output_path))
    return added_duration


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

        if lt_end > lt_start and not seg.is_generic:
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
    storage: "StorageBackend | None" = None,
    intro_outro_cache_dir: Path | None = None,
    dog_logo: "DogLogoConfig | None" = None,
    dog_logo_cache_dir: Path | None = None,
) -> ComposeResult:
    """Compose recorded segments into a single MP4 with transitions and overlays.

    Args:
        segments: Recorded video segments from video_gen.record_episode().
        audio_path: Optional podcast audio track.  When provided, it is
            overlaid as the **sole** audio track on the final joined video
            (intro + content + outro), trimmed to the shorter of the
            video/audio durations.  The content itself is composed video-only.
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
        storage: Optional storage backend.  When provided, the reusable intro
            (``assets/video/intro.mp4``) and outro (``assets/video/outro.mp4``)
            clips are downloaded (and cached locally), prepended/appended around
            the composed content.  Missing clips are skipped with a warning
            (graceful degradation).
        intro_outro_cache_dir: Local cache directory for the downloaded
            intro/outro clips.  Defaults to a stable temp-dir location so
            repeated runs on the same host avoid re-downloading.
        dog_logo: Optional DOG (Digital On-Screen Graphic) watermark config.
            When provided, the logo at ``dog_logo.url`` is downloaded and
            overlaid on the main content segments (never the intro/outro) in
            the configured corner at the configured size/opacity.  A failed
            download is skipped silently (graceful degradation).
        dog_logo_cache_dir: Local cache directory for the downloaded DOG logo.
            Defaults to a stable temp-dir location.

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

    # Resolve reusable intro/outro clips (graceful degradation if unavailable).
    intro_path: Path | None = None
    outro_path: Path | None = None
    if storage is not None:
        cache_dir = intro_outro_cache_dir or _default_intro_outro_cache_dir()
        intro_path, outro_path = _fetch_intro_outro(storage, cache_dir)

    # The content is always composed **video-only**; the podcast MP3 (if any)
    # is overlaid as the sole audio track on the FINAL joined video so it spans
    # the entire output (intro + content + outro) without double audio.
    has_bookends = intro_path is not None or outro_path is not None
    needs_audio = audio_path is not None
    if has_bookends or needs_audio:
        compose_target = output_path.parent / "content.mp4"
    else:
        compose_target = output_path

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

    # Step 3.5: DOG (Digital On-Screen Graphic) watermark — overlaid on the main
    # content here, before intro/outro are joined, so it never covers the bumpers.
    dog_logo_path: Path | None = None
    if dog_logo is not None:
        dog_cache = dog_logo_cache_dir or _default_dog_cache_dir()
        dog_logo_path = _fetch_dog_logo(dog_logo.url, dog_cache)
    # The content is video-only here, so the logo input follows the video inputs.
    dog_input_idx = len(normalized_paths)
    if dog_logo_path is not None:
        dog_src = video_label if filter_complex_parts else "0:v"
        filter_complex_parts.append(
            _build_dog_overlay_filter(dog_logo, dog_input_idx, dog_src)
        )
        video_label = "dogout"

    # Step 4: Build the (video-only) content composition command
    cmd: list[str] = [compose_ffmpeg_bin, "-hide_banner", "-loglevel", "warning", "-y"]

    # Add all normalized video inputs
    for norm_path in normalized_paths:
        cmd.extend(["-i", str(norm_path)])

    # Add DOG logo image input (follows the video inputs to match dog_input_idx)
    if dog_logo_path is not None:
        cmd.extend(["-i", str(dog_logo_path)])

    if filter_complex_parts:
        cmd.extend(["-filter_complex", ";".join(filter_complex_parts)])
        cmd.extend(["-map", f"[{video_label}]"])
    else:
        cmd.extend(["-map", "0:v"])

    # Output encoding — video-only (audio is overlaid on the final joined video).
    cmd.extend([
        "-an",
        "-c:v", "libx264",
        "-preset", ENCODE_PRESET,
        "-crf", str(ENCODE_CRF),
        "-pix_fmt", ENCODE_PIX_FMT,
        "-colorspace", "bt709",
        "-color_trc", "bt709",
        "-color_primaries", "bt709",
        "-color_range", "tv",
        "-movflags", "+faststart",
    ])
    cmd.append(str(compose_target))

    logger.info("Composing %d segments into %s", len(segments), compose_target)
    run(cmd)

    # Content video duration (accounting for transition overlaps).
    video_duration = sum(durations) - transition_duration * max(0, len(segments) - 1)

    # Prepend intro / append outro around the composed content (video-only).
    if has_bookends:
        joined_target = (
            output_path.parent / "joined.mp4" if needs_audio else output_path
        )
        added = _join_intro_outro(
            compose_target,
            joined_target,
            intro_path,
            outro_path,
            run=run,
            work_dir=output_path.parent,
        )
        video_duration += added
        video_only_path = joined_target
    else:
        video_only_path = compose_target

    # Overlay the podcast MP3 as the sole audio track on the full video.
    if needs_audio:
        _, audio_duration = _probe_media(audio_path, run)
        _, probed_video_duration = _probe_media(video_only_path, run)
        effective_video_duration = probed_video_duration or video_duration
        run(_build_audio_overlay_cmd(
            video_only_path,
            audio_path,
            output_path,
            video_duration=effective_video_duration,
            audio_duration=audio_duration,
        ))
        # Audio is never truncated; the muxed output runs for the longer stream.
        total_duration = (
            max(effective_video_duration, audio_duration)
            if audio_duration > 0
            else effective_video_duration
        )
    else:
        total_duration = video_duration

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

    Pairs recordings to plan segments by segment label, preserving plan order.
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
        rec.segment.label: rec for rec in recordings
    }
    result: list[SyncedSegment] = []
    for seg in plan.segments:
        url = seg.label
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
    ``segment.start_seconds`` updated to ``target_start_seconds``.

    .. note::
        :func:`compose_video` currently places segments sequentially based on
        list order and ``duration_seconds`` (with transition overlap) and does
        **not** read ``start_seconds``.  The updated ``start_seconds`` is set
        for downstream consumers and debugging (e.g. plan serialization,
        inspection); it does not affect ``compose_video`` placement today.
        Keep the returned list in the intended playback order.

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
