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


def test_default_density_increases_backchannels_and_includes_hums():
    """#560: denser defaults land more reactions and surface hums ('hmm')."""
    # A realistic multi-turn stretch with several sentence ends per turn so the
    # turn-final pause (issue #573) gives every turn a safe placement slot.
    clause = "we kept building it. and it worked. and we liked the result."
    turns = I.assign_turn_ids([("host_a" if i % 2 == 0 else "host_b", clause) for i in range(8)])
    durations = [30.0] * len(turns)

    dense = I.build_interaction_map(turns, durations, BackchannelConfig(enabled=True))
    sparse = I.build_interaction_map(
        turns, durations, BackchannelConfig(enabled=True, min_gap_seconds=45, max_gap_seconds=60)
    )

    # Tighter default gaps must yield a measurable density increase (#560).
    assert len(dense) > len(sparse)
    # Reactions come from the listening host and are clearly audible (>= -12 dB
    # default, well above the old -16 dB) but still a background voice.
    assert all(i.gain_db >= -12.0 for i in dense.interactions)
    # Hums must actually surface across the episode (tone cycle reaches 'thinking').
    assert any(i.text == "hmm" for i in dense.interactions)
    # A reaction anchors on the speaking host's closing clause and lands in the
    # pause *after* it (issue #573) — reinforcing the point, never talking over it.
    turns = I.assign_turn_ids(
        [
            ("host_a", "We loved the demo, it was the best thing ever!"),
            ("host_b", "Totally agree."),
        ]
    )
    cfg = BackchannelConfig(enabled=True, min_gap_seconds=1)
    m = I.build_interaction_map(turns, [20.0, 10.0], cfg)
    closing = next((i for i in m.interactions if i.under_turn_id == "a_000"), None)
    assert closing is not None
    # The anchor is the turn-final clause; there is no spoken word after it, so
    # the reaction cannot overlap resumed speech from host_a.
    assert "best thing ever" in closing.anchor_text


def test_build_interaction_map_clamps_gain_and_uses_library():
    turns = I.assign_turn_ids([("host_a", "We tried it, and it worked, and we shipped it.")])
    cfg = BackchannelConfig(enabled=True, min_gap_seconds=1, gain_db=-30.0)
    m = I.build_interaction_map(turns, [40.0], cfg)
    assert m.interactions[0].gain_db == -18.0  # clamped to [-18, -10] floor
    assert all(i.text in BACKCHANNEL_LIBRARY for i in m.interactions)


def test_build_interaction_map_validates_parallel_durations():
    turns = I.assign_turn_ids([("host_a", "hello there.")])
    with pytest.raises(ValueError):
        I.build_interaction_map(turns, [1.0, 2.0], BackchannelConfig(enabled=True))


def test_resolve_placements_anchors_to_text_and_skips_missing_clips():
    turns = I.assign_turn_ids(
        [
            ("host_a", "We tried graph retrieval, and it worked well here."),
            ("host_b", "Nice, that lines up."),
        ]
    )
    durations = [40.0, 20.0]
    cfg = BackchannelConfig(enabled=True, min_gap_seconds=1)
    m = I.build_interaction_map(turns, durations, cfg)
    assert len(m) >= 1
    first = next(i for i in m.interactions if i.under_turn_id == "a_000")
    placements = I.resolve_placements(m, turns, durations, {first.text: b"clip-bytes"})
    assert len(placements) == 1
    assert placements[0].clip == b"clip-bytes"
    # The reaction lands at host_a's turn-final pause (~40s, the end of its 40s
    # turn), not at a mid-clause comma — so it never overlaps resumed speech
    # (issue #573) and still plays during the inter-turn gap into host_b's line.
    assert 38.0 < placements[0].start_seconds <= 40.0
    # Missing clip -> placement skipped.
    assert I.resolve_placements(m, turns, durations, {}) == []


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


def test_backchannel_not_placed_over_resumed_speech_issue_573():
    """#573: a reaction must never land at a mid-clause comma with no room.

    The speaking host's line is full of mid-sentence commas; synthesized speech
    is continuous, so the host resumes immediately after each comma. The only
    place a reaction fits without overlapping resumed speech is the turn-final
    pause. Prove no placement sits at an interior boundary.
    """

    speaking = "We tried it, then we shipped it, and then it broke, but we fixed it."
    turns = I.assign_turn_ids([("host_a", speaking), ("host_b", "Right, that tracks.")])
    durations = [40.0, 20.0]
    cfg = BackchannelConfig(enabled=True, min_gap_seconds=1)

    m = I.build_interaction_map(turns, durations, cfg)
    a_turn = next((i for i in m.interactions if i.under_turn_id == "a_000"), None)
    assert a_turn is not None, "expected a reaction on the host_a turn"

    clips = {i.text: b"clip" for i in m.interactions}
    placements = I.resolve_placements(m, turns, durations, clips)
    a_placement = next(p for p in placements if p.interaction.under_turn_id == "a_000")

    # The latest interior (mid-clause) comma sits before "but we fixed it.".
    last_comma = speaking.rfind(",")
    interior_time = 40.0 * (last_comma / len(speaking))
    # The reaction must start *after* every interior boundary — i.e. only at the
    # turn-final pause — so it cannot bleed over host_a's resumed words.
    assert a_placement.start_seconds > interior_time
    # And it must not start before host_a stops speaking (~end of the 40s turn).
    assert a_placement.start_seconds >= 39.0


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
