#!/usr/bin/env python3
"""One-time YouTube OAuth2 consent flow to mint a refresh token (#441).

Service accounts cannot upload to YouTube, so an operator must grant user
consent once. This script runs the installed-app (loopback) authorization-code
flow against the OAuth2 *Desktop* client created in Google Cloud, then prints
the resulting refresh token for secure storage (see
``docs/youtube-token-storage.md`` — the `prod` GitHub environment secret for
this repo's production deployment, or Azure Key Vault for a direct-Key-Vault
deployment; #443).

By default this requests ``https://www.googleapis.com/auth/youtube`` (see
``podcaster.youtube_oauth.YOUTUBE_SCOPE``), because the distribution pipeline
needs more than upload: it reads videos back (``videos.list``) to verify
status/metadata, updates their publish status (``videos.update``,
``part=status``) to promote an approved draft to public or schedule a future
publish, and manages the show playlist (``playlistItems.list``/``insert``). A
refresh token minted with the narrower ``youtube.upload`` scope will upload
fine but every one of those other calls returns HTTP 403
``insufficientPermissions`` (#649). Scopes cannot be widened in place — re-run
this script to mint a new token, then revoke only the *old* token value via
Google's revocation endpoint (``POST https://oauth2.googleapis.com/revoke``),
passing the token through stdin rather than as a command-line argument so it
never lands in shell history or a process listing — see
``docs/youtube-oauth-setup.md`` for the exact command. Do not revoke via
https://myaccount.google.com/permissions unless decommissioning entirely —
that page revokes the whole app grant, including the new token, since both
share the same OAuth client.

It depends only on the Python standard library and ``podcaster.youtube_oauth``
so it can run anywhere without extra packages.

Usage:
    export VIDEO_YOUTUBE_CLIENT_ID=...        # from GCP OAuth2 desktop client
    export VIDEO_YOUTUBE_CLIENT_SECRET=...
    python scripts/youtube_oauth_setup.py

Stop rule: if the client id/secret are missing this script prints the exact
missing variable names and exits non-zero instead of attempting a workaround.
The refresh token is printed only to the operator's terminal — never logged,
committed, or echoed elsewhere. Store it immediately as a secret.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from podcaster.youtube_oauth import (  # noqa: E402
    DEFAULT_REDIRECT_URI,
    TOKEN_ENDPOINT,
    YOUTUBE_SCOPE,
    YOUTUBE_UPLOAD_SCOPE,
    OAuthClient,
    build_consent_url,
    build_token_exchange_payload,
    parse_redirect_query,
    parse_token_response,
    redact_secret,
)

_ENV_CLIENT_ID = "VIDEO_YOUTUBE_CLIENT_ID"
_ENV_CLIENT_SECRET = "VIDEO_YOUTUBE_CLIENT_SECRET"


def missing_client_context() -> list[str]:
    """Return the names of required env vars that are unset."""

    return [name for name in (_ENV_CLIENT_ID, _ENV_CLIENT_SECRET) if not os.environ.get(name)]


class _CallbackHandler(BaseHTTPRequestHandler):
    captured: dict[str, list[str]] = {}

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlsplit(self.path)
        if parsed.path != "/oauth2callback":
            self.send_response(404)
            self.end_headers()
            return
        type(self).captured = parse_qs(parsed.query)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>YouTube authorization received.</h2>"
            b"<p>You can close this tab and return to the terminal.</p></body></html>"
        )

    def log_message(self, *args: object) -> None:  # silence default logging
        return


def _exchange_code(client: OAuthClient, code: str, redirect_uri: str):
    payload = build_token_exchange_payload(client, code, redirect_uri)
    req = Request(
        TOKEN_ENDPOINT,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed Google endpoint)
        body = resp.read()
    return parse_token_response(body)


def run_consent_flow(
    client: OAuthClient,
    *,
    host: str = "127.0.0.1",
    open_browser: bool = True,
    scope: str = YOUTUBE_SCOPE,
):
    """Run the loopback consent flow and return a TokenResult."""

    client.require()
    state = secrets.token_urlsafe(24)

    server = HTTPServer((host, 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = DEFAULT_REDIRECT_URI.format(port=port)

    consent_url = build_consent_url(client, redirect_uri, scopes=[scope], state=state)
    print(f"\nRequesting scope: {scope}\n", file=sys.stderr)
    print("Open this URL in a browser signed in to the YouTube channel owner:\n", file=sys.stderr)
    print(consent_url + "\n", file=sys.stderr)
    if open_browser:
        try:
            webbrowser.open(consent_url)
        except Exception:
            pass

    print(f"Waiting for the consent redirect on {redirect_uri} ...", file=sys.stderr)
    server.handle_request()  # blocks until the single callback arrives
    server.server_close()

    code = parse_redirect_query(_CallbackHandler.captured, state)
    return _exchange_code(client, code, redirect_uri)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a YouTube OAuth2 refresh token (#441)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the consent URL but do not try to open a browser.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the refresh token as JSON to stdout (for piping into a secret store).",
    )
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help=(
            "Request the narrower youtube.upload scope instead of the default "
            f"({YOUTUBE_SCOPE}). Only use this against a separate, "
            "non-production OAuth client or a testing-mode consent screen "
            "(see docs/youtube-oauth-setup.md) -- the verified production "
            "consent screen must list the youtube scope only. Videos can be "
            "uploaded, but playlist management, read-back verification "
            "(videos.list), and status updates (videos.update) that promote "
            "a draft to public will fail with 403 insufficientPermissions."
        ),
    )
    args = parser.parse_args(argv)

    missing = missing_client_context()
    if missing:
        print(
            "Refusing to start consent flow: missing required environment variables:\n  "
            + "\n  ".join(missing),
            file=sys.stderr,
        )
        return 3

    client = OAuthClient(
        client_id=os.environ[_ENV_CLIENT_ID],
        client_secret=os.environ[_ENV_CLIENT_SECRET],
    )

    scope = YOUTUBE_UPLOAD_SCOPE if args.upload_only else YOUTUBE_SCOPE
    token = run_consent_flow(client, open_browser=not args.no_browser, scope=scope)

    if args.json:
        # Only the refresh token is emitted for piping into a secret store (#443).
        print(json.dumps({"refresh_token": token.refresh_token, "scope": token.scope}))
    else:
        print("\n✅ Refresh token obtained (store this in a secret manager):\n")
        print(token.refresh_token)
        print(f"\nScope granted: {token.scope}")
        print(f"Access token (short-lived, redacted): {redact_secret(token.access_token)}")
        print(
            "\nNext: store the refresh token as VIDEO_YOUTUBE_REFRESH_TOKEN — for "
            "this repo's production deployment, set it as the `prod` GitHub "
            "environment secret and redeploy (see docs/youtube-oauth-setup.md); "
            "for a deployment that resolves it directly from Azure Key Vault at "
            "runtime instead, store it there (see docs/youtube-token-storage.md). "
            "Never commit or log it."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
