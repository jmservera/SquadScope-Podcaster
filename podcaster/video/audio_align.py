"""Forced alignment of podcast scripts to TTS audio (issue #374).

Video segments must show each repository while the hosts are actually
discussing it.  Proportional / character-position timing (see
:func:`podcaster.video.sync_plan.plan_from_script_timed`) only approximates
this because speech rate is not uniform across the script.

This module bridges the gap by aligning the *known* script text to the
*synthesised* audio:

1. Transcribe the audio with word-level timestamps (faster-whisper).
2. Tokenise both the script and the transcript and align the two word
   streams with :class:`difflib.SequenceMatcher`.
3. Map each repository's first-mention position in the script to the real
   audio timestamp at that point in the transcript.

The alignment is deliberately **context based** rather than matching the repo
name directly: TTS often renders unusual repo names oddly (e.g. ``baoyu`` is
heard as "BALU"), but the surrounding common words still align, so the
timestamp lands on the right moment.

Everything here degrades gracefully.  If faster-whisper is not installed, the
model cannot be loaded, or transcription fails, the public entry points return
``None`` so callers fall back to proportional timing.  The pure alignment logic
(:func:`align_token_times`) has no heavy dependencies and is fully unit-tested.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Sequence

from podcaster.video.sync_plan import RepoReference

logger = logging.getLogger(__name__)

# Default faster-whisper model. "tiny" is fast (~4 s load on CPU) and accurate
# enough for context alignment; the repo names themselves don't need to be
# transcribed correctly, only the surrounding speech.
DEFAULT_MODEL_SIZE = "tiny"

# Minimum SequenceMatcher ratio between script and transcript token streams for
# the alignment to be trusted. Below this the audio probably doesn't match the
# script (wrong file, heavy corruption) and we fall back to proportional timing.
MIN_ALIGN_RATIO = 0.5

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class WordTimestamp:
    """A single transcribed word and its start time in the audio (seconds)."""

    text: str
    start_seconds: float


class TranscriptionUnavailable(RuntimeError):
    """Raised when word-level transcription cannot be produced.

    Callers treat this as a signal to fall back to proportional timing.
    """


def normalize_tokens(text: str) -> list[str]:
    """Lower-case *text* and split into alphanumeric word tokens.

    Punctuation, URLs and markdown decoration collapse to whitespace so the
    script and transcript tokenise consistently.
    """
    return _TOKEN_RE.findall(text.lower())


def _script_body(script: str) -> str:
    """Return the dialogue body of *script*, dropping the metadata header.

    Scripts begin with a ``Key: value`` header terminated by a ``---`` line.
    The header contains the repo URLs (and other text) that would skew token
    positions, so alignment uses the body only.
    """
    if "---" in script:
        return script.split("---", 1)[1]
    return script


def build_transcript_tokens(
    words: Sequence[WordTimestamp],
) -> tuple[list[str], list[float]]:
    """Flatten word timestamps into parallel token / start-time lists.

    A single :class:`WordTimestamp` may normalise to zero or several tokens
    (e.g. ``"GitHub.com/slash"`` → ``["github", "com", "slash"]``).  Every
    emitted token inherits its source word's start time.
    """
    tokens: list[str] = []
    starts: list[float] = []
    for word in words:
        for tok in normalize_tokens(word.text):
            tokens.append(tok)
            starts.append(word.start_seconds)
    return tokens, starts


def align_token_times(
    script_tokens: Sequence[str],
    transcript_tokens: Sequence[str],
    transcript_starts: Sequence[float],
) -> tuple[list[float | None], float]:
    """Map each script token index to an audio start time via stream alignment.

    Uses :class:`difflib.SequenceMatcher` to find matching blocks between the
    script and transcript token streams.  Script tokens inside a matching block
    take the timestamp of their aligned transcript token; script tokens in a
    gap take the timestamp of the next matched transcript token (so a mention is
    never placed *before* the audio reaches it).

    Args:
        script_tokens: Normalised script word tokens (body only).
        transcript_tokens: Normalised transcript word tokens.
        transcript_starts: Start time (seconds) for each transcript token;
            must be the same length as *transcript_tokens*.

    Returns:
        ``(times, ratio)`` where ``times[i]`` is the audio start time for
        ``script_tokens[i]`` (or ``None`` if it falls after the last match),
        and ``ratio`` is the SequenceMatcher similarity (0.0–1.0).

    Raises:
        ValueError: If *transcript_tokens* and *transcript_starts* differ in
            length.
    """
    if len(transcript_tokens) != len(transcript_starts):
        raise ValueError("transcript_tokens and transcript_starts must be the same length")

    n = len(script_tokens)
    times: list[float | None] = [None] * n
    if n == 0 or not transcript_tokens:
        return times, 0.0

    matcher = difflib.SequenceMatcher(
        a=list(script_tokens), b=list(transcript_tokens), autojunk=False
    )
    blocks = matcher.get_matching_blocks()

    # Fill timestamps inside matching blocks directly.
    for a, b, size in blocks:
        for k in range(size):
            times[a + k] = transcript_starts[b + k]

    # For unmatched script tokens, carry the timestamp of the *next* matched
    # transcript token forward so each mention is anchored to where the audio
    # first reaches its surrounding context.
    next_time: float | None = None
    for i in range(n - 1, -1, -1):
        if times[i] is not None:
            next_time = times[i]
        else:
            times[i] = next_time

    return times, matcher.ratio()


def _first_mention_index(script_tokens: Sequence[str], repo: RepoReference) -> int | None:
    """Return the script-token index where *repo* is first mentioned.

    Searches for two identifiers and returns the **earliest** match: the bare
    ``name`` tokens (usually spoken first, e.g. the markdown link text) and the
    fully-qualified ``owner`` + ``name`` sequence (the URL).  The bare name is
    only used when it is distinctive enough (a single token of <4 characters is
    ignored) to avoid matching common English words.

    Returns ``None`` if neither identifier is found.
    """
    name_tokens = normalize_tokens(repo.name)
    owner_tokens = normalize_tokens(repo.owner)

    candidates: list[list[str]] = []
    # Bare name first, but skip a lone short token (e.g. "cli", "app") that
    # could collide with ordinary words.
    if name_tokens and not (len(name_tokens) == 1 and len(name_tokens[0]) < 4):
        candidates.append(name_tokens)
    if owner_tokens and name_tokens:
        candidates.append(owner_tokens + name_tokens)

    best: int | None = None
    for needle in candidates:
        for i in range(len(script_tokens) - len(needle) + 1):
            if list(script_tokens[i : i + len(needle)]) == needle:
                if best is None or i < best:
                    best = i
                break
    return best


def map_repo_times(
    script: str,
    repos: Sequence[RepoReference],
    words: Sequence[WordTimestamp],
    *,
    min_ratio: float = MIN_ALIGN_RATIO,
) -> dict[RepoReference, float] | None:
    """Compute the audio timestamp at which each repo is first discussed.

    Pure (no I/O): given an already-produced word-level transcript, align it to
    *script* and resolve a start time for every repo that can be located.

    Args:
        script: Full podcast script text (header + body).
        repos: Repositories referenced in the script.
        words: Word-level transcript of the audio.
        min_ratio: Minimum alignment similarity to trust the result.

    Returns:
        Mapping of repo → audio start time (seconds) for every repo that was
        both found in the script and successfully aligned.  Returns ``None`` if
        the alignment ratio is below *min_ratio* (untrustworthy) or no repo
        could be mapped — both signal the caller to fall back.
    """
    if not repos or not words:
        return None

    script_tokens = normalize_tokens(_script_body(script))
    trans_tokens, trans_starts = build_transcript_tokens(words)
    times, ratio = align_token_times(script_tokens, trans_tokens, trans_starts)

    if ratio < min_ratio:
        logger.warning(
            "audio alignment ratio %.3f below threshold %.3f; falling back to proportional timing",
            ratio,
            min_ratio,
        )
        return None

    result: dict[RepoReference, float] = {}
    for repo in repos:
        idx = _first_mention_index(script_tokens, repo)
        if idx is None:
            logger.debug("repo %s not found in script tokens", repo.url)
            continue
        t = times[idx] if idx < len(times) else None
        if t is None:
            logger.debug("repo %s mention not aligned to audio", repo.url)
            continue
        result[repo] = float(t)

    if not result:
        logger.warning("no repo mentions could be aligned to audio")
        return None

    logger.info(
        "aligned %d/%d repo mention(s) to audio (ratio=%.3f)",
        len(result),
        len(repos),
        ratio,
    )
    return result


def transcribe_words(
    audio_path: str,
    *,
    model_size: str = DEFAULT_MODEL_SIZE,
) -> list[WordTimestamp]:
    """Transcribe *audio_path* into word-level timestamps with faster-whisper.

    Args:
        audio_path: Path to the audio file (mp3/wav/...).
        model_size: faster-whisper model name. Default ``"tiny"``.

    Returns:
        Word timestamps in chronological order.

    Raises:
        TranscriptionUnavailable: If faster-whisper is not installed, the model
            cannot be loaded, or transcription fails for any reason.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore import-not-found
    except Exception as exc:  # pragma: no cover - import guard
        raise TranscriptionUnavailable(
            "faster-whisper is not installed; cannot transcribe audio"
        ) from exc

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(audio_path, word_timestamps=True)
        words: list[WordTimestamp] = []
        for segment in segments:
            for word in getattr(segment, "words", None) or []:
                words.append(
                    WordTimestamp(
                        text=word.word,
                        start_seconds=float(word.start),
                    )
                )
    except TranscriptionUnavailable:
        raise
    except Exception as exc:
        raise TranscriptionUnavailable(f"word-level transcription failed: {exc}") from exc

    if not words:
        raise TranscriptionUnavailable("transcription produced no words")
    return words


def repo_audio_timestamps(
    script: str,
    repos: Sequence[RepoReference],
    audio_path: str,
    *,
    model_size: str = DEFAULT_MODEL_SIZE,
) -> dict[RepoReference, float] | None:
    """Resolve each repo's discussion start time from real audio (issue #374).

    End-to-end convenience wrapper: transcribe *audio_path*, then align it to
    *script* to time every repo mention.

    Never raises: any failure (missing dependency, model load error, low
    alignment confidence, no repo matched) is logged and returned as ``None``
    so callers fall back to proportional timing.

    Args:
        script: Full podcast script text.
        repos: Repositories referenced in the script.
        audio_path: Path to the synthesised episode audio.
        model_size: faster-whisper model name.

    Returns:
        Mapping of repo → audio start time (seconds), or ``None`` to signal a
        fallback to proportional timing.
    """
    if not repos:
        return None
    try:
        words = transcribe_words(audio_path, model_size=model_size)
    except TranscriptionUnavailable as exc:
        logger.info("audio-cue sync unavailable (%s); using proportional timing", exc)
        return None
    return map_repo_times(script, repos, words)
