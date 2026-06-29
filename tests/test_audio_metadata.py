"""Tests for podcaster.audio_metadata — Layer 2 realized audio metadata (#486)."""

from __future__ import annotations

import pytest

from podcaster.audio_metadata import (
    AUDIO_METADATA_SCHEMA_VERSION,
    RealizedAudioMetadata,
    RealizedAudioMetadataError,
    TopicRange,
    UtteranceTiming,
    WordTiming,
    distribute_word_timings,
    extract_realized_audio_metadata,
)
from podcaster.script_plan import ScriptPlan, ScriptPlanSegment, VisualMode, parse_script_plan


def _seg(index, speaker, text, mode, repo_url=None, section_id=None):
    return ScriptPlanSegment(
        index=index,
        speaker=speaker,
        text=text,
        visual_mode=mode,
        repo_url=repo_url,
        section_id=section_id,
    )


def _plan():
    """A small two-host plan: article cold open, repo-a, repo-b, intermission."""
    return ScriptPlan(
        segments=(
            _seg(0, "Theo", "Welcome to the show", VisualMode.ARTICLE),
            _seg(
                1,
                "Vera",
                "First repo here",
                VisualMode.REPO,
                "https://github.com/owner/repo-a",
                "section-1",
            ),
            _seg(
                2,
                "Theo",
                "Totally agree friend",
                VisualMode.REPO,
                "https://github.com/owner/repo-a",
                "section-1",
            ),
            _seg(
                3,
                "Vera",
                "Now the second one",
                VisualMode.REPO,
                "https://github.com/owner/repo-b",
                "section-1",
            ),
            _seg(4, "Theo", "Lets take a breather", VisualMode.INTERMISSION, None, "section-2"),
        )
    )


# --- word timing distribution ---


def test_distribute_word_timings_is_contiguous_and_exact():
    words = distribute_word_timings("alpha beta gamma", 1000, 4000)
    assert [w.text for w in words] == ["alpha", "beta", "gamma"]
    assert words[0].start_ms == 1000
    assert words[-1].end_ms == 4000
    # contiguous, non-overlapping, monotonic
    for prev, nxt in zip(words, words[1:]):
        assert prev.end_ms == nxt.start_ms
        assert nxt.end_ms >= nxt.start_ms


def test_distribute_word_timings_proportional_to_length():
    # "aaaa" (4) vs "bb" (2) → first word gets ~2x the second's span.
    words = distribute_word_timings("aaaa bb", 0, 600)
    assert words[0].duration_ms == 400
    assert words[1].duration_ms == 200


def test_distribute_word_timings_empty_or_zero_span():
    assert distribute_word_timings("", 0, 1000) == ()
    assert distribute_word_timings("   ", 0, 1000) == ()
    assert distribute_word_timings("word", 1000, 1000) == ()
    assert distribute_word_timings("word", 1000, 500) == ()


def test_distribute_word_timings_is_deterministic():
    a = distribute_word_timings("one two three four", 100, 2500)
    b = distribute_word_timings("one two three four", 100, 2500)
    assert a == b


# --- utterance timings ---


def test_utterance_timings_use_durations_and_gap():
    plan = _plan()
    durations = [2.0, 3.0, 1.0, 2.0, 1.5]
    meta = extract_realized_audio_metadata(plan, durations, gap_seconds=0.5)

    assert len(meta.utterances) == 5
    u0, u1, u2 = meta.utterances[0], meta.utterances[1], meta.utterances[2]
    # u0: 0..2000
    assert (u0.start_ms, u0.end_ms) == (0, 2000)
    # u1 starts after u0 + gap (2000 + 500 = 2500), lasts 3000
    assert (u1.start_ms, u1.end_ms) == (2500, 5500)
    # u2 starts after u1 + gap (5500 + 500 = 6000)
    assert u2.start_ms == 6000
    # total includes 4 gaps of 0.5s + 9.5s speech = 11.5s
    assert meta.total_duration_ms == 11500
    assert meta.gap_ms == 500


def test_repo_topic_start_equals_segment_start_plus_gap_and_offset():
    """#553: with gap=0.35 and speech_offset=10.0 the first repo topic starts at
    segment_start + gap + offset — the deterministic timing the video pipeline
    consumes instead of whisper forced alignment.
    """
    plan = _plan()
    # Segment 0 (article cold open) is 8s; the first repo turn is segment 1.
    durations = [8.0, 3.0, 1.0, 2.0, 1.5]
    meta = extract_realized_audio_metadata(
        plan,
        durations,
        gap_seconds=0.35,
        speech_offset_seconds=10.0,
    )
    repo_topic = meta.repo_topics[0]
    # First repo turn starts after the 8s article segment + one inter-segment
    # gap (0.35) + the 10s intro-music speech offset.
    expected_start_ms = int(round((8.0 + 0.35 + 10.0) * 1000))
    assert repo_topic.start_ms == expected_start_ms


def test_speech_offset_shifts_every_timestamp():
    plan = _plan()
    durations = [2.0, 3.0, 1.0, 2.0, 1.5]
    meta = extract_realized_audio_metadata(
        plan, durations, gap_seconds=0.5, speech_offset_seconds=4.0
    )
    assert meta.speech_offset_ms == 4000
    assert meta.utterances[0].start_ms == 4000
    assert meta.utterances[0].words[0].start_ms == 4000


def test_speaker_ids_resolved_by_host_labels():
    plan = _plan()
    durations = [1.0] * 5
    meta = extract_realized_audio_metadata(plan, durations, host_labels=("Theo", "Vera"))
    ids = {u.speaker: u.speaker_id for u in meta.utterances}
    assert ids == {"Theo": "host_a", "Vera": "host_b"}


def test_speaker_ids_default_first_appearance():
    plan = _plan()  # Theo speaks first
    durations = [1.0] * 5
    meta = extract_realized_audio_metadata(plan, durations)
    ids = {u.speaker: u.speaker_id for u in meta.utterances}
    assert ids == {"Theo": "host_a", "Vera": "host_b"}


def test_words_cover_each_utterance_span():
    plan = _plan()
    meta = extract_realized_audio_metadata(plan, [2.0, 3.0, 1.0, 2.0, 1.5])
    for u in meta.utterances:
        if u.text.split():
            assert u.words[0].start_ms == u.start_ms
            assert u.words[-1].end_ms == u.end_ms


# --- topic ranges align to visual markers ---


def test_topics_group_by_visual_context():
    plan = _plan()
    durations = [2.0, 3.0, 1.0, 2.0, 1.5]
    meta = extract_realized_audio_metadata(plan, durations, gap_seconds=0.5)

    # article (u0) | repo-a (u1,u2) | repo-b (u3) | intermission (u4)
    modes = [(t.visual_mode, t.repo_url) for t in meta.topics]
    assert modes == [
        (VisualMode.ARTICLE, None),
        (VisualMode.REPO, "https://github.com/owner/repo-a"),
        (VisualMode.REPO, "https://github.com/owner/repo-b"),
        (VisualMode.INTERMISSION, None),
    ]
    repo_a = meta.topics[1]
    assert repo_a.utterance_indices == (1, 2)
    # topic span = first utterance start .. last utterance end
    assert repo_a.start_ms == meta.utterances[1].start_ms
    assert repo_a.end_ms == meta.utterances[2].end_ms
    assert repo_a.section_id == "section-1"


def test_repo_topics_helper():
    plan = _plan()
    meta = extract_realized_audio_metadata(plan, [1.0] * 5)
    repos = [t.repo_url for t in meta.repo_topics]
    assert repos == [
        "https://github.com/owner/repo-a",
        "https://github.com/owner/repo-b",
    ]


def test_consecutive_same_repo_stays_one_topic():
    plan = ScriptPlan(
        segments=(
            _seg(0, "Theo", "one", VisualMode.REPO, "https://github.com/o/r"),
            _seg(1, "Vera", "two", VisualMode.REPO, "https://github.com/o/r"),
            _seg(2, "Theo", "three", VisualMode.REPO, "https://github.com/o/r"),
        )
    )
    meta = extract_realized_audio_metadata(plan, [1.0, 1.0, 1.0])
    assert len(meta.topics) == 1
    assert meta.topics[0].utterance_indices == (0, 1, 2)


def test_backfilled_repo_topics_follow_cumulative_clip_offsets():
    """#579: every later named repo gets a topic at its realized audio cue."""
    script = (
        "Title: Weekly\n"
        "Repos featured: https://github.com/vercel/eve "
        "https://github.com/openai/gym https://github.com/astral-sh/ruff\n"
        "---\n"
        "Theo: Cold open before the repo run.\n"
        "## Visual: repo https://github.com/vercel/eve\n"
        "Vera: vercel/eve is first on the timeline.\n"
        "Theo: openai/gym follows in the very next turn.\n"
        "Vera: astral-sh/ruff is the third repo we name.\n"
    )
    plan = parse_script_plan(script)
    durations = [4.0, 5.0, 6.0, 7.0]

    meta = extract_realized_audio_metadata(
        plan,
        durations,
        gap_seconds=0.25,
        speech_offset_seconds=1.0,
        host_labels=("Theo", "Vera"),
    )

    assert [topic.repo_url for topic in meta.repo_topics] == [
        "https://github.com/vercel/eve",
        "https://github.com/openai/gym",
        "https://github.com/astral-sh/ruff",
    ]
    assert [topic.start_ms for topic in meta.repo_topics] == [
        5250,  # offset + cold open + one gap
        10500,  # previous cue + eve duration + one gap
        16750,  # previous cue + gym duration + one gap
    ]


# --- validation ---


def test_mismatched_durations_raise():
    plan = _plan()
    with pytest.raises(RealizedAudioMetadataError):
        extract_realized_audio_metadata(plan, [1.0, 2.0])


def test_negative_duration_raises():
    plan = _plan()
    with pytest.raises(RealizedAudioMetadataError):
        extract_realized_audio_metadata(plan, [1.0, -1.0, 1.0, 1.0, 1.0])


def test_negative_offset_raises():
    plan = _plan()
    with pytest.raises(RealizedAudioMetadataError):
        extract_realized_audio_metadata(plan, [1.0] * 5, speech_offset_seconds=-1.0)


def test_negative_gap_raises():
    plan = _plan()
    with pytest.raises(RealizedAudioMetadataError):
        extract_realized_audio_metadata(plan, [1.0] * 5, gap_seconds=-0.5)


def test_empty_plan_yields_empty_metadata():
    meta = extract_realized_audio_metadata(ScriptPlan(), [])
    assert meta.utterances == ()
    assert meta.topics == ()
    assert meta.total_duration_ms == 0


# --- serialization round-trip ---


def test_round_trip_serialization():
    plan = _plan()
    meta = extract_realized_audio_metadata(
        plan, [2.0, 3.0, 1.0, 2.0, 1.5], gap_seconds=0.5, host_labels=("Theo", "Vera")
    )
    restored = RealizedAudioMetadata.from_dict(meta.to_dict())
    assert restored == meta
    assert restored.schema_version == AUDIO_METADATA_SCHEMA_VERSION


def test_to_dict_shape():
    plan = _plan()
    data = extract_realized_audio_metadata(plan, [1.0] * 5).to_dict()
    assert data["schema_version"] == AUDIO_METADATA_SCHEMA_VERSION
    assert {
        "gap_ms",
        "speech_offset_ms",
        "total_duration_ms",
        "utterances",
        "topics",
    } <= data.keys()
    first = data["utterances"][0]
    assert {
        "index",
        "speaker",
        "speaker_id",
        "start_ms",
        "end_ms",
        "visual_mode",
        "words",
    } <= first.keys()


def test_dataclass_round_trip_units():
    word = WordTiming("hi", 0, 100)
    assert WordTiming.from_dict(word.to_dict()) == word
    utt = UtteranceTiming(
        index=0,
        speaker="Theo",
        speaker_id="host_a",
        text="hi there",
        start_ms=0,
        end_ms=500,
        visual_mode=VisualMode.ARTICLE,
        words=(WordTiming("hi", 0, 250), WordTiming("there", 250, 500)),
    )
    assert UtteranceTiming.from_dict(utt.to_dict()) == utt
    topic = TopicRange(
        visual_mode=VisualMode.REPO,
        start_ms=0,
        end_ms=500,
        utterance_indices=(0,),
        repo_url="https://github.com/o/r",
        section_id="s1",
    )
    assert TopicRange.from_dict(topic.to_dict()) == topic
