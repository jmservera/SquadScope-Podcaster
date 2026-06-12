"""Lightweight HTTP API server for /api/generate (#131).

This is the thin HTTP front door that replaces the removed Function App. It
handles request validation, job creation, artifact staging, synthesis enqueue,
and returns the stable 202 response per the integration contract.

Runs as the ACA App entrypoint with HTTP ingress. No external web framework
required — uses only the standard library ``http.server`` module for minimal
image size and attack surface. For production, front with ACA's built-in TLS
termination and ingress.

Security:
- API key auth via x-podcaster-api-key header (constant-time comparison)
- Never logs tokens, keys, or full request bodies
- Returns structured errors without leaking internals
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from podcaster.jobs import failed_response, run_generation_job
from podcaster.validation import is_authorized, validate_payload_details

logger = logging.getLogger("podcaster.api")

DEFAULT_PORT = 8000
MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MiB

# Health check path for ACA liveness/readiness probes.
HEALTH_PATH = "/healthz"


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class GenerateHandler(BaseHTTPRequestHandler):
    """Handles POST /api/generate and GET /healthz."""

    # Suppress per-request logging to stderr (use structured logging instead).
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == HEALTH_PATH:
            _json_response(self, HTTPStatus.OK, {"status": "healthy"})
            return
        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/generate":
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        # Auth check
        headers = {k: v for k, v in self.headers.items()}
        if not is_authorized(headers):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        # Read body
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_REQUEST_BODY:
            _json_response(self, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request body too large"})
            return

        try:
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            response = failed_response(["request body must be valid JSON"])
            _json_response(self, HTTPStatus.BAD_REQUEST, response)
            return

        # Validate
        validation = validate_payload_details(payload)
        if validation.errors:
            response = failed_response(validation.errors, validation.warnings or None)
            _json_response(self, HTTPStatus.BAD_REQUEST, response)
            return

        # Run generation job
        try:
            result = run_generation_job(payload, validation_warnings=validation.warnings or None)
        except Exception:
            logger.exception("unhandled error in generation job")
            response = failed_response(["internal server error"])
            _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, response)
            return

        # Determine status code
        status_code = HTTPStatus.ACCEPTED
        if result.response.get("status") == "failed":
            status_code = HTTPStatus.BAD_REQUEST
        elif result.response.get("status") == "dry_run":
            status_code = HTTPStatus.OK

        _json_response(self, status_code, result.response)
        logger.info(
            "api_generate job_id=%s status=%s dry_run=%s",
            result.response.get("job_id"),
            result.response.get("status"),
            bool(payload.get("dry_run")),
        )


def main() -> None:
    """Start the HTTP API server."""
    port = int(os.environ.get("PODCASTER_API_PORT", str(DEFAULT_PORT)))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    if not os.environ.get("PODCASTER_API_KEY"):
        logger.error("PODCASTER_API_KEY is not configured; the API will reject all requests.")

    server = HTTPServer(("0.0.0.0", port), GenerateHandler)
    logger.info("podcaster API server listening on port %d", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
