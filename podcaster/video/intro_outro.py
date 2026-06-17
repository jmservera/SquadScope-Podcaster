"""Generate animated intro/outro video clips using Playwright.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Renders HTML pages with CSS animations in headless Chromium, recording
them as MP4 files for video_compose.py to concatenate.

Closes jmservera/SquadScope-Podcaster#241.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]
    _PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)

# --- Constants ---

WIDTH = 1920
HEIGHT = 1080
INTRO_DURATION_MS = 5000
OUTRO_DURATION_MS = 5000
FPS = 30

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
  <div class="brand">Claracle</div>
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
  <div class="brand">Claracle</div>
  <div class="url">{url}</div>
  <div class="links">
    {links_html}
  </div>
  <div class="cta">Subscribe &amp; Follow</div>
</div>
</body></html>
"""


# --- Data Classes ---


@dataclass
class IntroConfig:
    """Configuration for intro clip generation."""

    episode_title: str = "Untitled Episode"
    subtitle: str = ""
    duration_ms: int = INTRO_DURATION_MS
    width: int = WIDTH
    height: int = HEIGHT


@dataclass
class OutroConfig:
    """Configuration for outro clip generation."""

    url: str = CLARACLE_URL
    links: list[tuple[str, str]] | None = None
    duration_ms: int = OUTRO_DURATION_MS
    width: int = WIDTH
    height: int = HEIGHT

    def __post_init__(self) -> None:
        if self.links is None:
            self.links = list(REPO_LINKS)


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
        episode_title=config.episode_title,
        subtitle=config.subtitle,
    )


def _render_outro_html(config: OutroConfig) -> str:
    """Render the outro HTML template with config values."""
    links_html = "\n    ".join(
        f'<div class="link-item">{name}</div>' for name, _url in (config.links or [])
    )
    return OUTRO_HTML.format(
        width=config.width,
        height=config.height,
        url=config.url,
        links_html=links_html,
    )


def _record_html_to_video(
    html_content: str,
    output_path: Path,
    duration_ms: int,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Path:
    """Render HTML page with Playwright and record to MP4.

    Opens a headless Chromium browser, loads the HTML content,
    waits for animations to complete, and records the viewport.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright is not installed. Install with: pip install playwright"
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
        context.close()
        browser.close()

    # Playwright saves video with auto-generated name; rename to target
    video_files = list(output_path.parent.glob("*.webm"))
    if video_files:
        latest = max(video_files, key=lambda f: f.stat().st_mtime)
        latest.rename(output_path)

    if not output_path.exists():
        raise RuntimeError(
            f"Playwright recording failed: no video file produced at {output_path}"
        )

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
        ClipResult with path to the generated MP4/WebM file.
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
        ClipResult with path to the generated MP4/WebM file.
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
