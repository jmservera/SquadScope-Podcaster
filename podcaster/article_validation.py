"""Lightweight article input validation helpers.

This module intentionally avoids heavy runtime dependencies so request
validation can import it without pulling in LLM, Azure, or TTS modules.
"""

from __future__ import annotations

ARTICLE_MIN_CHARS = 150


def validate_article_inputs(article_title: object, article_content: object) -> None:
    """Reject missing/blank/undersized article inputs before any LLM call."""

    normalized_title = article_title.strip() if isinstance(article_title, str) else ""
    if not normalized_title:
        raise ValueError(
            "article_title is missing or empty — cannot generate script; provide the source "
            "article title in the job payload"
        )

    normalized_content = article_content.strip() if isinstance(article_content, str) else ""
    if not normalized_content:
        raise ValueError(
            "article_content is missing or empty — cannot generate script; provide the full "
            "article body in the job payload"
        )

    if len(normalized_content) < ARTICLE_MIN_CHARS:
        raise ValueError(
            "article_content is too short "
            f"({len(normalized_content)} chars); minimum is {ARTICLE_MIN_CHARS} — "
            "cannot generate script; provide the full scraped article text before retrying"
        )
