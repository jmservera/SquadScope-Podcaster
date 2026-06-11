from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


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
