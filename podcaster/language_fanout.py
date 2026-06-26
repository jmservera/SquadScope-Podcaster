"""Language fan-out job architecture (#439).

The multilanguage pipeline gathers *source* assets once (episode brief + the
language-independent browser recordings) and then processes each configured
language **independently**: script generation, TTS, overlays, compose, publish.
A failure in one language must not block or re-run the shared source work or the
other languages — each language branch retries on its own.

This module provides the orchestration primitives for that shape:

* :func:`plan_language_branches` — ordered, de-duplicated locales to fan out to.
* :func:`shared_artifact_path` / :func:`language_artifact_path` — the
  ``jobs/{id}/...`` vs ``jobs/{id}/{locale}/...`` artifact layout.
* :func:`run_language_fanout` — gather source once, then run each language with
  independent retry, isolating per-language failures.

The functions are pure/orchestration-only (no I/O of their own): callers inject
``gather_source`` and ``process_language`` callables, so the engine, the job
runner, and tests all compose the same control flow.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, TypeVar

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"

S = TypeVar("S")  # shared-source result type
B = TypeVar("B")  # per-language branch payload type


class NonRetryableError(RuntimeError):
    """Raise from ``process_language`` to fail a branch without retrying."""


# --- artifact layout ----------------------------------------------------------


def _language_key(locale: str | None) -> str:
    """Return a normalized locale key (full locale, lowercased).

    Full subtags are preserved so that ``pt-BR`` and ``pt-PT`` produce distinct
    keys (``pt-br`` / ``pt-pt``), maintaining per-locale isolation for artifact
    paths and retry branches.
    """
    return (locale or "").strip().lower()


def shared_artifact_path(job_id: str, name: str) -> str:
    """Path for a shared, language-independent artifact: ``jobs/{id}/{name}``."""

    return f"jobs/{job_id}/{name.lstrip('/')}"


def language_artifact_path(
    job_id: str,
    locale: str,
    name: str,
    *,
    flat_default_language: str | None = DEFAULT_LANGUAGE,
) -> str:
    """Path for a per-language artifact: ``jobs/{id}/{locale}/{name}``.

    *locale* is normalized to lowercase (e.g. ``fr-FR`` → ``fr-fr``,
    ``pt-BR`` → ``pt-br``); full subtags are preserved so that ``pt-BR`` and
    ``pt-PT`` produce distinct artifact prefixes.

    To preserve existing English layout, the default language can stay flat
    (``jobs/{id}/{name}``) by leaving ``flat_default_language`` at ``"en"``; pass
    ``None`` to always nest every language (including English) under its locale.
    """

    key = _language_key(locale)
    name = name.lstrip("/")
    if flat_default_language is not None and key == _language_key(flat_default_language):
        return f"jobs/{job_id}/{name}"
    return f"jobs/{job_id}/{key}/{name}"


# --- branch planning ----------------------------------------------------------


def plan_language_branches(
    languages: Iterable[Any] | Mapping[str, Any] | None,
    *,
    default_language: str = DEFAULT_LANGUAGE,
) -> list[str]:
    """Return the ordered, de-duplicated list of languages to fan out to.

    Accepts a list of language codes/locales, a ``{lang: config}`` mapping, or an
    iterable of objects exposing a ``language``/``locale`` attribute. The default
    language is always present and sorted first so English work stays primary.
    """

    ordered: list[str] = []

    def _add(raw: Any) -> None:
        if isinstance(raw, str):
            key = _language_key(raw)
        else:
            key = _language_key(getattr(raw, "language", None) or getattr(raw, "locale", None))
        if key and key not in ordered:
            ordered.append(key)

    if isinstance(languages, Mapping):
        for k in languages:
            _add(k)
    elif languages is not None:
        for item in languages:
            _add(item)

    default_key = _language_key(default_language)
    if default_key and default_key not in ordered:
        ordered.insert(0, default_key)
    elif default_key in ordered:
        ordered.remove(default_key)
        ordered.insert(0, default_key)

    return ordered


# --- results ------------------------------------------------------------------


@dataclass
class LanguageBranchResult:
    """Outcome of processing one language branch."""

    language: str
    status: str  # "succeeded" | "failed"
    attempts: int = 0
    payload: Any = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


@dataclass
class FanOutResult:
    """Aggregate result: one shared gather + N independent language branches."""

    shared: Any
    branches: dict[str, LanguageBranchResult] = field(default_factory=dict)

    @property
    def succeeded_languages(self) -> list[str]:
        return [lang for lang, r in self.branches.items() if r.succeeded]

    @property
    def failed_languages(self) -> list[str]:
        return [lang for lang, r in self.branches.items() if not r.succeeded]

    @property
    def all_succeeded(self) -> bool:
        return bool(self.branches) and not self.failed_languages


# --- retry --------------------------------------------------------------------


@dataclass(frozen=True)
class RetryPolicy:
    """Per-branch retry policy. ``NonRetryableError`` always short-circuits."""

    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")


def _run_branch(
    language: str,
    shared: S,
    process_language: Callable[[str, S], B],
    retry: RetryPolicy,
) -> LanguageBranchResult:
    last_error: Exception | None = None
    for attempt in range(1, retry.max_attempts + 1):
        try:
            payload = process_language(language, shared)
        except NonRetryableError as exc:
            logger.warning("Language %r failed (non-retryable): %s", language, exc)
            return LanguageBranchResult(
                language=language, status="failed", attempts=attempt, error=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-language failure
            last_error = exc
            logger.warning(
                "Language %r attempt %d/%d failed: %s",
                language,
                attempt,
                retry.max_attempts,
                exc,
            )
            continue
        else:
            return LanguageBranchResult(
                language=language,
                status="succeeded",
                attempts=attempt,
                payload=payload,
            )
    return LanguageBranchResult(
        language=language,
        status="failed",
        attempts=retry.max_attempts,
        error=str(last_error) if last_error else "unknown error",
    )


def run_language_fanout(
    languages: Iterable[Any] | Mapping[str, Any] | None,
    *,
    gather_source: Callable[[], S],
    process_language: Callable[[str, S], B],
    retry: RetryPolicy | None = None,
    default_language: str = DEFAULT_LANGUAGE,
    max_workers: int = 1,
) -> FanOutResult:
    """Gather source once, then process each language independently.

    Args:
        languages: Configured languages (see :func:`plan_language_branches`).
        gather_source: Runs the shared, language-independent source stage exactly
            once. If it raises, the whole job fails (no branches run).
        process_language: ``(language, shared) -> payload`` for one language.
            Exceptions are retried per :class:`RetryPolicy`; raise
            :class:`NonRetryableError` to fail immediately. A failure isolates to
            that branch and never re-runs ``gather_source``.

            **Thread safety**: when ``max_workers > 1``, the same ``shared``
            object is passed to all threads concurrently. ``process_language``
            **must treat ``shared`` as read-only**; any mutation of shared state
            introduces data races. If per-branch mutation is needed, deep-copy
            the relevant parts inside ``process_language``.
        retry: Per-branch retry policy (default 3 attempts).
        default_language: Always-present primary language.
        max_workers: >1 runs branches in parallel threads.

    Returns:
        :class:`FanOutResult` with the shared payload and per-language results.
    """

    retry = retry or RetryPolicy()
    branch_languages = plan_language_branches(languages, default_language=default_language)

    shared = gather_source()  # once; propagate failure (fatal, no branches)

    branches: dict[str, LanguageBranchResult] = {}
    if max_workers > 1 and len(branch_languages) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_run_branch, lang, shared, process_language, retry): lang
                for lang in branch_languages
            }
            for future in futures:
                result = future.result()
                branches[result.language] = result
        # Preserve planned ordering for deterministic iteration.
        branches = {lang: branches[lang] for lang in branch_languages}
    else:
        for lang in branch_languages:
            branches[lang] = _run_branch(lang, shared, process_language, retry)

    return FanOutResult(shared=shared, branches=branches)
