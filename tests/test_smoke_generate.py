from __future__ import annotations

import pytest

from scripts.smoke_generate import SmokeError, redact_url, validate_smoke_response


def test_validate_smoke_response_accepts_required_deploy_shape() -> None:
    summary = validate_smoke_response(
        202,
        {
            "job_id": "podcast-2026-W23-abc12345",
            "manifest_url": "https://storage.example/jobs/podcast-2026-W23-abc12345/manifest.json",
            "errors": [],
        },
    )

    assert summary == {
        "http_status": 202,
        "job_id": "podcast-2026-W23-abc12345",
        "manifest_url": "https://storage.example/jobs/podcast-2026-W23-abc12345/manifest.json",
    }


def test_validate_smoke_response_redacts_manifest_url_query() -> None:
    summary = validate_smoke_response(
        202,
        {
            "job_id": "podcast-2026-W23-abc12345",
            "manifest_url": "https://storage.example/jobs/podcast-2026-W23-abc12345/manifest.json?sig=secret&se=tomorrow",
            "errors": [],
        },
    )

    assert summary["manifest_url"] == "https://storage.example/jobs/podcast-2026-W23-abc12345/manifest.json?[redacted-query]"
    assert "secret" not in summary["manifest_url"]


@pytest.mark.parametrize(
    ("status_code", "body", "message"),
    [
        (401, {"errors": ["unauthorized"]}, "expected HTTP 202"),
        (202, {"manifest_url": "https://storage.example/manifest.json", "errors": []}, "missing required field"),
        (202, {"job_id": "", "manifest_url": "https://storage.example/manifest.json", "errors": []}, "job_id"),
        (202, {"job_id": "job", "manifest_url": "", "errors": []}, "manifest_url"),
        (202, {"job_id": "job", "manifest_url": "https://storage.example/manifest.json", "errors": ["bad"]}, "errors"),
    ],
)
def test_validate_smoke_response_rejects_non_ready_shapes(
    status_code: int, body: dict[str, object], message: str
) -> None:
    with pytest.raises(SmokeError, match=message):
        validate_smoke_response(status_code, body)


def test_redact_url_removes_query_and_fragment() -> None:
    assert redact_url("https://storage.example/manifest.json?sig=secret#fragment") == (
        "https://storage.example/manifest.json?[redacted-query]"
    )
