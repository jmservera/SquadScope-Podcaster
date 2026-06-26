"""Language-neutral episode brief (#433).

A single structured brief is built once per episode from the gathered source
material (article, extracted claims, links). It is **data, not prose**: facts,
claims, links, entities, and a topic outline — with no locale-specific phrasing.
Every per-language script generation (#434) consumes the same brief plus that
language's :class:`podcaster.config.LanguageConfig`, so source gathering and
claim extraction happen once and are reused across en/es/fr.

This module is pure (no network, no LLM calls) so it is unit-testable and safe
to import anywhere. It builds on the already-reviewed claim ledger
(:mod:`podcaster.claim_extraction`) rather than re-deriving facts per language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from podcaster.claim_extraction import Claim

EPISODE_BRIEF_SCHEMA = "podcaster.episode-brief/v1"

# Tokens that look like preservable proper nouns / tech terms (kept verbatim
# across languages so localized scripts still say "GitHub", "OIDC", repo names).
# Three alternatives: owner/repo style slugs (any leading case), capitalized
# proper nouns (optionally dotted/hyphenated), and all-caps acronyms.
_ENTITY_RE = re.compile(
    r"\b(?:[A-Za-z0-9]+(?:/[A-Za-z0-9]+)+|[A-Z][A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*|[A-Z]{2,})\b"
)
# Common English words that match the entity pattern at sentence start but are
# not proper nouns; excluded so the entity list stays meaningful.
_ENTITY_STOPWORDS = frozenset(
    {
        "The", "This", "That", "These", "Those", "There", "Then", "They", "Their",
        "A", "An", "And", "But", "For", "Nor", "Or", "So", "Yet", "It", "Its",
        "We", "You", "He", "She", "His", "Her", "Our", "Your", "When", "While",
        "With", "Where", "What", "Who", "Why", "How", "If", "In", "On", "At",
        "By", "To", "Of", "As", "Is", "Are", "Was", "Were", "Be", "Been",
    }
)


@dataclass(frozen=True)
class BriefClaim:
    """A language-neutral reference to a reviewed factual claim."""

    claim_id: str
    statement: str
    source_url: str
    source_quote: str | None = None
    verified: bool = False

    @classmethod
    def from_claim(cls, claim: Claim) -> "BriefClaim":
        return cls(
            claim_id=claim.claim_id,
            statement=claim.script_excerpt.strip(),
            source_url=claim.source_url,
            source_quote=claim.source_quote,
            verified=claim.verified,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "source_url": self.source_url,
            "verified": self.verified,
        }
        if self.source_quote:
            data["source_quote"] = self.source_quote
        return data


@dataclass(frozen=True)
class BriefLink:
    """A reference link (label + url), language-neutral."""

    url: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "label": self.label}


@dataclass(frozen=True)
class EpisodeBrief:
    """Shared, language-neutral input for all per-language script generations."""

    week: str
    source_title: str
    source_url: str
    topics: tuple[str, ...] = ()
    key_facts: tuple[str, ...] = ()
    claims: tuple[BriefClaim, ...] = ()
    links: tuple[BriefLink, ...] = ()
    entities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EPISODE_BRIEF_SCHEMA,
            "language_neutral": True,
            "week": self.week,
            "source": {"title": self.source_title, "url": self.source_url},
            "topics": list(self.topics),
            "key_facts": list(self.key_facts),
            "claims": [claim.to_dict() for claim in self.claims],
            "links": [link.to_dict() for link in self.links],
            "entities": list(self.entities),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EpisodeBrief":
        schema = data.get("schema")
        if schema is not None and schema != EPISODE_BRIEF_SCHEMA:
            raise ValueError(
                f"unsupported episode-brief schema '{schema}'; expected '{EPISODE_BRIEF_SCHEMA}'"
            )
        source = data.get("source") if isinstance(data.get("source"), Mapping) else {}
        claims = tuple(
            BriefClaim(
                claim_id=str(item.get("claim_id", "")),
                statement=str(item.get("statement", "")).strip(),
                source_url=str(item.get("source_url", "")),
                source_quote=item.get("source_quote") if isinstance(item.get("source_quote"), str) else None,
                verified=bool(item.get("verified", False)),
            )
            for item in data.get("claims", [])
            if isinstance(item, Mapping)
        )
        links = tuple(
            BriefLink(url=str(item.get("url", "")).strip(), label=str(item.get("label", "")))
            for item in data.get("links", [])
            if isinstance(item, Mapping) and str(item.get("url", "")).strip()
        )
        return cls(
            week=str(data.get("week", "")).strip(),
            source_title=str(source.get("title", "")).strip(),
            source_url=str(source.get("url", "")).strip(),
            topics=tuple(str(t).strip() for t in data.get("topics", []) if str(t).strip()),
            key_facts=tuple(str(f).strip() for f in data.get("key_facts", []) if str(f).strip()),
            claims=claims,
            links=links,
            entities=tuple(str(e).strip() for e in data.get("entities", []) if str(e).strip()),
        )


def _dedupe_preserving_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if key and key.lower() not in seen:
            seen.add(key.lower())
            out.append(key)
    return out


def extract_entities(texts: Iterable[str], *, limit: int = 20) -> list[str]:
    """Extract preservable proper-noun / tech-term entities from text.

    Entities (GitHub, OIDC, repo slugs) are kept verbatim across languages so a
    localized script does not translate or mangle product/tech names.
    """

    found: list[str] = []
    for text in texts:
        for match in _ENTITY_RE.findall(text or ""):
            if match in _ENTITY_STOPWORDS:
                continue
            if len(match) < 2:
                continue
            found.append(match)
    return _dedupe_preserving_order(found)[:limit]


def _links_from_claims(claims: Sequence[Claim], source_url: str) -> list[BriefLink]:
    urls = _dedupe_preserving_order(
        [c.source_url for c in claims if c.source_url and c.source_url != source_url]
    )
    return [BriefLink(url=url, label=urlparse(url).netloc or url) for url in urls]


def build_episode_brief(
    *,
    week: str,
    source_title: str,
    source_url: str,
    claims: Sequence[Claim] | None = None,
    topics: Sequence[str] | None = None,
    key_facts: Sequence[str] | None = None,
    links: Sequence[Mapping[str, str]] | None = None,
    entities: Sequence[str] | None = None,
) -> EpisodeBrief:
    """Assemble the language-neutral episode brief from gathered source material.

    ``claims`` are the reviewed claim ledger entries. When ``key_facts``,
    ``links``, or ``entities`` are not supplied they are derived from the claims
    (and the source title is additionally used for entity extraction) so the
    brief is self-contained from a single extraction pass.
    """

    if not week or not week.strip():
        raise ValueError("episode brief requires a week")
    if not source_title or not source_title.strip():
        raise ValueError("episode brief requires a source_title")
    if not source_url or not source_url.strip():
        raise ValueError("episode brief requires a source_url")

    claims = list(claims or [])
    brief_claims = tuple(BriefClaim.from_claim(c) for c in claims if c.script_excerpt.strip())

    if key_facts is None:
        derived_facts = [c.statement for c in brief_claims]
    else:
        derived_facts = list(key_facts)

    if links is None:
        brief_links = tuple(_links_from_claims(claims, source_url))
    else:
        brief_links = tuple(
            BriefLink(url=str(item.get("url", "")), label=str(item.get("label", "")))
            for item in links
            if str(item.get("url", "")).strip()
        )

    if entities is None:
        entity_texts = [source_title] + [c.statement for c in brief_claims]
        derived_entities = extract_entities(entity_texts)
    else:
        derived_entities = list(entities)

    return EpisodeBrief(
        week=week.strip(),
        source_title=(source_title or "").strip(),
        source_url=source_url.strip(),
        topics=tuple(_dedupe_preserving_order(topics or [])),
        key_facts=tuple(_dedupe_preserving_order(derived_facts)),
        claims=brief_claims,
        links=brief_links,
        entities=tuple(derived_entities),
    )
