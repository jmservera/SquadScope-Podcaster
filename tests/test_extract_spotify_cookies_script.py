from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "extract-spotify-cookies.py"
SPEC = importlib.util.spec_from_file_location("extract_spotify_cookies", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_upsert_env_contents_replaces_cookie_lines_and_preserves_other_values() -> None:
    existing = "\n".join(
        [
            "EXISTING=value",
            "SP_DC=old-dc",
            "# comment",
            "SP_KEY=old-key",
            "",
        ]
    )

    rendered = MODULE.upsert_env_contents(existing, {"SP_DC": "new-dc", "SP_KEY": "new-key"})

    assert rendered == "EXISTING=value\nSP_DC=new-dc\n# comment\nSP_KEY=new-key\n"


def test_upsert_env_contents_appends_missing_cookie_lines_once() -> None:
    existing = "EXISTING=value\nSP_DC=stale-dc\nSP_DC=duplicate-dc\n"

    rendered = MODULE.upsert_env_contents(existing, {"SP_DC": "fresh-dc", "SP_KEY": "fresh-key"})

    assert rendered == "EXISTING=value\nSP_DC=fresh-dc\nSP_KEY=fresh-key\n"


def test_is_dashboard_url_detects_spotify_dashboard_paths() -> None:
    assert MODULE.is_dashboard_url("https://creators.spotify.com/pod/dashboard")
    assert MODULE.is_dashboard_url("https://creators.spotify.com/pod/show/123")
    assert not MODULE.is_dashboard_url("https://creators.spotify.com/")
    assert not MODULE.is_dashboard_url("https://notcreators.spotify.com/pod/dashboard")
    assert not MODULE.is_dashboard_url("https://example.com/pod/dashboard")
