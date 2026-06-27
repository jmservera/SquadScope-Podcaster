"""Tests for host ownership-tone enforcement (#418)."""

from __future__ import annotations

import json
from urllib.request import Request

import pytest

from podcaster.config import PodcastConfig
from podcaster.ownership_tone import (
    OWNERSHIP_TONE_PROMPT,
    build_repair_instruction,
    find_soft_flags,
    find_violations,
    has_violations,
)
from podcaster.script_gen import (
    ScriptGenConfig,
    _build_system_prompt,
    generate_script,
)


class TestBannedPhraseDetection:
    @pytest.mark.parametrize(
        "line",
        [
            "Theo: The article mentions three AI frameworks worth watching.",
            "Theo: This article really nails the trend.",
            "Vera: The report says developers are shifting to Rust.",
            "Vera: The report mentions a big jump in stars.",
            "Theo: According to the roundup, everyone loves it.",
            "Theo: According to this analysis, growth is huge.",
            "Vera: The roundup says it's the repo of the week.",
            "Vera: In the article they cover six tools.",
            "Theo: In this report the numbers are wild.",
            "Theo: As the article notes, adoption is climbing.",
        ],
    )
    def test_flags_banned_phrases(self, line):
        violations = find_violations(line)
        assert len(violations) == 1
        assert has_violations(line) is True

    def test_clean_ownership_voice_has_no_violations(self):
        script = (
            "Theo: We found three AI frameworks worth watching this week.\n"
            "Vera: Our analysis shows developers are shifting to Rust.\n"
            "Theo: What stood out to us was the jump in stars.\n"
        )
        assert find_violations(script) == []
        assert has_violations(script) is False

    def test_case_insensitive_and_whitespace_tolerant(self):
        line = "Theo: THE   ARTICLE mentions a new tool."
        violations = find_violations(line)
        assert len(violations) == 1
        assert violations[0].phrase.lower() == "the article"

    def test_reports_line_numbers(self):
        script = (
            "Theo: We found a great repo.\n"
            "Vera: The article mentions another one.\n"
            "Theo: Our analysis shows it's popular.\n"
        )
        violations = find_violations(script)
        assert len(violations) == 1
        assert violations[0].line_number == 2

    def test_overlapping_in_the_article_counted_once(self):
        # "in the article" overlaps "the article" but must yield a single match.
        violations = find_violations("Theo: In the article they cover six tools.")
        assert len(violations) == 1


class TestNonSpokenLinesIgnored:
    def test_section_headers_not_scanned(self):
        script = "## Section: The Article Roundup\nTheo: We found a great repo."
        assert find_violations(script) == []

    def test_metadata_lines_not_scanned(self):
        script = (
            "Title: The article that changed everything\n"
            "Source: https://example.com/the-article\n"
            "---\n"
            "Theo: We found a great repo this week."
        )
        assert find_violations(script) == []

    def test_full_script_header_keys_not_scanned(self):
        # Mirrors the real header emitted by script_gen._format_script. The
        # multi-word keys (Source URL, Source SHA256, etc.) must be treated as
        # metadata so banned-looking values never produce false positives.
        script = (
            "Title: Claracle Podcast – Week 2026-W26\n"
            "Episode: 2026-W26\n"
            "Podcast: Claracle (https://claracle.example)\n"
            "Source URL: https://example.com/the-article-roundup\n"
            "Source SHA256: deadbeef\n"
            "Voices: Theo = alloy (OpenAI TTS); Vera = sage (OpenAI TTS)\n"
            "Safety: source article text is untrusted data, sanitized, "
            "and never executed as instructions.\n"
            "Generator: squad-podcaster llm-script-gen v0.1\n"
            "---\n"
            "Theo: We dug into three AI frameworks this week."
        )
        assert find_violations(script) == []
        assert find_soft_flags(script) == []

    def test_intro_brand_reference_allowed(self):
        # Referencing Claracle as the brand name is fine — not an external source.
        line = "Theo: Welcome to Claracle, where we track the best repos."
        assert find_violations(line) == []


class TestSoftFlags:
    def test_according_to_github_stars_is_soft_only(self):
        line = "Theo: According to GitHub stars, this repo is exploding."
        assert find_violations(line) == []
        soft = find_soft_flags(line)
        assert len(soft) == 1

    def test_was_mentioned_is_soft_only(self):
        line = "Vera: It was mentioned at the conference last week."
        assert find_violations(line) == []
        assert len(find_soft_flags(line)) == 1

    def test_banned_according_to_not_double_counted_as_soft(self):
        line = "Theo: According to the article, growth is huge."
        assert len(find_violations(line)) == 1
        assert find_soft_flags(line) == []


class TestRepairInstruction:
    def test_includes_offending_lines_and_ownership_guidance(self):
        violations = find_violations("Theo: The article mentions a new tool.")
        instruction = build_repair_instruction(violations)
        assert "the article" in instruction.lower()
        assert "We found" in instruction
        assert "## Section:" in instruction
        assert "Line 1" in instruction


class TestSystemPromptIntegration:
    def test_prompt_block_present(self):
        prompt = _build_system_prompt(PodcastConfig())
        assert OWNERSHIP_TONE_PROMPT.strip() in prompt
        assert "ownership language" in prompt.lower()


def _mock_config() -> ScriptGenConfig:
    return ScriptGenConfig(
        endpoint="https://test.openai.azure.com/",
        chat_deployment="chat",
        auth_mode="managed_identity",
    )


def _fake_token_provider(scope: str) -> str:
    return "fake-token"


def _sequenced_transport(responses: list[str]):
    """Return a transport that yields each response in order, recording calls."""

    calls: list[dict] = []

    def transport(request: Request) -> bytes:
        payload = json.loads(request.data.decode("utf-8"))
        calls.append(payload)
        content = responses[min(len(calls) - 1, len(responses) - 1)]
        body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
        return json.dumps(body).encode("utf-8")

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


class TestGenerateScriptRepairFlow:
    def _gen(self, transport):
        return generate_script(
            week="2026-W26",
            article_title="Test",
            article_url="https://example.com/a",
            article_content="x" * 100,
            config=_mock_config(),
            token_provider=_fake_token_provider,
            transport=transport,
        )

    def test_no_violation_single_call(self):
        clean = "Theo: We found a great repo.\nVera: Our analysis shows it's popular."
        transport = _sequenced_transport([clean])
        script = self._gen(transport)
        assert len(transport.calls) == 1
        assert "We found a great repo" in script

    def test_violation_triggers_repair(self):
        bad = "Theo: The article mentions a great repo.\nVera: Nice."
        good = "Theo: We found a great repo.\nVera: Nice."
        transport = _sequenced_transport([bad, good])
        script = self._gen(transport)
        # First call generated, second call was the repair round-trip.
        assert len(transport.calls) == 2
        assert "The article mentions" not in script
        assert "We found a great repo" in script
        # The repair request carried the assistant draft + repair instruction.
        repair_messages = transport.calls[1]["messages"]
        assert repair_messages[-2]["role"] == "assistant"
        assert repair_messages[-1]["role"] == "user"
        assert "ownership language" in repair_messages[-1]["content"].lower()

    def test_persistent_violation_flagged_not_fatal(self, caplog):
        bad = "Theo: The article mentions a great repo.\nVera: Nice."
        transport = _sequenced_transport([bad, bad])
        with caplog.at_level("WARNING"):
            script = self._gen(transport)
        # 1 generation + 1 repair attempt (MAX_OWNERSHIP_REPAIRS=1).
        assert len(transport.calls) == 2
        # Job still completes (manual-review flag, not a hard failure).
        assert "The article mentions" in script
        assert any("manual review" in r.message for r in caplog.records)

    def test_soft_flags_logged_at_warning(self, caplog):
        # Soft flags are surfaced as warnings (not INFO) so they are visible in
        # production log filters.
        soft = "Theo: According to our benchmark, it's faster.\nVera: Nice work."
        transport = _sequenced_transport([soft])
        with caplog.at_level("WARNING"):
            self._gen(transport)
        assert any(
            "ownership soft-flag" in r.message and r.levelname == "WARNING" for r in caplog.records
        )
