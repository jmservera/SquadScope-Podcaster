"""Tests for podcaster.ssrf shared SSRF guards (#601)."""

from __future__ import annotations

import socket
import urllib.request
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


def _answer(*addresses: str):
    """Build a ``getaddrinfo`` stub returning *addresses* as A/AAAA records."""

    def _fake(host, port=None, *_a, **_k):  # noqa: ARG001 — parity with socket
        return [
            (
                socket.AF_INET6 if ":" in addr else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (addr, port or 0),
            )
            for addr in addresses
        ]

    return _fake


class TestClassifyHost:
    """#658: "blocked" and "could not be resolved" are different verdicts.

    ``host_is_blocked`` collapsed both onto ``True``, so the watermark fetcher
    reported a DNS outage as a permanent SSRF block and the queue message was
    deleted on the first attempt.  Both verdicts still refuse the fetch — the
    guard is unchanged in what it *permits* — but only one of them is a stable
    statement about the configured URL.
    """

    def test_literal_public_ip_is_allowed_without_dns(self, monkeypatch):
        def _never(*_a, **_k):
            raise AssertionError("literal IPs must not be resolved")

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _never)
        assert ssrf.classify_host("93.184.216.34") is ssrf.HostVerdict.ALLOWED

    @pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "10.0.0.5", "localhost", ""])
    def test_definitively_blocked_hosts(self, host):
        assert ssrf.classify_host(host) is ssrf.HostVerdict.BLOCKED

    def test_resolver_failure_is_unresolvable_not_blocked(self, monkeypatch):
        def _boom(*_a, **_k):
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _boom)
        assert ssrf.classify_host("logo.example.com") is ssrf.HostVerdict.UNRESOLVABLE

    def test_empty_answer_is_unresolvable(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer())
        assert ssrf.classify_host("logo.example.com") is ssrf.HostVerdict.UNRESOLVABLE

    def test_resolved_private_address_is_permanently_blocked(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("10.1.2.3"))
        assert ssrf.classify_host("evil.example.com") is ssrf.HostVerdict.BLOCKED

    def test_mixed_answer_with_one_private_address_is_blocked(self, monkeypatch):
        """A public record must not launder a private one hiding behind it."""
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("93.184.216.34", "169.254.169.254"))
        assert ssrf.classify_host("evil.example.com") is ssrf.HostVerdict.BLOCKED

    def test_resolved_public_address_is_allowed(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("93.184.216.34"))
        assert ssrf.classify_host("example.com") is ssrf.HostVerdict.ALLOWED

    @pytest.mark.parametrize(
        "getaddrinfo,expected",
        [
            (_answer(), True),  # unresolvable
            (_answer("10.1.2.3"), True),  # blocked
            (_answer("93.184.216.34"), False),  # allowed
        ],
    )
    def test_host_is_blocked_still_fails_closed(self, getaddrinfo, expected, monkeypatch):
        """The boolean projection is unchanged: only ALLOWED permits a fetch."""
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", getaddrinfo)
        assert ssrf.host_is_blocked("example.com") is expected


class TestClassifyUrl:
    def test_unsupported_scheme_is_blocked_not_unresolvable(self):
        assert ssrf.classify_url("file:///etc/passwd") is ssrf.HostVerdict.BLOCKED

    def test_malformed_url_is_blocked(self):
        assert ssrf.classify_url("https://[bad]:80/logo.png") is ssrf.HostVerdict.BLOCKED

    def test_unresolvable_host_surfaces_as_unresolvable(self, monkeypatch):
        def _boom(*_a, **_k):
            raise socket.gaierror(socket.EAI_AGAIN, "temporary failure")

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _boom)
        assert ssrf.classify_url("https://logo.example.com/x.png") is ssrf.HostVerdict.UNRESOLVABLE

    def test_url_is_safe_only_for_allowed(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer())
        assert ssrf.url_is_safe("https://logo.example.com/x.png") is False
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("93.184.216.34"))
        assert ssrf.url_is_safe("https://logo.example.com/x.png") is True


class TestSafeUrlopenResolutionErrors:
    """A resolver outage must not be reported as a permanent SSRF block."""

    def test_unresolvable_host_raises_typed_transient_error(self, monkeypatch):
        def _boom(*_a, **_k):
            raise socket.gaierror(socket.EAI_AGAIN, "temporary failure")

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _boom)
        with pytest.raises(ssrf.UnresolvableHostError) as excinfo:
            ssrf.safe_urlopen("https://logo.example.com/x.png", timeout=1)
        exc = excinfo.value
        assert exc.verdict is ssrf.HostVerdict.UNRESOLVABLE
        # Back-compatible: existing ``except ValueError`` call sites still catch.
        assert isinstance(exc, ValueError)
        assert not isinstance(exc, ssrf.BlockedHostError)
        # Exact match, not a substring check: the message must be the redacted
        # URL and nothing else.
        assert str(exc) == (
            "could not resolve host for URL, refusing to fetch: https://logo.example.com/x.png"
        )

    def test_blocked_host_raises_permanent_error(self):
        with pytest.raises(ssrf.BlockedHostError) as excinfo:
            ssrf.safe_urlopen("http://169.254.169.254/latest/meta-data/", timeout=1)
        assert excinfo.value.verdict is ssrf.HostVerdict.BLOCKED
        assert isinstance(excinfo.value, ValueError)

    def test_resolved_private_host_is_blocked_not_unresolvable(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("127.0.0.1"))
        with pytest.raises(ssrf.BlockedHostError):
            ssrf.safe_urlopen("https://rebind.example.com/x.png", timeout=1)

    def test_never_leaks_credentials_or_query_in_the_message(self, monkeypatch):
        def _boom(*_a, **_k):
            raise socket.gaierror(socket.EAI_AGAIN, "temporary failure")

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _boom)
        userinfo = "s3cretuser:s3cretpass"
        url = f"https://{userinfo}@logo.example.com/x.png?sig=abc123"
        with pytest.raises(ssrf.UnresolvableHostError) as excinfo:
            ssrf.safe_urlopen(url, timeout=1)
        rendered = str(excinfo.value)
        assert "s3cret" not in rendered
        assert "abc123" not in rendered

    def test_redirect_to_unresolvable_host_is_transient(self, monkeypatch):
        def _boom(*_a, **_k):
            raise socket.gaierror(socket.EAI_AGAIN, "temporary failure")

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _boom)
        handler = ssrf._SafeRedirectHandler()
        with pytest.raises(ssrf.UnresolvableHostError):
            handler.redirect_request(
                req=None,
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="https://elsewhere.example.com/logo.png",
            )

    def test_redirect_to_resolved_private_host_is_still_refused(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("10.0.0.7"))
        handler = ssrf._SafeRedirectHandler()
        with pytest.raises(HTTPError):
            handler.redirect_request(
                req=None,
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="https://elsewhere.example.com/logo.png",
            )

    def test_allowed_redirect_still_followed(self, monkeypatch):
        import urllib.request

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("93.184.216.34"))
        handler = ssrf._SafeRedirectHandler()
        req = urllib.request.Request("https://example.com/a")
        new = handler.redirect_request(
            req=req,
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="https://example.com/b",
        )
        assert new is not None
        assert new.full_url == "https://example.com/b"


class TestConnectTimeAddressGuard:
    """The address that is *validated* must be the address that is *connected to*.

    Splitting validation (``getaddrinfo`` in :func:`classify_host`) from the
    connection (``getaddrinfo`` again inside ``socket.create_connection``) leaves
    a DNS-rebinding window: a hostile resolver answers the check with a public
    address and the connect with ``127.0.0.1``.  ``safe_urlopen`` therefore
    range-checks each resolved address immediately before connecting to it, and
    connects only to the vetted ones.
    """

    def test_rebinding_answer_is_refused_at_connect_time(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("127.0.0.1"))
        opened = []
        monkeypatch.setattr(ssrf.socket, "socket", lambda *a, **k: opened.append(a))
        with pytest.raises(ssrf.BlockedHostError):
            ssrf._guarded_create_connection(("rebind.example.com", 443), 5)
        assert opened == []

    def test_mixed_answer_is_refused_before_any_connect(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("93.184.216.34", "10.0.0.9"))
        opened = []
        monkeypatch.setattr(ssrf.socket, "socket", lambda *a, **k: opened.append(a))
        with pytest.raises(ssrf.BlockedHostError):
            ssrf._guarded_create_connection(("evil.example.com", 443), 5)
        assert opened == []

    def test_resolver_failure_propagates_as_oserror(self, monkeypatch):
        def _boom(*_a, **_k):
            raise socket.gaierror(socket.EAI_AGAIN, "temporary failure")

        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _boom)
        with pytest.raises(OSError):
            ssrf._guarded_create_connection(("logo.example.com", 443), 5)

    def test_blocked_error_is_not_an_oserror(self, monkeypatch):
        """So it can never be laundered into ``URLError`` and called a blip."""
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("169.254.169.254"))
        with pytest.raises(ssrf.BlockedHostError) as excinfo:
            ssrf._guarded_create_connection(("imds.example.com", 80), 5)
        assert not isinstance(excinfo.value, OSError)

    def test_public_answer_connects_to_the_vetted_address_only(self, monkeypatch):
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("93.184.216.34"))
        connected: list[tuple] = []

        class _FakeSocket:
            def settimeout(self, _t):
                pass

            def connect(self, addr):
                connected.append(addr)

            def close(self):  # pragma: no cover — only on failure paths
                pass

        monkeypatch.setattr(ssrf.socket, "socket", lambda *_a, **_k: _FakeSocket())
        sock = ssrf._guarded_create_connection(("example.com", 443), 5)
        assert isinstance(sock, _FakeSocket)
        assert connected == [("93.184.216.34", 443)]

    def test_opener_uses_the_guarded_connection_classes(self, monkeypatch):
        """Regression guard: the guard must be wired into the actual opener."""
        monkeypatch.setattr(ssrf.socket, "getaddrinfo", _answer("93.184.216.34"))
        captured = {}

        def _fake_open(self, url, timeout=None):  # noqa: ARG001
            captured["handlers"] = [type(h) for h in self.handlers]
            return "response"

        monkeypatch.setattr(urllib.request.OpenerDirector, "open", _fake_open)
        assert ssrf.safe_urlopen("https://example.com/logo.png", timeout=5) == "response"
        assert ssrf._GuardedHTTPHandler in captured["handlers"]
        assert ssrf._GuardedHTTPSHandler in captured["handlers"]
        assert ssrf._SafeRedirectHandler in captured["handlers"]

    def test_guarded_connections_install_the_guard(self):
        http_conn = ssrf._GuardedHTTPConnection("example.com", 80)
        https_conn = ssrf._GuardedHTTPSConnection("example.com", 443)
        assert http_conn._create_connection is ssrf._guarded_create_connection
        assert https_conn._create_connection is ssrf._guarded_create_connection
