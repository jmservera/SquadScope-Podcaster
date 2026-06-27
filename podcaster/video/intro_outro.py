"""Generate animated intro/outro video clips using Playwright.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Renders HTML pages with CSS animations in headless Chromium, recording
them as WebM files for video_compose.py to concatenate.

Tool selection: Issue #241 research evaluated HyperFrames and Playwright.
Playwright was selected for this implementation because it supports full CSS
animations and produces consistent WebM output that integrates cleanly with
the ffmpeg-based video_compose pipeline. See #241 for the decision record.

This module also provides ffmpeg-native title cards (no Playwright required)
for systems where a drawtext-capable ffmpeg is available (#295).

Related: jmservera/SquadScope-Podcaster#241.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from podcaster.video.localization import overlay_copy_for

try:
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]
    _PLAYWRIGHT_AVAILABLE = False

try:
    from podcaster.video.video_compose import (
        _find_drawtext_capable_ffmpeg as _get_drawtext_ffmpeg,
    )
except ImportError:  # pragma: no cover

    def _get_drawtext_ffmpeg() -> str | None:  # type: ignore[misc]
        return None


logger = logging.getLogger(__name__)

# --- Constants ---

WIDTH = 1920
HEIGHT = 1080
INTRO_DURATION_MS = 5000
OUTRO_DURATION_MS = 5000
FPS = 30

# Font for ffmpeg drawtext title cards (same as video_compose lower-thirds)
TITLE_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Default show branding
DEFAULT_SHOW_NAME = "Claracle Weekly"

CLARACLE_URL = "www.claracle.com"
REPO_LINKS = [
    ("SquadScope", "https://github.com/jmservera/SquadScope"),
    ("SquadScope-Podcaster", "https://github.com/jmservera/SquadScope-Podcaster"),
    ("SquadScope-Coordinator", "https://github.com/jmservera/SquadScope-Coordinator"),
]

# --- HTML Templates ---

INTRO_HTML = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  width: {width}px; height: {height}px;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1c2128 100%);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  overflow: hidden;
}}
.container {{
  text-align: center;
  animation: fadeInUp 1.2s ease-out forwards;
  opacity: 0;
}}
@keyframes fadeInUp {{
  from {{ opacity: 0; transform: translateY(40px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
.brand {{
  font-size: 96px; font-weight: 700;
  background: linear-gradient(90deg, #58a6ff, #bc8cff, #f778ba);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
  animation: shimmer 3s ease-in-out infinite;
  background-size: 200% 100%;
}}
@keyframes shimmer {{
  0%, 100% {{ background-position: 0% 50%; }}
  50% {{ background-position: 100% 50%; }}
}}
.episode-title {{
  font-size: 36px; color: #c9d1d9;
  margin-top: 24px;
  animation: fadeInUp 1.2s ease-out 0.5s forwards;
  opacity: 0;
}}
.subtitle {{
  font-size: 22px; color: #8b949e;
  margin-top: 12px;
  animation: fadeInUp 1.2s ease-out 0.8s forwards;
  opacity: 0;
}}
</style></head><body>
<div class="container">
  <div class="brand">{show_name}</div>
  <div class="episode-title">{episode_title}</div>
  <div class="subtitle">{subtitle}</div>
</div>
</body></html>
"""

OUTRO_HTML = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  width: {width}px; height: {height}px;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1c2128 100%);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  overflow: hidden;
}}
.container {{
  text-align: center;
  animation: fadeIn 1s ease-out forwards;
  opacity: 0;
}}
@keyframes fadeIn {{
  from {{ opacity: 0; }}
  to {{ opacity: 1; }}
}}
.brand {{
  font-size: 72px; font-weight: 700;
  color: #c9d1d9;
  margin-bottom: 16px;
}}
.url {{
  font-size: 36px;
  color: #58a6ff;
  margin-bottom: 40px;
  animation: fadeIn 1s ease-out 0.4s forwards;
  opacity: 0;
}}
.links {{
  display: flex; gap: 32px; justify-content: center;
  animation: fadeIn 1s ease-out 0.8s forwards;
  opacity: 0;
}}
.link-item {{
  font-size: 20px; color: #8b949e;
  text-decoration: none;
  padding: 12px 24px;
  border: 1px solid #30363d; border-radius: 8px;
  background: #21262d;
}}
.cta {{
  margin-top: 40px; font-size: 24px; color: #f0883e;
  animation: pulse 2s ease-in-out infinite;
}}
@keyframes pulse {{
  0%, 100% {{ opacity: 0.7; }}
  50% {{ opacity: 1; }}
}}
</style></head><body>
<div class="container">
  <div class="brand">{show_name}</div>
  <div class="url">{url}</div>
  <div class="links">
    {links_html}
  </div>
  <div class="cta">{cta}</div>
</div>
</body></html>
"""


# --- Data Classes ---


@dataclass
class IntroConfig:
    """Configuration for intro clip generation."""

    show_name: str = DEFAULT_SHOW_NAME
    episode_title: str = "Untitled Episode"
    subtitle: str = ""
    duration_ms: int = INTRO_DURATION_MS
    width: int = WIDTH
    height: int = HEIGHT
    locale: str = "en"

    @classmethod
    def for_locale(
        cls,
        locale: str,
        *,
        episode_title: str | None = None,
        subtitle: str = "",
        show_name: str | None = None,
        duration_ms: int = INTRO_DURATION_MS,
        width: int = WIDTH,
        height: int = HEIGHT,
    ) -> "IntroConfig":
        """Build an intro config with localized defaults for *locale*.

        ``show_name``/``episode_title`` override the localized defaults when
        provided (e.g. a per-language ``LanguageConfig.show_name`` from #432).
        """

        copy = overlay_copy_for(locale)
        return cls(
            show_name=show_name if show_name is not None else copy.show_name,
            episode_title=(
                episode_title if episode_title is not None else copy.default_episode_title
            ),
            subtitle=subtitle,
            duration_ms=duration_ms,
            width=width,
            height=height,
            locale=locale,
        )


@dataclass
class OutroConfig:
    """Configuration for outro clip generation."""

    show_name: str = DEFAULT_SHOW_NAME
    url: str = CLARACLE_URL
    links: list[tuple[str, str]] | None = None
    duration_ms: int = OUTRO_DURATION_MS
    width: int = WIDTH
    height: int = HEIGHT
    cta: str = "Subscribe & Follow"
    locale: str = "en"

    def __post_init__(self) -> None:
        if self.links is None:
            self.links = list(REPO_LINKS)

    @classmethod
    def for_locale(
        cls,
        locale: str,
        *,
        show_name: str | None = None,
        url: str = CLARACLE_URL,
        links: list[tuple[str, str]] | None = None,
        cta: str | None = None,
        duration_ms: int = OUTRO_DURATION_MS,
        width: int = WIDTH,
        height: int = HEIGHT,
    ) -> "OutroConfig":
        """Build an outro config with localized defaults for *locale*."""

        copy = overlay_copy_for(locale)
        return cls(
            show_name=show_name if show_name is not None else copy.show_name,
            url=url,
            links=links,
            cta=cta if cta is not None else copy.outro_cta,
            duration_ms=duration_ms,
            width=width,
            height=height,
            locale=locale,
        )


@dataclass
class ClipResult:
    """Result of generating a video clip."""

    path: Path
    duration_ms: int
    width: int
    height: int


# --- Core Functions ---


def _render_intro_html(config: IntroConfig) -> str:
    """Render the intro HTML template with config values."""
    return INTRO_HTML.format(
        width=config.width,
        height=config.height,
        show_name=html_mod.escape(config.show_name),
        episode_title=html_mod.escape(config.episode_title),
        subtitle=html_mod.escape(config.subtitle),
    )


def _render_outro_html(config: OutroConfig) -> str:
    """Render the outro HTML template with config values."""
    links_html = "\n    ".join(
        f'<a class="link-item" href="{html_mod.escape(url)}">{html_mod.escape(name)}</a>'
        for name, url in (config.links or [])
    )
    return OUTRO_HTML.format(
        width=config.width,
        height=config.height,
        show_name=html_mod.escape(config.show_name),
        url=html_mod.escape(config.url),
        links_html=links_html,
        cta=html_mod.escape(config.cta),
    )


def _record_html_to_video(
    html_content: str,
    output_path: Path,
    duration_ms: int,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Path:
    """Render HTML page with Playwright and record to WebM.

    Opens a headless Chromium browser, loads the HTML content,
    waits for animations to complete, and records the viewport.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright is not installed. "
            "Install with: pip install 'podcaster[video]' && playwright install chromium"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            record_video_dir=str(output_path.parent),
            record_video_size={"width": width, "height": height},
        )
        page = context.new_page()
        page.set_content(html_content)
        # Wait for CSS animations to play
        page.wait_for_timeout(duration_ms)
        # Close context first to finalize the recorded video file
        video = page.video
        context.close()
        # Resolve path after close (Playwright finalizes on close)
        video_path = Path(video.path()) if video else None
        browser.close()

    # Rename Playwright's auto-named file to the desired output path
    if video_path and video_path.exists():
        video_path.rename(output_path)
    else:
        raise RuntimeError(
            f"Playwright did not produce a video file{f' at {video_path}' if video_path else ''}"
        )

    if not output_path.exists():
        raise RuntimeError(f"Playwright recording failed: no video file produced at {output_path}")

    return output_path


def generate_intro(
    config: IntroConfig | None = None,
    output_dir: Path | None = None,
) -> ClipResult:
    """Generate an animated intro video clip.

    Args:
        config: Intro configuration. Uses defaults if None.
        output_dir: Directory for output. Uses tempdir if None.

    Returns:
        ClipResult with path to the generated WebM file.
    """
    if config is None:
        config = IntroConfig()

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="claracle_intro_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "intro.webm"
    html = _render_intro_html(config)

    _record_html_to_video(
        html_content=html,
        output_path=output_path,
        duration_ms=config.duration_ms,
        width=config.width,
        height=config.height,
    )

    return ClipResult(
        path=output_path,
        duration_ms=config.duration_ms,
        width=config.width,
        height=config.height,
    )


def generate_outro(
    config: OutroConfig | None = None,
    output_dir: Path | None = None,
) -> ClipResult:
    """Generate an animated outro video clip.

    Args:
        config: Outro configuration. Uses defaults if None.
        output_dir: Directory for output. Uses tempdir if None.

    Returns:
        ClipResult with path to the generated WebM file.
    """
    if config is None:
        config = OutroConfig()

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="claracle_outro_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "outro.webm"
    html = _render_outro_html(config)

    _record_html_to_video(
        html_content=html,
        output_path=output_path,
        duration_ms=config.duration_ms,
        width=config.width,
        height=config.height,
    )

    return ClipResult(
        path=output_path,
        duration_ms=config.duration_ms,
        width=config.width,
        height=config.height,
    )


# --- Script-based timing derivation (#295) ---

# Regex to detect explicit end-of-intro markers in scripts
_INTRO_END_MARKER_RE = re.compile(
    r"\[(?:INTRO[_\s]END|END[_\s]INTRO|CONTENT[_\s]START)\]",
    re.IGNORECASE,
)

# Regex to find the first GitHub repo URL (signals start of repo content)
_GITHUB_URL_RE = re.compile(r"https?://github\.com/")


def derive_intro_duration(
    script: str,
    words_per_minute: float = 130.0,
    default_seconds: float = 8.0,
    max_seconds: float = 30.0,
) -> float:
    """Estimate intro duration by timing the host introduction section of a script.

    Scans for either an explicit ``[INTRO_END]`` / ``[CONTENT_START]`` marker
    or the position of the first GitHub URL (which marks the start of repo
    content).  Counts words in the intro text and converts to seconds at
    *words_per_minute*.  Returns *default_seconds* when the boundary cannot be
    detected.  Result is clamped to ``[default_seconds, max_seconds]``.

    Args:
        script: Full podcast script text.
        words_per_minute: Average speaking pace. Default 130 wpm.
        default_seconds: Returned when intro section cannot be isolated.
        max_seconds: Upper bound on returned duration.

    Returns:
        Estimated intro duration in seconds.

    Raises:
        ValueError: If *words_per_minute* is not positive.
    """
    if words_per_minute <= 0:
        raise ValueError(f"words_per_minute must be positive, got {words_per_minute}")

    if not script.strip():
        return default_seconds

    # Prefer explicit end-of-intro marker
    end_marker = _INTRO_END_MARKER_RE.search(script)
    if end_marker:
        intro_text = script[: end_marker.start()]
    else:
        # Fall back: intro ends where first GitHub URL appears
        url_match = _GITHUB_URL_RE.search(script)
        if not url_match:
            return default_seconds
        intro_text = script[: url_match.start()]

    word_count = len(intro_text.split())
    if word_count == 0:
        return default_seconds

    estimated = (word_count / words_per_minute) * 60.0
    return max(default_seconds, min(estimated, max_seconds))


# --- ffmpeg-native title card generation (#295) ---


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a shell command via subprocess and raise on non-zero exit."""
    return subprocess.run(command, capture_output=True, text=True, check=True)


def _escape_drawtext(text: str) -> str:
    """Escape special characters for use inside an ffmpeg drawtext option value.

    Escapes backslash first (so later-inserted escapes aren't double-escaped),
    then the quote and colon used by drawtext option syntax, the comma that
    separates entries in the filter graph, and the percent sign that triggers
    drawtext text expansion.
    """
    return (
        text.replace("\\", "\\\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace(",", r"\,")
        .replace("%", r"\%")
    )


def _build_intro_ffmpeg_cmd(
    config: IntroConfig,
    output_path: Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build an ffmpeg command that generates an animated intro title card.

    Uses a ``color`` lavfi source with ``drawtext`` overlays (staggered
    ``enable='gte(t,...)'`` cues for the show name and episode title) and
    ``fade`` filters for smooth in/out transitions.

    Args:
        config: Intro configuration.
        output_path: Destination MP4 file path.
        ffmpeg_bin: Path or name of the ffmpeg binary to use.

    Returns:
        Command list suitable for :func:`subprocess.run`.
    """
    duration_sec = config.duration_ms / 1000.0
    fade_out_st = max(0.0, duration_sec - 0.5)

    filters: list[str] = [
        # Show name — main title, appears after 0.3 s
        (
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(config.show_name)}'"
            f":fontsize=96:fontcolor=#58a6ff"
            f":x=(w-text_w)/2:y=(h-text_h)/2-80"
            f":enable='gte(t,0.3)'"
        ),
    ]

    if config.episode_title:
        filters.append(
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(config.episode_title)}'"
            f":fontsize=36:fontcolor=#c9d1d9"
            f":x=(w-text_w)/2:y=(h-text_h)/2+60"
            f":enable='gte(t,0.7)'"
        )

    if config.subtitle:
        filters.append(
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(config.subtitle)}'"
            f":fontsize=24:fontcolor=#8b949e"
            f":x=(w-text_w)/2:y=(h-text_h)/2+110"
            f":enable='gte(t,1.0)'"
        )

    filters += [
        "fade=t=in:st=0:d=0.5",
        f"fade=t=out:st={fade_out_st:.3f}:d=0.5",
    ]

    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=#0d1117:size={config.width}x{config.height}:rate={FPS}",
        "-t",
        f"{duration_sec:.3f}",
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]


def _build_outro_ffmpeg_cmd(
    config: OutroConfig,
    output_path: Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build an ffmpeg command that generates an animated outro title card.

    Shows the show name, website URL, and a Subscribe call-to-action with
    staggered fade-in cues and a fade-out at the end.

    Args:
        config: Outro configuration.
        output_path: Destination MP4 file path.
        ffmpeg_bin: Path or name of the ffmpeg binary to use.

    Returns:
        Command list suitable for :func:`subprocess.run`.
    """
    duration_sec = config.duration_ms / 1000.0
    fade_out_st = max(0.0, duration_sec - 0.8)

    filters: list[str] = [
        # Show name
        (
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(config.show_name)}'"
            f":fontsize=72:fontcolor=#c9d1d9"
            f":x=(w-text_w)/2:y=(h-text_h)/2-100"
            f":enable='gte(t,0.3)'"
        ),
        # Website URL
        (
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(config.url)}'"
            f":fontsize=36:fontcolor=#58a6ff"
            f":x=(w-text_w)/2:y=(h-text_h)/2-30"
            f":enable='gte(t,0.6)'"
        ),
        # Call-to-action
        (
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='Subscribe & Follow'"
            f":fontsize=28:fontcolor=#f0883e"
            f":x=(w-text_w)/2:y=(h-text_h)/2+60"
            f":enable='gte(t,1.0)'"
        ),
        "fade=t=in:st=0:d=0.5",
        f"fade=t=out:st={fade_out_st:.3f}:d=0.8",
    ]

    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=#0d1117:size={config.width}x{config.height}:rate={FPS}",
        "-t",
        f"{duration_sec:.3f}",
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]


def generate_intro_ffmpeg(
    config: IntroConfig | None = None,
    output_dir: Path | None = None,
    ffmpeg_bin: str | None = None,
    runner: Any = None,
) -> ClipResult:
    """Generate an animated 'Claracle Weekly' intro title card using ffmpeg.

    Produces an MP4 with a dark background, the show name as a large gradient
    title, the episode name below it, and smooth fade-in/fade-out.  Uses the
    drawtext-capable ffmpeg binary from :func:`_get_drawtext_ffmpeg` when
    *ffmpeg_bin* is not specified.

    The intro duration can be sized from the script with
    :func:`derive_intro_duration` and passed via ``config.duration_ms``.

    Args:
        config: Intro configuration. Uses defaults (show_name='Claracle Weekly')
            if None.
        output_dir: Directory for the output ``intro.mp4``. Uses a temp dir if
            None.
        ffmpeg_bin: Explicit ffmpeg binary. Auto-detected via
            :func:`_get_drawtext_ffmpeg` if None.
        runner: Command runner for injection in tests. Uses
            :func:`subprocess.run` if None.

    Returns:
        ClipResult pointing to the generated ``intro.mp4``.
    """
    if config is None:
        config = IntroConfig()
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="claracle_intro_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    if ffmpeg_bin is None:
        ffmpeg_bin = _get_drawtext_ffmpeg() or "ffmpeg"

    output_path = output_dir / "intro.mp4"
    cmd = _build_intro_ffmpeg_cmd(config, output_path, ffmpeg_bin)

    run = runner or _default_runner
    run(cmd)
    logger.info("Generated ffmpeg intro title card: %s", output_path)

    return ClipResult(
        path=output_path,
        duration_ms=config.duration_ms,
        width=config.width,
        height=config.height,
    )


def generate_outro_ffmpeg(
    config: OutroConfig | None = None,
    output_dir: Path | None = None,
    ffmpeg_bin: str | None = None,
    runner: Any = None,
) -> ClipResult:
    """Generate an animated 'Claracle Weekly' outro title card using ffmpeg.

    Produces an MP4 with the show name, URL, and a Subscribe call-to-action,
    all with staggered fade-in cues and a fade-out at the end.

    Args:
        config: Outro configuration. Uses defaults if None.
        output_dir: Directory for the output ``outro.mp4``. Uses a temp dir if
            None.
        ffmpeg_bin: Explicit ffmpeg binary. Auto-detected if None.
        runner: Command runner for injection in tests.

    Returns:
        ClipResult pointing to the generated ``outro.mp4``.
    """
    if config is None:
        config = OutroConfig()
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="claracle_outro_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    if ffmpeg_bin is None:
        ffmpeg_bin = _get_drawtext_ffmpeg() or "ffmpeg"

    output_path = output_dir / "outro.mp4"
    cmd = _build_outro_ffmpeg_cmd(config, output_path, ffmpeg_bin)

    run = runner or _default_runner
    run(cmd)
    logger.info("Generated ffmpeg outro title card: %s", output_path)

    return ClipResult(
        path=output_path,
        duration_ms=config.duration_ms,
        width=config.width,
        height=config.height,
    )


# --- End credits sequence (#300) ---


@dataclass
class CreditsEntry:
    """A single line in the end-credits card.

    Rendered as a two-row label/value pair:
    ``label`` is shown in a muted colour above the larger ``value`` text.

    Attributes:
        label: Category heading (e.g. ``"Creator"``).
        value: Credit text (e.g. ``"Juan Manuel Servera (@jmservera)"``).
    """

    label: str
    value: str


# Default credits — overridable via CreditsConfig.entries.
# Music attribution sourced from podcaster/music.py (TRACK_ATTRIBUTION).
_DEFAULT_CREDITS_ENTRIES: list[CreditsEntry] = [
    CreditsEntry("Creator", "Juan Manuel Servera (@jmservera)"),
    CreditsEntry("AI Team", "Squad (Coordinator) \u00b7 Bender (Podcaster)"),
    CreditsEntry("AI Hosts", "Fable & Alloy"),
    CreditsEntry("Music", "Summer Sport by AudioCoffee (CC-BY-SA-3.0)"),
    CreditsEntry("Powered by", "Claracle / SquadScope"),
]

# Expose default list as a public constant so tests and callers can inspect it
DEFAULT_CREDITS_ENTRIES: list[CreditsEntry] = _DEFAULT_CREDITS_ENTRIES

# Credits card layout constants
_CREDITS_HEADING_Y = 120  # show name top margin
_CREDITS_TITLE_Y = 220  # "Credits" label y position
_CREDITS_ENTRIES_Y_START = 320  # first entry's label y position
_CREDITS_ENTRY_HEIGHT = 100  # pixels between consecutive entries
_CREDITS_VALUE_OFFSET = 36  # value y offset below label within one entry

# Default credits duration (8 s sits comfortably in the 5-10 s requirement)
DEFAULT_CREDITS_DURATION_MS = 8_000


@dataclass
class CreditsConfig:
    """Configuration for the end-credits video card.

    All fields have sensible defaults so callers can override only what they
    need.  ``entries`` defaults to :data:`DEFAULT_CREDITS_ENTRIES`; pass an
    explicit list to replace them entirely.

    Attributes:
        show_name: Branding headline at the top of the card.
        title: Section heading text (displayed below the show name).
        entries: Ordered list of :class:`CreditsEntry` items.  Defaults to
            :data:`DEFAULT_CREDITS_ENTRIES` when ``None``.
        duration_ms: Total clip duration in milliseconds (default 8 000 ms).
        width: Output video width in pixels.
        height: Output video height in pixels.
    """

    show_name: str = DEFAULT_SHOW_NAME
    title: str = "Credits"
    entries: list[CreditsEntry] | None = None
    duration_ms: int = DEFAULT_CREDITS_DURATION_MS
    width: int = WIDTH
    height: int = HEIGHT

    def __post_init__(self) -> None:
        if self.entries is None:
            self.entries = list(_DEFAULT_CREDITS_ENTRIES)


def _build_credits_ffmpeg_cmd(
    config: CreditsConfig,
    output_path: Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build an ffmpeg command that generates the end-credits title card.

    Produces a dark-background card consistent with the Claracle Weekly
    intro/outro branding:

    * Show name at top (``fontsize=60``, muted white).
    * ``"Credits"`` (or custom ``config.title``) heading in blue.
    * Each :class:`CreditsEntry` rendered as a staggered two-row label/value
      pair — label in ``#8b949e``, value in ``#c9d1d9``.
    * Smooth fade-in (0.5 s) and fade-out (1.0 s).

    Args:
        config: Credits configuration.
        output_path: Destination MP4 file path.
        ffmpeg_bin: Path or name of the ffmpeg binary to use.

    Returns:
        Command list suitable for :func:`subprocess.run`.
    """
    duration_sec = config.duration_ms / 1000.0
    fade_out_st = max(0.0, duration_sec - 1.0)
    entries = config.entries or []

    filters: list[str] = [
        # Show name — Claracle Weekly branding headline
        (
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(config.show_name)}'"
            f":fontsize=60:fontcolor=#c9d1d9"
            f":x=(w-text_w)/2:y={_CREDITS_HEADING_Y}"
            f":enable='gte(t,0.2)'"
        ),
        # Credits section heading
        (
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(config.title)}'"
            f":fontsize=44:fontcolor=#58a6ff"
            f":x=(w-text_w)/2:y={_CREDITS_TITLE_Y}"
            f":enable='gte(t,0.3)'"
        ),
    ]

    for i, entry in enumerate(entries):
        t_start = 0.5 + i * 0.4
        y_label = _CREDITS_ENTRIES_Y_START + i * _CREDITS_ENTRY_HEIGHT
        y_value = y_label + _CREDITS_VALUE_OFFSET

        # Category label (dim, smaller)
        filters.append(
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(entry.label)}'"
            f":fontsize=22:fontcolor=#8b949e"
            f":x=(w-text_w)/2:y={y_label}"
            f":enable='gte(t,{t_start:.1f})'"
        )
        # Credit value (bright, larger)
        filters.append(
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(entry.value)}'"
            f":fontsize=30:fontcolor=#c9d1d9"
            f":x=(w-text_w)/2:y={y_value}"
            f":enable='gte(t,{t_start:.1f})'"
        )

    filters += [
        "fade=t=in:st=0:d=0.5",
        f"fade=t=out:st={fade_out_st:.3f}:d=1.0",
    ]

    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=#0d1117:size={config.width}x{config.height}:rate={FPS}",
        "-t",
        f"{duration_sec:.3f}",
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]


def generate_credits_ffmpeg(
    config: CreditsConfig | None = None,
    output_dir: Path | None = None,
    ffmpeg_bin: str | None = None,
    runner: Any = None,
) -> ClipResult:
    """Generate the end-credits video card using ffmpeg drawtext.

    Produces an MP4 with Claracle Weekly branding at the top, a "Credits"
    heading, and staggered label/value pairs for creator, AI team, AI hosts,
    music attribution, and powered-by.  Uses the drawtext-capable ffmpeg
    binary from :func:`_get_drawtext_ffmpeg` when *ffmpeg_bin* is not
    specified; falls back to ``"ffmpeg"`` with a warning if none is found.

    All credit lines come from :class:`CreditsConfig`; override ``entries``
    to customise without touching the ffmpeg-building code.

    Args:
        config: Credits configuration.  Uses defaults (including
            :data:`DEFAULT_CREDITS_ENTRIES`) when ``None``.
        output_dir: Directory for the output ``credits.mp4``.  Uses a
            temp dir if ``None``.
        ffmpeg_bin: Explicit ffmpeg binary.  Auto-detected via
            :func:`_get_drawtext_ffmpeg` if ``None``.
        runner: Command runner for injection in tests.  Uses
            :func:`subprocess.run` if ``None``.

    Returns:
        :class:`ClipResult` pointing to the generated ``credits.mp4``.
    """
    if config is None:
        config = CreditsConfig()
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="claracle_credits_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    if ffmpeg_bin is None:
        detected = _get_drawtext_ffmpeg()
        if detected is None:
            logger.warning(
                "No ffmpeg binary with drawtext (libfreetype) found; "
                "credits card will use 'ffmpeg' but may fail without drawtext support."
            )
        ffmpeg_bin = detected or "ffmpeg"

    output_path = output_dir / "credits.mp4"
    cmd = _build_credits_ffmpeg_cmd(config, output_path, ffmpeg_bin)

    run = runner or _default_runner
    run(cmd)
    logger.info("Generated end-credits card: %s", output_path)

    return ClipResult(
        path=output_path,
        duration_ms=config.duration_ms,
        width=config.width,
        height=config.height,
    )
