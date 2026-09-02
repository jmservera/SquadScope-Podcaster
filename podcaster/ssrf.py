"""Shared SSRF guards for outbound HTTP requests.

Several call sites fetch caller- or operator-provided URLs (failure webhooks in
:mod:`podcaster.notifications`, the DOG watermark logo in
:mod:`podcaster.video.video_compose`). A URL that resolves to a loopback,
private, link-local, reserved, or cloud-metadata address lets an authenticated
caller coerce the worker into probing internal infrastructure or reading the
cloud instance-metadata endpoint (e.g. ``http://169.254.169.254/``).

This module centralises the host allow/deny logic so every outbound fetch shares
one hardened implementation, provides :func:`redact_url` for logging
caller-controlled URLs without leaking credentials/tokens, and provides
:func:`safe_urlopen`, a guarded ``urlopen``-style helper (URL string or
``Request``) that also re-validates every redirect target (defence against a
permitted host issuing a ``30x`` to an internal address) **and** re-validates
every address the socket layer actually resolves, immediately before connecting
to it.

Two kinds of "not allowed" are deliberately distinguished (:class:`HostVerdict`):
a host that *resolved* to a blocked address is a stable, permanent verdict, while
a host that could not be resolved at all is a resolver condition that may clear
on its own. Both are refused — the guard still fails closed and never connects —
but a caller that retries work (e.g. a queue worker) can now tell a
misconfiguration from a DNS outage instead of discarding the job on the first
blip. See :func:`classify_host`.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.request
from enum import Enum
from urllib.error import HTTPError
from urllib.parse import urlparse, urlunparse

#: Hostnames that must never be fetched. Literal IPs are additionally
#: range-checked below, so this only needs the symbolic metadata/loopback names.
BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "metadata.google.internal", "metadata", "metadata.azure.com"}
)


class HostVerdict(Enum):
    """Why a host may or may not be fetched — evidence, not just a boolean.

    ``ALLOWED`` is the *only* verdict that permits an outbound connection; both
    other members refuse it, so the guard still fails closed. The split exists so
    callers can classify the refusal:

    * :attr:`BLOCKED` — proven unsafe or unusable: an unsupported scheme, a
      missing host, a symbolic deny-listed name, or at least one resolved
      address in a loopback/private/link-local/reserved/unspecified/multicast
      range. Re-running the identical request re-reaches the identical verdict.
    * :attr:`UNRESOLVABLE` — no evidence either way: ``getaddrinfo`` failed (DNS
      outage, resolver restart, transient ``SERVFAIL``) or returned nothing
      parseable, so the host could not be *proven* safe. The request is still
      refused, but a later attempt may resolve and succeed.
    """

    ALLOWED = "allowed"
    BLOCKED = "blocked"
    UNRESOLVABLE = "unresolvable"


class SsrfError(ValueError):
    """Base class for a refusal by the SSRF guard.

    Subclasses :class:`ValueError` so existing ``except ValueError`` call sites
    around :func:`safe_urlopen` keep working unchanged.
    """

    def __init__(self, message: str, *, verdict: HostVerdict) -> None:
        super().__init__(message)
        self.verdict = verdict


class BlockedHostError(SsrfError):
    """The target resolved to (or literally is) an address that must not be fetched.

    Permanent: the same URL will reach the same verdict on every retry.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, verdict=HostVerdict.BLOCKED)


class UnresolvableHostError(SsrfError):
    """The target host could not be resolved, so it could not be proven safe.

    Transient: the fetch is still refused (fail closed — an unresolved host may
    resolve to a private address a moment later), but nothing about the URL has
    been shown to be wrong, so a bounded retry is appropriate.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, verdict=HostVerdict.UNRESOLVABLE)


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Normalize IPv6 forms that embed an IPv4 address (e.g. ``::ffff:127.0.0.1``,
    # 6to4, Teredo) so a mapped IPv4 literal can't smuggle past the range checks.
    if isinstance(ip, ipaddress.IPv6Address):
        # Normalize IPv6 forms that embed IPv4 (``::ffff:127.0.0.1``, 6to4,
        # Teredo) so a mapped IPv4 literal can't smuggle past the range checks.
        embedded: list[ipaddress.IPv4Address] = []
        if ip.ipv4_mapped is not None:
            embedded.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            embedded.append(ip.sixtofour)
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        if any(_ip_is_blocked(mapped) for mapped in embedded):
            return True
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


def classify_host(hostname: str | None) -> HostVerdict:
    """Return the :class:`HostVerdict` for *hostname* — the evidence-based check.

    This is the single source of truth for the deny logic; :func:`host_is_blocked`
    is the boolean projection of it.

    * Missing/empty host and the symbolic names in :data:`BLOCKED_HOSTNAMES` are
      :attr:`~HostVerdict.BLOCKED`.
    * A literal IP is range-checked directly, with no DNS at all.
    * A symbolic hostname is resolved and :attr:`~HostVerdict.BLOCKED` if *any*
      resolved address falls in a blocked range (an attacker must not be able to
      hide a private address behind a second, public ``A`` record).
    * If ``getaddrinfo`` raises, or yields nothing that parses as an address, the
      host cannot be *proven* safe and the verdict is
      :attr:`~HostVerdict.UNRESOLVABLE`.

    Both non-``ALLOWED`` verdicts refuse the fetch, so the guard is unchanged in
    what it permits; the distinction only tells the caller whether retrying the
    identical request could ever produce a different answer.
    """
    if not hostname:
        return HostVerdict.BLOCKED
    host = hostname.strip().lower().rstrip(".")
    if not host or host in BLOCKED_HOSTNAMES:
        return HostVerdict.BLOCKED
    # Literal IP? Range-check it directly without DNS.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return HostVerdict.BLOCKED if _ip_is_blocked(literal) else HostVerdict.ALLOWED
    # Hostname: resolve and reject if it maps to a blocked range.
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # No evidence: the resolver failed, which says nothing about the host.
        return HostVerdict.UNRESOLVABLE
    resolved = False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            continue
        resolved = True
        if _ip_is_blocked(ip):
            return HostVerdict.BLOCKED
    # An empty/unparseable answer proves nothing either — do not connect, but do
    # not call it a permanent configuration failure.
    return HostVerdict.ALLOWED if resolved else HostVerdict.UNRESOLVABLE


def host_is_blocked(hostname: str | None) -> bool:
    """Return ``True`` if *hostname* must not be fetched (SSRF deny-list).

    Blocks loopback, private, link-local, reserved, unspecified, and multicast
    IP ranges, plus the symbolic metadata/loopback hostnames in
    :data:`BLOCKED_HOSTNAMES`. Literal IP addresses are range-checked directly.
    Symbolic hostnames are resolved best-effort and rejected if *any* resolved
    address falls in a blocked range. Missing or unresolvable hosts fail
    **closed** (blocked), because ``urlopen`` may still resolve and connect to a
    private address later.

    This is the lossy boolean view of :func:`classify_host`: callers that need to
    tell "resolved to a private address" (permanent) from "the resolver is down"
    (transient) must use :func:`classify_host` instead.
    """
    return classify_host(hostname) is not HostVerdict.ALLOWED


def classify_url(url: str, *, allowed_schemes: tuple[str, ...] = ("http", "https")) -> HostVerdict:
    """Return the :class:`HostVerdict` for *url*, scheme included.

    An unsupported scheme, or a URL malformed enough that :func:`urlparse`
    refuses it, is :attr:`~HostVerdict.BLOCKED`: neither can be fixed by waiting.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return HostVerdict.BLOCKED
    if parsed.scheme.lower() not in allowed_schemes:
        return HostVerdict.BLOCKED
    return classify_host(hostname)


def url_is_safe(url: str, *, allowed_schemes: tuple[str, ...] = ("http", "https")) -> bool:
    """Return ``True`` when *url* uses an allowed scheme and a non-blocked host."""
    return classify_url(url, allowed_schemes=allowed_schemes) is HostVerdict.ALLOWED


def redact_url(url: str) -> str:
    """Reduce *url* to ``scheme://host[:port]/path`` for safe logging.

    Drops any ``user:pass@`` userinfo **and** the query string and fragment,
    which for caller-controlled URLs can carry embedded credentials or signed
    access tokens. IPv6 literal hosts are bracketed so the result stays a valid,
    unambiguous URL.
    """
    # ``urlparse`` is lazy: an invalid/out-of-range port only raises ``ValueError``
    # when ``.port`` is accessed, so both the parse and the port read must be
    # guarded. This helper logs attacker-controlled URLs on error paths and must
    # never raise.
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return "<unparseable-url>"
    if host is None:
        return f"{parsed.scheme}:<no-host>" if parsed.scheme else "<no-host>"
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that refuses ``30x`` hops to blocked hosts.

    Without this, an attacker-permitted host could redirect the worker to an
    internal address, defeating the up-front :func:`classify_url` check. A hop to
    a host that cannot be *resolved* is refused too, but as
    :class:`UnresolvableHostError` rather than an ``HTTPError``, so a resolver
    outage part-way through a redirect chain is not reported as a permanent
    verdict on the configured URL.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        verdict = classify_url(newurl)
        if verdict is HostVerdict.UNRESOLVABLE:
            raise UnresolvableHostError(
                f"redirect target could not be resolved: {redact_url(newurl)}"
            )
        if verdict is not HostVerdict.ALLOWED:
            raise HTTPError(
                redact_url(newurl), code, "redirect to blocked host refused", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _guarded_create_connection(address, timeout=None, source_address=None, **_kwargs):
    """``socket.create_connection`` replacement that vets every resolved address.

    The up-front :func:`classify_url` check and the socket layer would otherwise
    perform *two independent* resolutions, leaving a DNS-rebinding window: a
    hostile resolver can answer the check with a public address and the connect
    with ``127.0.0.1``. Here the same ``getaddrinfo`` answer that is validated is
    the one connected to, address by address, so there is no window between the
    decision and the connection.

    Resolver failure propagates as :class:`socket.gaierror` (an ``OSError``),
    which ``urllib`` wraps in ``URLError`` and callers classify as transient.
    A resolved-but-blocked address raises :class:`BlockedHostError`, which is
    permanent and is deliberately *not* an ``OSError``, so it cannot be mistaken
    for a network blip.
    """
    host, port = address[0], address[1]
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    vetted = []
    for family, socktype, proto, canonname, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])
        except ValueError:
            # Unparseable address: cannot be proven safe, so refuse the whole
            # answer rather than silently connecting to the rest of it.
            raise BlockedHostError(
                "refusing to connect: resolved address could not be range-checked"
            ) from None
        if _ip_is_blocked(ip):
            raise BlockedHostError(
                "refusing to connect: host resolved to a blocked (private/loopback/"
                "link-local/metadata) address"
            )
        vetted.append((family, socktype, proto, canonname, sockaddr))

    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in vetted:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if isinstance(timeout, (int, float)):
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                sock.close()
    if last_error is not None:
        raise last_error
    raise socket.gaierror(socket.EAI_NONAME, "no usable address for host")


class _GuardedHTTPConnection(http.client.HTTPConnection):
    """``HTTPConnection`` that range-checks the address it is about to connect to."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _guarded_create_connection


class _GuardedHTTPSConnection(http.client.HTTPSConnection):
    """``HTTPSConnection`` counterpart of :class:`_GuardedHTTPConnection`.

    TLS setup is untouched (``HTTPSConnection.connect`` wraps whatever socket
    ``_create_connection`` returns), so certificate and hostname verification
    behave exactly as before.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _guarded_create_connection


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):  # type: ignore[override]
        return self.do_open(_GuardedHTTPConnection, req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):  # type: ignore[override]
        return self.do_open(_GuardedHTTPSConnection, req, context=self._context)


def safe_urlopen(url: str | urllib.request.Request, *, timeout: float):
    """``urlopen`` variant that blocks SSRF targets, including via redirects.

    Accepts either a URL string or a :class:`urllib.request.Request`, so
    ``Request``-based call sites can share the same guard. This is not a full
    :func:`urllib.request.urlopen` replacement: it exposes only ``url`` and a
    required keyword-only ``timeout`` (no ``data``/``context``/``cafile``).

    Three layers, all fail-closed:

    1. the initial URL's scheme and host are classified up front;
    2. every ``30x`` target is re-classified before the hop is followed;
    3. every address the resolver returns is range-checked immediately before
       the socket connects to *that* address, so a rebinding resolver cannot
       swap a vetted public answer for an internal one.

    Raises:
        BlockedHostError: The target (or a redirect target, or a resolved
            address) is deny-listed. A ``ValueError``, so existing
            ``except ValueError`` handlers still catch it. **Permanent.**
        UnresolvableHostError: The host could not be resolved, so it could not
            be proven safe. Also a ``ValueError``. **Transient.**
        urllib.error.HTTPError: A redirect pointed at a blocked host.
    """
    target = url.full_url if isinstance(url, urllib.request.Request) else url
    verdict = classify_url(target)
    if verdict is HostVerdict.UNRESOLVABLE:
        raise UnresolvableHostError(
            f"could not resolve host for URL, refusing to fetch: {redact_url(target)}"
        )
    if verdict is not HostVerdict.ALLOWED:
        raise BlockedHostError(
            f"refusing to fetch blocked or unsupported URL: {redact_url(target)}"
        )
    opener = urllib.request.build_opener(
        _SafeRedirectHandler(), _GuardedHTTPHandler(), _GuardedHTTPSHandler()
    )
    return opener.open(url, timeout=timeout)
