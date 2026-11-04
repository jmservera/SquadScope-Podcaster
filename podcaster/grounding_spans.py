"""Deterministic source-span table for the grounding gate.

Pure and network-free: the same evidence always yields the same ids and hashes.
Ids are ``A01..`` for article sentences and ``N01..`` for breaking-news sentences.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from podcaster.claim_extraction import _candidate_spans, _normalize_with_map

MIN_SPAN_WORDS = 4
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(?:`{3,}|~{3,})")


@dataclass(frozen=True)
class Span:
    span_id: str
    source_kind: str
    start: int
    end: int
    text: str
    sha: str


def _usable(text: str) -> bool:
    stripped = text.strip()
    return (
        len(stripped.split()) >= MIN_SPAN_WORDS
        and not _URL.search(stripped)
        and not _FENCE.match(stripped)
        and not stripped.startswith("#")
        and "`" not in stripped
    )


def _table(evidence: str, prefix: str, kind: str) -> list[Span]:
    # _candidate_spans returns single sentences first, then adjacent pairs; keep sentences.
    sentences = _candidate_spans(evidence)
    count = (len(sentences) + 1) // 2
    spans: list[Span] = []
    for start, end in sentences[:count]:
        raw = evidence[start:end]
        if not _usable(raw):
            continue
        text = raw.strip()
        offset = start + raw.index(text)
        spans.append(
            Span(
                span_id=f"{prefix}{len(spans) + 1:02d}",
                source_kind=kind,
                start=offset,
                end=offset + len(text),
                text=text,
                sha=hashlib.sha1(text.encode("utf-8")).hexdigest()[:8],
            )
        )
    return spans


def build_span_table(article_evidence: str, news_evidence: str = "") -> list[Span]:
    """Ordered span table: article spans then breaking-news spans."""

    return _table(article_evidence, "A", "article") + _table(news_evidence, "N", "breaking_news")


def spans_overlapping(table: list[Span], kind: str, start: int, end: int) -> list[str]:
    return [s.span_id for s in table if s.source_kind == kind and s.start < end and s.end > start]


def quote_text(span: Span) -> str:
    """Markdown-stripped, whitespace-folded text used when a span is rendered as a quote."""

    return _normalize_with_map(span.text)[0].strip()
