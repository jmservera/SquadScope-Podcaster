"""Composite video segments with ffmpeg: concat, transitions, lower-thirds.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Phase 3: takes recorded WebM segments from video_gen and produces a single
YouTube/Spotify-ready MP4 with crossfade transitions and lower-third overlays.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol, Sequence

from podcaster.retry import DEFAULT_TASK_RETRIES, retry_call
from podcaster.video.sync_plan import EpisodePlan, VideoSegment
from podcaster.video.video_gen import RecordedSegment, _recording_blob_name
from podcaster.video.intermediates import ensure_disk_budget

if TYPE_CHECKING:
    from podcaster.storage import StorageBackend
    from podcaster.video.section_cards import SectionCardInsert

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

# --- Final encode settings (YouTube/Spotify-ready) -------------------------
# Every knob below is env-overridable so encode quality can be tuned — and the
# codec switched to HEVC — without code changes (issue #376).  Defaults target
# visually-lossless screen-recorded, text-heavy content while staying inside
# Spotify's accepted spec:
#   * Spotify accepts H.264 High Profile *or* H.265/HEVC, but mandates 8-bit
#     4:2:0 (``yuv420p``) chroma subsampling — ``yuv444p`` is rejected, so the
#     pixel format stays ``yuv420p`` regardless of codec.
#   * The composition pipeline re-encodes each segment several times (normalize,
#     pairwise xfade, canonical join) and ends in a stream-copy, so the final
#     output quality is determined by these intermediate encodes.  Lowering the
#     CRF therefore directly attacks the gradient banding / posterisation and
#     soft-text artefacts reported for scrolling screen recordings.


def _env_str(name: str, default: str) -> str:
    """Return a stripped non-empty env override for *name*, else *default*."""
    return os.environ.get(name, "").strip() or default


def _env_int(name: str, default: int) -> int:
    """Return *name* parsed as int, falling back to *default* when unset/bad."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int for %s=%r; using default %d", name, raw, default)
        return default


# Video codec.  "libx264" (default, broadest Spotify compatibility) or
# "libx265"/"hevc" for HEVC (officially accepted by Spotify, better quality per
# bit, but higher practical risk on older clients).
ENCODE_VCODEC = _env_str("VIDEO_ENCODE_VCODEC", "libx264")
_IS_HEVC = ENCODE_VCODEC in ("libx265", "hevc")

# HEVC reaches the same visual quality as H.264 at a CRF a few points higher, so
# the default CRF is codec-dependent.  Both defaults sit in the
# visually-lossless range for screen content (lower than the old 16 to remove
# gradient banding and keep fine GitHub UI glyphs crisp — issue #376).
ENCODE_CRF = _env_int("VIDEO_ENCODE_CRF", 18 if _IS_HEVC else 12)
ENCODE_PRESET = _env_str("VIDEO_ENCODE_PRESET", "slow")
# Spotify mandates 8-bit 4:2:0; do NOT change to yuv444p without confirming the
# whole delivery chain (Spotify rejects non-4:2:0 video).
ENCODE_PIX_FMT = _env_str("VIDEO_ENCODE_PIX_FMT", "yuv420p")
ENCODE_AUDIO_BITRATE = _env_str("VIDEO_ENCODE_AUDIO_BITRATE", "192k")
# Preset for the many intermediate re-encodes (segment normalize/fit).  The old
# "ultrafast" disabled CABAC/trellis and other quality tools, so each re-encode
# pass compounded artefacts; a slightly slower preset preserves more detail
# feeding the final stream-copy output (issue #376).
INTERMEDIATE_PRESET = _env_str("VIDEO_INTERMEDIATE_PRESET", "veryfast")
# Number of segment normalizations to run in parallel.  Each is an independent
# ffmpeg process, so this scales near-linearly with cores up to the cap and is
# the single biggest video-generation speed-up (issue #376).
NORMALIZE_WORKERS = max(
    1, _env_int("VIDEO_NORMALIZE_WORKERS", min(4, os.cpu_count() or 1))
)

# Bounded per-task retries for the parallel normalize phase (issue #483).  A
# single segment whose ffmpeg re-encode fails transiently is retried in
# isolation — the blob checkpoint makes the retry idempotent — instead of
# aborting the whole compose.  ``1`` disables retry (single attempt).
NORMALIZE_TASK_RETRIES = max(
    1, _env_int("VIDEO_NORMALIZE_TASK_RETRIES", DEFAULT_TASK_RETRIES)
)


def _video_encode_args(preset: str) -> list[str]:
    """Common video-encoder flags (codec, preset, CRF, pixel format, profile).

    Honours the env-configured codec/CRF/pixel-format so every re-encode in the
    pipeline switches consistently between H.264 and HEVC (issue #376).

    When NVENC hardware acceleration is available and enabled (issue #396), the
    equivalent GPU encoder is used instead — this offloads the heavy compose
    re-encodes (the pipeline's #1 bottleneck) to the GPU.  On the CPU-only ACA
    runtime (no GPU) this is a transparent no-op: detection fails and the exact
    libx264/libx265 flags below are returned unchanged, so production output is
    byte-for-byte identical until a GPU runner is provisioned.
    """
    hw = _select_hwaccel_encoder()
    if hw is not None:
        return _hwaccel_encode_args(hw, preset)
    args = [
        "-c:v", ENCODE_VCODEC,
        "-preset", preset,
        "-crf", str(ENCODE_CRF),
        "-pix_fmt", ENCODE_PIX_FMT,
    ]
    if ENCODE_VCODEC == "libx264":
        # Spotify requires H.264 *High* profile; it is x264's default for
        # yuv420p but we set it explicitly so it can never regress.
        args += ["-profile:v", "high"]
    return args


# --- Hardware-accelerated encoding (NVENC) — issue #396 ----------------------
#
# Composition is the pipeline bottleneck (~33 min): every pairwise xfade pass
# re-encodes the growing accumulator on the CPU.  When an NVIDIA GPU + an
# NVENC-capable ffmpeg are present, routing those encodes through NVENC cuts
# encode time dramatically.  ACA currently provisions no GPU, so this defaults
# to OFF via auto-detection — it never changes the CPU path unless a GPU is
# actually available *and* opted in.
#
# VIDEO_HWACCEL: "auto" (default) — use NVENC only when truly available;
#                "nvenc"          — force NVENC (assume available);
#                "none"/"off"     — always use the CPU encoder.
_HWACCEL_MODE = _env_str("VIDEO_HWACCEL", "auto").lower()

# NVENC equivalents of the software codecs.  HEVC maps to hevc_nvenc; everything
# else (H.264) maps to h264_nvenc.
_NVENC_CODEC = "hevc_nvenc" if _IS_HEVC else "h264_nvenc"

# Map the x264/x265 preset names used elsewhere to NVENC's p1..p7 presets
# (p1=fastest, p7=slowest/best).  Intermediate "ultrafast"/"veryfast" passes map
# to a fast NVENC preset; the final "slow" pass maps to a high-quality one.
_NVENC_PRESET_MAP = {
    "ultrafast": "p1",
    "superfast": "p1",
    "veryfast": "p2",
    "faster": "p3",
    "fast": "p4",
    "medium": "p4",
    "slow": "p6",
    "slower": "p7",
    "veryslow": "p7",
}


def _nvenc_available() -> bool:
    """True when an NVIDIA GPU device and an NVENC-capable ffmpeg are present."""
    # A GPU character device is the cheapest reliable presence signal.
    if not any(os.path.exists(f"/dev/nvidia{i}") for i in range(4)):
        if not os.path.exists("/dev/nvidiactl"):
            return False
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except Exception:  # pragma: no cover - defensive
        return False
    return _NVENC_CODEC in (out.stdout or "")


@lru_cache(maxsize=1)
def _select_hwaccel_encoder() -> str | None:
    """Return the NVENC codec name to use, or ``None`` for the CPU path.

    Cached: detection runs at most once per process.
    """
    if _HWACCEL_MODE in ("none", "off", ""):
        return None
    if _HWACCEL_MODE == "nvenc":
        logger.info("VIDEO_HWACCEL=nvenc — forcing %s encoder", _NVENC_CODEC)
        return _NVENC_CODEC
    # "auto": only when actually available.
    if _nvenc_available():
        logger.info("NVENC detected — using %s for video encodes (issue #396)", _NVENC_CODEC)
        return _NVENC_CODEC
    return None


def _hwaccel_encode_args(codec: str, preset: str) -> list[str]:
    """Build NVENC encoder flags equivalent to the software encode settings.

    Uses constant-quality rate control (``-rc constqp -qp``) so the quality
    target mirrors the software CRF, keeping the 8-bit 4:2:0 / H.264 High
    constraints Spotify requires (issue #376).
    """
    nv_preset = _NVENC_PRESET_MAP.get(preset, "p4")
    args = [
        "-c:v", codec,
        "-preset", nv_preset,
        "-rc", "constqp",
        "-qp", str(ENCODE_CRF),
        "-pix_fmt", ENCODE_PIX_FMT,
    ]
    if codec == "h264_nvenc":
        args += ["-profile:v", "high"]
    return args



def _metadata_bsf_spec() -> str:
    """Codec-aware colour-VUI bitstream-filter spec normalising to BT.709.

    Uses ``hevc_metadata`` for HEVC and ``h264_metadata`` for H.264 so the final
    stream-copy pass tags consistent BT.709 limited-range colour for Spotify
    (issues #353, #376).
    """
    name = "hevc_metadata" if _IS_HEVC else "h264_metadata"
    return (
        f"{name}=colour_primaries=1:transfer_characteristics=1:"
        "matrix_coefficients=1:video_full_range_flag=0"
    )

# When the podcast audio outlasts the composed video, the final frame is held
# (via tpad) and then faded to black over this many seconds so the outro audio
# plays in full without an abrupt freeze.
OUTRO_VIDEO_FADE_SECONDS = 2.0

# Smallest content window we will ever fit segments into. Guards against a
# pathological audio duration that is shorter than the intro + outro bumpers
# (issue #355): the content must still occupy at least this many seconds.
MIN_CONTENT_WINDOW_SECONDS = 1.0

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
DOG_DEFAULT_OPACITY = 0.5
# Pixel inset from the frame edge for the watermark.
DOG_MARGIN = 40
# Seconds before the intro ends at which the DOG watermark starts appearing,
# so the logo is on screen before the intro→content join rather than popping in
# only once the content begins (#361).
DOG_INTRO_LEAD_SECONDS = 3.0

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
    *,
    enable: str | None = None,
) -> str:
    """Build the ffmpeg filter fragment overlaying the DOG logo onto the video.

    Scales the logo input to ``config.size`` px wide (aspect preserved), applies
    ``config.opacity`` via the alpha channel, then overlays it in the configured
    corner.  ``video_label`` is the current video stream label (e.g. ``"vout"``
    or ``"0:v"``); the result is exposed as ``out_label``.  When ``enable`` is a
    timeline expression (e.g. ``"gte(t,7.0)"``) the overlay is only shown while
    that expression is true, used to reveal the DOG over the tail of the intro
    bumper (#361).
    """
    position = _DOG_POSITIONS.get(config.position, _DOG_POSITIONS[DOG_DEFAULT_POSITION])
    overlay = f"[{video_label}][dog]overlay={position}:format=auto"
    if enable:
        overlay += f":enable='{enable}'"
    return (
        f"[{logo_input_idx}:v]scale={config.size}:-1,format=rgba,"
        f"colorchannelmixer=aa={config.opacity}[dog];"
        f"{overlay}[{out_label}]"
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


def _free_compose_intermediates(content_clip: Path, norm_dir: Path) -> None:
    """Delete composition intermediates that are no longer needed (disk relief).

    Once the intro/outro have been joined around the content into ``joined.mp4``,
    the standalone content clip and the per-segment normalized clips are never
    read again.  With near-lossless (crf 12) intermediates these dominate disk
    usage, and the final audio mux's video-extend branch re-encodes the whole
    video into yet another full-size file.  On the size-limited ACA ephemeral
    disk that peak exhausted storage and failed the job at the very last step.
    Best-effort: any cleanup error is swallowed so it never blocks composition.
    """
    try:
        if content_clip.exists():
            content_clip.unlink()
    except OSError:
        logger.debug("could not remove content intermediate %s", content_clip, exc_info=True)
    try:
        if norm_dir.is_dir():
            for clip in norm_dir.glob("*.mp4"):
                try:
                    clip.unlink()
                except OSError:
                    logger.debug("could not remove normalized clip %s", clip, exc_info=True)
    except OSError:
        logger.debug("could not scan normalized dir %s", norm_dir, exc_info=True)


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
        *_video_encode_args(INTERMEDIATE_PRESET),
        "-colorspace", "bt709",
        "-color_trc", "bt709",
        "-color_primaries", "bt709",
        "-color_range", "tv",
        str(output_path),
    ]


def _build_fit_segment_cmd(
    input_path: Path,
    output_path: Path,
    target_duration: float,
) -> list[str]:
    """Normalize a clip to 1080p/30fps and fit it to *target_duration* seconds.

    Unlike :func:`_build_normalize_cmd`, the output is forced to an exact
    duration so each content segment occupies precisely its slice of the audio
    timeline (issue #355):

    * If the source is **longer** than ``target_duration`` it is trimmed
      (``-t``).
    * If the source is **shorter**, its final frame is held (frozen) via
      ``tpad=stop_mode=clone`` and the result is then cut to the target.

    ``tpad`` appends up to ``target_duration`` extra seconds of cloned frames
    *after* the source ends, so the subsequent ``-t target_duration`` always has
    enough material regardless of how short the recording is — a single pass
    that both trims and freeze-extends without probing the source length.
    """
    target = max(target_duration, 0.0)
    vf = (
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={OUTPUT_FPS},"
        f"tpad=stop_mode=clone:stop_duration={target:.3f}"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-i", str(input_path),
        "-vf", vf,
        "-t", f"{target:.3f}",
        "-an",
        *_video_encode_args(INTERMEDIATE_PRESET),
        "-colorspace", "bt709",
        "-color_trc", "bt709",
        "-color_primaries", "bt709",
        "-color_range", "tv",
        str(output_path),
    ]


def _fit_target_durations(
    plan_durations: Sequence[float],
    content_window: float,
    transition_duration: float,
) -> list[float]:
    """Scale per-segment *plan_durations* to fill *content_window* exactly.

    The composed content overlaps adjacent segments by ``transition_duration``
    (one xfade per boundary), so for the post-overlap content to equal
    ``content_window`` the segment durations must sum to
    ``content_window + transition_duration * (n - 1)``.

    Durations are scaled proportionally so each segment keeps its share of the
    timeline (preserving the sync-plan alignment), then floored to just above
    ``transition_duration`` so every xfade pass remains valid.
    """
    n = len(plan_durations)
    if n == 0:
        return []
    overlap_total = transition_duration * max(0, n - 1)
    target_sum = max(content_window, MIN_CONTENT_WINDOW_SECONDS) + overlap_total

    source_sum = sum(plan_durations)
    if source_sum <= 0:
        even = target_sum / n
        scaled = [even] * n
    else:
        scale = target_sum / source_sum
        scaled = [d * scale for d in plan_durations]

    # An xfade boundary needs both clips strictly longer than the transition.
    floor = transition_duration + 0.5
    floored = [max(d, floor) for d in scaled]

    # Flooring can push the sum above ``target_sum``, which would make the
    # composed content longer than the audio timeline. Re-normalize the
    # headroom each segment has above ``floor`` so the durations sum back to
    # exactly ``target_sum`` while keeping every clip valid for xfade.
    floored_sum = sum(floored)
    if floored_sum > target_sum:
        headroom = [d - floor for d in floored]
        headroom_total = sum(headroom)
        excess = floored_sum - target_sum
        # Only redistributable when there is enough headroom above the floor;
        # otherwise the floor constraints alone exceed the target (infeasible)
        # and the floored values are the best valid result.
        if headroom_total >= excess > 0:
            floored = [
                d - excess * (h / headroom_total)
                for d, h in zip(floored, headroom)
            ]

    return floored


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
        *_video_encode_args(ENCODE_PRESET),
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
        # Fade only the held/padded region: start the fade where the original
        # video ends and clamp its length to the padding so it never bleeds into
        # the real footage when the padding is shorter than the fade window.
        fade_start = video_duration
        fade_duration = min(OUTRO_VIDEO_FADE_SECONDS, pad_duration)
        vf = (
            f"tpad=stop_mode=clone:stop_duration={pad_duration:.3f},"
            f"fade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}"
        )
        cmd += [
            "-vf", vf,
            *_video_encode_args(ENCODE_PRESET),
            "-colorspace", "bt709",
            "-color_trc", "bt709",
            "-color_primaries", "bt709",
            "-color_range", "tv",
        ]
    else:
        cmd += ["-c:v", "copy"]

    # Spotify rejects with VIDEO_DURATION_LONGER_THAN_AUDIO when the video
    # outlasts the audio. When the video is the longer stream, pad the audio
    # track with trailing silence up to the full video duration (issue #353).
    if 0 < audio_duration < video_duration:
        cmd += ["-af", f"apad=whole_dur={video_duration:.3f}"]

    cmd += [
        "-c:a", "aac",
        "-b:a", ENCODE_AUDIO_BITRATE,
        "-ar", CONCAT_AUDIO_SAMPLE_RATE,
        "-ac", CONCAT_AUDIO_CHANNELS,
        "-movflags", "+faststart",
        str(output_path),
    ]
    return cmd


def _build_h264_metadata_cmd(input_path: Path, output_path: Path) -> list[str]:
    """Rewrite the video VUI colour metadata to a consistent set (stream copy).

    The concat demuxer copies coded NAL units from independently-encoded clips
    whose SPS VUI data may disagree, tripping Spotify's
    ``INCONSISTENT_COLOR_DETAILS`` check.  This final post-processing pass
    normalises ``colour_primaries``/``transfer_characteristics``/
    ``matrix_coefficients`` to BT.709 (value ``1``) with a limited-range flag
    via the codec-appropriate metadata bitstream filter — ``h264_metadata`` for
    H.264 or ``hevc_metadata`` for HEVC — with no re-encode (issues #353, #376).
    """
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(input_path),
        "-c:v", "copy",
        "-bsf:v",
        _metadata_bsf_spec(),
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]


def _build_intro_dog_cmd(
    intro_path: Path,
    dog_logo: "DogLogoConfig",
    dog_logo_path: Path,
    enable_start: float,
    output_path: Path,
    preset: str = ENCODE_PRESET,
) -> list[str]:
    """Overlay the DOG logo onto the intro clip for its final seconds (#361).

    The watermark is enabled only from *enable_start* (seconds) onward via an
    ``enable='gte(t,...)'`` expression so it appears over the tail of the intro
    bumper — and is therefore already on screen when the content begins — rather
    than covering the whole intro.  Encodes video-only at *preset*.
    """
    enable_expr = f"gte(t,{enable_start:.3f})"
    overlay = _build_dog_overlay_filter(dog_logo, 1, "0:v", enable=enable_expr)
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(intro_path),
        "-i", str(dog_logo_path),
        "-filter_complex", overlay,
        "-map", "[dogout]",
        *_encode_tail(preset),
        str(output_path),
    ]


def _build_outro_xfade_cmd(
    content_path: Path,
    outro_path: Path,
    transition: str,
    transition_duration: float,
    offset: float,
    output_path: Path,
) -> list[str]:
    """Crossfade the content tail into the outro head (issue #393).

    Both inputs must already be canonicalised to the same resolution / fps /
    pixel-format so the ``xfade`` filter accepts them.  Produces a **video-only**
    clip: the podcast MP3 is overlaid as the sole audio track later, so the
    source (silent) audio is dropped here and the crossfade never cuts audio.
    """
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(content_path),
        "-i", str(outro_path),
        "-filter_complex",
        f"[0:v][1:v]xfade=transition={transition}"
        f":duration={transition_duration}:offset={offset:.3f}[vx]",
        "-map", "[vx]",
        *_encode_tail(ENCODE_PRESET),
        str(output_path),
    ]


def _join_intro_outro(
    content_path: Path,
    output_path: Path,
    intro_path: Path | None,
    outro_path: Path | None,
    *,
    run: "CommandRunner",
    work_dir: Path,
    dog_logo: "DogLogoConfig | None" = None,
    dog_logo_path: Path | None = None,
    transition_duration: float = TRANSITION_DURATION,
    outro_transition: str = TRANSITION_FADE,
) -> float:
    """Prepend *intro_path* and append *outro_path* around *content_path*.

    Canonicalises each present clip to a uniform **video-only** AV format (a
    silent stereo track is synthesised so the concat-demuxer copy succeeds).
    The intro is hard-cut onto the content, while the content→outro boundary is
    joined with an ``xfade`` **crossfade** (issue #393) so the ending feels
    intentional rather than abrupt — the same xfade filter used between content
    segments.  When either clip is too short to overlap safely, the join falls
    back to a hard cut.

    Source audio on the intro/outro clips is always stripped: the podcast MP3
    is overlaid as the sole audio track on the final joined video afterwards, so
    the crossfade is purely visual and never truncates audio.

    When *dog_logo* and *dog_logo_path* are provided, the DOG watermark is also
    overlaid on the final :data:`DOG_INTRO_LEAD_SECONDS` seconds of the intro
    clip so it is already on screen before the intro→content join (#361).

    Returns the total added duration (seconds) of the intro/outro clips —
    accounting for the crossfade overlap — so the caller can adjust the reported
    episode duration.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    canon_dir = work_dir / "join"
    canon_dir.mkdir(parents=True, exist_ok=True)

    added_duration = 0.0

    # 1. Canonicalise the intro (with optional DOG overlay).
    intro_canon: Path | None = None
    if intro_path is not None:
        _, intro_dur = _probe_media(intro_path, run)
        added_duration += intro_dur
        intro_src = intro_path
        if dog_logo is not None and dog_logo_path is not None:
            enable_start = max(0.0, intro_dur - DOG_INTRO_LEAD_SECONDS)
            dog_intro = canon_dir / "intro_dog.mp4"
            logger.info(
                "Overlaying DOG on intro tail from %.1fs (intro=%.1fs)",
                enable_start, intro_dur,
            )
            run(_build_intro_dog_cmd(
                intro_path, dog_logo, dog_logo_path, enable_start, dog_intro
            ))
            intro_src = dog_intro
        intro_canon = canon_dir / "intro.mp4"
        logger.info("Canonicalizing intro clip for concat: %s", intro_src)
        run(_build_canonical_av_cmd(intro_src, intro_canon, has_audio=False))

    # 2. Canonicalise the content.
    content_canon = canon_dir / "content.mp4"
    logger.info("Canonicalizing content clip for concat: %s", content_path)
    run(_build_canonical_av_cmd(content_path, content_canon, has_audio=False))

    # 3. Resolve the tail clip(s) representing content (+ crossfaded outro).
    #    ``tail_clips`` is concatenated after the intro.
    tail_clips: list[Path] = [content_canon]
    if outro_path is not None:
        _, outro_dur = _probe_media(outro_path, run)
        outro_canon = canon_dir / "outro.mp4"
        logger.info("Canonicalizing outro clip for concat: %s", outro_path)
        run(_build_canonical_av_cmd(outro_path, outro_canon, has_audio=False))

        _, content_dur = _probe_media(content_canon, run)
        td = transition_duration
        if td > 0 and content_dur > td and outro_dur > td:
            # Crossfade content tail into the outro head.  The xfade overlap
            # shortens the timeline by ``td`` seconds, so the outro effectively
            # contributes ``outro_dur - td`` to the total duration.
            offset = max(0.0, content_dur - td)
            content_outro_v = canon_dir / "content_outro_v.mp4"
            logger.info(
                "Crossfading content->outro (transition=%s, duration=%.2fs) "
                "at offset %.3fs",
                outro_transition, td, offset,
            )
            run(_build_outro_xfade_cmd(
                content_canon, outro_canon, outro_transition, td, offset,
                content_outro_v,
            ))
            # Re-canonicalise so the crossfaded clip carries the uniform
            # silent-audio layout the concat demuxer needs.
            content_outro = canon_dir / "content_outro.mp4"
            run(_build_canonical_av_cmd(
                content_outro_v, content_outro, has_audio=False
            ))
            tail_clips = [content_outro]
            added_duration += outro_dur - td
        else:
            # Too short to crossfade safely — fall back to a hard cut.
            logger.info(
                "Skipping content->outro crossfade (content=%.2fs, "
                "outro=%.2fs, transition=%.2fs); using hard cut",
                content_dur, outro_dur, td,
            )
            tail_clips = [content_canon, outro_canon]
            added_duration += outro_dur

    canon_paths: list[Path] = []
    if intro_canon is not None:
        canon_paths.append(intro_canon)
    canon_paths.extend(tail_clips)

    list_file = canon_dir / "concat.txt"
    list_file.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in canon_paths),
        encoding="utf-8",
    )
    logger.info(
        "Joining %d clip(s) (intro/content/outro) into %s",
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


def _compute_lower_thirds_by_index(
    segments: list[RecordedSegment],
    transition_duration: float = TRANSITION_DURATION,
    durations: list[float] | None = None,
) -> dict[int, LowerThird]:
    """Compute lower-third timings keyed by segment index.

    Each lower-third uses an **absolute** time offset in the final video so it
    can be baked in during whichever composition pass adds its segment.  A
    segment's lower-third starts ``0.5`` s after the segment becomes visible
    (which, accounting for the xfade overlaps, is
    ``sum(durations[0..i-1]) - i*transition_duration``) and lasts
    :data:`LOWER_THIRD_DURATION` seconds or until the segment ends.

    Generic background segments and segments too short to display a readable
    overlay are skipped (absent from the returned mapping).

    When *durations* is supplied it overrides each segment's recorded length —
    used by the fit-to-window path (issue #355) so overlay timing matches the
    trimmed/extended on-screen durations rather than the raw recordings.
    """
    lower_thirds: dict[int, LowerThird] = {}
    cumulative_time = 0.0

    for i, rec in enumerate(segments):
        seg = rec.segment
        seg_duration = durations[i] if durations is not None else seg.duration_seconds
        # After first segment, account for transition overlap
        if i > 0:
            start = cumulative_time
        else:
            start = 0.0

        # Lower third starts slightly after segment begins
        lt_start = start + 0.5
        lt_end = min(lt_start + LOWER_THIRD_DURATION, start + seg_duration - 0.5)

        if lt_end > lt_start and not seg.is_generic:
            lower_thirds[i] = LowerThird(
                text=f"{seg.repo.owner}/{seg.repo.name}",
                url=seg.repo.url,
                start_seconds=lt_start,
                end_seconds=lt_end,
            )

        cumulative_time += seg_duration
        if i < len(segments) - 1:
            cumulative_time -= transition_duration

    return lower_thirds


def _compute_lower_thirds(
    segments: list[RecordedSegment],
    transition_duration: float = TRANSITION_DURATION,
) -> list[LowerThird]:
    """Compute lower-third timings from recorded segments.

    Each lower-third appears at the start of its segment (after transition)
    and lasts LOWER_THIRD_DURATION seconds or until segment ends.
    """
    return list(
        _compute_lower_thirds_by_index(segments, transition_duration).values()
    )


# --- Pairwise xfade composition (#349) ---
# Instead of a single N-input filter_complex (which OOMs in the ACA container
# at ~18+ segments), segments are composited two at a time: each pass takes the
# running accumulator plus the next segment, applies one xfade transition and
# the new segment's lower-third, and writes a fresh intermediate.  Memory stays
# constant (2 inputs per pass) regardless of segment count.

# bt709 colour flags applied to every libx264 encode for consistent colour.
_BT709_FLAGS: list[str] = [
    "-colorspace", "bt709",
    "-color_trc", "bt709",
    "-color_primaries", "bt709",
    "-color_range", "tv",
]


def _encode_tail(preset: str) -> list[str]:
    """Common video-only libx264 encode flags (with bt709) for a given preset."""
    return [
        "-an",
        *_video_encode_args(preset),
        *_BT709_FLAGS,
        "-movflags", "+faststart",
    ]


def _build_xfade_step_cmd(
    accumulator_path: Path,
    segment_path: Path,
    transition: str,
    transition_duration: float,
    offset: float,
    step_lower_thirds: list[LowerThird],
    drawtext_bin: str | None,
    output_path: Path,
    preset: str,
) -> list[str]:
    """Build a single pairwise xfade pass (accumulator + one segment).

    Crossfades ``accumulator_path`` into ``segment_path`` at ``offset`` and,
    when a drawtext-capable ffmpeg is available, bakes in the lower-thirds for
    this pass (absolute-timed, so they land at the right moment in the final
    video).  Encodes video-only at ``preset`` with bt709 colour flags.
    """
    binary = drawtext_bin or "ffmpeg"
    cmd: list[str] = [
        binary, "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(accumulator_path),
        "-i", str(segment_path),
    ]
    filters = [
        f"[0:v][1:v]xfade=transition={transition}"
        f":duration={transition_duration}:offset={offset:.3f}[vx]"
    ]
    video_label = "vx"
    if step_lower_thirds and drawtext_bin:
        filters.append(_build_drawtext_filter(step_lower_thirds, "vx"))
        video_label = "final"
    cmd += ["-filter_complex", ";".join(filters), "-map", f"[{video_label}]"]
    cmd += _encode_tail(preset)
    cmd.append(str(output_path))
    return cmd


def _build_finalize_cmd(
    input_path: Path,
    dog_logo: "DogLogoConfig | None",
    dog_logo_path: Path | None,
    final_lower_thirds: list[LowerThird],
    drawtext_bin: str | None,
    output_path: Path,
    preset: str,
) -> list[str]:
    """Build the final pass: optional lower-thirds + optional DOG overlay.

    Used both for the single-segment case (where there is no xfade pass to
    carry the lower-third) and for the final DOG overlay on the fully
    accumulated video.  Encodes video-only at ``preset`` with bt709 flags.
    """
    binary = drawtext_bin or "ffmpeg"
    cmd: list[str] = [
        binary, "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(input_path),
    ]
    if dog_logo_path is not None:
        cmd += ["-i", str(dog_logo_path)]

    filters: list[str] = []
    video_label = "0:v"
    if final_lower_thirds and drawtext_bin:
        filters.append(_build_drawtext_filter(final_lower_thirds, "0:v"))
        video_label = "final"
    if dog_logo_path is not None and dog_logo is not None:
        filters.append(
            _build_dog_overlay_filter(dog_logo, 1, video_label)
        )
        video_label = "dogout"

    if filters:
        cmd += ["-filter_complex", ";".join(filters), "-map", f"[{video_label}]"]
    else:
        cmd += ["-map", "0:v"]
    cmd += _encode_tail(preset)
    cmd.append(str(output_path))
    return cmd


def _compose_pairwise(
    normalized_paths: list[Path],
    durations: list[float],
    transition_duration: float,
    transitions: list[str],
    lower_thirds_by_index: dict[int, LowerThird],
    drawtext_bin: str | None,
    dog_logo: "DogLogoConfig | None",
    dog_logo_path: Path | None,
    compose_target: Path,
    run: "CommandRunner",
    work_dir: Path,
    *,
    fetch: "Callable[[Path], None] | None" = None,
    release: "Callable[[Path], None] | None" = None,
) -> None:
    """Composite normalized segments pairwise into *compose_target* (video-only).

    Runs ``len(segments) - 1`` sequential xfade passes (each with exactly two
    video inputs → constant memory), baking each segment's lower-third in as it
    is added.  Intermediates use ``-preset ultrafast``; the final output (the
    DOG overlay pass when a logo is present, otherwise the last xfade pass)
    uses :data:`ENCODE_PRESET`.  Intermediate files are deleted as soon as they
    are consumed to keep disk usage bounded.

    ``fetch``/``release`` (issue #410): when supplied, each input clip is fetched
    just-in-time before it is consumed and released immediately afterwards, so a
    blob-checkpointed run never holds more than the accumulator, the current
    input, and the current output on local disk.  When omitted both default to
    no-ops and the clips are expected to already be local.
    """
    _fetch = fetch or (lambda _p: None)
    _release = release or (lambda _p: None)

    def _budget(*paths: Path) -> None:
        sizes = [p.stat().st_size for p in paths if p.exists()]
        total = sum(sizes)
        # input footprint + an xfade output of roughly the inputs' combined size.
        ensure_disk_budget(work_dir, total * 2)

    n = len(normalized_paths)
    has_dog = dog_logo is not None and dog_logo_path is not None

    if n == 1:
        _fetch(normalized_paths[0])
        final_lts = (
            [lower_thirds_by_index[0]] if 0 in lower_thirds_by_index else []
        )
        run(
            _build_finalize_cmd(
                normalized_paths[0],
                dog_logo if has_dog else None,
                dog_logo_path if has_dog else None,
                final_lts,
                drawtext_bin,
                compose_target,
                ENCODE_PRESET,
            )
        )
        _release(normalized_paths[0])
        return

    pair_dir = work_dir / "pairwise"
    pair_dir.mkdir(parents=True, exist_ok=True)

    accumulator = normalized_paths[0]
    _fetch(accumulator)
    accumulator_is_intermediate = False
    # When True the accumulator is the (fetched) normalized clip at index 0 and
    # must be released — not unlinked as a pairwise intermediate — once consumed.
    accumulator_is_input = True
    # Length of the accumulated video so far (segments 0..i-1 with overlaps).
    cumulative = durations[0]

    for i in range(1, n):
        offset = cumulative - transition_duration

        # Segment 0 has no xfade pass of its own, so its lower-third rides
        # along on the first pass; every other segment's rides on its own pass.
        step_lts: list[LowerThird] = []
        if i == 1 and 0 in lower_thirds_by_index:
            step_lts.append(lower_thirds_by_index[0])
        if i in lower_thirds_by_index:
            step_lts.append(lower_thirds_by_index[i])

        is_last_pass = i == n - 1
        if is_last_pass and not has_dog:
            out_path = compose_target
            preset = ENCODE_PRESET
        else:
            out_path = pair_dir / f"acc_{i:03d}.mp4"
            preset = "ultrafast"

        _fetch(normalized_paths[i])
        _budget(accumulator, normalized_paths[i])
        run(
            _build_xfade_step_cmd(
                accumulator,
                normalized_paths[i],
                transitions[i - 1],
                transition_duration,
                offset,
                step_lts,
                drawtext_bin,
                out_path,
                preset,
            )
        )

        # Free the just-consumed input clip and the previous accumulator.
        _release(normalized_paths[i])
        if accumulator_is_intermediate:
            accumulator.unlink(missing_ok=True)
        elif accumulator_is_input:
            _release(accumulator)
        accumulator = out_path
        accumulator_is_intermediate = out_path != compose_target
        accumulator_is_input = False
        cumulative += durations[i] - transition_duration

    if has_dog:
        run(
            _build_finalize_cmd(
                accumulator,
                dog_logo,
                dog_logo_path,
                [],
                drawtext_bin,
                compose_target,
                ENCODE_PRESET,
            )
        )
        if accumulator_is_intermediate:
            accumulator.unlink(missing_ok=True)


def _splice_section_cards(
    normalized_paths: list[Path],
    durations: list[float],
    transitions: list[str],
    lower_thirds_by_index: dict[int, LowerThird],
    inserts: list[tuple[int, Path, float]],
    transition_duration: float = TRANSITION_DURATION,
) -> tuple[list[Path], list[float], list[str], dict[int, LowerThird]]:
    """Splice section title cards into the normalized content stream (#377).

    Each entry in *inserts* is ``(before_index, card_path, card_duration)`` and
    places a card immediately *before* content segment ``before_index`` (clamped
    to ``[0, len(content)]``; ``len(content)`` appends at the end).  Every
    boundary adjacent to a card uses a fade transition; original content↔content
    boundaries keep their selected transition.  Cards carry no lower-third, and
    each content lower-third is re-keyed to its new position *and* time-shifted
    by the card time inserted ahead of it (each card advances the absolute
    timeline by ``card_duration - transition_duration``) so overlays still fire
    over their own segment rather than the preceding card.

    Args:
        normalized_paths: Content segment clip paths (already normalized).
        durations: On-screen duration per content segment, parallel to paths.
        transitions: Selected transition per content boundary
            (``len == len(normalized_paths) - 1``).
        lower_thirds_by_index: Lower-third overlays keyed by content index.
        inserts: Card insertions ``(before_index, card_path, card_duration)``.
        transition_duration: xfade overlap length, used to time-shift overlays.

    Returns:
        ``(paths, durations, transitions, lower_thirds_by_index)`` for the
        combined content+card stream, ready for :func:`_compose_pairwise`.
    """
    n = len(normalized_paths)
    by_idx: dict[int, list[tuple[Path, float]]] = {}
    for before, card_path, card_dur in inserts:
        clamped = max(0, min(before, n))
        by_idx.setdefault(clamped, []).append((card_path, card_dur))

    new_paths: list[Path] = []
    new_durs: list[float] = []
    new_trans: list[str] = []
    new_lts: dict[int, LowerThird] = {}
    # Running absolute-timeline shift introduced by the cards emitted so far.
    time_shift = 0.0

    def emit(path: Path, dur: float, lt: LowerThird | None, trans_in: str | None) -> None:
        if new_paths and trans_in is not None:
            new_trans.append(trans_in)
        idx = len(new_paths)
        new_paths.append(path)
        new_durs.append(dur)
        if lt is not None:
            new_lts[idx] = replace(
                lt,
                start_seconds=lt.start_seconds + time_shift,
                end_seconds=lt.end_seconds + time_shift,
            )

    for k in range(n):
        had_card_before = k in by_idx
        for card_path, card_dur in by_idx.get(k, []):
            emit(card_path, card_dur, None, TRANSITION_FADE)
            time_shift += card_dur - transition_duration
        if k == 0:
            t_in = TRANSITION_FADE if had_card_before else None
        else:
            t_in = TRANSITION_FADE if had_card_before else transitions[k - 1]
        emit(normalized_paths[k], durations[k], lower_thirds_by_index.get(k), t_in)

    # Trailing cards appended after the final content segment.
    for card_path, card_dur in by_idx.get(n, []):
        emit(card_path, card_dur, None, TRANSITION_FADE)

    return new_paths, new_durs, new_trans, new_lts


def _finalize_output(
    *,
    video_only_path: Path,
    video_duration: float,
    audio_path: Path | None,
    output_path: Path,
    segment_count: int,
    run: "CommandRunner",
) -> ComposeResult:
    """Mux the podcast audio (if any) over the composed video and finalise.

    Shared by the normal compose path and the checkpoint-resume path (issue
    #410): given a finished video-only clip and the podcast MP3, this is the only
    work the *final mux* needs — it downloads/uses just the composed video plus
    the audio, never the per-segment intermediates.
    """
    needs_audio = audio_path is not None
    if needs_audio:
        muxed_target = output_path.parent / "muxed.mp4"
        _, audio_duration = _probe_media(audio_path, run)
        _, probed_video_duration = _probe_media(video_only_path, run)
        effective_video_duration = probed_video_duration or video_duration
        run(_build_audio_overlay_cmd(
            video_only_path,
            audio_path,
            muxed_target,
            video_duration=effective_video_duration,
            audio_duration=audio_duration,
        ))
        pre_final_path = muxed_target
        # Audio is never truncated and is padded to at least the video length,
        # so the muxed output runs for the longer stream.
        total_duration = (
            max(effective_video_duration, audio_duration)
            if audio_duration > 0
            else effective_video_duration
        )
    else:
        pre_final_path = video_only_path
        total_duration = video_duration

    # Final post-processing: normalise H.264 colour metadata (stream copy).
    run(_build_h264_metadata_cmd(pre_final_path, output_path))

    return ComposeResult(
        output_path=output_path,
        duration_seconds=total_duration,
        segment_count=segment_count,
        has_audio=audio_path is not None,
    )


# Blob checkpoint name for the finished video-only composed clip (issue #410).
COMPOSED_VIDEO_CHECKPOINT = "composed_video.mp4"


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
    audio_duration: float | None = None,
    section_cards: "list[SectionCardInsert] | None" = None,
    intermediates=None,
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
            overlaid on the main content segments in the configured corner at
            the configured size/opacity.  It additionally appears over the final
            :data:`DOG_INTRO_LEAD_SECONDS` seconds of the intro bumper so it is
            on screen before the intro ends (#361); the outro stays unbranded.
            A failed download is skipped silently (graceful degradation).
        dog_logo_cache_dir: Local cache directory for the downloaded DOG logo.
            Defaults to a stable temp-dir location.
        audio_duration: Total podcast audio length in seconds.  When provided
            (and positive), the content segments are *fit to the audio
            timeline* (issue #355): the intro and outro bumpers always play in
            full and the content is trimmed/freeze-extended to fill exactly
            ``audio_duration - intro_duration - outro_duration``.  Each segment
            keeps its proportional slice of the timeline so the right repo is on
            screen while the hosts discuss it.  When ``None`` the legacy
            behaviour (content length follows the recordings) is preserved.
        section_cards: Optional section title cards (issue #377) to splice into
            the content stream at section boundaries.  Each entry names the
            content segment it precedes and a pre-rendered card clip; the cards
            play at their fixed duration (they are excluded from fit-to-window)
            with fade transitions on both sides.  ``None``/empty is a no-op.

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
    # The content is always composed video-only and never written directly to
    # output_path: the pipeline always ends with the h264_metadata BSF pass
    # (issue #353), which requires a distinct input and output file.
    has_bookends = intro_path is not None or outro_path is not None
    compose_target = output_path.parent / "content.mp4"

    # Checkpoint/resume (issue #410): when the finished video-only composed clip
    # already survived in blob from a previous (interrupted) run, skip the whole
    # record→normalize→compose→join pipeline and go straight to the final mux,
    # which needs only the composed video plus the podcast audio.
    _intermediates_enabled = intermediates is not None and getattr(intermediates, "enabled", False)
    if _intermediates_enabled and intermediates.exists(COMPOSED_VIDEO_CHECKPOINT):
        resumed_video = output_path.parent / COMPOSED_VIDEO_CHECKPOINT
        if intermediates.download(COMPOSED_VIDEO_CHECKPOINT, resumed_video):
            _, resumed_duration = _probe_media(resumed_video, run)
            logger.info(
                "Resumed composed video from blob checkpoint (%.1fs); skipping to final mux",
                resumed_duration,
            )
            return _finalize_output(
                video_only_path=resumed_video,
                video_duration=resumed_duration,
                audio_path=audio_path,
                output_path=output_path,
                segment_count=len(segments),
                run=run,
            )

    # Fit-to-window planning (issue #355): when the audio duration is known we
    # trim/freeze the content so it fills exactly the audio timeline minus the
    # intro and outro bumpers (which always play in full).  Probe the bumper
    # durations up front so the content window can be computed.
    fit_to_window = audio_duration is not None and audio_duration > 0
    fit_durations: list[float] | None = None
    # Total wall-clock the section cards add to the timeline.  Each spliced card
    # contributes one *net* extra xfade boundary, so the content window is shrunk
    # by (sum of card durations − transition per card) to keep total video length
    # aligned with the audio timeline (issue #377).
    section_cards = section_cards or []
    card_reserve = sum(
        max(0.0, c.duration_seconds - transition_duration) for c in section_cards
    )
    if fit_to_window:
        intro_dur = _probe_media(intro_path, run)[1] if intro_path else 0.0
        outro_dur = _probe_media(outro_path, run)[1] if outro_path else 0.0
        content_window = max(
            audio_duration - intro_dur - outro_dur - card_reserve,
            MIN_CONTENT_WINDOW_SECONDS,
        )
        plan_durations = [seg.segment.duration_seconds for seg in segments]
        fit_durations = _fit_target_durations(
            plan_durations, content_window, transition_duration
        )
        logger.info(
            "Fitting %d content segment(s) to %.1fs window "
            "(audio=%.1fs, intro=%.1fs, outro=%.1fs)",
            len(segments), content_window, audio_duration, intro_dur, outro_dur,
        )

    # Step 1: Normalize all segments to 1080p/30fps (fitting each to its target
    # duration when fit-to-window is active).  Each segment is an independent
    # ffmpeg process, so normalization is run in parallel across cores — the
    # single biggest video-generation speed-up (issue #376).  Output paths stay
    # index-ordered regardless of completion order.
    norm_dir = output_path.parent / "normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)

    normalized_paths = [norm_dir / f"seg_{i:03d}.mp4" for i in range(len(segments))]
    # Map each per-segment normalized clip to its blob checkpoint name so the
    # pairwise compose can fetch it just-in-time and release it immediately after
    # use (issue #410), keeping only the clip currently being composed on disk.
    blob_name_by_path: dict[Path, str] = {
        normalized_paths[i]: f"normalized_{i:03d}.mp4" for i in range(len(segments))
    }

    # Per-segment normalize tasks.  In the blob-checkpoint path each task resolves
    # its raw recording (downloading it from blob on demand), normalizes it,
    # uploads the size-verified normalized clip, and then deletes *both* local
    # files — so the parallel normalize phase only ever holds a bounded number of
    # clips on disk (one raw + one normalized per worker) instead of every
    # segment at once (issue #410).  When checkpointing is disabled the legacy
    # all-local behaviour is preserved.
    norm_tasks: list = []
    for i, rec in enumerate(segments):
        norm_tasks.append((i, rec, normalized_paths[i]))

    def _normalize_one(task) -> None:
        idx, rec, dest = task
        name = f"normalized_{idx:03d}.mp4"
        # Already checkpointed: skip recompute.  In the enabled path we do NOT
        # download it here — the pairwise compose fetches it just-in-time so all
        # normalized clips never coexist on local disk.
        if _intermediates_enabled and intermediates.exists(name):
            logger.info("Resumed normalized segment %d from blob checkpoint", idx)
            return

        input_path = rec.video_path
        if _intermediates_enabled and not input_path.exists():
            # The raw recording was uploaded then freed by record_episode; pull it
            # back transiently to normalize it.
            suffix = rec.video_path.suffix or ".webm"
            raw_dest = norm_dir / f"_raw_{idx:03d}{suffix}"
            if intermediates.download(_recording_blob_name(idx, suffix), raw_dest):
                input_path = raw_dest

        if fit_durations is not None:
            cmd = _build_fit_segment_cmd(input_path, dest, fit_durations[idx])
            logger.info("Fitting segment %d to %.1fs: %s", idx, fit_durations[idx], input_path.name)
        else:
            cmd = _build_normalize_cmd(input_path, dest)
            logger.info("Normalizing segment %d: %s", idx, input_path.name)

        if _intermediates_enabled and input_path.exists():
            # input + an estimated same-order normalized output.
            in_size = input_path.stat().st_size
            ensure_disk_budget(norm_dir, in_size * 2)

        try:
            run(cmd)
            if _intermediates_enabled:
                # Size-verified upload; only free the local normalized clip when
                # the checkpoint is confirmed (otherwise keep it on disk so the
                # pairwise compose can still consume it directly — a failed
                # checkpoint must not become a hard compose failure).
                if intermediates.upload(name, dest, "video/mp4"):
                    try:
                        dest.unlink(missing_ok=True)
                    except OSError:
                        logger.debug("could not free normalized clip %s", dest, exc_info=True)
        finally:
            # The raw recording is no longer needed once the normalized clip
            # exists; free it (whether we downloaded it here or it was a resumed
            # local) so recordings never accumulate through the compose phase.
            if _intermediates_enabled:
                try:
                    input_path.unlink(missing_ok=True)
                except OSError:
                    logger.debug("could not free raw recording %s", input_path, exc_info=True)

    def _normalize_one_with_retry(task) -> None:
        idx = task[0]
        retry_call(
            lambda: _normalize_one(task),
            attempts=NORMALIZE_TASK_RETRIES,
            description=f"normalize segment {idx}",
        )

    workers = min(NORMALIZE_WORKERS, len(norm_tasks))
    if workers > 1:
        logger.info(
            "Normalizing %d segment(s) with %d parallel workers",
            len(norm_tasks), workers,
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Consume the iterator so any ffmpeg failure is re-raised here.
            list(pool.map(_normalize_one_with_retry, norm_tasks))
    else:
        for task in norm_tasks:
            _normalize_one_with_retry(task)

    # Step 2: Plan transitions and lower-thirds for pairwise composition.
    # When fitting, the segment durations on screen are the fitted targets, not
    # the original recording lengths.
    durations = (
        list(fit_durations)
        if fit_durations is not None
        else [seg.segment.duration_seconds for seg in segments]
    )

    if len(segments) >= 2:
        n_boundaries = len(segments) - 1
        chosen_transitions = select_transitions(n_boundaries, boundary_kinds)
    else:
        chosen_transitions = []

    # Step 3: Lower-third overlays — only probe for a drawtext-capable ffmpeg
    # when there is at least one lower-third to draw.
    lower_thirds_by_index = _compute_lower_thirds_by_index(
        segments, transition_duration, durations=fit_durations
    )
    drawtext_bin: str | None = None
    if lower_thirds_by_index:
        drawtext_bin = _find_drawtext_capable_ffmpeg()
        if drawtext_bin is None:
            logger.warning(
                "No ffmpeg binary with drawtext filter (libfreetype) found; "
                "skipping lower-third overlays.  Install system ffmpeg "
                "(e.g. apt install ffmpeg) or a build with libfreetype to enable overlays."
            )

    # Step 3.5: DOG (Digital On-Screen Graphic) watermark — overlaid on the main
    # content here, before intro/outro are joined.  It is additionally overlaid
    # on the intro tail during the join so it appears before the intro ends (#361).
    dog_logo_path: Path | None = None
    if dog_logo is not None:
        dog_cache = dog_logo_cache_dir or _default_dog_cache_dir()
        dog_logo_path = _fetch_dog_logo(dog_logo.url, dog_cache)

    # Step 3.6: Splice section title cards into the content stream (#377).  Cards
    # are normalized to the canonical layout (so the xfade copy path stays valid)
    # and inserted at their section boundaries with fade transitions.  They play
    # at their fixed duration — excluded from the fit-to-window scaling above.
    if section_cards:
        card_norm_paths: list[Path] = []
        for ci, card in enumerate(section_cards):
            dest = norm_dir / f"card_{ci:03d}.mp4"
            card_norm_paths.append(dest)
            run(_build_normalize_cmd(card.clip_path, dest))
        inserts = [
            (card.before_index, card_norm_paths[ci], card.duration_seconds)
            for ci, card in enumerate(section_cards)
        ]
        normalized_paths, durations, chosen_transitions, lower_thirds_by_index = (
            _splice_section_cards(
                normalized_paths,
                durations,
                chosen_transitions,
                lower_thirds_by_index,
                inserts,
                transition_duration,
            )
        )
        logger.info(
            "Spliced %d section title card(s) into content (%d clips total)",
            len(section_cards), len(normalized_paths),
        )

    # Step 4: Composite the (video-only) content pairwise — each pass uses
    # exactly two video inputs so memory stays constant regardless of segment
    # count (replaces the old N-input filter_complex that OOMed at ~18 segments).
    #
    # In the blob-checkpoint path the per-segment normalized clips were uploaded
    # and freed during normalize, so the pairwise pass fetches each one back
    # just-in-time and releases it immediately after the xfade step consumes it
    # (issue #410).  This keeps disk to ~(accumulator + one input + one output)
    # instead of all normalized clips at once.
    def _fetch_clip(path: Path) -> None:
        if path.exists():
            return
        if _intermediates_enabled and path in blob_name_by_path:
            if not intermediates.download(blob_name_by_path[path], path):
                raise RuntimeError(f"could not fetch normalized clip checkpoint for {path.name}")

    def _release_clip(path: Path) -> None:
        # Only reclaim per-segment normalized clips in the enabled path; the
        # legacy path keeps them for _free_compose_intermediates to remove later.
        if _intermediates_enabled and path in blob_name_by_path:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.debug("could not release normalized clip %s", path, exc_info=True)

    logger.info("Composing %d clips into %s", len(normalized_paths), compose_target)
    _compose_pairwise(
        normalized_paths,
        durations,
        transition_duration,
        chosen_transitions,
        lower_thirds_by_index,
        drawtext_bin,
        dog_logo,
        dog_logo_path,
        compose_target,
        run,
        output_path.parent,
        fetch=_fetch_clip,
        release=_release_clip,
    )
    # Content video duration (accounting for transition overlaps).
    video_duration = sum(durations) - transition_duration * max(0, len(normalized_paths) - 1)

    # Prepend intro / append outro around the composed content (video-only).
    if has_bookends:
        joined_target = output_path.parent / "joined.mp4"
        added = _join_intro_outro(
            compose_target,
            joined_target,
            intro_path,
            outro_path,
            run=run,
            work_dir=output_path.parent,
            dog_logo=dog_logo,
            dog_logo_path=dog_logo_path,
            transition_duration=transition_duration,
        )
        video_duration += added
        video_only_path = joined_target
        # Free the now-unreferenced intermediates (the composed content clip and
        # the per-segment normalized clips) before the final audio mux.  With
        # near-lossless intermediates these are the largest files on disk, and
        # the mux's ``extend_video`` branch re-encodes the whole video into an
        # additional full-size file — on the size-limited ACA ephemeral storage
        # that combination exhausted the disk and failed the job.  joined.mp4 is
        # the only video input the mux needs from here on.
        _free_compose_intermediates(compose_target, norm_dir)
    else:
        video_only_path = compose_target

    # Checkpoint the finished video-only composed clip (issue #410) so a crash
    # during the final audio mux can resume straight from here next time.
    if _intermediates_enabled:
        intermediates.upload(COMPOSED_VIDEO_CHECKPOINT, video_only_path, "video/mp4")
        intermediates.mark("composed_video", duration_seconds=round(video_duration, 3))

    # Overlay the podcast MP3 as the sole audio track on the full video, then
    # always run a final h264_metadata BSF pass into output_path so the colour
    # VUI is consistent for Spotify (issue #353).
    return _finalize_output(
        video_only_path=video_only_path,
        video_duration=video_duration,
        audio_path=audio_path,
        output_path=output_path,
        segment_count=len(segments),
        run=run,
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
