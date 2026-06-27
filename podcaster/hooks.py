"""LLM-generated conversational hooks for podcast hosts.

Generates short conversational lead-in phrases per host personality using a
single LLM call at episode start. Falls back to neutral generic hooks if the
LLM call fails.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.request import Request

from podcaster.config import PodcastConfig
from podcaster.script_gen import ScriptGenConfig
from podcaster.storage import ManagedIdentityTokenCredential
from podcaster.tts import OPENAI_SCOPE, TokenProvider, Transport

logger = logging.getLogger("podcaster.hooks")

_GENERIC_HOOKS: list[str] = [
    "Let me tell you about...",
    "Here is what caught my eye...",
    "This one is interesting...",
    "So check this out...",
    "What really stands out here...",
    "Here is the thing...",
    "This is worth noting...",
    "I want to highlight...",
    "One thing that jumped out...",
    "Pay attention to this...",
]


@dataclass(frozen=True)
class HostHooks:
    """Conversational hooks for both hosts, cached per episode."""

    host_a: list[str]
    host_b: list[str]


def generate_hooks(
    *,
    config: ScriptGenConfig,
    podcast_config: PodcastConfig | None = None,
    token_provider: TokenProvider | None = None,
    transport: Transport | None = None,
) -> HostHooks:
    """Generate conversational hooks for both hosts via a single LLM call.

    Returns :class:`HostHooks` with ~10 hooks per host. Falls back to generic
    hooks when the LLM is unavailable or the call fails.
    """

    podcast_config = podcast_config or PodcastConfig()

    if not config.ready:
        logger.info("hooks: LLM not configured, using generic fallback")
        return _fallback_hooks()

    try:
        return _call_llm_for_hooks(
            config=config,
            podcast_config=podcast_config,
            token_provider=token_provider,
            transport=transport,
        )
    except Exception as exc:
        logger.warning("hooks: LLM call failed (%s), using generic fallback", exc)
        return _fallback_hooks()


def _fallback_hooks() -> HostHooks:
    return HostHooks(host_a=list(_GENERIC_HOOKS), host_b=list(_GENERIC_HOOKS))


def _call_llm_for_hooks(
    *,
    config: ScriptGenConfig,
    podcast_config: PodcastConfig,
    token_provider: TokenProvider | None,
    transport: Transport | None,
) -> HostHooks:
    """Make a single LLM call to generate hooks for both hosts."""

    from podcaster.script_gen import _default_transport

    token_provider = token_provider or ManagedIdentityTokenCredential().get_token
    transport = transport or _default_transport

    prompt = (
        "Generate conversational lead-in phrases for two podcast hosts.\n\n"
        f"Host A personality: {podcast_config.host_a.style}\n"
        f"Host B personality: {podcast_config.host_b.style}\n\n"
        "For each host, generate exactly 10 short conversational lead-in phrases "
        "(5-10 words each) that this host would naturally use to introduce a topic "
        "or react to a point. Be varied, natural, conversational.\n\n"
        "Respond ONLY with valid JSON in this exact format:\n"
        '{"host_a": ["phrase1", ...], "host_b": ["phrase1", ...]}'
    )

    token = token_provider(OPENAI_SCOPE)
    if not token:
        raise RuntimeError("empty token for hook generation")

    base = config.endpoint if config.endpoint.endswith("/") else f"{config.endpoint}/"
    url = (
        f"{base}openai/deployments/{config.chat_deployment}"
        f"/chat/completions?api-version={config.api_version}"
    )

    payload = {
        "messages": [
            {
                "role": "system",
                "content": "You generate short conversational phrases. Respond only with JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.9,
        "max_tokens": 600,
    }

    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    logger.info("generating conversational hooks deployment=%s", config.chat_deployment)
    raw_response = transport(request)
    response = json.loads(raw_response.decode("utf-8"))

    choices = response.get("choices", [])
    if not choices:
        raise ValueError("LLM returned no choices for hook generation")

    content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        raise ValueError("LLM returned empty hook content")

    # Parse JSON response — handle markdown code fences if present
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    data = json.loads(content)
    host_a_hooks = data.get("host_a", [])
    host_b_hooks = data.get("host_b", [])

    if not isinstance(host_a_hooks, list) or not isinstance(host_b_hooks, list):
        raise ValueError("LLM returned invalid hook format")

    # Ensure we have strings and at least some hooks
    host_a_hooks = [str(h) for h in host_a_hooks if h]
    host_b_hooks = [str(h) for h in host_b_hooks if h]

    if len(host_a_hooks) < 3 or len(host_b_hooks) < 3:
        raise ValueError("LLM returned too few hooks")

    logger.info("hooks generated: host_a=%d, host_b=%d", len(host_a_hooks), len(host_b_hooks))
    return HostHooks(host_a=host_a_hooks, host_b=host_b_hooks)
