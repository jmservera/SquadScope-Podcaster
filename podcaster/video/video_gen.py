"""Record GitHub repo pages as WebM video segments using Playwright.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Phase 2: headless Chromium screen-recording with auto-scroll.

Each segment navigates to a GitHub repo URL, waits for the page to load,
then smoothly scrolls the page over the segment duration while Playwright
records the viewport as a WebM file.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
import uuid

import requests

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        sync_playwright,
    )

    _PLAYWRIGHT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    _PLAYWRIGHT_AVAILABLE = False

from podcaster.video.sync_plan import EpisodePlan, VideoSegment

logger = logging.getLogger(__name__)

# --- Constants ---

WIDTH = 1920
HEIGHT = 1080
SCROLL_TICKS_PER_SEC = 4
MAX_SCROLL_VIEWPORT_MULTIPLIER = 2.5
NETWORK_IDLE_TIMEOUT_MS = 10_000
# Brief settle pause after navigation, before scrolling begins, so the page has
# finished painting (fonts, images, lazy content) and the recording does not
# capture the initial layout flash/flicker (issues #353, #355).
PAGE_SETTLE_MS = 1000

# A full-viewport dark hold page painted *before* navigation so the recording's
# first frames are the GitHub-dark background instead of a white flash while the
# real page loads (issue #355).
DARK_HOLD_HTML = (
    "<!DOCTYPE html><html><head><style>"
    "html,body{margin:0;width:100%;height:100%;background:#0d1117;}"
    "</style></head><body></body></html>"
)

# Injected once the page has loaded: neutralise the motion that produces awkward
# flashes during the recorded scroll (issue #355).  Disabling CSS
# animations/transitions stops elements popping in mid-scroll, forcing instant
# (non-smooth) scrolling keeps motion uniform, and hiding the scrollbar removes
# a flickering UI element.
ANTI_FLASH_CSS = (
    "*,*::before,*::after{"
    "animation:none !important;transition:none !important;}"
    "html{scroll-behavior:auto !important;}"
    "::-webkit-scrollbar{display:none !important;}"
)
FALLBACK_BRAND_HTML = """\
<!DOCTYPE html>
<html><head><style>
body {{
  margin: 0; display: flex; align-items: center; justify-content: center;
  width: {width}px; height: {height}px;
  background: #0d1117; color: #c9d1d9; font-family: -apple-system, sans-serif;
}}
.card {{ text-align: center; }}
h1 {{ font-size: 48px; margin-bottom: 16px; }}
p {{ font-size: 24px; color: #8b949e; }}
</style></head><body>
<div class="card">
  <h1>{owner}/{name}</h1>
  <p>Repository unavailable</p>
</div>
</body></html>
"""

# Animated branded background used for generic segments (no repo to record).
GENERIC_BACKGROUND_HTML = """\
<!DOCTYPE html>
<html><head><style>
body {{
  margin: 0; overflow: hidden;
  width: {width}px; height: {height}px;
  display: flex; align-items: center; justify-content: center;
  font-family: -apple-system, sans-serif; color: #c9d1d9;
  background: linear-gradient(-45deg, #0d1117, #161b22, #1f6feb, #161b22);
  background-size: 400% 400%;
  animation: gradient 18s ease infinite;
}}
@keyframes gradient {{
  0% {{ background-position: 0% 50%; }}
  50% {{ background-position: 100% 50%; }}
  100% {{ background-position: 0% 50%; }}
}}
.card {{ text-align: center; }}
h1 {{ font-size: 64px; margin: 0 0 16px; letter-spacing: 1px; }}
p {{ font-size: 28px; color: #8b949e; margin: 0; }}
</style></head><body>
<div class="card">
  <h1>{title}</h1>
  <p>{subtitle}</p>
</div>
</body></html>
"""

# Common GitHub overlay selectors to dismiss
_OVERLAY_SELECTORS = [
    "[data-testid='cookie-banner'] button",
    ".js-signup-prompt button[aria-label='Close']",
    ".js-notice-dismiss",
]


@dataclass
class RecordedSegment:
    """Result of recording a single video segment."""

    segment: VideoSegment
    video_path: Path
    is_fallback: bool = False
    has_pages: bool = False


@dataclass
class RecordingResult:
    """Result of recording all segments for an episode."""

    recorded: list[RecordedSegment] = field(default_factory=list)
    output_dir: Path = field(default_factory=lambda: Path("."))


def _check_gh_pages(owner: str, name: str, timeout: float = 5.0) -> bool:
    """Detect whether the repo has a GitHub Pages site via HEAD request."""
    url = f"https://{owner}.github.io/{name}/"
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        return resp.status_code == 200
    except Exception:
        return False


def _check_repo_accessible(url: str, timeout: float = 5.0) -> bool:
    """Check if a GitHub repo URL returns a non-404 status."""
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        return resp.status_code != 404
    except Exception:
        # Network errors — assume accessible and let Playwright handle it
        return True


def _dismiss_overlays(page: Page) -> None:
    """Try to dismiss common GitHub overlays/banners."""
    for selector in _OVERLAY_SELECTORS:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                logger.debug("Dismissed overlay: %s", selector)
        except Exception:
            pass


def _prepare_page_for_recording(page: Page) -> None:
    """Reduce flashes/flicker before the recorded scroll begins (issue #355).

    Injects CSS that disables animations/transitions, forces instant scrolling
    and hides the scrollbar, then waits for web fonts to finish loading so text
    does not visibly re-flow once recording starts.  Best-effort: any failure is
    swallowed so recording proceeds with the page as-is.
    """
    try:
        page.add_style_tag(content=ANTI_FLASH_CSS)
    except Exception:
        pass
    try:
        page.evaluate(
            "() => (document.fonts && document.fonts.ready) "
            "? document.fonts.ready : Promise.resolve()"
        )
    except Exception:
        pass


def _smooth_scroll(page: Page, duration_seconds: float) -> None:
    """Auto-scroll the page smoothly over the given duration.

    Scrolls at SCROLL_TICKS_PER_SEC, capping total scroll distance to
    MAX_SCROLL_VIEWPORT_MULTIPLIER × viewport height.
    """
    total_ticks = int(duration_seconds * SCROLL_TICKS_PER_SEC)
    if total_ticks <= 0:
        # Duration is positive but too short for a full tick — wait it out
        if duration_seconds > 0:
            page.wait_for_timeout(int(duration_seconds * 1000))
        return

    tick_interval_ms = int(1000 / SCROLL_TICKS_PER_SEC)

    viewport_height = page.viewport_size["height"] if page.viewport_size else HEIGHT
    max_scroll = int(viewport_height * MAX_SCROLL_VIEWPORT_MULTIPLIER)

    # Get the actual scrollable height
    scroll_height = page.evaluate("document.documentElement.scrollHeight")
    page_scroll_distance = max(0, scroll_height - viewport_height)
    effective_scroll = min(page_scroll_distance, max_scroll)

    if effective_scroll <= 0:
        # Page is not scrollable — just wait out the duration
        page.wait_for_timeout(int(duration_seconds * 1000))
        return

    scroll_per_tick = effective_scroll / total_ticks

    for _ in range(total_ticks):
        page.evaluate(f"window.scrollBy(0, {scroll_per_tick})")
        page.wait_for_timeout(tick_interval_ms)

    # Wait out any remaining fractional duration not covered by ticks
    elapsed_ms = total_ticks * tick_interval_ms
    requested_ms = int(duration_seconds * 1000)
    remainder_ms = requested_ms - elapsed_ms
    if remainder_ms > 0:
        page.wait_for_timeout(remainder_ms)


def _render_fallback_page(
    page: Page, owner: str, name: str, duration_seconds: float
) -> None:
    """Show a branded fallback screen for unavailable repos."""
    html = FALLBACK_BRAND_HTML.format(
        width=WIDTH, height=HEIGHT, owner=owner, name=name
    )
    page.set_content(html)
    page.wait_for_timeout(int(duration_seconds * 1000))


GENERIC_BACKGROUND_TITLE = "SquadScope"
GENERIC_BACKGROUND_SUBTITLE = "Open Source Highlights"


def _render_generic_background(page: Page, duration_seconds: float) -> None:
    """Show the animated branded background for a generic (no-repo) segment."""
    html = GENERIC_BACKGROUND_HTML.format(
        width=WIDTH,
        height=HEIGHT,
        title=GENERIC_BACKGROUND_TITLE,
        subtitle=GENERIC_BACKGROUND_SUBTITLE,
    )
    page.set_content(html)
    page.wait_for_timeout(int(duration_seconds * 1000))


def _record_generic_segment(
    browser: Browser,
    segment: VideoSegment,
    output_dir: Path,
) -> RecordedSegment:
    """Record a generic background segment (no repo).

    When the segment has a ``source_url`` (e.g. the article's weekly page),
    that page is navigated to and scrolled like a regular repo recording;
    otherwise the static branded background animation is shown.
    """
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
        source_url = segment.source_url
        if source_url:
            try:
                try:
                    page.set_content(DARK_HOLD_HTML)
                except Exception:
                    pass
                page.goto(
                    source_url,
                    wait_until="networkidle",
                    timeout=NETWORK_IDLE_TIMEOUT_MS,
                )
                page.wait_for_timeout(PAGE_SETTLE_MS)
                _dismiss_overlays(page)
                _prepare_page_for_recording(page)
                _smooth_scroll(page, segment.duration_seconds)
            except Exception:
                logger.exception(
                    "Error recording generic source %s — using background",
                    source_url,
                )
                _render_generic_background(page, segment.duration_seconds)
        else:
            _render_generic_background(page, segment.duration_seconds)
    finally:
        video = page.video
        context.close()

    if video is None:
        raise RuntimeError("No video object for generic background recording")

    src_path = Path(video.path())
    unique_suffix = uuid.uuid4().hex[:8]
    dest_path = output_dir / f"generic_{unique_suffix}.webm"
    if src_path.exists():
        src_path.rename(dest_path)
    else:
        raise FileNotFoundError(f"Playwright video file not found at {src_path}")

    return RecordedSegment(
        segment=segment,
        video_path=dest_path,
        is_fallback=False,
        has_pages=False,
    )


def _record_segment(
    browser: Browser,
    segment: VideoSegment,
    output_dir: Path,
    check_accessibility: bool = True,
) -> RecordedSegment:
    """Record a single video segment for a repo.

    Creates a fresh browser context with video recording, navigates to the
    repo URL, scrolls, and closes the context to finalize the video file.
    """
    if segment.is_generic:
        return _record_generic_segment(browser, segment, output_dir)

    repo = segment.repo
    video_dir = output_dir / "raw"
    video_dir.mkdir(parents=True, exist_ok=True)

    is_fallback = False
    has_pages = False

    if check_accessibility and not _check_repo_accessible(repo.url):
        is_fallback = True
        logger.warning("Repo %s is not accessible, using fallback", repo.url)

    if not is_fallback:
        has_pages = _check_gh_pages(repo.owner, repo.name)
        if has_pages:
            logger.info("Repo %s has GitHub Pages", repo.url)

    context: BrowserContext = browser.new_context(
        record_video_dir=str(video_dir),
        record_video_size={"width": WIDTH, "height": HEIGHT},
        viewport={"width": WIDTH, "height": HEIGHT},
        color_scheme="dark",
    )

    page: Page = context.new_page()

    try:
        if is_fallback:
            _render_fallback_page(
                page, repo.owner, repo.name, segment.duration_seconds
            )
        else:
            # Paint a dark hold frame before navigating so the recording's
            # opening frames are GitHub-dark rather than a white flash while
            # the real page loads (issue #355).
            try:
                page.set_content(DARK_HOLD_HTML)
            except Exception:
                pass
            response = page.goto(repo.url, wait_until="networkidle",
                                 timeout=NETWORK_IDLE_TIMEOUT_MS)
            if response is not None and response.status == 404:
                logger.warning("Got 404 for %s — using fallback", repo.url)
                is_fallback = True
                _render_fallback_page(
                    page, repo.owner, repo.name, segment.duration_seconds
                )
            else:
                # Wait for the page to fully settle (load + paint) before
                # recording motion, avoiding the initial content flash.
                try:
                    page.wait_for_load_state(
                        "networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS
                    )
                except Exception:
                    pass
                page.wait_for_timeout(PAGE_SETTLE_MS)
                _dismiss_overlays(page)
                _prepare_page_for_recording(page)
                _smooth_scroll(page, segment.duration_seconds)
    except Exception:
        logger.exception("Error recording %s — using fallback", repo.url)
        is_fallback = True
        _render_fallback_page(
            page, repo.owner, repo.name, segment.duration_seconds
        )

    # Close context first to finalize the recorded video file
    video = page.video
    context.close()
    if video is None:
        raise RuntimeError(f"No video object for page recording of {repo.url}")

    # Resolve path after close (Playwright finalizes on close)
    video_path_str = video.path()
    src_path = Path(video_path_str)

    # Use a UUID suffix to avoid collisions for repeated repos
    unique_suffix = uuid.uuid4().hex[:8]
    dest_name = f"{repo.owner}_{repo.name}_{unique_suffix}.webm"
    dest_path = output_dir / dest_name
    if src_path.exists():
        src_path.rename(dest_path)
    else:
        raise FileNotFoundError(
            f"Playwright video file not found at {src_path}"
        )

    return RecordedSegment(
        segment=segment,
        video_path=dest_path,
        is_fallback=is_fallback,
        has_pages=has_pages,
    )


def record_episode(
    plan: EpisodePlan,
    output_dir: Path | str | None = None,
    headless: bool = True,
    check_accessibility: bool = True,
) -> RecordingResult:
    """Record all video segments for an episode plan.

    Args:
        plan: The episode plan with timed segments.
        output_dir: Directory for output video files. Uses a temp dir if None.
        headless: Run Chromium in headless mode (default True).
        check_accessibility: Pre-check repo URLs for 404 (default True).

    Returns:
        RecordingResult with paths to all recorded WebM files.
    """
    if not plan.segments:
        raise ValueError("Episode plan has no segments to record")

    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright is not installed. Install it with: "
            "pip install 'podcaster[video]' && playwright install chromium"
        )

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="video_gen_"))
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = RecordingResult(output_dir=output_dir)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            for segment in plan.segments:
                logger.info(
                    "Recording segment: %s (%.1fs)",
                    segment.label,
                    segment.duration_seconds,
                )
                recorded = _record_segment(
                    browser, segment, output_dir, check_accessibility
                )
                result.recorded.append(recorded)
                logger.info(
                    "Saved: %s (fallback=%s, pages=%s)",
                    recorded.video_path.name,
                    recorded.is_fallback,
                    recorded.has_pages,
                )
        finally:
            browser.close()

    return result
