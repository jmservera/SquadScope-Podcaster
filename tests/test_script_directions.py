"""Tests for ScriptDirections and MusicMixConfig parsing (#163)."""

from __future__ import annotations

from podcaster.config import (
    EpisodeStyle,
    MusicMixConfig,
    ScriptDirections,
    _parse_time_offset,
)


class TestScriptDirections:
    def test_empty_payload_returns_defaults(self) -> None:
        sd = ScriptDirections.from_payload(None)
        assert not sd.has_content
        assert sd.episode_style.format == ""
        assert sd.cold_open == ""

    def test_missing_script_directions_key(self) -> None:
        sd = ScriptDirections.from_payload({"week": "2026-W24"})
        assert not sd.has_content

    def test_full_payload_parses(self) -> None:
        payload = {
            "script_directions": {
                "opening_cues": {
                    "cold_open": "Did you know 40% of repos have zero tests?",
                    "ai_disclosure": "Voices are AI-generated.",
                },
                "closing_cues": {
                    "corrections_path": "/corrections",
                    "source_article_link": "https://example.com/article",
                },
                "episode_style": {
                    "format": "Two-host conversational podcast, 8-10 minutes.",
                    "tone": "Conversational, not performative.",
                    "segment_order": ["Cold Open", "The Signal", "Outro"],
                },
            }
        }
        sd = ScriptDirections.from_payload(payload)
        assert sd.has_content
        assert sd.cold_open == "Did you know 40% of repos have zero tests?"
        assert sd.ai_disclosure_cue == "Voices are AI-generated."
        assert sd.corrections_path == "/corrections"
        assert sd.source_article_link == "https://example.com/article"
        assert sd.episode_style.format == "Two-host conversational podcast, 8-10 minutes."
        assert sd.episode_style.tone == "Conversational, not performative."
        assert sd.episode_style.segment_order == ("Cold Open", "The Signal", "Outro")

    def test_partial_payload_still_works(self) -> None:
        payload = {
            "script_directions": {
                "episode_style": {"tone": "Upbeat and curious"},
            }
        }
        sd = ScriptDirections.from_payload(payload)
        assert sd.has_content
        assert sd.episode_style.tone == "Upbeat and curious"
        assert sd.cold_open == ""


class TestMusicMixConfig:
    def test_empty_payload_returns_defaults(self) -> None:
        mm = MusicMixConfig.from_payload(None)
        assert not mm.has_track
        assert mm.intro_full_volume_seconds == 10.0

    def test_top_level_music_mix(self) -> None:
        payload = {
            "music_mix": {
                "track": "Summer Sport",
                "intro": {"full_volume_seconds": 8, "fade_down_under": "Host A opening"},
                "outro": {"start_position": "1:15", "fade_up_during": "farewell", "play_to_end": True},
            }
        }
        mm = MusicMixConfig.from_payload(payload)
        assert mm.has_track
        assert mm.track == "Summer Sport"
        assert mm.intro_full_volume_seconds == 8.0
        assert mm.intro_fade_down_under == "Host A opening"
        assert mm.outro_start_position == "1:15"
        assert mm.outro_fade_up_during == "farewell"
        assert mm.outro_play_to_end is True

    def test_nested_under_script_directions(self) -> None:
        payload = {
            "script_directions": {
                "music_mix": {
                    "track": "Chill Beat",
                    "intro": {"full_volume_seconds": 5},
                    "outro": {"start_position": "2:00"},
                }
            }
        }
        mm = MusicMixConfig.from_payload(payload)
        assert mm.has_track
        assert mm.track == "Chill Beat"
        assert mm.intro_full_volume_seconds == 5.0

    def test_to_mix_spec_kwargs_default(self) -> None:
        mm = MusicMixConfig(track="X", intro_full_volume_seconds=10.0)
        kwargs = mm.to_mix_spec_kwargs()
        assert kwargs == {}

    def test_to_mix_spec_kwargs_custom(self) -> None:
        mm = MusicMixConfig(
            track="X",
            intro_full_volume_seconds=8.0,
            outro_start_position="1:30",
        )
        kwargs = mm.to_mix_spec_kwargs()
        assert kwargs["intro_full_volume_seconds"] == 8.0
        assert kwargs["outro_start_offset_seconds"] == 90.0


class TestParseTimeOffset:
    def test_minutes_seconds(self) -> None:
        assert _parse_time_offset("1:15") == 75.0

    def test_plain_seconds(self) -> None:
        assert _parse_time_offset("90") == 90.0

    def test_invalid_returns_default(self) -> None:
        assert _parse_time_offset("abc") == 75.0
