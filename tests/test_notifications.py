"""Tests for the failure-alert webhook notifier (#473)."""

from __future__ import annotations

import json

import pytest

from podcaster.notifications import (
    ENV_DISABLED,
    ENV_UI_BASE_URL,
    ENV_WEBHOOK_FORMAT,
    ENV_WEBHOOK_URL,
    FORMAT_GENERIC,
    FORMAT_SLACK,
    FORMAT_TEAMS,
    NotificationConfig,
    NotificationError,
    notify_failure,
)


class _Resp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return b""


class _Transport:
    """Captures the outgoing Request; returns a configurable status."""

    def __init__(self, status=200, raise_exc=None):
        self.status = status
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        return _Resp(self.status)

    @property
    def last_payload(self):
        return json.loads(self.calls[-1].data.decode("utf-8"))


_HTTPS = "https://example.com/webhook/abc"


# --------------------------------------------------------------------------- #
# Config / from_env
# --------------------------------------------------------------------------- #
def test_from_env_returns_none_when_unset():
    assert NotificationConfig.from_env(env={}) is None


def test_from_env_returns_none_when_disabled():
    env = {ENV_WEBHOOK_URL: _HTTPS, ENV_DISABLED: "true"}
    assert NotificationConfig.from_env(env=env) is None


def test_from_env_reads_url_and_format():
    env = {
        ENV_WEBHOOK_URL: _HTTPS,
        ENV_WEBHOOK_FORMAT: "teams",
        ENV_UI_BASE_URL: "https://ui.example.com/",
    }
    cfg = NotificationConfig.from_env(env=env)
    assert cfg is not None
    assert cfg.webhook_url == _HTTPS
    assert cfg.fmt == FORMAT_TEAMS
    assert cfg.ui_base_url == "https://ui.example.com/"
    assert cfg.enabled


def test_from_env_unknown_format_falls_back_to_generic():
    env = {ENV_WEBHOOK_URL: _HTTPS, ENV_WEBHOOK_FORMAT: "carrier-pigeon"}
    cfg = NotificationConfig.from_env(env=env)
    assert cfg.fmt == FORMAT_GENERIC


# --------------------------------------------------------------------------- #
# SSRF / scheme validation
# --------------------------------------------------------------------------- #
def test_validate_rejects_http():
    with pytest.raises(NotificationError):
        NotificationConfig(webhook_url="http://example.com/hook").validate()


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/hook",
        "https://127.0.0.1/hook",
        "https://10.0.0.5/hook",
        "https://169.254.169.254/latest/meta-data",  # cloud metadata
        "https://192.168.1.10/hook",
        "https://metadata.google.internal/x",
    ],
)
def test_validate_rejects_unsafe_hosts(url):
    with pytest.raises(NotificationError):
        NotificationConfig(webhook_url=url).validate()


def test_validate_accepts_public_https():
    NotificationConfig(webhook_url=_HTTPS).validate()  # no raise


def test_notify_refuses_unsafe_webhook_without_network():
    transport = _Transport()
    cfg = NotificationConfig(webhook_url="https://127.0.0.1/hook")
    ok = notify_failure(
        job_id="j1",
        stage="synthesis",
        error_summary="boom",
        config=cfg,
        transport=transport,
    )
    assert ok is False
    assert transport.calls == []  # never attempted the POST


# --------------------------------------------------------------------------- #
# Payload shaping
# --------------------------------------------------------------------------- #
def test_generic_payload_contents():
    transport = _Transport()
    cfg = NotificationConfig(webhook_url=_HTTPS, fmt=FORMAT_GENERIC, ui_base_url="https://ui.x")
    ok = notify_failure(
        job_id="job-42",
        stage="record",
        error_summary="ffmpeg exited 1",
        error_type="RecordError",
        config=cfg,
        transport=transport,
    )
    assert ok is True
    body = transport.last_payload
    assert body["event"] == "job_failed"
    assert body["job_id"] == "job-42"
    assert body["stage"] == "record"
    assert body["error_type"] == "RecordError"
    assert body["summary"] == "ffmpeg exited 1"
    assert body["job_url"] == "https://ui.x/jobs/job-42"


def test_generic_payload_without_ui_base_has_null_link():
    transport = _Transport()
    cfg = NotificationConfig(webhook_url=_HTTPS)
    notify_failure(
        job_id="j", stage="synthesis", error_summary="x", config=cfg, transport=transport
    )
    assert transport.last_payload["job_url"] is None


def test_teams_payload_is_message_card_with_action():
    transport = _Transport()
    cfg = NotificationConfig(webhook_url=_HTTPS, fmt=FORMAT_TEAMS, ui_base_url="https://ui.x/")
    notify_failure(
        job_id="job-7",
        stage="compose",
        error_summary="oom",
        error_type="OOMKilled",
        config=cfg,
        transport=transport,
    )
    body = transport.last_payload
    assert body["@type"] == "MessageCard"
    facts = body["sections"][0]["facts"]
    assert {"name": "Stage", "value": "compose"} in facts
    action = body["potentialAction"][0]
    assert action["targets"][0]["uri"] == "https://ui.x/jobs/job-7"


def test_teams_payload_without_link_omits_action():
    transport = _Transport()
    cfg = NotificationConfig(webhook_url=_HTTPS, fmt=FORMAT_TEAMS)
    notify_failure(job_id="j", stage="mux", error_summary="x", config=cfg, transport=transport)
    assert "potentialAction" not in transport.last_payload


def test_slack_payload_has_text_and_link():
    transport = _Transport()
    cfg = NotificationConfig(webhook_url=_HTTPS, fmt=FORMAT_SLACK, ui_base_url="https://ui.x")
    notify_failure(
        job_id="job-9",
        stage="normalize",
        error_summary="bad clip",
        error_type="NormError",
        config=cfg,
        transport=transport,
    )
    text = transport.last_payload["text"]
    assert "normalize" in text
    assert "NormError" in text
    assert "https://ui.x/jobs/job-9" in text


# --------------------------------------------------------------------------- #
# Sanitization of the summary
# --------------------------------------------------------------------------- #
def test_summary_is_length_capped():
    transport = _Transport()
    cfg = NotificationConfig(webhook_url=_HTTPS)
    notify_failure(
        job_id="j",
        stage="synthesis",
        error_summary="A" * 5000,
        config=cfg,
        transport=transport,
    )
    assert len(transport.last_payload["summary"]) <= 1100  # capped well below 5000


def test_summary_control_chars_stripped():
    transport = _Transport()
    cfg = NotificationConfig(webhook_url=_HTTPS)
    notify_failure(
        job_id="j",
        stage="synthesis",
        error_summary="line1\x00\x07line2",
        config=cfg,
        transport=transport,
    )
    assert "\x00" not in transport.last_payload["summary"]


# --------------------------------------------------------------------------- #
# Robustness — notification never raises
# --------------------------------------------------------------------------- #
def test_no_config_is_noop_returns_false():
    assert (
        notify_failure(
            job_id="j",
            stage="synthesis",
            error_summary="x",
            config=None,
            transport=_Transport(),
        )
        is False
    )


def test_non_2xx_returns_false():
    transport = _Transport(status=500)
    cfg = NotificationConfig(webhook_url=_HTTPS)
    assert (
        notify_failure(
            job_id="j", stage="synthesis", error_summary="x", config=cfg, transport=transport
        )
        is False
    )


def test_transport_exception_is_swallowed():
    transport = _Transport(raise_exc=OSError("network down"))
    cfg = NotificationConfig(webhook_url=_HTTPS)
    # Must not raise.
    assert (
        notify_failure(
            job_id="j", stage="synthesis", error_summary="x", config=cfg, transport=transport
        )
        is False
    )


def test_uses_env_config_when_not_passed(monkeypatch):
    monkeypatch.setenv(ENV_WEBHOOK_URL, _HTTPS)
    monkeypatch.setenv(ENV_WEBHOOK_FORMAT, FORMAT_GENERIC)
    monkeypatch.delenv(ENV_DISABLED, raising=False)
    transport = _Transport()
    ok = notify_failure(job_id="env-job", stage="synthesis", error_summary="x", transport=transport)
    assert ok is True
    assert transport.last_payload["job_id"] == "env-job"
