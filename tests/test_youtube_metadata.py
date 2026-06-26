"""Tests for podcaster.video.youtube_metadata — metadata + thumbnail (#445)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from podcaster.video import youtube_metadata as ym


# --- metadata ---------------------------------------------------------------


class TestBuildYouTubeMetadata:
    def test_english_title_format_with_week(self):
        meta = ym.build_youtube_metadata(
            "Kubernetes 1.30 deep dive", "Notes", week=26, locale="en"
        )
        assert meta.title == "Claracle Weekly — W26: Kubernetes 1.30 deep dive"
        assert meta.category_id == ym.YOUTUBE_CATEGORY_SCIENCE_TECH == "28"
        assert meta.privacy_status == "unlisted"
        assert meta.default_language == "en"
        assert meta.default_audio_language == "en"

    def test_spanish_localized_label_and_language(self):
        meta = ym.build_youtube_metadata(
            "Repaso de Kubernetes", "Notas", week=3, locale="es-MX"
        )
        assert meta.title.startswith("Claracle Semanal — W03:")
        assert meta.default_language == "es"
        assert meta.default_audio_language == "es"
        assert "español" in meta.tags

    def test_french_localized_label(self):
        meta = ym.build_youtube_metadata("Tour Kubernetes", "Notes", locale="fr")
        assert meta.title.startswith("Claracle Hebdo — ")
        assert meta.default_language == "fr"
        assert "français" in meta.tags

    def test_unknown_locale_falls_back_to_english(self):
        meta = ym.build_youtube_metadata("Topic", "Notes", locale="de")
        assert meta.title.startswith("Claracle Weekly")
        assert meta.default_language == "en"

    def test_does_not_double_prefix_show_label(self):
        meta = ym.build_youtube_metadata(
            "Claracle Weekly — W10: Already labelled", "Notes", week=11
        )
        assert meta.title == "Claracle Weekly — W10: Already labelled"

    def test_title_truncated_to_100_chars(self):
        meta = ym.build_youtube_metadata("x" * 200, "Notes", week=1)
        assert len(meta.title) == ym.MAX_TITLE_CHARS == 100

    def test_description_truncated(self):
        meta = ym.build_youtube_metadata("Topic", "d" * 6000)
        body = meta.to_snippet()
        assert len(body["description"]) == ym.MAX_DESCRIPTION_CHARS

    def test_explicit_tags_respected_and_clamped(self):
        long_tags = [f"tag{i}" * 5 for i in range(100)]
        meta = ym.build_youtube_metadata("Topic", "Notes", tags=long_tags)
        joined = ",".join(meta.tags)
        assert len(joined) <= ym.MAX_TAGS_TOTAL_CHARS
        assert meta.tags[0] == long_tags[0]

    def test_custom_show_label_override(self):
        meta = ym.build_youtube_metadata(
            "Topic", "Notes", week=5, show_label="Claracle Especial"
        )
        assert meta.title == "Claracle Especial — W05: Topic"

    def test_request_body_structure(self):
        meta = ym.build_youtube_metadata(
            "Topic", "Notes", week=1, privacy_status="private", made_for_kids=False
        )
        body = meta.to_request_body()
        assert set(body) == {"snippet", "status"}
        assert body["snippet"]["categoryId"] == "28"
        assert body["status"]["privacyStatus"] == "private"
        assert body["status"]["selfDeclaredMadeForKids"] is False


# --- thumbnail command / content type ---------------------------------------


class TestThumbnailCommand:
    def test_command_contains_expected_args(self):
        cmd = ym.build_thumbnail_command(
            Path("/in.mp4"), Path("/out.jpg"), timestamp_seconds=4.5
        )
        assert "ffmpeg" in cmd[0]
        assert "-frames:v" in cmd and cmd[cmd.index("-frames:v") + 1] == "1"
        assert "/in.mp4" in cmd
        assert "/out.jpg" in cmd
        # 16:9 scale+crop filter present
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=1280:720" in vf and "crop=1280:720" in vf
        # Seek timestamp formatted
        assert cmd[cmd.index("-ss") + 1] == "4.500"

    def test_negative_timestamp_clamped_to_zero(self):
        cmd = ym.build_thumbnail_command(
            Path("/in.mp4"), Path("/out.jpg"), timestamp_seconds=-2
        )
        assert cmd[cmd.index("-ss") + 1] == "0.000"

    def test_content_type_inference(self):
        assert ym.thumbnail_content_type(Path("t.jpg")) == "image/jpeg"
        assert ym.thumbnail_content_type(Path("t.jpeg")) == "image/jpeg"
        assert ym.thumbnail_content_type(Path("t.png")) == "image/png"
        # Unknown suffix defaults to JPEG.
        assert ym.thumbnail_content_type(Path("t.webp")) == "image/jpeg"


# --- thumbnail extraction (real ffmpeg) -------------------------------------


_FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg not available")
class TestExtractThumbnail:
    @pytest.fixture
    def sample_video(self, tmp_path: Path) -> Path:
        video = tmp_path / "sample.mp4"
        subprocess.run(
            [
                _FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=1",
                "-pix_fmt", "yuv420p", str(video),
            ],
            check=True,
            capture_output=True,
        )
        return video

    def test_extracts_valid_jpeg(self, sample_video: Path, tmp_path: Path):
        out = tmp_path / "thumb.jpg"
        result = ym.extract_thumbnail(sample_video, out, timestamp_seconds=0.0)
        assert result == out
        assert out.exists() and out.stat().st_size > 0
        # JPEG SOI magic bytes.
        assert out.read_bytes()[:2] == b"\xff\xd8"

    def test_missing_video_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            ym.extract_thumbnail(tmp_path / "nope.mp4", tmp_path / "t.jpg")


def test_extract_thumbnail_without_ffmpeg_raises(tmp_path: Path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 1024)
    monkeypatch.setattr(ym.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="ffmpeg is not available"):
        ym.extract_thumbnail(video, tmp_path / "t.jpg", ffmpeg_bin=None)


# --- thumbnail upload --------------------------------------------------------


class _FakeTransport:
    def __init__(self, responses: list[tuple[int, bytes]] | None = None):
        self.requests: list[dict] = []
        self._responses = list(responses or [])
        self._idx = 0

    def request(self, url, *, method="GET", headers=None, data=None):
        self.requests.append(
            {"url": url, "method": method, "headers": headers, "data": data}
        )
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return (200, b"{}")


class TestUploadThumbnail:
    @pytest.fixture
    def image(self, tmp_path: Path) -> Path:
        img = tmp_path / "thumb.jpg"
        img.write_bytes(b"\xff\xd8" + b"\x00" * 100)
        return img

    def test_success(self, image: Path):
        transport = _FakeTransport([(200, b"{}")])
        ok = ym.upload_thumbnail("yt-123", image, "tok", transport=transport)
        assert ok is True
        req = transport.requests[0]
        assert "videoId=yt-123" in req["url"]
        assert req["method"] == "POST"
        assert req["headers"]["Authorization"] == "Bearer tok"
        assert req["headers"]["Content-Type"] == "image/jpeg"

    def test_http_error_returns_false(self, image: Path):
        transport = _FakeTransport([(403, b"forbidden")])
        assert ym.upload_thumbnail("yt-1", image, "tok", transport=transport) is False

    def test_transport_exception_returns_false(self, image: Path):
        class Boom:
            def request(self, *a, **k):
                raise RuntimeError("network down")

        assert ym.upload_thumbnail("yt-1", image, "tok", transport=Boom()) is False

    def test_token_never_logged(self, image: Path, caplog):
        transport = _FakeTransport([(200, b"{}")])
        with caplog.at_level("DEBUG"):
            ym.upload_thumbnail("yt-1", image, "super-secret-token", transport=transport)
        assert "super-secret-token" not in caplog.text

    def test_missing_video_id_raises(self, image: Path):
        with pytest.raises(ValueError, match="video_id is required"):
            ym.upload_thumbnail("", image, "tok", transport=_FakeTransport())

    def test_empty_image_raises(self, tmp_path: Path):
        img = tmp_path / "empty.jpg"
        img.write_bytes(b"")
        with pytest.raises(ValueError, match="empty"):
            ym.upload_thumbnail("yt-1", img, "tok", transport=_FakeTransport())

    def test_oversized_image_raises(self, tmp_path: Path):
        img = tmp_path / "big.jpg"
        img.write_bytes(b"\x00" * (ym.THUMBNAIL_MAX_BYTES + 1))
        with pytest.raises(ValueError, match="too large"):
            ym.upload_thumbnail("yt-1", img, "tok", transport=_FakeTransport())


class TestGenerateAndSetThumbnail:
    def test_extraction_failure_returns_false_without_upload(self, tmp_path: Path):
        transport = _FakeTransport([(200, b"{}")])
        # Missing video → extract fails → no upload attempted.
        ok = ym.generate_and_set_thumbnail(
            tmp_path / "missing.mp4",
            "yt-1",
            "tok",
            tmp_path / "t.jpg",
            transport=transport,
        )
        assert ok is False
        assert transport.requests == []
