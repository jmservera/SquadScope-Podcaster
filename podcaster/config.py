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

        # Support "hosts" array format: first element → host_a, second → host_b
        hosts_array = config_payload.get("hosts")
        if isinstance(hosts_array, (list, tuple)) and len(hosts_array) >= 1:
            if host_a_payload is None:
                host_a_payload = hosts_array[0]
            if host_b_payload is None and len(hosts_array) >= 2:
                host_b_payload = hosts_array[1]

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


@dataclass(frozen=True)
class SpotifyPublishConfig:
    title_template: str = "{article_title}"
    description_template: str = "<p>{article_summary}</p>"
    season_number: str | int = "{year}"
    episode_number: str | int = "{week}"
    publish_mode: str = "draft"

    @classmethod
    def from_payload(cls, data: Mapping[str, Any] | None) -> "SpotifyPublishConfig | None":
        """Build from an optional ``spotify_publish`` payload object.

        Returns None when the payload does not contain a ``spotify_publish``
        section, preserving the caller's existing publish behaviour (immediate).
        """

        if data is None:
            return None

        config_payload: object = data.get("spotify_publish") if "spotify_publish" in data else None

        if not isinstance(config_payload, Mapping):
            return None

        defaults = cls()
        return cls(
            title_template=_string_or_default(config_payload.get("title_template"), defaults.title_template),
            description_template=_string_or_default(
                config_payload.get("description_template"), defaults.description_template
            ),
            season_number=_string_or_int_or_default(config_payload.get("season_number"), defaults.season_number),
            episode_number=_string_or_int_or_default(
                config_payload.get("episode_number"), defaults.episode_number
            ),
            publish_mode=_string_or_default(config_payload.get("publish_mode"), defaults.publish_mode),
        )

    def resolve_title(self, year: int, week: int, article_title: str) -> str:
        return self.title_template.format(year=year, week=week, article_title=article_title)

    def resolve_description(self, year: int, week: int, article_title: str, article_summary: str) -> str:
        return self.description_template.format(
            year=year, week=week, article_title=article_title, article_summary=article_summary
        )

    def resolve_season(self, year: int, week: int) -> int:
        val = self.season_number
        if isinstance(val, int):
            return val
        return int(str(val).format(year=year, week=week))

    def resolve_episode(self, year: int, week: int) -> int:
        val = self.episode_number
        if isinstance(val, int):
            return val
        return int(str(val).format(year=year, week=week))


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
    """LLM-facing episode structure/tone constraints from caller config."""

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
    """Parsed ``script_directions`` from the caller payload.

    Guides LLM script generation: episode structure, opening/closing cues, tone.
    All fields are optional; absent values do not change the default prompt.
    """

    episode_style: EpisodeStyle = field(default_factory=EpisodeStyle)
    show_intro: str = ""
    cold_open: str = ""
    ai_disclosure_cue: str = ""
    corrections_path: str = ""
    source_article_link: str = ""

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
            show_intro=_safe_str(opening.get("show_intro")),
            cold_open=_safe_str(opening.get("cold_open")),
            ai_disclosure_cue=_safe_str(opening.get("ai_disclosure")),
            corrections_path=_safe_str(closing.get("corrections_path")),
            source_article_link=_safe_str(closing.get("source_article_link")),
        )

    @property
    def has_content(self) -> bool:
        """True if any field carries usable content."""
        return bool(
            self.episode_style.format
            or self.episode_style.tone
            or self.episode_style.segment_order
            or self.show_intro
            or self.cold_open
            or self.ai_disclosure_cue
            or self.corrections_path
            or self.source_article_link
        )


# --- Music Mix ---


@dataclass(frozen=True)
class MusicMixConfig:
    """Parsed ``music_mix`` spec from the caller payload.

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
        # outro_start_position is a time string like "1:15" → convert to seconds
        if self.outro_start_position:
            kwargs["outro_start_offset_seconds"] = _parse_time_offset(self.outro_start_position)
        return kwargs


def _safe_str(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _string_or_int_or_default(value: object, default: str | int) -> str | int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


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
