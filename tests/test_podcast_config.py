from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from podcaster import episode
from podcaster.config import HostConfig, PodcastConfig
from podcaster.generation import (
    AI_VOICE_DISCLOSURE,
    HOST_A_NAME,
    HOST_A_STYLE,
    HOST_A_VOICE,
    HOST_B_NAME,
    HOST_B_STYLE,
    HOST_B_VOICE,
    PODCAST_NAME,
    PODCAST_SPOKEN_SITE,
    PODCAST_URL,
    generate_artifacts,
)
from podcaster.jobs import build_job_id, run_generation_job
from podcaster.storage import LocalStorageBackend
from podcaster.validation import validate_payload, validate_payload_details


def test_podcast_config_defaults_match_generation_constants() -> None:
    config = PodcastConfig()
    assert config.name == PODCAST_NAME
    assert config.url == PODCAST_URL
    assert config.spoken_site == PODCAST_SPOKEN_SITE
    assert config.ai_voice_disclosure == AI_VOICE_DISCLOSURE
    assert config.host_a == HostConfig(name=HOST_A_NAME, voice=HOST_A_VOICE, style=HOST_A_STYLE)
    assert config.host_b == HostConfig(name=HOST_B_NAME, voice=HOST_B_VOICE, style=HOST_B_STYLE)


def test_generate_artifacts_without_podcast_config_matches_explicit_defaults() -> None:
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_sha256": "a" * 64,
    }
    created_at = datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc)
    job_id = build_job_id(payload)

    first = generate_artifacts(job_id, payload, created_at)
    second = generate_artifacts(job_id, payload, created_at, config=PodcastConfig())

    assert [(artifact.path, artifact.content, artifact.content_type) for artifact in first] == [
        (artifact.path, artifact.content, artifact.content_type) for artifact in second
    ]


def test_run_generation_job_threads_custom_podcast_config_into_artifacts() -> None:
    artifact_root = Path(".test-artifacts-podcast-config")
    shutil.rmtree(artifact_root, ignore_errors=True)
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "dry_run": True,
        "podcast_config": {
            "name": "SignalWire",
            "url": "https://pod.example.com",
            "spoken_site": "pod.example.com",
            "ai_voice_disclosure": "These hosts are synthetic voices.",
            "host_a": {
                "name": "Ada",
                "voice": "nova",
                "style": "Speak quickly and brightly.",
            },
            "host_b": {
                "name": "Lin",
                "voice": "shimmer",
                "style": "Speak slowly and dryly.",
            },
        },
    }

    result = run_generation_job(
        payload,
        storage=LocalStorageBackend(artifact_root, "https://example.invalid/artifacts"),
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    job_dir = artifact_root / "jobs" / result.response["job_id"]
    script = (job_dir / "script.txt").read_text(encoding="utf-8")
    transcript = (job_dir / "transcript.txt").read_text(encoding="utf-8")
    show_notes = (job_dir / "show-notes.md").read_text(encoding="utf-8")

    assert "Title: SignalWire Podcast – Week 2026-W23" in script
    assert "Podcast: SignalWire (https://pod.example.com)" in script
    assert "Voices: Ada = nova" in script
    assert "Lin = shimmer" in script
    assert "Ada: Welcome to SignalWire 2026-W23 issue!" in script
    assert (
        "Lin: And I'm Lin. One honest heads-up before we dive in — These hosts are "
        "synthetic voices."
        in script
    )
    assert "pod.example.com" in script
    assert "TTS Provider: OpenAI TTS (Ada nova / Lin shimmer)" in transcript
    assert "These hosts are synthetic voices." in show_notes
    assert "[SignalWire](https://pod.example.com)" in show_notes
    assert result.manifest["request"]["podcast_config"] == payload["podcast_config"]

    shutil.rmtree(artifact_root, ignore_errors=True)


def test_parse_script_segments_handles_custom_host_labels() -> None:
    article = episode.sanitize_article(
        week="2026-W24",
        title="Skills go vertical",
        url="https://example.com/article",
        sha256="",
        summary="A summary of the week's signal and noise.",
        beats=[{"topic": "agent skills go vertical", "points": ["point one", "point two"]}],
    )
    podcast_config = PodcastConfig(
        host_a=HostConfig(name="Ada", voice=HOST_A_VOICE, style=HOST_A_STYLE),
        host_b=HostConfig(name="Lin", voice=HOST_B_VOICE, style=HOST_B_STYLE),
    )

    script = episode.build_episode_script(article, podcast_config=podcast_config)
    segments = episode.parse_script_segments(script)

    assert segments[0][0] == "host_a"
    assert segments[1][0] == "host_b"
    assert segments[0][1].startswith("Welcome to Claracle 2026-W24 issue!")
    assert segments[1][1].startswith("Before Ada short-circuits")


def test_validate_payload_rejects_invalid_podcast_config_shapes() -> None:
    errors = validate_payload(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "podcast_config": {
                "url": "ftp://example.com/show",
                "host_a": {"name": "", "voice": 7, "style": ""},
                "host_b": [],
            },
        }
    )

    assert "podcast_config.url must be an http or https URL" in errors
    assert "podcast_config.host_a.name must be a non-empty string" in errors
    assert "podcast_config.host_a.voice must be a non-empty string" in errors
    assert "podcast_config.host_a.style must be a non-empty string" in errors
    assert "podcast_config.host_b must be an object" in errors


def test_validate_payload_warns_on_unknown_podcast_config_fields() -> None:
    validation = validate_payload_details(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "podcast_config": {
                "name": "SignalWire",
                "extra": "forward-compatible",
                "host_a": {"name": "Ada", "voice": "nova", "style": "Bright", "accent": "mid"},
            },
        }
    )

    assert validation.errors == []
    assert "podcast_config contains unsupported fields: extra" in validation.warnings
    assert "podcast_config.host_a contains unsupported fields: accent" in validation.warnings


def test_podcast_config_style_guide_from_payload() -> None:
    guide_text = "## Segment Structure\nCold Open → Signal → Noise Check → Gap"
    config = PodcastConfig.from_payload({"podcast_config": {"style_guide": guide_text}})
    assert config.style_guide == guide_text


def test_podcast_config_style_guide_defaults_empty() -> None:
    config = PodcastConfig()
    assert config.style_guide == ""


def test_podcast_config_style_guide_strips_whitespace() -> None:
    config = PodcastConfig.from_payload({"podcast_config": {"style_guide": "  guide text  \n"}})
    assert config.style_guide == "guide text"


def test_podcast_config_style_guide_non_string_ignored() -> None:
    config = PodcastConfig.from_payload({"podcast_config": {"style_guide": 42}})
    assert config.style_guide == ""


def test_build_style_guide_prompt_empty_when_no_guide() -> None:
    config = PodcastConfig()
    assert episode.build_style_guide_prompt(config) == ""


def test_build_style_guide_prompt_wraps_guide_text() -> None:
    guide = "Format: Cold Open → Signal → Outro"
    config = PodcastConfig.from_payload({"podcast_config": {"style_guide": guide}})
    prompt = episode.build_style_guide_prompt(config)
    assert "Editorial Style Guide" in prompt
    assert guide in prompt
    assert "End of Style Guide" in prompt


def test_build_episode_script_includes_style_guide_marker() -> None:
    guide = "Phrasing principles: be specific, cite numbers"
    config = PodcastConfig.from_payload({"podcast_config": {"style_guide": guide}})
    article = episode.sanitize_article(
        week="2026-W24",
        title="Test",
        url="https://example.com",
        sha256="abc123",
        summary="Summary text.",
        beats=[{"topic": "topic one", "points": ["point"]}],
    )
    script = episode.build_episode_script(article, podcast_config=config)
    assert "Style-Guide: included" in script
