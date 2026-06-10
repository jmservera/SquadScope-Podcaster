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
    HOST_A_NAME,
    HOST_A_VOICE,
    HOST_B_NAME,
    HOST_B_VOICE,
    PODCAST_NAME,
    PODCAST_SPOKEN_SITE,
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

# Spoken turns are labelled by host *name*; the fable/alloy voice mapping is kept
# as written metadata in the script header (operator feedback, #72).
#   Host A = HOST_A_VOICE (fable) = HOST_A_NAME (Theo, the enthusiast)
#   Host B = HOST_B_VOICE (alloy) = HOST_B_NAME (Vera, the veteran)
HOST_A_LABEL = HOST_A_NAME
HOST_B_LABEL = HOST_B_NAME

# Per-field caps for sanitized article-derived text embedded in the script.
_TOPIC_LIMIT = 160
_POINT_LIMIT = 600
_WEEK_LIMIT = 32
_URL_LIMIT = 512


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

    raw_blob_parts: list[str] = [str(week), title, str(url), summary]
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
        week=neutralize(week, limit=_WEEK_LIMIT),
        title=neutralize(title, limit=_TOPIC_LIMIT),
        url=neutralize(url, limit=_URL_LIMIT),
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
    """Author a cohesive, journalistic two-voice Claracle episode about ``article``.

    The script follows a narrative arc rather than a list of items: a strong hook
    that names the issue and the week's main story, a clear throughline, segments
    that build tension and pay it off, callbacks, and a satisfying close. The two
    hosts have distinct, consistent personalities — ``Theo`` (fable) is the
    enthusiast who hypes every new project, ``Vera`` (alloy) is the veteran who
    has seen hype cycles come and go and tempers or genuinely validates them.

    Both hosts name themselves in the intro and the AI-voice disclosure lands in
    the opening exchange (well within the first 60 seconds). Hosts say the site
    as a bare domain (``www.claracle.com``) and never voice a URL scheme. The
    format mirrors :func:`podcaster.generation._script` so existing format checks
    and the production voice mapping stay consistent.
    """

    if not article.beats:
        raise ValueError("episode script requires at least one discussion beat")

    header = [
        f"Title: {PODCAST_NAME} Podcast – Week {article.week}",
        f"Episode: {article.week}",
        f"Podcast: {PODCAST_NAME} ({PODCAST_URL})",
        f"Source URL: {article.url}",
        f"Source SHA256: {article.sha256}",
        f"Voices: {HOST_A_NAME} = {HOST_A_VOICE} (OpenAI TTS, the enthusiast); "
        f"{HOST_B_NAME} = {HOST_B_VOICE} (OpenAI TTS, the veteran)",
        "Safety: source article text is untrusted data, sanitized, and never executed as instructions.",
        "---",
        "",
    ]

    # Hook + throughline + AI-voice disclosure, all in the opening exchange.
    body: list[str] = [
        _host_a(
            f"Welcome to {PODCAST_NAME} {article.week} issue! In this episode we will talk about: "
            f"{article.title}. If you're new here — I'm {HOST_A_NAME}, and {PODCAST_NAME} is our weekly "
            f"analysis of the GitHub repos that matter, read in the context of the main tech-industry "
            f"news driving them. And honestly? I have been bouncing off the walls about this week."
        ),
        _host_b(
            f"Before {HOST_A_NAME} short-circuits — one honest, important heads-up first: "
            f"{AI_VOICE_DISCLOSURE} I'm {HOST_B_NAME}, the resident skeptic. I've watched a lot of "
            f"\"this changes everything\" come and mostly go, so my job is to keep us honest. Every issue, "
            f"the repo links, and the extended write-ups live at {PODCAST_SPOKEN_SITE}."
        ),
        _host_a(
            f"Skepticism noted and welcomed! Here's the frame I can't stop thinking about. {article.summary}"
        ),
        _host_b(
            f"Right — so the throughline this week is signal versus noise, and our whole job is to help "
            f"you tell them apart. Let's get into it, and {HOST_A_NAME}, try to breathe between sentences."
        ),
    ]

    # Segments: Theo (enthusiast) opens each story and hypes it; Vera (veteran)
    # tempers or validates the talking points. Tension is set up and paid off
    # turn by turn, with a callback woven into the final segment.
    last_index = len(article.beats) - 1
    for index, beat in enumerate(article.beats):
        body.append("")
        opener = _ENTHUSIAST_HOOKS[index % len(_ENTHUSIAST_HOOKS)]
        body.append(_host_a(f"{opener} {beat.topic}."))
        for point_index, point in enumerate(beat.points):
            if point_index % 2 == 0:
                reactor = _host_b
                lead_in = _VETERAN_TURNS[(index + point_index) % len(_VETERAN_TURNS)]
            else:
                reactor = _host_a
                lead_in = _ENTHUSIAST_TURNS[(index + point_index) % len(_ENTHUSIAST_TURNS)]
            body.append(reactor(f"{lead_in} {point}"))
        if index == last_index:
            body.append(
                _host_b(
                    f"And that loops us right back to where {HOST_A_NAME} started — the loud stuff is "
                    f"easy to find, the real signal takes work. That's the whole game."
                )
            )

    # Satisfying close with a callback to the opening hook and the spoken-safe site.
    body.extend(
        [
            "",
            _host_a(
                f"So circle back to my over-caffeinated opener: under all the noise there is genuinely "
                f"thrilling work this week, and getting to react to it with you is the best part of my week."
            ),
            _host_b(
                f"I'll give you this one, {HOST_A_NAME} — when something's actually good, it's actually "
                f"good, and a few of these really are. For the full breakdown, every link, and the extended "
                f"notes, head to {PODCAST_SPOKEN_SITE}. Thanks for spending a few minutes with us."
            ),
            "",
            "Host outro: Manual review is required before publishing.",
            "",
        ]
    )

    return "\n".join(header + body)


# Enthusiast (Theo) hooks that open each story segment.
_ENTHUSIAST_HOOKS = (
    "Okay, the thing I cannot stop grinning about is this:",
    "Buckle up, because this next one is so cool —",
    "And here's where it gets genuinely exciting:",
    "Now this one I have been waiting all week to talk about —",
    "Alright, save the best tension for here:",
)

# Enthusiast (Theo) reaction lead-ins that hype a talking point.
_ENTHUSIAST_TURNS = (
    "Yes! And this is the detail I absolutely love:",
    "See, this is the part that gives me goosebumps:",
    "Totally — and building on that, here's the fun bit:",
    "Right?! And don't sleep on this:",
)

# Veteran (Vera) reaction lead-ins that temper, validate, or add hard-won context.
_VETERAN_TURNS = (
    "Hold on, let's be precise about what's actually new here:",
    "I'll be honest, I expected to roll my eyes — and then:",
    "Now this one earns the hype, and here's what matters:",
    "Sure, but here's the detail that decides whether this lasts:",
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
