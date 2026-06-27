"""LLM-based claim ledger extraction from source articles (#141).

Extracts substantive factual claims from article content using the Azure OpenAI
chat endpoint, maps each claim to source text, and produces a structured
claim_ledger.json for human review.

Safety:
- Article text is treated as untrusted and sanitized before prompt embedding.
- Never logs full article content, tokens, or endpoint URLs.
- Unverified claims are flagged for human review.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.request import Request

from podcaster.sanitization import neutralize
from podcaster.script_gen import (
    MAX_ARTICLE_CHARS,
    ScriptGenConfig,
    _default_transport,
)
from podcaster.storage import ManagedIdentityTokenCredential
from podcaster.tts import OPENAI_SCOPE, TokenProvider, Transport

logger = logging.getLogger("podcaster.claim_extraction")

# Maximum claims to extract per article
MAX_CLAIMS = 15


@dataclass(frozen=True)
class Claim:
    """A single extracted factual claim with source mapping."""

    claim_id: str
    script_excerpt: str
    source_url: str
    source_quote: str | None
    source_paragraph: int | None
    verified: bool
    editor_notes: str


def _build_claim_extraction_prompt(article_content: str, article_url: str) -> tuple[str, str]:
    """Build system and user prompts for claim extraction."""

    system_prompt = f"""You are a fact-checking assistant. Extract substantive factual claims from \
the provided article.

For each claim:
1. Identify the specific factual assertion (not opinions or speculation)
2. Quote the source text that supports it
3. Note the approximate paragraph number where it appears
4. Mark it as unverified (verified=false) — human reviewers will verify

OUTPUT FORMAT (you MUST return valid JSON):
Return a JSON array of claim objects. Each object has:
- "claim_id": string like "claim_001", "claim_002", etc.
- "script_excerpt": the factual claim as it would be stated in conversation
- "source_url": "{article_url}"
- "source_quote": the exact quote from the article supporting this claim (or null if implicit)
- "source_paragraph": approximate paragraph number (integer, 1-indexed, or null)
- "verified": false (always — human review required)
- "editor_notes": brief note about verification difficulty or context

Extract {MAX_CLAIMS} or fewer claims. Focus on:
- Quantitative assertions (numbers, percentages, dates)
- Causal claims (X caused Y, X led to Y)
- Comparative claims (X is better/worse/faster than Y)
- Existence claims (X exists, X was released, X happened)

Skip opinions, predictions, and hedged speculation ("might", "could", "arguably").
Return ONLY the JSON array, no additional text or markdown formatting."""

    user_prompt = f"""Extract factual claims from this article:

{article_content}"""

    return system_prompt, user_prompt


def extract_claims(
    *,
    article_content: str,
    article_url: str,
    config: ScriptGenConfig,
    token_provider: TokenProvider | None = None,
    transport: Transport | None = None,
) -> list[Claim]:
    """Extract factual claims from article content using the Azure OpenAI chat endpoint.

    Returns a list of Claim objects. Falls back to an empty list on parse failure.
    Raises ValueError if config is not ready.
    """

    if not config.ready:
        raise ValueError("claim extraction requires a configured Azure OpenAI chat endpoint")

    safe_content = neutralize(article_content, limit=MAX_ARTICLE_CHARS)

    system_prompt, user_prompt = _build_claim_extraction_prompt(safe_content, article_url)

    token_provider = token_provider or ManagedIdentityTokenCredential().get_token
    transport = transport or _default_transport

    token = token_provider(OPENAI_SCOPE)
    if not token:
        raise RuntimeError("managed identity returned an empty token for claim extraction")

    base = config.endpoint if config.endpoint.endswith("/") else f"{config.endpoint}/"
    url = (
        f"{base}openai/deployments/{config.chat_deployment}/chat/completions"
        f"?api-version={config.api_version}"
    )

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
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

    logger.info(
        "extracting claims deployment=%s article_chars=%s",
        config.chat_deployment,
        len(safe_content),
    )

    raw_response = transport(request)
    response = json.loads(raw_response.decode("utf-8"))

    choices = response.get("choices", [])
    if not choices:
        logger.warning("claim extraction returned no choices")
        return []

    content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        logger.warning("claim extraction returned empty content")
        return []

    return _parse_claims(content, article_url)


def _parse_claims(raw_json: str, article_url: str) -> list[Claim]:
    """Parse the LLM response into Claim objects."""

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning("claim extraction returned invalid JSON")
        return []

    # Handle both array directly and object with a claims key
    if isinstance(parsed, dict):
        # Try common keys
        for key in ("claims", "results", "data"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                break
        else:
            logger.warning("claim extraction returned object without recognized claims key")
            return []

    if not isinstance(parsed, list):
        logger.warning("claim extraction did not return an array")
        return []

    claims: list[Claim] = []
    for i, item in enumerate(parsed[:MAX_CLAIMS]):
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("claim_id", f"claim_{i + 1:03d}"))
        script_excerpt = str(item.get("script_excerpt", "")).strip()
        if not script_excerpt:
            continue
        claims.append(
            Claim(
                claim_id=claim_id,
                script_excerpt=script_excerpt,
                source_url=str(item.get("source_url", article_url)),
                source_quote=(
                    item.get("source_quote") if isinstance(item.get("source_quote"), str) else None
                ),
                source_paragraph=(
                    item.get("source_paragraph")
                    if isinstance(item.get("source_paragraph"), int)
                    else None
                ),
                verified=False,  # Always false — human review required
                editor_notes=str(item.get("editor_notes", "Requires human verification")),
            )
        )

    logger.info("extracted %d claims from article", len(claims))
    return claims


def claims_to_ledger_json(claims: list[Claim]) -> str:
    """Serialize claims to the standard claim-ledger.json format."""

    if not claims:
        return (
            json.dumps(
                [
                    {
                        "claim_id": "stub_000",
                        "script_excerpt": (
                            "[No claims extracted — pending editorial "
                            "generation from source article]"
                        ),
                        "source_url": "",
                        "source_quote": None,
                        "verified": False,
                        "editor_notes": (
                            "Claim ledger will be populated during editorial "
                            "generation. Human review required."
                        ),
                    }
                ],
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )

    ledger: list[dict[str, Any]] = []
    for claim in claims:
        entry: dict[str, Any] = {
            "claim_id": claim.claim_id,
            "script_excerpt": claim.script_excerpt,
            "source_url": claim.source_url,
            "source_quote": claim.source_quote,
            "verified": claim.verified,
            "editor_notes": claim.editor_notes,
        }
        if claim.source_paragraph is not None:
            entry["source_paragraph"] = claim.source_paragraph
        ledger.append(entry)

    return json.dumps(ledger, sort_keys=True, indent=2) + "\n"
