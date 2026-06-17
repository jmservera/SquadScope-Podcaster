"""Tests for podcaster.video.intro_outro module.

Unit tests mock Playwright so they run without a browser.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch, call

import pytest

from podcaster.video.intro_outro import (
    WIDTH,
    HEIGHT,
    DEFAULT_INTRO_DURATION,
    DEFAULT_OUTRO_DURATION,
    IntroConfig,
    OutroConfig,
    IntroOutroResult,
    _intro_html,
    _outro_html,
    _record_html_clip,
    generate_intro,
    generate_outro,
    generate_intro_outro,
)


# --- HTML generation tests ---


class TestIntroHtml:
    """Tests for _intro_html template generation."""

    def test_default_config_contains_brand_name(self):
        html = _intro_html(IntroConfig())
        assert "Claracle" in html

    def test_episode_title_appears(self):
        config = IntroConfig(episode_title="AI Tools Roundup")
        html = _intro_html(config)
        assert "AI Tools Roundup" in html

    def test_episode_number_appears(self):
        config = IntroConfig(episode_number=42)
        html = _intro_html(config)
        assert "Episode 42" in html

    def test_episode_date_appears(self):
        config = IntroConfig(episode_date="2026-06-15")
        html = _intro_html(config)
        assert "2026-06-15" in html

    def test_subtitle_with_both_number_and_date(self):
        config = IntroConfig(episode_number=7, episode_date="Jan 2026")
        html = _intro_html(config)
        assert "Episode 7" in html
        assert "Jan 2026" in html
        assert "&middot;" in html

    def test_no_subtitle_when_empty(self):
        config = IntroConfig()
        html = _intro_html(config)
        assert "subtitle" not in html or "class='subtitle'" not in html

    def test_no_episode_title_element_when_empty(self):
        config = IntroConfig(episode_title="")
        html = _intro_html(config)
        assert "episode-title" not in html or "class='episode-title'" not in html

    def test_custom_podcast_name(self):
        config = IntroConfig(podcast_name="MyShow")
        html = _intro_html(config)
        assert "MyShow" in html
        assert "Claracle" not in html

    def test_viewport_dimensions(self):
        html = _intro_html(IntroConfig())
        assert f"{WIDTH}px" in html
        assert f"{HEIGHT}px" in html

    def test_has_css_animation(self):
        html = _intro_html(IntroConfig())
        assert "@keyframes" in html
        assert "fade-cycle" in html

    def test_short_duration_clamps(self):
        config = IntroConfig(duration_seconds=0.5)
        html = _intro_html(config)
        # Should not crash; animation dur clamps to 1.0s
        assert "1.0s" in html or "1s" in html


class TestOutroHtml:
    """Tests for _outro_html template generation."""

    def test_default_config_contains_brand(self):
        html = _outro_html(OutroConfig())
        assert "Claracle" in html
        assert "www.claracle.com" in html

    def test_subscribe_cta(self):
        html = _outro_html(OutroConfig())
        assert "Subscribe for weekly updates" in html

    def test_custom_cta(self):
        config = OutroConfig(subscribe_cta="Follow us!")
        html = _outro_html(config)
        assert "Follow us!" in html

    def test_repo_urls_displayed(self):
        config = OutroConfig(repo_urls=(
            "https://github.com/jmservera/SquadScope",
            "https://github.com/jmservera/SquadScope-Podcaster",
        ))
        html = _outro_html(config)
        assert "jmservera/SquadScope" in html
        assert "jmservera/SquadScope-Podcaster" in html

    def test_max_four_repos(self):
        urls = tuple(f"https://github.com/owner/repo{i}" for i in range(6))
        config = OutroConfig(repo_urls=urls)
        html = _outro_html(config)
        assert "owner/repo0" in html
        assert "owner/repo3" in html
        assert "owner/repo4" not in html

    def test_no_repos_section_when_empty(self):
        config = OutroConfig(repo_urls=())
        html = _outro_html(config)
        assert "<ul" not in html

    def test_has_css_animation(self):
        html = _outro_html(OutroConfig())
        assert "@keyframes" in html
        assert "fade-cycle" in html

    def test_viewport_dimensions(self):
        html = _outro_html(OutroConfig())
        assert f"{WIDTH}px" in html
        assert f"{HEIGHT}px" in html


# --- Dataclass tests ---


class TestConfigs:
    """Test IntroConfig and OutroConfig defaults."""

    def test_intro_defaults(self):
        c = IntroConfig()
        assert c.podcast_name == "Claracle"
        assert c.episode_title == ""
        assert c.episode_number is None
        assert c.episode_date == ""
        assert c.duration_seconds == DEFAULT_INTRO_DURATION

    def test_outro_defaults(self):
        c = OutroConfig()
        assert c.podcast_name == "Claracle"
        assert c.website_url == "www.claracle.com"
        assert c.repo_urls == ()
        assert c.subscribe_cta == "Subscribe for weekly updates"
        assert c.duration_seconds == DEFAULT_OUTRO_DURATION

    def test_intro_config_frozen(self):
        c = IntroConfig()
        with pytest.raises(AttributeError):
            c.podcast_name = "Other"  # type: ignore[misc]

    def test_outro_config_frozen(self):
        c = OutroConfig()
        with pytest.raises(AttributeError):
            c.podcast_name = "Other"  # type: ignore[misc]

    def test_intro_outro_result_defaults(self):
        r = IntroOutroResult()
        assert r.intro_path is None
        assert r.outro_path is None


# --- Recording tests (mocked Playwright) ---


def _make_mock_browser(tmp_path: Path) -> MagicMock:
    """Create a mock Browser that simulates Playwright recording."""
    browser = MagicMock()
    video_mock = MagicMock()
    video_file = tmp_path / "raw" / "mock_video.webm"
    video_file.parent.mkdir(parents=True, exist_ok=True)
    video_file.write_bytes(b"fake-webm-data")
    video_mock.path.return_value = str(video_file)

    page_mock = MagicMock()
    type(page_mock).video = PropertyMock(return_value=video_mock)

    context_mock = MagicMock()
    context_mock.new_page.return_value = page_mock

    browser.new_context.return_value = context_mock
    return browser


class TestRecordHtmlClip:
    """Tests for _record_html_clip."""

    def test_creates_output_file(self, tmp_path):
        browser = _make_mock_browser(tmp_path)
        result = _record_html_clip(
            browser, "<html><body>test</body></html>",
            2.0, tmp_path, "test_clip"
        )
        assert result.exists()
        assert result.name.startswith("test_clip_")
        assert result.suffix == ".webm"

    def test_sets_viewport_and_video_size(self, tmp_path):
        browser = _make_mock_browser(tmp_path)
        _record_html_clip(browser, "<html></html>", 1.0, tmp_path, "clip")
        ctx_call = browser.new_context.call_args
        assert ctx_call.kwargs["viewport"] == {"width": WIDTH, "height": HEIGHT}
        assert ctx_call.kwargs["record_video_size"] == {"width": WIDTH, "height": HEIGHT}

    def test_waits_for_duration(self, tmp_path):
        browser = _make_mock_browser(tmp_path)
        page = browser.new_context.return_value.new_page.return_value
        _record_html_clip(browser, "<html></html>", 3.5, tmp_path, "clip")
        page.wait_for_timeout.assert_called_once_with(3500)

    def test_closes_context(self, tmp_path):
        browser = _make_mock_browser(tmp_path)
        ctx = browser.new_context.return_value
        _record_html_clip(browser, "<html></html>", 1.0, tmp_path, "clip")
        ctx.close.assert_called_once()

    def test_raises_on_missing_video(self, tmp_path):
        browser = _make_mock_browser(tmp_path)
        page = browser.new_context.return_value.new_page.return_value
        type(page).video = PropertyMock(return_value=None)
        with pytest.raises(RuntimeError, match="No video object"):
            _record_html_clip(browser, "<html></html>", 1.0, tmp_path, "clip")

    def test_raises_on_missing_file(self, tmp_path):
        browser = _make_mock_browser(tmp_path)
        video_mock = MagicMock()
        video_mock.path.return_value = str(tmp_path / "nonexistent.webm")
        page = browser.new_context.return_value.new_page.return_value
        type(page).video = PropertyMock(return_value=video_mock)
        with pytest.raises(FileNotFoundError):
            _record_html_clip(browser, "<html></html>", 1.0, tmp_path, "clip")


class TestGenerateIntro:
    """Tests for generate_intro with mocked Playwright."""

    @patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", False)
    def test_raises_without_playwright(self):
        with pytest.raises(RuntimeError, match="Playwright is not installed"):
            generate_intro()

    @patch("podcaster.video.intro_outro.sync_playwright")
    def test_generates_intro_with_defaults(self, mock_pw, tmp_path):
        browser = _make_mock_browser(tmp_path)
        mock_pw.return_value.start.return_value.chromium.launch.return_value = browser

        # Ensure raw dir has the video file
        video_file = tmp_path / "raw" / "mock_intro.webm"
        video_file.parent.mkdir(parents=True, exist_ok=True)
        video_file.write_bytes(b"fake-webm")
        video_mock = MagicMock()
        video_mock.path.return_value = str(video_file)
        page = browser.new_context.return_value.new_page.return_value
        type(page).video = PropertyMock(return_value=video_mock)

        result = generate_intro(output_dir=tmp_path)
        assert result.exists()
        assert "intro" in result.name

    @patch("podcaster.video.intro_outro.sync_playwright")
    def test_uses_provided_browser(self, mock_pw, tmp_path):
        browser = _make_mock_browser(tmp_path)
        result = generate_intro(
            config=IntroConfig(), output_dir=tmp_path, browser=browser
        )
        assert result.exists()
        # Should NOT call launch since browser was provided
        mock_pw.return_value.start.return_value.chromium.launch.assert_not_called()

    @patch("podcaster.video.intro_outro.sync_playwright")
    def test_does_not_close_provided_browser(self, mock_pw, tmp_path):
        browser = _make_mock_browser(tmp_path)
        generate_intro(browser=browser, output_dir=tmp_path)
        browser.close.assert_not_called()


class TestGenerateOutro:
    """Tests for generate_outro with mocked Playwright."""

    @patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", False)
    def test_raises_without_playwright(self):
        with pytest.raises(RuntimeError, match="Playwright is not installed"):
            generate_outro()

    @patch("podcaster.video.intro_outro.sync_playwright")
    def test_generates_outro_with_repos(self, mock_pw, tmp_path):
        browser = _make_mock_browser(tmp_path)
        mock_pw.return_value.start.return_value.chromium.launch.return_value = browser

        video_file = tmp_path / "raw" / "mock_outro.webm"
        video_file.parent.mkdir(parents=True, exist_ok=True)
        video_file.write_bytes(b"fake-webm")
        video_mock = MagicMock()
        video_mock.path.return_value = str(video_file)
        page = browser.new_context.return_value.new_page.return_value
        type(page).video = PropertyMock(return_value=video_mock)

        config = OutroConfig(repo_urls=(
            "https://github.com/jmservera/SquadScope",
        ))
        result = generate_outro(config=config, output_dir=tmp_path)
        assert result.exists()
        assert "outro" in result.name


class TestGenerateIntroOutro:
    """Tests for generate_intro_outro combined function."""

    def test_raises_when_both_none(self):
        with pytest.raises(ValueError, match="At least one"):
            generate_intro_outro(intro_config=None, outro_config=None)

    @patch("podcaster.video.intro_outro._PLAYWRIGHT_AVAILABLE", False)
    def test_raises_without_playwright(self):
        with pytest.raises(RuntimeError, match="Playwright is not installed"):
            generate_intro_outro(intro_config=IntroConfig())

    @patch("podcaster.video.intro_outro.sync_playwright")
    def test_generates_both(self, mock_pw, tmp_path):
        browser = MagicMock()
        mock_pw.return_value.start.return_value.chromium.launch.return_value = browser

        call_count = 0

        def make_video_file():
            nonlocal call_count
            call_count += 1
            video_file = tmp_path / "raw" / f"mock_{call_count}.webm"
            video_file.parent.mkdir(parents=True, exist_ok=True)
            video_file.write_bytes(b"fake-webm")
            video_mock = MagicMock()
            video_mock.path.return_value = str(video_file)
            return video_mock

        def new_page_side_effect():
            page = MagicMock()
            type(page).video = PropertyMock(return_value=make_video_file())
            return page

        context = MagicMock()
        context.new_page.side_effect = new_page_side_effect
        browser.new_context.return_value = context

        result = generate_intro_outro(
            intro_config=IntroConfig(),
            outro_config=OutroConfig(),
            output_dir=tmp_path,
        )
        assert result.intro_path is not None
        assert result.outro_path is not None
        assert result.intro_path.exists()
        assert result.outro_path.exists()
        assert result.output_dir == tmp_path

    @patch("podcaster.video.intro_outro.sync_playwright")
    def test_intro_only(self, mock_pw, tmp_path):
        browser = MagicMock()
        mock_pw.return_value.start.return_value.chromium.launch.return_value = browser

        video_file = tmp_path / "raw" / "mock.webm"
        video_file.parent.mkdir(parents=True, exist_ok=True)
        video_file.write_bytes(b"fake-webm")
        video_mock = MagicMock()
        video_mock.path.return_value = str(video_file)
        page = MagicMock()
        type(page).video = PropertyMock(return_value=video_mock)
        browser.new_context.return_value.new_page.return_value = page

        result = generate_intro_outro(
            intro_config=IntroConfig(), outro_config=None,
            output_dir=tmp_path,
        )
        assert result.intro_path is not None
        assert result.outro_path is None

    @patch("podcaster.video.intro_outro.sync_playwright")
    def test_outro_only(self, mock_pw, tmp_path):
        browser = MagicMock()
        mock_pw.return_value.start.return_value.chromium.launch.return_value = browser

        video_file = tmp_path / "raw" / "mock.webm"
        video_file.parent.mkdir(parents=True, exist_ok=True)
        video_file.write_bytes(b"fake-webm")
        video_mock = MagicMock()
        video_mock.path.return_value = str(video_file)
        page = MagicMock()
        type(page).video = PropertyMock(return_value=video_mock)
        browser.new_context.return_value.new_page.return_value = page

        result = generate_intro_outro(
            intro_config=None, outro_config=OutroConfig(),
            output_dir=tmp_path,
        )
        assert result.intro_path is None
        assert result.outro_path is not None

    @patch("podcaster.video.intro_outro.sync_playwright")
    def test_closes_browser_on_success(self, mock_pw, tmp_path):
        browser = MagicMock()
        pw_instance = MagicMock()
        pw_instance.chromium.launch.return_value = browser
        mock_pw.return_value.start.return_value = pw_instance

        video_file = tmp_path / "raw" / "mock.webm"
        video_file.parent.mkdir(parents=True, exist_ok=True)
        video_file.write_bytes(b"fake-webm")
        video_mock = MagicMock()
        video_mock.path.return_value = str(video_file)
        page = MagicMock()
        type(page).video = PropertyMock(return_value=video_mock)
        browser.new_context.return_value.new_page.return_value = page

        generate_intro_outro(
            intro_config=IntroConfig(), output_dir=tmp_path,
        )
        browser.close.assert_called_once()
        pw_instance.stop.assert_called_once()
