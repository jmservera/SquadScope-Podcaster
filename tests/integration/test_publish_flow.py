from __future__ import annotations

from pathlib import Path

import pytest

import podcaster.orchestration as orchestration
from podcaster.config import SpotifyPublishConfig
from podcaster.publish import PublishResult, publish_episode
from podcaster.video.distribution import (
    DistributionResult,
    VideoDistributionConfig,
    distribute_video,
)

pytestmark = pytest.mark.integration


def test_audio_only_publish_flow_calls_publish_episode(
    monkeypatch,
    fake_mp3: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_publish_episode(mp3_path, title, description, **kwargs):
        calls.append(
            {
                "mp3_path": Path(mp3_path),
                "title": title,
                "description": description,
                "kwargs": kwargs,
            }
        )
        return PublishResult(status="published", dry_run=True)

    monkeypatch.setattr(orchestration, "publish_episode", fake_publish_episode)

    manifest = {
        "job_id": "audio-only-265",
        "request": {
            "week": "2026-W25",
            "article_url": "https://example.invalid/post",
            "article_title": "Audio-only integration",
            "spotify_publish": {
                "publish_mode": "immediate",
                "upload_format": "mp3",
            },
        },
    }

    result = orchestration._publish_from_manifest((fake_mp3, None), manifest)

    assert result.status == "published"
    assert calls == [
        {
            "mp3_path": fake_mp3,
            "title": "Audio-only integration",
            "description": (
                "<p>Claracle week 2026-W25.</p>"
                "<p>Source article: https://example.invalid/post</p>"
                f"<p>Generated audio artifact: {fake_mp3.name}</p>"
            ),
            "kwargs": {
                "spotify_publish_config": SpotifyPublishConfig(
                    publish_mode="immediate",
                    upload_format="mp3",
                ),
                "year": 2026,
                "week": 25,
                "article_title": "Audio-only integration",
                "wav_path": None,
            },
        }
    ]


def test_video_distribution_dry_run_returns_expected_urls(fake_mp4: Path) -> None:
    result = distribute_video(
        fake_mp4,
        job_id="video-only-265",
        title="Video integration",
        description="Dry-run video distribution",
        duration_seconds=42.0,
        config=VideoDistributionConfig(
            youtube_enabled=True,
            spotify_rss_enabled=True,
            blob_archive_enabled=True,
            dry_run=True,
        ),
    )

    assert isinstance(result, DistributionResult)
    assert result.status == "completed"
    assert result.youtube_id == "dry-run-id"
    assert result.youtube_url == "https://youtube.com/watch?v=dry-run-id"
    assert result.spotify_rss_updated is True
    assert (
        result.blob_path
        == "https://dry-run.blob.core.windows.net/jobs/video-only-265/video/video-only-265.mp4"
    )
    assert result.errors == []


def test_audio_and_video_publish_paths_are_independent(
    monkeypatch,
    tmp_path: Path,
    fake_mp3: Path,
    fake_mp4: Path,
) -> None:
    audio_path = tmp_path / "combined.mp3"
    audio_path.write_bytes(fake_mp3.read_bytes())
    video_path = audio_path.with_suffix(".mp4")
    video_path.write_bytes(fake_mp4.read_bytes())

    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
    monkeypatch.setenv("SPOTIFY_SHOW_ID", "fake-show-id")
    monkeypatch.setenv("SP_DC", "fake-sp-dc")
    monkeypatch.setenv("SP_KEY", "fake-sp-key")

    publish_result = publish_episode(
        audio_path,
        "Combined integration",
        "<p>Audio and video artifacts both exist.</p>",
        spotify_publish_config=SpotifyPublishConfig(
            publish_mode="immediate",
            upload_format="mp3",
        ),
    )
    distribution_result = distribute_video(
        video_path,
        job_id="combined-265",
        title="Combined integration",
        description="Dry-run distribution for combined artifacts",
        duration_seconds=30.0,
        config=VideoDistributionConfig(
            youtube_enabled=True,
            spotify_rss_enabled=True,
            blob_archive_enabled=True,
            dry_run=True,
        ),
    )

    assert isinstance(publish_result, PublishResult)
    assert publish_result.dry_run is True
    assert publish_result.status == "published"
    # MP4 is preferred when present alongside audio
    assert publish_result.details["upload_path"] == str(video_path)
    assert publish_result.details["content_type"] == "video/mp4"

    assert isinstance(distribution_result, DistributionResult)
    assert distribution_result.status == "completed"
    assert distribution_result.youtube_url == "https://youtube.com/watch?v=dry-run-id"
    assert distribution_result.blob_path is not None
    assert distribution_result.blob_path.endswith("/combined-265.mp4")
