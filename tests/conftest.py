"""Shared pytest fixtures for the unit test suite.

The monitoring/admin API fails **closed** when no authentication is configured
(#604): without any ``UI_AUTH_*`` or ``MONITORING_API_KEY``/``PODCASTER_API_KEY``
env vars it rejects every request with ``401`` unless an operator explicitly
opts out via ``MONITORING_AUTH_DISABLED=true`` for local development.

Most unit tests exercise endpoint behaviour without configuring auth, so they
run in that explicit local-dev mode by default. Tests that assert the auth gate
itself simply override the env var via their own ``monkeypatch``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest


def _offline_script_claims(*, script, podcast_config, article_content, article_url, **kwargs):
    from podcaster.claim_extraction import Claim
    from podcaster.episode import parse_script_segments

    return [
        Claim(
            claim_id=f"claim_{index:03d}",
            script_excerpt=text,
            source_url=article_url,
            source_quote=article_content,
            source_paragraph=1,
            verified=False,
            editor_notes="Offline fixture source alignment only; human review still required.",
        )
        for index, (_, text) in enumerate(parse_script_segments(script, podcast_config), 1)
    ]


@pytest.fixture
def offline_script_generation(monkeypatch):
    """Provide configured production generation with only an offline LLM transport."""
    import json

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "test-chat")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")
    dialogue = (
        "Theo: Welcome to the weekly signal, where we examine the engineering details.\n"
        "Vera: Both hosts are AI-generated synthetic voices, not human presenters.\n"
        "## Section: Product Rollout\n"
        "Theo: The product rollout changes how teams deploy their production systems.\n"
        "Vera: Customer impact depends on the engineering tradeoffs described this week.\n"
        "Theo: Reliability and developer experience deserve careful attention from users.\n"
        "Vera: Those choices explain the competitive response across the wider market.\n"
        "## Section: Engineering Tradeoffs\n"
        "Theo: The operational costs matter as much as the initial feature announcement.\n"
        "Vera: Teams should evaluate the practical deployment evidence before committing.\n"
        "Theo: That evidence connects the product decisions to measurable customer impact.\n"
        "Vera: We will keep watching how those engineering tradeoffs develop next week.\n"
    )

    def transport(request):
        body = json.loads(request.data)
        prompt = json.dumps(body)
        text = dialogue
        if "Ada" in prompt and "Bo" in prompt:
            text = text.replace("Theo:", "Ada:").replace("Vera:", "Bo:")
        return json.dumps({"choices": [{"message": {"content": text}}]}).encode()

    monkeypatch.setattr("podcaster.script_gen._default_transport", transport)
    monkeypatch.setattr(
        "podcaster.script_gen.ManagedIdentityTokenCredential.get_token",
        staticmethod(lambda scope: "offline-test-token"),
    )
    monkeypatch.setattr("podcaster.jobs.extract_claims", _offline_script_claims)


@pytest.fixture(autouse=True)
def _local_dev_auth(monkeypatch):
    # Clear any ambient auth env vars so endpoint-behaviour tests deterministically
    # run in explicit local-dev mode regardless of the developer's shell, then opt
    # in via MONITORING_AUTH_DISABLED. Tests asserting the auth gate override these.
    for var in (
        "UI_AUTH_USERNAME",
        "UI_AUTH_PASSWORD",
        "UI_AUTH_SECRET",
        "MONITORING_API_KEY",
        "PODCASTER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MONITORING_AUTH_DISABLED", "true")
    yield


@pytest.fixture
def reviewed_stage(tmp_path, monkeypatch):
    """Stage real dialogue with local storage and no network or provider calls."""
    from podcaster import queue as queue_module
    from podcaster.jobs import run_generation_job
    from podcaster.storage import LocalStorageBackend

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://podcaster-openai.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "test-chat")
    monkeypatch.setenv("AZURE_OPENAI_TTS_DEPLOYMENT", "tts-prod")
    monkeypatch.setenv("AZURE_OPENAI_TTS_VOICE_HOST_A", "fable")
    monkeypatch.setenv("AZURE_OPENAI_TTS_VOICE_HOST_B", "alloy")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")
    script = (
        "Title: Reviewed weekly signal\n---\n"
        "Theo: The reviewed opening is unique to this staged episode.\n"
        "Vera: Both voices are synthetic, and this sentence must survive unchanged.\n"
    )
    generator = Mock(return_value=script)
    monkeypatch.setattr("podcaster.jobs.generate_script", generator)
    monkeypatch.setattr("podcaster.jobs.extract_claims", _offline_script_claims)

    class Producer:
        def __init__(self):
            self.sent = []

        def send_message(self, body):
            self.sent.append(body)

    producer = Producer()
    monkeypatch.setattr(queue_module, "create_queue_backend", lambda: producer)
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    result = run_generation_job(
        {
            "week": "2026-W44",
            "article_url": "https://example.com/weekly/w44",
            "article_title": "Reviewed weekly signal",
            "podcast_config": {"name": "Claracle", "host_a": {"name": "Theo", "voice": "fable"}},
            "article_content": (
                "The source describes a product rollout and explains the engineering "
                "tradeoffs, customer impact, and competitive context in enough detail "
                "for the hosts to discuss the week's developments."
            ),
        },
        storage=storage,
        now=datetime(2026, 10, 29, 11, tzinfo=timezone.utc),
    )
    return storage, result, generator, producer, script.encode("utf-8")
