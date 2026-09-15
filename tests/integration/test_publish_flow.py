from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import podcaster.orchestration as orchestration
from podcaster.config import SpotifyPublishConfig
from podcaster.costs import build_cost_ledger
from podcaster.generation import manifest_bytes
from podcaster.music import TRACK_ATTRIBUTION
from podcaster.publish import publish_episode
from podcaster.storage import LocalStorageBackend
from podcaster.video.distribution import (
    DistributionResult,
    VideoDistributionConfig,
    distribute_video,
)

pytestmark = pytest.mark.integration


def test_audio_only_publish_requires_approved_gate(
    monkeypatch,
    tmp_path: Path,
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
        return orchestration.PublishResult(status="published", dry_run=True)

    monkeypatch.setattr(orchestration, "publish_episode", fake_publish_episode)

    job_id = "audio-only-265"
    mp3_blob = f"jobs/{job_id}/audio/{job_id}.mp3"
    wav_blob = f"jobs/{job_id}/audio/{job_id}.wav"
    manifest = {
        "job_id": job_id,
        "status": "synthesized_review_ready",
        "request": {
            "week": "2026-W25",
            "article_url": "https://example.invalid/post",
            "article_title": "Audio-only integration",
            "spotify_publish": {
                "publish_mode": "immediate",
                "upload_format": "mp3",
            },
        },
        "review": {"status": "pending", "audit_trail": [], "gate": {"status": "blocked"}},
        "cost_ledger": build_cost_ledger(
            week="2026-W25",
            month="2026-06",
            provider="openai-tts",
            voice="fable,alloy",
            voice_config_hash="abc123",
            billable_characters=100,
            duration_seconds=300,
            audio_byte_length=3,
            staged_byte_length=6,
        ),
        "generation": {
            "audio_mode": "synthesized",
            "audio_validation": {"status": "passed", "ready": True},
            "synthesis_runner": {
                "status": "completed",
                "audio": {
                    "path": mp3_blob,
                    "artifacts": {
                        "mp3": {"path": mp3_blob},
                        "wav": {"path": wav_blob},
                    },
                },
            },
        },
        "publishing": {
            "mode": "review_gate",
            "eligible": False,
            "packet_ready": True,
            "blocked_by": ["human_review"],
            "readiness_checks": {
                "editorial_review_complete": False,
                "real_audio_available": True,
                "audio_validation_passed": True,
            },
        },
        "lifecycle": {"status": "synthesized_review_ready", "revision": 1, "transitions": []},
        "artifacts": {
            mp3_blob: {"url": f"https://example.invalid/{mp3_blob}"},
            wav_blob: {"url": f"https://example.invalid/{wav_blob}"},
        },
    }
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    storage.put_bytes(
        orchestration.manifest_path(job_id),
        manifest_bytes(manifest),
        "application/json; charset=utf-8",
    )
    storage.put_bytes(mp3_blob, fake_mp3.read_bytes(), "audio/mpeg")
    storage.put_bytes(wav_blob, b"wav", "audio/wav")

    blocked = orchestration.publish_staged_job(
        job_id,
        storage=storage,
        actor="operator",
        trigger="manual_request",
    )
    assert blocked.publish_result is None
    assert blocked.manifest["publishing"]["result"]["status"] == "blocked"
    assert calls == []

    approved = orchestration.process_review_decision(
        job_id,
        reviewer="leela",
        decision="approved",
        reviewed_at="2026-09-15T17:00:00Z",
        storage=storage,
    )

    assert approved.publish_result is not None
    assert approved.publish_result.status == "published"
    assert len(calls) == 1
    call = calls[0]
    assert call["mp3_path"] == storage.root / mp3_blob
    assert call["title"] == "Audio-only integration"
    assert "Source article: https://example.invalid/post" in str(call["description"])
    assert f"Intro/outro music: {TRACK_ATTRIBUTION}" in str(call["description"])
    kwargs = call["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["spotify_publish_config"].publish_mode == "immediate"
    assert kwargs["spotify_publish_config"].upload_format == "mp3"
    assert kwargs["year"] == 2026
    assert kwargs["week"] == 25
    assert kwargs["article_title"] == "Audio-only integration"
    assert kwargs["wav_path"] == storage.root / wav_blob
    assert kwargs["language"] == "en"


def test_review_gated_audio_blocks_partial_implicit_canonical_identity(
    monkeypatch,
    fake_mp3: Path,
) -> None:
    publish = MagicMock()
    monkeypatch.setattr(orchestration, "publish_episode", publish)
    manifest = {
        "job_id": "audio-partial-canonical",
        "request": {
            "week": "2026-W25",
            "article_url": "https://example.invalid/post",
            "publish_run_id": "123",
            "spotify_publish": {"publish_mode": "draft", "upload_format": "mp3"},
        },
        "lifecycle": {"transitions": [{"to": "accepted"}]},
    }

    result = orchestration._publish_from_manifest(
        (fake_mp3, None),
        manifest,
        storage=MagicMock(),
        job_id=manifest["job_id"],
    )

    assert result.outcome == "publication_unknown"
    assert result.details["retry_blocked"] is True
    publish.assert_not_called()


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
    monkeypatch.setenv("SPOTIFY_ALLOW_LIVE_PUBLISH", "true")
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

    assert publish_result.status == "published"
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
