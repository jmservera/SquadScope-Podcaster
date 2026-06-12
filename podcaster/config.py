from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


def _generation_defaults():
    from podcaster import generation

    return generation


def _default_name() -> str:
    return _generation_defaults().PODCAST_NAME


def _default_url() -> str:
    return _generation_defaults().PODCAST_URL


def _default_spoken_site() -> str:
    return _generation_defaults().PODCAST_SPOKEN_SITE


def _default_disclosure() -> str:
    return _generation_defaults().AI_VOICE_DISCLOSURE


def _default_host_a() -> "HostConfig":
    defaults = _generation_defaults()
    return HostConfig(name=defaults.HOST_A_NAME, voice=defaults.HOST_A_VOICE, style=defaults.HOST_A_STYLE)


def _default_host_b() -> "HostConfig":
    defaults = _generation_defaults()
    return HostConfig(name=defaults.HOST_B_NAME, voice=defaults.HOST_B_VOICE, style=defaults.HOST_B_STYLE)


def _string_or_default(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


@dataclass(frozen=True)
class HostConfig:
    name: str
    voice: str
    style: str


@dataclass(frozen=True)
class PodcastConfig:
    name: str = field(default_factory=_default_name)
    url: str = field(default_factory=_default_url)
    spoken_site: str = field(default_factory=_default_spoken_site)
    ai_voice_disclosure: str = field(default_factory=_default_disclosure)
    host_a: HostConfig = field(default_factory=_default_host_a)
    host_b: HostConfig = field(default_factory=_default_host_b)
    style_guide: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "PodcastConfig":
        """Build from an optional ``podcast_config`` payload object."""

        if payload is None:
            return cls()

        config_payload: object = payload
        if "podcast_config" in payload:
            config_payload = payload.get("podcast_config")

        if not isinstance(config_payload, Mapping):
            return cls()

        defaults = cls()
        host_a_payload = config_payload.get("host_a")
        host_b_payload = config_payload.get("host_b")
        style_guide_raw = config_payload.get("style_guide")
        style_guide = style_guide_raw.strip() if isinstance(style_guide_raw, str) else ""
        return cls(
            name=_string_or_default(config_payload.get("name"), defaults.name),
            url=_string_or_default(config_payload.get("url"), defaults.url),
            spoken_site=_string_or_default(config_payload.get("spoken_site"), defaults.spoken_site),
            ai_voice_disclosure=_string_or_default(
                config_payload.get("ai_voice_disclosure"), defaults.ai_voice_disclosure
            ),
            host_a=_host_from_payload(host_a_payload, defaults.host_a),
            host_b=_host_from_payload(host_b_payload, defaults.host_b),
            style_guide=style_guide,
        )


def _host_from_payload(payload: object, defaults: HostConfig) -> HostConfig:
    if not isinstance(payload, Mapping):
        return defaults

    return HostConfig(
        name=_string_or_default(payload.get("name"), defaults.name),
        voice=_string_or_default(payload.get("voice"), defaults.voice),
        style=_string_or_default(payload.get("style"), defaults.style),
    )


# --- Script Directions ---


@dataclass(frozen=True)
class EpisodeStyle:
    """LLM-facing episode structure/tone constraints from SquadScope config."""

    format: str = ""
    tone: str = ""
    segment_order: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "EpisodeStyle":
        if not isinstance(data, Mapping):
            return cls()
        segment_order_raw = data.get("segment_order")
        segments: tuple[str, ...] = ()
        if isinstance(segment_order_raw, (list, tuple)):
            segments = tuple(str(s) for s in segment_order_raw if s)
        return cls(
            format=_safe_str(data.get("format")),
            tone=_safe_str(data.get("tone")),
            segment_order=segments,
        )


@dataclass(frozen=True)
class ScriptDirections:
    """Parsed ``script_directions`` from the SquadScope payload.

    Guides LLM script generation: episode structure, opening/closing cues, tone.
    All fields are optional; absent values do not change the default prompt.
    """

    episode_style: EpisodeStyle = field(default_factory=EpisodeStyle)
    cold_open: str = ""
    ai_disclosure_cue: str = ""
    corrections_path: str = ""
    source_article_link: str = ""
    sign_off: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "ScriptDirections":
        """Parse ``script_directions`` from the top-level API payload."""

        if not isinstance(payload, Mapping):
            return cls()
        sd = payload.get("script_directions")
        if not isinstance(sd, Mapping):
            return cls()

        opening = sd.get("opening_cues") if isinstance(sd.get("opening_cues"), Mapping) else {}
        closing = sd.get("closing_cues") if isinstance(sd.get("closing_cues"), Mapping) else {}
        episode_style = EpisodeStyle.from_mapping(sd.get("episode_style"))

        return cls(
            episode_style=episode_style,
            cold_open=_safe_str(opening.get("cold_open")),
            ai_disclosure_cue=_safe_str(opening.get("ai_disclosure")),
            corrections_path=_safe_str(closing.get("corrections_path")),
            source_article_link=_safe_str(closing.get("source_article_link")),
            sign_off=_safe_str(closing.get("sign_off")),
        )

    @property
    def has_content(self) -> bool:
        """True if any field carries usable content."""
        return bool(
            self.episode_style.format
            or self.episode_style.tone
            or self.episode_style.segment_order
            or self.cold_open
            or self.ai_disclosure_cue
            or self.corrections_path
            or self.source_article_link
            or self.sign_off
        )


# --- Music Mix ---


@dataclass(frozen=True)
class MusicMixConfig:
    """Parsed ``music_mix`` spec from the SquadScope payload.

    Maps onto :class:`podcaster.audio.MusicMixSpec` for audio stitching. Fields
    use the same naming as ``MusicMixSpec`` attributes where applicable. Track
    resolution (file lookup) is the caller's responsibility.
    """

    track: str = ""
    intro_full_volume_seconds: float = 10.0
    intro_fade_down_under: str = ""
    outro_start_position: str = ""
    outro_fade_up_during: str = ""
    outro_play_to_end: bool = True

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "MusicMixConfig":
        """Parse ``music_mix`` (may live inside ``script_directions`` or at top level)."""

        if not isinstance(payload, Mapping):
            return cls()

        # Accept music_mix at top level or nested under script_directions
        mm = payload.get("music_mix")
        if not isinstance(mm, Mapping):
            sd = payload.get("script_directions")
            if isinstance(sd, Mapping):
                mm = sd.get("music_mix")
            if not isinstance(mm, Mapping):
                return cls()

        intro = mm.get("intro") if isinstance(mm.get("intro"), Mapping) else {}
        outro = mm.get("outro") if isinstance(mm.get("outro"), Mapping) else {}

        return cls(
            track=_safe_str(mm.get("track")),
            intro_full_volume_seconds=_safe_float(intro.get("full_volume_seconds"), 10.0),
            intro_fade_down_under=_safe_str(intro.get("fade_down_under")),
            outro_start_position=_safe_str(outro.get("start_position")),
            outro_fade_up_during=_safe_str(outro.get("fade_up_during")),
            outro_play_to_end=bool(outro.get("play_to_end", True)),
        )

    @property
    def has_track(self) -> bool:
        return bool(self.track)

    def to_mix_spec_kwargs(self) -> dict[str, Any]:
        """Return keyword arguments compatible with :class:`podcaster.audio.MusicMixSpec`.

        Only includes fields that diverge from MusicMixSpec defaults so callers
        can do ``MusicMixSpec(**config.to_mix_spec_kwargs())``.
        """

        kwargs: dict[str, Any] = {}
        if self.intro_full_volume_seconds != 10.0:
            kwargs["intro_full_volume_seconds"] = self.intro_full_volume_seconds
        # intro_fade_down_under may carry a numeric segment count (e.g. "2")
        if self.intro_fade_down_under:
            segments = _parse_segment_count(self.intro_fade_down_under)
            if segments is not None:
                kwargs["intro_speech_segments_under_music"] = segments
        # outro_start_position is a time string like "1:15" → convert to seconds
        if self.outro_start_position:
            kwargs["outro_start_offset_seconds"] = _parse_time_offset(self.outro_start_position)
        # outro_fade_up_during may carry a duration in seconds (e.g. "5")
        if self.outro_fade_up_during:
            fade_secs = _parse_fade_duration(self.outro_fade_up_during)
            if fade_secs is not None:
                kwargs["outro_fade_in_seconds"] = fade_secs
        return kwargs


def _safe_str(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _safe_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _parse_time_offset(value: str) -> float:
    """Parse a time offset like '1:15' or '75' into seconds."""

    parts = value.strip().split(":")
    try:
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 75.0


def _parse_segment_count(value: str) -> int | None:
    """Parse a segment count from intro_fade_down_under.

    Accepts plain integers (e.g. "2") or descriptive text with a leading digit
    (e.g. "2 segments"). Returns None when the value is purely descriptive and
    cannot be interpreted as a numeric segment count.
    """
    stripped = value.strip()
    # Try extracting a leading integer
    digits = ""
    for ch in stripped:
        if ch.isdigit():
            digits += ch
        else:
            break
    if digits:
        n = int(digits)
        if n > 0:
            return n
    return None


def _parse_fade_duration(value: str) -> float | None:
    """Parse a fade duration in seconds from outro_fade_up_during.

    Accepts plain numbers (e.g. "5", "3.5") or descriptive text with a leading
    number. Returns None when the value cannot be interpreted numerically.
    """
    stripped = value.strip()
    # Try parsing as a plain float first
    try:
        secs = float(stripped)
        if secs > 0:
            return secs
    except ValueError:
        pass
    # Try extracting a leading number
    num_str = ""
    for ch in stripped:
        if ch.isdigit() or ch == ".":
            num_str += ch
        else:
            break
    if num_str:
        try:
            secs = float(num_str)
            if secs > 0:
                return secs
        except ValueError:
            pass
    return None
