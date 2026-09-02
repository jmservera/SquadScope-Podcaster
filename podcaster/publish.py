"""Spotify for Creators auto-publish integration (#182).

Publishes generated Claracle episodes to Spotify for Creators using the
unofficial internal API. Publication is opt-in (``SPOTIFY_PUBLISH_ENABLED=true``)
and **never blocks** the generation pipeline — publish failures are logged and
reported but do not fail the overall episode workflow.

Authentication uses browser session cookies (``SP_DC`` + ``SP_KEY``) to obtain
a short-lived Bearer token via ``spotifyconnector``. The module provides a
health-check function to verify auth status without side effects.

Security:
- Cookies are read from environment variables, never logged or committed.
- Dry-run mode (``SPOTIFY_PUBLISH_DRY_RUN=true``) simulates all steps without
  making real API calls.
- Fail-safe publishing: because this uses an unofficial cookie-authenticated
  API (a cookie leak is account-takeover material), episodes are only ever made
  public when an operator explicitly sets ``SPOTIFY_ALLOW_LIVE_PUBLISH=true``.
  Otherwise any ``immediate``/``scheduled`` request is downgraded to a draft
  (jmservera/SquadScope-Podcaster#602).
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests

from podcaster.config import MAX_SPOTIFY_DESCRIPTION_CHARS, SpotifyPublishConfig
from podcaster.spotify_shows import resolve_show_target

try:
    from spotifyconnector import SpotifyConnector
except ModuleNotFoundError:  # pragma: no cover - exercised via monkeypatch in tests
    SpotifyConnector = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Spotify for Creators internal API base
_BASE_URL = "https://api-v5.anchor.fm"
_SPOTIFY_CLIENT_ID = (
    os.environ.get("SPOTIFY_CLIENT_ID") or ""
).strip() or "05a1371ee5194c27860b3ff3ff3979d2"
_SPOTIFY_CONNECTOR_BASE_URL = "https://generic.wg.spotify.com/podcasters/v0"

# Required headers for mutation requests
_MUTATION_HEADERS = {
    "Origin": "https://creators.spotify.com",
    "Referer": "https://creators.spotify.com/",
}

# Retry configuration
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2.0
_POLL_INTERVAL = 5
_POLL_MAX_ATTEMPTS = 60


@dataclass
class PublishResult:
    """Result of an episode publish attempt."""

    anchor_episode_id: int | None = None
    status: str = "failed"  # "published" | "scheduled" | "draft" | "failed"
    error: str | None = None
    dry_run: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class SpotifyPublishError(Exception):
    """Raised when a Spotify API call fails."""


class SpotifyDraftReconcileError(SpotifyPublishError):
    """Raised when the existing-draft lookup cannot be completed.

    Reconcile-before-create only prevents duplicate Spotify drafts when the
    lookup is known to be complete. A failed or truncated lookup must never be
    reported as "no draft exists", because the caller would then create a
    second draft for an episode that already has one. Callers that genuinely
    prefer a blind create can disable reconcile with
    ``PODCASTER_SPOTIFY_RECONCILE=0``.
    """


class SpotifyDraftCreateAmbiguousError(SpotifyPublishError):
    """Raised when a draft-create POST may or may not have taken effect.

    ``POST /v3/stations/{id}/episodes`` is state-mutating and the Anchor v5 API
    exposes no idempotency key. A timeout, a dropped connection, a 408/429 or a
    5xx therefore leaves the *server* state unknown: the draft may already
    exist. Re-sending the request blindly would create a second, untitled draft
    that title-based reconcile can never find, so the create is never retried
    on its own. It is raised for the caller to resolve with evidence (a
    re-list, see :func:`_recover_ambiguous_create`) or to surface as a failure.
    """


class SpotifyCredentialExpiredError(SpotifyPublishError):
    """Raised when Spotify rejects the request due to expired credentials.

    This signals that the ``SP_DC`` / ``SP_KEY`` browser cookies (or the
    short-lived bearer token derived from them) are no longer valid and an
    operator must refresh them. It is distinct from generic publish failures
    so callers can trigger an explicit, actionable credential-expiry
    notification.
    """


def _is_enabled() -> bool:
    """Check if Spotify publishing is enabled."""
    return os.environ.get("SPOTIFY_PUBLISH_ENABLED", "").lower() == "true"


def _is_dry_run() -> bool:
    """Check if dry-run mode is active."""
    return os.environ.get("SPOTIFY_PUBLISH_DRY_RUN", "").lower() == "true"


# Truthy values accepted for boolean opt-in env vars.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _live_publish_allowed() -> bool:
    """Whether going *live* on Spotify (publish/schedule) is explicitly allowed.

    The Spotify integration authenticates with browser session cookies against
    an **unofficial** internal API — a cookie leak is Spotify-account-takeover
    material and the API can break without notice
    (jmservera/SquadScope-Podcaster#602). Because of that risk the publisher
    fails **safe**: unless an operator has explicitly accepted the risk by
    setting ``SPOTIFY_ALLOW_LIVE_PUBLISH`` truthy, any ``immediate``/``scheduled``
    request is downgraded to a **draft** so nothing is ever silently made public.

    ``SPOTIFY_PUBLISH_ENABLED`` only gates whether the integration runs at all;
    this separate flag gates whether it may make an episode *public*.
    """
    return os.environ.get("SPOTIFY_ALLOW_LIVE_PUBLISH", "").strip().lower() in _TRUTHY


@functools.lru_cache(maxsize=1)
def _warn_live_publish_downgraded_once() -> None:
    """Log the live-publish downgrade warning at most once per process."""
    logger.warning(
        "Spotify live publishing is not enabled (SPOTIFY_ALLOW_LIVE_PUBLISH is "
        "unset or not truthy) — downgrading to a DRAFT. The Spotify path uses an "
        "unofficial cookie-authenticated API "
        "(jmservera/SquadScope-Podcaster#602); set SPOTIFY_ALLOW_LIVE_PUBLISH to "
        "a truthy value (1/true/yes/on) only after accepting that risk to make "
        "episodes public."
    )


def _get_credentials(
    language: str = "en",
    *,
    language_config: object | None = None,
) -> tuple[str, str, str]:
    """Return (show_id, sp_dc, sp_key) from environment / per-language config.

    ``language`` selects the per-language Spotify show (#438): each language
    publishes to its own show (Claracle Weekly/Semanal/Hebdo). English resolves
    ``SPOTIFY_SHOW_ID`` exactly as before; other languages resolve
    ``SPOTIFY_SHOW_ID_<LANG>`` (falling back to ``SPOTIFY_SHOW_ID``) or an
    explicit ``language_config.spotify_show_id``.

    Raises ValueError if any credential is missing.
    """
    target = resolve_show_target(language, language_config=language_config)
    show_id = target.show_id
    sp_dc = os.environ.get("SP_DC", "")
    sp_key = os.environ.get("SP_KEY", "")

    missing = []
    if not show_id:
        missing.append(target.env_var)
    if not sp_dc:
        missing.append("SP_DC")
    if not sp_key:
        missing.append("SP_KEY")

    if missing:
        raise ValueError(
            f"Missing Spotify credentials: {', '.join(missing)}. "
            "Set these environment variables to enable publishing."
        )
    return show_id, sp_dc, sp_key


def _build_session(sp_dc: str, sp_key: str, show_id: str) -> requests.Session:
    """Build a requests session with Spotify bearer auth."""
    session = requests.Session()
    bearer = _request_bearer_token(sp_dc, sp_key, show_id)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer}",
        }
    )
    return session


def _mums_params(**kwargs: str) -> dict[str, str]:
    return {**kwargs, "isMumsCompatible": "true"}


def _request_bearer_token(sp_dc: str, sp_key: str, show_id: str) -> str:
    """Exchange browser cookies for a short-lived Spotify bearer token."""
    if SpotifyConnector is None:
        raise SpotifyPublishError(
            "spotifyconnector is not installed. Install with `pip install -e .` to "
            "enable Spotify publishing."
        )
    connector = SpotifyConnector(
        base_url=_SPOTIFY_CONNECTOR_BASE_URL,
        client_id=_SPOTIFY_CLIENT_ID,
        podcast_id=show_id,
        sp_dc=sp_dc,
        sp_key=sp_key,
    )
    try:
        connector._authenticate()
    except Exception as exc:
        message = str(exc)
        if "login required" in message.lower() or "credentials" in message.lower():
            raise SpotifyCredentialExpiredError(
                "Spotify cookies expired — operator must refresh SP_DC/SP_KEY."
            ) from exc
        raise SpotifyPublishError("Failed to exchange Spotify cookies for bearer token.") from exc

    bearer = connector._bearer or ""
    if not bearer:
        raise SpotifyPublishError("Spotify auth flow returned no bearer token.")
    return bearer


def _safe_url(url: str) -> str:
    """Strip query parameters from a URL to avoid leaking signed tokens in logs."""
    parsed = urlparse(url)
    if parsed.query or parsed.fragment:
        return urlunparse(parsed._replace(query="[REDACTED]", fragment=""))
    return url


def _is_retryable(exc: requests.RequestException) -> bool:
    """Return True only for transient failures safe to retry."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    return False


def _http_suffix(exc: BaseException) -> str:
    """`` (HTTP 503)`` when the exception carries a response, else ``""``."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return f" (HTTP {status})" if isinstance(status, int) else ""


def _retry_request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_attempts: int = _MAX_RETRIES,
    **kwargs: Any,
) -> requests.Response:
    """Execute an HTTP request with exponential backoff retry.

    Only retries on transient errors (5xx, 408, 429, timeouts, connection
    errors). Client errors (4xx) are raised immediately to avoid duplicating
    state-mutating requests.

    ``max_attempts=1`` disables retries entirely. Requests whose *server-side*
    effect cannot be observed from a transport failure — notably the draft
    create — use it, because a transient error there is indistinguishable from
    "the draft was created and the response was lost" (see
    :class:`SpotifyDraftCreateAmbiguousError`).
    """
    last_exc: Exception | None = None
    log_url = _safe_url(url)
    attempts = max(1, int(max_attempts))
    for attempt in range(attempts):
        try:
            resp = session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if (
                isinstance(exc, requests.HTTPError)
                and exc.response is not None
                and exc.response.status_code in {401, 403}
            ):
                logger.error(
                    "Spotify API %s %s returned HTTP %d — credentials expired.",
                    method,
                    log_url,
                    exc.response.status_code,
                )
                raise SpotifyCredentialExpiredError(
                    "Spotify rejected the request (HTTP "
                    f"{exc.response.status_code}) — SP_DC/SP_KEY credentials "
                    "expired. Operator must refresh them."
                ) from exc
            if not _is_retryable(exc) or attempt >= attempts - 1:
                if exc.response is not None:
                    body_snippet = exc.response.text[:500] if exc.response.text else "(empty)"
                    logger.error(
                        "Spotify API %s %s final failure body: %s",
                        method,
                        log_url,
                        body_snippet,
                    )
                break
            wait = _RETRY_BACKOFF_BASE**attempt
            safe_reason = type(exc).__name__
            if exc.response is not None:
                safe_reason += f" (HTTP {exc.response.status_code})"
            logger.warning(
                "Spotify API %s %s failed (attempt %d/%d): %s — retrying in %.1fs",
                method,
                log_url,
                attempt + 1,
                attempts,
                safe_reason,
                wait,
            )
            time.sleep(wait)
    raise SpotifyPublishError(
        f"Spotify API {method} {log_url} failed after {attempt + 1} attempt(s)"
    ) from last_exc


def verify_spotify_auth(
    language: str = "en",
    *,
    language_config: object | None = None,
) -> tuple[bool, str]:
    """Health-check: verify Spotify auth is valid without side effects.

    ``language`` selects the per-language show to verify (#438).

    Returns (is_valid, message).
    """
    try:
        show_id, sp_dc, sp_key = _get_credentials(language, language_config=language_config)
    except ValueError as exc:
        return False, str(exc)

    if _is_dry_run():
        return True, "Dry-run mode — credentials present, skipping live check."

    try:
        session = _build_session(sp_dc, sp_key, show_id)
        url = f"{_BASE_URL}/v3/shows/{show_id}/legacyIds"
        resp = session.get(url, params=_mums_params(), timeout=10)
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                return False, "Spotify auth invalid — legacyIds response is not valid JSON."
            if data.get("stationId") and data.get("userId"):
                return True, "Spotify auth valid."
            return False, "Spotify auth invalid — legacyIds response missing IDs."
        elif resp.status_code in {401, 403}:
            return False, (
                "Spotify cookies expired (HTTP "
                f"{resp.status_code}) — operator must refresh SP_DC/SP_KEY."
            )
        else:
            return False, f"Unexpected status {resp.status_code} from Spotify."
    except SpotifyPublishError as exc:
        return False, str(exc)
    except requests.RequestException as exc:
        return False, f"Spotify connectivity error: {exc}"


def _require_identity(raw: Any, field_name: str, show_id: str) -> str:
    """Coerce a legacyIds identity field to a non-empty string, or fail loudly."""
    if raw is None or isinstance(raw, bool):
        raise SpotifyPublishError(
            f"Spotify legacyIds response for show {show_id} is missing {field_name}."
        )
    value = str(raw).strip()
    if not value:
        raise SpotifyPublishError(
            f"Spotify legacyIds response for show {show_id} is missing {field_name}."
        )
    return value


def _resolve_legacy_ids(session: requests.Session, show_id: str) -> tuple[str, str]:
    """Step 1: Resolve show_id to stationId + userId."""
    url = f"{_BASE_URL}/v3/shows/{show_id}/legacyIds"
    resp = _retry_request(session, "GET", url, params=_mums_params(), timeout=15)
    try:
        data = resp.json()
    except ValueError as exc:
        raise SpotifyPublishError(
            f"Spotify legacyIds response for show {show_id} is not valid JSON."
        ) from exc
    if not isinstance(data, dict):
        raise SpotifyPublishError(
            f"Spotify legacyIds response for show {show_id} has an unexpected shape."
        )
    station_id = _require_identity(data.get("stationId"), "stationId", show_id)
    user_id = _require_identity(data.get("userId"), "userId", show_id)
    logger.info("Resolved show %s → station=%s user=%s", show_id, station_id, user_id)
    return station_id, user_id


def _create_episode(session: requests.Session, station_id: str) -> int:
    """Step 2: Create a draft episode, returns anchorId.

    The POST is sent **exactly once**. It is state-mutating and the API offers
    no idempotency key, so the generic 408/429/5xx/timeout retry must not be
    applied here: each retried attempt could create another *untitled* draft,
    and an untitled draft is invisible to title-based reconcile — an orphan no
    later run can ever clean up. A transient failure (or a success whose body
    cannot be read) therefore raises
    :class:`SpotifyDraftCreateAmbiguousError`, which the video path resolves
    with evidence in :func:`_recover_ambiguous_create`. Deterministic failures
    (4xx other than 408/429) raise :class:`SpotifyPublishError`: no draft was
    created.
    """
    url = f"{_BASE_URL}/v3/stations/{station_id}/episodes"
    try:
        resp = _retry_request(
            session,
            "POST",
            url,
            max_attempts=1,
            headers=_MUTATION_HEADERS,
            params=_mums_params(),
            json={"hourOffset": 0},
            timeout=15,
        )
    except SpotifyCredentialExpiredError:
        raise
    except SpotifyPublishError as exc:
        cause = exc.__cause__
        if isinstance(cause, requests.RequestException) and _is_retryable(cause):
            raise SpotifyDraftCreateAmbiguousError(
                f"Spotify draft create for station {station_id} "
                f"({_safe_url(url)}) failed transiently ({type(cause).__name__}"
                f"{_http_suffix(cause)}); the draft may or may not exist "
                "server-side. Not retrying blindly."
            ) from exc
        raise

    try:
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("episode create response is not an object")
        anchor_id = int(data.get("episodeId") or data["id"])
    except (ValueError, TypeError, KeyError) as exc:
        # The server accepted the create; only its *identifier* was lost.
        raise SpotifyDraftCreateAmbiguousError(
            f"Spotify draft create for station {station_id} "
            f"({_safe_url(url)}) returned HTTP {resp.status_code} with a body "
            f"this code cannot read ({type(exc).__name__}); a draft was very "
            "likely created but its id is unknown. Not retrying blindly."
        ) from exc
    logger.info("Created draft episode anchorId=%d", anchor_id)
    return anchor_id


def _spotify_reconcile_enabled() -> bool:
    """Whether video draft reconcile-before-create is enabled (default on)."""
    raw = os.environ.get("PODCASTER_SPOTIFY_RECONCILE")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _spotify_strict_paging_enabled() -> bool:
    """Whether an explicitly paginated listing should fail closed (opt-in).

    The Anchor v5 paging contract is unverified (see :data:`_PAGINATION_HINT_KEYS`),
    so failing closed on a *guessed* key name could block every new video publish.
    Operators who have confirmed the contract for their show can opt in.
    """
    raw = os.environ.get("PODCASTER_SPOTIFY_RECONCILE_STRICT_PAGING", "")
    return raw.strip().lower() in _TRUTHY


_EPISODE_LIST_KEYS = ("episodes", "items", "data", "results")
_TITLE_KEYS = ("title", "name")
_ID_KEYS = ("episodeId", "id", "anchorId")
_STATUS_KEYS = ("status", "state", "publishStatus", "publishState")

# Boolean state fields. ``isDraft`` is the flag this listing has been seen to
# use; ``isPublished`` is the field name this code itself sends on
# ``/v3/episodes/{id}/update`` (:func:`_set_metadata`), so its polarity is
# known. Both must be a real JSON boolean — a string ``"false"`` is *not* a
# boolean and is treated as schema drift, never as truthiness.
_BOOL_STATE_KEYS: dict[str, bool] = {"isDraft": True, "isPublished": False}

# String state tokens with evidence, deliberately minimal. ``draft`` is the
# value this integration itself drives episodes into (``publish_behavior``) and
# the only token the previous revision recognised; ``published`` is its
# observed opposite. Every other token — ``scheduled``, ``processing``,
# ``error``, anything new — is *unknown*, and unknown is an error rather than a
# guessed "not a draft", because guessing wrong either reuses a published
# episode or creates a duplicate draft. Extend only with observed evidence.
_DRAFT_STATE_TOKENS = frozenset({"draft"})
_NON_DRAFT_STATE_TOKENS = frozenset({"published"})

# Unverified: no successful listing response has ever been observed (every call
# 400'd on the missing userId), so these key names are informed guesses only.
_PAGINATION_HINT_KEYS = ("hasMore", "hasNextPage", "nextPageToken", "nextPage")

_FAIL_CLOSED_SUFFIX = (
    "refusing to report 'no draft exists' from a listing this code cannot read, "
    "because that would create a duplicate draft"
)

_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def _safe_keys(data: dict[Any, Any], limit: int = 10) -> str:
    """Allow-listed key *names* (never values) for diagnostics.

    Response *values* may carry tokens or personal data and are never logged;
    key names are what identify a schema change, so only those are surfaced,
    filtered through a conservative character allow-list.
    """
    names = [key for key in data if isinstance(key, str) and _SAFE_KEY_RE.match(key)]
    shown = sorted(names)[:limit]
    suffix = ", …" if len(names) > len(shown) else ""
    return f"[{', '.join(shown)}{suffix}]" if shown else "[]"


def _episode_items(data: Any) -> list[Any]:
    """Extract the episode array from a listing payload, failing **closed**.

    ``_find_existing_draft`` may only answer "no draft exists" when it actually
    understood the listing. An unrecognised container, an error body, a renamed
    field or a primitive payload therefore raises
    :class:`SpotifyDraftReconcileError` instead of degrading to ``[]`` — the old
    behaviour, which silently turned schema drift into a duplicate draft on
    every publish. A *recognised* empty list is still a legitimate no-match.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in _EPISODE_LIST_KEYS:
            if key in data:
                value = data[key]
                if isinstance(value, list):
                    return value
                raise SpotifyDraftReconcileError(
                    f"Spotify episode listing field '{key}' is a "
                    f"{type(value).__name__}, not an array; {_FAIL_CLOSED_SUFFIX}."
                )
        raise SpotifyDraftReconcileError(
            "Spotify episode listing exposes no recognised episode array "
            f"(top-level keys: {_safe_keys(data)}); {_FAIL_CLOSED_SUFFIX}."
        )
    raise SpotifyDraftReconcileError(
        f"Spotify episode listing returned an unexpected {type(data).__name__} "
        f"payload; {_FAIL_CLOSED_SUFFIX}."
    )


def _safe_token(value: str) -> str:
    """A state token, only when it passes the conservative allow-list.

    Diagnosing schema drift needs the *token* ("scheduled", "processing"), but
    response values may carry personal data, so anything that is not a short
    identifier-shaped string is reported as ``<unprintable>`` instead.
    """
    return value if _SAFE_KEY_RE.match(value) else "<unprintable>"


def _episode_is_draft(episode: dict[Any, Any]) -> bool:
    """Whether *episode* is a draft, decided from explicit evidence only.

    Every recognised state field is read and must be *understood*:

    - a boolean field (:data:`_BOOL_STATE_KEYS`) must be an actual JSON
      ``bool``. A string (``"false"``), a number (``0``) or an object is schema
      drift, not truthiness — ``bool("false")`` is ``True``, which would have
      made a published episode look like a draft and got it overwritten.
    - a string state field (:data:`_STATUS_KEYS`) must carry a token this code
      has evidence for. An unknown token (``"scheduled"``, ``"processing"``, a
      value invented by a future API version) is **not** silently treated as
      "not a draft": that answer would create a duplicate draft, or, if wrong
      in the other direction, reuse an episode that is already live.
    - fields that disagree with each other are drift as well.

    An explicit ``null`` carries no state and is skipped, exactly like an
    absent field. Raises :class:`SpotifyDraftReconcileError` when the state
    cannot be established.
    """
    evidence: dict[str, bool] = {}

    for key, draft_when_true in _BOOL_STATE_KEYS.items():
        if key not in episode:
            continue
        raw = episode[key]
        if raw is None:
            continue
        if not isinstance(raw, bool):
            raise SpotifyDraftReconcileError(
                f"Spotify episode listing entry has a non-boolean '{key}' "
                f"({type(raw).__name__} where a JSON boolean is required); it is "
                f"not truth-tested, because e.g. the string 'false' is truthy; "
                f"{_FAIL_CLOSED_SUFFIX}."
            )
        evidence[key] = raw is draft_when_true

    for key in _STATUS_KEYS:
        if key not in episode:
            continue
        raw = episode[key]
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise SpotifyDraftReconcileError(
                f"Spotify episode listing entry has a {type(raw).__name__} '{key}' "
                f"where a state string was expected; {_FAIL_CLOSED_SUFFIX}."
            )
        token = raw.strip().lower()
        if token in _DRAFT_STATE_TOKENS:
            evidence[key] = True
        elif token in _NON_DRAFT_STATE_TOKENS:
            evidence[key] = False
        else:
            raise SpotifyDraftReconcileError(
                f"Spotify episode listing entry has an unrecognised '{key}' value "
                f"'{_safe_token(token)}' (known: "
                f"{sorted(_DRAFT_STATE_TOKENS | _NON_DRAFT_STATE_TOKENS)}); an "
                f"unknown state is never assumed to be 'not a draft'; "
                f"{_FAIL_CLOSED_SUFFIX}."
            )

    if not evidence:
        raise SpotifyDraftReconcileError(
            "Spotify episode listing entry exposes no recognised draft/published "
            f"state (keys: {_safe_keys(episode)}); {_FAIL_CLOSED_SUFFIX}."
        )
    if len(set(evidence.values())) > 1:
        raise SpotifyDraftReconcileError(
            "Spotify episode listing entry carries contradictory draft/published "
            f"state across {sorted(evidence)}; {_FAIL_CLOSED_SUFFIX}."
        )
    return next(iter(evidence.values()))


def _episode_anchor_id(episode: dict[Any, Any]) -> int | None:
    """Anchor id of *episode*, or ``None`` when no usable id is present."""
    for key in _ID_KEYS:
        raw = episode.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def _draft_episode_id(episode: Any, title: str) -> int | None:
    """Anchor id of *episode* when it is a draft whose title matches.

    Returns ``None`` when the entry was fully understood and does not match, and
    raises :class:`SpotifyDraftReconcileError` when it cannot be understood.
    An entry whose title cannot be read might be the very draft being looked
    for, so it can never count towards proving absence.

    A *present but null* title is understood: that is an untitled draft, which
    genuinely does not match (see :func:`_claim_draft_title`).
    """
    if not isinstance(episode, dict):
        raise SpotifyDraftReconcileError(
            f"Spotify episode listing contains a {type(episode).__name__} entry "
            f"where an object was expected; {_FAIL_CLOSED_SUFFIX}."
        )

    if not any(key in episode for key in _TITLE_KEYS):
        raise SpotifyDraftReconcileError(
            "Spotify episode listing entry exposes no recognised title field "
            f"(keys: {_safe_keys(episode)}); {_FAIL_CLOSED_SUFFIX}."
        )

    raw_title: Any = None
    for key in _TITLE_KEYS:
        if episode.get(key) is not None:
            raw_title = episode[key]
            break
    if raw_title is None:
        return None
    if not isinstance(raw_title, str):
        raise SpotifyDraftReconcileError(
            f"Spotify episode listing entry has a {type(raw_title).__name__} title "
            f"where a string was expected; {_FAIL_CLOSED_SUFFIX}."
        )
    if raw_title.strip() != title.strip():
        return None

    if not _episode_is_draft(episode):
        return None

    anchor_id = _episode_anchor_id(episode)
    if anchor_id is None:
        raise SpotifyDraftReconcileError(
            "Spotify episode listing entry matching the target title exposes no "
            f"usable episode id (keys: {_safe_keys(episode)}); {_FAIL_CLOSED_SUFFIX}."
        )
    return anchor_id


def _pagination_hint(data: Any) -> str | None:
    """Name of the key by which the listing appears to signal further pages."""
    if not isinstance(data, dict):
        return None
    for key in _PAGINATION_HINT_KEYS:
        if data.get(key):
            return key
    return None


def _fetch_episode_listing(
    session: requests.Session,
    station_id: str,
    *,
    user_id: str,
) -> Any:
    """GET the station episode listing, failing **closed** on any problem.

    The Anchor v5 episode listing requires ``userId`` as a query parameter;
    omitting it returns HTTP 400 ``query.userId is required``. ``user_id`` comes
    from :func:`_resolve_legacy_ids` alongside ``station_id``.
    """
    resolved_user_id = str(user_id).strip()
    if not resolved_user_id:
        raise SpotifyDraftReconcileError(
            "Spotify draft reconcile requires a userId, but none was resolved "
            f"for station {station_id}."
        )

    url = f"{_BASE_URL}/v3/stations/{station_id}/episodes"
    try:
        resp = _retry_request(
            session,
            "GET",
            url,
            params=_mums_params(userId=resolved_user_id),
            timeout=15,
        )
        return resp.json()
    except SpotifyCredentialExpiredError:
        raise
    except (SpotifyPublishError, requests.RequestException, ValueError) as exc:
        # Never echo the response body or session cookies — only the request
        # shape and the failure type.
        raise SpotifyDraftReconcileError(
            f"Spotify draft reconcile lookup failed for station {station_id} "
            f"({_safe_url(url)}): {type(exc).__name__}. Refusing to create a new "
            "draft because an existing one may already exist."
        ) from exc


def _match_existing_draft(
    data: Any,
    station_id: str,
    title: str,
    *,
    exclude_id: int | None = None,
) -> int | None:
    """Anchor id of the draft in *data* matching *title*, or ``None``.

    ``None`` is a *proof of absence*: it is returned only when a recognised
    container was read and every entry in it was understood.
    """
    for episode in _episode_items(data):
        episode_id = _draft_episode_id(episode, title)
        if episode_id is not None and episode_id != exclude_id:
            logger.info(
                "Reconciled existing Spotify draft anchorId=%d for title=%r",
                episode_id,
                title,
            )
            return episode_id

    hint_key = _pagination_hint(data)
    if hint_key is not None:
        if _spotify_strict_paging_enabled():
            raise SpotifyDraftReconcileError(
                f"Spotify draft reconcile lookup for station {station_id} signalled "
                f"further pages via '{hint_key}' and found no match on the first "
                "page; strict paging is enabled, so a possibly incomplete read "
                "will not be used to justify creating a new draft."
            )
        logger.warning(
            "Spotify episode listing for station %s carries a truthy '%s' key and "
            "contained no match for title=%r. Pagination is NOT implemented (the "
            "real paging contract is unverified), so this read may be incomplete "
            "and a duplicate draft is possible. Set "
            "PODCASTER_SPOTIFY_RECONCILE_STRICT_PAGING=1 to fail closed instead.",
            station_id,
            hint_key,
            title,
        )

    logger.info("No existing Spotify draft matched title=%r; a new draft is needed.", title)
    return None


def _find_existing_draft(
    session: requests.Session,
    station_id: str,
    title: str,
    *,
    user_id: str,
    exclude_id: int | None = None,
) -> int | None:
    """Look up an existing draft episode with an exact title match.

    ``exclude_id`` (the audio anchor episode) is never returned, so a same-titled
    audio draft can never be mistaken for the separate video draft (#564).

    Returns the anchor id of the matching draft, or ``None`` only when the
    listing was read **and understood** and contained no match. Any failure —
    transport, HTTP, non-JSON body, or a listing schema this code cannot read —
    raises :class:`SpotifyDraftReconcileError` so a broken lookup can never be
    mistaken for "no draft exists" and duplicate a draft.
    """
    data = _fetch_episode_listing(session, station_id, user_id=user_id)
    return _match_existing_draft(data, station_id, title, exclude_id=exclude_id)


def _snapshot_episode_ids(data: Any) -> tuple[set[int], bool]:
    """Ids present in a listing, plus whether *every* entry yielded one.

    The second element is the honesty flag for
    :func:`_recover_ambiguous_create`: identifying a draft created by a request
    whose response was lost relies on "this id was not here a moment ago", and
    that inference is only sound when the earlier read produced an id for every
    entry. If it did not, the diff is not evidence and must not be used.
    """
    ids: set[int] = set()
    complete = True
    for episode in _episode_items(data):
        anchor_id = _episode_anchor_id(episode) if isinstance(episode, dict) else None
        if anchor_id is None:
            complete = False
            continue
        ids.add(anchor_id)
    return ids, complete


def _new_untitled_draft_ids(data: Any, known_ids: set[int]) -> tuple[list[int], int]:
    """Untitled drafts in *data* whose id is new, and a count of opaque entries.

    A freshly created draft is *untitled* (:func:`_create_episode` posts only
    ``{"hourOffset": 0}``), so it cannot be found by title — the only handle on
    it is that its id was not in the pre-create listing. Entries that cannot be
    classified (no readable id, unreadable title, unknown state) are counted
    separately: one of them might *be* the created draft, so their presence
    means the recovery has no proof either way.
    """
    candidates: list[int] = []
    opaque = 0
    for episode in _episode_items(data):
        if not isinstance(episode, dict):
            opaque += 1
            continue
        anchor_id = _episode_anchor_id(episode)
        if anchor_id is None or anchor_id in known_ids:
            if anchor_id is None:
                opaque += 1
            continue
        raw_title = next(
            (episode[key] for key in _TITLE_KEYS if episode.get(key) is not None),
            None,
        )
        if raw_title is not None and not isinstance(raw_title, str):
            opaque += 1
            continue
        try:
            is_draft = _episode_is_draft(episode)
        except SpotifyDraftReconcileError:
            opaque += 1
            continue
        if is_draft and (raw_title is None or not raw_title.strip()):
            candidates.append(anchor_id)
    return sorted(candidates), opaque


def _recover_ambiguous_create(
    session: requests.Session,
    station_id: str,
    *,
    user_id: str,
    title: str,
    exclude_id: int | None,
    known_ids: set[int],
    snapshot_complete: bool,
    cause: SpotifyDraftCreateAmbiguousError,
) -> int:
    """Resolve a create whose server-side effect is unknown, using evidence.

    Called only after :func:`_create_episode` has sent **one** POST that failed
    ambiguously. It re-reads the listing (fail-closed) and decides:

    * the target title now matches a draft → adopt it (a concurrent attempt, or
      a server that titles on create);
    * exactly one *new* untitled draft → that is the draft this create made →
      adopt it, and the caller titles it;
    * no new entry at all, from a snapshot known to be complete → the create
      provably did not take effect → send exactly one more POST;
    * anything else (several candidates, opaque entries, an unusable snapshot)
      → raise, naming the candidate ids for the operator. Guessing here is what
      produces orphan untitled drafts.

    At most two create POSTs are ever sent per publish attempt, and the second
    only with positive evidence that the first created nothing.
    """
    logger.warning(
        "Spotify draft create for station %s was ambiguous; re-listing episodes "
        "to identify any draft it may have created before deciding to retry.",
        station_id,
    )
    data = _fetch_episode_listing(session, station_id, user_id=user_id)

    titled_match = _match_existing_draft(data, station_id, title, exclude_id=exclude_id)
    if titled_match is not None:
        logger.info(
            "Ambiguous Spotify draft create resolved to titled draft anchorId=%d.",
            titled_match,
        )
        return titled_match

    candidates, opaque = _new_untitled_draft_ids(data, known_ids)

    if len(candidates) == 1 and not opaque:
        adopted = candidates[0]
        logger.info(
            "Ambiguous Spotify draft create resolved to new untitled draft "
            "anchorId=%d; adopting it instead of creating another.",
            adopted,
        )
        return adopted

    if not candidates and not opaque and snapshot_complete:
        logger.warning(
            "Spotify draft create for station %s left no new draft in the "
            "listing; it provably did not take effect, retrying it once.",
            station_id,
        )
        return _create_episode(session, station_id)

    raise SpotifyDraftReconcileError(
        f"Spotify draft create for station {station_id} failed ambiguously "
        f"({type(cause).__name__}) and the follow-up listing cannot identify "
        f"whether it created a draft (new untitled draft candidates: "
        f"{candidates or 'none'}, unclassifiable entries: {opaque}, pre-create "
        f"snapshot complete: {snapshot_complete}). Refusing to send a second "
        "create that could orphan an untitled duplicate; inspect the drafts for "
        "this show in the Spotify creator UI and retry, or set "
        "PODCASTER_SPOTIFY_RECONCILE=0 to fall back to blind create."
    ) from cause


def _reconcile_or_create_draft(
    session: requests.Session,
    station_id: str,
    *,
    user_id: str,
    title: str,
    exclude_id: int | None = None,
) -> tuple[int, bool]:
    """Return ``(anchor_id, created)`` for the video draft carrying *title*.

    One listing read serves both purposes: it reconciles an existing draft, and
    — when there is none — it is the pre-create snapshot that makes an
    ambiguous create recoverable without a blind retry.
    """
    data = _fetch_episode_listing(session, station_id, user_id=user_id)
    match = _match_existing_draft(data, station_id, title, exclude_id=exclude_id)
    if match is not None:
        return match, False

    known_ids, snapshot_complete = _snapshot_episode_ids(data)
    if exclude_id is not None:
        known_ids.add(exclude_id)
    try:
        return _create_episode(session, station_id), True
    except SpotifyDraftCreateAmbiguousError as exc:
        return (
            _recover_ambiguous_create(
                session,
                station_id,
                user_id=user_id,
                title=title,
                exclude_id=exclude_id,
                known_ids=known_ids,
                snapshot_complete=snapshot_complete,
                cause=exc,
            ),
            True,
        )


def _claim_draft_title(
    session: requests.Session,
    anchor_id: int,
    *,
    user_id: str,
    title: str,
) -> None:
    """Title a freshly created draft **before** any media is uploaded.

    :func:`_create_episode` returns an *untitled* draft, but
    :func:`_find_existing_draft` can only recognise a draft by its title. Until
    the title is set the draft is invisible to reconcile, so a crash anywhere in
    the (multi-minute) signed-URL → multipart upload → process-poll window
    orphans it and the next attempt creates a *second* draft. Claiming the title
    immediately narrows that window from the whole upload to this one request.

    This reuses the ``/v3/episodes/{id}/update`` contract already exercised by
    every successful publish (:func:`_set_metadata`) — no new or guessed fields
    are sent, and the final metadata call still applies the real description and
    numbering. The residual window (create request sent, response not yet
    observed) cannot be closed without a server-side idempotency key, which this
    API does not expose.
    """
    try:
        _set_metadata(
            session,
            anchor_id,
            user_id,
            title=title,
            description="",
            publish_behavior="draft",
            publish_on=None,
        )
    except SpotifyCredentialExpiredError:
        raise
    except (SpotifyPublishError, requests.RequestException) as exc:
        raise SpotifyDraftReconcileError(
            f"Spotify draft {anchor_id} was created but could not be titled "
            f"({type(exc).__name__}), so reconcile can never reuse it. Aborting "
            "before upload rather than orphaning a second untitled draft; delete "
            f"draft {anchor_id} in the Spotify creator UI, or set "
            "PODCASTER_SPOTIFY_RECONCILE=0 to fall back to blind create."
        ) from exc
    logger.info("Claimed title=%r on new Spotify draft anchorId=%d", title, anchor_id)


_VIDEO_CHUNK_SIZE = 30 * 1024 * 1024  # 30MB per chunk for video multipart


def _get_upload_url(
    session: requests.Session,
    anchor_id: int,
    *,
    filename: str,
    content_type: str,
    is_video: bool = False,
    file_size: int = 0,
) -> "tuple[str, str] | tuple[list[dict], str]":
    """Step 3: Get signed upload URL(s). Returns (signed_url, upload_id) for
    audio or (signed_url_parts, request_uuid) for video.

    Video uploads use multipart: each part gets its own signed GCS URL.
    Audio uses a single S3 signed URL.
    """
    url = f"{_BASE_URL}/v3/episodes/{anchor_id}/upload/signedUrl"
    params = _mums_params(filename=filename, type=content_type)
    if is_video:
        import math

        num_parts = max(1, math.ceil(file_size / _VIDEO_CHUNK_SIZE))
        params["uploadType"] = "video"
        params["isMultipartUpload"] = "true"
        params["numParts"] = str(num_parts)
    resp = _retry_request(
        session,
        "GET",
        url,
        params=params,
        timeout=15,
    )
    data = resp.json()
    upload_id = data.get("uploadId") or data["requestUuid"]
    if is_video and "signedUrlParts" in data:
        return data["signedUrlParts"], str(upload_id)
    return data.get("signedUrl") or data["url"], str(upload_id)


def _upload_audio(
    session: requests.Session,
    signed_url: str,
    audio_data: bytes,
    *,
    content_type: str,
) -> str:
    """Upload a single file to a signed URL (S3). Returns ETag."""
    resp = _retry_request(
        session,
        "PUT",
        signed_url,
        data=audio_data,
        headers={
            "Content-Type": content_type,
            "Authorization": None,  # strip bearer token
            **_MUTATION_HEADERS,
        },
        timeout=300,
    )
    etag = resp.headers.get("ETag", "").strip('"')
    logger.info("Uploaded audio (%d bytes, %s), ETag=%s", len(audio_data), content_type, etag)
    return etag


def _upload_video_multipart(
    session: requests.Session,
    signed_url_parts: list[dict],
    video_data: bytes,
) -> list[dict]:
    """Upload video in chunks to GCS multipart signed URLs.

    Returns list of {partNumber, etag} for process_upload.
    GCS signed URLs must NOT receive extra headers (Origin, Referer, Auth).
    """
    parts_etags = []
    for i, part_info in enumerate(signed_url_parts):
        start = i * _VIDEO_CHUNK_SIZE
        end = min(start + _VIDEO_CHUNK_SIZE, len(video_data))
        chunk = video_data[start:end]
        part_url = part_info["url"]

        resp = _retry_request(
            session,
            "PUT",
            part_url,
            data=chunk,
            headers={
                "Authorization": None,
                "Referer": "https://creators.spotify.com/",
            },
            timeout=300,
        )
        etag = resp.headers.get("ETag", "").strip('"')
        parts_etags.append({"partNumber": part_info["partNumber"], "etag": etag})
        logger.info(
            "Uploaded video part %d/%d (%d bytes), ETag=%s",
            part_info["partNumber"],
            len(signed_url_parts),
            len(chunk),
            etag,
        )
    return parts_etags


def _process_upload(
    session: requests.Session,
    upload_id: str,
    *,
    anchor_id: int,
    station_id: str,
    user_id: str,
    filename: str,
    content_type: str = "audio/mpeg",
    parts_etags: list[dict] | None = None,
) -> None:
    """Step 5: Trigger processing and poll until complete."""
    is_video = content_type.startswith("video/")
    # Video uses multipart GCS upload (multiple parts with ETags).
    # Audio uses a single S3 PUT — isMultipartUpload must be False for audio
    # or Anchor's process_upload returns HTTP 500.
    if is_video and not parts_etags:
        raise ValueError("Video uploads require non-empty parts_etags")
    is_multipart = is_video and bool(parts_etags)

    url = f"{_BASE_URL}/v3/upload/{upload_id}/process_upload"
    payload: dict[str, Any] = {
        "userId": int(user_id),
        "uploadType": "video" if is_video else "default",
        "origin": "episode-media:upload",
        "caption": filename,
        "isExtractedFromVideo": False,
        "isMultipartUpload": is_multipart,
        "uploadId": upload_id,
        "episodeId": anchor_id,
        "stationId": int(station_id),
    }
    if is_multipart and parts_etags:
        payload["parts"] = parts_etags

    _retry_request(
        session,
        "POST",
        url,
        headers=_MUTATION_HEADERS,
        params=_mums_params(),
        json=payload,
        timeout=30,
    )

    # Poll for completion; tolerate 404 (media may not be visible immediately)
    # Use exponential backoff for 404s (known Spotify transient quirk)
    status_url = f"{_BASE_URL}/v3/upload/media/{upload_id}"
    backoff = _POLL_INTERVAL
    for attempt in range(_POLL_MAX_ATTEMPTS):
        time.sleep(backoff)
        try:
            resp = session.request(
                "GET",
                status_url,
                params=_mums_params(includeMediaValidation="true"),
                timeout=15,
            )
            if resp.status_code == 404:
                logger.debug(
                    "Upload %s status poll 404 (not ready), attempt %d",
                    upload_id,
                    attempt + 1,
                )
                backoff = min(backoff * 1.5, 30)  # backoff up to 30s between polls
                continue
            resp.raise_for_status()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in {401, 403}:
                raise SpotifyCredentialExpiredError(
                    "Spotify rejected the request (HTTP "
                    f"{exc.response.status_code}) — SP_DC/SP_KEY credentials "
                    "expired. Operator must refresh them."
                ) from exc
            raise SpotifyPublishError(f"Upload {upload_id} status poll failed: {exc}") from exc
        backoff = _POLL_INTERVAL  # reset on success
        data = resp.json()
        # Status is in data.request.state (not top-level "status")
        request_data = data.get("request", data)
        status = request_data.get("state") or data.get("status", "")
        if status in ("processed", "completed"):
            # Check mediaValidation for video
            validation = data.get("mediaValidation", {})
            if validation.get("status") == "validation_failure":
                logger.debug(
                    "Upload %s full response on validation failure: %s",
                    upload_id,
                    json.dumps(data),
                )
                reasons = [r.get("reason", "unknown") for r in validation.get("failures", [])]
                error_code = validation.get("failureInfo", {}).get("errorCode")
                failure_info = validation.get("failureInfo")
                detail = f"{reasons}"
                if error_code:
                    detail += f" (errorCode={error_code})"
                if failure_info:
                    detail += f" failureInfo={failure_info}"
                raise SpotifyPublishError(f"Upload {upload_id} media validation failed: {detail}")
            logger.info("Upload %s processing completed (state=%s)", upload_id, status)
            return
        elif status == "failed":
            logger.debug(
                "Upload %s full response on failure: %s",
                upload_id,
                json.dumps(data),
            )
            reason = request_data.get("failureReason") or "unknown"
            # Also check mediaValidation for details
            validation = data.get("mediaValidation", {})
            failures = [r.get("reason", "") for r in validation.get("failures", [])]
            # Spotify returns the actual error at mediaValidation.failureInfo.errorCode
            failure_info = validation.get("failureInfo", {})
            error_code = failure_info.get("errorCode")
            detail = f"{reason}"
            if error_code:
                detail += f" (errorCode={error_code})"
            if failures:
                detail += f" (validation: {failures})"
            if failure_info:
                detail += f" failureInfo={failure_info}"
            raise SpotifyPublishError(f"Upload {upload_id} processing failed: {detail}")
        logger.debug("Upload %s status: %s (attempt %d)", upload_id, status, attempt + 1)

    raise SpotifyPublishError(
        f"Upload {upload_id} processing timed out after {_POLL_MAX_ATTEMPTS * _POLL_INTERVAL}s"
    )


def _set_metadata(
    session: requests.Session,
    anchor_id: int,
    user_id: str,
    title: str,
    description: str,
    publish_behavior: str,
    publish_on: datetime | None,
    season_number: int | None = None,
    episode_number: int | None = None,
    episode_type: str = "full",
    explicit: bool = False,
) -> None:
    """Step 6: Set episode metadata."""
    url = f"{_BASE_URL}/v3/episodes/{anchor_id}/update"
    payload: dict[str, Any] = {
        "userId": int(user_id),
        "title": title,
        "description": description,
        "episodeType": episode_type,
        "isPublished": publish_behavior == "immediate",
        "podcastEpisodeIsExplicit": explicit,
    }
    if publish_behavior == "scheduled" and publish_on is not None:
        publish_on_utc = publish_on.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        payload["publishOn"] = publish_on_utc
        payload["wizardDraftedToPublishOn"] = publish_on_utc
    if season_number is not None:
        payload["seasonNumber"] = season_number
    if episode_number is not None:
        payload["episodeNumber"] = episode_number
    _retry_request(
        session,
        "POST",
        url,
        headers=_MUTATION_HEADERS,
        params=_mums_params(),
        json=payload,
        timeout=15,
    )
    logger.info(
        "Metadata set for episode %d: %s",
        anchor_id,
        title,
    )


def _publish_episode_live(
    session: requests.Session,
    anchor_id: int,
    publish_on: datetime | None = None,
) -> None:
    """Step 7: Publish or schedule an episode."""
    url = f"{_BASE_URL}/v3/episodes/{anchor_id}/publish?isMumsCompatible=true"
    payload: dict[str, Any] = {}
    if publish_on:
        payload["publishOn"] = publish_on.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _retry_request(
        session,
        "POST",
        url,
        headers=_MUTATION_HEADERS,
        json=payload,
        timeout=15,
    )
    logger.info(
        "Episode %d publish requested (%s)",
        anchor_id,
        publish_on or "immediate",
    )


def upload_video_to_episode(
    video_path: Path,
    anchor_id: int | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
    content_type: str = "video/mp4",
    show_id: str | None = None,
    sp_dc: str | None = None,
    sp_key: str | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> PublishResult:
    """Publish a video as a NEW separate Spotify episode draft (#340).

    Spotify rejects attaching a video to an episode that already holds audio
    (``process_upload`` returns ``state=failed``). To work around this the video
    is published as its own brand-new draft episode: create a draft, upload the
    MP4 as the episode's primary media, process the upload, and set metadata.

    The existing audio episode (``anchor_id``, kept only for logging/reference)
    is never modified — the result is two independent drafts on Spotify, one
    audio and one video.

    ``season_number`` and ``episode_number`` are forwarded to Spotify's episode
    metadata so the video episode carries the same numbering as the audio episode.

    Returns a PublishResult; ``anchor_episode_id`` is the NEW video episode id,
    status is "draft" on success and "failed" otherwise.
    """
    video_title = title or "Video Episode"
    video_description = description or ""

    if _is_dry_run():
        logger.info(
            "DRY RUN: Would create new video episode draft '%s' for %s (%s); "
            "audio episode anchorId=%s left untouched",
            video_title,
            video_path,
            content_type,
            anchor_id,
        )
        return PublishResult(
            anchor_episode_id=None,
            status="draft",
            dry_run=True,
            details={
                "upload_path": str(video_path),
                "content_type": content_type,
                "title": video_title,
                "audio_anchor_id": anchor_id,
            },
        )

    if not video_path.exists() or video_path.stat().st_size == 0:
        return PublishResult(status="failed", error=f"Video file not found or empty: {video_path}")

    try:
        env_show_id, env_sp_dc, env_sp_key = _get_credentials()
        show_id = show_id or env_show_id
        sp_dc = sp_dc or env_sp_dc
        sp_key = sp_key or env_sp_key
    except ValueError as exc:
        return PublishResult(status="failed", error=str(exc))

    try:
        session = _build_session(sp_dc, sp_key, show_id)
        station_id, user_id = _resolve_legacy_ids(session, show_id)

        # Create or reconcile a separate video draft — never touch the audio one.
        reconcile_enabled = bool(title) and _spotify_reconcile_enabled()
        if reconcile_enabled:
            try:
                exclude_audio_id = int(anchor_id) if anchor_id is not None else None
            except (TypeError, ValueError):
                exclude_audio_id = None
            video_anchor_id, draft_created = _reconcile_or_create_draft(
                session,
                station_id,
                user_id=user_id,
                title=video_title,
                exclude_id=exclude_audio_id,
            )
        else:
            video_anchor_id, draft_created = _create_episode(session, station_id), True

        if draft_created and reconcile_enabled:
            # A new draft is created untitled; title it now so a crash during
            # the upload below leaves a draft reconcile can find on retry.
            _claim_draft_title(
                session,
                video_anchor_id,
                user_id=user_id,
                title=video_title,
            )
        elif not draft_created:
            logger.info(
                "Reusing reconciled Spotify video draft anchorId=%d for title=%r",
                video_anchor_id,
                video_title,
            )

        file_data = video_path.read_bytes()
        upload_result = _get_upload_url(
            session,
            video_anchor_id,
            filename=video_path.name,
            content_type=content_type,
            is_video=True,
            file_size=len(file_data),
        )
        signed_url_parts, upload_id = upload_result  # type: ignore[misc]
        parts_etags = _upload_video_multipart(session, signed_url_parts, file_data)

        _process_upload(
            session,
            upload_id,
            anchor_id=video_anchor_id,
            station_id=station_id,
            user_id=user_id,
            filename=video_path.name,
            content_type=content_type,
            parts_etags=parts_etags,
        )

        _set_metadata(
            session,
            video_anchor_id,
            user_id,
            title=video_title,
            description=video_description,
            publish_behavior="draft",
            publish_on=None,
            season_number=season_number,
            episode_number=episode_number,
        )

        logger.info(
            "Video published as new episode draft anchorId=%d "
            "(audio episode anchorId=%s untouched, %d bytes)",
            video_anchor_id,
            anchor_id,
            len(file_data),
        )
        return PublishResult(
            anchor_episode_id=video_anchor_id,
            status="draft",
            details={
                "station_id": station_id,
                "upload_id": upload_id,
                "content_type": content_type,
                "audio_anchor_id": anchor_id,
                "title": video_title,
            },
        )
    except SpotifyCredentialExpiredError as exc:
        logger.error(
            "Spotify video upload failed — credentials expired: %s. "
            "Opening credential-expiry notification.",
            exc,
        )
        try:
            from podcaster.credential_expiry import notify_credential_expiry

            issue_number = notify_credential_expiry(str(exc))
        except Exception:  # pragma: no cover - defensive; notify never raises
            logger.warning("credential-expiry notification failed", exc_info=True)
            issue_number = None
        return PublishResult(
            status="failed",
            error=str(exc),
            details={
                "credentials_expired": True,
                "notification_issue": issue_number,
                "audio_anchor_id": anchor_id,
            },
        )
    except SpotifyPublishError as exc:
        logger.error("Spotify video upload failed: %s", exc)
        return PublishResult(status="failed", error=str(exc))
    except Exception as exc:
        safe_msg = re.sub(r"https?://\S+", lambda m: _safe_url(m.group()), str(exc))
        logger.error("Unexpected error during Spotify video upload: %s", safe_msg)
        return PublishResult(status="failed", error=f"Unexpected: {safe_msg}")


def _safe_resolve_number(
    label: str,
    resolver,
    *,
    year: int,
    week: int,
    fallback: int | None,
) -> int | None:
    try:
        return int(resolver(year=year, week=week))
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        logger.warning("Spotify publish %s template failed; using fallback: %s", label, exc)
        return fallback


def _resolve_publish_inputs(
    title: str,
    description: str,
    publish_on: datetime | None,
    spotify_publish_config: SpotifyPublishConfig | None,
    *,
    year: int | None,
    week: int | None,
    article_title: str | None,
    article_summary: str | None,
) -> tuple[str, str, int | None, int | None, str, datetime | None, str]:
    if spotify_publish_config is None:
        return title, description, None, None, "immediate", None, "wav"

    resolved_title = spotify_publish_config.title or title
    resolved_description = spotify_publish_config.description or description
    resolved_season: int | None = None
    resolved_episode: int | None = None

    if year is not None and week is not None:
        resolved_season = _safe_resolve_number(
            "season",
            spotify_publish_config.resolve_season,
            year=year,
            week=week,
            fallback=year,
        )
        resolved_episode = _safe_resolve_number(
            "episode",
            spotify_publish_config.resolve_episode,
            year=year,
            week=week,
            fallback=week,
        )

    publish_mode_raw = spotify_publish_config.publish_mode.strip()
    publish_mode = publish_mode_raw.lower()
    if publish_mode == "draft":
        return (
            resolved_title,
            resolved_description,
            resolved_season,
            resolved_episode,
            "draft",
            None,
            spotify_publish_config.upload_format,
        )
    if publish_mode == "immediate":
        return (
            resolved_title,
            resolved_description,
            resolved_season,
            resolved_episode,
            "immediate",
            None,
            spotify_publish_config.upload_format,
        )

    try:
        parsed_publish_on = datetime.fromisoformat(publish_mode_raw.replace("Z", "+00:00"))
        if parsed_publish_on.tzinfo is None:
            parsed_publish_on = parsed_publish_on.replace(tzinfo=timezone.utc)
        return (
            resolved_title,
            resolved_description,
            resolved_season,
            resolved_episode,
            "scheduled",
            parsed_publish_on,
            spotify_publish_config.upload_format,
        )
    except ValueError as exc:
        logger.warning(
            "Spotify publish_mode %r is invalid; using fallback publish behavior: %s",
            spotify_publish_config.publish_mode,
            exc,
        )
        if publish_on is not None:
            return (
                resolved_title,
                resolved_description,
                resolved_season,
                resolved_episode,
                "scheduled",
                publish_on,
                spotify_publish_config.upload_format,
            )
        return (
            resolved_title,
            resolved_description,
            resolved_season,
            resolved_episode,
            "immediate",
            None,
            spotify_publish_config.upload_format,
        )


def inject_timestamps_into_description(
    description: str,
    timestamps_html: str,
    max_length: int | None = None,
) -> str:
    """Append timestamps HTML to the episode description if within char limit.

    If the combined description would exceed ``max_length``, the original
    description is returned unchanged (timestamps are dropped rather than
    truncating the description body).
    """
    limit = max_length if max_length is not None else MAX_SPOTIFY_DESCRIPTION_CHARS
    if not timestamps_html:
        return description
    combined = f"{description}{timestamps_html}"
    if len(combined) > limit:
        return description
    return combined


def publish_episode(
    mp3_path: Path,
    title: str,
    description: str,
    show_id: str | None = None,
    sp_dc: str | None = None,
    sp_key: str | None = None,
    publish_on: datetime | None = None,
    episode_type: str = "full",
    explicit: bool = False,
    spotify_publish_config: SpotifyPublishConfig | None = None,
    year: int | None = None,
    week: int | None = None,
    article_title: str | None = None,
    article_summary: str | None = None,
    *,
    wav_path: Path | None = None,
    timestamps_html: str = "",
    language: str = "en",
    language_config: object | None = None,
) -> PublishResult:
    """Publish an episode to Spotify for Creators.

    This function is designed to never raise — it catches all exceptions and
    returns a :class:`PublishResult` with status="failed" and the error message.
    This ensures publish failures never break the generation pipeline.

    Args:
        mp3_path: Path to the distribution MP3 artifact.
        title: Episode title.
        description: Episode description (HTML format).
        show_id: Spotify show ID (defaults to SPOTIFY_SHOW_ID env).
        sp_dc: Session cookie (defaults to SP_DC env).
        sp_key: Session cookie (defaults to SP_KEY env).
        publish_on: Schedule for this datetime (None = immediate).
        episode_type: "full", "trailer", or "bonus".
        explicit: Whether the episode has explicit content.
        spotify_publish_config: Optional Spotify metadata/publish config.
        year: Episode year context for config template resolution.
        week: Episode ISO week context for config template resolution.
        article_title: Source article title for config template resolution.
        article_summary: Source article summary for config template resolution.
        wav_path: Optional WAV artifact path for Spotify upload.
        timestamps_html: Pre-formatted HTML timestamps block to append to
            the episode description (from :func:`~podcaster.episode.format_timestamps_html`).

    Returns:
        PublishResult with status and any error details.
    """
    if not _is_enabled():
        return PublishResult(
            status="failed",
            error="Spotify publishing disabled (SPOTIFY_PUBLISH_ENABLED != true).",
        )

    (
        resolved_title,
        resolved_description,
        season_number,
        episode_number,
        publish_behavior,
        resolved_publish_on,
        upload_format,
    ) = _resolve_publish_inputs(
        title,
        description,
        publish_on,
        spotify_publish_config,
        year=year,
        week=week,
        article_title=article_title,
        article_summary=article_summary,
    )

    # Fail safe: never make an episode public unless live publishing is
    # explicitly enabled. The Spotify path uses an unofficial, cookie-authed
    # API (#602), so any immediate/scheduled request is downgraded to a draft
    # unless an operator has accepted that risk via SPOTIFY_ALLOW_LIVE_PUBLISH.
    if publish_behavior != "draft" and not _live_publish_allowed():
        _warn_live_publish_downgraded_once()
        publish_behavior = "draft"
        resolved_publish_on = None

    # Append timestamps to description if provided and within Spotify's limit
    if timestamps_html:
        resolved_description = inject_timestamps_into_description(
            resolved_description, timestamps_html
        )

    # Detect video artifact — prefer MP4 when present and non-empty.
    video_path: Path | None = None
    if mp3_path is not None:
        candidate_mp4 = mp3_path.parent / (mp3_path.stem + ".mp4")
        if candidate_mp4.exists() and candidate_mp4.stat().st_size > 0:
            video_path = candidate_mp4
            logger.info(
                "Video artifact found (%s, %.1f MB) — preferring MP4 for Spotify upload.",
                candidate_mp4.name,
                candidate_mp4.stat().st_size / 1_048_576,
            )

    if video_path is not None:
        upload_path: Path | None = video_path
        content_type = "video/mp4"
        format_label = "MP4"
    else:
        upload_path = wav_path if upload_format == "wav" else mp3_path
        content_type = "audio/wav" if upload_format == "wav" else "audio/mpeg"
        format_label = "WAV" if upload_format == "wav" else "MP3"

    # Dry-run mode
    if _is_dry_run():
        target = resolve_show_target(language, language_config=language_config)
        logger.info(
            "DRY RUN: Would publish %s as '%s' to show '%s' (lang=%s, tag=%s, %s, "
            "format=%s, content_type=%s)",
            upload_path,
            resolved_title,
            target.show_name,
            target.language,
            target.language_tag,
            publish_behavior,
            format_label,
            content_type,
        )
        return PublishResult(
            anchor_episode_id=None,
            status=(
                "draft"
                if publish_behavior == "draft"
                else ("scheduled" if resolved_publish_on else "published")
            ),
            dry_run=True,
            details={
                "title": resolved_title,
                "mp3_path": str(mp3_path),
                "wav_path": str(wav_path) if wav_path else None,
                "upload_path": str(upload_path) if upload_path else None,
                "upload_format": format_label.lower(),
                "content_type": content_type,
                "publish_behavior": publish_behavior,
                "language": target.language,
                "language_tag": target.language_tag,
                "show_name": target.show_name,
            },
        )

    # Resolve credentials
    try:
        env_show_id, env_sp_dc, env_sp_key = _get_credentials(
            language, language_config=language_config
        )
        show_id = show_id or env_show_id
        sp_dc = sp_dc or env_sp_dc
        sp_key = sp_key or env_sp_key
    except ValueError as exc:
        return PublishResult(status="failed", error=str(exc))

    if upload_path is None or not upload_path.exists():
        return PublishResult(status="failed", error=f"{format_label} file not found: {upload_path}")

    try:
        session = _build_session(sp_dc, sp_key, show_id)

        # Step 1: Resolve IDs
        station_id, user_id = _resolve_legacy_ids(session, show_id)

        # Step 2: Create draft episode
        anchor_id = _create_episode(session, station_id)

        # Step 3 & 4: Upload file (video uses multipart GCS, audio uses single S3)
        is_video = content_type.startswith("video/")
        file_data = upload_path.read_bytes()

        if is_video:
            upload_result = _get_upload_url(
                session,
                anchor_id,
                filename=upload_path.name,
                content_type=content_type,
                is_video=True,
                file_size=len(file_data),
            )
            signed_url_parts, upload_id = upload_result  # type: ignore[misc]
            parts_etags = _upload_video_multipart(session, signed_url_parts, file_data)
        else:
            signed_url, upload_id = _get_upload_url(
                session,
                anchor_id,
                filename=upload_path.name,
                content_type=content_type,
            )
            etag = _upload_audio(session, signed_url, file_data, content_type=content_type)
            parts_etags = [{"partNumber": 1, "etag": etag}]

        # Step 5: Process upload
        _process_upload(
            session,
            upload_id,
            anchor_id=anchor_id,
            station_id=station_id,
            user_id=user_id,
            filename=upload_path.name,
            content_type=content_type,
            parts_etags=parts_etags if is_video else None,
        )

        # Step 6: Set metadata
        _set_metadata(
            session,
            anchor_id,
            user_id,
            title=resolved_title,
            description=resolved_description,
            publish_behavior=publish_behavior,
            publish_on=resolved_publish_on,
            season_number=season_number,
            episode_number=episode_number,
            episode_type=episode_type,
            explicit=explicit,
        )
        if publish_behavior != "draft":
            _publish_episode_live(session, anchor_id, resolved_publish_on)

        status = (
            "draft"
            if publish_behavior == "draft"
            else ("scheduled" if resolved_publish_on else "published")
        )
        logger.info(
            "Episode published to Spotify: anchorId=%d status=%s",
            anchor_id,
            status,
        )
        return PublishResult(
            anchor_episode_id=anchor_id,
            status=status,
            details={"station_id": station_id, "upload_id": upload_id},
        )

    except SpotifyCredentialExpiredError as exc:
        logger.error(
            "Spotify publish failed — credentials expired: %s. "
            "Opening credential-expiry notification.",
            exc,
        )
        try:
            from podcaster.credential_expiry import notify_credential_expiry

            issue_number = notify_credential_expiry(str(exc))
        except Exception:  # pragma: no cover - defensive; notify never raises
            logger.warning("credential-expiry notification failed", exc_info=True)
            issue_number = None
        return PublishResult(
            status="failed",
            error=str(exc),
            details={
                "credentials_expired": True,
                "notification_issue": issue_number,
            },
        )
    except SpotifyPublishError as exc:
        logger.error("Spotify publish failed: %s", exc)
        return PublishResult(status="failed", error=str(exc))
    except Exception as exc:
        safe_msg = re.sub(r"https?://\S+", lambda m: _safe_url(m.group()), str(exc))
        logger.error("Unexpected error during Spotify publish: %s", safe_msg)
        return PublishResult(status="failed", error=f"Unexpected: {safe_msg}")
