from __future__ import annotations

import shutil
from datetime import datetime, timezone
import json
from pathlib import Path

from podcaster.validation import RESPONSE_KEYS, build_stub_response, empty_error_response, is_authorized, validate_payload


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def test_valid_minimal_payload_has_no_errors() -> None:
    errors = validate_payload({"week": "2026-W23", "article_url": "https://example.com/article"})
    assert errors == []


def test_legacy_string_source_artifacts_fixture_has_no_errors() -> None:
    payload = json.loads((FIXTURE_ROOT / "podcaster_request_legacy_strings.json").read_text(encoding="utf-8"))
    assert validate_payload(payload) == []


def test_squadscope_object_source_artifacts_fixture_has_no_errors() -> None:
    payload = json.loads((FIXTURE_ROOT / "podcaster_request_squadscope_objects.json").read_text(encoding="utf-8"))
    assert validate_payload(payload) == []


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
            "cost_override": {"actor": "", "reason": "", "recorded_at": ""},
            "callback": {"url": "mailto:test@example.com", "secret_name": 7},
        }
    )
    assert "week contains unsupported characters" in errors
    assert "article_url must be an http or https URL" in errors
    assert "article_sha256 must be a lowercase hex SHA-256 digest" in errors
    assert "source_artifacts[1] must be a string or source artifact object" in errors
    assert "dry_run must be a boolean" in errors
    assert "force must be a boolean" in errors
    assert "cost_override.actor is required" in errors
    assert "cost_override.reason is required" in errors
    assert "cost_override.recorded_at is required" in errors
    assert "cost_override requires force=true" in errors
    assert "callback.url must be an http or https URL" in errors
    assert "callback.secret_name must be a string" in errors


def test_rejects_malformed_source_artifact_objects() -> None:
    errors = validate_payload(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "source_artifacts": [
                {},
                {
                    "role": 7,
                    "path": "data/raw/2026-W23.json",
                    "sha256": "bad-sha",
                    "exists": "yes",
                    "size_bytes": -1,
                    "freshness": [],
                    "unexpected": "value",
                },
                {"url": "ftp://example.com/source.json"},
            ],
        }
    )
    assert "source_artifacts[0] must include path, url, href, uri, or name" in errors
    assert "source_artifacts[1] contains unsupported fields: unexpected" in errors
    assert "source_artifacts[1].role must be a string" in errors
    assert "source_artifacts[1].sha256 must be a lowercase hex SHA-256 digest" in errors
    assert "source_artifacts[1].exists must be a boolean" in errors
    assert "source_artifacts[1].size_bytes must be a non-negative integer" in errors
    assert "source_artifacts[1].freshness must be an object" in errors
    assert "source_artifacts[2].url must be an http or https URL" in errors


def test_accepts_source_artifact_object_reference_fields() -> None:
    errors = validate_payload(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "source_artifacts": [
                {"href": "https://example.com/source.json"},
                {"uri": "https://example.com/source-2.json"},
                {"name": "operator-note"},
            ],
        }
    )
    assert errors == []


def test_stub_response_shape_is_contract_complete(monkeypatch) -> None:
    artifact_root = Path(".test-artifacts")
    shutil.rmtree(artifact_root, ignore_errors=True)
    monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(artifact_root))
    response = build_stub_response(
        {"week": "2026-W23", "article_url": "https://example.com/article", "dry_run": True},
        now=datetime(2026, 6, 7, 17, 41, 40, tzinfo=timezone.utc),
    )
    assert tuple(response.keys()) == RESPONSE_KEYS
    assert response["job_id"].startswith("podcast-2026-W23-")
    assert response["status"] == "dry_run"
    assert response["manifest_url"].endswith(f"/jobs/{response['job_id']}/manifest.json")
    assert response["mp3_url"].endswith(f"/audio/{response['job_id']}.mp3")
    assert response["wav_url"] is None
    assert response["expires_at"] == "2026-06-14T17:41:40Z"
    assert response["errors"] == []
    assert (artifact_root / "jobs" / response["job_id"] / "manifest.json").exists()
    shutil.rmtree(artifact_root, ignore_errors=True)


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
