# Error notification system — webhook / Teams (#473)

Part of the **Phase 5 Observability** epic (jmservera/SquadScope-Coordinator#30).

When a job fails terminally, operators previously had to *go look* — at the
GitHub failure issue ([`failure_reporting.py`](../podcaster/failure_reporting.py))
or the durable manifest status. `podcaster/notifications.py` adds an active
**push** channel: a failed job pings a chat/incident webhook within seconds with
the job id, the failed stage, an error summary, and a deep link to the job in
the monitoring UI.

## Usage

```python
from podcaster.notifications import notify_failure

notify_failure(
    job_id="abc123",
    stage="synthesis",
    error_type="RetryExhausted",
    error_summary="Synthesis failed after 5 attempts",
)
```

It is already wired into the synthesis runner's terminal-failure path
(`podcaster/job_runner.py`). `notify_failure` **never raises** and returns
`True` only when the webhook accepted the alert (HTTP 2xx) — a notification
failure can never break the pipeline.

## Configuration

| Environment variable             | Default     | Meaning                                            |
|----------------------------------|-------------|----------------------------------------------------|
| `PODCASTER_ALERT_WEBHOOK_URL`    | _(unset)_   | Incoming webhook URL. **Unset ⇒ notifications off.**|
| `PODCASTER_ALERT_WEBHOOK_FORMAT` | `generic`   | Payload shape: `teams`, `slack`, or `generic`.     |
| `PODCASTER_UI_BASE_URL`          | _(unset)_   | Base URL of the monitoring UI; used to build the `…/jobs/<id>` deep link. |
| `PODCASTER_ALERT_NOTIFY_DISABLED`| _(unset)_   | Set to `true` to hard-disable notifications.       |

### Payload formats
- **`teams`** — a Microsoft Teams [`MessageCard`](https://learn.microsoft.com/outlook/actionable-messages/message-card-reference)
  with facts (job / stage / error) and an *Open job in UI* action.
- **`slack`** — a Slack-compatible `{"text": …}` message with a clickable link.
- **`generic`** — stable JSON: `{event, job_id, stage, error_type, summary, job_url}`.

## Security (Hermes)

The webhook URL is an operator-provided **secret** (store it in Key Vault / an
ACA secret, not in source). Defense-in-depth checks before any request:

- The URL **must** use `https`.
- The host must not be loopback, private, link-local, reserved, or a cloud
  **metadata** endpoint (`169.254.169.254`, `metadata.google.internal`, …) — an
  SSRF guard, validated against literal IPs and resolved hostnames.
- The error summary is length-capped (1000 chars) and control-character stripped
  via `podcaster.sanitization.neutralize` before leaving the process, so raw
  tracebacks / secrets are not blindly forwarded to a third-party chat service.

## Acceptance criteria

- ✅ **A failed job triggers a notification within minutes** — sent inline on the
  terminal failure path with a 10s POST timeout.
- ✅ **Notification target configurable** — webhook URL + format via environment.
