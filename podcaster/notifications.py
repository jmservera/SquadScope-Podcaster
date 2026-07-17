"""Push failure alerts to a configurable webhook (Teams / Slack / generic) (#473).

Existing failure surfaces — :mod:`podcaster.failure_reporting` (GitHub issues)
and the durable manifest status — require someone to *go look*. This module
adds an active *push* channel so a failed job pings a chat/incident webhook
within seconds, carrying the job id, the failed stage, an error summary, and a
deep link back to the job in the monitoring UI.

Part of the Phase 5 Observability epic (jmservera/SquadScope-Coordinator#30).

Design, matching the repository's conventions:

* **Configurable, opt-in.** Notifications are sent only when
  ``PODCASTER_ALERT_WEBHOOK_URL`` is set; otherwise the call is a no-op. The
  payload shape adapts to ``PODCASTER_ALERT_WEBHOOK_FORMAT`` (``teams`` /
  ``slack`` / ``generic``).
* **Never breaks the pipeline.** Every error (bad config, network, timeout,
  non-2xx) is swallowed and logged. :func:`notify_failure` returns ``True`` only
  when the webhook accepted the alert.
* **Secret-safe (Hermes).** The webhook URL must be ``https`` and must not point
  at loopback / link-local / cloud-metadata hosts (SSRF defense in depth, since
  the URL is an operator-provided secret). The error summary is length-capped
  and control-stripped via :mod:`podcaster.sanitization` so raw tracebacks /
  secrets are not blindly forwarded to a third-party chat service.
* **Testable.** ``transport`` is injectable (a callable matching
  ``urlopen(request)``), so tests exercise payloads without real network I/O.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from podcaster.sanitization import neutralize
from podcaster.ssrf import host_is_blocked

logger = logging.getLogger("podcaster.notifications")

#: Environment variables (all optional).
ENV_WEBHOOK_URL = "PODCASTER_ALERT_WEBHOOK_URL"
ENV_WEBHOOK_FORMAT = "PODCASTER_ALERT_WEBHOOK_FORMAT"
ENV_UI_BASE_URL = "PODCASTER_UI_BASE_URL"
ENV_DISABLED = "PODCASTER_ALERT_NOTIFY_DISABLED"

FORMAT_GENERIC = "generic"
FORMAT_TEAMS = "teams"
FORMAT_SLACK = "slack"
_VALID_FORMATS = frozenset({FORMAT_GENERIC, FORMAT_TEAMS, FORMAT_SLACK})

#: POST timeout (seconds). Short — a slow webhook must not stall the runner.
_HTTP_TIMEOUT = 10
#: Cap applied to the error summary before it leaves the process.
_SUMMARY_LIMIT = 1000


class NotificationError(ValueError):
    """Invalid notification configuration (raised only by ``from_env`` callers
    that opt into strict validation; the normal path logs and no-ops)."""


class _Transport(Protocol):
    """Call signature shared by :func:`urllib.request.urlopen` and test doubles.

    ``notify_failure`` invokes the transport as ``send(request, timeout=...)``
    and uses the result as a context manager, so injected callables must accept
    the ``timeout`` keyword. Declaring it here (rather than a bare
    ``Callable[[Request], Any]``) keeps test doubles honest about the real
    call signature.
    """

    def __call__(self, request: Request, timeout: float = ...) -> Any: ...


def _host_is_blocked(hostname: str) -> bool:
    """Return True if *hostname* is loopback / private / link-local / metadata.

    Thin wrapper preserved for backwards compatibility; the implementation now
    lives in :func:`podcaster.ssrf.host_is_blocked` so every outbound fetch
    shares one hardened SSRF guard. The shared guard adds one destination class
    the previous inlined webhook check missed — **multicast** — on top of the
    loopback/private/link-local/reserved/unspecified ranges it already blocked
    (intended SSRF hardening).
    """
    return host_is_blocked(hostname)


@dataclass(frozen=True)
class NotificationConfig:
    """Resolved webhook configuration."""

    webhook_url: str
    fmt: str = FORMAT_GENERIC
    ui_base_url: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def validate(self) -> None:
        """Raise :class:`NotificationError` if the webhook URL is unsafe."""
        parsed = urlparse(self.webhook_url)
        if parsed.scheme != "https":
            raise NotificationError(f"webhook URL must use https, got scheme {parsed.scheme!r}")
        if not parsed.hostname:
            raise NotificationError("webhook URL has no host")
        if _host_is_blocked(parsed.hostname):
            raise NotificationError(
                f"webhook host {parsed.hostname!r} is loopback/private/metadata; refused"
            )

    @classmethod
    def from_env(cls, env: "dict[str, str] | None" = None) -> "NotificationConfig | None":
        """Build config from the environment, or ``None`` when disabled/unset."""
        env = os.environ if env is None else env
        if env.get(ENV_DISABLED, "").strip().lower() == "true":
            logger.debug("alert notifications disabled via %s", ENV_DISABLED)
            return None
        url = (env.get(ENV_WEBHOOK_URL) or "").strip()
        if not url:
            return None
        fmt = (env.get(ENV_WEBHOOK_FORMAT) or FORMAT_GENERIC).strip().lower()
        if fmt not in _VALID_FORMATS:
            logger.warning(
                "unknown %s=%r; falling back to %r", ENV_WEBHOOK_FORMAT, fmt, FORMAT_GENERIC
            )
            fmt = FORMAT_GENERIC
        ui_base = (env.get(ENV_UI_BASE_URL) or "").strip() or None
        return cls(webhook_url=url, fmt=fmt, ui_base_url=ui_base)


def _job_link(ui_base_url: str | None, job_id: str) -> str | None:
    if not ui_base_url:
        return None
    return f"{ui_base_url.rstrip('/')}/jobs/{job_id}"


def _build_payload(
    config: NotificationConfig,
    *,
    job_id: str,
    stage: str,
    summary: str,
    error_type: str | None,
) -> dict[str, Any]:
    """Render the webhook body for the configured chat service."""
    link = _job_link(config.ui_base_url, job_id)
    title = f"Podcaster job failed: {job_id}"
    facts = [
        ("Job", job_id),
        ("Stage", stage),
        ("Error", error_type or "Unknown"),
    ]

    if config.fmt == FORMAT_TEAMS:
        sections: dict[str, Any] = {
            "activityTitle": title,
            "facts": [{"name": name, "value": value} for name, value in facts],
            "text": summary,
        }
        card: dict[str, Any] = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "themeColor": "D93F3F",
            "summary": title,
            "sections": [sections],
        }
        if link:
            card["potentialAction"] = [
                {
                    "@type": "OpenUri",
                    "name": "Open job in UI",
                    "targets": [{"os": "default", "uri": link}],
                }
            ]
        return card

    if config.fmt == FORMAT_SLACK:
        lines = [f"*{title}*", f"*Stage:* {stage}", f"*Error:* {error_type or 'Unknown'}"]
        if link:
            lines.append(f"<{link}|Open job in UI>")
        lines.append(summary)
        return {"text": "\n".join(lines)}

    # Generic JSON — stable, consumer-defined shape.
    return {
        "event": "job_failed",
        "job_id": job_id,
        "stage": stage,
        "error_type": error_type or "Unknown",
        "summary": summary,
        "job_url": link,
    }


def notify_failure(
    *,
    job_id: str,
    stage: str,
    error_summary: str,
    error_type: str | None = None,
    config: NotificationConfig | None = None,
    transport: _Transport | None = None,
) -> bool:
    """Send a failure alert to the configured webhook.

    Returns ``True`` only when the webhook accepted the alert (HTTP 2xx). Any
    misconfiguration, network error, or non-2xx response is logged and yields
    ``False`` — notification failures never propagate to the caller.

    Args:
        job_id: The failed job's id.
        stage: Pipeline stage that failed (e.g. ``record``, ``synthesis``).
        error_summary: Human-readable error description; length-capped + control
            stripped before sending.
        error_type: Short error class/type (e.g. ``RetryExhausted``).
        config: Resolved config; defaults to :meth:`NotificationConfig.from_env`.
        transport: Injectable ``urlopen``-like callable for tests; must accept
            ``transport(request, timeout=...)`` (see :class:`_Transport`).
    """
    if config is None:
        config = NotificationConfig.from_env()
    if config is None or not config.enabled:
        logger.debug("no alert webhook configured; skipping notification job_id=%s", job_id)
        return False

    try:
        config.validate()
    except NotificationError:
        logger.warning("refusing to send alert to unsafe webhook", exc_info=True)
        return False

    summary = neutralize(error_summary, limit=_SUMMARY_LIMIT)
    payload = _build_payload(
        config,
        job_id=job_id,
        stage=stage,
        summary=summary,
        error_type=error_type,
    )
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        config.webhook_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    send = transport or urlopen
    try:
        with send(request, timeout=_HTTP_TIMEOUT) as resp:
            status = getattr(resp, "status", None)
            if status is None:
                status = resp.getcode()
            if 200 <= int(status) < 300:
                logger.info(
                    "sent failure alert job_id=%s stage=%s format=%s",
                    job_id,
                    stage,
                    config.fmt,
                )
                return True
            logger.warning("alert webhook returned non-2xx status=%s job_id=%s", status, job_id)
            return False
    except HTTPError as exc:
        logger.warning("alert webhook HTTP error status=%s job_id=%s", exc.code, job_id)
        return False
    except (URLError, OSError, ValueError, TypeError):
        logger.warning("failed to deliver failure alert job_id=%s", job_id, exc_info=True)
        return False
