#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


REQUIRED_RESPONSE_FIELDS = ("job_id", "manifest_url", "errors")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a deployed Podcaster /api/generate endpoint.")
    parser.add_argument("--endpoint", default=os.environ.get("PODCASTER_GENERATE_URL"), help="Full /api/generate URL.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("PODCASTER_API_KEY"),
        help="Podcaster API key. Prefer PODCASTER_API_KEY so it is not stored in shell history.",
    )
    parser.add_argument(
        "--payload",
        default="tests/fixtures/podcaster_request_squadscope_objects.json",
        help="JSON request payload path.",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    if not args.endpoint:
        print("PODCASTER_GENERATE_URL or --endpoint is required", file=sys.stderr)
        return 2
    if not args.api_key:
        print("PODCASTER_API_KEY or --api-key is required", file=sys.stderr)
        return 2

    try:
        payload = load_payload(Path(args.payload))
        status_code, body = post_generate(args.endpoint, args.api_key, payload, args.timeout)
        summary = validate_smoke_response(status_code, body)
    except SmokeError as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        return 1

    print("smoke passed")
    print(f"http_status={summary['http_status']}")
    print(f"job_id={summary['job_id']}")
    print(f"manifest_url={summary['manifest_url']}")
    print("errors=[]")
    return 0


class SmokeError(Exception):
    pass


def load_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SmokeError(f"cannot read payload {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeError(f"payload {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SmokeError(f"payload {path} must be a JSON object")
    return payload


def post_generate(endpoint: str, api_key: str, payload: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]:
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-podcaster-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.status
            body_bytes = response.read()
    except HTTPError as exc:
        status_code = exc.code
        body_bytes = exc.read()
    except URLError as exc:
        raise SmokeError(f"request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SmokeError("request timed out") from exc

    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeError(f"response was not valid JSON (HTTP {status_code})") from exc
    if not isinstance(body, dict):
        raise SmokeError(f"response JSON must be an object (HTTP {status_code})")
    return status_code, body


def validate_smoke_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    if status_code != 202:
        raise SmokeError(f"expected HTTP 202, got HTTP {status_code}: {safe_error_summary(body)}")

    missing = [field for field in REQUIRED_RESPONSE_FIELDS if field not in body]
    if missing:
        raise SmokeError(f"response missing required field(s): {', '.join(missing)}")

    job_id = body["job_id"]
    manifest_url = body["manifest_url"]
    errors = body["errors"]
    if not isinstance(job_id, str) or not job_id:
        raise SmokeError("response job_id must be a non-empty string")
    if not isinstance(manifest_url, str) or not manifest_url:
        raise SmokeError("response manifest_url must be a non-empty string")
    if errors != []:
        raise SmokeError(f"response errors must be empty: {safe_error_summary(body)}")

    return {
        "http_status": status_code,
        "job_id": job_id,
        "manifest_url": redact_url(manifest_url),
    }


def redact_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.query and not parsed.fragment:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "[redacted-query]", ""))


def safe_error_summary(body: dict[str, Any]) -> str:
    errors = body.get("errors")
    if isinstance(errors, list):
        return json.dumps({"errors": errors})
    return "{}"


if __name__ == "__main__":
    raise SystemExit(main())
