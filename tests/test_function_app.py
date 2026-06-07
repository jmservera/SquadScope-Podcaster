from __future__ import annotations

import json

import azure.functions as func

from function_app import generate
from podcaster.validation import RESPONSE_KEYS


def _request(body: dict[str, object], headers: dict[str, str] | None = None) -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url="http://localhost/api/generate",
        headers=headers or {},
        params={},
        route_params={},
        body=json.dumps(body).encode("utf-8"),
    )


def test_generate_endpoint_returns_accepted_shape(monkeypatch) -> None:
    monkeypatch.setenv("PODCASTER_API_KEY", "expected")
    response = generate(_request({"week": "2026-W23", "article_url": "https://example.com/article"}, {"x-podcaster-api-key": "expected"}))
    body = json.loads(response.get_body())
    assert response.status_code == 202
    assert tuple(body.keys()) == RESPONSE_KEYS
    assert body["status"] == "accepted"
    assert body["errors"] == []


def test_generate_endpoint_rejects_unauthorized(monkeypatch) -> None:
    monkeypatch.setenv("PODCASTER_API_KEY", "expected")
    response = generate(_request({"week": "2026-W23", "article_url": "https://example.com/article"}, {"x-podcaster-api-key": "wrong"}))
    body = json.loads(response.get_body())
    assert response.status_code == 401
    assert body["errors"] == ["unauthorized"]


def test_generate_endpoint_rejects_invalid_payload(monkeypatch) -> None:
    monkeypatch.setenv("PODCASTER_API_KEY", "expected")
    response = generate(_request({}, {"x-podcaster-api-key": "expected"}))
    body = json.loads(response.get_body())
    assert response.status_code == 400
    assert "week is required" in body["errors"]
    assert "article_url is required" in body["errors"]
