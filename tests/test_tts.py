from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcaster.tts import (  # noqa: E402
    AUTH_MODE_MANAGED_IDENTITY,
    HOST_A_ROLE,
    HOST_B_ROLE,
    PROVIDER,
    TtsConfig,
    build_voice_plan,
    load_tts_config,
    synthesis_decision,
    synthesize_turn,
    synthesize_two_voice,
)


def _production_env() -> dict[str, str]:
    return {
        "AZURE_OPENAI_ENDPOINT": "https://podcaster-openai.openai.azure.com/",
        "AZURE_OPENAI_TTS_DEPLOYMENT": "tts",
        "AZURE_OPENAI_CHAT_DEPLOYMENT": "chat",
        "AZURE_OPENAI_TTS_VOICE_HOST_A": "fable",
        "AZURE_OPENAI_TTS_VOICE_HOST_B": "alloy",
        "AZURE_OPENAI_AUTH_MODE": "managed_identity",
    }


def _production_config() -> TtsConfig:
    return load_tts_config(_production_env())


def test_load_config_reads_all_settings_and_is_production_ready():
    config = _production_config()
    assert config.endpoint == "https://podcaster-openai.openai.azure.com/"
    assert config.tts_deployment == "tts"
    assert config.chat_deployment == "chat"
    assert config.voice_host_a == "fable"
    assert config.voice_host_b == "alloy"
    assert config.auth_mode == AUTH_MODE_MANAGED_IDENTITY
    assert config.production_ready is True


def test_load_config_defaults_to_not_ready_when_unconfigured():
    config = load_tts_config({})
    assert config.production_ready is False
    assert config.endpoint is None
    assert config.voice_host_a is None


def test_load_config_blank_values_are_treated_as_missing():
    env = _production_env()
    env["AZURE_OPENAI_ENDPOINT"] = "   "
    config = load_tts_config(env)
    assert config.endpoint is None
    assert config.production_ready is False


def test_local_auth_mode_is_not_production_ready():
    env = _production_env()
    env["AZURE_OPENAI_AUTH_MODE"] = "api_key"
    assert load_tts_config(env).production_ready is False


def test_voice_for_maps_hosts_to_fable_and_alloy():
    config = _production_config()
    assert config.voice_for("host_a") == "fable"
    assert config.voice_for("A") == "fable"
    assert config.voice_for("host_b") == "alloy"
    assert config.voice_for("guest") == "alloy"
    assert config.voice_for("unknown") == "fable"


def test_safe_summary_never_exposes_full_endpoint_or_secrets():
    summary = _production_config().safe_summary()
    assert summary["provider"] == PROVIDER
    assert summary["endpoint_configured"] is True
    assert summary["endpoint_host"] == "podcaster-openai.openai.azure.com"
    assert summary["voices"] == {HOST_A_ROLE: "fable", HOST_B_ROLE: "alloy"}
    serialized = json.dumps(summary)
    assert "openai.azure.com/" not in serialized.replace("podcaster-openai.openai.azure.com", "")
    assert "Bearer" not in serialized
    assert "/openai/deployments" not in serialized


def test_decision_blocked_for_dry_run_even_when_configured_and_reviewed():
    decision = synthesis_decision(_production_config(), dry_run=True, review_approved=True)
    assert decision["allowed"] is False
    assert "dry_run" in decision["blocked_by"]


def test_decision_blocked_without_review():
    decision = synthesis_decision(_production_config(), dry_run=False, review_approved=False)
    assert decision["allowed"] is False
    assert decision["blocked_by"] == ["human_review"]


def test_decision_blocked_without_config():
    decision = synthesis_decision(load_tts_config({}), dry_run=False, review_approved=True)
    assert decision["allowed"] is False
    assert "openai_tts_not_configured" in decision["blocked_by"]


def test_decision_allowed_only_when_configured_reviewed_and_not_dry_run():
    decision = synthesis_decision(_production_config(), dry_run=False, review_approved=True)
    assert decision["allowed"] is True
    assert decision["status"] == "allowed"
    assert decision["blocked_by"] == []
    assert decision["auth_mode"] == AUTH_MODE_MANAGED_IDENTITY


def test_build_voice_plan_assigns_two_voices():
    config = _production_config()
    segments = [("host_a", "Welcome to Claracle."), ("host_b", "Great to be here."), ("host_a", "Let's dig in.")]
    plan = build_voice_plan(segments, config)
    assert [turn.voice for turn in plan] == ["fable", "alloy", "fable"]
    assert [turn.role for turn in plan] == [HOST_A_ROLE, HOST_B_ROLE, HOST_A_ROLE]
    assert all(turn.deployment == "tts" for turn in plan)


def test_build_voice_plan_rejects_empty_segments():
    with pytest.raises(ValueError):
        build_voice_plan([], _production_config())


def test_build_voice_plan_rejects_unconfigured():
    with pytest.raises(ValueError):
        build_voice_plan([("host_a", "hi")], load_tts_config({}))


def test_synthesize_turn_builds_request_without_leaking_token(caplog):
    config = _production_config()
    plan = build_voice_plan([("host_a", "Hello from Claracle.")], config)
    captured: dict[str, object] = {}

    def fake_transport(request):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.headers.get("Authorization")
        return b"ID3-fake-mp3-bytes"

    with caplog.at_level("INFO"):
        audio = synthesize_turn(
            plan[0],
            config,
            token_provider=lambda scope: "secret-token-value",
            transport=fake_transport,
        )

    assert audio == b"ID3-fake-mp3-bytes"
    assert captured["url"] == (
        "https://podcaster-openai.openai.azure.com/openai/deployments/tts/audio/speech"
        "?api-version=2024-12-01-preview"
    )
    assert captured["body"] == {
        "model": "tts",
        "input": "Hello from Claracle.",
        "voice": "fable",
        "response_format": "mp3",
    }
    assert captured["auth"] == "Bearer secret-token-value"
    # The secret token and untrusted input text must never reach the logs.
    assert "secret-token-value" not in caplog.text
    assert "Hello from Claracle." not in caplog.text


def test_synthesize_two_voice_fails_closed_when_blocked():
    config = _production_config()
    plan = build_voice_plan([("host_a", "hi")], config)
    decision = synthesis_decision(config, dry_run=True, review_approved=True)
    with pytest.raises(PermissionError):
        synthesize_two_voice(
            plan,
            config,
            decision,
            token_provider=lambda scope: pytest.fail("must not request a token when blocked"),
            transport=lambda request: pytest.fail("must not call the network when blocked"),
        )


def test_synthesize_two_voice_runs_each_turn_when_allowed():
    config = _production_config()
    plan = build_voice_plan([("host_a", "one"), ("host_b", "two")], config)
    decision = synthesis_decision(config, dry_run=False, review_approved=True)
    calls: list[str] = []

    def fake_transport(request):
        calls.append(json.loads(request.data.decode("utf-8"))["voice"])
        return b"audio"

    audio = synthesize_two_voice(
        plan,
        config,
        decision,
        token_provider=lambda scope: "t",
        transport=fake_transport,
    )
    assert audio == [b"audio", b"audio"]
    assert calls == ["fable", "alloy"]


def _styled_config() -> TtsConfig:
    env = _production_env()
    env["AZURE_OPENAI_TTS_STYLE_HOST_A"] = "bright and energetic"
    env["AZURE_OPENAI_TTS_STYLE_HOST_B"] = "dry and measured"
    return load_tts_config(env)


def test_build_voice_plan_assigns_per_host_style():
    config = _styled_config()
    plan = build_voice_plan([("host_a", "hi"), ("host_b", "yo")], config)
    assert plan[0].style == "bright and energetic"
    assert plan[1].style == "dry and measured"


def test_synthesize_turn_includes_style_instructions_when_configured():
    config = _styled_config()
    plan = build_voice_plan([("host_a", "Hello.")], config)
    captured: dict[str, object] = {}

    def fake_transport(request):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return b"audio"

    synthesize_turn(plan[0], config, token_provider=lambda s: "t", transport=fake_transport)
    assert captured["body"]["instructions"] == "bright and energetic"


def test_synthesize_turn_falls_back_without_style_when_instructions_rejected():
    config = _styled_config()
    plan = build_voice_plan([("host_a", "Hello.")], config)
    bodies: list[dict] = []

    def fake_transport(request):
        body = json.loads(request.data.decode("utf-8"))
        bodies.append(body)
        if "instructions" in body:
            raise RuntimeError("model does not support instructions")
        return b"audio"

    audio = synthesize_turn(plan[0], config, token_provider=lambda s: "t", transport=fake_transport)
    assert audio == b"audio"
    # First attempt carried style; the retry dropped it and succeeded.
    assert "instructions" in bodies[0]
    assert "instructions" not in bodies[1]


def test_styles_summary_flags_configuration_without_exposing_text():
    summary = _styled_config().safe_summary()
    assert summary["styles_configured"] == {HOST_A_ROLE: True, HOST_B_ROLE: True}
