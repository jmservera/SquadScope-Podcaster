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

import random
from dataclasses import dataclass, field, replace
from pathlib import Path

from podcaster.audio import (
    AudioValidationResult,
    MusicMixSpec,
    compute_segment_timeline,
    probe_audio,
    render_distribution_audio,
    validate_audio_outputs,
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
from podcaster.hooks import HostHooks, _GENERIC_HOOKS
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


def build_episode_script(
    article: Article,
    podcast_config: PodcastConfig | None = None,
    hooks: HostHooks | None = None,
) -> str:
    """Author a cohesive, journalistic two-voice Claracle episode about ``article``.

    The script follows a narrative arc rather than a list of items: a strong hook
    that names the issue and the week's main story, a clear throughline, segments
    that build tension and pay it off, callbacks, and a satisfying close. The two
    hosts have distinct personalities defined by their ``style`` field in the
    podcast config.

    Both hosts name themselves in the intro and the AI-voice disclosure lands in
    the opening exchange (well within the first 60 seconds). Hosts say the site
    as the configured ``spoken_site`` and never voice a URL scheme. The format
    mirrors :func:`podcaster.generation._script` so existing format checks and
    the production voice mapping stay consistent.

    If ``hooks`` is provided, generated conversational lead-ins are woven into
    segment openings for natural variety.
    """

    if not article.beats:
        raise ValueError("episode script requires at least one discussion beat")

    podcast_config = podcast_config or PodcastConfig()
    host_a_hooks = list(hooks.host_a) if hooks else list(_GENERIC_HOOKS)
    host_b_hooks = list(hooks.host_b) if hooks else list(_GENERIC_HOOKS)
    random.shuffle(host_a_hooks)
    random.shuffle(host_b_hooks)
    header = [
        f"Title: {podcast_config.name} Podcast – Week {article.week}",
        f"Episode: {article.week}",
        f"Podcast: {podcast_config.name} ({podcast_config.url})",
        f"Source URL: {article.url}",
        f"Source SHA256: {article.sha256}",
        f"Voices: {podcast_config.host_a.name} = {podcast_config.host_a.voice} (OpenAI TTS); "
        f"{podcast_config.host_b.name} = {podcast_config.host_b.voice} (OpenAI TTS)",
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

    # Segments: Host A opens each story; Host B responds. They alternate
    # on talking points. Tension is set up and paid off turn by turn, with a
    # callback woven into the final segment.
    last_index = len(article.beats) - 1
    for index, beat in enumerate(article.beats):
        body.append("")
        # Use a generated hook as a lead-in for Host A's topic introduction
        hook = host_a_hooks[index % len(host_a_hooks)]
        body.append(_host_a(f"{hook} {beat.topic}.", podcast_config))
        for point_index, point in enumerate(beat.points):
            if point_index % 2 == 0:
                # Occasionally lead Host B's response with a hook
                if point_index == 0 and host_b_hooks:
                    b_hook = host_b_hooks[index % len(host_b_hooks)]
                    body.append(_host_b(f"{b_hook} {point}", podcast_config))
                else:
                    body.append(_host_b(point, podcast_config))
            else:
                body.append(_host_a(point, podcast_config))
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
class SectionTimestamp:
    """A named section with its start time in the episode."""

    name: str
    start_seconds: float

    @property
    def formatted(self) -> str:
        """Format start_seconds as MM:SS."""
        minutes = int(self.start_seconds) // 60
        seconds = int(self.start_seconds) % 60
        return f"{minutes:02d}:{seconds:02d}"

    def __str__(self) -> str:
        return f"{self.formatted} {self.name}"


def label_script_sections(
    script: str,
    segments: list[tuple[str, str]],
    podcast_config: PodcastConfig | None = None,
) -> list[str]:
    """Map each segment to its section name based on script structure.

    Returns a list of section labels parallel to ``segments``. Consecutive
    segments in the same section share the same label.

    Structure: Intro (first 4 segments), then one section per beat (topic),
    then Outro (last 2 segments).
    """
    config = podcast_config or PodcastConfig()
    # Extract beat topics from the script to use as section names
    beats = _extract_beat_topics(script, config)

    n = len(segments)
    labels: list[str] = []

    intro_count = 4
    outro_count = 2

    # If the segment count is too small, label everything as the episode
    if n <= intro_count + outro_count:
        return [config.name] * n

    # Intro
    for _ in range(min(intro_count, n)):
        labels.append("Intro")

    # Middle segments: distribute among beats
    middle_count = n - intro_count - outro_count
    if beats:
        # Count segments per beat by scanning the middle for host_a topic lines
        # Each beat starts with a host_a line containing the beat topic
        beat_boundaries = _find_beat_boundaries(segments[intro_count : intro_count + middle_count], beats)
        for section_name in beat_boundaries:
            labels.append(section_name)
    else:
        # Fallback: label all middle segments as "Discussion"
        for _ in range(middle_count):
            labels.append("Discussion")

    # Outro
    for _ in range(min(outro_count, n - len(labels))):
        labels.append("Outro")

    # Pad if needed (shouldn't happen, but be safe)
    while len(labels) < n:
        labels.append("Outro")

    return labels[:n]


def _extract_beat_topics(script: str, config: PodcastConfig) -> list[str]:
    """Extract beat topic names from the script by finding host_a topic intro lines."""
    _, _, after = script.partition("\n---")
    source = after if after else script

    host_a_label = config.host_a.name
    topics: list[str] = []

    lines = source.splitlines()
    # Skip the first 4 spoken lines (intro) and look for empty-line-then-host_a patterns
    spoken_count = 0
    in_body = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if spoken_count >= 4:
                in_body = True
            continue
        if stripped.startswith(host_a_label + ":"):
            spoken_count += 1
            if in_body:
                # This is a beat topic intro — extract the topic
                text = stripped[len(host_a_label) + 1:].strip()
                # Topic is typically after the hook: "Hook text. Topic."
                # Take the last sentence as the topic name
                parts = text.rsplit(". ", 1)
                topic = parts[-1].rstrip(".")
                if len(topic) > 60:
                    topic = topic[:57] + "..."
                topics.append(topic)
                in_body = False
        elif stripped.startswith(config.host_b.name + ":"):
            spoken_count += 1

    return topics


def _find_beat_boundaries(
    middle_segments: list[tuple[str, str]],
    beats: list[str],
) -> list[str]:
    """Assign a section label to each middle segment based on beat boundaries.

    Each beat starts at the first host_a segment whose text ends with the beat
    topic (as generated by build_episode_script). Falls back to even distribution
    if detection fails.
    """
    if not middle_segments:
        return []
    if not beats:
        return ["Discussion"] * len(middle_segments)

    # Find segment indices where each beat starts
    beat_start_indices: list[int] = []
    beat_queue = list(beats)

    for i, (role, text) in enumerate(middle_segments):
        if role == "host_a" and beat_queue:
            # The topic intro line ends with "{topic}."
            candidate = beat_queue[0]
            if candidate.rstrip(".") in text:
                beat_start_indices.append(i)
                beat_queue.pop(0)

    # If we couldn't find all beats, distribute evenly
    if len(beat_start_indices) < len(beats) // 2 + 1:
        chunk = max(1, len(middle_segments) // max(1, len(beats)))
        labels: list[str] = []
        for idx, seg in enumerate(middle_segments):
            beat_idx = min(idx // chunk, len(beats) - 1)
            labels.append(beats[beat_idx])
        return labels

    # Assign labels based on detected boundaries
    labels = []
    boundary_iter = iter(enumerate(beat_start_indices))
    current_beat_idx, _ = next(boundary_iter)
    next_entry = next(boundary_iter, None)

    for i in range(len(middle_segments)):
        if next_entry is not None and i >= next_entry[1]:
            current_beat_idx = next_entry[0]
            next_entry = next(boundary_iter, None)
        labels.append(beats[current_beat_idx] if current_beat_idx < len(beats) else beats[-1])

    return labels


def compute_section_timestamps(
    segment_durations: list[float],
    section_labels: list[str],
    gap_seconds: float = 0.35,
    speech_offset_seconds: float = 0.0,
) -> list[SectionTimestamp]:
    """Compute de-duplicated section timestamps from segment timing.

    Returns one :class:`SectionTimestamp` per unique consecutive section,
    using the start time of the first segment in that section.

    ``speech_offset_seconds`` accounts for any delay before speech starts
    (e.g., intro music full-volume period) so timestamps match the final mix.
    """
    if not segment_durations or not section_labels:
        return []

    starts, _ = compute_segment_timeline(segment_durations, gap_seconds)
    timestamps: list[SectionTimestamp] = []
    prev_label: str | None = None

    for i, label in enumerate(section_labels[: len(starts)]):
        if label != prev_label:
            timestamps.append(SectionTimestamp(
                name=label, start_seconds=starts[i] + speech_offset_seconds
            ))
            prev_label = label

    return timestamps


def format_timestamps_block(timestamps: list[SectionTimestamp]) -> str:
    """Format timestamps as a plain-text block for episode descriptions.

    Example output:
        00:00 Intro
        01:32 The Signal
        04:12 Outro
    """
    return "\n".join(str(ts) for ts in timestamps)


def format_timestamps_html(timestamps: list[SectionTimestamp]) -> str:
    """Format timestamps as HTML for Spotify episode descriptions."""
    import html as html_mod

    if not timestamps:
        return ""
    lines = "<br/>".join(
        f"{ts.formatted} {html_mod.escape(ts.name)}" for ts in timestamps
    )
    return f"<p>Timestamps:</p><p>{lines}</p>"


@dataclass(frozen=True)
class EpisodeAudio:
    """Result of synthesizing and validating one episode's audio."""

    output_path: Path
    wav_output_path: Path
    sha256: str
    wav_sha256: str
    byte_length: int
    wav_byte_length: int
    segment_count: int
    validation: AudioValidationResult
    voices: tuple[str, ...] = field(default_factory=tuple)
    timestamps: tuple[SectionTimestamp, ...] = field(default_factory=tuple)


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
    """Synthesize the two-voice script into validated WAV and MP3 artifacts.

    Parses spoken segments, builds the fable/alloy voice plan, synthesizes each
    turn through the gated :func:`podcaster.tts.synthesize_two_voice` (fails
    closed when ``decision['allowed']`` is false), renders a normalized WAV for
    Spotify upload plus a 192 kbps MP3 for distribution, and validates both.
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
    wav_output_path = output_path.with_suffix(".wav")
    # Use provided mix_spec; fall back to default when music paths are given without one.
    effective_mix_spec = music_mix_spec or (MusicMixSpec() if (intro_music or outro_music) else None)
    segment_durations: list[float] = []
    render_distribution_audio(
        audio_segments,
        wav_output_path,
        output_path,
        runner=runner,
        intro_music=intro_music,
        outro_music=outro_music,
        mix_spec=effective_mix_spec,
        segment_extension=effective_config.audio_extension,
        segment_durations_out=segment_durations,
    )
    data = output_path.read_bytes()
    wav_data = wav_output_path.read_bytes()
    digest = checksum(data)
    wav_digest = checksum(wav_data)
    metadata = {
        "mp3": probe_audio(output_path, digest, runner=runner),
        "wav": probe_audio(wav_output_path, wav_digest, runner=runner),
    }
    validation = validate_audio_outputs(metadata, manual_duration_override=manual_duration_override)

    section_labels = label_script_sections(script, segments, podcast_config)
    # Account for intro music delay so timestamps match the final mixed audio
    speech_offset = effective_mix_spec.intro_full_volume_seconds if intro_music else 0.0
    try:
        section_timestamps = tuple(
            compute_section_timestamps(
                segment_durations, section_labels, gap_seconds=0.35,
                speech_offset_seconds=speech_offset,
            )
        )
    except (RuntimeError, OSError):
        section_timestamps = ()

    return EpisodeAudio(
        output_path=output_path,
        wav_output_path=wav_output_path,
        sha256=digest,
        wav_sha256=wav_digest,
        byte_length=len(data),
        wav_byte_length=len(wav_data),
        segment_count=len(segments),
        validation=validation,
        voices=tuple(turn.voice for turn in plan),
        timestamps=section_timestamps,
    )


def _apply_podcast_config(config: TtsConfig, podcast_config: PodcastConfig | None) -> TtsConfig:
    if podcast_config is None:
        return config
    return replace(
        config,
        style_host_a=podcast_config.host_a.style,
        style_host_b=podcast_config.host_b.style,
    )
