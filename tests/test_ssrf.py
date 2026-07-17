"""Tests for podcaster.ssrf shared SSRF guards (#601)."""

from __future__ import annotations

from urllib.error import HTTPError

import pytest

from podcaster import ssrf


class TestHostIsBlocked:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "metadata",
            "metadata.google.internal",
            "metadata.azure.com",
            "127.0.0.1",
            "127.0.0.53",
            "::1",
            "10.0.0.5",
            "192.168.1.10",
            "172.16.5.4",
            "169.254.169.254",  # cloud metadata IMDS
            "224.0.0.1",  # multicast
            "0.0.0.0",  # noqa: S104 — testing that unspecified is blocked
            "",  # empty -> fail closed
        ],
    )
    def test_blocked(self, host):
        assert ssrf.host_is_blocked(host) is True

    @pytest.mark.parametrize("host", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
    def test_public_ip_allowed(self, host):
        assert ssrf.host_is_blocked(host) is False

    def test_trailing_dot_and_case_normalized(self):
        assert ssrf.host_is_blocked("LocalHost.") is True

    def test_unresolvable_host_fails_closed(self, monkeypatch):
        def _boom(*_a, **_k):
            raise OSError("no such host")

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _boom)
        assert ssrf.host_is_blocked("does-not-exist.invalid") is True

    def test_hostname_resolving_to_private_blocked(self, monkeypatch):
        def _fake(_host, _port):
            return [(2, 1, 6, "", ("10.1.2.3", 0))]

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _fake)
        assert ssrf.host_is_blocked("evil.example.com") is True

    def test_hostname_resolving_to_public_allowed(self, monkeypatch):
        def _fake(_host, _port):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _fake)
        assert ssrf.host_is_blocked("example.com") is False


class TestUrlIsSafe:
    def test_rejects_non_http_scheme(self):
        assert ssrf.url_is_safe("file:///etc/passwd") is False
        assert ssrf.url_is_safe("gopher://127.0.0.1/") is False

    def test_rejects_blocked_host(self):
        assert ssrf.url_is_safe("http://169.254.169.254/latest/meta-data/") is False
        assert ssrf.url_is_safe("http://127.0.0.1:8080/") is False

    def test_allows_public_https(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", lambda *_: [(2, 1, 6, "", ("8.8.8.8", 0))])
        assert ssrf.url_is_safe("https://example.com/logo.png") is True


class TestSafeUrlopen:
    def test_refuses_unsafe_initial_url(self):
        with pytest.raises(ValueError):
            ssrf.safe_urlopen("http://127.0.0.1/", timeout=1)

    def test_redirect_to_blocked_host_refused(self):
        handler = ssrf._SafeRedirectHandler()
        with pytest.raises(HTTPError):
            handler.redirect_request(
                req=None,
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="http://169.254.169.254/",
            )
