from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.youtube_oauth_setup as cli  # noqa: E402
from podcaster.youtube_oauth import (  # noqa: E402
    YOUTUBE_UPLOAD_SCOPE,
    OAuthClient,
    build_consent_url,
    build_token_exchange_payload,
    parse_redirect_query,
    parse_token_response,
    redact_secret,
)

CLIENT = OAuthClient(client_id="cid.apps.googleusercontent.com", client_secret="secret")


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def test_consent_url_requests_offline_refresh_token():
    url = build_consent_url(CLIENT, "http://127.0.0.1:5000/oauth2callback", state="xyz")
    q = _query(url)
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    assert q["response_type"] == ["code"]
    assert q["scope"] == [YOUTUBE_UPLOAD_SCOPE]
    assert q["state"] == ["xyz"]
    assert q["client_id"] == ["cid.apps.googleusercontent.com"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")


def test_consent_url_uses_minimal_upload_scope_only():
    url = build_consent_url(CLIENT, "http://127.0.0.1:5000/oauth2callback", state="s")
    scope = _query(url)["scope"][0]
    assert scope == YOUTUBE_UPLOAD_SCOPE
    assert "force-ssl" not in scope


def test_consent_url_requires_state_and_redirect():
    with pytest.raises(ValueError):
        build_consent_url(CLIENT, "http://127.0.0.1:5000/oauth2callback", state="")
    with pytest.raises(ValueError):
        build_consent_url(CLIENT, "", state="s")
    with pytest.raises(ValueError):
        build_consent_url(OAuthClient("", ""), "http://x/cb", state="s")


def test_token_exchange_payload_is_authorization_code_grant():
    payload = build_token_exchange_payload(CLIENT, "auth-code", "http://127.0.0.1:5000/oauth2callback")
    parsed = parse_qs(payload.decode())
    assert parsed["grant_type"] == ["authorization_code"]
    assert parsed["code"] == ["auth-code"]
    assert parsed["client_secret"] == ["secret"]


def test_parse_token_response_requires_refresh_token():
    good = json.dumps(
        {
            "refresh_token": "1//rt",
            "access_token": "at",
            "expires_in": 3599,
            "scope": YOUTUBE_UPLOAD_SCOPE,
            "token_type": "Bearer",
        }
    )
    result = parse_token_response(good)
    assert result.refresh_token == "1//rt"
    assert result.expires_in == 3599

    with pytest.raises(RuntimeError, match="missing refresh_token"):
        parse_token_response(json.dumps({"access_token": "at"}))


def test_parse_token_response_surfaces_error():
    body = json.dumps({"error": "invalid_grant", "error_description": "bad code"})
    with pytest.raises(RuntimeError, match="invalid_grant"):
        parse_token_response(body)


def test_parse_redirect_query_validates_state_and_errors():
    assert parse_redirect_query({"code": ["abc"], "state": ["s"]}, "s") == "abc"
    with pytest.raises(RuntimeError, match="state mismatch"):
        parse_redirect_query({"code": ["abc"], "state": ["other"]}, "s")
    with pytest.raises(RuntimeError, match="denied or failed"):
        parse_redirect_query({"error": ["access_denied"], "state": ["s"]}, "s")
    with pytest.raises(RuntimeError, match="missing authorization code"):
        parse_redirect_query({"state": ["s"]}, "s")


def test_redact_secret_hides_all_but_tail():
    assert redact_secret("supersecrettoken") == "************oken"
    assert redact_secret("ab") == "**"
    assert redact_secret("") == ""


def test_cli_missing_context_lists_exact_vars(monkeypatch):
    for name in ("VIDEO_YOUTUBE_CLIENT_ID", "VIDEO_YOUTUBE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    missing = cli.missing_client_context()
    assert "VIDEO_YOUTUBE_CLIENT_ID" in missing
    assert "VIDEO_YOUTUBE_CLIENT_SECRET" in missing


def test_cli_refuses_without_client_context(monkeypatch, capsys):
    for name in ("VIDEO_YOUTUBE_CLIENT_ID", "VIDEO_YOUTUBE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    rc = cli.main([])
    assert rc == 3
    err = capsys.readouterr().err
    assert "VIDEO_YOUTUBE_CLIENT_ID" in err
    assert "Refusing to start" in err
