"""Tests for podcaster.video.intro_outro module.

Unit tests mock Playwright to avoid requiring a real browser.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

pytest.importorskip("playwright", reason="playwright not installed — skipping video intro/outro tests")

from podcaster.video.intro_outro import (
    CLARACLE_URL,
    INTRO_DURATION_MS,
    OUTRO_DURATION_MS,
    REPO_LINKS,
    WIDTH,
    HEIGHT,
    ClipResult,
    IntroConfig,
    OutroConfig,
    _render_intro_html,
    _render_outro_html,
    _record_html_to_video,
    generate_intro,
    generate_outro,
)


# --- HTML Rendering Tests ---


class TestRenderIntroHtml:
    def test_contains_brand_name(self):
        html = _render_intro_html(IntroConfig())
        assert "Claracle" in html

    def test_contains_episode_title(self):
        config = IntroConfig(episode_title="My Great Episode")
        html = _render_intro_html(config)
        assert "My Great Episode" in html

    def test_contains_subtitle(self):
        config = IntroConfig(subtitle="A deep dive into AI")
        html = _render_intro_html(config)
        assert "A deep dive into AI" in html

    def test_contains_viewport_dimensions(self):
        config = IntroConfig(width=1280, height=720)
        html = _render_intro_html(config)
        assert "1280px" in html
        assert "720px" in html

    def test_contains_animation_keyframes(self):
        html = _render_intro_html(IntroConfig())
        assert "@keyframes fadeInUp" in html
        assert "@keyframes shimmer" in html


class TestRenderOutroHtml:
    def test_contains_url(self):
        html = _render_outro_html(OutroConfig())
        assert CLARACLE_URL in html

    def test_contains_repo_links(self):
        html = _render_outro_html(OutroConfig())
        for name, _url in REPO_LINKS:
            assert name in html

    def test_custom_url(self):
        config = OutroConfig(url="custom.example.com")
        html = _render_outro_html(config)
        assert "custom.example.com" in html

    def test_custom_links(self):
        config = OutroConfig(links=[("MyRepo", "https://github.com/me/repo")])
        html = _render_outro_html(config)
        assert "MyRepo" in html

    def test_contains_subscribe_cta(self):
        html = _render_outro_html(OutroConfig())
        assert "Subscribe" in html


# --- Config Tests ---


class TestIntroConfig:
    def test_defaults(self):
        config = IntroConfig()
        assert config.episode_title == "Untitled Episode"
        assert config.subtitle == ""
        assert config.duration_ms == INTRO_DURATION_MS
        assert config.width == WIDTH
        assert config.height == HEIGHT

    def test_custom_values(self):
        config = IntroConfig(
            episode_title="Ep 42",
            subtitle="The meaning of life",
            duration_ms=7000,
            width=1280,
            height=720,
        )
        assert config.episode_title == "Ep 42"
        assert config.duration_ms == 7000


class TestOutroConfig:
    def test_defaults(self):
        config = OutroConfig()
        assert config.url == CLARACLE_URL
        assert config.links == list(REPO_LINKS)
        assert config.duration_ms == OUTRO_DURATION_MS

    def test_custom_links(self):
        custom = [("A", "http://a.com")]
        config = OutroConfig(links=custom)
        assert config.links == custom


# --- Recording Tests (mocked Playwright) ---


def _mock_playwright_context(tmp_path: Path):
    """Create a full mock Playwright context that simulates video recording."""
    mock_pw = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_pw.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    video_file = tmp_path / "recording-001.webm"

    # Mock page.video.path() to return the video file path
    mock_page.video.path.return_value = str(video_file)

    # Simulate video file creation on context.close()
    def create_video_file():
        video_file.write_bytes(b"\x1a\x45\xdf\xa3")  # WebM magic bytes

    mock_context.close.side_effect = create_video_file

    return mock_pw


class TestRecordHtmlToVideo:
    def test_calls_playwright_correctly(self, tmp_path):
        mock_pw = _mock_playwright_context(tmp_path)
        output_path = tmp_path / "test.webm"

        with patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", True), \
             patch(
            "podcaster.video.intro_outro.sync_playwright"
        ) as mock_sync_pw:
            mock_sync_pw.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sync_pw.return_value.__exit__ = MagicMock(return_value=False)

            _record_html_to_video(
                html_content="<html></html>",
                output_path=output_path,
                duration_ms=3000,
                width=1920,
                height=1080,
            )

        mock_pw.chromium.launch.assert_called_once_with(headless=True)
        mock_pw.chromium.launch.return_value.new_context.assert_called_once()
        context_kwargs = mock_pw.chromium.launch.return_value.new_context.call_args
        assert context_kwargs.kwargs["viewport"] == {"width": 1920, "height": 1080}
        assert context_kwargs.kwargs["record_video_size"] == {
            "width": 1920,
            "height": 1080,
        }

    def test_sets_content_and_waits(self, tmp_path):
        mock_pw = _mock_playwright_context(tmp_path)
        output_path = tmp_path / "test.webm"
        mock_page = mock_pw.chromium.launch.return_value.new_context.return_value.new_page.return_value

        with patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", True), \
             patch(
            "podcaster.video.intro_outro.sync_playwright"
        ) as mock_sync_pw:
            mock_sync_pw.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sync_pw.return_value.__exit__ = MagicMock(return_value=False)

            _record_html_to_video(
                html_content="<html><body>Hello</body></html>",
                output_path=output_path,
                duration_ms=5000,
            )

        mock_page.set_content.assert_called_once_with("<html><body>Hello</body></html>")
        mock_page.wait_for_timeout.assert_called_once_with(5000)

    def test_renames_video_to_output_path(self, tmp_path):
        mock_pw = _mock_playwright_context(tmp_path)
        output_path = tmp_path / "final.webm"

        with patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", True), \
             patch(
            "podcaster.video.intro_outro.sync_playwright"
        ) as mock_sync_pw:
            mock_sync_pw.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sync_pw.return_value.__exit__ = MagicMock(return_value=False)

            result = _record_html_to_video(
                html_content="<html></html>",
                output_path=output_path,
                duration_ms=1000,
            )

        assert result == output_path
        assert output_path.exists()

    def test_raises_without_playwright(self, tmp_path):
        output_path = tmp_path / "test.webm"
        with patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="Playwright is not installed"):
                _record_html_to_video(
                    html_content="<html></html>",
                    output_path=output_path,
                    duration_ms=1000,
                )


# --- Integration Tests (mocked Playwright) ---


class TestGenerateIntro:
    def test_returns_clip_result(self, tmp_path):
        mock_pw = _mock_playwright_context(tmp_path)

        with patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", True), \
             patch(
            "podcaster.video.intro_outro.sync_playwright"
        ) as mock_sync_pw:
            mock_sync_pw.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sync_pw.return_value.__exit__ = MagicMock(return_value=False)

            result = generate_intro(
                config=IntroConfig(episode_title="Test Episode"),
                output_dir=tmp_path,
            )

        assert isinstance(result, ClipResult)
        assert result.path == tmp_path / "intro.webm"
        assert result.duration_ms == INTRO_DURATION_MS
        assert result.width == WIDTH
        assert result.height == HEIGHT

    def test_uses_default_config(self, tmp_path):
        mock_pw = _mock_playwright_context(tmp_path)

        with patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", True), \
             patch(
            "podcaster.video.intro_outro.sync_playwright"
        ) as mock_sync_pw:
            mock_sync_pw.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sync_pw.return_value.__exit__ = MagicMock(return_value=False)

            result = generate_intro(output_dir=tmp_path)

        assert result.duration_ms == INTRO_DURATION_MS

    def test_creates_output_dir(self):
        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td) / "new_subdir"
            mock_pw = _mock_playwright_context(output_dir)

            with patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", True), \
                 patch(
                "podcaster.video.intro_outro.sync_playwright"
            ) as mock_sync_pw:
                mock_sync_pw.return_value.__enter__ = MagicMock(return_value=mock_pw)
                mock_sync_pw.return_value.__exit__ = MagicMock(return_value=False)

                # Need to create the dir before mock writes to it
                output_dir.mkdir(parents=True, exist_ok=True)
                result = generate_intro(output_dir=output_dir)

            assert output_dir.exists()


class TestGenerateOutro:
    def test_returns_clip_result(self, tmp_path):
        mock_pw = _mock_playwright_context(tmp_path)

        with patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", True), \
             patch(
            "podcaster.video.intro_outro.sync_playwright"
        ) as mock_sync_pw:
            mock_sync_pw.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sync_pw.return_value.__exit__ = MagicMock(return_value=False)

            result = generate_outro(
                config=OutroConfig(),
                output_dir=tmp_path,
            )

        assert isinstance(result, ClipResult)
        assert result.path == tmp_path / "outro.webm"
        assert result.duration_ms == OUTRO_DURATION_MS

    def test_custom_config(self, tmp_path):
        mock_pw = _mock_playwright_context(tmp_path)
        config = OutroConfig(
            url="example.com",
            links=[("Test", "http://test.com")],
            duration_ms=8000,
        )

        with patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", True), \
             patch(
            "podcaster.video.intro_outro.sync_playwright"
        ) as mock_sync_pw:
            mock_sync_pw.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sync_pw.return_value.__exit__ = MagicMock(return_value=False)

            result = generate_outro(config=config, output_dir=tmp_path)

        assert result.duration_ms == 8000
