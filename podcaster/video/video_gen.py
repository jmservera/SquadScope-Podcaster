"""Record GitHub repo pages as WebM video segments using Playwright.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Phase 2: headless Chromium screen-recording with auto-scroll.

Each segment navigates to a GitHub repo URL, waits for the page to load,
then smoothly scrolls the page over the segment duration while Playwright
records the viewport as a WebM file.
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
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

from podcaster.video.sync_plan import (
    EpisodePlan,
    RepoReference,
    VideoSegment,
    fetch_repos_from_article,
)

logger = logging.getLogger(__name__)

# --- Constants ---

WIDTH = 1920
HEIGHT = 1080
# Playwright records the viewport at ~25fps.  Scrolling at a coarse rate (the
# previous 4 ticks/sec) moved the page in large ~250ms jumps, so the recorder
# captured visible judder and motion-blurred, hard-to-read text.  Ticking faster
# than the capture rate means each recorded frame reflects a small, fresh scroll
# offset, producing smooth motion with crisp text (issue #359).
SCROLL_TICKS_PER_SEC = 30
MAX_SCROLL_VIEWPORT_MULTIPLIER = 2.5

# Chromium flags that keep the compositor and timers running at full rate while
# headless.  Without these, Chromium throttles background/occluded renderers and
# timers, which drops recorded frames and makes scrolling stutter (issue #359).
RECORDING_CHROMIUM_ARGS = [
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]
NETWORK_IDLE_TIMEOUT_MS = 10_000
# Timeout for navigating to a repo's external website (issue #360).  Kept
# shorter than the GitHub timeout so a slow/down website falls back quickly to
# the already-rendered GitHub page.
WEBSITE_NAV_TIMEOUT_MS = 8_000

# When a repo navigation fails (timeout / HTTP error), retry with an incremental
# backoff (issue #381): wait 1 s before the first retry, 3 s before the second,
# and 5 s before the third.  Spacing the retries out gives a transiently slow or
# rate-limited host more time to recover than a single fixed delay would.
REPO_RETRY_BACKOFF_SECONDS = (1.0, 3.0, 5.0)

# Deprecated alias kept for external callers (issue #378).  The previous fixed
# retry delay was 4 s; it has been intentionally replaced by the incremental
# backoff above, so this now resolves to the *first* backoff delay (1 s) rather
# than the old 4 s value.  Prefer ``REPO_RETRY_BACKOFF_SECONDS`` directly.
REPO_RETRY_DELAY_SECONDS = REPO_RETRY_BACKOFF_SECONDS[0]

# JavaScript that extracts the repo's website/homepage URL from the GitHub
# "About" sidebar (issue #360).  GitHub renders the homepage link as an anchor
# immediately following the link octicon:
#   <svg class="octicon octicon-link ...">…</svg>
#   <span ...><a role="link" href="https://example.com">example.com</a></span>
# We also fall back to any external (non-github.com) ``role="link"`` anchor in
# the header/sidebar.  Returns the absolute URL string or null.
_WEBSITE_URL_JS = """
() => {
  const isExternal = (a) => {
    if (!a || !a.href) return false;
    try {
      const u = new URL(a.href);
      if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
      const host = u.hostname.toLowerCase();
      return host !== 'github.com' && !host.endsWith('.github.com');
    } catch (e) {
      return false;
    }
  };
  const icon = document.querySelector('svg.octicon-link');
  if (icon) {
    let sib = icon.nextElementSibling;
    while (sib) {
      const a = sib.matches && sib.matches('a[href]')
        ? sib
        : (sib.querySelector ? sib.querySelector('a[href]') : null);
      if (isExternal(a)) return a.href;
      sib = sib.nextElementSibling;
    }
  }
  const candidates = document.querySelectorAll(
    'a[role="link"][href^="http"], a.text-bold[href^="http"]'
  );
  for (const a of candidates) {
    if (isExternal(a)) return a.href;
  }
  return null;
}
"""
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

# Minimum natural width AND height (px) for an image to receive the zoom effect.
# Small icons/avatars stay still; only genuinely large images get emphasis.
IMAGE_ZOOM_MIN_SIZE_PX = 200

# A subtle Ken Burns / zoom-in effect applied to large images during recording
# (issue #362) to give the video visual dynamics.  ANTI_FLASH_CSS disables all
# animations via the universal selector with !important, so this rule must also
# use !important *and* a higher-specificity element+class selector
# (`img.ss-zoom-target`) to win the cascade and keep animating.  Only images
# tagged by _IMAGE_ZOOM_JS receive the class, so pages without large images are
# unaffected.  `alternate` keeps the motion smooth (zoom in then back) without a
# hard jump that would cause flickering.
ZOOM_IMAGE_CSS = (
    "@keyframes ss-image-zoom{"
    "from{transform:scale(1);}to{transform:scale(1.05);}}"
    "img.ss-zoom-target{"
    "animation:ss-image-zoom 2.5s ease-in-out infinite alternate !important;"
    "transform-origin:center center !important;will-change:transform;}"
)

# Flags large images (both natural dimensions above the threshold) with the
# ss-zoom-target class so ZOOM_IMAGE_CSS applies only to them.  Returns the
# number of images tagged.
_IMAGE_ZOOM_JS = """
(minSize) => {
  const imgs = Array.from(document.images || []);
  let count = 0;
  for (const img of imgs) {
    const w = img.naturalWidth || 0;
    const h = img.naturalHeight || 0;
    if (w === 0) {
      continue;
    }
    if (w > minSize && h > minSize) {
      img.classList.add('ss-zoom-target');
      count += 1;
    }
  }
  return count;
}
"""
FALLBACK_BRAND_HTML = """\
<!DOCTYPE html>
<html><head><style>
body {{
  margin: 0; display: flex; align-items: center; justify-content: center;
  width: {width}px; height: {height}px;
  background: #0d1117; color: #c9d1d9; font-family: -apple-system, sans-serif;
}}
.card {{ text-align: center; padding: 0 64px; }}
.logo {{ margin-bottom: 28px; }}
.logo svg {{ fill: #c9d1d9; }}
h1 {{ font-size: 52px; margin: 0 0 24px; font-weight: 600; }}
.url {{
  display: inline-block; font-size: 34px; color: #58a6ff;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: #161b22; border: 1px solid #30363d; border-radius: 12px;
  padding: 18px 32px; letter-spacing: 0.5px;
}}
</style></head><body>
<div class="card">
  <div class="logo">
    <svg height="72" viewBox="0 0 16 16" width="72" aria-hidden="true">
      <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38\
 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15\
 .08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82\
-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07\
-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49\
 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z"></path>
    </svg>
  </div>
  <h1>{owner}/{name}</h1>
  <div class="url">{url}</div>
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
    website_url: str | None = None
    # Which recovery path produced a usable recording (issues #378, #386):
    # "direct" (first attempt), "retry" (second attempt after a delay),
    # "article" (URL corrected from the source article), "website" (the repo's
    # GitHub Pages site, recorded when the repo page 404s/needs login), or
    # "fallback" (all recovery paths failed → clean URL card).
    recovery_path: str = "direct"


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


# GitHub sends unauthenticated requests for private / login-required repos to a
# login (or session) page that returns HTTP 200, so a plain status check treats
# it as "accessible".  Detecting the redirect target lets us distinguish a
# login wall from a genuine 404 and from a slow-loading repo (issue #386).
_LOGIN_REDIRECT_RE = re.compile(r"github\.com/(?:login|session)(?:[/?]|$)", re.I)


def _is_login_redirect(url: str | None) -> bool:
    """Return True when *url* is a GitHub login/session redirect (issue #386).

    Used after navigation to detect repos that are private or otherwise require
    authentication: GitHub redirects these to ``github.com/login?return_to=…``
    (HTTP 200), which a status-only check would mistake for a healthy page.
    """
    if not url:
        return False
    if not isinstance(url, str):
        return False
    return bool(_LOGIN_REDIRECT_RE.search(url))


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
    _apply_image_zoom(page)


def _apply_image_zoom(page: Page) -> int:
    """Apply a subtle zoom (Ken Burns) effect to large images (issue #362).

    Detects ``<img>`` elements whose natural width *and* height both exceed
    ``IMAGE_ZOOM_MIN_SIZE_PX``, tags them, then injects keyframe CSS that scales
    them 1.0 → 1.05 on a smooth loop.  The CSS deliberately overrides the
    universal ``animation:none`` rule from ANTI_FLASH_CSS via a higher
    specificity selector plus ``!important``.  Best-effort: any failure is
    swallowed and no style is injected.  Returns the number of images affected
    (0 if none were found or an error occurred).
    """
    try:
        count = page.evaluate(_IMAGE_ZOOM_JS, IMAGE_ZOOM_MIN_SIZE_PX)
    except Exception:
        return 0
    if not count:
        return 0
    try:
        page.add_style_tag(content=ZOOM_IMAGE_CSS)
    except Exception:
        return 0
    logger.debug("Applied zoom effect to %d large image(s)", count)
    return int(count)


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


def _extract_website_url(page: Page) -> str | None:
    """Extract the repo's external website URL from the GitHub page (issue #360).

    Looks for the homepage link in the repo's "About" sidebar/header.  Returns
    the absolute URL when an external (non-github.com) http(s) link is found,
    otherwise ``None``.  Best-effort: any failure returns ``None`` so recording
    falls back to the GitHub page.
    """
    try:
        url = page.evaluate(_WEBSITE_URL_JS)
    except Exception:
        return None
    if isinstance(url, str) and url.strip():
        return url.strip()
    return None


def _navigate_to_website(page: Page, url: str) -> bool:
    """Navigate to the repo's external website (issue #360).

    Uses a shorter timeout than GitHub so a slow/down site fails fast.  Returns
    True if the website loaded successfully (HTTP < 400), False otherwise so the
    caller can fall back to the GitHub page.
    """
    try:
        response = page.goto(
            url,
            wait_until="networkidle",
            timeout=WEBSITE_NAV_TIMEOUT_MS,
        )
    except Exception:
        logger.warning("Failed to load website %s — falling back to GitHub", url)
        return False
    if response is not None and response.status >= 400:
        logger.warning(
            "Website %s returned %s — falling back to GitHub",
            url,
            response.status,
        )
        return False
    logger.info("Recording website %s instead of GitHub page", url)
    return True


def _render_url_card(
    page: Page, owner: str, name: str, duration_seconds: float
) -> None:
    """Show a clean URL card for a repo we couldn't record (issue #386).

    Replaces the old "Repository unavailable" screen: instead of telling the
    viewer something is broken, we present the repository identity and its URL
    prominently so the segment still reads as intentional, branded content.
    """
    url = f"github.com/{owner}/{name}"
    html = FALLBACK_BRAND_HTML.format(
        width=WIDTH, height=HEIGHT, owner=owner, name=name, url=url
    )
    page.set_content(html)
    page.wait_for_timeout(int(duration_seconds * 1000))


# Backwards-compatible alias: earlier code/tests referenced the screen shown
# when a repo can't be recorded as the "fallback page".  It now renders the
# clean URL card (issue #386).
_render_fallback_page = _render_url_card


# Valid GitHub owner/repo path segments contain only these characters.
_VALID_REPO_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _looks_malformed_repo_url(url: str) -> bool:
    """Heuristically detect a corrupted/malformed GitHub repo URL (issue #378).

    A URL is considered malformed when it is empty, uses a non-http(s) scheme,
    points at a non-GitHub host (when a GitHub repo is expected), is missing the
    ``owner/name`` path segments, carries percent-encoding in the repo path, or
    contains characters invalid for GitHub owner/repo names.
    """
    if not url:
        return True
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    host = (parsed.hostname or "").lower()
    if host != "github.com":
        # Non-GitHub domain when expecting a GitHub repo.
        return True
    if "%" in parsed.path:
        # Percent-encoding has no place in a plain owner/repo path.
        return True
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        # Missing path segments (e.g. truncated to just the owner).
        return True
    owner, name = parts[0], parts[1]
    if not _VALID_REPO_SEGMENT_RE.match(owner) or not _VALID_REPO_SEGMENT_RE.match(name):
        return True
    return False


def _try_navigate_repo(page: Page, url: str) -> bool:
    """Attempt a single navigation to *url*; return True on success.

    Success means the page loaded without raising and did not return an HTTP
    error status (404 or any >= 400). Any exception (e.g. ``TimeoutError``) or
    error status returns False so the caller can decide on a recovery step.
    """
    try:
        response = page.goto(
            url, wait_until="networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS
        )
    except Exception as exc:  # noqa: BLE001 — recovery decides next step
        logger.warning("Navigation to %s failed: %s", url, exc)
        return False
    status = getattr(response, "status", None)
    if isinstance(status, int) and status >= 400:
        logger.warning("Navigation to %s returned HTTP %s", url, status)
        return False
    # A 200 that lands on GitHub's login/session page means the repo is private
    # or login-required (issue #386): treat it as a failure so recovery can try
    # the source article and, ultimately, the URL card.
    final_url = getattr(page, "url", None)
    if _is_login_redirect(final_url):
        logger.warning(
            "Navigation to %s redirected to login (%s) — repo likely "
            "private/login-required",
            url,
            final_url,
        )
        return False
    return True


def _correct_repo_from_article(
    repo: RepoReference, source_url: str | None
) -> RepoReference | None:
    """Find the correct repo for *repo* on the source article page (issue #378).

    Fetches the article at *source_url* (the script header's ``Source URL:``)
    and returns a repo reference found there whose name matches *repo*'s name.
    Returns ``None`` when no source URL is available, the article cannot be
    fetched, or no confident name match is found. An owner-only match is
    deliberately *not* used as a fallback: it is too broad and can select an
    unrelated repo, which would be worse than the generic fallback.
    """
    if not source_url:
        return None
    try:
        candidates = fetch_repos_from_article(source_url)
    except Exception:  # noqa: BLE001 — best-effort recovery
        logger.exception("Failed to fetch repos from source article %s", source_url)
        return None
    if not candidates:
        return None
    for candidate in candidates:
        if candidate.name.lower() == repo.name.lower():
            return candidate
    return None


@dataclass
class _NavOutcome:
    """Result of a (possibly multi-step) repo navigation attempt."""

    repo: RepoReference
    recovery_path: str
    success: bool


def _navigate_with_recovery(
    page: Page,
    repo: RepoReference,
    source_url: str | None,
    *,
    backoff_seconds: Sequence[float] = REPO_RETRY_BACKOFF_SECONDS,
) -> _NavOutcome:
    """Navigate to *repo*, retrying and validating against the source article.

    Recovery order (issues #378, #381):

    1. ``direct`` — validate the URL format, then navigate to ``repo.url``.
       A URL that is obviously malformed (bad host/case/typo) skips the direct
       and retry attempts and goes straight to article correction.
    2. ``retry`` — on failure, retry with an incremental backoff
       (``backoff_seconds``, default 1 s / 3 s / 5 s) until one attempt succeeds.
    3. ``article`` — if every retry fails, fetch the correct URL from the source
       article and try that.
    4. ``fallback`` — only when every path above fails.

    Returns a :class:`_NavOutcome` carrying the (possibly corrected) repo, the
    recovery path used, and whether navigation ultimately succeeded.
    """
    # Validate the URL format before attempting (issue #381). Hammering an
    # obviously malformed URL (wrong host, bad casing, typo'd path) with retries
    # is wasted time; jump straight to consulting the source article instead.
    malformed = _looks_malformed_repo_url(repo.url)
    if malformed:
        logger.warning(
            "Repo URL %s looks malformed; skipping direct/retry and consulting "
            "source article",
            repo.url,
        )
    else:
        if _try_navigate_repo(page, repo.url):
            logger.info("Recovery path=direct succeeded for %s", repo.url)
            return _NavOutcome(repo, "direct", True)

        for attempt, delay in enumerate(backoff_seconds, start=1):
            logger.info(
                "Navigation to %s failed; retry %d/%d after %.1fs",
                repo.url,
                attempt,
                len(backoff_seconds),
                delay,
            )
            try:
                page.wait_for_timeout(int(delay * 1000))
            except Exception:  # noqa: BLE001 — delay is best-effort
                pass
            if _try_navigate_repo(page, repo.url):
                logger.info(
                    "Recovery path=retry succeeded for %s (attempt %d)",
                    repo.url,
                    attempt,
                )
                return _NavOutcome(repo, "retry", True)

        logger.info(
            "Repo URL %s still unreachable after %d retries; consulting source "
            "article",
            repo.url,
            len(backoff_seconds),
        )

    corrected = _correct_repo_from_article(repo, source_url)
    if corrected is not None and corrected != repo:
        logger.info(
            "Recovery: trying corrected URL %s (from source article %s)",
            corrected.url,
            source_url,
        )
        if _try_navigate_repo(page, corrected.url):
            logger.info(
                "Recovery path=article succeeded: %s -> %s",
                repo.url,
                corrected.url,
            )
            return _NavOutcome(corrected, "article", True)

    logger.warning(
        "Recovery path=fallback: all recovery attempts failed for %s", repo.url
    )
    return _NavOutcome(repo, "fallback", False)


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


def _try_record_project_site(
    page: Page, repo: RepoReference, duration_seconds: float
) -> str | None:
    """Record the repo's GitHub Pages site as a fallback (issue #386).

    When the GitHub repo page itself can't be recorded (404 / login-required /
    unreachable), the repo's *project website* is the best content we can still
    show.  We can't read the homepage link from the (failed) About sidebar, so
    we probe the conventional GitHub Pages URL ``https://{owner}.github.io/
    {name}/``; if it exists and loads, we record it like a normal website
    segment and return its URL.  Returns ``None`` (so the caller renders the URL
    card) when there's no Pages site or it fails to load.  Best-effort: a
    failure *after* the site loads keeps the partial recording rather than
    stacking a card on top of it (issue #381).
    """
    if not _check_gh_pages(repo.owner, repo.name):
        return None
    pages_url = f"https://{repo.owner}.github.io/{repo.name}/"
    if not _navigate_to_website(page, pages_url):
        return None
    logger.info(
        "Repo %s unrecordable; recording project site %s instead",
        repo.url,
        pages_url,
    )
    try:
        try:
            page.wait_for_load_state("networkidle", timeout=WEBSITE_NAV_TIMEOUT_MS)
        except Exception:
            pass
        page.wait_for_timeout(PAGE_SETTLE_MS)
        _dismiss_overlays(page)
        _prepare_page_for_recording(page)
        _smooth_scroll(page, duration_seconds)
    except Exception:
        logger.exception(
            "Error while recording project site %s — keeping partial recording",
            pages_url,
        )
    return pages_url


def _record_segment(
    browser: Browser,
    segment: VideoSegment,
    output_dir: Path,
    check_accessibility: bool = True,
    source_url: str | None = None,
) -> RecordedSegment:
    """Record a single video segment for a repo.

    Creates a fresh browser context with video recording, navigates to the
    repo URL, scrolls, and closes the context to finalize the video file.

    When the repo URL fails to load, navigation is retried and validated
    against the episode's source article before falling back to a generic
    branded screen (issue #378). *source_url* is the script header's
    ``Source URL:`` used to recover a corrected repo URL.
    """
    if segment.is_generic:
        return _record_generic_segment(browser, segment, output_dir)

    repo = segment.repo
    video_dir = output_dir / "raw"
    video_dir.mkdir(parents=True, exist_ok=True)

    is_fallback = False
    has_pages = False
    website_url: str | None = None
    recovery_path = "direct"

    if check_accessibility and not _check_repo_accessible(repo.url):
        # A failed pre-check (404) is a strong "URL looks bad" signal; let the
        # recovery flow retry and consult the source article (issue #378)
        # instead of immediately rendering the generic fallback.
        logger.warning(
            "Repo %s failed accessibility pre-check; will attempt recovery",
            repo.url,
        )
    else:
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

    # Reference point for the recorded clip length. If a later step fails after
    # the page has loaded, we pad the recording up to ``segment.duration_seconds``
    # so the clip isn't truncated (issue #381).
    record_start = time.monotonic()

    try:
        # Paint a dark hold frame before navigating so the recording's opening
        # frames are GitHub-dark rather than a white flash while the real page
        # loads (issue #355).
        try:
            page.set_content(DARK_HOLD_HTML)
        except Exception:
            pass

        outcome = _navigate_with_recovery(page, repo, source_url)
        recovery_path = outcome.recovery_path
        if not outcome.success:
            # Before showing a static card, try the repo's project website —
            # its GitHub Pages site — so we record real content when the repo
            # page 404s or requires login (issue #386).
            pages_url = _try_record_project_site(
                page, repo, segment.duration_seconds
            )
            if pages_url is not None:
                website_url = pages_url
                has_pages = True
                recovery_path = "website"
            else:
                is_fallback = True
                _render_url_card(
                    page, repo.owner, repo.name, segment.duration_seconds
                )
        else:
            # Navigation may have corrected the repo (e.g. via the source
            # article); use the effective repo for naming and the website flow.
            repo = outcome.repo
            # The repo page has already loaded successfully and is being
            # recorded.  Any error from here on (settle/scroll/website lookup)
            # must NOT append a generic "repo unavailable" fallback on top of
            # the good recording (issue #381): we already have valid content, so
            # we keep it and simply stop the extra polish steps.
            try:
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
                # If the repo links an external website, record that instead of
                # the GitHub page; fall back to GitHub if it fails to load
                # (issue #360).
                website_url = _extract_website_url(page)
                if website_url and _navigate_to_website(page, website_url):
                    try:
                        page.wait_for_load_state(
                            "networkidle", timeout=WEBSITE_NAV_TIMEOUT_MS
                        )
                    except Exception:
                        pass
                    page.wait_for_timeout(PAGE_SETTLE_MS)
                    _dismiss_overlays(page)
                else:
                    if website_url:
                        # _navigate_to_website may have navigated the page away
                        # from the GitHub repo (e.g. an HTTP >= 400 response
                        # still loads a page); go back so the GitHub flow records
                        # the right page.
                        try:
                            page.goto(
                                repo.url,
                                wait_until="networkidle",
                                timeout=NETWORK_IDLE_TIMEOUT_MS,
                            )
                        except Exception:
                            pass
                        page.wait_for_timeout(PAGE_SETTLE_MS)
                        _dismiss_overlays(page)
                    website_url = None
                _prepare_page_for_recording(page)
                _smooth_scroll(page, segment.duration_seconds)
            except Exception:
                # Keep the successfully recorded repo page; do not render a
                # fallback on top of it (issue #381).
                logger.exception(
                    "Error after successful navigation to %s — keeping the "
                    "recorded repo page (no fallback)",
                    repo.url,
                )
                # A polish step (settle/scroll/website lookup) raised before the
                # recording reached the full segment duration. Hold the already
                # loaded page (best effort) so the clip is long enough for
                # downstream composition/xfade assumptions, without rendering the
                # fallback overlay on top of the good content (issue #381).
                remaining_ms = int(
                    (segment.duration_seconds - (time.monotonic() - record_start))
                    * 1000
                )
                if remaining_ms > 0:
                    try:
                        page.wait_for_timeout(remaining_ms)
                    except Exception:
                        pass
    except Exception:
        logger.exception("Error recording %s — using fallback", repo.url)
        is_fallback = True
        recovery_path = "fallback"
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
        website_url=website_url,
        recovery_path=recovery_path,
    )


def record_episode(
    plan: EpisodePlan,
    output_dir: Path | str | None = None,
    headless: bool = True,
    check_accessibility: bool = True,
    source_url: str | None = None,
) -> RecordingResult:
    """Record all video segments for an episode plan.

    Args:
        plan: The episode plan with timed segments.
        output_dir: Directory for output video files. Uses a temp dir if None.
        headless: Run Chromium in headless mode (default True).
        check_accessibility: Pre-check repo URLs for 404 (default True).
        source_url: The script header's ``Source URL:``. Used to recover a
            corrected repo URL from the source article when a repo navigation
            fails (issue #378).

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
        browser = pw.chromium.launch(
            headless=headless, args=RECORDING_CHROMIUM_ARGS
        )
        try:
            for segment in plan.segments:
                logger.info(
                    "Recording segment: %s (%.1fs)",
                    segment.label,
                    segment.duration_seconds,
                )
                recorded = _record_segment(
                    browser, segment, output_dir, check_accessibility,
                    source_url=source_url,
                )
                result.recorded.append(recorded)
                logger.info(
                    "Saved: %s (fallback=%s, pages=%s, website=%s, recovery=%s)",
                    recorded.video_path.name,
                    recorded.is_fallback,
                    recorded.has_pages,
                    recorded.website_url,
                    recorded.recovery_path,
                )
        finally:
            browser.close()

    return result
