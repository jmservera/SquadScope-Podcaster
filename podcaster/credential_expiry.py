"""Notify operators when Spotify credentials (SP_DC/SP_KEY) expire (#364).

When the Spotify for Creators API rejects a request with HTTP 401/403, the
``SP_DC`` / ``SP_KEY`` browser cookies have expired and uploads will keep
failing until an operator refreshes them. This module opens a GitHub issue
(via the ``gh`` CLI) with actionable refresh instructions so the failure is
explicit rather than silent.

Deduplication: before creating a new issue it searches for an existing open
issue carrying the ``credentials-expired`` label and a matching title. If one
is found, no duplicate is created.

The repository is inferred from ``GITHUB_REPOSITORY`` (``owner/repo``) or
defaults to the canonical ``jmservera/SquadScope-Podcaster``. Requires the
``gh`` CLI to be installed and authenticated (a ``GH_TOKEN`` / ``GITHUB_TOKEN``
in the environment is sufficient).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone

logger = logging.getLogger("podcaster.credential_expiry")

CREDENTIALS_EXPIRED_LABEL = "credentials-expired"
DEFAULT_REPO = "jmservera/SquadScope-Podcaster"
ISSUE_TITLE = "[Spotify] SP_DC/SP_KEY credentials expired — refresh required"
YOUTUBE_ISSUE_TITLE = "[YouTube] OAuth refresh token revoked/expired — re-authentication required"

_GH_TIMEOUT = 30


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO).strip() or DEFAULT_REPO


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=_GH_TIMEOUT,
    )


def _find_open_issue(repo: str, title: str = ISSUE_TITLE) -> int | None:
    """Return the number of an open ``credentials-expired`` issue, or None."""
    try:
        result = _run_gh(
            [
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--label",
                CREDENTIALS_EXPIRED_LABEL,
                "--json",
                "number,title",
                "--limit",
                "30",
            ]
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        logger.warning("failed to list existing credential-expiry issues", exc_info=True)
        return None

    try:
        issues = json.loads(result.stdout or "[]")
    except ValueError:
        return None
    if not isinstance(issues, list):
        return None
    for issue in issues:
        if issue.get("title") == title:
            return int(issue["number"])
    return None


def _ensure_label(repo: str) -> None:
    """Best-effort creation of the ``credentials-expired`` label."""
    try:
        _run_gh(
            [
                "label",
                "create",
                CREDENTIALS_EXPIRED_LABEL,
                "--repo",
                repo,
                "--color",
                "B60205",
                "--description",
                "Spotify SP_DC/SP_KEY cookies expired — refresh required",
            ]
        )
    except subprocess.CalledProcessError:
        # Label most likely already exists — that's fine.
        logger.debug("credentials-expired label already exists or could not be created")
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("failed to ensure credentials-expired label", exc_info=True)


def build_issue_body(error_message: str, *, timestamp: str | None = None) -> str:
    """Build the Markdown body with credential-refresh instructions."""
    now = timestamp or (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    return "\n".join(
        [
            "## Spotify credentials expired",
            "",
            "The Spotify for Creators API rejected a publish request because the "
            "`SP_DC` / `SP_KEY` browser cookies have expired. Episode uploads "
            "will keep failing until these are refreshed.",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| **Detected at** | {now} |",
            "| **Detector** | `podcaster.publish` Spotify upload flow |",
            "",
            "### Error",
            "```",
            (error_message or "(no message)")[:2000],
            "```",
            "",
            "### How to refresh SP_DC / SP_KEY",
            "",
            "1. Open <https://creators.spotify.com> in a browser and log in to "
            "the show owner account.",
            "2. Open the browser developer tools → **Application** (Chrome) or "
            "**Storage** (Firefox) → **Cookies** → `https://creators.spotify.com`.",
            "3. Copy the values of the `sp_dc` and `sp_key` cookies.",
            "4. Update the Azure Container App environment variables `SP_DC` and "
            "`SP_KEY` with the new values, for example:",
            "",
            "```bash",
            "az containerapp update \\",
            "  --name <podcaster-app> \\",
            "  --resource-group <resource-group> \\",
            '  --set-env-vars "SP_DC=<new-sp_dc>" "SP_KEY=<new-sp_key>"',
            "```",
            "",
            "5. Re-run the failed publish, or wait for the next scheduled run.",
            "",
            "> **Note:** `sp_dc` cookies typically expire after about a year, but "
            "they can be invalidated sooner by a password change or Spotify "
            "session revocation.",
            "",
            "---",
            "_Automatically reported by the Spotify credential-expiry detector (#364)._",
        ]
    )


def notify_credential_expiry(error_message: str) -> int | None:
    """Open a GitHub issue notifying operators of expired Spotify credentials.

    Returns the issue number on success. If a matching open issue already
    exists, returns that existing issue's number instead of creating a
    duplicate. Returns ``None`` if notification was skipped (``gh``
    unavailable / disabled) or an error occurred. Never raises —
    credential-expiry notification must not break the publish pipeline.
    """
    if os.environ.get("CREDENTIAL_EXPIRY_NOTIFY_DISABLED", "").lower() == "true":
        logger.info("credential-expiry notification disabled via env; skipping")
        return None

    if not _gh_available():
        logger.warning(
            "gh CLI not available — cannot open credential-expiry issue. "
            "Refresh SP_DC/SP_KEY manually."
        )
        return None

    repo = _repo()
    try:
        existing = _find_open_issue(repo)
        if existing is not None:
            logger.info(
                "credential-expiry issue already open (#%d) — not creating a duplicate",
                existing,
            )
            return existing

        _ensure_label(repo)
        body = build_issue_body(error_message)
        result = _run_gh(
            [
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                ISSUE_TITLE,
                "--body",
                body,
                "--label",
                CREDENTIALS_EXPIRED_LABEL,
            ]
        )
        issue_url = (result.stdout or "").strip()
        logger.error("Spotify credentials expired — opened GitHub issue: %s", issue_url)
        number = _parse_issue_number(issue_url)
        return number
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        logger.warning("failed to open credential-expiry issue via gh CLI", exc_info=True)
        return None


def _parse_issue_number(issue_url: str) -> int | None:
    """Extract the trailing issue number from a ``gh issue create`` URL."""
    if not issue_url:
        return None
    tail = issue_url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


# --- YouTube OAuth refresh-token expiry/revocation (#443) ---------------------


def build_youtube_issue_body(error_message: str, *, timestamp: str | None = None) -> str:
    """Build the Markdown body with YouTube re-authentication instructions."""
    now = timestamp or (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    return "\n".join(
        [
            "## YouTube OAuth refresh token revoked/expired",
            "",
            "Google rejected the stored YouTube OAuth2 refresh token "
            "(`invalid_grant`). Access tokens can no longer be minted, so video "
            "uploads to YouTube will keep failing until the token is renewed.",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| **Detected at** | {now} |",
            "| **Detector** | `podcaster.youtube_credentials` token refresh |",
            "",
            "### Error",
            "```",
            (error_message or "(no message)")[:2000],
            "```",
            "",
            "### Why this happens",
            "",
            "A YouTube refresh token only stops working if it is revoked — e.g. "
            "the Google account password changed, consent was withdrawn, the "
            "OAuth client secret was rotated, or the token went unused for 6 "
            "months. It does **not** expire on a fixed schedule otherwise.",
            "",
            "### How to re-authenticate",
            "",
            "1. Run the one-time consent flow to mint a fresh refresh token "
            "(see `docs/youtube-oauth-setup.md` / `scripts/youtube_oauth_setup.py`).",
            "2. Store the new refresh token per `docs/youtube-token-storage.md`. "
            "For this repo's production deployment, update the `prod` GitHub "
            "environment secret `VIDEO_YOUTUBE_REFRESH_TOKEN` and redeploy — "
            "this app injects that secret directly and returns it before ever "
            "consulting Key Vault, so updating Key Vault alone would **not** "
            "take effect and would leave the revoked token in place. Only "
            "update Key Vault directly if this deployment is instead "
            "configured for the direct Key Vault runtime-resolution path "
            "(`VIDEO_YOUTUBE_KEYVAULT_URL` set, no `VIDEO_YOUTUBE_REFRESH_TOKEN` "
            "env var injected):",
            "",
            "```bash",
            "az keyvault secret set \\",
            "  --vault-name <vault> \\",
            "  --name youtube-oauth-refresh-token \\",
            '  --value "<new-refresh-token>"',
            "```",
            "",
            "3. Re-run the failed video distribution, or wait for the next run. "
            "For the GitHub-secret path, a redeploy is required to pick up the "
            "new value. For the direct Key Vault path, no code or app restart "
            "is required — the token is read from Key Vault at runtime.",
            "",
            "---",
            "_Automatically reported by the YouTube credential-expiry detector (#443)._",
        ]
    )


def notify_youtube_credential_expiry(error_message: str) -> int | None:
    """Open a GitHub issue alerting operators that the YouTube token is revoked.

    Mirrors :func:`notify_credential_expiry` (de-duplicates against an existing
    open issue, never raises). Returns the issue number or ``None``.
    """
    if os.environ.get("CREDENTIAL_EXPIRY_NOTIFY_DISABLED", "").lower() == "true":
        logger.info("credential-expiry notification disabled via env; skipping")
        return None

    if not _gh_available():
        logger.warning(
            "gh CLI not available — cannot open YouTube re-auth issue. "
            "Re-authenticate the YouTube OAuth token manually."
        )
        return None

    repo = _repo()
    try:
        existing = _find_open_issue(repo, YOUTUBE_ISSUE_TITLE)
        if existing is not None:
            logger.info(
                "YouTube re-auth issue already open (#%d) — not creating a duplicate",
                existing,
            )
            return existing

        _ensure_label(repo)
        body = build_youtube_issue_body(error_message)
        result = _run_gh(
            [
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                YOUTUBE_ISSUE_TITLE,
                "--body",
                body,
                "--label",
                CREDENTIALS_EXPIRED_LABEL,
            ]
        )
        issue_url = (result.stdout or "").strip()
        logger.error("YouTube refresh token revoked — opened GitHub issue: %s", issue_url)
        return _parse_issue_number(issue_url)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        logger.warning("failed to open YouTube re-auth issue via gh CLI", exc_info=True)
        return None
