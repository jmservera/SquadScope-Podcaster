"""Tests for podcaster.claim_extraction — LLM-based claim ledger extraction (#141)."""

from __future__ import annotations

import json
from urllib.request import Request

import pytest

from podcaster.claim_extraction import (
    Claim,
    _parse_claims,
    claims_to_ledger_json,
    extract_claims,
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


def _make_transport(claims_json: str):
    """Build a mock transport that returns a fake chat completion with claims JSON."""

    def transport(request: Request) -> bytes:
        # Wrap in a JSON object since response_format: json_object is used
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": claims_json,
                    }
                }
            ]
        }
        return json.dumps(response).encode("utf-8")

    return transport


SAMPLE_CLAIMS = [
    {
        "claim_id": "claim_001",
        "script_excerpt": "Python 3.14 was released in June 2026",
        "source_url": "https://example.com/article",
        "source_quote": "Python 3.14 was officially released on June 1, 2026.",
        "source_paragraph": 2,
        "verified": False,
        "editor_notes": "Verify release date against python.org",
    },
    {
        "claim_id": "claim_002",
        "script_excerpt": "Performance improved by 40% over Python 3.13",
        "source_url": "https://example.com/article",
        "source_quote": "Benchmarks show a 40% improvement",
        "source_paragraph": 5,
        "verified": False,
        "editor_notes": "Verify benchmark methodology",
    },
]


class TestExtractClaims:
    def test_extracts_claims_successfully(self):
        config = _mock_config()
        transport = _make_transport(json.dumps(SAMPLE_CLAIMS))

        claims = extract_claims(
            article_content="Python 3.14 was released in June 2026 with 40% better performance.",
            article_url="https://example.com/article",
            config=config,
            token_provider=_fake_token_provider,
            transport=transport,
        )

        assert len(claims) == 2
        assert claims[0].claim_id == "claim_001"
        assert claims[0].script_excerpt == "Python 3.14 was released in June 2026"
        assert claims[0].source_quote == "Python 3.14 was officially released on June 1, 2026."
        assert claims[0].source_paragraph == 2
        assert claims[0].verified is False

    def test_raises_when_config_not_ready(self):
        config = _mock_config(ready=False)
        with pytest.raises(ValueError, match="configured"):
            extract_claims(
                article_content="Some content.",
                article_url="https://example.com",
                config=config,
                token_provider=_fake_token_provider,
                transport=_make_transport("[]"),
            )

    def test_returns_empty_on_no_choices(self):
        config = _mock_config()

        def empty_transport(request: Request) -> bytes:
            return json.dumps({"choices": []}).encode()

        claims = extract_claims(
            article_content="Content here.",
            article_url="https://example.com",
            config=config,
            token_provider=_fake_token_provider,
            transport=empty_transport,
        )
        assert claims == []

    def test_handles_object_wrapper(self):
        """LLM might return {"claims": [...]} instead of bare array."""
        config = _mock_config()
        wrapped = {"claims": SAMPLE_CLAIMS}
        transport = _make_transport(json.dumps(wrapped))

        claims = extract_claims(
            article_content="Python 3.14 was released.",
            article_url="https://example.com/article",
            config=config,
            token_provider=_fake_token_provider,
            transport=transport,
        )
        assert len(claims) == 2


class TestParseClaims:
    def test_parses_valid_array(self):
        claims = _parse_claims(json.dumps(SAMPLE_CLAIMS), "https://example.com")
        assert len(claims) == 2
        assert claims[0].claim_id == "claim_001"

    def test_returns_empty_on_invalid_json(self):
        claims = _parse_claims("not valid json", "https://example.com")
        assert claims == []

    def test_returns_empty_on_non_array_non_object(self):
        claims = _parse_claims('"just a string"', "https://example.com")
        assert claims == []

    def test_skips_items_without_script_excerpt(self):
        data = [{"claim_id": "c1", "script_excerpt": ""}, {"claim_id": "c2", "script_excerpt": "Real claim"}]
        claims = _parse_claims(json.dumps(data), "https://example.com")
        assert len(claims) == 1
        assert claims[0].script_excerpt == "Real claim"

    def test_forces_verified_false(self):
        """Even if LLM returns verified=true, we force it to false."""
        data = [{"claim_id": "c1", "script_excerpt": "A claim", "verified": True}]
        claims = _parse_claims(json.dumps(data), "https://example.com")
        assert claims[0].verified is False


class TestClaimsToLedgerJson:
    def test_empty_claims_produces_stub(self):
        result = claims_to_ledger_json([])
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["claim_id"] == "stub_000"

    def test_claims_serialized_correctly(self):
        claims = [
            Claim(
                claim_id="claim_001",
                script_excerpt="Test claim",
                source_url="https://example.com",
                source_quote="The exact quote",
                source_paragraph=3,
                verified=False,
                editor_notes="Check this",
            )
        ]
        result = claims_to_ledger_json(claims)
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["claim_id"] == "claim_001"
        assert parsed[0]["source_quote"] == "The exact quote"
        assert parsed[0]["source_paragraph"] == 3
        assert parsed[0]["verified"] is False

    def test_null_optional_fields(self):
        claims = [
            Claim(
                claim_id="claim_001",
                script_excerpt="A claim",
                source_url="https://example.com",
                source_quote=None,
                source_paragraph=None,
                verified=False,
                editor_notes="Notes",
            )
        ]
        result = claims_to_ledger_json(claims)
        parsed = json.loads(result)
        assert parsed[0]["source_quote"] is None
        assert "source_paragraph" not in parsed[0]
