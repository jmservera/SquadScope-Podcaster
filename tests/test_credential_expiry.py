"""Tests for podcaster.credential_expiry — Spotify credential-expiry notify (#364)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from podcaster import credential_expiry as ce


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("CREDENTIAL_EXPIRY_NOTIFY_DISABLED", raising=False)


def _completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr="")


class TestBuildIssueBody:
    def test_includes_refresh_instructions(self):
        body = ce.build_issue_body("boom", timestamp="2026-01-01T00:00:00Z")
        assert "sp_dc" in body
        assert "sp_key" in body
        assert "az containerapp update" in body
        assert "2026-01-01T00:00:00Z" in body
        assert "boom" in body
        assert "#364" in body

    def test_truncates_long_error(self):
        body = ce.build_issue_body("x" * 5000)
        # Error block is capped at 2000 chars.
        assert "x" * 2000 in body
        assert "x" * 2001 not in body


class TestParseIssueNumber:
    def test_parses_trailing_number(self):
        assert ce._parse_issue_number("https://github.com/o/r/issues/42") == 42

    def test_handles_trailing_slash(self):
        assert ce._parse_issue_number("https://github.com/o/r/issues/7/") == 7

    def test_returns_none_for_empty(self):
        assert ce._parse_issue_number("") is None

    def test_returns_none_for_non_numeric(self):
        assert ce._parse_issue_number("https://github.com/o/r/issues/abc") is None


class TestNotifyCredentialExpiry:
    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("CREDENTIAL_EXPIRY_NOTIFY_DISABLED", "true")
        with patch.object(ce, "_gh_available", return_value=True) as gh:
            assert ce.notify_credential_expiry("expired") is None
            gh.assert_not_called()

    def test_skips_when_gh_unavailable(self):
        with patch.object(ce, "_gh_available", return_value=False):
            assert ce.notify_credential_expiry("expired") is None

    def test_creates_issue_when_none_exists(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[0] == "issue" and args[1] == "list":
                return _completed(stdout="[]")
            if args[0] == "label":
                return _completed()
            if args[0] == "issue" and args[1] == "create":
                return _completed(stdout="https://github.com/owner/repo/issues/99\n")
            raise AssertionError(f"unexpected gh call: {args}")

        with (
            patch.object(ce, "_gh_available", return_value=True),
            patch.object(ce, "_run_gh", side_effect=fake_run),
        ):
            num = ce.notify_credential_expiry("HTTP 401 expired")

        assert num == 99
        # The create call must target the right repo and label.
        create = next(c for c in calls if c[:2] == ["issue", "create"])
        assert "owner/repo" in create
        assert ce.CREDENTIALS_EXPIRED_LABEL in create

    def test_dedups_existing_open_issue(self):
        def fake_run(args):
            if args[0] == "issue" and args[1] == "list":
                return _completed(stdout=json.dumps([{"number": 12, "title": ce.ISSUE_TITLE}]))
            raise AssertionError(f"should not create when dedup hit: {args}")

        with (
            patch.object(ce, "_gh_available", return_value=True),
            patch.object(ce, "_run_gh", side_effect=fake_run),
        ):
            num = ce.notify_credential_expiry("expired")

        assert num == 12

    def test_returns_none_on_gh_error(self):
        def fake_run(args):
            raise subprocess.CalledProcessError(1, args, stderr="nope")

        with (
            patch.object(ce, "_gh_available", return_value=True),
            patch.object(ce, "_run_gh", side_effect=fake_run),
        ):
            assert ce.notify_credential_expiry("expired") is None

    def test_label_creation_failure_is_non_fatal(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

        def fake_run(args):
            if args[0] == "issue" and args[1] == "list":
                return _completed(stdout="[]")
            if args[0] == "label":
                raise subprocess.CalledProcessError(1, args, stderr="exists")
            if args[0] == "issue" and args[1] == "create":
                return _completed(stdout="https://github.com/owner/repo/issues/5")
            raise AssertionError(f"unexpected: {args}")

        with (
            patch.object(ce, "_gh_available", return_value=True),
            patch.object(ce, "_run_gh", side_effect=fake_run),
        ):
            assert ce.notify_credential_expiry("expired") == 5
