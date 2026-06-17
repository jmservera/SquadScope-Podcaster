"""Generate animated intro/outro video clips using Playwright.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Renders branded HTML/CSS animations in headless Chromium and captures
them as WebM files via Playwright's built-in video recording.

Tool selection rationale (issue #241):
  Evaluated hyperframes, Remotion, and custom Playwright recording.
  Playwright is already a dependency for repo screen-capture (video_gen.py),
  runs headless in ACA containers, and needs no extra runtime (Node/npm).
  CSS @keyframes provide smooth, GPU-accelerated animations without JS.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

try:
    from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    _PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)

WIDTH = 1920
HEIGHT = 1080

# Default durations (seconds)
DEFAULT_INTRO_DURATION = 5.0
DEFAULT_OUTRO_DURATION = 6.0

# Claracle brand palette
_BRAND_BG = "#0d1117"
_BRAND_PRIMARY = "#58a6ff"
_BRAND_TEXT = "#c9d1d9"
_BRAND_MUTED = "#8b949e"
_BRAND_ACCENT = "#f78166"


@dataclass(frozen=True)
class IntroConfig:
    """Configuration for the intro clip."""

    podcast_name: str = "Claracle"
    episode_title: str = ""
    episode_number: int | None = None
    episode_date: str = ""
    duration_seconds: float = DEFAULT_INTRO_DURATION


@dataclass(frozen=True)
class OutroConfig:
    """Configuration for the outro clip."""

    podcast_name: str = "Claracle"
    website_url: str = "www.claracle.com"
    repo_urls: tuple[str, ...] = ()
    subscribe_cta: str = "Subscribe for weekly updates"
    duration_seconds: float = DEFAULT_OUTRO_DURATION


@dataclass
class IntroOutroResult:
    """Paths to the generated intro and/or outro video files."""

    intro_path: Path | None = None
    outro_path: Path | None = None
    output_dir: Path = field(default_factory=lambda: Path("."))


def _intro_html(config: IntroConfig) -> str:
    """Generate the animated intro HTML."""
    subtitle_parts: list[str] = []
    if config.episode_number is not None:
        subtitle_parts.append(f"Episode {config.episode_number}")
    if config.episode_date:
        subtitle_parts.append(config.episode_date)
    subtitle = " &middot; ".join(subtitle_parts) if subtitle_parts else ""

    # Animation timing: total duration minus 0.5s buffer
    anim_dur = max(config.duration_seconds - 0.5, 1.0)
    # Fade-in over first 20%, hold, fade-out last 15%
    fade_in_pct = 20
    fade_out_start_pct = 85

    return f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  width: {WIDTH}px; height: {HEIGHT}px;
  background: {_BRAND_BG};
  display: flex; align-items: center; justify-content: center;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  overflow: hidden;
}}
.container {{
  text-align: center;
  animation: fade-cycle {anim_dur}s ease-in-out forwards;
}}
@keyframes fade-cycle {{
  0%   {{ opacity: 0; transform: translateY(30px); }}
  {fade_in_pct}%  {{ opacity: 1; transform: translateY(0); }}
  {fade_out_start_pct}% {{ opacity: 1; transform: translateY(0); }}
  100% {{ opacity: 0; transform: translateY(-20px); }}
}}
.logo-line {{
  display: flex; align-items: center; justify-content: center; gap: 18px;
  margin-bottom: 32px;
}}
.logo-icon {{
  width: 72px; height: 72px; border-radius: 16px;
  background: linear-gradient(135deg, {_BRAND_PRIMARY}, {_BRAND_ACCENT});
  display: flex; align-items: center; justify-content: center;
  animation: pulse {anim_dur * 0.4:.1f}s ease-in-out 0.3s infinite alternate;
}}
@keyframes pulse {{
  0%   {{ transform: scale(1); }}
  100% {{ transform: scale(1.06); }}
}}
.logo-icon svg {{ width: 40px; height: 40px; fill: white; }}
h1 {{
  font-size: 80px; font-weight: 700; letter-spacing: -2px;
  color: {_BRAND_PRIMARY};
}}
.episode-title {{
  font-size: 36px; color: {_BRAND_TEXT}; margin-top: 24px;
  max-width: 1400px;
  animation: slide-up 0.8s ease-out 0.5s both;
}}
@keyframes slide-up {{
  0%   {{ opacity: 0; transform: translateY(20px); }}
  100% {{ opacity: 1; transform: translateY(0); }}
}}
.subtitle {{
  font-size: 22px; color: {_BRAND_MUTED}; margin-top: 16px;
  animation: slide-up 0.8s ease-out 0.8s both;
}}
.accent-bar {{
  width: 120px; height: 4px; margin: 28px auto 0;
  background: linear-gradient(90deg, {_BRAND_PRIMARY}, {_BRAND_ACCENT});
  border-radius: 2px;
  animation: bar-grow 1s ease-out 0.6s both;
}}
@keyframes bar-grow {{
  0%   {{ width: 0; opacity: 0; }}
  100% {{ width: 120px; opacity: 1; }}
}}
</style></head><body>
<div class="container">
  <div class="logo-line">
    <div class="logo-icon">
      <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10
        10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93
        0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39
        -1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9
        2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>
    </div>
    <h1>{config.podcast_name}</h1>
  </div>
  {"<p class='episode-title'>" + config.episode_title + "</p>" if config.episode_title else ""}
  {"<p class='subtitle'>" + subtitle + "</p>" if subtitle else ""}
  <div class="accent-bar"></div>
</div>
</body></html>"""


def _outro_html(config: OutroConfig) -> str:
    """Generate the animated outro HTML."""
    anim_dur = max(config.duration_seconds - 0.5, 1.0)
    fade_in_pct = 15
    fade_out_start_pct = 85

    # Build repo links list (show max 4)
    repo_items = ""
    for url in config.repo_urls[:4]:
        # Extract owner/name from URL
        parts = url.rstrip("/").split("/")
        if len(parts) >= 2:
            label = f"{parts[-2]}/{parts[-1]}"
        else:
            label = url
        repo_items += f'<li><span class="repo-icon">&#128193;</span> {label}</li>\n'

    return f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  width: {WIDTH}px; height: {HEIGHT}px;
  background: {_BRAND_BG};
  display: flex; align-items: center; justify-content: center;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  overflow: hidden;
}}
.container {{
  text-align: center;
  animation: fade-cycle {anim_dur}s ease-in-out forwards;
}}
@keyframes fade-cycle {{
  0%   {{ opacity: 0; transform: scale(0.95); }}
  {fade_in_pct}%  {{ opacity: 1; transform: scale(1); }}
  {fade_out_start_pct}% {{ opacity: 1; transform: scale(1); }}
  100% {{ opacity: 0; transform: scale(0.98); }}
}}
h1 {{
  font-size: 64px; font-weight: 700; color: {_BRAND_PRIMARY};
  letter-spacing: -1px; margin-bottom: 20px;
}}
.website {{
  font-size: 36px; color: {_BRAND_ACCENT}; font-weight: 600;
  margin-bottom: 28px;
  animation: slide-up 0.7s ease-out 0.4s both;
}}
@keyframes slide-up {{
  0%   {{ opacity: 0; transform: translateY(16px); }}
  100% {{ opacity: 1; transform: translateY(0); }}
}}
.cta {{
  font-size: 24px; color: {_BRAND_TEXT}; margin-bottom: 32px;
  animation: slide-up 0.7s ease-out 0.6s both;
}}
.repos {{
  list-style: none; display: flex; flex-wrap: wrap;
  justify-content: center; gap: 16px; margin-top: 12px;
  animation: slide-up 0.7s ease-out 0.9s both;
}}
.repos li {{
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 10px 20px; font-size: 18px; color: {_BRAND_TEXT};
  display: flex; align-items: center; gap: 8px;
}}
.repo-icon {{ font-size: 20px; }}
.accent-bar {{
  width: 100px; height: 4px; margin: 32px auto 0;
  background: linear-gradient(90deg, {_BRAND_PRIMARY}, {_BRAND_ACCENT});
  border-radius: 2px;
  animation: bar-grow 0.8s ease-out 0.5s both;
}}
@keyframes bar-grow {{
  0%   {{ width: 0; opacity: 0; }}
  100% {{ width: 100px; opacity: 1; }}
}}
</style></head><body>
<div class="container">
  <h1>{config.podcast_name}</h1>
  <p class="website">{config.website_url}</p>
  <p class="cta">{config.subscribe_cta}</p>
  {"<ul class='repos'>" + repo_items + "</ul>" if repo_items else ""}
  <div class="accent-bar"></div>
</div>
</body></html>"""


def _record_html_clip(
    browser: Browser,
    html: str,
    duration_seconds: float,
    output_dir: Path,
    label: str,
) -> Path:
    """Render HTML in Playwright and record to a WebM file."""
    video_dir = output_dir / "raw"
    video_dir.mkdir(parents=True, exist_ok=True)

    context: BrowserContext = browser.new_context(
        record_video_dir=str(video_dir),
        record_video_size={"width": WIDTH, "height": HEIGHT},
        viewport={"width": WIDTH, "height": HEIGHT},
        color_scheme="dark",
    )
    page: Page = context.new_page()

    try:
        page.set_content(html)
        page.wait_for_timeout(int(duration_seconds * 1000))
    finally:
        video = page.video
        context.close()

    if video is None:
        raise RuntimeError(f"No video object for {label} recording")

    src_path = Path(video.path())
    unique_suffix = uuid.uuid4().hex[:8]
    dest_path = output_dir / f"{label}_{unique_suffix}.webm"
    if src_path.exists():
        src_path.rename(dest_path)
    else:
        raise FileNotFoundError(
            f"Playwright video file not found at {src_path}"
        )

    return dest_path


def generate_intro(
    config: IntroConfig | None = None,
    output_dir: Path | str | None = None,
    headless: bool = True,
    browser: Browser | None = None,
) -> Path:
    """Generate an animated intro video clip.

    Args:
        config: Intro configuration. Uses defaults if None.
        output_dir: Directory for output files. Uses a temp dir if None.
        headless: Run Chromium in headless mode (default True).
        browser: Optional pre-existing Playwright browser instance.
            If provided, it will be used (and NOT closed by this function).

    Returns:
        Path to the generated WebM intro clip.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright is not installed. Install it with: "
            "pip install 'podcaster[video]' && playwright install chromium"
        )

    if config is None:
        config = IntroConfig()

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="intro_"))
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    html = _intro_html(config)
    own_browser = browser is None

    if own_browser:
        pw_ctx = sync_playwright().start()
        browser = pw_ctx.chromium.launch(headless=headless)
    else:
        pw_ctx = None

    try:
        return _record_html_clip(
            browser, html, config.duration_seconds, output_dir, "intro"
        )
    finally:
        if own_browser:
            browser.close()
            if pw_ctx is not None:
                pw_ctx.stop()


def generate_outro(
    config: OutroConfig | None = None,
    output_dir: Path | str | None = None,
    headless: bool = True,
    browser: Browser | None = None,
) -> Path:
    """Generate an animated outro video clip.

    Args:
        config: Outro configuration. Uses defaults if None.
        output_dir: Directory for output files. Uses a temp dir if None.
        headless: Run Chromium in headless mode (default True).
        browser: Optional pre-existing Playwright browser instance.

    Returns:
        Path to the generated WebM outro clip.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright is not installed. Install it with: "
            "pip install 'podcaster[video]' && playwright install chromium"
        )

    if config is None:
        config = OutroConfig()

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="outro_"))
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    html = _outro_html(config)
    own_browser = browser is None

    if own_browser:
        pw_ctx = sync_playwright().start()
        browser = pw_ctx.chromium.launch(headless=headless)
    else:
        pw_ctx = None

    try:
        return _record_html_clip(
            browser, html, config.duration_seconds, output_dir, "outro"
        )
    finally:
        if own_browser:
            browser.close()
            if pw_ctx is not None:
                pw_ctx.stop()


def generate_intro_outro(
    intro_config: IntroConfig | None = None,
    outro_config: OutroConfig | None = None,
    output_dir: Path | str | None = None,
    headless: bool = True,
) -> IntroOutroResult:
    """Generate both intro and outro clips, sharing one browser instance.

    Args:
        intro_config: Intro configuration (None to skip intro).
        outro_config: Outro configuration (None to skip outro).
        output_dir: Directory for output files.
        headless: Run Chromium in headless mode.

    Returns:
        IntroOutroResult with paths to the generated clips.

    Raises:
        ValueError: If both configs are None.
    """
    if intro_config is None and outro_config is None:
        raise ValueError("At least one of intro_config or outro_config must be provided")

    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright is not installed. Install it with: "
            "pip install 'podcaster[video]' && playwright install chromium"
        )

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="intro_outro_"))
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = IntroOutroResult(output_dir=output_dir)

    pw_ctx = sync_playwright().start()
    browser = pw_ctx.chromium.launch(headless=headless)
    try:
        if intro_config is not None:
            result.intro_path = generate_intro(
                config=intro_config, output_dir=output_dir,
                headless=headless, browser=browser,
            )
            logger.info("Generated intro: %s", result.intro_path)

        if outro_config is not None:
            result.outro_path = generate_outro(
                config=outro_config, output_dir=output_dir,
                headless=headless, browser=browser,
            )
            logger.info("Generated outro: %s", result.outro_path)
    finally:
        browser.close()
        pw_ctx.stop()

    return result
