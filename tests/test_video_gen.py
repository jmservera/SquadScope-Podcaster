"""Tests for podcaster.video.video_gen module.

Unit tests mock Playwright and requests; the integration test class
(marked slow) exercises real Playwright recording against live GitHub.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcaster.video.sync_plan import (
    EpisodePlan,
    RepoReference,
    VideoSegment,
)
from podcaster.video.video_gen import (
    MAX_SCROLL_VIEWPORT_MULTIPLIER,
    RECORDING_CHROMIUM_ARGS,
    SCREENSHOT_CAPTURE_FPS,
    SCREENSHOT_CAPTURE_TUNE,
    SCROLL_TICKS_PER_SEC,
    WIDTH,
    HEIGHT,
    IMAGE_ZOOM_MIN_SIZE_PX,
    PAGE_ZOOM_SCALE,
    ZOOM_PAGE_CSS,
    RecordedSegment,
    RecordingResult,
    _Capturer,
    _build_frames_to_video_cmd,
    _build_still_to_video_cmd,
    _check_gh_pages,
    _check_repo_accessible,
    _compose_screenshot_segment,
    _correct_repo_from_article,
    _dismiss_overlays,
    _dismiss_cookie_consent,
    _extract_website_url,
    _is_github_url,
    _is_login_redirect,
    _is_github_url,
    _page_has_content,
    _looks_malformed_repo_url,
    _page_has_content,
    _make_recording_context,
    _navigate_to_website,
    _navigate_with_recovery,
    _pad_frames,
    _smooth_scroll,
    _scroll_positions,
    _scroll_github_readme,
    _github_scroll_plan,
    _ease_out_cubic,
    _ease_linear,
    READING_PX_PER_FRAME,
    GITHUB_README_TOP_MARGIN,
    _try_navigate_repo,
    _try_record_project_site,
    _PAGE_ZOOM_JS,
    _NEUTRALIZE_FIXED_STICKY_JS,
    _apply_page_zoom,
    _neutralize_fixed_sticky,
    _prepare_page_for_recording,
    _render_fallback_page,
    _render_url_card,
    _render_removed_card,
    _record_segment,
    record_episode,
)


# A tiny valid PNG (even dimensions) so mocked page.screenshot writes real,
# ffmpeg-decodable frame files.
def _valid_png_bytes(width: int = 64, height: int = 64) -> bytes:
    import struct
    import zlib

    raw = bytearray()
    row = bytes([0]) + bytes((20, 20, 30)) * width
    for _ in range(height):
        raw += row

    def _chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


_PNG_64x64 = _valid_png_bytes()


@pytest.fixture
def stub_compose():
    """Replace ffmpeg screenshot composition with a fast stub (no ffmpeg)."""
    def fake(capturer, duration_seconds, output_path):
        Path(output_path).write_bytes(b"\x00\x00\x00\x18ftypmp42stub")
        return Path(output_path)

    with patch(
        "podcaster.video.video_gen._compose_screenshot_segment",
        side_effect=fake,
    ):
        yield


# --- Helpers ---

def _make_segment(
    owner: str = "test-owner",
    name: str = "test-repo",
    start: float = 0.0,
    duration: float = 10.0,
) -> VideoSegment:
    return VideoSegment(
        repo=RepoReference(owner=owner, name=name),
        start_seconds=start,
        duration_seconds=duration,
    )


def _make_plan(*segments: VideoSegment, total: float = 60.0) -> EpisodePlan:
    if not segments:
        segments = (_make_segment(),)
    return EpisodePlan(
        total_duration_seconds=total,
        segments=tuple(segments),
    )


# --- _check_gh_pages tests ---


class TestCheckGhPages:
    @patch("podcaster.video.video_gen.requests.head")
    def test_returns_true_on_200(self, mock_head):
        mock_head.return_value = MagicMock(status_code=200)
        assert _check_gh_pages("owner", "repo") is True

    @patch("podcaster.video.video_gen.requests.head")
    def test_returns_false_on_404(self, mock_head):
        mock_head.return_value = MagicMock(status_code=404)
        assert _check_gh_pages("owner", "repo") is False

    @patch("podcaster.video.video_gen.requests.head")
    def test_returns_false_on_exception(self, mock_head):
        mock_head.side_effect = Exception("network error")
        assert _check_gh_pages("owner", "repo") is False

    @patch("podcaster.video.video_gen.requests.head")
    def test_calls_correct_url(self, mock_head):
        mock_head.return_value = MagicMock(status_code=200)
        _check_gh_pages("myowner", "myrepo")
        mock_head.assert_called_once()
        url = mock_head.call_args[0][0]
        assert url == "https://myowner.github.io/myrepo/"


# --- _check_repo_accessible tests ---


class TestCheckRepoAccessible:
    @patch("podcaster.video.video_gen.requests.head")
    def test_returns_true_on_200(self, mock_head):
        mock_head.return_value = MagicMock(status_code=200)
        assert _check_repo_accessible("https://github.com/o/r") is True

    @patch("podcaster.video.video_gen.requests.head")
    def test_returns_false_on_404(self, mock_head):
        mock_head.return_value = MagicMock(status_code=404)
        assert _check_repo_accessible("https://github.com/o/r") is False

    @patch("podcaster.video.video_gen.requests.head")
    def test_returns_true_on_exception(self, mock_head):
        mock_head.side_effect = Exception("timeout")
        assert _check_repo_accessible("https://github.com/o/r") is True


# --- _dismiss_overlays tests ---


class TestDismissOverlays:
    def test_clicks_visible_overlay(self):
        page = MagicMock()
        el = MagicMock()
        el.is_visible.return_value = True
        page.query_selector.return_value = el
        _dismiss_overlays(page)
        assert el.click.called

    def test_ignores_invisible_overlay(self):
        page = MagicMock()
        el = MagicMock()
        el.is_visible.return_value = False
        page.query_selector.return_value = el
        _dismiss_overlays(page)
        assert not el.click.called

    def test_ignores_missing_overlay(self):
        page = MagicMock()
        page.query_selector.return_value = None
        _dismiss_overlays(page)  # Should not raise


# --- _dismiss_cookie_consent tests (issue #388) ---


class TestDismissCookieConsent:
    def test_clicks_first_visible_framework_selector(self):
        page = MagicMock()
        el = MagicMock()
        el.is_visible.return_value = True
        page.query_selector.return_value = el
        assert _dismiss_cookie_consent(page) is True
        assert el.click.called
        # Generic JS fallback must not run once a selector matched.
        page.evaluate.assert_not_called()

    def test_first_matched_selector_is_onetrust(self):
        page = MagicMock()
        el = MagicMock()
        el.is_visible.return_value = True
        page.query_selector.return_value = el
        _dismiss_cookie_consent(page)
        first_selector = page.query_selector.call_args_list[0].args[0]
        assert first_selector == "#onetrust-accept-btn-handler"

    def test_skips_invisible_then_uses_text_fallback(self):
        page = MagicMock()
        el = MagicMock()
        el.is_visible.return_value = False
        page.query_selector.return_value = el
        page.evaluate.return_value = "accept all"
        assert _dismiss_cookie_consent(page) is True
        assert not el.click.called
        assert page.evaluate.called

    def test_generic_text_fallback_when_no_selector_matches(self):
        page = MagicMock()
        page.query_selector.return_value = None
        page.evaluate.return_value = "ok"
        assert _dismiss_cookie_consent(page) is True
        assert page.evaluate.called

    def test_returns_false_when_no_banner(self):
        page = MagicMock()
        page.query_selector.return_value = None
        page.evaluate.return_value = None
        assert _dismiss_cookie_consent(page) is False

    def test_selector_exceptions_are_swallowed(self):
        page = MagicMock()
        page.query_selector.side_effect = RuntimeError("bad selector")
        page.evaluate.return_value = None
        # Should not raise even though every selector lookup errors.
        assert _dismiss_cookie_consent(page) is False

    def test_evaluate_exception_is_swallowed(self):
        page = MagicMock()
        page.query_selector.return_value = None
        page.evaluate.side_effect = RuntimeError("boom")
        assert _dismiss_cookie_consent(page) is False

    def test_respects_timeout_budget(self):
        page = MagicMock()
        page.query_selector.return_value = None
        page.evaluate.return_value = None
        # A non-positive budget short-circuits before the JS fallback.
        assert _dismiss_cookie_consent(page, timeout_ms=0) is False
        page.evaluate.assert_not_called()


# --- _smooth_scroll tests ---


class TestSmoothScroll:
    def test_scrolls_expected_ticks(self):
        page = MagicMock()
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        # scrollable content: 5000px total, viewport 1080px
        page.evaluate.side_effect = lambda js: (
            5000 if "scrollHeight" in js else None
        )

        duration = 2.0
        _smooth_scroll(page, duration)

        ticks = int(duration * SCROLL_TICKS_PER_SEC)
        # 1 call for scrollHeight + ticks calls for scrollBy
        assert page.evaluate.call_count == ticks + 1
        # One wait per tick, plus an optional trailing wait for the fractional
        # remainder not covered by whole ticks.
        assert page.wait_for_timeout.call_count in (ticks, ticks + 1)

    def test_caps_scroll_distance(self):
        page = MagicMock()
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        # Very long page
        page.evaluate.side_effect = lambda js: (
            100_000 if "scrollHeight" in js else None
        )

        duration = 1.0
        _smooth_scroll(page, duration)

        ticks = int(duration * SCROLL_TICKS_PER_SEC)
        # Deterministic absolute positioning: one scrollTo per tick (issue #413).
        scroll_calls = [
            c for c in page.evaluate.call_args_list if "scrollTo" in str(c)
        ]
        assert len(scroll_calls) == ticks

        # Parse the target Y of each scrollTo and verify the per-frame step never
        # exceeds the reading-speed cap (no steppy jumps) and motion is forward.
        ys = []
        for call in scroll_calls:
            m = re.search(r"scrollTo\(0,\s*(\d+)\)", call[0][0])
            assert m is not None
            ys.append(int(m.group(1)))
        assert ys == sorted(ys)  # monotonic, no backwards jumps
        for prev, cur in zip(ys, ys[1:]):
            assert cur - prev <= READING_PX_PER_FRAME
        # Total distance is bounded by the reading cap, not the 2.5×viewport cap.
        # The derived path uses the configurable READING_PX_PER_FRAME default
        # (issue #413), not the hard MAX_READING_PX_PER_FRAME ceiling.
        assert ys[-1] <= ticks * READING_PX_PER_FRAME

    def test_reading_cap_honours_max_px_per_frame_argument(self):
        # The derived-scroll path must respect the caller-supplied per-frame cap
        # (it previously ignored it and always used the 10px hard ceiling).
        page = MagicMock()
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        page.evaluate.side_effect = lambda js: (
            100_000 if "scrollHeight" in js else None
        )

        duration = 1.0
        _smooth_scroll(page, duration, max_px_per_frame=3)

        scroll_calls = [
            c for c in page.evaluate.call_args_list if "scrollTo" in str(c)
        ]
        ys = []
        for call in scroll_calls:
            m = re.search(r"scrollTo\(0,\s*(\d+)\)", call[0][0])
            assert m is not None
            ys.append(int(m.group(1)))
        for prev, cur in zip(ys, ys[1:]):
            assert cur - prev <= 3

    def test_no_scroll_on_short_page(self):
        page = MagicMock()
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        # Page fits in viewport
        page.evaluate.side_effect = lambda js: (
            HEIGHT if "scrollHeight" in js else None
        )

        _smooth_scroll(page, 2.0)

        # Should wait instead of scrolling
        page.wait_for_timeout.assert_called_once_with(2000)

    def test_zero_duration(self):
        page = MagicMock()
        _smooth_scroll(page, 0.0)
        page.evaluate.assert_not_called()

    def test_short_duration_waits_without_scrolling(self):
        """Duration > 0 but < 1 tick should still wait the full duration."""
        page = MagicMock()
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        page.evaluate.side_effect = lambda js: (
            5000 if "scrollHeight" in js else None
        )

        _smooth_scroll(page, 0.02)  # too short for a full tick

        # Should not call scrollHeight (returns before evaluating page)
        page.evaluate.assert_not_called()
        # Should wait for the full duration
        page.wait_for_timeout.assert_called_once_with(20)


# --- Deterministic frame-indexed scrolling (issue #413) ---


class TestScrollPositions:
    def test_count_matches_frames(self):
        assert len(_scroll_positions(0, 1000, 50)) == 50

    def test_starts_and_ends_exact(self):
        positions = _scroll_positions(100, 900, 30)
        assert positions[0] == 100
        assert positions[-1] == 900

    def test_linear_is_evenly_spaced(self):
        positions = _scroll_positions(0, 290, 30)  # ~10px/frame
        deltas = [b - a for a, b in zip(positions, positions[1:])]
        # Even spacing: every step within 1px of every other (rounding only).
        assert max(deltas) - min(deltas) <= 1

    def test_monotonic_non_decreasing(self):
        positions = _scroll_positions(0, 500, 40)
        assert positions == sorted(positions)

    def test_single_frame_returns_start(self):
        assert _scroll_positions(123, 999, 1) == [123]

    def test_zero_frames_empty(self):
        assert _scroll_positions(0, 100, 0) == []

    def test_ease_out_cubic_decelerates(self):
        positions = _scroll_positions(0, 1000, 50, easing="ease_out_cubic")
        deltas = [b - a for a, b in zip(positions, positions[1:])]
        # easeOutCubic: starts fast, ends slow.
        assert deltas[0] > deltas[-1]
        assert positions == sorted(positions)
        assert positions[-1] == 1000

    def test_unknown_easing_falls_back_to_linear(self):
        assert _scroll_positions(0, 100, 11, easing="nope") == _scroll_positions(
            0, 100, 11, easing="linear"
        )


class TestEasingFunctions:
    def test_linear_endpoints(self):
        assert _ease_linear(0.0) == 0.0
        assert _ease_linear(1.0) == 1.0

    def test_ease_out_cubic_endpoints(self):
        assert _ease_out_cubic(0.0) == 0.0
        assert _ease_out_cubic(1.0) == 1.0

    def test_ease_out_cubic_front_loaded(self):
        # At the midpoint easeOutCubic is already well past halfway (fast start).
        assert _ease_out_cubic(0.5) > 0.5


# --- README-first scroll for GitHub repos (issue #415) ---


class TestGithubScrollPlan:
    def test_returns_exact_frame_count(self):
        plan = _github_scroll_plan(8000, HEIGHT, 12000, 300)
        assert plan is not None
        assert len(plan) == 300

    def test_holds_on_header_then_jumps_then_reads(self):
        plan = _github_scroll_plan(8000, HEIGHT, 12000, 300)
        assert plan is not None
        # Header hold: opening frames stay at the top.
        assert plan[0] == 0
        assert plan[5] == 0
        # Ends deeper in the README than where the jump landed.
        assert plan[-1] > 8000 - GITHUB_README_TOP_MARGIN - 1

    def test_monotonic_non_decreasing(self):
        plan = _github_scroll_plan(6000, HEIGHT, 9000, 240)
        assert plan == sorted(plan)

    def test_reading_phase_at_reading_speed(self):
        plan = _github_scroll_plan(5000, HEIGHT, 50000, 300)
        assert plan is not None
        # The largest per-frame step in the reading tail stays within the
        # reading-speed cap (no steppy crawl through README content).  The span
        # is sized off the interval count so the cap holds exactly (issue #415).
        tail = plan[-100:]
        deltas = [b - a for a, b in zip(tail, tail[1:])]
        assert max(deltas) <= READING_PX_PER_FRAME

    def test_does_not_exceed_scrollable(self):
        plan = _github_scroll_plan(5000, HEIGHT, 5200, 300)
        assert plan is not None
        assert max(plan) <= 5200

    def test_too_few_frames_returns_none(self):
        # Not enough frames to stage header + jump + reading (header == 0).
        assert _github_scroll_plan(8000, HEIGHT, 12000, 2) is None
        assert _github_scroll_plan(8000, HEIGHT, 12000, 0) == []


class TestScrollGithubReadme:
    def _page(self, *, url="https://github.com/owner/repo", readme_y=8000,
              scrollable=12000):
        page = MagicMock()
        page.url = url
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}

        def _eval(js, *args):
            if "readmeY" in js:
                return {"readmeY": readme_y, "scrollable": scrollable}
            if "scrollHeight" in js:
                return scrollable + HEIGHT
            return None

        page.evaluate.side_effect = _eval
        return page

    def test_non_github_falls_back_to_smooth_scroll(self):
        page = self._page(url="https://example.com/page")
        with patch(
            "podcaster.video.video_gen._smooth_scroll"
        ) as smooth:
            _scroll_github_readme(page, 5.0)
        smooth.assert_called_once()

    def test_no_readme_falls_back(self):
        page = MagicMock()
        page.url = "https://github.com/owner/repo"
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        page.evaluate.side_effect = lambda js, *a: (
            {"readmeY": None, "scrollable": 9000} if "readmeY" in js else None
        )
        with patch("podcaster.video.video_gen._smooth_scroll") as smooth:
            _scroll_github_readme(page, 5.0)
        smooth.assert_called_once()

    def test_readme_near_top_falls_back(self):
        page = self._page(readme_y=100)
        with patch("podcaster.video.video_gen._smooth_scroll") as smooth:
            _scroll_github_readme(page, 5.0)
        smooth.assert_called_once()

    def test_github_readme_uses_staged_scroll_capture(self, tmp_path):
        page = self._page()
        # Drive a real screenshot capturer so we assert on the staged plan.
        cap = _Capturer(tmp_path / "frames")

        def _screenshot(path):
            Path(path).write_bytes(_PNG_64x64)

        page.screenshot.side_effect = _screenshot

        _scroll_github_readme(page, 5.0, capturer=cap)

        # One frame per output frame at the capture fps.
        assert cap.count == int(5.0 * SCREENSHOT_CAPTURE_FPS)
        # The staged flow uses absolute scrollTo positioning.
        scroll_calls = [
            c for c in page.evaluate.call_args_list if "scrollTo" in str(c)
        ]
        assert scroll_calls, "expected scrollTo calls from staged plan"
        # Early frames hold on the header (y==0).
        first = re.search(r"scrollTo\(0,\s*(\d+)\)", str(scroll_calls[0]))
        assert first is not None and int(first.group(1)) == 0

    def _header_hold_frames(self, fps, duration, tmp_path):
        page = self._page()
        cap = _Capturer(tmp_path / "frames")
        page.screenshot.side_effect = lambda path: Path(path).write_bytes(_PNG_64x64)
        with patch("podcaster.video.video_gen.SCREENSHOT_CAPTURE_FPS", fps):
            _scroll_github_readme(page, duration, capturer=cap)
        ys = [
            int(m.group(1))
            for c in page.evaluate.call_args_list
            if (m := re.search(r"scrollTo\(0,\s*(\d+)\)", str(c)))
        ]
        # Count the leading header-hold frames (y == 0).  The eased jump's first
        # frame is also at y==0 (ease(0)==0), so subtract that single boundary
        # frame to recover the pure header-hold count.
        hold = 0
        for y in ys:
            if y != 0:
                break
            hold += 1
        return hold - 1

    def test_header_hold_duration_is_fps_stable(self, tmp_path):
        # The header-hold budget is specified in seconds, so its frame count must
        # scale with the capture rate (issue #415): doubling fps doubles frames
        # but keeps the ~2.0s wall-clock hold constant.
        from podcaster.video.video_gen import GITHUB_HEADER_HOLD_SECONDS

        hold_30 = self._header_hold_frames(30, 10.0, tmp_path / "a")
        hold_60 = self._header_hold_frames(60, 10.0, tmp_path / "b")
        assert hold_30 == round(GITHUB_HEADER_HOLD_SECONDS * 30)
        assert hold_60 == round(GITHUB_HEADER_HOLD_SECONDS * 60)


# --- _render_url_card tests (issue #386) ---


class TestRenderUrlCard:
    def test_sets_content_and_waits(self):
        page = MagicMock()
        _render_url_card(page, "owner", "repo", 5.0)
        page.set_content.assert_called_once()
        html = page.set_content.call_args[0][0]
        assert "owner/repo" in html
        # The clean URL card shows the repo URL, never an "unavailable" message.
        assert "github.com/owner/repo" in html
        assert "unavailable" not in html.lower()
        page.wait_for_timeout.assert_called_once_with(5000)

    def test_fallback_alias_points_to_url_card(self):
        assert _render_fallback_page is _render_url_card


# --- _render_removed_card tests (issue #394) ---


class TestRenderRemovedCard:
    def test_sets_content_and_waits(self):
        page = MagicMock()
        _render_removed_card(
            page, "owner", "repo", "This repo was removed from GitHub", 4.0
        )
        page.set_content.assert_called_once()
        html = page.set_content.call_args[0][0]
        assert "owner/repo" in html
        assert "github.com/owner/repo" in html
        # The card explicitly states the repo was removed (not "unavailable").
        assert "removed from GitHub" in html
        assert "unavailable" not in html.lower()
        page.wait_for_timeout.assert_called_once_with(4000)

    def test_screenshot_mode_captures_still(self):
        page = MagicMock()
        capturer = MagicMock()
        _render_removed_card(
            page, "o", "r", "This repo was removed from GitHub", 4.0, capturer
        )
        capturer.reset_frames.assert_called_once()
        capturer.still.assert_called_once_with(page)
        page.wait_for_timeout.assert_not_called()


# --- _is_login_redirect tests (issue #386) ---


class TestIsLoginRedirect:
    def test_detects_login_path(self):
        assert _is_login_redirect(
            "https://github.com/login?return_to=%2Fowner%2Frepo"
        ) is True

    def test_detects_session_path(self):
        assert _is_login_redirect("https://github.com/session") is True

    def test_normal_repo_is_not_redirect(self):
        assert _is_login_redirect("https://github.com/owner/repo") is False

    def test_login_in_repo_name_is_not_redirect(self):
        # A repo literally named "login" must not be treated as a redirect.
        assert _is_login_redirect("https://github.com/owner/login") is False

    def test_none_is_false(self):
        assert _is_login_redirect(None) is False


# --- _apply_page_zoom tests (issue #395) ---


class TestApplyPageZoom:
    def test_applies_full_page_zoom_and_injects_css(self):
        page = MagicMock()
        page.evaluate.return_value = 1  # focal image found, zoom applied

        result = _apply_page_zoom(page)

        assert result == 1
        # JS detection ran with the size threshold
        page.evaluate.assert_called_once()
        args = page.evaluate.call_args[0]
        assert IMAGE_ZOOM_MIN_SIZE_PX in args
        # Full-page zoom CSS was injected
        page.add_style_tag.assert_called_once_with(content=ZOOM_PAGE_CSS)

    def test_no_focal_image_skips_css(self):
        page = MagicMock()
        page.evaluate.return_value = 0

        result = _apply_page_zoom(page)

        assert result == 0
        page.add_style_tag.assert_not_called()

    def test_evaluate_error_is_swallowed(self):
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError("boom")

        assert _apply_page_zoom(page) == 0
        page.add_style_tag.assert_not_called()

    def test_add_style_tag_error_is_swallowed(self):
        page = MagicMock()
        page.evaluate.return_value = 1
        page.add_style_tag.side_effect = RuntimeError("boom")

        assert _apply_page_zoom(page) == 0

    def test_css_zooms_whole_page_no_bounce(self):
        # Must use !important to beat the universal animation:none rule, and the
        # body.ss-page-zoom selector for higher specificity.
        assert "!important" in ZOOM_PAGE_CSS
        assert "body.ss-page-zoom" in ZOOM_PAGE_CSS
        # Scales the whole page from 1 up to the configured peak.
        assert "scale(1)" in ZOOM_PAGE_CSS
        assert f"scale({PAGE_ZOOM_SCALE})" in ZOOM_PAGE_CSS
        # Smooth single zoom-in that holds — no bounce/rebound.
        assert "ease-in-out" in ZOOM_PAGE_CSS
        assert "forwards" in ZOOM_PAGE_CSS
        assert "alternate" not in ZOOM_PAGE_CSS

    def test_js_anchors_origin_on_body(self):
        # The page zoom transforms the body (whole viewport), not an isolated img.
        assert "document.body" in _PAGE_ZOOM_JS
        assert "transformOrigin" in _PAGE_ZOOM_JS
        assert "ss-page-zoom" in _PAGE_ZOOM_JS

    def test_prepare_page_applies_zoom(self):
        page = MagicMock()
        page.evaluate.return_value = 1
        _prepare_page_for_recording(page)
        # Both anti-flash CSS and full-page zoom CSS injected
        injected = [
            c.kwargs.get("content", "") for c in page.add_style_tag.call_args_list
        ]
        assert any("ss-page-zoom" in css for css in injected)


# --- _neutralize_fixed_sticky tests (issue #406) ---


class TestNeutralizeFixedSticky:
    def test_js_targets_fixed_and_sticky(self):
        # The injected JS detects both fixed and sticky positioning and forces
        # them static so headers stop bouncing during the scroll capture.
        assert "fixed" in _NEUTRALIZE_FIXED_STICKY_JS
        assert "sticky" in _NEUTRALIZE_FIXED_STICKY_JS
        assert "getComputedStyle" in _NEUTRALIZE_FIXED_STICKY_JS
        assert "static" in _NEUTRALIZE_FIXED_STICKY_JS

    def test_returns_count_from_evaluate(self):
        page = MagicMock()
        page.evaluate.return_value = 3
        assert _neutralize_fixed_sticky(page) == 3
        page.evaluate.assert_called_once_with(_NEUTRALIZE_FIXED_STICKY_JS)

    def test_returns_zero_when_none_found(self):
        page = MagicMock()
        page.evaluate.return_value = 0
        assert _neutralize_fixed_sticky(page) == 0

    def test_swallows_evaluate_errors(self):
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError("boom")
        # Best-effort: never raises, returns 0.
        assert _neutralize_fixed_sticky(page) == 0

    def test_prepare_page_neutralizes_before_scroll(self):
        page = MagicMock()
        page.evaluate.return_value = 2
        _prepare_page_for_recording(page)
        # The neutralize JS must be evaluated as part of page preparation, which
        # runs before _smooth_scroll in every recording flow.
        evaluated = [c.args[0] for c in page.evaluate.call_args_list if c.args]
        assert _NEUTRALIZE_FIXED_STICKY_JS in evaluated


# --- _extract_website_url / _navigate_to_website tests ---


class TestExtractWebsiteUrl:
    def test_returns_url_from_evaluate(self):
        page = MagicMock()
        page.evaluate.return_value = "https://claracle.com"
        assert _extract_website_url(page) == "https://claracle.com"

    def test_strips_whitespace(self):
        page = MagicMock()
        page.evaluate.return_value = "  https://nextjs.org  "
        assert _extract_website_url(page) == "https://nextjs.org"

    def test_returns_none_when_no_link(self):
        page = MagicMock()
        page.evaluate.return_value = None
        assert _extract_website_url(page) is None

    def test_returns_none_on_empty_string(self):
        page = MagicMock()
        page.evaluate.return_value = "   "
        assert _extract_website_url(page) is None

    def test_returns_none_on_exception(self):
        page = MagicMock()
        page.evaluate.side_effect = Exception("eval failed")
        assert _extract_website_url(page) is None


class TestNavigateToWebsite:
    def test_returns_true_on_success(self):
        page = MagicMock()
        page.goto.return_value = MagicMock(status=200)
        assert _navigate_to_website(page, "https://example.com") is True
        page.goto.assert_called_once()

    def test_returns_true_when_no_response(self):
        page = MagicMock()
        page.goto.return_value = None
        assert _navigate_to_website(page, "https://example.com") is True

    def test_returns_false_on_http_error(self):
        page = MagicMock()
        page.goto.return_value = MagicMock(status=503)
        assert _navigate_to_website(page, "https://example.com") is False

    def test_returns_false_on_exception(self):
        page = MagicMock()
        page.goto.side_effect = Exception("timeout")
        assert _navigate_to_website(page, "https://example.com") is False


# --- _record_segment tests (mocked Playwright) ---


@pytest.mark.usefixtures("stub_compose")
class TestRecordSegment:
    def _mock_browser(self, tmp_path: Path) -> tuple[MagicMock, Path]:
        """Create a mock browser that produces a fake video file."""
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        video = MagicMock()

        browser.new_context.return_value = context
        context.new_page.return_value = page
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        page.evaluate.side_effect = lambda js: (
            2000 if "scrollHeight" in js else None
        )

        # Create a fake video file that Playwright would produce
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        fake_video = raw_dir / "abc123.webm"
        fake_video.write_bytes(b"\x1a\x45\xdf\xa3")  # WebM magic bytes

        video.path.return_value = str(fake_video)
        page.video = video

        return browser, tmp_path

    def test_records_accessible_repo(self, tmp_path):
        browser, out_dir = self._mock_browser(tmp_path)
        segment = _make_segment(owner="microsoft", name="vscode", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False):
            result = _record_segment(browser, segment, out_dir)

        assert result.video_path.exists()
        assert result.video_path.name.startswith("microsoft_vscode_")
        assert result.video_path.name.endswith(".mp4")
        assert result.is_fallback is False
        page = browser.new_context.return_value.new_page.return_value
        page.goto.assert_called_once()

    def test_fallback_on_404(self, tmp_path):
        browser, out_dir = self._mock_browser(tmp_path)
        page = browser.new_context.return_value.new_page.return_value
        # Every navigation returns 404 and no source article is available, so
        # all recovery paths fail and we render the clean URL card (#386).
        page.goto.return_value = MagicMock(status=404)
        segment = _make_segment(owner="gone", name="repo", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=False), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False):
            result = _record_segment(browser, segment, out_dir)

        assert result.is_fallback is True
        assert result.recovery_path == "fallback"
        assert result.video_path.exists()
        # The card shows the URL, never an "unavailable" message (#386).
        card_html = next(
            c.args[0]
            for c in page.set_content.call_args_list
            if "gone/repo" in c.args[0]
        )
        assert "unavailable" not in card_html.lower()

    def test_404_records_github_pages_site(self, tmp_path):
        # When the repo page 404s but the project has a GitHub Pages site, we
        # record that site instead of showing the URL card (issue #386).
        browser, out_dir = self._mock_browser(tmp_path)
        page = browser.new_context.return_value.new_page.return_value
        page.goto.return_value = MagicMock(status=404)
        segment = _make_segment(owner="proj", name="site", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=False), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=True), \
             patch("podcaster.video.video_gen._navigate_to_website", return_value=True):
            result = _record_segment(browser, segment, out_dir)

        assert result.is_fallback is False
        assert result.recovery_path == "website"
        assert result.has_pages is True
        assert result.website_url == "https://proj.github.io/site/"
        assert result.video_path.exists()

    def test_removed_repo_skips_navigation_and_renders_card(self, tmp_path):
        # A planning-time pre-flight flagged the repo as removed (issue #394):
        # no navigation is attempted and a "Repo removed" card is rendered.
        browser, out_dir = self._mock_browser(tmp_path)
        page = browser.new_context.return_value.new_page.return_value
        segment = VideoSegment(
            repo=RepoReference(owner="someuser", name="mktail"),
            start_seconds=0.0,
            duration_seconds=2.0,
            removed_reason="This repo was removed from GitHub",
        )

        with patch("podcaster.video.video_gen._check_repo_accessible") as acc, \
             patch("podcaster.video.video_gen._check_gh_pages") as pages:
            result = _record_segment(browser, segment, out_dir)

        # No accessibility checks, no GitHub Pages probe, no navigation.
        acc.assert_not_called()
        pages.assert_not_called()
        page.goto.assert_not_called()

        assert result.is_removed is True
        assert result.is_fallback is True
        assert result.recovery_path == "removed"
        assert result.video_path.exists()
        assert result.video_path.name.startswith("someuser_mktail_")
        card_html = next(
            c.args[0]
            for c in page.set_content.call_args_list
            if "someuser/mktail" in c.args[0]
        )
        assert "removed from GitHub" in card_html
        assert "unavailable" not in card_html.lower()

    def test_fallback_on_navigation_error(self, tmp_path):
        browser, out_dir = self._mock_browser(tmp_path)
        page = browser.new_context.return_value.new_page.return_value
        page.goto.side_effect = Exception("timeout")

        segment = _make_segment(owner="slow", name="repo", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False):
            result = _record_segment(browser, segment, out_dir)

        assert result.is_fallback is True
        assert result.video_path.exists()

    def test_detects_gh_pages(self, tmp_path):
        browser, out_dir = self._mock_browser(tmp_path)
        segment = _make_segment(owner="pages-owner", name="pages-repo", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=True):
            result = _record_segment(browser, segment, out_dir)

        assert result.has_pages is True

    def test_skip_accessibility_check(self, tmp_path):
        browser, out_dir = self._mock_browser(tmp_path)
        segment = _make_segment(duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible") as mock_check, \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False):
            _record_segment(browser, segment, out_dir, check_accessibility=False)

        mock_check.assert_not_called()

    def test_context_uses_dark_mode(self, tmp_path):
        browser, out_dir = self._mock_browser(tmp_path)
        segment = _make_segment(duration=1.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False):
            _record_segment(browser, segment, out_dir)

        call_kwargs = browser.new_context.call_args[1]
        assert call_kwargs["color_scheme"] == "dark"
        assert call_kwargs["viewport"] == {"width": WIDTH, "height": HEIGHT}
        # Screenshot mode (default) records no Playwright screencast (#387).
        assert "record_video_size" not in call_kwargs
        assert "record_video_dir" not in call_kwargs

    def test_context_screencast_mode_records_video(self, tmp_path):
        # With screenshot capture disabled the legacy screencast context is used.
        browser, out_dir = self._mock_browser(tmp_path)
        segment = _make_segment(duration=1.0)

        with patch("podcaster.video.video_gen.SCREENSHOT_CAPTURE_ENABLED", False), \
             patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False):
            result = _record_segment(browser, segment, out_dir)

        call_kwargs = browser.new_context.call_args[1]
        assert call_kwargs["color_scheme"] == "dark"
        assert call_kwargs["record_video_size"] == {"width": WIDTH, "height": HEIGHT}
        assert result.video_path.name.endswith(".webm")

    def test_navigates_to_website_when_available(self, tmp_path):
        browser, out_dir = self._mock_browser(tmp_path)
        page = browser.new_context.return_value.new_page.return_value
        # First goto is GitHub, second is the website — both succeed.
        page.goto.return_value = MagicMock(status=200)
        segment = _make_segment(owner="jmservera", name="SquadScope", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False), \
             patch("podcaster.video.video_gen._extract_website_url",
                   return_value="https://claracle.com"):
            result = _record_segment(browser, segment, out_dir)

        assert result.website_url == "https://claracle.com"
        assert result.is_fallback is False
        # Two navigations: GitHub page, then the website.
        assert page.goto.call_count == 2
        assert page.goto.call_args_list[1][0][0] == "https://claracle.com"

    def test_no_website_records_github(self, tmp_path):
        browser, out_dir = self._mock_browser(tmp_path)
        page = browser.new_context.return_value.new_page.return_value
        page.goto.return_value = MagicMock(status=200)
        segment = _make_segment(owner="microsoft", name="vscode", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False), \
             patch("podcaster.video.video_gen._extract_website_url", return_value=None):
            result = _record_segment(browser, segment, out_dir)

        assert result.website_url is None
        assert page.goto.call_count == 1

    def test_falls_back_to_github_when_website_fails(self, tmp_path):
        browser, out_dir = self._mock_browser(tmp_path)
        page = browser.new_context.return_value.new_page.return_value
        page.goto.return_value = MagicMock(status=200)
        segment = _make_segment(owner="jmservera", name="SquadScope", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False), \
             patch("podcaster.video.video_gen._extract_website_url",
                   return_value="https://broken.example"), \
             patch("podcaster.video.video_gen._navigate_to_website", return_value=False):
            result = _record_segment(browser, segment, out_dir)

        # Website failed to load — record the GitHub page, no website recorded.
        assert result.website_url is None
        assert result.is_fallback is False
        assert result.video_path.exists()


@pytest.mark.usefixtures("stub_compose")
class TestRecordGenericSegment:
    def _mock_browser(self, tmp_path: Path):
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        video = MagicMock()
        browser.new_context.return_value = context
        context.new_page.return_value = page
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        page.evaluate.side_effect = lambda js: (
            2000 if "scrollHeight" in js else None
        )
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        fake_video = raw_dir / "generic.webm"
        fake_video.write_bytes(b"\x1a\x45\xdf\xa3")
        video.path.return_value = str(fake_video)
        page.video = video
        return browser, page, tmp_path

    def test_static_background_when_no_source_url(self, tmp_path):
        browser, page, out_dir = self._mock_browser(tmp_path)
        segment = VideoSegment(repo=None, start_seconds=0.0, duration_seconds=2.0)

        result = _record_segment(browser, segment, out_dir)

        assert result.video_path.exists()
        # Static background uses set_content, never navigates.
        page.set_content.assert_called_once()
        page.goto.assert_not_called()

    def test_navigates_to_source_url(self, tmp_path):
        browser, page, out_dir = self._mock_browser(tmp_path)
        segment = VideoSegment(
            repo=None,
            source_url="https://claracle.com/weekly/2026/W26/",
            start_seconds=0.0,
            duration_seconds=2.0,
        )

        result = _record_segment(browser, segment, out_dir)

        assert result.video_path.exists()
        # Source URL is navigated to and scrolled, not the static background.
        page.goto.assert_called_once()
        assert page.goto.call_args[0][0] == "https://claracle.com/weekly/2026/W26/"
        # The only set_content call is the dark hold frame painted before
        # navigation (issue #355); the static background is never rendered.
        from podcaster.video.video_gen import DARK_HOLD_HTML
        page.set_content.assert_called_once_with(DARK_HOLD_HTML)

    def test_falls_back_to_background_on_nav_error(self, tmp_path):
        browser, page, out_dir = self._mock_browser(tmp_path)
        page.goto.side_effect = Exception("timeout")
        segment = VideoSegment(
            repo=None,
            source_url="https://claracle.com/weekly/2026/W26/",
            start_seconds=0.0,
            duration_seconds=2.0,
        )

        result = _record_segment(browser, segment, out_dir)

        assert result.video_path.exists()
        page.goto.assert_called_once()
        # On failure it renders the static background. set_content is called
        # twice: once for the dark hold frame, once for the background.
        from podcaster.video.video_gen import DARK_HOLD_HTML
        assert page.set_content.call_count == 2
        contents = [c.args[0] for c in page.set_content.call_args_list]
        assert contents[0] == DARK_HOLD_HTML
        assert "SquadScope" in contents[1]


# --- repo URL recovery tests (issue #378) ---


class TestLooksMalformedRepoUrl:
    def test_valid_github_url(self):
        assert _looks_malformed_repo_url("https://github.com/vercel/eve") is False

    def test_valid_with_dots_and_dashes(self):
        assert (
            _looks_malformed_repo_url(
                "https://github.com/astral-sh/ruff.rs"
            )
            is False
        )

    def test_empty_url(self):
        assert _looks_malformed_repo_url("") is True

    def test_non_http_scheme(self):
        assert _looks_malformed_repo_url("ftp://github.com/a/b") is True

    def test_non_github_host(self):
        assert _looks_malformed_repo_url("https://example.com/a/b") is True

    def test_missing_repo_name(self):
        assert _looks_malformed_repo_url("https://github.com/vercel") is True

    def test_percent_encoding_in_path(self):
        assert (
            _looks_malformed_repo_url("https://github.com/vercel/e%20ve") is True
        )


class TestTryNavigateRepo:
    def test_returns_true_on_success(self):
        page = MagicMock()
        page.goto.return_value = MagicMock(status=200)
        assert _try_navigate_repo(page, "https://github.com/a/b") is True
        page.goto.assert_called_once()

    def test_returns_true_when_no_response(self):
        page = MagicMock()
        page.goto.return_value = None
        assert _try_navigate_repo(page, "https://github.com/a/b") is True

    def test_returns_false_on_404(self):
        page = MagicMock()
        page.goto.return_value = MagicMock(status=404)
        assert _try_navigate_repo(page, "https://github.com/a/b") is False

    def test_returns_false_on_500(self):
        page = MagicMock()
        page.goto.return_value = MagicMock(status=503)
        assert _try_navigate_repo(page, "https://github.com/a/b") is False

    def test_returns_false_on_exception(self):
        # A timeout/exception with NO usable content on the page must still be
        # treated as a failure (issue #405).
        page = MagicMock()
        page.goto.side_effect = Exception("timeout")
        page.url = "https://github.com/a/b"
        page.evaluate.return_value = False  # no content selectors present
        assert _try_navigate_repo(page, "https://github.com/a/b") is False

    def test_returns_true_on_timeout_with_content(self):
        # networkidle never settled, but the page rendered usable content — we
        # record it instead of falling back to a URL card (issue #405).
        page = MagicMock()
        page.goto.side_effect = Exception("Timeout 60000ms exceeded")
        page.url = "https://github.com/a/b"
        page.evaluate.return_value = True  # .repository-content / main present
        assert _try_navigate_repo(page, "https://github.com/a/b") is True

    def test_returns_false_on_timeout_login_page(self):
        # A timeout that lands on the login wall is a real failure even if the
        # login page itself has a <main> element (issue #405).
        page = MagicMock()
        page.goto.side_effect = Exception("timeout")
        page.url = "https://github.com/login?return_to=%2Fa%2Fb"
        page.evaluate.return_value = True
        assert _try_navigate_repo(page, "https://github.com/a/b") is False
        # We must not even consult content for a login redirect.
        page.evaluate.assert_not_called()

    def test_github_url_uses_longer_timeout(self):
        from podcaster.video.video_gen import GITHUB_NETWORK_IDLE_TIMEOUT_MS

        page = MagicMock()
        page.goto.return_value = MagicMock(status=200)
        page.url = "https://github.com/a/b"
        _try_navigate_repo(page, "https://github.com/a/b")
        assert (
            page.goto.call_args.kwargs["timeout"]
            == GITHUB_NETWORK_IDLE_TIMEOUT_MS
        )

    def test_non_github_url_uses_default_timeout(self):
        from podcaster.video.video_gen import NETWORK_IDLE_TIMEOUT_MS

        page = MagicMock()
        page.goto.return_value = MagicMock(status=200)
        page.url = "https://example.com/a/b"
        _try_navigate_repo(page, "https://example.com/a/b")
        assert (
            page.goto.call_args.kwargs["timeout"] == NETWORK_IDLE_TIMEOUT_MS
        )

    def test_returns_false_on_login_redirect(self):
        # A 200 that lands on the login page means the repo is private /
        # login-required and must be treated as a failure (issue #386).
        page = MagicMock()
        page.goto.return_value = MagicMock(status=200)
        page.url = "https://github.com/login?return_to=%2Fa%2Fb"
        assert _try_navigate_repo(page, "https://github.com/a/b") is False

    def test_returns_true_on_normal_final_url(self):
        page = MagicMock()
        page.goto.return_value = MagicMock(status=200)
        page.url = "https://github.com/a/b"
        assert _try_navigate_repo(page, "https://github.com/a/b") is True


class TestIsGithubUrl:
    def test_bare_github(self):
        assert _is_github_url("https://github.com/a/b") is True

    def test_subdomain_github(self):
        assert _is_github_url("https://gist.github.com/a/b") is True

    def test_non_github(self):
        assert _is_github_url("https://example.com/a/b") is False

    def test_lookalike_not_matched(self):
        # A host that merely contains "github.com" as a substring must not match.
        assert _is_github_url("https://github.com.evil.test/a/b") is False

    def test_none_and_garbage(self):
        assert _is_github_url(None) is False
        assert _is_github_url("not a url") is False


class TestPageHasContent:
    def test_true_when_selector_found(self):
        page = MagicMock()
        page.evaluate.return_value = True
        assert _page_has_content(page) is True

    def test_false_when_selector_absent(self):
        page = MagicMock()
        page.evaluate.return_value = False
        assert _page_has_content(page) is False

    def test_false_on_evaluate_error(self):
        page = MagicMock()
        page.evaluate.side_effect = Exception("execution context destroyed")
        assert _page_has_content(page) is False


class TestTryRecordProjectSite:
    def test_returns_none_when_no_pages(self):
        page = MagicMock()
        repo = RepoReference("o", "r")
        with patch(
            "podcaster.video.video_gen._check_gh_pages", return_value=False
        ):
            assert _try_record_project_site(page, repo, 2.0) is None
        page.goto.assert_not_called()

    def test_returns_none_when_pages_fails_to_load(self):
        page = MagicMock()
        repo = RepoReference("o", "r")
        with patch(
            "podcaster.video.video_gen._check_gh_pages", return_value=True
        ), patch(
            "podcaster.video.video_gen._navigate_to_website", return_value=False
        ):
            assert _try_record_project_site(page, repo, 2.0) is None

    def test_records_and_returns_pages_url(self):
        page = MagicMock()
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        page.evaluate.side_effect = lambda js: 2000 if "scrollHeight" in js else None
        repo = RepoReference("o", "r")
        with patch(
            "podcaster.video.video_gen._check_gh_pages", return_value=True
        ), patch(
            "podcaster.video.video_gen._navigate_to_website", return_value=True
        ):
            url = _try_record_project_site(page, repo, 2.0)
        assert url == "https://o.github.io/r/"
    _ARTICLE = "https://claracle.com/weekly/2026/w26/"

    def test_returns_none_without_source_url(self):
        repo = RepoReference("vercel", "eve")
        assert _correct_repo_from_article(repo, None) is None

    @patch("podcaster.video.video_gen.fetch_repos_from_article", return_value=[])
    def test_returns_none_when_article_has_no_repos(self, mock_fetch):
        repo = RepoReference("vercel", "eve")
        assert _correct_repo_from_article(repo, self._ARTICLE) is None

    @patch("podcaster.video.video_gen.fetch_repos_from_article")
    def test_prefers_name_match(self, mock_fetch):
        mock_fetch.return_value = [
            RepoReference("someone", "other"),
            RepoReference("vercel", "eve"),
        ]
        repo = RepoReference("vercel-typo", "eve")
        result = _correct_repo_from_article(repo, self._ARTICLE)
        assert result == RepoReference("vercel", "eve")

    @patch("podcaster.video.video_gen.fetch_repos_from_article")
    def test_returns_none_on_owner_only_match(self, mock_fetch):
        # Name was truncated (eve -> ev); an owner-only match is too broad to
        # trust, so we return None instead of guessing an unrelated repo.
        mock_fetch.return_value = [
            RepoReference("vercel", "eve"),
            RepoReference("other", "thing"),
        ]
        repo = RepoReference("vercel", "ev")
        assert _correct_repo_from_article(repo, self._ARTICLE) is None

    @patch("podcaster.video.video_gen.fetch_repos_from_article")
    def test_returns_none_when_no_confident_match(self, mock_fetch):
        mock_fetch.return_value = [RepoReference("foo", "bar")]
        repo = RepoReference("vercel", "eve")
        assert _correct_repo_from_article(repo, self._ARTICLE) is None

    @patch(
        "podcaster.video.video_gen.fetch_repos_from_article",
        side_effect=Exception("network"),
    )
    def test_swallows_fetch_errors(self, mock_fetch):
        repo = RepoReference("vercel", "eve")
        assert _correct_repo_from_article(repo, self._ARTICLE) is None


class TestNavigateWithRecovery:
    def _page(self, *goto_results):
        page = MagicMock()
        page.goto.side_effect = list(goto_results)
        return page

    def test_direct_success(self):
        page = self._page(MagicMock(status=200))
        repo = RepoReference("vercel", "eve")
        outcome = _navigate_with_recovery(
            page, repo, None, backoff_seconds=(0.0,)
        )
        assert outcome.success is True
        assert outcome.recovery_path == "direct"
        assert outcome.repo == repo
        assert page.goto.call_count == 1

    def test_retry_success(self):
        page = self._page(MagicMock(status=503), MagicMock(status=200))
        repo = RepoReference("vercel", "eve")
        outcome = _navigate_with_recovery(
            page, repo, None, backoff_seconds=(0.0,)
        )
        assert outcome.success is True
        assert outcome.recovery_path == "retry"
        assert page.goto.call_count == 2
        page.wait_for_timeout.assert_called_once()

    def test_incremental_backoff_delays(self):
        # Direct + two retries fail, third retry succeeds: the backoff delays are
        # applied in order (1s, 3s, 5s -> ms) before each retry (issue #381).
        page = self._page(
            MagicMock(status=503),
            MagicMock(status=503),
            MagicMock(status=503),
            MagicMock(status=200),
        )
        repo = RepoReference("vercel", "eve")
        outcome = _navigate_with_recovery(page, repo, None)
        assert outcome.success is True
        assert outcome.recovery_path == "retry"
        assert page.goto.call_count == 4
        waited = [c.args[0] for c in page.wait_for_timeout.call_args_list]
        assert waited == [1000, 3000, 5000]

    def test_malformed_url_skips_direct_and_retry(self):
        # A non-GitHub host is malformed: skip direct/retry and go straight to
        # article correction (issue #381 — validate before attempting).
        page = self._page(MagicMock(status=200))
        repo = RepoReference("vercel", "eve")
        with patch(
            "podcaster.video.video_gen._looks_malformed_repo_url",
            return_value=True,
        ), patch(
            "podcaster.video.video_gen._correct_repo_from_article",
            return_value=None,
        ):
            outcome = _navigate_with_recovery(page, repo, None)
        assert outcome.success is False
        assert outcome.recovery_path == "fallback"
        # No navigation attempted against the malformed URL.
        assert page.goto.call_count == 0

    @patch("podcaster.video.video_gen._correct_repo_from_article")
    def test_article_recovery(self, mock_correct):
        corrected = RepoReference("vercel", "eve")
        mock_correct.return_value = corrected
        page = self._page(
            MagicMock(status=404),  # direct
            MagicMock(status=404),  # retry
            MagicMock(status=200),  # corrected URL
        )
        repo = RepoReference("vercel", "ev")
        outcome = _navigate_with_recovery(
            page,
            repo,
            "https://claracle.com/weekly/2026/w26/",
            backoff_seconds=(0.0,),
        )
        assert outcome.success is True
        assert outcome.recovery_path == "article"
        assert outcome.repo == corrected
        assert page.goto.call_count == 3
        assert page.goto.call_args_list[2][0][0] == corrected.url

    @patch(
        "podcaster.video.video_gen._correct_repo_from_article",
        return_value=None,
    )
    def test_all_paths_fail(self, mock_correct):
        page = self._page(MagicMock(status=404), MagicMock(status=404))
        repo = RepoReference("vercel", "eve")
        outcome = _navigate_with_recovery(
            page, repo, None, backoff_seconds=(0.0,)
        )
        assert outcome.success is False
        assert outcome.recovery_path == "fallback"
        assert page.goto.call_count == 2


# --- _record_segment recovery integration (mocked Playwright, issue #378) ---


@pytest.mark.usefixtures("stub_compose")
class TestRecordSegmentRecovery:
    def _mock_browser(self, tmp_path: Path):
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        video = MagicMock()
        browser.new_context.return_value = context
        context.new_page.return_value = page
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        page.evaluate.side_effect = lambda js: (
            2000 if "scrollHeight" in js else None
        )
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        fake_video = raw_dir / "rec.webm"
        fake_video.write_bytes(b"\x1a\x45\xdf\xa3")
        video.path.return_value = str(fake_video)
        page.video = video
        return browser, page, tmp_path

    def test_retry_recovers(self, tmp_path):
        browser, page, out_dir = self._mock_browser(tmp_path)
        # First navigation times out, retry succeeds.
        page.goto.side_effect = [Exception("timeout"), MagicMock(status=200)]
        segment = _make_segment(owner="vercel", name="eve", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False), \
             patch("podcaster.video.video_gen._extract_website_url", return_value=None):
            result = _record_segment(browser, segment, out_dir)

        assert result.is_fallback is False
        assert result.recovery_path == "retry"
        assert result.video_path.name.startswith("vercel_eve_")

    @patch("podcaster.video.video_gen._correct_repo_from_article")
    def test_article_recovers_and_renames(self, mock_correct, tmp_path):
        browser, page, out_dir = self._mock_browser(tmp_path)
        mock_correct.return_value = RepoReference("vercel", "eve")
        # Direct + all three retries fail with 404; corrected URL loads (#381
        # backoff: 1s/3s/5s -> four failed attempts before article correction).
        page.goto.side_effect = [
            MagicMock(status=404),  # direct
            MagicMock(status=404),  # retry 1
            MagicMock(status=404),  # retry 2
            MagicMock(status=404),  # retry 3
            MagicMock(status=200),  # corrected URL
        ]
        segment = _make_segment(owner="vercel", name="ev", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False), \
             patch("podcaster.video.video_gen._extract_website_url", return_value=None):
            result = _record_segment(
                browser,
                segment,
                out_dir,
                source_url="https://claracle.com/weekly/2026/w26/",
            )

        assert result.is_fallback is False
        assert result.recovery_path == "article"
        # File is named after the corrected repo.
        assert result.video_path.name.startswith("vercel_eve_")
        mock_correct.assert_called_once()

    def test_fallback_when_all_recovery_fails(self, tmp_path):
        browser, page, out_dir = self._mock_browser(tmp_path)
        page.goto.return_value = MagicMock(status=404)
        segment = _make_segment(owner="vercel", name="eve", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False):
            result = _record_segment(browser, segment, out_dir)

        assert result.is_fallback is True
        assert result.recovery_path == "fallback"


    def test_no_fallback_after_successful_navigation(self, tmp_path):
        # Navigation succeeds, but a later recording step (smooth scroll) raises.
        # The good repo recording must be KEPT — no "repo unavailable" fallback
        # is rendered on top of it (issue #381).
        browser, page, out_dir = self._mock_browser(tmp_path)
        page.goto.return_value = MagicMock(status=200)
        segment = _make_segment(owner="vercel", name="eve", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=True), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False), \
             patch("podcaster.video.video_gen._extract_website_url", return_value=None), \
             patch(
                 "podcaster.video.video_gen._smooth_scroll",
                 side_effect=RuntimeError("scroll boom"),
             ), \
             patch("podcaster.video.video_gen._render_fallback_page") as mock_fallback:
            result = _record_segment(browser, segment, out_dir)

        assert result.is_fallback is False
        assert result.recovery_path == "direct"
        mock_fallback.assert_not_called()
        assert result.video_path.name.startswith("vercel_eve_")


# --- record_episode tests (no Playwright dependency) ---


class TestRecordEpisodeNoPW:
    def test_raises_on_empty_plan(self):
        plan = EpisodePlan(total_duration_seconds=60.0, segments=())
        with pytest.raises(ValueError, match="no segments"):
            record_episode(plan)

    @patch("podcaster.video.video_gen._PLAYWRIGHT_AVAILABLE", False)
    def test_raises_without_playwright(self):
        plan = _make_plan(_make_segment(duration=2.0), total=2.0)
        with pytest.raises(RuntimeError, match="Playwright is not installed"):
            record_episode(plan)


# --- record_episode tests (mocked Playwright) ---


@pytest.mark.usefixtures("stub_compose")
class TestRecordEpisode:
    @patch("podcaster.video.video_gen._PLAYWRIGHT_AVAILABLE", True)
    @patch("podcaster.video.video_gen.sync_playwright", create=True)
    @patch("podcaster.video.video_gen._check_repo_accessible", return_value=True)
    @patch("podcaster.video.video_gen._check_gh_pages", return_value=False)
    def test_records_all_segments(self, mock_pages, mock_access, mock_pw):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Set up mock browser chain
            pw_instance = MagicMock()
            mock_pw.return_value.__enter__ = MagicMock(return_value=pw_instance)
            mock_pw.return_value.__exit__ = MagicMock(return_value=False)
            browser = pw_instance.chromium.launch.return_value

            def make_context_side_effect(**kwargs):
                ctx = MagicMock()
                page = MagicMock()
                video = MagicMock()

                page.viewport_size = {"width": WIDTH, "height": HEIGHT}
                page.evaluate.side_effect = lambda js: (
                    2000 if "scrollHeight" in js else None
                )

                # Create a unique fake video
                raw_dir = tmp_path / "raw"
                raw_dir.mkdir(parents=True, exist_ok=True)
                import uuid
                fake_video = raw_dir / f"{uuid.uuid4()}.webm"
                fake_video.write_bytes(b"\x1a\x45\xdf\xa3")
                video.path.return_value = str(fake_video)

                page.video = video
                ctx.new_page.return_value = page
                return ctx

            browser.new_context.side_effect = make_context_side_effect

            segments = [
                _make_segment("microsoft", "vscode", 0, 20),
                _make_segment("astral-sh", "ruff", 20, 20),
                _make_segment("jmservera", "SquadScope", 40, 20),
            ]
            plan = _make_plan(*segments, total=60.0)

            result = record_episode(plan, output_dir=tmp_path)

            assert len(result.recorded) == 3
            assert result.output_dir == tmp_path
            names = [r.video_path.name for r in result.recorded]
            assert any(n.startswith("microsoft_vscode_") for n in names)
            assert any(n.startswith("astral-sh_ruff_") for n in names)
            assert any(n.startswith("jmservera_SquadScope_") for n in names)

    @patch("podcaster.video.video_gen._PLAYWRIGHT_AVAILABLE", True)
    @patch("podcaster.video.video_gen.sync_playwright", create=True)
    @patch("podcaster.video.video_gen._check_repo_accessible", return_value=True)
    @patch("podcaster.video.video_gen._check_gh_pages", return_value=False)
    def test_creates_temp_dir_when_none(self, mock_pages, mock_access, mock_pw):
        pw_instance = MagicMock()
        mock_pw.return_value.__enter__ = MagicMock(return_value=pw_instance)
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        browser = pw_instance.chromium.launch.return_value

        def make_context_side_effect(**kwargs):
            ctx = MagicMock()
            page = MagicMock()
            video = MagicMock()

            page.viewport_size = {"width": WIDTH, "height": HEIGHT}
            page.evaluate.side_effect = lambda js: (
                2000 if "scrollHeight" in js else None
            )

            raw_dir = Path(kwargs.get("record_video_dir", "/tmp"))
            raw_dir.mkdir(parents=True, exist_ok=True)
            import uuid
            fake_video = raw_dir / f"{uuid.uuid4()}.webm"
            fake_video.write_bytes(b"\x1a\x45\xdf\xa3")
            video.path.return_value = str(fake_video)

            page.video = video
            ctx.new_page.return_value = page
            return ctx

        browser.new_context.side_effect = make_context_side_effect

        plan = _make_plan(_make_segment(duration=2.0), total=2.0)
        result = record_episode(plan, output_dir=None)

        assert result.output_dir.exists()
        assert "video_gen_" in result.output_dir.name

    @patch("podcaster.video.video_gen._PLAYWRIGHT_AVAILABLE", True)
    @patch("podcaster.video.video_gen.sync_playwright", create=True)
    @patch("podcaster.video.video_gen._check_repo_accessible", return_value=True)
    @patch("podcaster.video.video_gen._check_gh_pages", return_value=False)
    def test_launches_with_anti_throttling_args(
        self, mock_pages, mock_access, mock_pw
    ):
        """Chromium is launched with the anti-throttling recording args (#359)."""
        pw_instance = MagicMock()
        mock_pw.return_value.__enter__ = MagicMock(return_value=pw_instance)
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        browser = pw_instance.chromium.launch.return_value

        def make_context_side_effect(**kwargs):
            ctx = MagicMock()
            page = MagicMock()
            video = MagicMock()
            page.viewport_size = {"width": WIDTH, "height": HEIGHT}
            page.evaluate.side_effect = lambda js: (
                2000 if "scrollHeight" in js else None
            )
            raw_dir = Path(kwargs.get("record_video_dir", "/tmp"))
            raw_dir.mkdir(parents=True, exist_ok=True)
            import uuid
            fake_video = raw_dir / f"{uuid.uuid4()}.webm"
            fake_video.write_bytes(b"\x1a\x45\xdf\xa3")
            video.path.return_value = str(fake_video)
            page.video = video
            ctx.new_page.return_value = page
            return ctx

        browser.new_context.side_effect = make_context_side_effect

        plan = _make_plan(_make_segment(duration=2.0), total=2.0)
        record_episode(plan, output_dir=None)

        _, kwargs = pw_instance.chromium.launch.call_args
        assert kwargs.get("args") == RECORDING_CHROMIUM_ARGS

    @patch("podcaster.video.video_gen._PLAYWRIGHT_AVAILABLE", True)
    @patch("podcaster.video.video_gen.sync_playwright", create=True)
    @patch("podcaster.video.video_gen._record_segment")
    def test_passes_source_url_to_record_segment(
        self, mock_record, mock_pw, tmp_path
    ):
        """The episode's Source URL is forwarded for repo-URL recovery (#378)."""
        pw_instance = MagicMock()
        mock_pw.return_value.__enter__ = MagicMock(return_value=pw_instance)
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        mock_record.return_value = RecordedSegment(
            segment=_make_segment(duration=2.0),
            video_path=tmp_path / "x.webm",
        )

        plan = _make_plan(_make_segment(duration=2.0), total=2.0)
        article = "https://claracle.com/weekly/2026/w26/"
        record_episode(plan, output_dir=tmp_path, source_url=article)

        assert mock_record.call_args.kwargs["source_url"] == article


# --- Integration tests (require Playwright + network) ---


@pytest.mark.slow
class TestRecordEpisodeIntegration:
    """Integration tests that actually launch Playwright against live GitHub.

    Marked with @pytest.mark.slow — skipped by default.
    Run with: pytest -o "addopts=" -m slow tests/test_video_gen.py
    """

    @pytest.fixture(autouse=True)
    def _require_playwright(self):
        pw = pytest.importorskip("playwright")
        # Also verify browsers are installed
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                browser.close()
        except Exception as exc:
            pytest.skip(f"Playwright browsers not available: {exc}")

    def test_records_three_repos(self):
        segments = [
            _make_segment("microsoft", "vscode", 0, 3),
            _make_segment("astral-sh", "ruff", 3, 3),
            _make_segment("jmservera", "SquadScope", 6, 3),
        ]
        plan = _make_plan(*segments, total=9.0)

        with tempfile.TemporaryDirectory() as tmp:
            result = record_episode(
                plan, output_dir=tmp, headless=True, check_accessibility=True
            )

            assert len(result.recorded) == 3
            for rec in result.recorded:
                assert rec.video_path.exists()
                # Screenshot mode composes .mp4; legacy screencast .webm (#387).
                assert rec.video_path.suffix in (".mp4", ".webm")
                assert rec.video_path.stat().st_size > 0

    def test_handles_404_repo(self):
        segments = [
            _make_segment(
                "nonexistent-owner-zzz", "nonexistent-repo-zzz", 0, 3
            ),
        ]
        plan = _make_plan(*segments, total=3.0)

        with tempfile.TemporaryDirectory() as tmp:
            result = record_episode(plan, output_dir=tmp, headless=True)

            assert len(result.recorded) == 1
            rec = result.recorded[0]
            assert rec.is_fallback is True
            assert rec.video_path.exists()


# --- Screenshot-based (hyperframe) capture helpers (issue #387) ---


class TestMakeRecordingContext:
    def test_hyperframe_mode_sets_native_scale_and_logs(self, tmp_path, caplog):
        browser = MagicMock()
        with patch(
            "podcaster.video.video_gen.SCREENSHOT_CAPTURE_ENABLED", True
        ), caplog.at_level("INFO"):
            context, capturer = _make_recording_context(
                browser, tmp_path, segment_label="repo x/y"
            )
        assert capturer is not None
        kwargs = browser.new_context.call_args.kwargs
        # Native 1920x1080 capture, no HiDPI scaling (issue #392).
        assert kwargs["viewport"] == {"width": WIDTH, "height": HEIGHT}
        assert kwargs["device_scale_factor"] == 1
        # No real-time screencast recording in hyperframe mode.
        assert "record_video_dir" not in kwargs
        assert any(
            "Hyperframe capture mode active" in r.message and "repo x/y" in r.message
            for r in caplog.records
        )

    def test_screencast_mode_records_video_and_logs(self, tmp_path, caplog):
        browser = MagicMock()
        with patch(
            "podcaster.video.video_gen.SCREENSHOT_CAPTURE_ENABLED", False
        ), caplog.at_level("INFO"):
            context, capturer = _make_recording_context(browser, tmp_path)
        assert capturer is None
        kwargs = browser.new_context.call_args.kwargs
        assert "record_video_dir" in kwargs
        assert any(
            "screencast capture mode active" in r.message for r in caplog.records
        )


def _make_screenshot_page(scroll_height: int = 5000) -> MagicMock:
    """A mock Page whose screenshot writes a real PNG to the given path."""
    page = MagicMock()
    page.viewport_size = {"width": WIDTH, "height": HEIGHT}
    page.evaluate.side_effect = lambda js: (
        scroll_height if "scrollHeight" in js else None
    )

    def _screenshot(path):
        Path(path).write_bytes(_PNG_64x64)

    page.screenshot.side_effect = _screenshot
    return page


class TestCapturer:
    def test_frame_writes_sequential_pngs(self, tmp_path):
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")

        cap.frame(page)
        cap.frame(page)

        assert cap.count == 2
        assert (cap.frames_dir / "frame_00001.png").exists()
        assert (cap.frames_dir / "frame_00002.png").exists()

    def test_still_writes_single_png(self, tmp_path):
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")

        result = cap.still(page)

        assert result == cap.still_image
        assert cap.count == 0
        assert (cap.frames_dir / "still.png").exists()

    def test_frame_swallows_screenshot_errors(self, tmp_path):
        page = MagicMock()
        page.screenshot.side_effect = RuntimeError("boom")
        cap = _Capturer(tmp_path / "frames")

        assert cap.frame(page) is None
        assert cap.count == 0

    def test_reset_frames_removes_sequence(self, tmp_path):
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")
        cap.frame(page)
        cap.frame(page)
        cap.frame(page)

        cap.reset_frames()

        assert cap.count == 0
        assert not list(cap.frames_dir.glob("frame_*.png"))


class TestSmoothScrollCapture:
    def test_captures_one_frame_per_tick(self, tmp_path):
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")

        _smooth_scroll(page, duration_seconds=2.0, capturer=cap)

        assert cap.count == int(2.0 * SCREENSHOT_CAPTURE_FPS)
        # Screenshot mode must not block on real-time waits.
        page.wait_for_timeout.assert_not_called()

    def test_non_scrollable_page_still_captures_full_run(self, tmp_path):
        # scrollHeight <= viewport height -> no scroll, but frames still emitted.
        page = _make_screenshot_page(scroll_height=HEIGHT)
        cap = _Capturer(tmp_path / "frames")

        _smooth_scroll(page, duration_seconds=1.0, capturer=cap)

        assert cap.count == int(1.0 * SCREENSHOT_CAPTURE_FPS)

    def test_very_short_duration_captures_one_frame(self, tmp_path):
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")

        _smooth_scroll(page, duration_seconds=0.001, capturer=cap)

        assert cap.count == 1

    def test_frame_count_follows_capture_fps_override(self, tmp_path):
        # The captured frame count tracks SCREENSHOT_CAPTURE_FPS so the composed
        # duration (frames / fps) stays correct under a custom FPS (#387).
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")

        with patch("podcaster.video.video_gen.SCREENSHOT_CAPTURE_FPS", 12):
            _smooth_scroll(page, duration_seconds=2.0, capturer=cap)

        assert cap.count == int(2.0 * 12)


class TestPadFrames:
    def test_pads_to_target(self, tmp_path):
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")
        cap.frame(page)  # one real frame

        _pad_frames(cap, 5)

        assert cap.count == 5
        for i in range(1, 6):
            assert (cap.frames_dir / f"frame_{i:05d}.png").exists()

    def test_noop_when_already_at_target(self, tmp_path):
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")
        cap.frame(page)
        cap.frame(page)

        _pad_frames(cap, 2)

        assert cap.count == 2

    def test_noop_when_no_frames(self, tmp_path):
        cap = _Capturer(tmp_path / "frames")
        _pad_frames(cap, 5)
        assert cap.count == 0


class TestComposeScreenshotSegment:
    def test_raises_when_nothing_captured(self, tmp_path):
        cap = _Capturer(tmp_path / "frames")
        with pytest.raises(RuntimeError, match="No screenshots captured"):
            _compose_screenshot_segment(cap, 2.0, tmp_path / "out.mp4")

    def test_frames_command_uses_framerate_and_h264(self, tmp_path):
        cmd = _build_frames_to_video_cmd(tmp_path, SCREENSHOT_CAPTURE_FPS, tmp_path / "o.mp4")
        assert "-framerate" in cmd
        assert str(SCREENSHOT_CAPTURE_FPS) in cmd
        assert "libx264" in cmd
        assert "frame_%05d.png" in cmd[cmd.index("-i") + 1]
        # Hyperframe quality tuning for screen content (issue #392).
        assert "-tune" in cmd
        assert cmd[cmd.index("-tune") + 1] == SCREENSHOT_CAPTURE_TUNE

    def test_still_command_loops_for_duration(self, tmp_path):
        cmd = _build_still_to_video_cmd(
            tmp_path / "still.png", 3.0, SCREENSHOT_CAPTURE_FPS, tmp_path / "o.mp4"
        )
        assert "-loop" in cmd
        assert "-t" in cmd
        assert cmd[cmd.index("-t") + 1] == "3.000"
        assert "-tune" in cmd
        assert cmd[cmd.index("-tune") + 1] == SCREENSHOT_CAPTURE_TUNE

    @pytest.mark.skipif(
        not __import__("shutil").which("ffmpeg"),
        reason="ffmpeg not available",
    )
    def test_real_ffmpeg_composes_frame_sequence(self, tmp_path):
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")
        for _ in range(5):
            cap.frame(page)

        out = tmp_path / "seg.mp4"
        _compose_screenshot_segment(cap, 5 / SCREENSHOT_CAPTURE_FPS, out)

        assert out.exists()
        assert out.stat().st_size > 0

    @pytest.mark.skipif(
        not __import__("shutil").which("ffmpeg"),
        reason="ffmpeg not available",
    )
    def test_real_ffmpeg_composes_still(self, tmp_path):
        page = _make_screenshot_page()
        cap = _Capturer(tmp_path / "frames")
        cap.still(page)

        out = tmp_path / "seg.mp4"
        _compose_screenshot_segment(cap, 1.0, out)

        assert out.exists()
        assert out.stat().st_size > 0


# --- Per-segment checkpoint/resume against blob (issue #410) ------------------


class TestRecordEpisodeCheckpointResume:
    def _store(self, tmp_path):
        from podcaster.storage import LocalStorageBackend
        from podcaster.video.intermediates import IntermediateStore

        backend = LocalStorageBackend(
            root=tmp_path / "scratch", base_url="https://example.test/scratch"
        )
        return IntermediateStore(backend, "job-rec")

    @patch("podcaster.video.video_gen._PLAYWRIGHT_AVAILABLE", True)
    @patch("podcaster.video.video_gen.sync_playwright", create=True)
    @patch("podcaster.video.video_gen._record_segment")
    def test_full_resume_skips_browser(self, mock_record, mock_pw, tmp_path):
        store = self._store(tmp_path)
        # Pre-seed a checkpoint for the only segment (recording file + sidecar).
        rec_file = tmp_path / "seed.mp4"
        rec_file.write_bytes(b"\x00\x00\x00\x18ftypmp42seed")
        store.upload("recording_000.mp4", rec_file, "video/mp4")
        store.write_text(
            "recording_000.json",
            '{"suffix": ".mp4", "is_fallback": false, "has_pages": true, '
            '"website_url": "https://x.test", "is_removed": false, '
            '"recovery_path": "website"}',
        )

        plan = _make_plan(_make_segment(duration=2.0), total=2.0)
        result = record_episode(plan, output_dir=tmp_path / "out", intermediates=store)

        # Browser was never launched and no segment was recorded.
        mock_pw.assert_not_called()
        mock_record.assert_not_called()
        assert len(result.recorded) == 1
        rec = result.recorded[0]
        assert rec.recovery_path == "website"
        assert rec.has_pages is True
        assert rec.website_url == "https://x.test"
        assert rec.video_path.exists()

    @patch("podcaster.video.video_gen._PLAYWRIGHT_AVAILABLE", True)
    @patch("podcaster.video.video_gen.sync_playwright", create=True)
    @patch("podcaster.video.video_gen._record_segment")
    def test_records_and_checkpoints_when_absent(self, mock_record, mock_pw, tmp_path):
        store = self._store(tmp_path)
        pw_instance = MagicMock()
        mock_pw.return_value.__enter__ = MagicMock(return_value=pw_instance)
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        out_dir = tmp_path / "out"

        def _fake_record(browser, segment, output_dir, check_accessibility, source_url=None):
            from podcaster.video.video_gen import RecordedSegment

            path = Path(output_dir) / "fresh_000.mp4"
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42new")
            return RecordedSegment(segment=segment, video_path=path, recovery_path="direct")

        mock_record.side_effect = _fake_record

        plan = _make_plan(_make_segment(duration=2.0), total=2.0)
        result = record_episode(plan, output_dir=out_dir, intermediates=store)

        assert len(result.recorded) == 1
        mock_record.assert_called_once()
        # The freshly-recorded segment was checkpointed to blob.
        assert store.exists("recording_000.mp4") is True
        assert store.read_text("recording_000.json") is not None

    @patch("podcaster.video.video_gen._PLAYWRIGHT_AVAILABLE", True)
    @patch("podcaster.video.video_gen.sync_playwright", create=True)
    @patch("podcaster.video.video_gen._record_segment")
    def test_partial_resume_records_only_missing(self, mock_record, mock_pw, tmp_path):
        store = self._store(tmp_path)
        # Seed only segment 0; segment 1 must still be recorded.
        rec_file = tmp_path / "seed.mp4"
        rec_file.write_bytes(b"\x00\x00\x00\x18ftypmp42seed")
        store.upload("recording_000.mp4", rec_file, "video/mp4")
        store.write_text(
            "recording_000.json",
            '{"suffix": ".mp4", "recovery_path": "direct"}',
        )

        pw_instance = MagicMock()
        mock_pw.return_value.__enter__ = MagicMock(return_value=pw_instance)
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        def _fake_record(browser, segment, output_dir, check_accessibility, source_url=None):
            from podcaster.video.video_gen import RecordedSegment

            path = Path(output_dir) / "fresh_001.mp4"
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42new")
            return RecordedSegment(segment=segment, video_path=path)

        mock_record.side_effect = _fake_record

        plan = _make_plan(
            _make_segment("a", "b", 0, 10),
            _make_segment("c", "d", 10, 10),
            total=20.0,
        )
        result = record_episode(plan, output_dir=tmp_path / "out", intermediates=store)

        assert len(result.recorded) == 2
        # Only the missing segment (index 1) was recorded.
        assert mock_record.call_count == 1
        assert store.exists("recording_001.mp4") is True

    @patch("podcaster.video.video_gen._PLAYWRIGHT_AVAILABLE", True)
    @patch("podcaster.video.video_gen.sync_playwright", create=True)
    @patch("podcaster.video.video_gen._record_segment")
    def test_local_recording_deleted_after_checkpoint(self, mock_record, mock_pw, tmp_path):
        """Issue #410: a freshly-recorded segment is freed from local disk once
        its size-verified blob checkpoint is confirmed."""
        store = self._store(tmp_path)
        pw_instance = MagicMock()
        mock_pw.return_value.__enter__ = MagicMock(return_value=pw_instance)
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)

        out_dir = tmp_path / "out"
        recorded_paths = []

        def _fake_record(browser, segment, output_dir, check_accessibility, source_url=None):
            from podcaster.video.video_gen import RecordedSegment

            path = Path(output_dir) / "fresh_000.mp4"
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42new")
            recorded_paths.append(path)
            return RecordedSegment(segment=segment, video_path=path, recovery_path="direct")

        mock_record.side_effect = _fake_record

        plan = _make_plan(_make_segment(duration=2.0), total=2.0)
        record_episode(plan, output_dir=out_dir, intermediates=store)

        # Checkpointed to blob …
        assert store.exists("recording_000.mp4") is True
        # … and the local copy was deleted (disk holds only the current file).
        assert recorded_paths and not recorded_paths[0].exists()
