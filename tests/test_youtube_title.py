from __future__ import annotations

import json

import pytest

from podcaster.video.distribution import (
    VideoDistributionConfig,
    upload_to_youtube,
    youtube_publication_metadata,
)
from podcaster.video.youtube import build_snippet
from podcaster.video.youtube_metadata import build_youtube_metadata
from podcaster.video.youtube_title import clamp_youtube_title
from tests.test_video_distribution import FakeTransport
from tests.test_youtube_single_session import FakeResumableYouTube


@pytest.mark.parametrize("week", ["W42", "2026-W43"])
@pytest.mark.parametrize("locale", ["", " [ES]", " [FR]"])
@pytest.mark.parametrize("topic", ["Agents and Rust " * 30, "é宇宙🤖" * 100])
def test_direct_and_chunked_titles_reserve_full_week_and_locale(tmp_path, week, locale, topic):
    title = f"{topic} | {week}{locale}"
    expected = clamp_youtube_title(title)
    assert len(expected) <= 100
    assert expected.endswith(f" | {week}{locale}")
    assert build_snippet(title, "description")["title"] == expected
    assert (
        build_youtube_metadata(title, "description", show_label="", locale="en")
        .to_snippet()["title"]
        .endswith(f" | {week}{locale}")
    )
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"x" * 2048)
    transport = FakeTransport()
    config = VideoDistributionConfig(
        youtube_enabled=True,
        youtube_client_id="test",
        youtube_client_secret="test",
        youtube_refresh_token="test",
    )
    upload_to_youtube(video, title, "description", config, transport=transport)
    request = next(
        item
        for item in transport.requests
        if item["url"].endswith("uploadType=resumable&part=snippet,status")
        or b'"snippet"' in (item["data"] or b"")
    )
    metadata = json.loads(request["data"])
    assert metadata["snippet"]["title"] == expected


def test_title_removes_only_invalid_provider_characters():
    assert clamp_youtube_title("Café <Rust>\n | W43 [ES]") == "Café Rust | W43 [ES]"


def test_short_title_and_complete_identity_unchanged():
    title = "Devices and Rust | W43 [FR]"
    assert clamp_youtube_title(title) == title


def test_unbounded_identity_suffix_is_rejected():
    with pytest.raises(ValueError):
        clamp_youtube_title("topic | W43 " + "x" * 100)


def test_rightmost_valid_marker_preserves_suffix_whitespace_and_trailing_text():
    topic = "Comparison | W42 " + "agent updates " * 10
    suffix = "  — 2026-W43 [ES]  | W44invalid  "
    title = clamp_youtube_title(topic + suffix)
    assert title.startswith("Comparison | W42 ")
    assert title.endswith(suffix)
    assert len(title) <= 100
    assert clamp_youtube_title(title) == title


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("suffix", [" | W42", " | 2026-W43 [ES]", " | W43 [FR]"])
@pytest.mark.parametrize(
    "topic",
    [
        "é宇宙🤖" * 100,
        "Comparison | W42 " + "agent updates " * 10,
        "Comparison | W43 " + "agent updates " * 10,
    ],
)
def test_actual_resumable_paths_keep_week_locale_and_identity_tag(
    tmp_path, monkeypatch, chunked, suffix, topic
):
    metadata = []

    class CaptureTransport(FakeResumableYouTube):
        def request_with_headers(self, url, *, method="GET", headers=None, data=None):
            if method == "POST" and "uploadType=resumable" in url:
                metadata.append(json.loads(data))
            return super().request_with_headers(url, method=method, headers=headers, data=data)

    monkeypatch.setattr(
        "podcaster.video.distribution._MAX_SINGLE_UPLOAD_BYTES", 0 if chunked else 10000
    )
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"x" * 4096)
    title = topic + suffix
    identity_tag = "sqpub-" + "a" * 32
    config = VideoDistributionConfig(
        youtube_enabled=True,
        youtube_client_id="test",
        youtube_client_secret="test",
        youtube_refresh_token="test",
    )
    marker = "Publication " + "b" * 64
    composed = youtube_publication_metadata(title, "é" * 6000, publication_marker=marker)
    assert len(composed["title"]) <= 100
    assert len(composed["description"]) == 5000
    upload_to_youtube(
        video,
        composed["title"],
        composed["description"],
        config,
        transport=CaptureTransport(),
        identity_tag=identity_tag,
        raise_on_failure=True,
    )
    assert len(metadata) == 1
    assert metadata[0]["snippet"]["title"] == clamp_youtube_title(title)
    assert metadata[0]["snippet"]["title"].endswith(suffix)
    assert metadata[0]["snippet"]["description"].endswith("\n\n" + marker)
    assert identity_tag in metadata[0]["snippet"]["tags"]
    assert metadata[0]["status"]["privacyStatus"] == "unlisted"


def test_shared_metadata_preserves_existing_normal_title_and_description_limits():
    composed = youtube_publication_metadata("Short title | W43 [ES]", "normal description")
    assert composed == {"title": "Short title | W43 [ES]", "description": "normal description"}
    assert len(youtube_publication_metadata("x" * 200, "d" * 6000)["title"]) == 100
    assert len(youtube_publication_metadata("normal", "d" * 6000)["description"]) == 5000
