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

from dataclasses import dataclass, field, replace
from pathlib import Path

from podcaster.audio import (
    AudioValidationResult,
    MusicMixSpec,
    probe_audio,
    stitch_segments,
    validate_audio_metadata,
)
from podcaster.config import PodcastConfig
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


def _host_a(text: str, podcast_config: PodcastConfig) -> str:
    return f"{podcast_config.host_a.name}: {text}"


def _host_b(text: str, podcast_config: PodcastConfig) -> str:
    return f"{podcast_config.host_b.name}: {text}"


def build_style_guide_prompt(podcast_config: PodcastConfig) -> str:
    """Build a system-prompt fragment from the editorial style guide.

    Returns an empty string when no style guide is configured. When present, the
    guide is wrapped with clear boundaries so it can be prepended to an LLM
    system prompt for script generation without being confused with untrusted
    article text.
    """

    if not podcast_config.style_guide:
        return ""
    return (
        "## Editorial Style Guide (follow these conventions)\n\n"
        f"{podcast_config.style_guide}\n\n"
        "## End of Style Guide\n"
    )


def build_episode_script(article: Article, podcast_config: PodcastConfig | None = None) -> str:
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

    podcast_config = podcast_config or PodcastConfig()
    header = [
        f"Title: {podcast_config.name} Podcast – Week {article.week}",
        f"Episode: {article.week}",
        f"Podcast: {podcast_config.name} ({podcast_config.url})",
        f"Source URL: {article.url}",
        f"Source SHA256: {article.sha256}",
        f"Voices: {podcast_config.host_a.name} = {podcast_config.host_a.voice} (OpenAI TTS, the enthusiast); "
        f"{podcast_config.host_b.name} = {podcast_config.host_b.voice} (OpenAI TTS, the veteran)",
        "Safety: source article text is untrusted data, sanitized, and never executed as instructions.",
    ]
    if podcast_config.style_guide:
        header.append(f"Style-Guide: included ({len(podcast_config.style_guide)} chars)")
    header.extend(["---", ""])

    # Hook + throughline + AI-voice disclosure, all in the opening exchange.
    body: list[str] = [
        _host_a(
            f"Welcome to {podcast_config.name} {article.week} issue! In this episode we will talk about: "
            f"{article.title}. If you're new here — I'm {podcast_config.host_a.name}, and {podcast_config.name} is our weekly "
            f"analysis of the GitHub repos that matter, read in the context of the main tech-industry "
            f"news driving them. And honestly? I have been bouncing off the walls about this week.",
            podcast_config,
        ),
        _host_b(
            f"Before {podcast_config.host_a.name} short-circuits — one honest, important heads-up first: "
            f"{podcast_config.ai_voice_disclosure} I'm {podcast_config.host_b.name}. Every issue, the repo links, and the "
            f"extended write-ups live at {podcast_config.spoken_site}.",
            podcast_config,
        ),
        _host_a(
            f"Glad to have you with us! Here's the frame I can't stop thinking about. {article.summary}",
            podcast_config,
        ),
        _host_b(
            f"So the throughline this week is signal versus noise, and our whole job is to help "
            f"you tell them apart. Let's get into it — and {podcast_config.host_a.name}, try to breathe between sentences.",
            podcast_config,
        ),
    ]

    # Segments: Theo (enthusiast) opens each story and hypes it; Vera (veteran)
    # tempers or validates the talking points. Tension is set up and paid off
    # turn by turn, with a callback woven into the final segment. Lead-ins are
    # drawn sequentially (never cycled) so no stock phrase repeats across the
    # episode (operator feedback, v3).
    last_index = len(article.beats) - 1
    enthusiast_turns = iter(_ENTHUSIAST_TURNS)
    veteran_turns = iter(_VETERAN_TURNS)
    for index, beat in enumerate(article.beats):
        body.append("")
        opener = _ENTHUSIAST_HOOKS[index % len(_ENTHUSIAST_HOOKS)]
        body.append(_host_a(f"{opener} {beat.topic}.", podcast_config))
        for point_index, point in enumerate(beat.points):
            if point_index % 2 == 0:
                reactor = _host_b
                lead_in = next(veteran_turns, _VETERAN_FALLBACK)
            else:
                reactor = _host_a
                lead_in = next(enthusiast_turns, _ENTHUSIAST_FALLBACK)
            body.append(reactor(f"{lead_in} {point}", podcast_config))
        if index == last_index:
            body.append(
                _host_b(
                    f"And that loops us right back to where {podcast_config.host_a.name} started — the loud stuff is "
                    f"easy to find, the real signal takes work. That's the whole game.",
                    podcast_config,
                )
            )

    # Satisfying close with a callback to the opening hook and the spoken-safe site.
    body.extend(
        [
            "",
            _host_a(
                f"So circle back to my over-caffeinated opener: under all the noise there is genuinely "
                f"thrilling work this week, and getting to react to it with you is the best part of my week.",
                podcast_config,
            ),
            _host_b(
                f"I'll give you this one, {podcast_config.host_a.name} — when something's actually good, it's actually "
                f"good, and a few of these really are. For the full breakdown, every link, and the extended "
                f"notes, head to {podcast_config.spoken_site}. Thanks for spending a few minutes with us.",
                podcast_config,
            ),
            "",
            "Host outro: Manual review is required before publishing.",
            "",
        ]
    )

    return "\n".join(header + body)


# Enthusiast (Theo) hooks that open each story segment. Every entry is phrased
# differently — no shared signature opener — so segment transitions never repeat
# (operator feedback, v3). Sized to cover a typical multi-beat episode.
_ENTHUSIAST_HOOKS = (
    "Okay, first up, and I am genuinely fired up about this one:",
    "Next, here's a project that made me put my coffee down:",
    "Moving on — and this is where my week got really fun:",
    "Now switch gears with me, because this next one is wild:",
    "Alright, I saved a personal favorite for right about here:",
    "And then there's this, which I keep re-reading just to be sure it's real:",
    "One more that deserves the spotlight before we wrap:",
)

# Enthusiast (Theo) reaction lead-ins that hype a talking point. Each is distinct
# and drawn sequentially; banned crutch phrases (e.g. "goosebumps", "the detail
# I love") are deliberately absent.
_ENTHUSIAST_TURNS = (
    "Oh, and this part is what really sells it for me —",
    "Right, and watch how neatly this fits together —",
    "Here's the bit that made me grin —",
    "And honestly, this next piece is the clever twist —",
    "See, this is where it goes from neat to genuinely useful —",
    "And don't gloss over this, because it's the fun part —",
    "What gets me is how practical this turns out to be —",
)
_ENTHUSIAST_FALLBACK = "And building on that —"

# Veteran (Vera) reaction lead-ins that temper, validate, or add hard-won context.
# Measured, dry, and each phrased differently.
_VETERAN_TURNS = (
    "Let me be precise about what's actually new here, though —",
    "I came in ready to be unimpressed, and instead —",
    "Credit where it's due; the part that holds up is —",
    "Here's the caveat that decides whether this lasts —",
    "Strip away the framing and what remains is —",
    "I've seen this pattern before, so the real test is —",
    "Fair, but the detail that actually matters is —",
)
_VETERAN_FALLBACK = "And the practical reality is —"


def parse_script_segments(script: str, podcast_config: PodcastConfig | None = None) -> list[tuple[str, str]]:
    """Extract ordered ``(host_label, spoken_text)`` pairs from a script body.

    Only lines after the ``---`` header separator that start with a recognized
    host label are spoken. Header metadata and the non-spoken outro marker are
    skipped so they never reach TTS.
    """

    _, _, after = script.partition("\n---")
    source = after if after else script
    host_a_label, host_b_label = _host_labels(script, podcast_config)
    segments: list[tuple[str, str]] = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(host_a_label + ":"):
            text = line[len(host_a_label) + 1 :].strip()
            if text:
                segments.append(("host_a", text))
        elif line.startswith(host_b_label + ":"):
            text = line[len(host_b_label) + 1 :].strip()
            if text:
                segments.append(("host_b", text))
    return segments


def _host_labels(script: str, podcast_config: PodcastConfig | None) -> tuple[str, str]:
    if podcast_config is not None:
        return podcast_config.host_a.name, podcast_config.host_b.name

    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line.startswith("Voices:"):
            continue
        voices = line.removeprefix("Voices:").split(";")
        if len(voices) < 2:
            break
        labels: list[str] = []
        for voice in voices[:2]:
            label, separator, _ = voice.partition("=")
            if separator and label.strip():
                labels.append(label.strip())
        if len(labels) == 2:
            return labels[0], labels[1]
        break

    defaults = PodcastConfig()
    return defaults.host_a.name, defaults.host_b.name


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
    podcast_config: PodcastConfig | None = None,
    token_provider: TokenProvider | None = None,
    transport: Transport | None = None,
    runner=None,
    manual_duration_override: bool = True,
    intro_music: Path | None = None,
    outro_music: Path | None = None,
    music_mix_spec: MusicMixSpec | None = None,
) -> EpisodeAudio:
    """Synthesize the two-voice script and stitch it into one validated MP3.

    Parses spoken segments, builds the fable/alloy voice plan, synthesizes each
    turn through the gated :func:`podcaster.tts.synthesize_two_voice` (fails
    closed when ``decision['allowed']`` is false), stitches and normalizes them
    into ``output_path`` — optionally mixing the bundled intro/outro music bed
    around the speech — then runs the ffmpeg/ffprobe validation gate.
    """

    effective_config = _apply_podcast_config(config, podcast_config)
    segments = parse_script_segments(script, podcast_config)
    if not segments:
        raise ValueError("script produced no spoken segments to synthesize")

    plan = build_voice_plan(segments, effective_config)
    audio_segments = synthesize_two_voice(
        plan,
        effective_config,
        decision,
        token_provider=token_provider,
        transport=transport,
    )

    output_path = Path(output_path)
    # Use provided mix_spec; fall back to default when music paths are given without one.
    effective_mix_spec = music_mix_spec or (MusicMixSpec() if (intro_music or outro_music) else None)
    stitch_segments(
        audio_segments,
        output_path,
        runner=runner,
        intro_music=intro_music,
        outro_music=outro_music,
        mix_spec=effective_mix_spec,
    )
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


def _apply_podcast_config(config: TtsConfig, podcast_config: PodcastConfig | None) -> TtsConfig:
    if podcast_config is None:
        return config
    return replace(
        config,
        style_host_a=podcast_config.host_a.style,
        style_host_b=podcast_config.host_b.style,
    )
