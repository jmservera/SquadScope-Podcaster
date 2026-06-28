"""Record GitHub repo pages as video segments using Playwright.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Phase 2: headless Chromium capture with auto-scroll.

Each segment navigates to a GitHub repo URL, waits for the page to load,
then smoothly scrolls the page over the segment duration.

Capture modes (issue #387):

* **Screenshot / hyperframe (default).** While scrolling, sequential lossless
  PNG screenshots of the 1920×1080 viewport are taken (one per scroll tick) and
  composed by ffmpeg into a high-quality H.264 ``.mp4`` segment. Because the PNG
  source is lossless and ffmpeg encodes offline, the result is pixel-perfect —
  no flicker, tearing or gradient banding from VP8 real-time encoding.
* **Legacy screencast.** Set ``VIDEO_SCREENSHOT_CAPTURE=false`` to instead let
  Playwright record the viewport to a WebM (VP8) in real time.
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        sync_playwright,
    )

    _PLAYWRIGHT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    _PLAYWRIGHT_AVAILABLE = False

from podcaster.retry import DEFAULT_TASK_RETRIES, retry_call
from podcaster.video.recording_pool import (
    MAX_RECORDING_CONCURRENCY,
    RecordingPoolConfig,
    load_recording_pool_config,
    record_segments_parallel,
)
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

# Deterministic, frame-indexed scrolling (issue #413).
#
# Stepping/judder happens when the scroll distance covered by a single captured
# frame is too large for the eye to read as continuous motion.  Two fixes:
#   1. Absolute, frame-indexed positioning (``window.scrollTo`` to a precomputed
#      integer Y per frame) instead of repeated fractional ``scrollBy`` calls,
#      which the browser rounds every tick and which accumulate drift, producing
#      uneven gaps between frames.
#   2. Capping the per-frame scroll distance to a comfortable reading speed so
#      short segments (or very long pages) don't fly past the content.  At 30fps
#      6-10 px/frame (180-300 px/sec) reads as smooth and legible.
# These are intentionally module constants so ``VIDEO_SCROLL_PX_PER_FRAME`` can
# tune the reading speed without code changes (applied below, once ``_env_int``
# is defined).
READING_PX_PER_FRAME = 8
# Hard cap on per-frame scroll distance for content reading; above ~10 px/frame
# at 30fps text starts to look steppy (issue #413).
MAX_READING_PX_PER_FRAME = 10

# Chromium flags that keep the compositor and timers running at full rate while
# headless.  Without these, Chromium throttles background/occluded renderers and
# timers, which drops recorded frames and makes scrolling stutter (issue #359).
RECORDING_CHROMIUM_ARGS = [
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]
NETWORK_IDLE_TIMEOUT_MS = 10_000
# GitHub repo pages are heavy (many async network requests for avatars, code
# navigation widgets, telemetry) and frequently never reach the ``networkidle``
# state within the generic 10 s budget — even though the DOM rendered usable
# content almost immediately.  A short timeout caused these repos to be treated
# as navigation failures and shown as a bare URL card (issue #405).  Give
# github.com a much larger budget so the page has time to settle, while the
# content-loaded check below lets us proceed even if it never does.
GITHUB_NETWORK_IDLE_TIMEOUT_MS = 60_000
# CSS selectors whose presence means a GitHub repo page (or most websites) has
# rendered substantial, usable content.  If any of these exist after a
# navigation that timed out on ``networkidle``, the page is good enough to
# record and we proceed instead of falling back to a URL card (issue #405).
CONTENT_LOADED_SELECTOR = ".repository-content, .markdown-body, [data-testid=repo-header], main"
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


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable (true/1/yes/on are truthy)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back to *default*."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid int for %s=%r; using default %d", name, raw, default)
        return default


# Bounded per-task retries for browser recording (issue #483).  A single
# segment whose recording fails transiently (flaky navigation, browser hiccup)
# is retried in isolation rather than aborting the whole episode.  Recording is
# idempotent: each attempt records into the same destination and a successful
# attempt is blob-checkpointed, so a restart skips it.  ``1`` disables retry.
RECORD_TASK_RETRIES = max(1, _env_int("VIDEO_RECORD_TASK_RETRIES", DEFAULT_TASK_RETRIES))


# --- Screenshot-based (hyperframe) capture (issue #387) ---
#
# Playwright's screencast (page.video) encodes VP8 in real time, which is lossy
# at capture time and produces flicker, tearing during scroll and gradient
# banding regardless of the final compose CRF.  Instead we capture sequential
# lossless PNG screenshots of the 1920x1080 viewport while scrolling, then let
# ffmpeg compose them into a high-quality H.264 segment — the same lossless
# source -> ffmpeg pattern that makes the intro/outro hyperframe clips look
# crisp.  Set VIDEO_SCREENSHOT_CAPTURE=false to fall back to the legacy
# screencast recorder for side-by-side comparison.
SCREENSHOT_CAPTURE_ENABLED = _env_bool("VIDEO_SCREENSHOT_CAPTURE", True)
# One screenshot per scroll tick keeps the scroll speed and segment duration
# identical to the screencast behaviour (frames / fps == duration), since the
# composed framerate equals the scroll tick rate.
SCREENSHOT_CAPTURE_FPS = _env_int("VIDEO_SCREENSHOT_FPS", SCROLL_TICKS_PER_SEC)
# Apply the optional reading-speed override now that _env_int is available
# (issue #413).  Kept here so the constant is defined alongside its default.
# Clamp to [1, MAX_READING_PX_PER_FRAME] so an env override can't produce
# steppy/unreadable motion above the hard cap (issue #415).
READING_PX_PER_FRAME = min(
    MAX_READING_PX_PER_FRAME,
    max(1, _env_int("VIDEO_SCROLL_PX_PER_FRAME", READING_PX_PER_FRAME)),
)
# A zero or negative framerate would break the frame-count math (division by
# zero, negative tick intervals) and is meaningless for capture, so clamp to a
# sane minimum of one frame per second.
if SCREENSHOT_CAPTURE_FPS < 1:
    logger.warning(
        "VIDEO_SCREENSHOT_FPS=%d is invalid; clamping to minimum of 1",
        SCREENSHOT_CAPTURE_FPS,
    )
    SCREENSHOT_CAPTURE_FPS = 1
# Near-visually-lossless CRF for the intermediate screenshot->video segment; the
# downstream compose re-encodes again, so we keep this high quality to avoid
# compounding compression artefacts.
SCREENSHOT_CAPTURE_CRF = _env_int("VIDEO_SCREENSHOT_CRF", 12)
# Use a slower x264 preset by default for the intermediate screenshot->video
# segment: screen content (text, gradients, dark-theme UI) benefits from the
# better motion estimation / rate-distortion of a slower preset, and this clip
# is short-lived so the extra encode time is acceptable (issue #392).
SCREENSHOT_CAPTURE_PRESET = os.environ.get("VIDEO_SCREENSHOT_PRESET", "slow")
# x264 ``-tune`` for the PNG->video compose.  ``stillimage`` is optimised for
# high-detail static screen content (sharp text, flat gradients) and avoids the
# psychovisual blurring ``-tune film`` would apply, which is exactly what the
# scrolling-screenshot frames are (issue #392).  Set to an empty string to
# disable tuning entirely.
SCREENSHOT_CAPTURE_TUNE = os.environ.get("VIDEO_SCREENSHOT_TUNE", "stillimage")

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

# Injected once the page has loaded, before the recorded scroll begins: convert
# every position:fixed / position:sticky element to a normal in-flow element so
# it scrolls away with the rest of the page instead of staying pinned to the
# viewport (issue #406).  Sticky/fixed headers (claracle.com nav, GitHub's repo
# header bar, …) otherwise stay in view while the content beneath them scrolls,
# so their position *relative to the surrounding content* changes every frame and
# the header appears to bounce/jump when the screenshots are composed into video.
# Forcing ``position:static`` drops them into the normal document flow so they
# scroll off cleanly and stay rock-steady between frames.  Inline ``cssText``
# overrides are used (rather than an injected stylesheet) because a stylesheet
# rule cannot beat an element's own ``style="position:fixed !important"``.
# Returns the number of elements that were neutralised.
_NEUTRALIZE_FIXED_STICKY_JS = """
() => {
  let count = 0;
  document.querySelectorAll('*').forEach((el) => {
    const pos = getComputedStyle(el).position;
    if (pos === 'fixed' || pos === 'sticky') {
      el.style.setProperty('position', 'static', 'important');
      count += 1;
    }
  });
  return count;
}
"""

# Minimum natural width AND height (px) for an image to anchor the zoom effect.
# Small icons/avatars are ignored; only a genuinely large image becomes the
# focal point of the camera zoom.
IMAGE_ZOOM_MIN_SIZE_PX = 200

# Peak zoom level for the full-page camera zoom (issue #395).  1.25× makes the
# focal area fill ~80% of the viewport (1 / 0.8 = 1.25) — enough to feel like a
# camera pushing in without cropping so far that surrounding context is lost.
PAGE_ZOOM_SCALE = 1.25
# Duration of the smooth ease-in-out zoom, in seconds.
PAGE_ZOOM_DURATION_S = 2.5

# A full-page "camera zoom" applied during recording (issue #395, supersedes the
# image-only bounce from #362/#373).  Instead of scaling a single <img>, the
# ENTIRE viewport is scaled via a transform on ``document.body`` with
# ``transform-origin`` anchored at the focal image's centre (set inline by
# _PAGE_ZOOM_JS), so everything zooms toward that point like a camera pushing in.
# ANTI_FLASH_CSS disables all animations via the universal selector with
# !important, so this rule must also use !important *and* the higher-specificity
# ``body.ss-page-zoom`` selector to win the cascade.  ``ease-in-out … forwards``
# gives a single smooth zoom-in that holds — no bounce / rebound.
ZOOM_PAGE_CSS = (
    "@keyframes ss-page-zoom{"
    f"from{{transform:scale(1);}}to{{transform:scale({PAGE_ZOOM_SCALE});}}}}"
    "body.ss-page-zoom{"
    f"animation:ss-page-zoom {PAGE_ZOOM_DURATION_S}s ease-in-out forwards "
    "!important;will-change:transform;}"
)

# Finds the largest qualifying image (both natural dimensions above the
# threshold), anchors the page-zoom ``transform-origin`` at its centre — expressed
# as a percentage of the full document so it stays correct while the page scrolls
# — and tags ``document.body`` with the ``ss-page-zoom`` class so ZOOM_PAGE_CSS
# animates the whole viewport.  Returns 1 when a focal image was found and the
# zoom was applied, 0 otherwise.
_PAGE_ZOOM_JS = """
(minSize) => {
  const imgs = Array.from(document.images || []);
  let best = null;
  let bestArea = 0;
  for (const img of imgs) {
    const w = img.naturalWidth || 0;
    const h = img.naturalHeight || 0;
    if (w === 0) {
      continue;
    }
    if (w > minSize && h > minSize) {
      const area = w * h;
      if (area > bestArea) {
        bestArea = area;
        best = img;
      }
    }
  }
  const body = document.body;
  if (!best || !body) {
    return 0;
  }
  const rect = best.getBoundingClientRect();
  const docW = Math.max(document.documentElement.scrollWidth, 1);
  const docH = Math.max(document.documentElement.scrollHeight, 1);
  const cx = rect.left + window.scrollX + rect.width / 2;
  const cy = rect.top + window.scrollY + rect.height / 2;
  const ox = Math.min(100, Math.max(0, (cx / docW) * 100));
  const oy = Math.min(100, Math.max(0, (cy / docH) * 100));
  body.style.transformOrigin = ox.toFixed(2) + '% ' + oy.toFixed(2) + '%';
  body.classList.add('ss-page-zoom');
  return 1;
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
 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95\
 0-.88-.31-1.59-.82-2.15\
 .08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27\
-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82\
-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95\
-.23.2-.44.55-.51 1.07\
-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53\
.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49\
 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z"></path>
    </svg>
  </div>
  <h1>{owner}/{name}</h1>
  <div class="url">{url}</div>
</div>
</body></html>
"""

# Card shown for a repo a pre-flight check flagged as removed from GitHub
# (issue #394).  Distinct from FALLBACK_BRAND_HTML (a clean URL card for repos
# we merely *couldn't* record): this explicitly tells the viewer the project was
# taken down, matching the speaker note the hosts read.
REMOVED_REPO_HTML = """\
<!DOCTYPE html>
<html><head><style>
body {{
  margin: 0; display: flex; align-items: center; justify-content: center;
  width: {width}px; height: {height}px;
  background: #0d1117; color: #c9d1d9; font-family: -apple-system, sans-serif;
}}
.card {{ text-align: center; padding: 0 64px; }}
.icon {{ font-size: 96px; margin-bottom: 24px; line-height: 1; }}
h1 {{ font-size: 52px; margin: 0 0 20px; font-weight: 600; }}
.reason {{ font-size: 32px; color: #f85149; margin: 0 0 28px; }}
.url {{
  display: inline-block; font-size: 30px; color: #8b949e;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: #161b22; border: 1px solid #30363d; border-radius: 12px;
  padding: 16px 28px; letter-spacing: 0.5px; text-decoration: line-through;
}}
</style></head><body>
<div class="card">
  <div class="icon">&#128683;</div>
  <h1>{owner}/{name}</h1>
  <div class="reason">{reason}</div>
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

# --- Cookie consent dismissal (issue #388) ---

# Overall time budget (ms) for matching the cookie-consent selectors against
# the page.  Each selector is checked once and the loop bails out when this
# deadline passes; it does not poll or wait for a banner to appear.  Kept short
# so sites without a banner don't stall the recording pipeline.
COOKIE_CONSENT_TIMEOUT_MS = 2500
# Small settle pause after clicking an accept button so the banner's dismissal
# animation finishes before recording starts.
COOKIE_DISMISS_SETTLE_MS = 400
# Consent banners are often injected by JavaScript shortly after page load, so a
# single pass can race ahead of the banner.  Poll the selectors/text fallback on
# this interval (ms) until the timeout budget is exhausted.
COOKIE_CONSENT_POLL_INTERVAL_MS = 100

# Known "accept" button selectors for the most common consent frameworks
# (OneTrust, Cookiebot, CookieConsent / Osano) plus widely-used generic ids and
# data-attributes.  Tried in order; the first visible match is clicked.
_COOKIE_CONSENT_SELECTORS = [
    # OneTrust
    "#onetrust-accept-btn-handler",
    "button#onetrust-accept-btn-handler",
    # Cookiebot
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    "#CybotCookiebotDialogBodyLevelButtonAccept",
    # CookieConsent (Osano / cookieconsent)
    ".cc-btn.cc-allow",
    ".cc-btn.cc-dismiss",
    # Common data-testids / attributes
    "[data-testid='cookie-policy-dialog-accept-button']",
    "[data-testid='uc-accept-all-button']",
    "[aria-label='Accept all']",
    "[aria-label='Accept All']",
    # Generic containers
    "[id*='cookie'] button",
    "[class*='cookie'] button",
    "[id*='consent'] button",
    "[class*='consent'] button",
]

# Lower-cased button labels treated as "accept" actions for the generic
# text-based fallback.  Ordered most→least specific so "accept all" wins over a
# bare "accept" when both exist.
_COOKIE_CONSENT_ACCEPT_TEXTS = [
    "accept all cookies",
    "accept all",
    "allow all",
    "accept cookies",
    "i accept",
    "i agree",
    "agree",
    "accept",
    "allow",
    "got it",
    "ok",
]

# JS that finds the first *visible* clickable element (button / role=button /
# anchor / input) whose trimmed, lower-cased text matches one of the accept
# labels and clicks it.  Returns the matched label or null.  Used as a generic
# fallback when none of the framework-specific selectors match (issue #388).
_COOKIE_ACCEPT_JS = """
(acceptTexts) => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none'
      && style.opacity !== '0';
  };
  const candidates = Array.from(document.querySelectorAll(
    "button, [role='button'], a, input[type='button'], input[type='submit']"
  ));
  const labelOf = (el) => {
    const t = (el.innerText || el.textContent || el.value || '').trim()
      .toLowerCase();
    if (t) return t;
    const aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
    return aria;
  };
  for (const wanted of acceptTexts) {
    for (const el of candidates) {
      if (!isVisible(el)) continue;
      if (labelOf(el) === wanted) {
        el.click();
        return wanted;
      }
    }
  }
  return null;
}
"""


def _dismiss_cookie_consent(page: Page, timeout_ms: int = COOKIE_CONSENT_TIMEOUT_MS) -> bool:
    """Find and accept a cookie-consent banner before recording (issue #388).

    Tries, in order:

    1. Known framework selectors (OneTrust, Cookiebot, CookieConsent, plus
       common ``cookie``/``consent`` container buttons).
    2. A generic text-based fallback that clicks the first visible button whose
       label is an "accept" action (Accept All / Accept / OK / I agree / …).

    Best-effort and non-blocking: every step swallows its own errors and the
    whole routine is budgeted by *timeout_ms* so a site without a banner never
    stalls recording.  Because consent UIs are frequently injected shortly after
    load, the selectors/text fallback are polled until the budget is exhausted
    rather than run once.  Returns ``True`` if a banner was dismissed.
    """
    # Non-positive budgets mean "don't wait" — short-circuit explicitly so the
    # behaviour doesn't depend on the resolution of ``time.monotonic()``.
    if timeout_ms <= 0:
        return False

    poll_interval_ms = COOKIE_CONSENT_POLL_INTERVAL_MS
    # At least one pass; otherwise spread the budget across evenly-spaced polls.
    max_passes = max(1, timeout_ms // poll_interval_ms)

    for attempt in range(max_passes):
        for selector in _COOKIE_CONSENT_SELECTORS:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click(timeout=500)
                    logger.debug("Dismissed cookie consent via selector %s", selector)
                    try:
                        page.wait_for_timeout(COOKIE_DISMISS_SETTLE_MS)
                    except Exception:
                        pass
                    return True
            except Exception:
                # Selector invalid for this page, element detached, click blocked —
                # move on to the next candidate.
                continue

        try:
            matched = page.evaluate(_COOKIE_ACCEPT_JS, _COOKIE_CONSENT_ACCEPT_TEXTS)
        except Exception:
            matched = None
        if matched:
            logger.debug("Dismissed cookie consent via generic button '%s'", matched)
            try:
                page.wait_for_timeout(COOKIE_DISMISS_SETTLE_MS)
            except Exception:
                pass
            return True

        # No banner yet — wait a beat for a late-injected one before retrying,
        # unless this was the final pass within the budget.
        if attempt < max_passes - 1:
            try:
                page.wait_for_timeout(poll_interval_ms)
            except Exception:
                break

    logger.debug("No cookie consent banner found")
    return False


@dataclass
class RecordedSegment:
    """Result of recording a single video segment."""

    segment: VideoSegment
    video_path: Path
    is_fallback: bool = False
    has_pages: bool = False
    website_url: str | None = None
    # True when the segment was a repo flagged "removed" by the planning-time
    # pre-flight check (issue #394): no navigation was attempted; the clip is a
    # clean "Repo removed" card.
    is_removed: bool = False
    # Which recovery path produced a usable recording (issues #378, #386):
    # "direct" (first attempt), "retry" (second attempt after a delay),
    # "article" (URL corrected from the source article), "website" (the repo's
    # GitHub Pages site, recorded when the repo page 404s/needs login), or
    # "fallback" (all recovery paths failed → clean URL card), or "removed"
    # (planning pre-flight flagged the repo as 404/removed → "Repo removed"
    # card, no navigation attempted — issue #394).
    recovery_path: str = "direct"


@dataclass
class RecordingResult:
    """Result of recording all segments for an episode."""

    recorded: list[RecordedSegment] = field(default_factory=list)
    output_dir: Path = field(default_factory=lambda: Path("."))


# --- Per-segment checkpoint/resume (issue #410) -------------------------------
#
# When an IntermediateStore is supplied, each recorded segment is checkpointed to
# blob (the recording file plus a small JSON sidecar carrying the recording-only
# metadata).  On a restart the recording — by far the most expensive phase, since
# it drives a headless browser through GitHub navigation — is skipped for any
# segment whose checkpoint already survived in blob.


def _recording_blob_name(index: int, suffix: str) -> str:
    return f"recording_{index:03d}{suffix}"


def _recording_meta_name(index: int) -> str:
    return f"recording_{index:03d}.json"


def _serialize_recording_meta(recorded: "RecordedSegment") -> str:
    """Serialize the recording-only metadata of a RecordedSegment to JSON.

    The ``segment`` (an immutable plan entry) is intentionally excluded: the plan
    is regenerated deterministically from the script on resume, so only the
    fields produced *during* recording need to survive in blob.
    """
    import json

    return json.dumps(
        {
            "suffix": recorded.video_path.suffix,
            "is_fallback": recorded.is_fallback,
            "has_pages": recorded.has_pages,
            "website_url": recorded.website_url,
            "is_removed": recorded.is_removed,
            "recovery_path": recorded.recovery_path,
        }
    )


def _resume_recorded_segment(
    index: int,
    segment: "VideoSegment",
    output_dir: Path,
    intermediates,
) -> "RecordedSegment | None":
    """Rebuild a RecordedSegment from its blob checkpoint, or return None.

    Returns ``None`` (so the caller records the segment normally) when the store
    is disabled, the sidecar metadata is missing/corrupt, or the recording file
    cannot be downloaded.
    """
    import json

    if intermediates is None or not intermediates.enabled:
        return None
    meta_text = intermediates.read_text(_recording_meta_name(index))
    if not meta_text:
        return None
    try:
        meta = json.loads(meta_text)
    except ValueError:
        return None
    if not isinstance(meta, dict):
        return None
    suffix = meta.get("suffix") or ".mp4"
    blob_name = _recording_blob_name(index, suffix)
    if not intermediates.exists(blob_name):
        return None
    dest = output_dir / blob_name
    if not intermediates.download(blob_name, dest):
        return None
    return RecordedSegment(
        segment=segment,
        video_path=dest,
        is_fallback=bool(meta.get("is_fallback", False)),
        has_pages=bool(meta.get("has_pages", False)),
        website_url=meta.get("website_url"),
        is_removed=bool(meta.get("is_removed", False)),
        recovery_path=str(meta.get("recovery_path", "direct")),
    )


def _validate_recording(path: Path) -> None:
    """Best-effort ffprobe sanity check of a freshly-recorded segment.

    Never raises: a probe failure (e.g. no system ffprobe, or a stub file in
    tests) is logged and ignored so it cannot block the checkpoint upload.  The
    authoritative integrity guarantee is the size-verified blob upload.
    """
    try:
        if not path.exists() or path.stat().st_size == 0:
            logger.warning("recorded segment is empty: %s", path)
            return
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001 — validation is advisory only
        logger.debug("ffprobe validation skipped/failed for %s", path, exc_info=True)


def _checkpoint_recorded_segment(
    index: int,
    recorded: "RecordedSegment",
    intermediates,
) -> None:
    """Checkpoint a freshly-recorded segment to blob, then free local disk.

    Validates the recording (best-effort ffprobe), uploads it (+ a JSON sidecar
    of recovery metadata) with the upload size-verified, and — only once the
    blob checkpoint is confirmed — deletes the local recording so the job's
    local disk holds at most the segment currently being recorded (issue #410).
    The recording is re-fetched from blob on demand when composition normalizes
    it.
    """
    if intermediates is None or not intermediates.enabled:
        return
    suffix = recorded.video_path.suffix or ".mp4"
    content_type = "video/webm" if suffix == ".webm" else "video/mp4"
    _validate_recording(recorded.video_path)
    if intermediates.upload(_recording_blob_name(index, suffix), recorded.video_path, content_type):
        intermediates.write_text(_recording_meta_name(index), _serialize_recording_meta(recorded))
        intermediates.mark(f"recording_{index:03d}", recovery_path=recorded.recovery_path)
        # Upload was size-verified above; the recording now lives safely in blob
        # so drop the local copy to keep disk usage bounded.
        try:
            recorded.video_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not delete local recording %s", recorded.video_path, exc_info=True)


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
    _neutralize_fixed_sticky(page)
    try:
        page.evaluate(
            "() => (document.fonts && document.fonts.ready) "
            "? document.fonts.ready : Promise.resolve()"
        )
    except Exception:
        pass
    _apply_page_zoom(page)


def _neutralize_fixed_sticky(page: Page) -> int:
    """Convert all fixed/sticky elements to static before recording (issue #406).

    Sticky/fixed headers (e.g. claracle.com's nav, GitHub's repo header) stay
    pinned to the viewport while the page scrolls, so their position relative to
    the surrounding content shifts every frame and they appear to bounce/jump in
    the composed video.  Forcing ``position:static`` drops them into normal flow
    so they scroll off cleanly and stay steady between frames.  Best-effort: any
    failure is swallowed so recording proceeds with the page as-is.  Returns the
    number of elements neutralised (0 on error or when none were found).
    """
    try:
        neutralised = page.evaluate(_NEUTRALIZE_FIXED_STICKY_JS)
    except Exception:
        return 0
    if neutralised:
        logger.debug("Neutralised %s fixed/sticky element(s) before recording", neutralised)
    return int(neutralised or 0)


def _apply_page_zoom(page: Page) -> int:
    """Apply a full-page "camera zoom" toward a focal image (issue #395).

    Supersedes the image-only bounce (#362/#373): instead of scaling a single
    ``<img>``, the largest qualifying image (natural width *and* height above
    ``IMAGE_ZOOM_MIN_SIZE_PX``) becomes the focal point and the **entire
    viewport** is zoomed toward it.  ``_PAGE_ZOOM_JS`` anchors the
    ``transform-origin`` at that image's centre and tags ``document.body`` so
    ZOOM_PAGE_CSS scales the whole page with a smooth ease-in-out that holds (no
    bounce).  The CSS overrides the universal ``animation:none`` rule from
    ANTI_FLASH_CSS via the higher-specificity ``body.ss-page-zoom`` selector plus
    ``!important``.  Best-effort: any failure is swallowed and no zoom is applied.
    Returns 1 when the page zoom was applied, 0 otherwise.
    """
    try:
        applied = page.evaluate(_PAGE_ZOOM_JS, IMAGE_ZOOM_MIN_SIZE_PX)
    except Exception:
        return 0
    if not applied:
        return 0
    try:
        page.add_style_tag(content=ZOOM_PAGE_CSS)
    except Exception:
        return 0
    logger.debug("Applied full-page camera zoom toward focal image")
    return int(applied)


# Backwards-compatible alias: earlier code/tests referenced the in-browser zoom
# as ``_apply_image_zoom``; it now performs a full-page camera zoom (issue #395).
_apply_image_zoom = _apply_page_zoom


class _Capturer:
    """Collects lossless PNG frames for screenshot-based segment composition.

    Screenshots are written as ``frame_00001.png`` … into *frames_dir* for the
    scrolling motion path, or as a single ``still.png`` for static pages
    (fallback / generic background) that are later held for the segment
    duration with ffmpeg's ``-loop``.  Capturing is best-effort: a failed
    screenshot is logged and skipped rather than aborting the recording.
    """

    def __init__(self, frames_dir: Path) -> None:
        self.frames_dir = frames_dir
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self.still_image: Path | None = None

    def frame(self, page: Page) -> Path | None:
        """Capture the next sequential viewport screenshot."""
        next_index = self.count + 1
        path = self.frames_dir / f"frame_{next_index:05d}.png"
        try:
            page.screenshot(path=str(path))
        except Exception:
            logger.exception("Failed to capture frame %d", next_index)
            return None
        self.count = next_index
        return path

    def still(self, page: Page) -> Path | None:
        """Capture a single still screenshot for a static page."""
        path = self.frames_dir / "still.png"
        try:
            page.screenshot(path=str(path))
        except Exception:
            logger.exception("Failed to capture still screenshot")
            return None
        self.still_image = path
        return path

    def reset_frames(self) -> None:
        """Discard any captured sequence frames.

        Used when a partially-scrolled page is abandoned in favour of a static
        fallback/background still, so composition holds the still rather than a
        truncated motion sequence (issue #387).
        """
        for index in range(1, self.count + 1):
            frame = self.frames_dir / f"frame_{index:05d}.png"
            try:
                frame.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                logger.debug("Could not remove frame %s", frame)
        self.count = 0


def _build_frames_to_video_cmd(frames_dir: Path, fps: int, output_path: Path) -> list[str]:
    """ffmpeg command composing a PNG frame sequence into an H.264 clip."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(frames_dir / "frame_%05d.png"),
        "-vf",
        "format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        SCREENSHOT_CAPTURE_PRESET,
    ]
    if SCREENSHOT_CAPTURE_TUNE:
        cmd += ["-tune", SCREENSHOT_CAPTURE_TUNE]
    cmd += [
        "-crf",
        str(SCREENSHOT_CAPTURE_CRF),
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        str(output_path),
    ]
    return cmd


def _build_still_to_video_cmd(
    still_path: Path, duration_seconds: float, fps: int, output_path: Path
) -> list[str]:
    """ffmpeg command holding a single still PNG for *duration_seconds*."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-t",
        f"{max(duration_seconds, 1.0 / fps):.3f}",
        "-i",
        str(still_path),
        "-vf",
        "format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        SCREENSHOT_CAPTURE_PRESET,
    ]
    if SCREENSHOT_CAPTURE_TUNE:
        cmd += ["-tune", SCREENSHOT_CAPTURE_TUNE]
    cmd += [
        "-crf",
        str(SCREENSHOT_CAPTURE_CRF),
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        str(output_path),
    ]
    return cmd


def _pad_frames(capturer: _Capturer, target_count: int) -> None:
    """Duplicate the last captured frame until *target_count* is reached.

    Error paths may capture fewer frames than the segment duration requires;
    duplicating the final frame keeps the composed clip the correct length
    without inventing motion.
    """
    if capturer.count == 0 or capturer.count >= target_count:
        return
    last = capturer.frames_dir / f"frame_{capturer.count:05d}.png"
    if not last.exists():
        return
    while capturer.count < target_count:
        next_index = capturer.count + 1
        dest = capturer.frames_dir / f"frame_{next_index:05d}.png"
        shutil.copyfile(last, dest)
        capturer.count = next_index


def _compose_screenshot_segment(
    capturer: _Capturer, duration_seconds: float, output_path: Path
) -> Path:
    """Compose captured screenshots into the segment video at *output_path*.

    Uses the held-still command for static pages and the frame-sequence
    command for scrolling motion.  Raises if nothing was captured.
    """
    fps = SCREENSHOT_CAPTURE_FPS
    if capturer.count > 0:
        expected = max(1, math.ceil(duration_seconds * fps))
        _pad_frames(capturer, expected)
        cmd = _build_frames_to_video_cmd(capturer.frames_dir, fps, output_path)
    elif capturer.still_image is not None and capturer.still_image.exists():
        cmd = _build_still_to_video_cmd(capturer.still_image, duration_seconds, fps, output_path)
    else:
        raise RuntimeError("No screenshots captured for screenshot-based segment composition")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg timed out composing screenshot segment: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed composing screenshot segment "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    if not output_path.exists():
        raise RuntimeError(f"ffmpeg did not produce screenshot segment at {output_path}")
    return output_path


def _make_recording_context(
    browser: Browser, output_dir: Path, segment_label: str = ""
) -> "tuple[BrowserContext, _Capturer | None]":
    """Create a browser context for capture, honouring the capture mode.

    In screenshot/hyperframe mode (default, issue #387) the context records no
    video; frames are captured as PNG screenshots into a per-segment temp dir.
    In legacy screencast mode the context records a WebM via Playwright.

    A per-segment INFO line confirms which capture path is active so production
    logs make it obvious that the high-quality hyperframe path — not the lossy
    VP8 screencast fallback — is being used (issue #392).
    """
    label = f" for {segment_label}" if segment_label else ""
    kwargs: dict = {
        "viewport": {"width": WIDTH, "height": HEIGHT},
        # Pin a 1.0 device scale factor so screenshots are captured at the
        # native 1920x1080 viewport resolution with no HiDPI up/down-scaling
        # (issue #392) — scaled captures would soften text and gradients.
        "device_scale_factor": 1,
        "color_scheme": "dark",
    }
    capturer: _Capturer | None = None
    if SCREENSHOT_CAPTURE_ENABLED:
        frames_dir = output_dir / "frames" / uuid.uuid4().hex
        capturer = _Capturer(frames_dir)
        logger.info(
            "Hyperframe capture mode active%s: lossless PNG screenshots at "
            "%dx%d, composed at %d fps (preset=%s, tune=%s, crf=%d)",
            label,
            WIDTH,
            HEIGHT,
            SCREENSHOT_CAPTURE_FPS,
            SCREENSHOT_CAPTURE_PRESET,
            SCREENSHOT_CAPTURE_TUNE or "none",
            SCREENSHOT_CAPTURE_CRF,
        )
    else:
        video_dir = output_dir / "raw"
        video_dir.mkdir(parents=True, exist_ok=True)
        kwargs["record_video_dir"] = str(video_dir)
        kwargs["record_video_size"] = {"width": WIDTH, "height": HEIGHT}
        logger.info(
            "Legacy VP8 screencast capture mode active%s (VIDEO_SCREENSHOT_"
            "CAPTURE=false): real-time WebM at %dx%d — hyperframe disabled",
            label,
            WIDTH,
            HEIGHT,
        )
    context = browser.new_context(**kwargs)
    return context, capturer


def _finalize_segment(
    page: Page,
    context: BrowserContext,
    capturer: "_Capturer | None",
    output_dir: Path,
    dest_stem: str,
    duration_seconds: float,
) -> Path:
    """Close the context and produce the segment video file.

    Screenshot mode composes the captured PNG frames into an H.264 ``.mp4`` via
    ffmpeg; screencast mode finalises and renames Playwright's WebM.
    """
    unique_suffix = uuid.uuid4().hex[:8]
    if capturer is not None:
        # No real-time video to finalise; frames are already on disk.
        context.close()
        dest_path = output_dir / f"{dest_stem}_{unique_suffix}.mp4"
        _compose_screenshot_segment(capturer, duration_seconds, dest_path)
        # Best-effort cleanup of the per-segment frame directory.
        try:
            shutil.rmtree(capturer.frames_dir, ignore_errors=True)
        except Exception:
            pass
        return dest_path

    # Legacy screencast: read the video handle before closing finalises it.
    video = page.video
    context.close()
    if video is None:
        raise RuntimeError(f"No video object for recording of {dest_stem}")
    src_path = Path(video.path())
    dest_path = output_dir / f"{dest_stem}_{unique_suffix}.webm"
    if src_path.exists():
        src_path.rename(dest_path)
    else:
        raise FileNotFoundError(f"Playwright video file not found at {src_path}")
    return dest_path


def _ease_linear(t: float) -> float:
    """Identity easing — constant scroll speed (issue #413)."""
    return t


def _ease_out_cubic(t: float) -> float:
    """easeOutCubic — fast start, gentle deceleration (issue #413).

    Used for quick transitions (e.g. jumping to a README) so the motion lands
    softly instead of stopping abruptly.
    """
    return 1.0 - (1.0 - t) ** 3


def _ease_in_out(t: float) -> float:
    """Smoothstep ease-in-out — gentle start and finish (issue #543).

    ``3t² − 2t³`` (Hermite smoothstep): velocity ramps up from zero, peaks at
    the midpoint and decelerates back to zero.  Its peak slope is exactly
    ``1.5×`` the average, so — unlike :func:`_ease_out_cubic`, which front-loads
    the largest delta into the very first frame — it never produces a hard
    per-frame jump.  Used for the README travel phase so the transition reads as
    a continuous fast scroll rather than a cut (issue #543).
    """
    return t * t * (3.0 - 2.0 * t)


# Peak-slope multiplier of :func:`_ease_in_out` relative to its average speed.
# Used to size the travel-phase frame budget so the *peak* per-frame velocity
# stays within the smooth-travel cap (issue #543).
_EASE_IN_OUT_PEAK_FACTOR = 1.5


_EASINGS: dict[str, Callable[[float], float]] = {
    "linear": _ease_linear,
    "ease_out_cubic": _ease_out_cubic,
    "ease_in_out": _ease_in_out,
}


def _scroll_positions(
    start_y: float,
    end_y: float,
    total_frames: int,
    easing: str = "linear",
) -> "list[int]":
    """Compute deterministic integer scroll Y positions, one per frame (#413).

    Returns ``total_frames`` absolute Y offsets from *start_y* to *end_y*
    following the named *easing* curve.  Positions are produced from a single
    continuous parameter ``t in [0, 1]`` (not by accumulating per-frame deltas)
    so there is no rounding drift between frames — the motion plays back as
    butter-smooth, evenly spaced steps.  For ``total_frames >= 2`` the final
    position is ``round(end_y)``; the degenerate ``total_frames == 1`` case
    returns ``round(start_y)`` since a single frame cannot move.
    """
    if total_frames <= 0:
        return []
    if total_frames == 1:
        return [int(round(start_y))]
    fn = _EASINGS.get(easing, _ease_linear)
    span = end_y - start_y
    last = total_frames - 1
    return [int(round(start_y + span * fn(i / last))) for i in range(total_frames)]


def _scroll_frame_count(duration_seconds: float, capturer: "_Capturer | None") -> "tuple[int, int]":
    """Return ``(total_frames, tick_rate)`` for a scroll of *duration_seconds*.

    In screenshot mode the capture rate must equal the composed framerate so
    ``frame_count / fps == duration`` (ceil, matching the composer); in
    screencast mode the historical SCROLL_TICKS_PER_SEC floor is used so a
    sub-tick duration simply waits without scrolling.
    """
    tick_rate = SCREENSHOT_CAPTURE_FPS if capturer is not None else SCROLL_TICKS_PER_SEC
    total_frames = (
        math.ceil(duration_seconds * tick_rate)
        if capturer is not None
        else int(duration_seconds * tick_rate)
    )
    return total_frames, tick_rate


def _run_scroll_positions(
    page: Page,
    positions: "list[int]",
    capturer: "_Capturer | None",
    tick_interval_ms: int,
) -> None:
    """Drive the page through *positions* (absolute Y), one frame each (#413).

    Screenshot mode captures a frame per position; screencast mode waits one
    tick interval per position so the motion plays back in real time.
    """
    for y in positions:
        page.evaluate(f"window.scrollTo(0, {y})")
        if capturer is not None:
            capturer.frame(page)
        else:
            page.wait_for_timeout(tick_interval_ms)


def _smooth_scroll(
    page: Page,
    duration_seconds: float,
    capturer: "_Capturer | None" = None,
    *,
    start_y: "float | None" = None,
    end_y: "float | None" = None,
    easing: str = "linear",
    max_px_per_frame: int = READING_PX_PER_FRAME,
) -> None:
    """Deterministically scroll the page over the given duration (issue #413).

    Motion is driven by precomputed, frame-indexed absolute positions
    (``window.scrollTo``) instead of repeated fractional ``scrollBy`` calls, so
    every frame lands on an exact Y with no rounding drift — the playback reads
    as smooth, evenly spaced steps rather than visible jumps.

    By default the scroll distance is derived from the page height (capped to
    MAX_SCROLL_VIEWPORT_MULTIPLIER × viewport) and further clamped to a
    comfortable reading speed of *max_px_per_frame* per frame (default
    READING_PX_PER_FRAME, tunable via the VIDEO_SCROLL_PX_PER_FRAME env var and
    hard-capped at MAX_READING_PX_PER_FRAME) so short segments or very long
    pages don't fly past the content.  The derived path always uses linear
    spacing so each per-frame delta stays at or below the cap regardless of the
    *easing* argument.  Callers that need an explicit range (e.g. the
    README-first flow, issue #415) pass *start_y* / *end_y* and an *easing*
    curve directly, bypassing the reading-speed cap.

    When *capturer* is provided (screenshot/hyperframe mode, issue #387) a
    lossless PNG screenshot is taken per frame instead of waiting in real time;
    the frame count equals ``duration_seconds * SCREENSHOT_CAPTURE_FPS`` so the
    captured motion plays back at exactly the segment duration.
    """
    total_ticks, tick_rate = _scroll_frame_count(duration_seconds, capturer)
    if total_ticks <= 0:
        # Duration is positive but too short for a full tick.
        if capturer is not None:
            # Still emit at least one frame so the segment has content.
            capturer.frame(page)
        elif duration_seconds > 0:
            page.wait_for_timeout(int(duration_seconds * 1000))
        return

    tick_interval_ms = int(1000 / tick_rate)
    viewport_height = page.viewport_size["height"] if page.viewport_size else HEIGHT

    scroll_easing = easing
    if end_y is not None:
        # Explicit range requested by the caller (issue #415); no reading cap.
        s_y = float(start_y or 0)
        e_y = float(end_y)
    else:
        # Derive the scroll distance from the page, capped to a reasonable span
        # and then to a comfortable reading speed (issue #413).  Use the
        # configurable READING_PX_PER_FRAME default (hard-capped at
        # MAX_READING_PX_PER_FRAME) and force linear spacing so every per-frame
        # delta stays at or below the cap — a non-linear easing would otherwise
        # let early-frame deltas exceed it even when the total span is bounded.
        per_frame_cap = min(max(1, max_px_per_frame), MAX_READING_PX_PER_FRAME)
        scroll_easing = "linear"
        max_scroll = int(viewport_height * MAX_SCROLL_VIEWPORT_MULTIPLIER)
        scroll_height = page.evaluate("document.documentElement.scrollHeight")
        page_scroll_distance = max(0, scroll_height - viewport_height)
        effective_scroll = min(page_scroll_distance, max_scroll)
        reading_cap = max(total_ticks - 1, 1) * per_frame_cap
        s_y = float(start_y or 0)
        e_y = s_y + float(min(effective_scroll, reading_cap))

    if e_y <= s_y:
        # Nothing to scroll.  In screenshot mode we still capture a full run of
        # (identical) frames so the segment keeps its intended duration; in
        # screencast mode we simply wait it out.
        if capturer is None:
            page.wait_for_timeout(int(duration_seconds * 1000))
            return
        for _ in range(total_ticks):
            capturer.frame(page)
        return

    positions = _scroll_positions(s_y, e_y, total_ticks, scroll_easing)
    _run_scroll_positions(page, positions, capturer, tick_interval_ms)

    if capturer is not None:
        # Frame count (total_ticks) already encodes the duration at the
        # composed framerate; no real-time padding is needed.
        return

    # Wait out any remaining fractional duration not covered by ticks
    elapsed_ms = total_ticks * tick_interval_ms
    requested_ms = int(duration_seconds * 1000)
    remainder_ms = requested_ms - elapsed_ms
    if remainder_ms > 0:
        page.wait_for_timeout(remainder_ms)


# --- README-first scroll for GitHub repos (issue #415) ---
#
# GitHub repo pages render the file tree above the README, so a plain top-to-
# bottom scroll wastes screen time crawling through file names before reaching
# the content viewers actually care about.  Instead we: briefly hold on the repo
# header, do a quick eased jump down to the README, then scroll the README at
# reading speed.  All staging is best-effort — any detection failure falls back
# to the normal deterministic scroll so the pipeline never breaks (#415).

# Header hold / jump budgets, expressed in *seconds* so the staged durations
# stay constant regardless of the active capture rate (VIDEO_SCREENSHOT_FPS).
# They are converted to frame counts at the live tick rate in
# ``_scroll_github_readme``.
GITHUB_HEADER_HOLD_SECONDS = 2.0  # issue range 1.5-2.5s
GITHUB_JUMP_SECONDS = 0.6  # eased transition, issue range 0.5-0.9s
# Peak per-frame velocity (px/frame) allowed during the README travel phase.
# The travel that brings the README into view used to be a fixed, short
# ``ease_out_cubic`` jump whose first frame could leap >1000px — a hard cut, not
# a scroll (issue #543).  The travel-phase frame budget is now sized from the
# distance so the *peak* per-frame step never exceeds this cap, keeping the
# transition a continuous (if brisk) scroll.  At 30fps, 60px/frame ≈ 1800px/s —
# fast enough to clear the file tree quickly, slow enough to read as motion.
GITHUB_JUMP_MAX_PX_PER_FRAME = 60
# Frame-count equivalents at the default 30fps capture rate.  Kept as the
# defaults for ``_github_scroll_plan`` (frame-indexed) and for tests.
GITHUB_HEADER_HOLD_FRAMES = round(GITHUB_HEADER_HOLD_SECONDS * SCROLL_TICKS_PER_SEC)  # 60
GITHUB_JUMP_FRAMES = round(GITHUB_JUMP_SECONDS * SCROLL_TICKS_PER_SEC)  # 18
# Pixels of headroom kept above the README so its heading stays visible after
# the jump (rather than aligning the README flush to the very top).
GITHUB_README_TOP_MARGIN = 120

# Robustly locate the README and report its document offset plus the page's max
# scrollable Y.  Returns ``{readmeY: int|null, scrollable: int}``.
_README_METRICS_JS = """
() => {
  const readme =
    document.querySelector('#readme') ||
    document.querySelector('article.markdown-body') ||
    document.querySelector('[data-testid="readme"]') ||
    (() => {
      const h = [...document.querySelectorAll('h2, h3')].find(
        (el) => /readme/i.test(el.textContent || '')
      );
      return h ? h.closest('div, section, article') : null;
    })();
  const docH = Math.max(
    document.documentElement.scrollHeight,
    document.body ? document.body.scrollHeight : 0
  );
  const vh = window.innerHeight || document.documentElement.clientHeight || 0;
  const scrollable = Math.max(0, docH - vh);
  if (!readme) return { readmeY: null, scrollable };
  const rect = readme.getBoundingClientRect();
  const y = Math.round(rect.top + window.scrollY);
  return { readmeY: Math.max(0, y), scrollable };
}
"""


def _github_scroll_plan(
    readme_y: int,
    viewport_height: int,
    doc_scrollable: int,
    total_frames: int,
    *,
    header_frames: int = GITHUB_HEADER_HOLD_FRAMES,
    jump_frames: int = GITHUB_JUMP_FRAMES,
    px_per_frame: int = READING_PX_PER_FRAME,
    jump_px_per_frame: int = GITHUB_JUMP_MAX_PX_PER_FRAME,
) -> "list[int] | None":
    """Build the per-frame Y positions for the README-first flow (issue #415).

    Phases: hold on the header, a velocity-bounded eased *travel* down to the
    README, then a reading-speed linear scroll through the README content.
    Returns a list of exactly *total_frames* absolute Y offsets, or ``None`` when
    there aren't enough frames to stage the flow (the caller then does a plain
    smooth scroll).

    The travel phase used to be a fixed, short ``ease_out_cubic`` jump whose very
    first frame could leap over a thousand pixels — a hard cut rather than a
    scroll (issue #543).  Its frame budget is now derived from the travel
    distance and *jump_px_per_frame* so the *peak* per-frame step stays within
    that cap, using the symmetric :func:`_ease_in_out` curve (gentle start and
    finish).  *jump_frames* now acts as a lower bound so even short travels keep
    a soft eased glide.  When the README is so deep that it can't be reached
    within the available frames at travel speed, the travel distance is clamped
    (landing partway, then reading continues) rather than ever jumping hard.
    """
    if total_frames <= 0:
        return []
    readme_y = max(0, min(int(readme_y), max(0, int(doc_scrollable))))
    px_per_frame = min(
        MAX_READING_PX_PER_FRAME,
        max(1, int(px_per_frame)),
    )
    travel_cap = max(1, int(jump_px_per_frame))

    # Header hold first, then split the remainder between travel and reading.
    header = min(header_frames, total_frames // 4)
    available = total_frames - header

    # Frames the eased travel needs so its peak per-frame step (≈ peak-factor ×
    # the average) stays within travel_cap.  ``+1`` converts movement intervals
    # to positions; ``jump_frames`` is a soft minimum so short travels still ease
    # in and out instead of snapping (issue #543).
    needed_intervals = math.ceil(_EASE_IN_OUT_PEAK_FACTOR * readme_y / travel_cap)
    jump_needed = max(needed_intervals + 1, jump_frames)
    # Always reserve a real reading budget so a deep README never collapses the
    # read phase to a single held (no-op) frame — travel yields frames to keep
    # this floor, clamping its distance if necessary (issue #543 review).
    reading_floor = max(2, available // 6)
    max_jump = max(1, available - reading_floor)
    jump = min(jump_needed, max_jump)
    reading = available - jump
    if header < 1 or jump < 1 or reading < 1:
        return None

    # If the budget couldn't fit the full eased travel, only go as far as the
    # travel speed allows so the peak step never exceeds the cap (issue #543).
    if jump >= jump_needed:
        travel_end = readme_y
    else:
        reachable = int((jump - 1) * travel_cap / _EASE_IN_OUT_PEAK_FACTOR)
        travel_end = min(readme_y, reachable)
    travel_end = max(0, min(travel_end, int(doc_scrollable)))

    plan: "list[int]" = [0] * header
    plan += _scroll_positions(0, travel_end, jump, easing="ease_in_out")
    # The reading phase has ``reading`` frames, i.e. ``reading - 1`` movement
    # intervals, so size the span off the interval count to keep the average
    # per-frame delta at or below *px_per_frame* (issue #415).
    read_end = min(int(doc_scrollable), travel_end + max(reading - 1, 1) * px_per_frame)
    plan += _scroll_positions(travel_end, read_end, reading, easing="linear")

    # Guard against off-by-one from the phase concatenation.
    if len(plan) < total_frames:
        plan += [plan[-1]] * (total_frames - len(plan))
    return plan[:total_frames]


def _scroll_github_readme(
    page: Page,
    duration_seconds: float,
    capturer: "_Capturer | None" = None,
) -> None:
    """README-first scroll for a GitHub repo page (issue #415).

    Holds on the repo header, eases down to the README, then scrolls it at
    reading speed.  Falls back to :func:`_smooth_scroll` for non-GitHub pages,
    when no README is found, when the README is already near the top, or on any
    detection error — so behaviour never regresses for other pages.
    """
    try:
        if not _is_github_repo_root(getattr(page, "url", None)):
            _smooth_scroll(page, duration_seconds, capturer)
            return
    except Exception:  # noqa: BLE001 — treat detection failure as "not a repo root"
        _smooth_scroll(page, duration_seconds, capturer)
        return

    total_frames, tick_rate = _scroll_frame_count(duration_seconds, capturer)
    if total_frames <= 0:
        _smooth_scroll(page, duration_seconds, capturer)
        return

    viewport_height = page.viewport_size["height"] if page.viewport_size else HEIGHT

    try:
        metrics = page.evaluate(_README_METRICS_JS)
    except Exception:  # noqa: BLE001 — fall back to a plain scroll on JS errors
        metrics = None
    if not isinstance(metrics, dict) or metrics.get("readmeY") is None:
        _smooth_scroll(page, duration_seconds, capturer)
        return

    doc_scrollable = int(metrics.get("scrollable") or 0)
    readme_y = max(0, int(metrics["readmeY"]) - GITHUB_README_TOP_MARGIN)
    # Clamp to the real max scrollable Y so both the scroll plan and the log
    # below reflect a physically reachable target (README near the bottom plus
    # the top margin can otherwise overshoot ``doc_scrollable``).
    readme_y = min(readme_y, max(0, doc_scrollable))
    # README already near the top → a normal reading scroll is the right thing.
    if readme_y <= viewport_height * 0.5:
        _smooth_scroll(page, duration_seconds, capturer)
        return

    # Convert the seconds-based hold/jump budgets to frame counts at the *live*
    # tick rate so their wall-clock durations are stable even when
    # VIDEO_SCREENSHOT_FPS overrides the capture rate (issue #415).
    header_frames = max(1, round(GITHUB_HEADER_HOLD_SECONDS * tick_rate))
    jump_frames = max(1, round(GITHUB_JUMP_SECONDS * tick_rate))
    plan = _github_scroll_plan(
        readme_y,
        viewport_height,
        doc_scrollable,
        total_frames,
        header_frames=header_frames,
        jump_frames=jump_frames,
    )
    if not plan:
        _smooth_scroll(page, duration_seconds, capturer)
        return

    logger.info(
        "README-first scroll: header hold, smooth travel to y=%d then read to y=%d (#543)",
        readme_y,
        plan[-1],
    )
    tick_interval_ms = int(1000 / tick_rate)
    _run_scroll_positions(page, plan, capturer, tick_interval_ms)

    if capturer is not None:
        return
    elapsed_ms = total_frames * tick_interval_ms
    remainder_ms = int(duration_seconds * 1000) - elapsed_ms
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
    page: Page,
    owner: str,
    name: str,
    duration_seconds: float,
    capturer: "_Capturer | None" = None,
) -> None:
    """Show a clean URL card for a repo we couldn't record (issue #386).

    Replaces the old "Repository unavailable" screen: instead of telling the
    viewer something is broken, we present the repository identity and its URL
    prominently so the segment still reads as intentional, branded content.

    In screenshot mode (*capturer* provided) a single still is captured and the
    clip is held for the duration during composition; otherwise the page is held
    in real time for the screencast recorder (issue #387).
    """
    url = f"github.com/{owner}/{name}"
    html = FALLBACK_BRAND_HTML.format(width=WIDTH, height=HEIGHT, owner=owner, name=name, url=url)
    page.set_content(html)
    if capturer is not None:
        # Discard any partially-captured frames so the fallback is held as a
        # still rather than a truncated motion sequence (issue #387).
        capturer.reset_frames()
        capturer.still(page)
    else:
        page.wait_for_timeout(int(duration_seconds * 1000))


# Backwards-compatible alias: earlier code/tests referenced the screen shown
# when a repo can't be recorded as the "fallback page".  It now renders the
# clean URL card (issue #386).
_render_fallback_page = _render_url_card


def _render_removed_card(
    page: Page,
    owner: str,
    name: str,
    reason: str,
    duration_seconds: float,
    capturer: "_Capturer | None" = None,
) -> None:
    """Show a "Repo removed" card for a repo taken down from GitHub (issue #394).

    Used when planning-time pre-flight detected an HTTP 404 for the repo. Unlike
    :func:`_render_url_card` (a neutral URL card for repos we merely couldn't
    record), this explicitly states the project was removed so the card matches
    the speaker note the hosts read.  No navigation is attempted, so no
    recording time is wasted on the dead URL.

    In screenshot mode (*capturer* provided) a single still is captured and held
    for the duration during composition; otherwise the page is held in real time
    for the screencast recorder (issue #387).
    """
    url = f"github.com/{owner}/{name}"
    html = REMOVED_REPO_HTML.format(
        width=WIDTH, height=HEIGHT, owner=owner, name=name, reason=reason, url=url
    )
    page.set_content(html)
    if capturer is not None:
        capturer.reset_frames()
        capturer.still(page)
    else:
        page.wait_for_timeout(int(duration_seconds * 1000))


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


def _is_github_repo_root(url: str | None) -> bool:
    """Return True only for a GitHub repo *root* page (``/owner/repo``).

    The README-first scroll (issue #415) is meant for repository landing pages.
    Issues, PRs, wiki, and other deep pages share the ``github.com`` host but
    have extra path segments and unrelated ``article.markdown-body`` content, so
    they must fall back to the normal deterministic scroll.
    """
    if not _is_github_url(url):
        return False
    try:
        path = urlparse(url).path
    except Exception:  # noqa: BLE001 — malformed URLs are simply "not a repo root"
        return False
    segments = [seg for seg in path.split("/") if seg]
    return len(segments) == 2


def _is_github_url(url: str | None) -> bool:
    """Return True when *url* points at github.com (issue #405).

    Used to give GitHub repo pages a longer ``networkidle`` budget than generic
    websites, since they routinely keep background requests alive well past the
    point the visible content has rendered.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001 — malformed URLs are simply "not github"
        return False
    return host == "github.com" or host.endswith(".github.com")


def _page_has_content(page: Page) -> bool:
    """Return True when *page* has rendered substantial, usable content.

    Checks for any of :data:`CONTENT_LOADED_SELECTOR` in the live DOM.  This is
    the signal that lets us record a page whose ``networkidle`` wait timed out
    but which nonetheless loaded real content (issue #405).  Any error querying
    the page is treated as "no content" so the caller falls back safely.
    """
    try:
        found = page.evaluate("(sel) => !!document.querySelector(sel)", CONTENT_LOADED_SELECTOR)
    except Exception:  # noqa: BLE001 — a dead/blank page has no usable content
        return False
    return bool(found)


def _try_navigate_repo(page: Page, url: str) -> bool:
    """Attempt a single navigation to *url*; return True on success.

    Success means the page loaded without raising and did not return an HTTP
    error status (404 or any >= 400).

    When the ``networkidle`` wait times out (or otherwise raises) the page may
    still have loaded usable content — common for heavy GitHub pages whose
    background requests never go idle (issue #405).  In that case we proceed
    with recording as long as the page isn't a login wall and actually has
    content (:func:`_page_has_content`).  Only a truly blank/login page after a
    timeout returns False so the caller can decide on a recovery step.
    """
    timeout_ms = GITHUB_NETWORK_IDLE_TIMEOUT_MS if _is_github_url(url) else NETWORK_IDLE_TIMEOUT_MS
    try:
        response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001 — recovery decides next step
        # ``networkidle`` may never settle on a heavy GitHub page even though the
        # DOM already rendered usable content (issue #405).  Before giving up,
        # check whether the page is a login wall or actually has content.
        final_url = getattr(page, "url", None)
        if _is_login_redirect(final_url):
            logger.warning(
                "Navigation to %s timed out on a login page (%s) — repo likely "
                "private/login-required",
                url,
                final_url,
            )
            return False
        if _page_has_content(page):
            logger.info(
                "Navigation to %s did not reach networkidle (%s) but the page "
                "has loadable content — proceeding (issue #405)",
                url,
                exc,
            )
            return True
        logger.warning("Navigation to %s failed with no usable content: %s", url, exc)
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
            "Navigation to %s redirected to login (%s) — repo likely private/login-required",
            url,
            final_url,
        )
        return False
    return True


def _correct_repo_from_article(repo: RepoReference, source_url: str | None) -> RepoReference | None:
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
            "Repo URL %s looks malformed; skipping direct/retry and consulting source article",
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
            "Repo URL %s still unreachable after %d retries; consulting source article",
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

    logger.warning("Recovery path=fallback: all recovery attempts failed for %s", repo.url)
    return _NavOutcome(repo, "fallback", False)


GENERIC_BACKGROUND_TITLE = "Claracle"
GENERIC_BACKGROUND_SUBTITLE = "Open Source Highlights"


def _render_generic_background(
    page: Page,
    duration_seconds: float,
    capturer: "_Capturer | None" = None,
    *,
    brand_name: str | None = None,
    brand_subtitle: str | None = None,
) -> None:
    """Show the animated branded background for a generic (no-repo) segment.

    The on-screen title is the configured show/site name (``brand_name``, e.g.
    "Claracle") so the card never hardcodes the internal pipeline name — the
    pipeline is config-driven and may serve other sites (issue #559).
    """
    html = GENERIC_BACKGROUND_HTML.format(
        width=WIDTH,
        height=HEIGHT,
        title=(brand_name or "").strip() or GENERIC_BACKGROUND_TITLE,
        subtitle=(brand_subtitle or "").strip() or GENERIC_BACKGROUND_SUBTITLE,
    )
    page.set_content(html)
    if capturer is not None:
        # Abandon any partially-captured scroll frames so the static background
        # is held as a still rather than a truncated motion sequence (#387).
        capturer.reset_frames()
        capturer.still(page)
    else:
        page.wait_for_timeout(int(duration_seconds * 1000))


def _record_generic_segment(
    browser: Browser,
    segment: VideoSegment,
    output_dir: Path,
    *,
    brand_name: str | None = None,
) -> RecordedSegment:
    """Record a generic background segment (no repo).

    When the segment has a ``source_url`` (e.g. the article's weekly page),
    that page is navigated to and scrolled like a regular repo recording;
    otherwise the static branded background animation is shown, titled with the
    configured show/site name (``brand_name``, issue #559).
    """
    context, capturer = _make_recording_context(
        browser, output_dir, segment_label="generic segment"
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
                _dismiss_cookie_consent(page)
                _prepare_page_for_recording(page)
                _smooth_scroll(page, segment.duration_seconds, capturer)
            except Exception:
                logger.exception(
                    "Error recording generic source %s — using background",
                    source_url,
                )
                _render_generic_background(
                    page, segment.duration_seconds, capturer, brand_name=brand_name
                )
        else:
            _render_generic_background(
                page, segment.duration_seconds, capturer, brand_name=brand_name
            )
        dest_path = _finalize_segment(
            page,
            context,
            capturer,
            output_dir,
            "generic",
            segment.duration_seconds,
        )
    except Exception:
        try:
            context.close()
        except Exception:
            pass
        raise

    return RecordedSegment(
        segment=segment,
        video_path=dest_path,
        is_fallback=False,
        has_pages=False,
    )


def _try_record_project_site(
    page: Page,
    repo: RepoReference,
    duration_seconds: float,
    capturer: "_Capturer | None" = None,
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
        _smooth_scroll(page, duration_seconds, capturer)
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
    *,
    brand_name: str | None = None,
) -> RecordedSegment:
    """Record a single video segment for a repo.

    Creates a fresh browser context with video recording, navigates to the
    repo URL, scrolls, and closes the context to finalize the video file.

    When the repo URL fails to load, navigation is retried and validated
    against the episode's source article before falling back to a generic
    branded screen (issue #378). *source_url* is the script header's
    ``Source URL:`` used to recover a corrected repo URL. ``brand_name`` is the
    configured show/site name used to title generic background cards (#559).
    """
    if segment.is_generic:
        return _record_generic_segment(browser, segment, output_dir, brand_name=brand_name)

    repo = segment.repo

    # A planning-time pre-flight check flagged this repo as removed from GitHub
    # (HTTP 404 — e.g. a polymarket/spam bot GitHub took down).  Skip navigation
    # entirely and render a clean "Repo removed" card so no recording time is
    # wasted on a dead URL (issue #394).
    if segment.is_removed:
        logger.info(
            "Repo %s flagged removed (%s); rendering removed card without recording (issue #394)",
            repo.url,
            segment.removed_reason,
        )
        context, capturer = _make_recording_context(browser, output_dir)
        page = context.new_page()
        try:
            _render_removed_card(
                page,
                repo.owner,
                repo.name,
                segment.removed_reason or "This repo was removed from GitHub",
                segment.duration_seconds,
                capturer,
            )
        except Exception:
            logger.exception("Error rendering removed card for %s", repo.url)
        dest_path = _finalize_segment(
            page,
            context,
            capturer,
            output_dir,
            f"{repo.owner}_{repo.name}",
            segment.duration_seconds,
        )
        return RecordedSegment(
            segment=segment,
            video_path=dest_path,
            is_fallback=True,
            is_removed=True,
            recovery_path="removed",
        )

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

    context, capturer = _make_recording_context(
        browser, output_dir, segment_label=f"repo {repo.url}"
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
            pages_url = _try_record_project_site(page, repo, segment.duration_seconds, capturer)
            if pages_url is not None:
                website_url = pages_url
                has_pages = True
                recovery_path = "website"
            else:
                is_fallback = True
                _render_url_card(page, repo.owner, repo.name, segment.duration_seconds, capturer)
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
                    page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
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
                        page.wait_for_load_state("networkidle", timeout=WEBSITE_NAV_TIMEOUT_MS)
                    except Exception:
                        pass
                    page.wait_for_timeout(PAGE_SETTLE_MS)
                    _dismiss_overlays(page)
                    # External sites (unlike github.com) commonly show cookie
                    # consent banners that would overlay the recording (#388).
                    _dismiss_cookie_consent(page)
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
                # README-first scroll for GitHub repo pages; falls back to a
                # plain deterministic scroll for non-GitHub pages / no README
                # (issue #415).
                _scroll_github_readme(page, segment.duration_seconds, capturer)
            except Exception:
                # Keep the successfully recorded repo page; do not render a
                # fallback on top of it (issue #381).
                logger.exception(
                    "Error after successful navigation to %s — keeping the "
                    "recorded repo page (no fallback)",
                    repo.url,
                )
                # A polish step (settle/scroll/website lookup) raised before the
                # recording reached the full segment duration. In screencast
                # mode, hold the already loaded page (best effort) so the clip is
                # long enough for downstream composition/xfade assumptions. In
                # screenshot mode the composition pads frames to the expected
                # count instead, so no real-time wait is needed (issue #387).
                if capturer is None:
                    remaining_ms = int(
                        (segment.duration_seconds - (time.monotonic() - record_start)) * 1000
                    )
                    if remaining_ms > 0:
                        try:
                            page.wait_for_timeout(remaining_ms)
                        except Exception:
                            pass
                elif capturer.count == 0:
                    # The scroll raised before any frame was captured; grab a
                    # single still of the loaded repo page so the segment still
                    # has valid (held) content to compose (issue #387).
                    capturer.still(page)
    except Exception:
        logger.exception("Error recording %s — using fallback", repo.url)
        is_fallback = True
        recovery_path = "fallback"
        _render_fallback_page(page, repo.owner, repo.name, segment.duration_seconds, capturer)

    dest_path = _finalize_segment(
        page,
        context,
        capturer,
        output_dir,
        f"{repo.owner}_{repo.name}",
        segment.duration_seconds,
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
    intermediates=None,
    concurrency: int | None = None,
    brand_name: str | None = None,
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
        intermediates: Optional
            :class:`podcaster.video.intermediates.IntermediateStore` enabling
            per-segment checkpoint/resume against blob storage (issue #410).
            When supplied, each recorded segment is uploaded to blob and a
            restarted job skips recording for any segment already checkpointed.
            ``None`` (default) preserves the legacy record-everything behaviour.
        concurrency: Optional override for the number of browsers recording in
            parallel (issue #479). ``None`` (default) loads
            :data:`PODCASTER_RECORDING_CONCURRENCY` from the environment; ``1``
            forces fully-sequential recording. Values are clamped to the
            RAM-safe pool maximum.
        brand_name: Configured show/site name (e.g. "Claracle") used to title
            generic background cards instead of the internal pipeline name
            (issue #559). ``None`` falls back to the module default.

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

    # Resolve which segments already have a blob checkpoint so the browser is
    # only launched when there is at least one segment left to record (issue
    # #410).  Resumed segments are rebuilt from blob without touching Playwright.
    resumed: dict[int, RecordedSegment] = {}
    if intermediates is not None and getattr(intermediates, "enabled", False):
        for index, segment in enumerate(plan.segments):
            recovered = _resume_recorded_segment(index, segment, output_dir, intermediates)
            if recovered is not None:
                resumed[index] = recovered
        if resumed:
            logger.info(
                "Resuming recording from blob: %d/%d segment(s) already checkpointed",
                len(resumed),
                len(plan.segments),
            )

    needs_browser = len(resumed) < len(plan.segments)

    # ``IntermediateStore.mark`` does a read-modify-write of a single shared
    # manifest blob, so checkpointing must be serialized when several browsers
    # record concurrently (issue #479).
    checkpoint_lock = threading.Lock()

    def _record_one(browser: "Browser", index: int, segment: VideoSegment) -> RecordedSegment:
        """Record + checkpoint a single (non-resumed) segment."""
        logger.info(
            "Recording segment: %s (%.1fs)",
            segment.label,
            segment.duration_seconds,
        )
        recorded = retry_call(
            lambda: _record_segment(
                browser,
                segment,
                output_dir,
                check_accessibility,
                source_url=source_url,
                brand_name=brand_name,
            ),
            attempts=RECORD_TASK_RETRIES,
            description=f"record segment {index} ({segment.label})",
        )
        with checkpoint_lock:
            _checkpoint_recorded_segment(index, recorded, intermediates)
        logger.info(
            "Saved: %s (fallback=%s, pages=%s, website=%s, recovery=%s)",
            recorded.video_path.name,
            recorded.is_fallback,
            recorded.has_pages,
            recorded.website_url,
            recorded.recovery_path,
        )
        return recorded

    def _log_reused(index: int, recovered: RecordedSegment) -> None:
        logger.info(
            "Reused checkpointed segment %d: %s (recovery=%s)",
            index,
            recovered.video_path.name,
            recovered.recovery_path,
        )

    if not needs_browser:
        for index in range(len(plan.segments)):
            recovered = resumed[index]
            result.recorded.append(recovered)
            _log_reused(index, recovered)
        return result

    pool_config = (
        load_recording_pool_config()
        if concurrency is None
        else RecordingPoolConfig(concurrency=max(1, min(concurrency, MAX_RECORDING_CONCURRENCY)))
    )
    pending = [
        (index, segment) for index, segment in enumerate(plan.segments) if index not in resumed
    ]

    def _launch(pw: "Playwright") -> "Browser":
        return pw.chromium.launch(headless=headless, args=RECORDING_CHROMIUM_ARGS)

    if pool_config.parallel and len(pending) > 1:
        # Record the outstanding segments concurrently, each worker driving its
        # own browser; results come back keyed by plan index (issue #479).
        recorded_map = record_segments_parallel(
            pending,
            _record_one,
            _launch,
            pool_config,
            playwright_factory=sync_playwright,
        )
        for index in range(len(plan.segments)):
            recovered = resumed.get(index)
            if recovered is not None:
                result.recorded.append(recovered)
                _log_reused(index, recovered)
            else:
                result.recorded.append(recorded_map[index])
        return result

    # Sequential path: a single browser records every outstanding segment in
    # plan order (concurrency == 1, or only one segment left to record).
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            for index, segment in enumerate(plan.segments):
                recovered = resumed.get(index)
                if recovered is not None:
                    result.recorded.append(recovered)
                    _log_reused(index, recovered)
                    continue
                result.recorded.append(_record_one(browser, index, segment))
        finally:
            browser.close()

    return result
