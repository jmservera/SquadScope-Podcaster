"""Tests for podcaster.video.video_gen module.

Unit tests mock Playwright and requests; the integration test class
(marked slow) exercises real Playwright recording against live GitHub.
"""

from __future__ import annotations

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
    SCROLL_TICKS_PER_SEC,
    WIDTH,
    HEIGHT,
    IMAGE_ZOOM_MIN_SIZE_PX,
    ZOOM_IMAGE_CSS,
    RecordedSegment,
    RecordingResult,
    _check_gh_pages,
    _check_repo_accessible,
    _correct_repo_from_article,
    _dismiss_overlays,
    _extract_website_url,
    _is_login_redirect,
    _looks_malformed_repo_url,
    _navigate_to_website,
    _navigate_with_recovery,
    _smooth_scroll,
    _try_navigate_repo,
    _try_record_project_site,
    _apply_image_zoom,
    _prepare_page_for_recording,
    _render_fallback_page,
    _render_url_card,
    _record_segment,
    record_episode,
)


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

        max_scroll = int(HEIGHT * MAX_SCROLL_VIEWPORT_MULTIPLIER)
        ticks = int(duration * SCROLL_TICKS_PER_SEC)
        expected_per_tick = max_scroll / ticks

        # Verify scrollBy calls use the capped distance
        scroll_calls = [
            c for c in page.evaluate.call_args_list
            if "scrollBy" in str(c)
        ]
        for call in scroll_calls:
            js = call[0][0]
            assert f"scrollBy(0, {expected_per_tick})" in js

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


# --- _apply_image_zoom tests ---


class TestApplyImageZoom:
    def test_tags_large_images_and_injects_css(self):
        page = MagicMock()
        page.evaluate.return_value = 3  # 3 large images tagged

        count = _apply_image_zoom(page)

        assert count == 3
        # JS detection ran with the size threshold
        page.evaluate.assert_called_once()
        args = page.evaluate.call_args[0]
        assert IMAGE_ZOOM_MIN_SIZE_PX in args
        # Zoom CSS was injected
        page.add_style_tag.assert_called_once_with(content=ZOOM_IMAGE_CSS)

    def test_no_large_images_skips_css(self):
        page = MagicMock()
        page.evaluate.return_value = 0

        count = _apply_image_zoom(page)

        assert count == 0
        page.add_style_tag.assert_not_called()

    def test_evaluate_error_is_swallowed(self):
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError("boom")

        assert _apply_image_zoom(page) == 0
        page.add_style_tag.assert_not_called()

    def test_add_style_tag_error_is_swallowed(self):
        page = MagicMock()
        page.evaluate.return_value = 2
        page.add_style_tag.side_effect = RuntimeError("boom")

        assert _apply_image_zoom(page) == 0

    def test_css_overrides_anti_flash_and_scales(self):
        # Must use !important to beat the universal animation:none rule, and
        # an element+class selector for higher specificity.
        assert "!important" in ZOOM_IMAGE_CSS
        assert "img.ss-zoom-target" in ZOOM_IMAGE_CSS
        assert "scale(1)" in ZOOM_IMAGE_CSS
        assert "scale(1.05)" in ZOOM_IMAGE_CSS

    def test_prepare_page_applies_zoom(self):
        page = MagicMock()
        page.evaluate.return_value = 1
        _prepare_page_for_recording(page)
        # Both anti-flash CSS and zoom CSS injected
        injected = [
            c.kwargs.get("content", "") for c in page.add_style_tag.call_args_list
        ]
        assert any("ss-image-zoom" in css for css in injected)


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
        assert result.video_path.name.endswith(".webm")
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
        assert call_kwargs["record_video_size"] == {"width": WIDTH, "height": HEIGHT}

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
        page = MagicMock()
        page.goto.side_effect = Exception("timeout")
        assert _try_navigate_repo(page, "https://github.com/a/b") is False

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
                assert rec.video_path.suffix == ".webm"
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
