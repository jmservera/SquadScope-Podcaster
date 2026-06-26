from __future__ import annotations

import json

import pytest

from podcaster.claim_extraction import Claim
from podcaster.episode_brief import (
    EPISODE_BRIEF_SCHEMA,
    BriefClaim,
    BriefLink,
    EpisodeBrief,
    build_episode_brief,
    extract_entities,
)


def _claim(claim_id: str, excerpt: str, url: str = "https://claracle.com/a", quote: str | None = None) -> Claim:
    return Claim(
        claim_id=claim_id,
        script_excerpt=excerpt,
        source_url=url,
        source_quote=quote,
        source_paragraph=None,
        verified=False,
        editor_notes="Requires human verification",
    )


def test_build_brief_requires_week_and_source_url():
    with pytest.raises(ValueError, match="week"):
        build_episode_brief(week="", source_title="t", source_url="https://x")
    with pytest.raises(ValueError, match="source_url"):
        build_episode_brief(week="2026-W23", source_title="t", source_url="")


def test_brief_is_built_once_from_claims_and_reusable():
    claims = [
        _claim("c1", "GitHub Actions added OIDC support for Azure."),
        _claim("c2", "The Claracle pipeline runs weekly.", url="https://blog.example.com/post"),
    ]
    brief = build_episode_brief(
        week="2026-W23",
        source_title="Claracle Weekly: OIDC and Azure",
        source_url="https://claracle.com/a",
        claims=claims,
        topics=["OIDC", "Azure auth", "weekly pipeline"],
    )
    assert brief.week == "2026-W23"
    assert len(brief.claims) == 2
    assert brief.claims[0].statement == "GitHub Actions added OIDC support for Azure."
    # key_facts derived from claims when not supplied.
    assert "GitHub Actions added OIDC support for Azure." in brief.key_facts
    # External link (different from source_url) is captured with its netloc label.
    assert any(link.url == "https://blog.example.com/post" for link in brief.links)
    assert any(link.label == "blog.example.com" for link in brief.links)


def test_brief_contains_no_locale_specific_phrasing_only_data():
    # The brief is data (topics/facts/claims/links/entities) — there is no
    # narration prose field that would carry a single language's phrasing.
    brief = build_episode_brief(
        week="2026-W23", source_title="t", source_url="https://claracle.com/a"
    )
    data = brief.to_dict()
    assert data["language_neutral"] is True
    assert set(data) >= {"topics", "key_facts", "claims", "links", "entities", "source"}
    assert "script" not in data and "dialogue" not in data and "narration" not in data


def test_entities_preserve_proper_nouns_and_tech_terms():
    entities = extract_entities(
        ["GitHub Actions added OIDC support for Azure with the jmservera/SquadScope repo."]
    )
    assert "GitHub" in entities
    assert "OIDC" in entities
    assert "Azure" in entities
    assert "jmservera/SquadScope" in entities
    # Sentence-leading stopwords are excluded.
    assert "The" not in entities


def test_entities_derived_from_title_and_claims_when_not_supplied():
    brief = build_episode_brief(
        week="2026-W23",
        source_title="Azure and GitHub",
        source_url="https://claracle.com/a",
        claims=[_claim("c1", "OIDC tokens replace stored secrets.")],
    )
    assert "Azure" in brief.entities
    assert "GitHub" in brief.entities
    assert "OIDC" in brief.entities


def test_explicit_overrides_take_precedence():
    brief = build_episode_brief(
        week="2026-W23",
        source_title="t",
        source_url="https://claracle.com/a",
        claims=[_claim("c1", "derived fact")],
        key_facts=["explicit fact only"],
        links=[{"url": "https://docs.example.com", "label": "Docs"}],
        entities=["CustomEntity"],
    )
    assert brief.key_facts == ("explicit fact only",)
    assert brief.links == (BriefLink(url="https://docs.example.com", label="Docs"),)
    assert brief.entities == ("CustomEntity",)


def test_dedupe_topics_and_facts_case_insensitively():
    brief = build_episode_brief(
        week="2026-W23",
        source_title="t",
        source_url="https://claracle.com/a",
        topics=["Azure", "azure", "OIDC"],
        key_facts=["Fact one.", "fact one.", "Fact two."],
    )
    assert brief.topics == ("Azure", "OIDC")
    assert brief.key_facts == ("Fact one.", "Fact two.")


def test_brief_round_trips_through_dict_and_json():
    brief = build_episode_brief(
        week="2026-W23",
        source_title="Azure and GitHub",
        source_url="https://claracle.com/a",
        claims=[_claim("c1", "OIDC tokens replace stored secrets.", quote="OIDC replaces secrets")],
        topics=["auth"],
    )
    data = brief.to_dict()
    assert data["schema"] == EPISODE_BRIEF_SCHEMA
    blob = json.dumps(data)
    restored = EpisodeBrief.from_dict(json.loads(blob))
    assert restored.week == brief.week
    assert restored.source_title == brief.source_title
    assert restored.topics == brief.topics
    assert restored.key_facts == brief.key_facts
    assert restored.claims[0].claim_id == "c1"
    assert restored.claims[0].source_quote == "OIDC replaces secrets"


def test_brief_claim_from_claim_maps_fields():
    bc = BriefClaim.from_claim(_claim("c9", "  spaced excerpt  ", quote="q"))
    assert bc.claim_id == "c9"
    assert bc.statement == "spaced excerpt"
    assert bc.source_quote == "q"
    assert bc.verified is False
