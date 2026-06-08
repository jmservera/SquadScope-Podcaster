from __future__ import annotations

import json
import shutil
from pathlib import Path

import azure.functions as func

import function_app
from function_app import generate
from podcaster.validation import RESPONSE_KEYS


def _request(body: object, headers: dict[str, str] | None = None) -> func.HttpRequest:
    return _raw_request(json.dumps(body).encode("utf-8"), headers)


def _raw_request(body: bytes, headers: dict[str, str] | None = None) -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url="http://localhost/api/generate",
        headers=headers or {},
        params={},
        route_params={},
        body=body,
    )


def test_generate_endpoint_returns_accepted_shape(monkeypatch) -> None:
    artifact_root = Path(".test-artifacts")
    shutil.rmtree(artifact_root, ignore_errors=True)
    monkeypatch.setenv("PODCASTER_API_KEY", "expected")
    monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(artifact_root))
    response = generate(_request({"week": "2026-W23", "article_url": "https://example.com/article"}, {"x-podcaster-api-key": "expected"}))
    body = json.loads(response.get_body())
    assert response.status_code == 202
    assert tuple(body.keys()) == RESPONSE_KEYS
    assert body["status"] == "accepted"
    assert body["errors"] == []
    assert "expected" not in response.get_body().decode("utf-8")
    assert (artifact_root / "jobs" / body["job_id"] / "packets" / f"{body['job_id']}.zip").exists()
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_generate_endpoint_accepts_squadscope_source_artifacts_fixture(monkeypatch) -> None:
    artifact_root = Path(".test-artifacts-squadscope")
    shutil.rmtree(artifact_root, ignore_errors=True)
    monkeypatch.setenv("PODCASTER_API_KEY", "expected")
    monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(artifact_root))
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "podcaster_request_squadscope_objects.json").read_text(encoding="utf-8")
    )

    response = generate(_request(payload, {"x-podcaster-api-key": "expected"}))

    body = json.loads(response.get_body())
    assert response.status_code == 202
    assert tuple(body.keys()) == RESPONSE_KEYS
    assert body["job_id"]
    assert body["manifest_url"]
    assert body["errors"] == []
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_generate_endpoint_rejects_unauthorized(monkeypatch) -> None:
    monkeypatch.setenv("PODCASTER_API_KEY", "expected")
    response = generate(_request({"week": "2026-W23", "article_url": "https://example.com/article"}, {"x-podcaster-api-key": "wrong"}))
    body = json.loads(response.get_body())
    assert response.status_code == 401
    assert tuple(body.keys()) == RESPONSE_KEYS
    assert body["status"] == "failed"
    assert body["errors"] == ["unauthorized"]
    assert "wrong" not in response.get_body().decode("utf-8")


def test_generate_endpoint_rejects_invalid_payload(monkeypatch) -> None:
    monkeypatch.setenv("PODCASTER_API_KEY", "expected")
    response = generate(_request({}, {"x-podcaster-api-key": "expected"}))
    body = json.loads(response.get_body())
    assert response.status_code == 400
    assert tuple(body.keys()) == RESPONSE_KEYS
    assert "week is required" in body["errors"]
    assert "article_url is required" in body["errors"]


def test_generate_endpoint_rejects_malformed_json_with_contract_shape(monkeypatch) -> None:
    monkeypatch.setenv("PODCASTER_API_KEY", "expected")
    response = generate(_raw_request(b'{"week":', {"x-podcaster-api-key": "expected"}))
    body = json.loads(response.get_body())
    assert response.status_code == 400
    assert tuple(body.keys()) == RESPONSE_KEYS
    assert body["status"] == "failed"
    assert body["errors"] == ["request body must be valid JSON"]


def test_generate_endpoint_generation_failure_keeps_contract_and_hides_secret(monkeypatch) -> None:
    def boom(payload: dict[str, object]) -> object:
        del payload
        raise RuntimeError("backend exploded")

    monkeypatch.setenv("PODCASTER_API_KEY", "dont-leak-me")
    monkeypatch.setattr(function_app, "run_generation_job", boom)
    response = generate(_request({"week": "2026-W23", "article_url": "https://example.com/article"}, {"x-podcaster-api-key": "dont-leak-me"}))
    body_text = response.get_body().decode("utf-8")
    body = json.loads(body_text)
    assert response.status_code == 500
    assert tuple(body.keys()) == RESPONSE_KEYS
    assert body["status"] == "failed"
    assert body["errors"] == ["generation failed; retry later or contact operator"]
    assert "dont-leak-me" not in body_text
