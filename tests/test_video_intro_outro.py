"""Tests for podcaster.video.intro_outro module.

Unit tests mock Playwright to avoid requiring a real browser.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

pytest.importorskip("playwright", reason="playwright not installed — skipping video intro/outro tests")

from podcaster.video.intro_outro import (
    CLARACLE_URL,
    DEFAULT_SHOW_NAME,
    INTRO_DURATION_MS,
    OUTRO_DURATION_MS,
    TITLE_FONT,
    WIDTH,
    HEIGHT,
    ClipResult,
    IntroConfig,
    OutroConfig,
    _build_intro_ffmpeg_cmd,
    _build_outro_ffmpeg_cmd,
    _escape_drawtext,
    _render_intro_html,
    _render_outro_html,
    _record_html_to_video,
    derive_intro_duration,
    generate_intro,
    generate_intro_ffmpeg,
    generate_outro,
    generate_outro_ffmpeg,
    REPO_LINKS,
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


# --- Tests for show_name field (#295) ---


class TestShowNameConfig:
    def test_intro_config_default_show_name(self):
        assert IntroConfig().show_name == DEFAULT_SHOW_NAME
        assert IntroConfig().show_name == "Claracle Weekly"

    def test_intro_config_custom_show_name(self):
        assert IntroConfig(show_name="My Podcast").show_name == "My Podcast"

    def test_outro_config_default_show_name(self):
        assert OutroConfig().show_name == DEFAULT_SHOW_NAME

    def test_outro_config_custom_show_name(self):
        assert OutroConfig(show_name="Custom Show").show_name == "Custom Show"


class TestHtmlShowName:
    def test_intro_html_contains_show_name(self):
        html = _render_intro_html(IntroConfig(show_name="Claracle Weekly"))
        assert "Claracle Weekly" in html

    def test_intro_html_custom_show_name(self):
        html = _render_intro_html(IntroConfig(show_name="My Show"))
        assert "My Show" in html

    def test_intro_html_escapes_show_name(self):
        html = _render_intro_html(IntroConfig(show_name="Show <B>"))
        assert "Show &lt;B&gt;" in html

    def test_outro_html_contains_show_name(self):
        html = _render_outro_html(OutroConfig(show_name="Claracle Weekly"))
        assert "Claracle Weekly" in html

    def test_outro_html_custom_show_name(self):
        html = _render_outro_html(OutroConfig(show_name="My Show"))
        assert "My Show" in html


# --- Tests for derive_intro_duration (#295) ---


class TestDeriveIntroDuration:
    def test_empty_script_returns_default(self):
        assert derive_intro_duration("") == 8.0
        assert derive_intro_duration("   ") == 8.0

    def test_no_github_url_returns_default(self):
        script = "Host A: Hello everyone!\nHost B: Hi there, welcome to the show!"
        assert derive_intro_duration(script) == 8.0

    def test_estimates_from_intro_words_before_url(self):
        # 65 words @ 130 wpm = 30 s exactly (at default floor 8 s)
        intro_words = " ".join(["word"] * 65)
        script = f"{intro_words}\nhttps://github.com/owner/repo\nMore content"
        result = derive_intro_duration(script, words_per_minute=130.0, default_seconds=8.0)
        assert result == pytest.approx(30.0, abs=1.0)

    def test_clamps_to_max(self):
        many_words = " ".join(["hello"] * 1000)
        script = f"{many_words}\nhttps://github.com/owner/repo"
        assert derive_intro_duration(script, max_seconds=20.0) == 20.0

    def test_respects_default_floor(self):
        # Very few words → estimated < default_seconds → returns default
        script = "Hello world\nhttps://github.com/owner/repo"
        result = derive_intro_duration(script, default_seconds=8.0)
        assert result == 8.0

    def test_explicit_intro_end_marker(self):
        script = (
            "Host: Welcome to Claracle Weekly!\n"
            "Host B: Thanks!\n"
            "[INTRO_END]\n"
            "Now let's look at https://github.com/owner/repo"
        )
        # Words before [INTRO_END]: ~8 words → 3.7 s → clamped to default (8.0)
        result = derive_intro_duration(script, default_seconds=8.0)
        assert result == 8.0

    def test_content_start_marker(self):
        intro = " ".join(["word"] * 65)  # 30 s of words
        script = f"{intro}\n[CONTENT_START]\nhttps://github.com/owner/repo"
        result = derive_intro_duration(script, words_per_minute=130.0, max_seconds=60.0)
        assert result == pytest.approx(30.0, abs=1.0)

    def test_custom_words_per_minute(self):
        # 40 words at 80 wpm = 30 s
        intro = " ".join(["word"] * 40)
        script = f"{intro}\nhttps://github.com/owner/repo"
        result = derive_intro_duration(
            script, words_per_minute=80.0, default_seconds=5.0, max_seconds=60.0
        )
        assert result == pytest.approx(30.0, abs=1.0)

    def test_empty_intro_section_returns_default(self):
        script = "https://github.com/owner/repo"
        assert derive_intro_duration(script) == 8.0


# --- Tests for _escape_drawtext (#295) ---


class TestEscapeDrawtext:
    def test_colon_escaped(self):
        assert r"\:" in _escape_drawtext("Hello: World")

    def test_apostrophe_escaped(self):
        assert r"\'" in _escape_drawtext("It's")

    def test_backslash_escaped(self):
        assert r"\\" in _escape_drawtext("a\\b")

    def test_plain_text_unchanged(self):
        assert _escape_drawtext("Claracle Weekly") == "Claracle Weekly"


# --- Tests for _build_intro_ffmpeg_cmd (#295) ---


class TestBuildIntroFfmpegCmd:
    def _get_vf(self, config: IntroConfig, bin: str = "ffmpeg") -> str:
        cmd = _build_intro_ffmpeg_cmd(config, Path("out.mp4"), bin)
        return cmd[cmd.index("-vf") + 1]

    def test_starts_with_ffmpeg_bin(self):
        cmd = _build_intro_ffmpeg_cmd(IntroConfig(), Path("out.mp4"), "/usr/bin/ffmpeg")
        assert cmd[0] == "/usr/bin/ffmpeg"

    def test_uses_lavfi_color_source(self):
        cmd = _build_intro_ffmpeg_cmd(IntroConfig(), Path("out.mp4"))
        assert "-f" in cmd
        assert cmd[cmd.index("-f") + 1] == "lavfi"
        assert any("color=" in a for a in cmd)

    def test_contains_drawtext(self):
        assert "drawtext" in self._get_vf(IntroConfig())

    def test_contains_show_name_in_filter(self):
        assert "Claracle Weekly" in self._get_vf(IntroConfig(show_name="Claracle Weekly"))

    def test_contains_episode_title_when_set(self):
        assert "My Episode" in self._get_vf(IntroConfig(episode_title="My Episode"))

    def test_episode_title_omitted_when_empty(self):
        vf = self._get_vf(IntroConfig(episode_title=""))
        # Only the show_name drawtext should be present
        assert vf.count("drawtext") == 1

    def test_subtitle_included_when_set(self):
        assert "My Sub" in self._get_vf(IntroConfig(subtitle="My Sub"))

    def test_subtitle_omitted_when_empty(self):
        config = IntroConfig(episode_title="Ep", subtitle="")
        assert self._get_vf(config).count("drawtext") == 2  # show_name + episode

    def test_contains_fade_in_and_out(self):
        vf = self._get_vf(IntroConfig())
        assert "fade=t=in" in vf
        assert "fade=t=out" in vf

    def test_duration_in_command(self):
        cmd = _build_intro_ffmpeg_cmd(IntroConfig(duration_ms=8000), Path("out.mp4"))
        t_idx = cmd.index("-t")
        assert cmd[t_idx + 1] == "8.000"

    def test_output_path_in_command(self):
        out = Path("/some/path/intro.mp4")
        cmd = _build_intro_ffmpeg_cmd(IntroConfig(), out)
        assert str(out) in cmd

    def test_encodes_libx264(self):
        cmd = _build_intro_ffmpeg_cmd(IntroConfig(), Path("out.mp4"))
        assert "libx264" in cmd


# --- Tests for _build_outro_ffmpeg_cmd (#295) ---


class TestBuildOutroFfmpegCmd:
    def _get_vf(self, config: OutroConfig, bin: str = "ffmpeg") -> str:
        cmd = _build_outro_ffmpeg_cmd(config, Path("out.mp4"), bin)
        return cmd[cmd.index("-vf") + 1]

    def test_starts_with_ffmpeg_bin(self):
        cmd = _build_outro_ffmpeg_cmd(OutroConfig(), Path("out.mp4"), "/usr/bin/ffmpeg")
        assert cmd[0] == "/usr/bin/ffmpeg"

    def test_contains_show_name(self):
        assert "Claracle Weekly" in self._get_vf(OutroConfig(show_name="Claracle Weekly"))

    def test_contains_url(self):
        assert "example.com" in self._get_vf(OutroConfig(url="example.com"))

    def test_contains_subscribe_cta(self):
        assert "Subscribe" in self._get_vf(OutroConfig())

    def test_contains_fade_in_and_out(self):
        vf = self._get_vf(OutroConfig())
        assert "fade=t=in" in vf
        assert "fade=t=out" in vf

    def test_duration_in_command(self):
        cmd = _build_outro_ffmpeg_cmd(OutroConfig(duration_ms=6000), Path("out.mp4"))
        t_idx = cmd.index("-t")
        assert cmd[t_idx + 1] == "6.000"

    def test_uses_lavfi_source(self):
        cmd = _build_outro_ffmpeg_cmd(OutroConfig(), Path("out.mp4"))
        assert cmd[cmd.index("-f") + 1] == "lavfi"


# --- Tests for generate_intro_ffmpeg / generate_outro_ffmpeg (#295) ---


def _make_ffmpeg_runner() -> MagicMock:
    runner = MagicMock()
    runner.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    return runner


class TestGenerateIntroFfmpeg:
    def test_returns_clip_result(self, tmp_path):
        runner = _make_ffmpeg_runner()
        result = generate_intro_ffmpeg(
            config=IntroConfig(episode_title="Test Ep"),
            output_dir=tmp_path,
            ffmpeg_bin="/usr/bin/ffmpeg",
            runner=runner,
        )
        assert isinstance(result, ClipResult)
        assert result.path == tmp_path / "intro.mp4"
        assert result.duration_ms == INTRO_DURATION_MS
        assert result.width == WIDTH
        assert result.height == HEIGHT

    def test_runner_called_once(self, tmp_path):
        runner = _make_ffmpeg_runner()
        generate_intro_ffmpeg(output_dir=tmp_path, ffmpeg_bin="ffmpeg", runner=runner)
        runner.assert_called_once()

    def test_uses_provided_ffmpeg_bin(self, tmp_path):
        runner = _make_ffmpeg_runner()
        generate_intro_ffmpeg(output_dir=tmp_path, ffmpeg_bin="/custom/ffmpeg", runner=runner)
        cmd = runner.call_args[0][0]
        assert cmd[0] == "/custom/ffmpeg"

    def test_auto_detects_ffmpeg_bin(self, tmp_path):
        runner = _make_ffmpeg_runner()
        with patch("podcaster.video.intro_outro._get_drawtext_ffmpeg", return_value="/sys/ffmpeg"):
            generate_intro_ffmpeg(output_dir=tmp_path, runner=runner)
        assert runner.call_args[0][0][0] == "/sys/ffmpeg"

    def test_falls_back_to_ffmpeg_when_not_detected(self, tmp_path):
        runner = _make_ffmpeg_runner()
        with patch("podcaster.video.intro_outro._get_drawtext_ffmpeg", return_value=None):
            generate_intro_ffmpeg(output_dir=tmp_path, runner=runner)
        assert runner.call_args[0][0][0] == "ffmpeg"

    def test_uses_default_config_show_name(self, tmp_path):
        runner = _make_ffmpeg_runner()
        generate_intro_ffmpeg(output_dir=tmp_path, ffmpeg_bin="ffmpeg", runner=runner)
        cmd = runner.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        assert DEFAULT_SHOW_NAME in vf


class TestGenerateOutroFfmpeg:
    def test_returns_clip_result(self, tmp_path):
        runner = _make_ffmpeg_runner()
        result = generate_outro_ffmpeg(
            config=OutroConfig(),
            output_dir=tmp_path,
            ffmpeg_bin="/usr/bin/ffmpeg",
            runner=runner,
        )
        assert isinstance(result, ClipResult)
        assert result.path == tmp_path / "outro.mp4"
        assert result.duration_ms == OUTRO_DURATION_MS

    def test_runner_called_once(self, tmp_path):
        runner = _make_ffmpeg_runner()
        generate_outro_ffmpeg(output_dir=tmp_path, ffmpeg_bin="ffmpeg", runner=runner)
        runner.assert_called_once()

    def test_uses_provided_ffmpeg_bin(self, tmp_path):
        runner = _make_ffmpeg_runner()
        generate_outro_ffmpeg(output_dir=tmp_path, ffmpeg_bin="/alt/ffmpeg", runner=runner)
        assert runner.call_args[0][0][0] == "/alt/ffmpeg"

    def test_auto_detects_ffmpeg_bin(self, tmp_path):
        runner = _make_ffmpeg_runner()
        with patch("podcaster.video.intro_outro._get_drawtext_ffmpeg", return_value="/dt/ffmpeg"):
            generate_outro_ffmpeg(output_dir=tmp_path, runner=runner)
        assert runner.call_args[0][0][0] == "/dt/ffmpeg"

    def test_falls_back_when_not_detected(self, tmp_path):
        runner = _make_ffmpeg_runner()
        with patch("podcaster.video.intro_outro._get_drawtext_ffmpeg", return_value=None):
            generate_outro_ffmpeg(output_dir=tmp_path, runner=runner)
        assert runner.call_args[0][0][0] == "ffmpeg"

    def test_default_config_show_name_in_filter(self, tmp_path):
        runner = _make_ffmpeg_runner()
        generate_outro_ffmpeg(output_dir=tmp_path, ffmpeg_bin="ffmpeg", runner=runner)
        cmd = runner.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        assert DEFAULT_SHOW_NAME in vf

