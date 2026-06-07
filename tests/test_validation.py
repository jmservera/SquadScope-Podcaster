from __future__ import annotations

from datetime import datetime, timezone

from podcaster.validation import RESPONSE_KEYS, build_stub_response, empty_error_response, is_authorized, validate_payload


def test_valid_minimal_payload_has_no_errors() -> None:
    errors = validate_payload({"week": "2026-W23", "article_url": "https://example.com/article"})
    assert errors == []


def test_missing_required_fields_return_errors() -> None:
    errors = validate_payload({})
    assert "week is required" in errors
    assert "article_url is required" in errors


def test_rejects_bad_types_and_urls() -> None:
    errors = validate_payload(
        {
            "week": "2026 W23",
            "article_url": "ftp://example.com/article",
            "article_sha256": "not-a-sha",
            "source_artifacts": ["ok", 123],
            "dry_run": "yes",
            "force": "no",
            "callback": {"url": "mailto:test@example.com", "secret_name": 7},
        }
    )
    assert "week contains unsupported characters" in errors
    assert "article_url must be an http or https URL" in errors
    assert "article_sha256 must be a lowercase hex SHA-256 digest" in errors
    assert "source_artifacts must be an array of strings" in errors
    assert "dry_run must be a boolean" in errors
    assert "force must be a boolean" in errors
    assert "callback.url must be an http or https URL" in errors
    assert "callback.secret_name must be a string" in errors


def test_stub_response_shape_is_contract_complete() -> None:
    response = build_stub_response(
        {"week": "2026-W23", "article_url": "https://example.com/article", "dry_run": True},
        now=datetime(2026, 6, 7, 17, 41, 40, tzinfo=timezone.utc),
    )
    assert tuple(response.keys()) == RESPONSE_KEYS
    assert response["job_id"].startswith("podcast-2026-W23-")
    assert response["status"] == "dry_run"
    assert response["manifest_url"].endswith(f"/{response['job_id']}.json")
    assert response["mp3_url"].endswith(f"/{response['job_id']}.mp3")
    assert response["wav_url"] is None
    assert response["expires_at"] == "2026-06-14T17:41:40Z"
    assert response["errors"] == []


def test_error_response_shape_is_contract_complete() -> None:
    response = empty_error_response(["bad request"])
    assert tuple(response.keys()) == RESPONSE_KEYS
    assert response["status"] == "failed"
    assert response["errors"] == ["bad request"]


def test_api_key_auth_fails_closed_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("PODCASTER_API_KEY", raising=False)
    assert is_authorized({}) is False


def test_api_key_auth_uses_header_without_exposing_secret(monkeypatch) -> None:
    monkeypatch.setenv("PODCASTER_API_KEY", "expected")
    assert is_authorized({"x-podcaster-api-key": "expected"}) is True
    assert is_authorized({"x-podcaster-api-key": "wrong"}) is False
