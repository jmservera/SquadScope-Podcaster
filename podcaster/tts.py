"""Azure OpenAI two-voice TTS wiring for the production /api/generate path (#60).

This module is intentionally *dry-run-safe and publication-blocked*:

* It only describes and authorizes real text-to-speech synthesis; it never
  synthesizes audio unless an explicit gating decision allows it
  (production config present, not a dry run, and recorded human review).
* It authenticates with the ACA managed identity using the same
  IMDS-based token pattern as :mod:`podcaster.storage`, so the runtime package
  stays minimal and account keys are never read, logged, or required.
* It never logs tokens, account keys, full endpoints, or untrusted script text.

The voices follow the operator decision in #60: ``fable`` for host A and
``alloy`` for host B of the Claracle conversation. The endpoint, deployments,
and voices are read from the Container App environment variables emitted by
``infra`` (see ``infra/main.bicep`` and ``infra/modules/aca.bicep``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from podcaster.storage import ManagedIdentityTokenCredential

PROVIDER = "openai-tts"
AUTH_MODE_MANAGED_IDENTITY = "managed_identity"
OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"
DEFAULT_API_VERSION = "2024-12-01-preview"
DEFAULT_RESPONSE_FORMAT = "wav"
_RESPONSE_CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
}

HOST_A_ROLE = "host_a"
HOST_B_ROLE = "host_b"

# Map free-form speaker labels parsed from a script onto the two configured hosts.
_HOST_A_LABELS = frozenset({"host_a", "hosta", "host a", "a", "fable", "narrator", "host"})
_HOST_B_LABELS = frozenset({"host_b", "hostb", "host b", "b", "alloy", "guest", "cohost", "co-host"})

TokenProvider = Callable[[str], str]
Transport = Callable[[Request], bytes]


@dataclass(frozen=True)
class TtsConfig:
    """Resolved Azure OpenAI TTS configuration from Container App settings."""

    endpoint: str | None
    tts_deployment: str | None
    chat_deployment: str | None
    voice_host_a: str | None
    voice_host_b: str | None
    auth_mode: str | None
    api_version: str = DEFAULT_API_VERSION
    style_host_a: str | None = None
    style_host_b: str | None = None
    response_format: str = DEFAULT_RESPONSE_FORMAT

    @property
    def production_ready(self) -> bool:
        """True only when every setting required for managed-identity synthesis is present."""

        return bool(
            self.endpoint
            and self.tts_deployment
            and self.voice_host_a
            and self.voice_host_b
            and self.auth_mode == AUTH_MODE_MANAGED_IDENTITY
        )

    @property
    def endpoint_host(self) -> str | None:
        """Host portion of the endpoint, safe to log; never exposes path or query."""

        if not self.endpoint:
            return None
        return urlsplit(self.endpoint).netloc or None

    def voice_for(self, role: str) -> str | None:
        """Return the configured voice for a normalized speaker role."""

        normalized = _normalize_role(role)
        if normalized == HOST_B_ROLE:
            return self.voice_host_b
        return self.voice_host_a

    def style_for(self, role: str) -> str | None:
        """Return the optional TTS style instruction for a speaker role."""

        normalized = _normalize_role(role)
        if normalized == HOST_B_ROLE:
            return self.style_host_b
        return self.style_host_a

    @property
    def audio_extension(self) -> str:
        return f".{self.response_format}"

    @property
    def audio_content_type(self) -> str:
        return _RESPONSE_CONTENT_TYPES.get(self.response_format, "application/octet-stream")

    def safe_summary(self) -> dict[str, object]:
        """Secret-safe configuration summary for manifests/logs.

        Only booleans, deployment aliases, voice names, and the endpoint host
        are exposed; the full endpoint URL, tokens, and account keys are not.
        """

        return {
            "provider": PROVIDER,
            "auth_mode": self.auth_mode,
            "production_ready": self.production_ready,
            "endpoint_configured": bool(self.endpoint),
            "endpoint_host": self.endpoint_host,
            "tts_deployment": self.tts_deployment,
            "chat_deployment": self.chat_deployment,
            "api_version": self.api_version,
            "response_format": self.response_format,
            "voices": {
                HOST_A_ROLE: self.voice_host_a,
                HOST_B_ROLE: self.voice_host_b,
            },
            "styles_configured": {
                HOST_A_ROLE: bool(self.style_host_a),
                HOST_B_ROLE: bool(self.style_host_b),
            },
        }


@dataclass(frozen=True)
class VoiceTurn:
    """A single synthesizable conversation turn assigned to one host voice."""

    role: str
    voice: str
    deployment: str
    text: str
    style: str | None = None


def load_tts_config(env: Mapping[str, str] | None = None) -> TtsConfig:
    """Read the Azure OpenAI TTS configuration from the environment.

    Defaults to :data:`os.environ`. Missing values resolve to ``None`` so the
    placeholder/dry-run path keeps working when ``deployOpenAi=false``.
    """

    if env is None:
        import os

        env = os.environ

    return TtsConfig(
        endpoint=_clean(env.get("AZURE_OPENAI_ENDPOINT")),
        tts_deployment=_clean(env.get("AZURE_OPENAI_TTS_DEPLOYMENT")),
        chat_deployment=_clean(env.get("AZURE_OPENAI_CHAT_DEPLOYMENT")),
        voice_host_a=_clean(env.get("AZURE_OPENAI_TTS_VOICE_HOST_A")),
        voice_host_b=_clean(env.get("AZURE_OPENAI_TTS_VOICE_HOST_B")),
        auth_mode=_clean(env.get("AZURE_OPENAI_AUTH_MODE")),
        api_version=_clean(env.get("AZURE_OPENAI_TTS_API_VERSION")) or DEFAULT_API_VERSION,
        style_host_a=_clean(env.get("AZURE_OPENAI_TTS_STYLE_HOST_A")),
        style_host_b=_clean(env.get("AZURE_OPENAI_TTS_STYLE_HOST_B")),
        response_format=_response_format(_clean(env.get("AZURE_OPENAI_TTS_FORMAT"))),
    )


def synthesis_decision(
    config: TtsConfig,
    *,
    dry_run: bool,
    review_approved: bool,
) -> dict[str, object]:
    """Decide whether real two-voice synthesis is authorized.

    Synthesis is allowed only when the production config is present, the request
    is not a dry run, and human editorial review has been recorded. Otherwise it
    returns the blocking reasons so callers stay publication-blocked.
    """

    blocked_by: list[str] = []
    if not config.production_ready:
        blocked_by.append("openai_tts_not_configured")
    if dry_run:
        blocked_by.append("dry_run")
    if not review_approved:
        blocked_by.append("human_review")

    allowed = not blocked_by
    return {
        "provider": PROVIDER,
        "auth_mode": AUTH_MODE_MANAGED_IDENTITY,
        "allowed": allowed,
        "status": "allowed" if allowed else "blocked",
        "blocked_by": sorted(set(blocked_by)),
        "endpoint_configured": bool(config.endpoint),
        "voices": {
            HOST_A_ROLE: config.voice_host_a,
            HOST_B_ROLE: config.voice_host_b,
        },
    }


def build_voice_plan(
    segments: list[tuple[str, str]],
    config: TtsConfig,
) -> list[VoiceTurn]:
    """Assign each ``(speaker_label, text)`` segment to a host voice.

    Raises :class:`ValueError` when the config lacks voices/deployment or when
    no segments are supplied, so a misconfigured request fails closed rather
    than silently producing single-voice audio.
    """

    if not config.voice_host_a or not config.voice_host_b or not config.tts_deployment:
        raise ValueError("two-voice plan requires tts deployment and both host voices configured")
    if not segments:
        raise ValueError("voice plan requires at least one speaker-labelled segment")

    plan: list[VoiceTurn] = []
    for label, text in segments:
        normalized = _normalize_role(label)
        voice = config.voice_host_b if normalized == HOST_B_ROLE else config.voice_host_a
        role = HOST_B_ROLE if normalized == HOST_B_ROLE else HOST_A_ROLE
        plan.append(
            VoiceTurn(
                role=role,
                voice=str(voice),
                deployment=config.tts_deployment,
                text=text,
                style=config.style_for(role),
            )
        )
    return plan


def synthesize_turn(
    turn: VoiceTurn,
    config: TtsConfig,
    *,
    token_provider: TokenProvider | None = None,
    transport: Transport | None = None,
) -> bytes:
    """Synthesize a single turn to audio bytes using the managed identity.

    Network and credential access are injectable for testing. This function
    never logs the bearer token, the full endpoint URL, or the (untrusted)
    input text; only the deployment, voice, and character count are logged.
    """

    if not config.endpoint or not config.tts_deployment:
        raise ValueError("cannot synthesize without an endpoint and tts deployment")

    token_provider = token_provider or ManagedIdentityTokenCredential().get_token
    transport = transport or _default_transport

    token = token_provider(OPENAI_SCOPE)
    if not token:
        raise RuntimeError("managed identity returned an empty token for Azure OpenAI")

    base = config.endpoint if config.endpoint.endswith("/") else f"{config.endpoint}/"
    url = f"{base}openai/deployments/{config.tts_deployment}/audio/speech?api-version={config.api_version}"

    def _request(include_style: bool) -> Request:
        payload: dict[str, object] = {
            "model": config.tts_deployment,
            "input": turn.text,
            "voice": turn.voice,
            "response_format": config.response_format,
        }
        # The ``instructions`` field steers tone/style on newer speech models.
        # Older models reject it, so we retry without it on failure (below).
        if include_style and turn.style:
            payload["instructions"] = turn.style
        return Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

    has_style = bool(turn.style)
    logging.info(
        "synthesizing tts turn deployment=%s voice=%s input_chars=%s styled=%s format=%s",
        config.tts_deployment,
        turn.voice,
        len(turn.text),
        has_style,
        config.response_format,
    )
    try:
        audio = transport(_request(include_style=has_style))
    except Exception as exc:  # noqa: BLE001 - degrade gracefully if style unsupported
        if not has_style:
            raise
        logging.warning(
            "tts style instructions rejected (deployment=%s voice=%s); retrying without style: %s",
            config.tts_deployment,
            turn.voice,
            type(exc).__name__,
        )
        audio = transport(_request(include_style=False))
    if not isinstance(audio, bytes) or not audio:
        raise RuntimeError("tts synthesis returned empty audio")
    return audio


def synthesize_two_voice(
    plan: list[VoiceTurn],
    config: TtsConfig,
    decision: Mapping[str, object],
    *,
    token_provider: TokenProvider | None = None,
    transport: Transport | None = None,
) -> list[bytes]:
    """Synthesize every turn, but only when the gating decision allows it.

    Fails closed: if ``decision['allowed']`` is not truthy the call raises
    :class:`PermissionError`, so synthesis can never run for a dry run, an
    unconfigured environment, or an unreviewed episode.
    """

    if not decision.get("allowed"):
        blocked_by = decision.get("blocked_by") or ["not_authorized"]
        raise PermissionError(f"tts synthesis is blocked: {', '.join(map(str, blocked_by))}")
    if not plan:
        raise ValueError("voice plan is empty")
    return [
        synthesize_turn(turn, config, token_provider=token_provider, transport=transport)
        for turn in plan
    ]


def _default_transport(request: Request) -> bytes:
    with urlopen(request, timeout=60) as response:
        return response.read()


def _normalize_role(role: str) -> str:
    key = str(role or "").strip().lower().replace("-", "_")
    if key in _HOST_B_LABELS or key.replace(" ", "_") in _HOST_B_LABELS:
        return HOST_B_ROLE
    if key in _HOST_A_LABELS or key.replace(" ", "_") in _HOST_A_LABELS:
        return HOST_A_ROLE
    return HOST_A_ROLE


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _response_format(value: str | None) -> str:
    normalized = (value or DEFAULT_RESPONSE_FORMAT).strip().lower()
    if normalized in _RESPONSE_CONTENT_TYPES:
        return normalized
    logging.warning(
        "Unsupported AZURE_OPENAI_TTS_FORMAT=%r; defaulting to %s.",
        value,
        DEFAULT_RESPONSE_FORMAT,
    )
    return DEFAULT_RESPONSE_FORMAT
