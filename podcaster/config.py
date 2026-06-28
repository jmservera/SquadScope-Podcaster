from __future__ import annotations

import logging
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Mapping

logger = logging.getLogger(__name__)
MAX_SPOTIFY_TITLE_CHARS = 200
MAX_SPOTIFY_DESCRIPTION_CHARS = 4_000
DEFAULT_SPOTIFY_UPLOAD_FORMAT = "mp3"
_SPOTIFY_UPLOAD_FORMATS = frozenset({"wav", "mp3"})
_VOID_HTML_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


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
    return HostConfig(
        name=defaults.HOST_A_NAME, voice=defaults.HOST_A_VOICE, style=defaults.HOST_A_STYLE
    )


def _default_host_b() -> "HostConfig":
    defaults = _generation_defaults()
    return HostConfig(
        name=defaults.HOST_B_NAME, voice=defaults.HOST_B_VOICE, style=defaults.HOST_B_STYLE
    )


def _string_or_default(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _has_text(value: object) -> bool:
    """Return ``True`` when *value* is a non-empty/non-whitespace string."""
    return isinstance(value, str) and bool(value.strip())


def _host_has_name(value: object) -> bool:
    """Return ``True`` when a host block carries a usable (non-blank) name.

    Accepts either a mapping with a ``name``/``host`` field or a bare string
    name.  An empty ``{"name": ""}`` block does not count as supplied identity
    (it would fall back to the default name), so it must not mask the
    absence warning (issue #545).
    """
    if _has_text(value):
        return True
    if isinstance(value, Mapping):
        return _has_text(value.get("name")) or _has_text(value.get("host"))
    return False


class _HTMLTruncator(HTMLParser):
    def __init__(self, max_length: int) -> None:
        super().__init__(convert_charrefs=False)
        self.max_length = max_length
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.current_length = 0
        self.truncated = False

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        self._append_tag(self.get_starttag_text(), tag, push=True)

    def handle_startendtag(self, tag: str, attrs) -> None:  # type: ignore[override]
        self._append_tag(self.get_starttag_text(), tag, push=False)

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        normalized = tag.lower()
        if self.truncated or normalized not in self.open_tags:
            return

        closings: list[str] = []
        while self.open_tags:
            open_tag = self.open_tags.pop()
            closings.append(f"</{open_tag}>")
            if open_tag == normalized:
                break

        for closing in closings:
            self._append(closing)

    def handle_data(self, data: str) -> None:
        self._append_text(data)

    def handle_entityref(self, name: str) -> None:
        self._append_text(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._append_text(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._append_text(f"<!--{data}-->")

    def _append_tag(self, raw_tag: str | None, tag: str, *, push: bool) -> None:
        if self.truncated or not raw_tag:
            return

        normalized = tag.lower()
        budget = self._closing_budget(extra_tag=normalized if push else None)
        if self.current_length + len(raw_tag) + budget > self.max_length:
            self.truncated = True
            return

        self._append(raw_tag)
        if push and normalized not in _VOID_HTML_TAGS:
            self.open_tags.append(normalized)

    def _append_text(self, text: str) -> None:
        if self.truncated or not text:
            return

        available = self.max_length - self.current_length - self._closing_budget()
        if available <= 0:
            self.truncated = True
            return

        piece = text[:available]
        if piece:
            self._append(piece)
        if len(piece) < len(text):
            self.truncated = True

    def _closing_budget(self, *, extra_tag: str | None = None) -> int:
        budget = sum(len(f"</{tag}>") for tag in self.open_tags)
        if extra_tag and extra_tag not in _VOID_HTML_TAGS:
            budget += len(f"</{extra_tag}>")
        return budget

    def _append(self, text: str) -> None:
        self.parts.append(text)
        self.current_length += len(text)

    def finish(self) -> str:
        for tag in reversed(self.open_tags):
            self._append(f"</{tag}>")
        return "".join(self.parts)


def truncate_html(html_str: str, max_length: int) -> str:
    if len(html_str) <= max_length:
        return html_str

    truncator = _HTMLTruncator(max_length)
    truncator.feed(html_str)
    truncator.close()
    return truncator.finish()


@dataclass(frozen=True)
class HostConfig:
    name: str
    voice: str
    style: str


# Default language is the existing English show. Spanish (es-419, Latin-American)
# and French (fr-FR) are the first localized targets for the multilanguage epic
# (jmservera/SquadScope-Coordinator#27). The voice IDs are the bakeoff-selected
# Azure multilingual neural pairs from #436; locales are intentionally specific
# (es-419 vs es-ES) so downstream selection can branch on them.
DEFAULT_LANGUAGE = "en"

_DEFAULT_LOCALES: dict[str, str] = {
    "en": "en-US",
    "es": "es-419",
    "fr": "fr-FR",
}

# Bakeoff-selected native host-pair voices (#436). es-419 has no dedicated Azure
# voice; es-MX is the agreed Latin-American proxy.
_DEFAULT_LANGUAGE_VOICES: dict[str, tuple[str, str]] = {
    "es": ("es-MX-JorgeMultilingualNeural", "es-MX-DaliaMultilingualNeural"),
    "fr": ("fr-FR-RemyMultilingualNeural", "fr-FR-VivienneMultilingualNeural"),
}

# Native-language AI-voice disclosure and closing CTA. Brand ("Claracle") and
# spoken site stay universal; only functional strings are localized.
_DEFAULT_DISCLOSURES: dict[str, str] = {
    "es": (
        "Las dos voces de este programa son generadas por inteligencia "
        "artificial, no son presentadores humanos."
    ),
    "fr": (
        "Les deux voix de cette émission sont générées par intelligence "
        "artificielle, ce ne sont pas des présentateurs humains."
    ),
}

_DEFAULT_CTAS: dict[str, str] = {
    "en": "Read more at www.claracle.com",
    "es": "Lee más en www.claracle.com",
    "fr": "Pour en savoir plus, rendez-vous sur www.claracle.com",
}


@dataclass(frozen=True)
class LanguageConfig:
    """Per-language configuration block for the multilanguage pipeline (#432).

    Drives language fan-out: each entry carries its own locale, host voice pair,
    script-generation prompt overrides, AI-voice disclosure, and closing CTA. The
    brand name stays universal; only functional/English strings are externalized.
    """

    language: str
    locale: str
    show_name: str
    host_a: HostConfig
    host_b: HostConfig
    prompts: Mapping[str, str] = field(default_factory=dict)
    disclosure: str = ""
    cta: str = ""
    enabled: bool = True

    @classmethod
    def default_for(cls, language: str) -> "LanguageConfig":
        """Documented per-locale defaults for a known language code."""

        gen = _generation_defaults()
        locale = _DEFAULT_LOCALES.get(language, language)
        voices = _DEFAULT_LANGUAGE_VOICES.get(language)
        if voices is None:
            host_a = HostConfig(gen.HOST_A_NAME, gen.HOST_A_VOICE, gen.HOST_A_STYLE)
            host_b = HostConfig(gen.HOST_B_NAME, gen.HOST_B_VOICE, gen.HOST_B_STYLE)
        else:
            host_a = HostConfig(gen.HOST_A_NAME, voices[0], gen.HOST_A_STYLE)
            host_b = HostConfig(gen.HOST_B_NAME, voices[1], gen.HOST_B_STYLE)
        return cls(
            language=language,
            locale=locale,
            show_name=gen.PODCAST_NAME,
            host_a=host_a,
            host_b=host_b,
            prompts={},
            disclosure=_DEFAULT_DISCLOSURES.get(language, gen.AI_VOICE_DISCLOSURE),
            cta=_DEFAULT_CTAS.get(language, _DEFAULT_CTAS["en"]),
            enabled=True,
        )

    @classmethod
    def from_payload(cls, language: str, payload: object) -> "LanguageConfig":
        """Build a language block, falling back to documented defaults per field."""

        defaults = cls.default_for(language)
        if not isinstance(payload, Mapping):
            return defaults

        hosts_array = payload.get("hosts")
        host_a_payload = payload.get("host_a")
        host_b_payload = payload.get("host_b")
        if isinstance(hosts_array, (list, tuple)) and len(hosts_array) >= 1:
            if host_a_payload is None:
                host_a_payload = hosts_array[0]
            if host_b_payload is None and len(hosts_array) >= 2:
                host_b_payload = hosts_array[1]

        # A dedicated "voices" map ({"host_a": id, "host_b": id}) overrides voices
        # without restating the full host blocks.
        voices = payload.get("voices")
        if isinstance(voices, Mapping):
            host_a_payload = _merge_voice(host_a_payload, voices.get("host_a"))
            host_b_payload = _merge_voice(host_b_payload, voices.get("host_b"))

        prompts_payload = payload.get("prompts")
        prompts = (
            {
                str(k): v.strip()
                for k, v in prompts_payload.items()
                if isinstance(v, str) and v.strip()
            }
            if isinstance(prompts_payload, Mapping)
            else dict(defaults.prompts)
        )

        enabled = payload.get("enabled")
        return cls(
            language=language,
            locale=_string_or_default(payload.get("locale"), defaults.locale),
            show_name=_string_or_default(
                payload.get("show_name") or payload.get("showName"), defaults.show_name
            ),
            host_a=_host_from_payload(host_a_payload, defaults.host_a),
            host_b=_host_from_payload(host_b_payload, defaults.host_b),
            prompts=prompts,
            disclosure=_string_or_default(payload.get("disclosure"), defaults.disclosure),
            cta=_string_or_default(payload.get("cta"), defaults.cta),
            enabled=enabled if isinstance(enabled, bool) else defaults.enabled,
        )


def _merge_voice(host_payload: object, voice: object) -> object:
    """Overlay a voice id onto a host payload mapping."""

    if not (isinstance(voice, str) and voice.strip()):
        return host_payload
    base = dict(host_payload) if isinstance(host_payload, Mapping) else {}
    base["voice"] = voice.strip()
    return base


def _default_languages() -> dict[str, LanguageConfig]:
    return {code: LanguageConfig.default_for(code) for code in _DEFAULT_LOCALES}


def validate_language_block(language: str, payload: object) -> None:
    """Raise ``ValueError`` if a language block is malformed.

    Lenient parsing (``from_payload``) silently falls back to defaults; this
    strict check is for API ingest where a malformed block should be rejected.
    """

    if not isinstance(language, str) or not language.strip():
        raise ValueError("language code must be a non-empty string")
    if not isinstance(payload, Mapping):
        raise ValueError(f"language block {language!r} must be an object")

    locale = payload.get("locale")
    if locale is not None and (not isinstance(locale, str) or not locale.strip()):
        raise ValueError(f"language {language!r}: locale must be a non-empty string")

    for key in ("host_a", "host_b"):
        host = payload.get(key)
        if host is not None and not isinstance(host, Mapping):
            raise ValueError(f"language {language!r}: {key} must be an object")

    hosts = payload.get("hosts")
    if hosts is not None:
        if not isinstance(hosts, (list, tuple)):
            raise ValueError(f"language {language!r}: hosts must be an array")
        for i, entry in enumerate(hosts):
            if not isinstance(entry, Mapping):
                raise ValueError(f"language {language!r}: hosts[{i}] must be an object")

    voices = payload.get("voices")
    if voices is not None:
        if not isinstance(voices, Mapping):
            raise ValueError(f"language {language!r}: voices must be an object")
        for vkey in ("host_a", "host_b"):
            vval = voices.get(vkey)
            if vval is not None and (not isinstance(vval, str) or not vval.strip()):
                raise ValueError(f"language {language!r}: voices.{vkey} must be a non-empty string")

    prompts = payload.get("prompts")
    if prompts is not None:
        if not isinstance(prompts, Mapping):
            raise ValueError(f"language {language!r}: prompts must be an object")
        for pkey, pval in prompts.items():
            if not isinstance(pval, str) or not pval.strip():
                raise ValueError(
                    f"language {language!r}: prompts[{pkey!r}] must be a non-empty string"
                )

    for key in ("show_name", "showName", "disclosure", "cta"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"language {language!r}: {key} must be a string")

    enabled = payload.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError(f"language {language!r}: enabled must be a boolean")


@dataclass(frozen=True)
class PodcastConfig:
    name: str = field(default_factory=_default_name)
    url: str = field(default_factory=_default_url)
    spoken_site: str = field(default_factory=_default_spoken_site)
    ai_voice_disclosure: str = field(default_factory=_default_disclosure)
    host_a: HostConfig = field(default_factory=_default_host_a)
    host_b: HostConfig = field(default_factory=_default_host_b)
    style_guide: str = ""
    languages: Mapping[str, LanguageConfig] = field(default_factory=_default_languages)

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

        languages = dict(defaults.languages)
        languages_payload = config_payload.get("languages")
        if isinstance(languages_payload, Mapping):
            for code, block in languages_payload.items():
                key = str(code).strip()
                if not key:
                    continue
                languages[key] = LanguageConfig.from_payload(key, block)

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
            languages=languages,
        )

    @classmethod
    def payload_provides_identity(cls, payload: Mapping[str, Any] | None) -> bool:
        """Return ``True`` when *payload* explicitly supplies podcast identity.

        "Identity" means the show name or either host (the fields that surface in
        the spoken script and on-screen credits).  When this returns ``False``
        the resolved config falls back to the module defaults, and callers should
        log that the configuration was genuinely absent (issue #545).
        """

        if not isinstance(payload, Mapping):
            return False
        config_payload: object = payload
        if "podcast_config" in payload:
            config_payload = payload.get("podcast_config")
        if not isinstance(config_payload, Mapping):
            return False
        # A bare show name counts as identity.
        if _has_text(config_payload.get("name")):
            return True
        # A host block counts only when it carries a usable name; an empty
        # ``{"name": ""}`` would otherwise fall back to the default name yet be
        # treated as "provided", masking the absence warning (issue #545).
        for key in ("host_a", "host_b"):
            if _host_has_name(config_payload.get(key)):
                return True
        hosts = config_payload.get("hosts")
        if isinstance(hosts, (list, tuple)) and any(_host_has_name(h) for h in hosts):
            return True
        return False

    def language_for(self, code: str | None) -> LanguageConfig:
        """Return the language block for ``code``, falling back to the default.

        Accepts a bare language code (``"es"``) or a full locale (``"es-419"``);
        unknown codes fall back to the English default block.
        """

        if isinstance(code, str) and code.strip():
            requested = code.strip()
            if requested in self.languages:
                return self.languages[requested]
            short = requested.split("-", 1)[0]
            if short in self.languages:
                return self.languages[short]
            for block in self.languages.values():
                if block.locale == requested:
                    return block
        if DEFAULT_LANGUAGE in self.languages:
            return self.languages[DEFAULT_LANGUAGE]
        return LanguageConfig.default_for(DEFAULT_LANGUAGE)


@dataclass(frozen=True)
class SpotifyPublishConfig:
    title: str = ""
    description: str = ""
    season_number: str | int = "{year}"
    episode_number: str | int = "{week}"
    publish_mode: str = "draft"
    upload_format: str = DEFAULT_SPOTIFY_UPLOAD_FORMAT

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "title", _truncate_with_warning("title", self.title, MAX_SPOTIFY_TITLE_CHARS)
        )
        object.__setattr__(
            self,
            "description",
            _truncate_html_with_warning(
                "description", self.description, MAX_SPOTIFY_DESCRIPTION_CHARS
            ),
        )
        object.__setattr__(self, "upload_format", _normalize_upload_format(self.upload_format))

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
            title=_string_or_default(config_payload.get("title"), defaults.title),
            description=_string_or_default(config_payload.get("description"), defaults.description),
            season_number=_string_or_int_or_default(
                config_payload.get("season_number"), defaults.season_number
            ),
            episode_number=_string_or_int_or_default(
                config_payload.get("episode_number"), defaults.episode_number
            ),
            publish_mode=_string_or_default(
                config_payload.get("publish_mode"), defaults.publish_mode
            ),
            upload_format=_string_or_default(
                config_payload.get("upload_format"), defaults.upload_format
            ),
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


def _truncate_with_warning(field_name: str, value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    logger.warning("Spotify publish %s exceeded %d chars; truncating.", field_name, limit)
    return value[:limit]


def _truncate_html_with_warning(field_name: str, value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    logger.warning("Spotify publish %s exceeded %d chars; truncating.", field_name, limit)
    return truncate_html(value, limit)


def _normalize_upload_format(value: str) -> str:
    normalized = str(value or DEFAULT_SPOTIFY_UPLOAD_FORMAT).strip().lower()
    if normalized in _SPOTIFY_UPLOAD_FORMATS:
        return normalized
    logger.warning(
        "Spotify publish upload_format %r is unsupported; defaulting to %s.",
        value,
        DEFAULT_SPOTIFY_UPLOAD_FORMAT,
    )
    return DEFAULT_SPOTIFY_UPLOAD_FORMAT


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
class HistoricalContext:
    """Caller-provided continuity hints for multi-episode script generation."""

    summary: str = ""
    month_synthesis: str = ""
    yearly_narrative: str = ""
    prior_episode_themes: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, data: object) -> "HistoricalContext":
        if isinstance(data, str):
            return cls(summary=_safe_str(data))
        if not isinstance(data, Mapping):
            return cls()

        prior_episode_themes_raw = data.get("prior_episode_themes")
        prior_episode_themes: tuple[str, ...] = ()
        if isinstance(prior_episode_themes_raw, str):
            theme = _safe_str(prior_episode_themes_raw)
            prior_episode_themes = (theme,) if theme else ()
        elif isinstance(prior_episode_themes_raw, (list, tuple)):
            prior_episode_themes = tuple(
                theme for item in prior_episode_themes_raw if (theme := _safe_str(item))
            )

        return cls(
            summary=_safe_str(data.get("summary")) or _safe_str(data.get("text")),
            month_synthesis=_safe_str(data.get("month_synthesis")),
            yearly_narrative=_safe_str(data.get("yearly_narrative")),
            prior_episode_themes=prior_episode_themes,
        )

    @property
    def has_content(self) -> bool:
        return bool(
            (self.summary and self.summary.strip())
            or (self.month_synthesis and self.month_synthesis.strip())
            or (self.yearly_narrative and self.yearly_narrative.strip())
            or self.prior_episode_themes
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
    historical_context: HistoricalContext = field(default_factory=HistoricalContext)
    backchannels: BackchannelConfig = field(default_factory=lambda: BackchannelConfig())

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "ScriptDirections":
        """Parse ``script_directions`` from the top-level API payload."""

        if not isinstance(payload, Mapping):
            return cls()
        backchannels = BackchannelConfig.from_payload(payload)
        sd = payload.get("script_directions")
        if not isinstance(sd, Mapping):
            return cls(backchannels=backchannels)

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
            historical_context=HistoricalContext.from_value(sd.get("historical_context")),
            backchannels=backchannels,
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
            or self.historical_context.has_content
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


# --- Backchannels (Natural audio, issue #419 Phase A) ---


# TTS-safe backchannel library (issue #419). Pre-recorded clips ("mm-hmm",
# "uh-huh", laughter) are future/optional work and intentionally excluded here.
BACKCHANNEL_LIBRARY: tuple[str, ...] = (
    "right",
    "yeah",
    "yes",
    "mhm",
    "exactly",
    "interesting",
    "oh wow",
    "hmm",
    "that's true",
)

# Allowed mixing gain window for backchannels (dB under the main speaker).
# The ceiling was raised from -14 to -10 dB (issue #560): on the W26 render the
# reactions fired but were too quiet to register. -10 dB is still clearly a
# background voice (well under the main speaker) yet audible. The floor stays at
# -18 dB so a caller can still ask for a very subtle layer.
BACKCHANNEL_GAIN_DB_MAX = -10.0
BACKCHANNEL_GAIN_DB_MIN = -18.0


@dataclass(frozen=True)
class BackchannelConfig:
    """Parsed ``backchannels`` interaction-layer spec from the caller payload.

    Phase A of issue #419 ("Natural audio"). Adds a lightweight interaction
    layer *separate* from the main two-host script: timed backchannel reactions
    ("right", "yeah", "exactly", ...) mixed quietly under the main speaker at
    natural pause points.

    The feature is **disabled by default** (``enabled=False``) so existing
    callers and rendered audio are unchanged until a caller opts in. All fields
    are optional and backward compatible with the config payload.
    """

    enabled: bool = False
    # Density: at most one backchannel per [min_gap, max_gap] window of speech.
    # Tightened from 45-60s (issue #560): the W26 render fired reactions far too
    # rarely, so hums and agreements barely registered. 18-30s lands reactions at
    # natural pauses often enough to read as a live second host while staying
    # below "every pause" saturation. Callers can still widen these.
    min_gap_seconds: float = 18.0
    max_gap_seconds: float = 30.0
    # Mixing gain in dB under the main speaker (clamped to [-18, -10]). Raised
    # from -16 to -12 (issue #560) so reactions are clearly audible as a
    # background voice rather than buried under the main speaker.
    gain_db: float = -12.0
    # Hard cap on a single backchannel clip's duration.
    max_duration_ms: int = 600
    # TTS-safe phrase library (overridable by the caller).
    library: tuple[str, ...] = BACKCHANNEL_LIBRARY

    def __post_init__(self) -> None:
        if self.min_gap_seconds < 0:
            raise ValueError("min_gap_seconds must be non-negative")
        if self.max_gap_seconds < self.min_gap_seconds:
            raise ValueError("max_gap_seconds must be >= min_gap_seconds")
        if self.max_duration_ms <= 0:
            raise ValueError("max_duration_ms must be positive")
        if not self.library:
            raise ValueError("backchannel library must not be empty")

    @property
    def clamped_gain_db(self) -> float:
        """Gain clamped to the documented [-18, -10] dB window."""
        return max(BACKCHANNEL_GAIN_DB_MIN, min(BACKCHANNEL_GAIN_DB_MAX, self.gain_db))

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "BackchannelConfig":
        """Parse ``backchannels`` (top level or nested under ``script_directions``)."""

        if not isinstance(payload, Mapping):
            return cls()

        bc = payload.get("backchannels")
        if not isinstance(bc, Mapping):
            sd = payload.get("script_directions")
            if isinstance(sd, Mapping):
                bc = sd.get("backchannels")
            if not isinstance(bc, Mapping):
                return cls()

        defaults = cls()
        library_raw = bc.get("library")
        library = defaults.library
        if isinstance(library_raw, (list, tuple)):
            phrases = tuple(p for item in library_raw if (p := _safe_str(item)))
            if phrases:
                library = phrases

        return cls(
            enabled=bool(bc.get("enabled", defaults.enabled)),
            min_gap_seconds=_safe_float(bc.get("min_gap_seconds"), defaults.min_gap_seconds),
            max_gap_seconds=_safe_float(bc.get("max_gap_seconds"), defaults.max_gap_seconds),
            gain_db=_safe_float(bc.get("gain_db"), defaults.gain_db),
            max_duration_ms=int(_safe_float(bc.get("max_duration_ms"), defaults.max_duration_ms)),
            library=library,
        )


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
