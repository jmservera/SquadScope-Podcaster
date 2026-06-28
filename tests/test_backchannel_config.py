from __future__ import annotations

import pytest

from podcaster.config import BACKCHANNEL_LIBRARY, BackchannelConfig


def test_defaults_are_disabled_and_conservative():
    cfg = BackchannelConfig()
    assert cfg.enabled is False
    assert cfg.min_gap_seconds == 45.0
    assert cfg.max_gap_seconds == 60.0
    assert cfg.gain_db == -16.0
    assert cfg.max_duration_ms == 600
    assert cfg.library == BACKCHANNEL_LIBRARY


def test_from_payload_absent_returns_defaults():
    assert BackchannelConfig.from_payload(None) == BackchannelConfig()
    assert BackchannelConfig.from_payload({}) == BackchannelConfig()


def test_from_payload_top_level():
    cfg = BackchannelConfig.from_payload(
        {
            "backchannels": {
                "enabled": True,
                "min_gap_seconds": 30,
                "max_gap_seconds": 50,
                "gain_db": -15,
                "max_duration_ms": 500,
                "library": ["right", "yeah"],
            }
        }
    )
    assert cfg.enabled is True
    assert cfg.min_gap_seconds == 30
    assert cfg.max_gap_seconds == 50
    assert cfg.gain_db == -15
    assert cfg.max_duration_ms == 500
    assert cfg.library == ("right", "yeah")


def test_from_payload_nested_under_script_directions():
    cfg = BackchannelConfig.from_payload({"script_directions": {"backchannels": {"enabled": True}}})
    assert cfg.enabled is True


def test_library_includes_agreement_tokens():
    """Operator intent (#555): the listening host's agreement sounds must be available."""
    for token in ("hmm", "mhm", "yes", "yeah"):
        assert token in BACKCHANNEL_LIBRARY


def test_production_request_shape_enables_backchannels():
    """Mirror the production request built from SquadScope config/podcast.json.

    The handoff passes the show config's ``script_directions`` straight through to
    the manifest ``request``; enabling backchannels there must turn the feature on
    with sensible defaults for every production render (#555).
    """
    production_request = {
        "week": "2026-W26",
        "script_directions": {
            "episode_style": {"format": "Two-host conversational podcast"},
            "backchannels": {"enabled": True},
        },
    }
    cfg = BackchannelConfig.from_payload(production_request)
    assert cfg.enabled is True
    # Defaults stay conservative so production audio is not over-saturated.
    assert cfg.min_gap_seconds == 45.0
    assert cfg.max_gap_seconds == 60.0
    assert cfg.library == BACKCHANNEL_LIBRARY


def test_from_payload_ignores_empty_library_override():
    cfg = BackchannelConfig.from_payload({"backchannels": {"enabled": True, "library": []}})
    assert cfg.library == BACKCHANNEL_LIBRARY


def test_clamped_gain_db_window():
    assert BackchannelConfig(gain_db=-10).clamped_gain_db == -14.0
    assert BackchannelConfig(gain_db=-30).clamped_gain_db == -18.0
    assert BackchannelConfig(gain_db=-16).clamped_gain_db == -16.0


def test_invalid_configs_raise():
    with pytest.raises(ValueError):
        BackchannelConfig(min_gap_seconds=-1)
    with pytest.raises(ValueError):
        BackchannelConfig(min_gap_seconds=60, max_gap_seconds=30)
    with pytest.raises(ValueError):
        BackchannelConfig(max_duration_ms=0)
    with pytest.raises(ValueError):
        BackchannelConfig(library=())
