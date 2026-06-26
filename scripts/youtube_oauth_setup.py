#!/usr/bin/env python3
"""One-time YouTube OAuth2 consent flow to mint a refresh token (#441).

Service accounts cannot upload to YouTube, so an operator must grant user
consent once. This script runs the installed-app (loopback) authorization-code
flow against the OAuth2 *Desktop* client created in Google Cloud, then prints
the resulting refresh token for secure storage (Azure Key Vault, #443).

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


def run_consent_flow(client: OAuthClient, *, host: str = "127.0.0.1", open_browser: bool = True):
    """Run the loopback consent flow and return a TokenResult."""

    client.require()
    state = secrets.token_urlsafe(24)

    server = HTTPServer((host, 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = DEFAULT_REDIRECT_URI.format(port=port)

    consent_url = build_consent_url(
        client, redirect_uri, scopes=[YOUTUBE_UPLOAD_SCOPE], state=state
    )
    print("\nOpen this URL in a browser signed in to the YouTube channel owner:\n")
    print(consent_url + "\n")
    if open_browser:
        try:
            webbrowser.open(consent_url)
        except Exception:
            pass

    print(f"Waiting for the consent redirect on {redirect_uri} ...")
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

    token = run_consent_flow(client, open_browser=not args.no_browser)

    if args.json:
        # Only the refresh token is emitted for piping into Key Vault (#443).
        print(json.dumps({"refresh_token": token.refresh_token, "scope": token.scope}))
    else:
        print("\n✅ Refresh token obtained (store this as a secret — Key Vault #443):\n")
        print(token.refresh_token)
        print(f"\nScope granted: {token.scope}")
        print(f"Access token (short-lived, redacted): {redact_secret(token.access_token)}")
        print(
            "\nNext: store the refresh token as VIDEO_YOUTUBE_REFRESH_TOKEN in Azure "
            "Key Vault. Never commit or log it."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
