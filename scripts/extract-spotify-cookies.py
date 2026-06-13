#!/usr/bin/env python3
"""Extract Spotify for Creators cookies via browser login.

Opens a browser window for interactive login, then saves sp_dc/sp_key
cookies to .env for use with set-spotify-secrets.sh.

Usage: python scripts/extract-spotify-cookies.py [--env-file .env]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


LOGIN_URL = "https://creators.spotify.com/"
COOKIE_NAMES = ("sp_dc", "sp_key")
DEFAULT_TIMEOUT_SECONDS = 300
LOGIN_POLL_SECONDS = 1.0
COOKIE_GRACE_SECONDS = 15.0
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Path to the .env file to create or update (default: %(default)s).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Seconds to wait for interactive login before timing out (default: %(default)s).",
    )
    return parser.parse_args()


def collect_spotify_cookies(context) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for cookie in context.cookies():
        name = cookie.get("name")
        value = cookie.get("value")
        if name in COOKIE_NAMES and isinstance(value, str) and value:
            cookies[name] = value
    return cookies


def is_dashboard_url(url: str) -> bool:
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    if not parsed.netloc.endswith("creators.spotify.com"):
        return False
    if path in ("", "/"):
        return False
    return "dashboard" in path or path.startswith("/pod")


def wait_for_login(page, context, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        cookies = collect_spotify_cookies(context)
        if "sp_dc" in cookies or is_dashboard_url(page.url):
            return True
        time.sleep(LOGIN_POLL_SECONDS)
    return False


def wait_for_required_cookies(context, timeout_seconds: float) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    cookies = collect_spotify_cookies(context)
    while time.monotonic() < deadline:
        if all(name in cookies for name in COOKIE_NAMES):
            return cookies
        time.sleep(LOGIN_POLL_SECONDS)
        cookies = collect_spotify_cookies(context)
    return cookies


def upsert_env_contents(existing_text: str, updates: dict[str, str]) -> str:
    lines = existing_text.splitlines()
    output_lines: list[str] = []
    seen: set[str] = set()

    for line in lines:
        key, separator, _ = line.partition("=")
        if separator and key in updates:
            if key in seen:
                continue
            output_lines.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
        output_lines.append(line)

    for key in COOKIE_NAMES:
        if key.upper() in updates and key.upper() not in seen:
            output_lines.append(f"{key.upper()}={updates[key.upper()]}")

    return "\n".join(output_lines).rstrip("\n") + "\n"


def write_env_file(env_file: Path, cookies: dict[str, str]) -> None:
    updates = {
        "SP_DC": cookies["sp_dc"],
        "SP_KEY": cookies["sp_key"],
    }
    existing_text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    env_file.write_text(upsert_env_contents(existing_text, updates), encoding="utf-8")


def main() -> int:
    args = parse_args()
    env_file = Path(args.env_file)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    print("Opening Chromium for Spotify for Creators login...")
    print(f"Navigate if needed: {LOGIN_URL}")
    print(f"Waiting up to {args.timeout} seconds for login to complete.")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded")
                if not wait_for_login(page, context, args.timeout):
                    print("Timed out waiting for Spotify login. No cookies were written.", file=sys.stderr)
                    return 1

                cookies = wait_for_required_cookies(context, COOKIE_GRACE_SECONDS)
                missing = [name for name in COOKIE_NAMES if name not in cookies]
                if missing:
                    print(
                        f"Warning: logged in, but could not find required cookies: {', '.join(missing)}",
                        file=sys.stderr,
                    )
                    return 1

                write_env_file(env_file, cookies)
            finally:
                browser.close()
    except Exception as exc:
        print(f"Failed to launch or use Playwright: {exc}", file=sys.stderr)
        if "Executable doesn't exist" in str(exc):
            print("pip install playwright && playwright install chromium", file=sys.stderr)
        return 1

    print(f"Saved SP_DC and SP_KEY to {env_file}")
    print("Next: run ./scripts/set-spotify-secrets.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
