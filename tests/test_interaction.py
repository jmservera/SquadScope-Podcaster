from __future__ import annotations

import pytest

from podcaster import interaction as I
from podcaster.config import BACKCHANNEL_LIBRARY, BackchannelConfig


def test_assign_turn_ids_uses_speaker_letter_and_index():
    turns = I.assign_turn_ids([("host_a", "hello"), ("host_b", "hi"), ("host_a", "yo")])
    assert [t.turn_id for t in turns] == ["a_000", "b_001", "a_002"]
    assert [t.speaker for t in turns] == ["host_a", "host_b", "host_a"]


def test_assign_turn_ids_normalizes_whitespace_consistently():
    # Trailing whitespace/casing must not make the turn-id letter disagree with
    # the normalized speaker.
    turns = I.assign_turn_ids([("host_b ", "hi"), ("HOST_B", "yo"), ("host_a\t", "ok")])
    assert [t.turn_id for t in turns] == ["b_000", "b_001", "a_002"]
    assert [t.speaker for t in turns] == ["host_b", "host_b", "host_a"]


def test_find_pause_points_returns_clause_boundaries():
    points = I.find_pause_points("We built it, then we shipped it.")
    anchors = [a for _, a in points]
    assert "We built it" in anchors[0]
    assert "shipped it" in anchors[-1]


def test_is_safe_anchor_blocks_numbers_urls_repos_and_tech_terms():
    assert I.is_safe_anchor("we discussed the design")
    assert not I.is_safe_anchor("it took 42 seconds")
    assert not I.is_safe_anchor("see github.com")
    assert not I.is_safe_anchor("the repo is openai/whisper")
    assert not I.is_safe_anchor("call the API")
    assert not I.is_safe_anchor("use snake_case here")
    assert not I.is_safe_anchor("the getUserData call")
    assert not I.is_safe_anchor("run the CLI")
    assert not I.is_safe_anchor("look at `code span`")


def test_build_interaction_map_disabled_returns_empty():
    turns = I.assign_turn_ids([("host_a", "We built it, then we shipped it, and it worked.")])
    m = I.build_interaction_map(turns, [30.0], BackchannelConfig(enabled=False))
    assert len(m) == 0
    assert m.to_dict() == {"interactions": []}


def test_build_interaction_map_speaker_is_the_listener():
    turns = I.assign_turn_ids(
        [("host_a", "We tried graph retrieval, and it worked, and we liked it.")]
    )
    cfg = BackchannelConfig(enabled=True, min_gap_seconds=1, max_gap_seconds=60)
    m = I.build_interaction_map(turns, [40.0], cfg)
    assert len(m) >= 1
    # The backchannel comes from the *other* host (the listener).
    assert m.interactions[0].speaker == "host_b"
    assert m.interactions[0].under_turn_id == "a_000"


def test_build_interaction_map_enforces_density_min_gap():
    # Many safe clause boundaries but a large min_gap -> at most one per window.
    text = ", ".join(["we kept building things"] * 12) + "."
    turns = I.assign_turn_ids([("host_a", text), ("host_b", text)])
    cfg = BackchannelConfig(enabled=True, min_gap_seconds=45, max_gap_seconds=60)
    m = I.build_interaction_map(turns, [60.0, 60.0], cfg)
    # Two turns, at most one backchannel each given the 45s min gap.
    assert len(m) <= 2
    assert len({i.under_turn_id for i in m.interactions}) == len(m.interactions)


def test_build_interaction_map_skips_final_clause_punchline():
    # Only one boundary plus the terminal '.' -> after dropping the last clause,
    # a single safe candidate remains; ensure the terminal punchline is avoided.
    turns = I.assign_turn_ids([("host_a", "We loved the demo, it was the best thing ever!")])
    cfg = BackchannelConfig(enabled=True, min_gap_seconds=1)
    m = I.build_interaction_map(turns, [20.0], cfg)
    # The placed anchor must not be the closing punchline clause.
    for interaction in m.interactions:
        assert "best thing ever" not in interaction.anchor_text


def test_build_interaction_map_clamps_gain_and_uses_library():
    turns = I.assign_turn_ids([("host_a", "We tried it, and it worked, and we shipped it.")])
    cfg = BackchannelConfig(enabled=True, min_gap_seconds=1, gain_db=-30.0)
    m = I.build_interaction_map(turns, [40.0], cfg)
    assert m.interactions[0].gain_db == -18.0  # clamped to [-18, -14]
    assert all(i.text in BACKCHANNEL_LIBRARY for i in m.interactions)


def test_build_interaction_map_validates_parallel_durations():
    turns = I.assign_turn_ids([("host_a", "hello there.")])
    with pytest.raises(ValueError):
        I.build_interaction_map(turns, [1.0, 2.0], BackchannelConfig(enabled=True))


def test_resolve_placements_anchors_to_text_and_skips_missing_clips():
    turns = I.assign_turn_ids([("host_a", "We tried graph retrieval, and it worked well here.")])
    cfg = BackchannelConfig(enabled=True, min_gap_seconds=1)
    m = I.build_interaction_map(turns, [40.0], cfg)
    assert len(m) >= 1
    text = m.interactions[0].text
    placements = I.resolve_placements(m, turns, [40.0], {text: b"clip-bytes"})
    assert len(placements) == 1
    assert placements[0].clip == b"clip-bytes"
    assert 0 < placements[0].start_seconds < 40.0
    # Missing clip -> placement skipped.
    assert I.resolve_placements(m, turns, [40.0], {}) == []


def test_interaction_to_dict_matches_issue_schema():
    interaction = I.Interaction(
        speaker="host_b",
        under_turn_id="a_014",
        anchor_text="graph-based retrieval",
        text="right",
        tone="agreeing",
        gain_db=-14,
        max_duration_ms=600,
    )
    assert interaction.to_dict() == {
        "type": "backchannel",
        "speaker": "host_b",
        "under_turn_id": "a_014",
        "anchor": {"mode": "after_text", "text": "graph-based retrieval"},
        "text": "right",
        "tone": "agreeing",
        "gain_db": -14,
        "max_duration_ms": 600,
    }


def test_resolve_placements_validates_parallel_durations():
    turns = I.assign_turn_ids([("host_a", "hello there.")])
    m = I.build_interaction_map(turns, [40.0], BackchannelConfig(enabled=True, min_gap_seconds=1))
    with pytest.raises(ValueError):
        I.resolve_placements(m, turns, [40.0, 1.0], {})


def test_build_interaction_map_max_gap_widens_spacing():
    # max_gap_seconds must be operative: widening the window (with min_gap fixed)
    # spaces backchannels further apart, so it is not a dead config knob.
    segments = [
        ("host_a" if i % 2 == 0 else "host_b", "we kept building things, and we shipped it.")
        for i in range(12)
    ]
    turns = I.assign_turn_ids(segments)
    durations = [6.0] * len(turns)
    narrow = I.build_interaction_map(
        turns, durations, BackchannelConfig(enabled=True, min_gap_seconds=5, max_gap_seconds=5)
    )
    wide = I.build_interaction_map(
        turns, durations, BackchannelConfig(enabled=True, min_gap_seconds=5, max_gap_seconds=40)
    )
    assert len(narrow) > len(wide)
