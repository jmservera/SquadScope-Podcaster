"""Report ACA container failures as GitHub issues (#237).

When a container app (API or Synth) encounters an unhandled failure, this
module creates or updates a GitHub issue in the source repository so the
team gets automated visibility without checking Azure portal.

Deduplication: searches for an existing open issue with the
``aca-failure`` label and matching container name in the title. If found,
appends a comment instead of creating a duplicate.

Requires ``GITHUB_TOKEN`` in the environment (already provisioned in ACA).
The repository is inferred from ``GITHUB_REPOSITORY`` or defaults to the
canonical ``jmservera/SquadScope-Podcaster``.
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("podcaster.failure_reporting")

FAILURE_LABEL = "aca-failure"
DEFAULT_REPO = "jmservera/SquadScope-Podcaster"
GITHUB_API = "https://api.github.com"


def _github_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN")


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json; charset=utf-8",
    }


def _api_request(
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
    *,
    transport=None,
) -> dict[str, Any]:
    """Make a GitHub API request.  *transport* is injectable for tests."""
    url = f"{GITHUB_API}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = Request(url, data=data, method=method, headers=_headers(token))
    send = transport or urlopen
    with send(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _find_open_issue(
    container: str,
    token: str,
    repo: str,
    *,
    transport=None,
) -> int | None:
    """Return the issue number of an existing open ``aca-failure`` issue.

    Returns None when there is no existing issue for *container*.
    """
    path = (
        f"/repos/{repo}/issues"
        f"?labels={FAILURE_LABEL}&state=open&per_page=30"
    )
    try:
        issues = _api_request("GET", path, token, transport=transport)
    except (URLError, OSError, ValueError):
        logger.warning("failed to search for existing failure issues", exc_info=True)
        return None

    if not isinstance(issues, list):
        return None

    prefix = f"[ACA failure] {container}:"
    for issue in issues:
        title = issue.get("title", "")
        if title.startswith(prefix):
            return int(issue["number"])
    return None


def _build_issue_body(
    container: str,
    error_type: str,
    error_message: str,
    timestamp: str,
    details: dict[str, Any] | None = None,
) -> str:
    lines = [
        "## ACA Container Failure",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Container** | `{container}` |",
        f"| **Failure type** | `{error_type}` |",
        f"| **Timestamp** | {timestamp} |",
        "",
        "### Error",
        "```",
        error_message[:2000],
        "```",
    ]
    if details:
        lines += [
            "",
            "### Details",
            "```json",
            json.dumps(details, indent=2, default=str)[:2000],
            "```",
        ]
    lines += [
        "",
        "---",
        "_Automatically reported by the ACA failure reporter (#237)._",
    ]
    return "\n".join(lines)


def _build_comment_body(
    error_type: str,
    error_message: str,
    timestamp: str,
    details: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"### Recurrence at {timestamp}",
        "",
        f"**Failure type:** `{error_type}`",
        "",
        "```",
        error_message[:2000],
        "```",
    ]
    if details:
        lines += [
            "",
            "```json",
            json.dumps(details, indent=2, default=str)[:2000],
            "```",
        ]
    return "\n".join(lines)


def report_failure(
    *,
    container: str,
    error: BaseException | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    details: dict[str, Any] | None = None,
    transport=None,
) -> int | None:
    """Create or update a GitHub issue for an ACA container failure.

    Returns the issue number on success, or ``None`` if reporting was
    skipped (no token) or failed (network error).

    *transport* is injectable for tests — a callable matching
    ``urlopen(request)`` that returns a context-manager response.
    """

    token = _github_token()
    if not token:
        logger.debug("GITHUB_TOKEN not set; skipping failure reporting")
        return None

    repo = _repo()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if error is not None:
        error_type = error_type or type(error).__name__
        error_message = error_message or "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    error_type = error_type or "UnknownError"
    error_message = error_message or "(no message)"

    try:
        existing = _find_open_issue(container, token, repo, transport=transport)
        if existing is not None:
            comment = _build_comment_body(error_type, error_message, now, details)
            _api_request(
                "POST",
                f"/repos/{repo}/issues/{existing}/comments",
                token,
                {"body": comment},
                transport=transport,
            )
            logger.info("updated existing failure issue #%d for container=%s", existing, container)
            return existing

        title = f"[ACA failure] {container}: {error_type}"
        if len(title) > 256:
            title = title[:253] + "..."
        body = _build_issue_body(container, error_type, error_message, now, details)
        result = _api_request(
            "POST",
            f"/repos/{repo}/issues",
            token,
            {"title": title, "body": body, "labels": [FAILURE_LABEL]},
            transport=transport,
        )
        issue_number = int(result.get("number", 0))
        logger.info("created failure issue #%d for container=%s", issue_number, container)
        return issue_number

    except Exception:
        logger.warning("failed to report ACA failure to GitHub", exc_info=True)
        return None
