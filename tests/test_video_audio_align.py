"""Tests for podcaster.video.audio_align (audio-cue video sync, #374).

These tests exercise the pure alignment logic with synthetic word-level
transcripts — no faster-whisper model is loaded, so they are fast and
hermetic. The transcription wrapper's graceful-degradation paths are covered
by feeding a non-existent / missing-dependency scenario.
"""

from __future__ import annotations

import pytest

from podcaster.video.audio_align import (
    TranscriptionUnavailable,
    WordTimestamp,
    align_token_times,
    build_transcript_tokens,
    map_repo_times,
    normalize_tokens,
    repo_audio_timestamps,
    transcribe_words,
    _first_mention_index,
)
from podcaster.video.sync_plan import RepoReference


# --- normalize_tokens ---


def test_normalize_tokens_lowercases_and_splits():
    assert normalize_tokens("Hello, World!") == ["hello", "world"]


def test_normalize_tokens_splits_urls_and_punctuation():
    assert normalize_tokens("https://github.com/JimLiu/baoyu-design") == [
        "https",
        "github",
        "com",
        "jimliu",
        "baoyu",
        "design",
    ]


def test_normalize_tokens_empty():
    assert normalize_tokens("  --- ?? ") == []


# --- build_transcript_tokens ---


def test_build_transcript_tokens_expands_multiword_and_carries_time():
    words = [
        WordTimestamp("Hello", 1.0),
        WordTimestamp("GitHub.com", 2.0),
    ]
    tokens, starts = build_transcript_tokens(words)
    assert tokens == ["hello", "github", "com"]
    # Both sub-tokens of "GitHub.com" inherit the source word's start time.
    assert starts == [1.0, 2.0, 2.0]


def test_build_transcript_tokens_skips_empty_words():
    tokens, starts = build_transcript_tokens([WordTimestamp("...", 5.0)])
    assert tokens == []
    assert starts == []


# --- align_token_times ---


def test_align_token_times_identical_streams():
    script = ["the", "skylight", "project", "is", "great"]
    trans = list(script)
    starts = [0.0, 1.0, 2.0, 3.0, 4.0]
    times, ratio = align_token_times(script, trans, starts)
    assert ratio == 1.0
    assert times == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_align_token_times_handles_mangled_word():
    # Transcript mis-hears "baoyu" as "balu" but surrounding context aligns.
    script = ["the", "baoyu", "design", "package", "rocks"]
    trans = ["the", "balu", "design", "package", "rocks"]
    starts = [0.0, 1.0, 2.0, 3.0, 4.0]
    times, ratio = align_token_times(script, trans, starts)
    # "baoyu" (unmatched) carries the timestamp of the next matched token.
    assert times[1] == 2.0  # "design" time
    assert times[0] == 0.0
    assert times[2] == 2.0
    assert ratio < 1.0


def test_align_token_times_unmatched_tail_is_none():
    script = ["a", "b", "c", "zzz"]
    trans = ["a", "b", "c"]
    starts = [0.0, 1.0, 2.0]
    times, _ = align_token_times(script, trans, starts)
    assert times[:3] == [0.0, 1.0, 2.0]
    assert times[3] is None


def test_align_token_times_empty_inputs():
    times, ratio = align_token_times([], ["a"], [0.0])
    assert times == []
    assert ratio == 0.0


def test_align_token_times_length_mismatch_raises():
    with pytest.raises(ValueError):
        align_token_times(["a"], ["a", "b"], [0.0])


# --- _first_mention_index ---


def test_first_mention_prefers_earliest_display_text_over_url():
    repo = RepoReference("JimLiu", "baoyu-design")
    # Display text "baoyu design" at idx 1, URL "jimliu baoyu design" at idx 5.
    tokens = [
        "the", "baoyu", "design", "package", "at",
        "jimliu", "baoyu", "design",
    ]
    assert _first_mention_index(tokens, repo) == 1


def test_first_mention_via_url_when_no_display_text():
    repo = RepoReference("cpaczek", "skylight")
    tokens = ["projects", "like", "cpaczek", "skylight", "are", "cool"]
    # Owner+name ("cpaczek skylight") at idx 2 is earlier than the bare name at
    # idx 3, so the owner-qualified URL position wins.
    assert _first_mention_index(tokens, repo) == 2


def test_first_mention_skips_short_generic_name():
    repo = RepoReference("acme", "cli")
    # "cli" is a short lone token; the bare-name match is suppressed, so only
    # the owner+name sequence ("acme cli") locates it.
    tokens = ["the", "cli", "is", "nice", "see", "acme", "cli", "now"]
    assert _first_mention_index(tokens, repo) == 5


def test_first_mention_not_found():
    repo = RepoReference("owner", "missingrepo")
    assert _first_mention_index(["nothing", "here"], repo) is None


# --- map_repo_times ---


SCRIPT = """\
Title: Test
Source: https://example.com
---
Theo: Check out https://github.com/JimLiu/baoyu-design for mockups.
Vera: And https://github.com/cpaczek/skylight is amazing hardware.
"""


def _words_from_text(text: str, *, step: float = 1.0, start: float = 0.0):
    """Build a synthetic word transcript, one token per word, evenly spaced."""
    words = []
    t = start
    for tok in text.split():
        words.append(WordTimestamp(tok, t))
        t += step
    return words


def test_map_repo_times_aligns_both_repos():
    repos = [
        RepoReference("JimLiu", "baoyu-design"),
        RepoReference("cpaczek", "skylight"),
    ]
    # Spoken transcript: repo names appear (mangled) with timestamps.
    transcript = (
        "check out the baoyu design package for mockups "
        "and the skylight project is amazing hardware"
    )
    words = _words_from_text(transcript, step=2.0)
    times = map_repo_times(SCRIPT, repos, words)
    assert times is not None
    assert set(times.keys()) == set(repos)
    # baoyu-design discussed before skylight.
    assert times[repos[0]] < times[repos[1]]


def test_map_repo_times_low_ratio_returns_none():
    repos = [RepoReference("JimLiu", "baoyu-design")]
    words = _words_from_text("completely unrelated random words here now")
    assert map_repo_times(SCRIPT, repos, words, min_ratio=0.9) is None


def test_map_repo_times_empty_inputs_return_none():
    repos = [RepoReference("a", "b")]
    assert map_repo_times(SCRIPT, repos, []) is None
    assert map_repo_times(SCRIPT, [], _words_from_text("x y z")) is None


# --- transcribe_words / repo_audio_timestamps graceful degradation ---


def test_transcribe_words_missing_file_raises_unavailable():
    # faster-whisper may or may not be installed; either way a bogus path must
    # surface as TranscriptionUnavailable, never a raw exception.
    with pytest.raises(TranscriptionUnavailable):
        transcribe_words("/nonexistent/path/to/audio.mp3")


def test_repo_audio_timestamps_returns_none_on_failure(monkeypatch):
    def _boom(*_a, **_k):
        raise TranscriptionUnavailable("no whisper")

    monkeypatch.setattr(
        "podcaster.video.audio_align.transcribe_words", _boom
    )
    repos = [RepoReference("JimLiu", "baoyu-design")]
    assert repo_audio_timestamps(SCRIPT, repos, "audio.mp3") is None


def test_repo_audio_timestamps_no_repos_returns_none():
    assert repo_audio_timestamps(SCRIPT, [], "audio.mp3") is None


def test_repo_audio_timestamps_happy_path(monkeypatch):
    repos = [RepoReference("cpaczek", "skylight")]
    # Transcript closely mirrors the script body so the alignment ratio clears
    # the trust threshold.
    transcript = (
        "Theo check out https github com JimLiu baoyu design for mockups "
        "Vera and https github com cpaczek skylight is amazing hardware"
    )
    monkeypatch.setattr(
        "podcaster.video.audio_align.transcribe_words",
        lambda *_a, **_k: _words_from_text(transcript, step=1.5),
    )
    times = repo_audio_timestamps(SCRIPT, repos, "audio.mp3")
    assert times is not None
    assert repos[0] in times
