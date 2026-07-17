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
            "::ffff:127.0.0.1",  # IPv4-mapped IPv6 loopback
            "::ffff:169.254.169.254",  # IPv4-mapped IPv6 IMDS
            "::ffff:10.0.0.5",  # IPv4-mapped IPv6 private
            "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
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


class TestRedactUrl:
    def test_strips_userinfo(self):
        assert ssrf.redact_url("https://user:secret@example.com/logo.png") == (
            "https://example.com/logo.png"
        )

    def test_strips_query_and_fragment(self):
        assert ssrf.redact_url("https://example.com/a?token=abc#frag") == "https://example.com/a"

    def test_preserves_port_and_path(self):
        assert ssrf.redact_url("http://example.com:8080/x/y") == "http://example.com:8080/x/y"

    def test_brackets_ipv6(self):
        assert ssrf.redact_url("https://[2001:db8::1]:443/p?q=1") == "https://[2001:db8::1]:443/p"

    def test_no_host(self):
        assert ssrf.redact_url("not-a-url") in ("<no-host>", "not-a-url:<no-host>")

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com:99999/logo.png",  # out-of-range port
            "http://example.com:-1/logo.png",  # negative port
            "http://example.com:abc/logo.png",  # non-numeric port
        ],
    )
    def test_invalid_port_never_raises(self, url):
        # redact_url logs attacker-controlled URLs on error paths and must never
        # raise, even when urlparse().port would raise ValueError.
        assert ssrf.redact_url(url) == "<unparseable-url>"


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

    def test_refuses_unsafe_request_object(self):
        # safe_urlopen accepts urllib.request.Request too and validates its
        # full_url, so Request-based call sites share the same SSRF guard (#601).
        import urllib.request

        req = urllib.request.Request("http://169.254.169.254/latest/meta-data/")
        with pytest.raises(ValueError):
            ssrf.safe_urlopen(req, timeout=1)
