"""Tests for podcaster.hooks — LLM-generated conversational hooks."""

from __future__ import annotations

import json
from urllib.request import Request

import pytest

from podcaster.config import PodcastConfig
from podcaster.hooks import (
    HostHooks,
    _GENERIC_HOOKS,
    _fallback_hooks,
    generate_hooks,
)
from podcaster.script_gen import ScriptGenConfig


def _mock_config(ready: bool = True) -> ScriptGenConfig:
    return ScriptGenConfig(
        endpoint="https://test.openai.azure.com/" if ready else None,
        chat_deployment="chat" if ready else None,
        auth_mode="managed_identity" if ready else None,
    )


def _fake_token_provider(scope: str) -> str:
    return "fake-token"


def _make_hooks_transport(host_a_hooks: list[str], host_b_hooks: list[str]):
    """Build a mock transport returning hooks JSON."""

    def transport(request: Request) -> bytes:
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"host_a": host_a_hooks, "host_b": host_b_hooks}),
                    }
                }
            ]
        }
        return json.dumps(response).encode("utf-8")

    return transport


class TestGenerateHooks:
    def test_returns_hooks_from_llm(self):
        a_hooks = [f"Hook A {i}" for i in range(10)]
        b_hooks = [f"Hook B {i}" for i in range(10)]
        transport = _make_hooks_transport(a_hooks, b_hooks)

        result = generate_hooks(
            config=_mock_config(),
            token_provider=_fake_token_provider,
            transport=transport,
        )
        assert isinstance(result, HostHooks)
        assert result.host_a == a_hooks
        assert result.host_b == b_hooks

    def test_fallback_when_not_configured(self):
        result = generate_hooks(config=_mock_config(ready=False))
        assert result.host_a == _GENERIC_HOOKS
        assert result.host_b == _GENERIC_HOOKS

    def test_fallback_on_transport_error(self):
        def bad_transport(request: Request) -> bytes:
            raise ConnectionError("network down")

        result = generate_hooks(
            config=_mock_config(),
            token_provider=_fake_token_provider,
            transport=bad_transport,
        )
        assert result.host_a == _GENERIC_HOOKS
        assert result.host_b == _GENERIC_HOOKS

    def test_fallback_on_invalid_json(self):
        def bad_json_transport(request: Request) -> bytes:
            response = {
                "choices": [{"message": {"role": "assistant", "content": "not json"}}]
            }
            return json.dumps(response).encode("utf-8")

        result = generate_hooks(
            config=_mock_config(),
            token_provider=_fake_token_provider,
            transport=bad_json_transport,
        )
        assert result.host_a == _GENERIC_HOOKS

    def test_fallback_on_too_few_hooks(self):
        transport = _make_hooks_transport(["only one"], ["only one"])
        result = generate_hooks(
            config=_mock_config(),
            token_provider=_fake_token_provider,
            transport=transport,
        )
        assert result.host_a == _GENERIC_HOOKS

    def test_handles_markdown_code_fences(self):
        a_hooks = [f"A phrase {i}" for i in range(10)]
        b_hooks = [f"B phrase {i}" for i in range(10)]
        content = f"```json\n{json.dumps({'host_a': a_hooks, 'host_b': b_hooks})}\n```"

        def fenced_transport(request: Request) -> bytes:
            response = {"choices": [{"message": {"role": "assistant", "content": content}}]}
            return json.dumps(response).encode("utf-8")

        result = generate_hooks(
            config=_mock_config(),
            token_provider=_fake_token_provider,
            transport=fenced_transport,
        )
        assert result.host_a == a_hooks
        assert result.host_b == b_hooks

    def test_uses_podcast_config_style(self):
        """Verify the prompt includes host style from config."""
        captured_requests: list[Request] = []

        def capturing_transport(request: Request) -> bytes:
            captured_requests.append(request)
            a_hooks = [f"Hook {i}" for i in range(10)]
            b_hooks = [f"Hook {i}" for i in range(10)]
            response = {
                "choices": [
                    {"message": {"content": json.dumps({"host_a": a_hooks, "host_b": b_hooks})}}
                ]
            }
            return json.dumps(response).encode("utf-8")

        from podcaster.config import HostConfig

        pc = PodcastConfig(
            host_a=HostConfig(name="Alice", voice="alloy", style="Super peppy and fast"),
            host_b=HostConfig(name="Bob", voice="fable", style="Calm and analytical"),
        )

        generate_hooks(
            config=_mock_config(),
            podcast_config=pc,
            token_provider=_fake_token_provider,
            transport=capturing_transport,
        )

        assert len(captured_requests) == 1
        body = json.loads(captured_requests[0].data)
        user_msg = body["messages"][1]["content"]
        assert "Super peppy and fast" in user_msg
        assert "Calm and analytical" in user_msg


class TestFallbackHooks:
    def test_returns_generic_lists(self):
        result = _fallback_hooks()
        assert len(result.host_a) == len(_GENERIC_HOOKS)
        assert len(result.host_b) == len(_GENERIC_HOOKS)
        # Should be independent copies
        result.host_a.append("extra")
        assert len(_GENERIC_HOOKS) == 10
