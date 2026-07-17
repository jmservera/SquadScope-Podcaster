"""Shared SSRF guards for outbound HTTP requests.

Several call sites fetch caller- or operator-provided URLs (failure webhooks in
:mod:`podcaster.notifications`, the DOG watermark logo in
:mod:`podcaster.video.video_compose`). A URL that resolves to a loopback,
private, link-local, reserved, or cloud-metadata address lets an authenticated
caller coerce the worker into probing internal infrastructure or reading the
cloud instance-metadata endpoint (e.g. ``http://169.254.169.254/``).

This module centralises the host allow/deny logic so every outbound fetch shares
one hardened implementation, and provides :func:`safe_urlopen`, a drop-in
``urlopen`` wrapper that also re-validates every redirect target (defence
against a permitted host issuing a ``30x`` to an internal address).
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urlparse

#: Hostnames that must never be fetched. Literal IPs are additionally
#: range-checked below, so this only needs the symbolic metadata/loopback names.
BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "metadata.google.internal", "metadata", "metadata.azure.com"}
)


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified
    )


def host_is_blocked(hostname: str | None) -> bool:
    """Return ``True`` if *hostname* is loopback / private / link-local / metadata.

    Literal IP addresses are range-checked directly. Symbolic hostnames are
    resolved best-effort and rejected if *any* resolved address falls in a
    blocked range. Missing or unresolvable hosts fail **closed** (blocked),
    because ``urlopen`` may still resolve and connect to a private address later.
    """
    if not hostname:
        return True
    host = hostname.strip().lower().rstrip(".")
    if not host or host in BLOCKED_HOSTNAMES:
        return True
    # Literal IP? Range-check it directly without DNS.
    try:
        return _ip_is_blocked(ipaddress.ip_address(host))
    except ValueError:
        pass
    # Hostname: best-effort resolve and reject if it maps to a blocked range.
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # Fail closed: an unresolvable host cannot be proven safe.
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            continue
        if _ip_is_blocked(ip):
            return True
    return False


def url_is_safe(url: str, *, allowed_schemes: tuple[str, ...] = ("http", "https")) -> bool:
    """Return ``True`` when *url* uses an allowed scheme and a non-blocked host."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in allowed_schemes:
        return False
    return not host_is_blocked(parsed.hostname)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that refuses ``30x`` hops to blocked hosts.

    Without this, an attacker-permitted host could redirect the worker to an
    internal address, defeating the up-front :func:`host_is_blocked` check.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not url_is_safe(newurl):
            raise HTTPError(newurl, code, "redirect to blocked host refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(url: str, *, timeout: float):
    """``urlopen`` variant that blocks SSRF targets, including via redirects.

    Raises :class:`ValueError` if the initial URL is unsafe, and
    :class:`urllib.error.HTTPError` if a redirect points at a blocked host.
    """
    if not url_is_safe(url):
        raise ValueError(f"refusing to fetch blocked or unsupported URL: {url!r}")
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    return opener.open(url, timeout=timeout)
