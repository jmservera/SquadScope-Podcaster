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
    SCROLL_TICKS_PER_SEC,
    WIDTH,
    HEIGHT,
    RecordedSegment,
    RecordingResult,
    _check_gh_pages,
    _check_repo_accessible,
    _dismiss_overlays,
    _smooth_scroll,
    _render_fallback_page,
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
        assert page.wait_for_timeout.call_count == ticks

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
        """Duration > 0 but < 1 tick (0.25s) should still wait the full duration."""
        page = MagicMock()
        page.viewport_size = {"width": WIDTH, "height": HEIGHT}
        page.evaluate.side_effect = lambda js: (
            5000 if "scrollHeight" in js else None
        )

        _smooth_scroll(page, 0.1)  # too short for a full tick

        # Should not call scrollHeight (returns before evaluating page)
        page.evaluate.assert_not_called()
        # Should wait for the full duration
        page.wait_for_timeout.assert_called_once_with(100)


# --- _render_fallback_page tests ---


class TestRenderFallbackPage:
    def test_sets_content_and_waits(self):
        page = MagicMock()
        _render_fallback_page(page, "owner", "repo", 5.0)
        page.set_content.assert_called_once()
        html = page.set_content.call_args[0][0]
        assert "owner/repo" in html
        assert "Repository unavailable" in html
        page.wait_for_timeout.assert_called_once_with(5000)


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
        segment = _make_segment(owner="gone", name="repo", duration=2.0)

        with patch("podcaster.video.video_gen._check_repo_accessible", return_value=False), \
             patch("podcaster.video.video_gen._check_gh_pages", return_value=False):
            result = _record_segment(browser, segment, out_dir)

        assert result.is_fallback is True
        assert result.video_path.exists()
        page = browser.new_context.return_value.new_page.return_value
        page.set_content.assert_called_once()

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
        page.set_content.assert_not_called()

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
        # On failure it renders the static background instead.
        page.set_content.assert_called_once()


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
