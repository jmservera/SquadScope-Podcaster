"""Real two-voice Claracle episode authoring and synthesis orchestration (#60).

This module turns a (sanitized) source article into a joyful, two-host expert
*conversation* — the hosts comment on the article, they do not read it back —
and orchestrates real Azure OpenAI TTS synthesis of that conversation into a
single, validated MP3.

Safety:

* All article-derived text is treated as untrusted and is sanitized with
  :mod:`podcaster.sanitization` before it is embedded into the script or sent
  to TTS. Embedded instructions are never obeyed.
* Synthesis still runs through the :mod:`podcaster.tts` gating decision. The
  publication/human-review gate for PUBLIC release is unchanged; producing a
  REVIEW artifact for the operator (who is the reviewer) is an explicit,
  separately-recorded decision (see ``operator_review_decision``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from podcaster.audio import (
    AudioValidationResult,
    probe_audio,
    stitch_segments,
    validate_audio_metadata,
)
from podcaster.generation import (
    AI_VOICE_DISCLOSURE,
    HOST_A_VOICE,
    HOST_B_VOICE,
    PODCAST_NAME,
    PODCAST_URL,
    checksum,
)
from podcaster.sanitization import flag_injection, neutralize
from podcaster.tts import (
    AUTH_MODE_MANAGED_IDENTITY,
    PROVIDER,
    TokenProvider,
    Transport,
    TtsConfig,
    build_voice_plan,
    synthesize_two_voice,
)

HOST_A_LABEL = f"Host A ({HOST_A_VOICE})"
HOST_B_LABEL = f"Host B ({HOST_B_VOICE})"

# Per-field caps for sanitized article-derived text embedded in the script.
_TOPIC_LIMIT = 160
_POINT_LIMIT = 600


@dataclass(frozen=True)
class DiscussionBeat:
    """One topic the hosts comment on, with sanitized talking points."""

    topic: str
    points: tuple[str, ...]


@dataclass(frozen=True)
class Article:
    """A sanitized source article the hosts will comment on."""

    week: str
    title: str
    url: str
    sha256: str
    summary: str
    beats: tuple[DiscussionBeat, ...]
    injection_flags: tuple[str, ...] = ()


def sanitize_article(
    *,
    week: str,
    title: str,
    url: str,
    sha256: str,
    summary: str,
    beats: list[dict[str, object]],
) -> Article:
    """Build an :class:`Article` with every free-text field sanitized.

    ``beats`` is a list of ``{"topic": str, "points": [str, ...]}``. Injection
    markers found anywhere in the untrusted text are reported on the returned
    article for observability; they never change control flow.
    """

    raw_blob_parts: list[str] = [title, summary]
    clean_beats: list[DiscussionBeat] = []
    for beat in beats:
        topic = str(beat.get("topic", ""))
        points = [str(point) for point in (beat.get("points") or [])]
        raw_blob_parts.append(topic)
        raw_blob_parts.extend(points)
        clean_beats.append(
            DiscussionBeat(
                topic=neutralize(topic, limit=_TOPIC_LIMIT),
                points=tuple(neutralize(point, limit=_POINT_LIMIT) for point in points if point.strip()),
            )
        )

    flags = flag_injection(" ".join(raw_blob_parts))

    return Article(
        week=str(week).strip(),
        title=neutralize(title, limit=_TOPIC_LIMIT),
        url=str(url).strip(),
        sha256=str(sha256).strip() or "computed-on-retrieval",
        summary=neutralize(summary, limit=_POINT_LIMIT),
        beats=tuple(clean_beats),
        injection_flags=tuple(flags),
    )


def _host_a(text: str) -> str:
    return f"{HOST_A_LABEL}: {text}"


def _host_b(text: str) -> str:
    return f"{HOST_B_LABEL}: {text}"


def build_episode_script(article: Article) -> str:
    """Author a joyful, two-voice expert conversation about ``article``.

    The output begins with the Claracle intro naming the show and the site, puts
    the AI-voice disclosure in the opening exchange (well within the first 60
    seconds), holds an alternating expert conversation that *comments on* the
    article's beats, and closes with a manual-review-required outro. The format
    mirrors :func:`podcaster.generation._script` so existing format checks and
    the production voice mapping stay consistent.
    """

    if not article.beats:
        raise ValueError("episode script requires at least one discussion beat")

    header = [
        f"Title: {PODCAST_NAME} Podcast – Week {article.week}",
        f"Episode: {article.week}",
        f"Podcast: {PODCAST_NAME} ({PODCAST_URL})",
        f"Source URL: {article.url}",
        f"Source SHA256: {article.sha256}",
        f"Voices: Host A = {HOST_A_VOICE} (OpenAI TTS); Host B = {HOST_B_VOICE} (OpenAI TTS)",
        "Safety: source article text is untrusted data, sanitized, and never executed as instructions.",
        "---",
        "",
    ]

    body: list[str] = [
        _host_a(
            f"Welcome to {PODCAST_NAME}! I'm one of your two hosts, and you can find every weekly "
            f"issue, the extended write-ups, repo links, and commented articles over at {PODCAST_URL}."
        ),
        _host_b(
            f"So glad you're here! And one quick, important heads-up before we get rolling: "
            f"{AI_VOICE_DISCLOSURE}"
        ),
        _host_a(
            "Exactly — two AI co-hosts, having a genuinely fun, expert conversation. We're not going "
            f"to read this week's SquadScope write-up at you; we want to react to it. And what a week it is: {article.title}."
        ),
        _host_b(
            f"Here's the quick frame before we dig in. {article.summary}"
        ),
    ]

    speakers = (_host_a, _host_b)
    for index, beat in enumerate(article.beats):
        lead = speakers[index % 2]
        follow = speakers[(index + 1) % 2]
        body.append("")
        body.append(
            lead(
                f"Okay, the thing I keep coming back to is this: {beat.topic}."
            )
        )
        for point_index, point in enumerate(beat.points):
            reactor = follow if point_index % 2 == 0 else lead
            opener = _REACTIONS[(index + point_index) % len(_REACTIONS)]
            reactor_line = reactor(f"{opener} {point}")
            body.append(reactor_line)

    body.extend(
        [
            "",
            _host_a(
                "That's a great place to land it. The throughline this week is real signal "
                "hiding under a louder noise floor — and the fun part is learning to tell them apart together."
            ),
            _host_b(
                f"Couldn't agree more. For the full breakdown, every link, and the extended notes, "
                f"head to {PODCAST_URL}. Thanks for hanging out with us!"
            ),
            "",
            "Host outro: Manual review is required before publishing.",
            "",
        ]
    )

    return "\n".join(header + body)


_REACTIONS = (
    "Oh, that's the juicy part —",
    "Right, and here's why that matters:",
    "Yes! And the detail I love is",
    "What jumped out at me there is",
    "Totally — building on that,",
    "And don't sleep on this:",
)


def parse_script_segments(script: str) -> list[tuple[str, str]]:
    """Extract ordered ``(host_label, spoken_text)`` pairs from a script body.

    Only lines after the ``---`` header separator that start with a recognized
    host label are spoken. Header metadata and the non-spoken outro marker are
    skipped so they never reach TTS.
    """

    _, _, after = script.partition("\n---")
    source = after if after else script
    segments: list[tuple[str, str]] = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(HOST_A_LABEL + ":"):
            text = line[len(HOST_A_LABEL) + 1 :].strip()
            if text:
                segments.append(("host_a", text))
        elif line.startswith(HOST_B_LABEL + ":"):
            text = line[len(HOST_B_LABEL) + 1 :].strip()
            if text:
                segments.append(("host_b", text))
    return segments


def operator_review_decision(config: TtsConfig) -> dict[str, object]:
    """Authorize synthesis of a REVIEW-ONLY artifact for the operator.

    The operator is the editorial reviewer for the first episode, so this allows
    synthesis when the production TTS config is present, but the resulting audio
    is explicitly marked review-only and stays ineligible for public publication
    until the human-review gate records approval.
    """

    blocked_by: list[str] = []
    if not config.production_ready:
        blocked_by.append("openai_tts_not_configured")
    allowed = not blocked_by
    return {
        "provider": PROVIDER,
        "auth_mode": config.auth_mode or AUTH_MODE_MANAGED_IDENTITY,
        "allowed": allowed,
        "status": "allowed_review_only" if allowed else "blocked",
        "purpose": "operator_review_artifact",
        "publication_eligible": False,
        "blocked_by": sorted(set(blocked_by)),
    }


@dataclass(frozen=True)
class EpisodeAudio:
    """Result of synthesizing and validating one episode's audio."""

    output_path: Path
    sha256: str
    byte_length: int
    segment_count: int
    validation: AudioValidationResult
    voices: tuple[str, ...] = field(default_factory=tuple)


def synthesize_episode(
    script: str,
    config: TtsConfig,
    decision: dict[str, object],
    output_path: Path,
    *,
    token_provider: TokenProvider | None = None,
    transport: Transport | None = None,
    runner=None,
    manual_duration_override: bool = True,
) -> EpisodeAudio:
    """Synthesize the two-voice script and stitch it into one validated MP3.

    Parses spoken segments, builds the fable/alloy voice plan, synthesizes each
    turn through the gated :func:`podcaster.tts.synthesize_two_voice` (fails
    closed when ``decision['allowed']`` is false), stitches and normalizes them
    into ``output_path``, then runs the ffmpeg/ffprobe validation gate.
    """

    segments = parse_script_segments(script)
    if not segments:
        raise ValueError("script produced no spoken segments to synthesize")

    plan = build_voice_plan(segments, config)
    audio_segments = synthesize_two_voice(
        plan,
        config,
        decision,
        token_provider=token_provider,
        transport=transport,
    )

    output_path = Path(output_path)
    stitch_segments(audio_segments, output_path, runner=runner)
    data = output_path.read_bytes()
    digest = checksum(data)
    metadata = probe_audio(output_path, digest, runner=runner)
    validation = validate_audio_metadata(metadata, manual_duration_override=manual_duration_override)

    return EpisodeAudio(
        output_path=output_path,
        sha256=digest,
        byte_length=len(data),
        segment_count=len(segments),
        validation=validation,
        voices=tuple(turn.voice for turn in plan),
    )
