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

import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from podcaster.auth_core import create_token, get_credentials, verify_token
from podcaster.credentials import CredentialStore
from podcaster.failure_reporting import report_failure
from podcaster.jobs import ReplayCollisionError, failed_response, run_generation_job
from podcaster.orchestration import process_review_decision
from podcaster.podcast_config import PodcastConfigStore
from podcaster.storage import create_storage_backend
from podcaster.validation import is_authorized, validate_payload_details

logger = logging.getLogger("podcaster.api")

DEFAULT_PORT = 8000
MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MiB

# Health check path for ACA liveness/readiness probes.
HEALTH_PATH = "/healthz"


# CORS is deny-by-default for the lightweight machine-to-machine API server.
# Set PODCASTER_CORS_ORIGINS to a comma-separated allowlist of trusted browser
# origins to opt in; a literal "*" is ignored (wildcard CORS on authenticated
# endpoints is a security risk, #607).
def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def _cors_allowlist() -> list[str]:
    raw = os.environ.get("PODCASTER_CORS_ORIGINS", "")
    allowlist: list[str] = []
    for candidate in raw.split(","):
        origin = candidate.strip()
        # Skip blanks, the wildcard, and — as defense-in-depth — any origin
        # carrying control characters (e.g. CR/LF). A misconfigured or tainted
        # env var must never be able to inject response headers through
        # Access-Control-Allow-Origin (#607).
        if not origin or origin == "*" or _has_control_chars(origin):
            continue
        allowlist.append(origin)
    return allowlist


def _cors_headers(handler: BaseHTTPRequestHandler) -> list[tuple[str, str]]:
    origin = handler.headers.get("Origin")
    # Echo back the matched entry from the trusted allowlist (sourced from
    # PODCASTER_CORS_ORIGINS), never the raw request header. This preserves the
    # exact-match semantics while ensuring the value written into the response
    # header comes from server configuration, not attacker-controlled input, so
    # it cannot be used for HTTP response splitting (CodeQL py/http-response-splitting).
    allowed_origin = next((o for o in _cors_allowlist() if o == origin), None)
    if allowed_origin is None:
        return []
    return [
        ("Access-Control-Allow-Origin", allowed_origin),
        ("Vary", "Origin"),
        ("Access-Control-Allow-Credentials", "true"),
        ("Access-Control-Allow-Methods", "POST, GET, PUT, DELETE, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type, Authorization, x-podcaster-api-key"),
    ]


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    for name, value in _cors_headers(handler):
        handler.send_header(name, value)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _empty_response(handler: BaseHTTPRequestHandler, status: int) -> None:
    handler.send_response(status)
    for name, value in _cors_headers(handler):
        handler.send_header(name, value)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


class GenerateHandler(BaseHTTPRequestHandler):
    """Handles POST /api/generate and GET /healthz."""

    # Suppress per-request logging to stderr (use structured logging instead).
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.OK)
        for name, value in _cors_headers(self):
            self.send_header(name, value)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == HEALTH_PATH:
            _json_response(self, HTTPStatus.OK, {"status": "healthy"})
            return
        if path == "/api/auth/me":
            GenerateHandler._handle_auth_me(self)
            return
        if path == "/api/credentials":
            GenerateHandler._handle_credentials_list(self)
            return
        if path == "/api/podcast-config":
            GenerateHandler._handle_podcast_config_get(self)
            return
        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/auth/login":
            GenerateHandler._handle_auth_login(self)
            return

        if path == "/api/credentials":
            GenerateHandler._handle_credentials_create(self)
            return

        if path == "/api/podcast-config":
            GenerateHandler._handle_podcast_config_save(self)
            return

        if path not in {"/api/generate", "/api/review"}:
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
            _json_response(
                self,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "request body too large"},
            )
            return

        try:
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            response = failed_response(["request body must be valid JSON"])
            _json_response(self, HTTPStatus.BAD_REQUEST, response)
            return
        if not isinstance(payload, dict):
            response = failed_response(["request body must be a JSON object"])
            _json_response(self, HTTPStatus.BAD_REQUEST, response)
            return

        if path == "/api/generate":
            GenerateHandler._handle_generate(self, payload)
            return
        GenerateHandler._handle_review(self, payload)

    def do_PUT(self) -> None:  # noqa: N802
        credential_id = GenerateHandler._credential_id_from_path(self)
        if credential_id is None:
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        GenerateHandler._handle_credentials_update(self, credential_id)

    def do_DELETE(self) -> None:  # noqa: N802
        credential_id = GenerateHandler._credential_id_from_path(self)
        if credential_id is None:
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        GenerateHandler._handle_credentials_delete(self, credential_id)

    # ------------------------------------------------------------------
    # Auth endpoints (#275)
    # ------------------------------------------------------------------

    def _handle_auth_login(self) -> None:
        """POST /api/auth/login — validate credentials, return JWT."""
        creds = get_credentials()
        if creds is None:
            _json_response(
                self,
                HTTPStatus.NOT_IMPLEMENTED,
                {"error": "Simple auth is not configured (UI_AUTH_* env vars missing)"},
            )
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_REQUEST_BODY:
            _json_response(
                self,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "request body too large"},
            )
            return

        try:
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            _json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {"error": "request body must be valid JSON"},
            )
            return
        if not isinstance(payload, dict):
            _json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {"error": "request body must be a JSON object"},
            )
            return

        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "").strip()
        if not username or not password:
            _json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {"error": "username and password are required"},
            )
            return

        expected_user, expected_pass, secret = creds
        if not hmac.compare_digest(username, expected_user) or not hmac.compare_digest(
            password, expected_pass
        ):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "Invalid username or password"})
            return

        token = create_token(username, secret)
        _json_response(self, HTTPStatus.OK, {"token": token, "username": username})

    def _handle_auth_me(self) -> None:
        """GET /api/auth/me — return current user from Bearer token."""
        creds = get_credentials()
        authorization = self.headers.get("Authorization", "")
        headers = {k: v for k, v in self.headers.items()}
        if authorization.startswith("Bearer ") and creds is not None:
            import jwt as _jwt

            try:
                payload = verify_token(authorization[7:], creds[2])
            except _jwt.PyJWTError:
                pass
            else:
                _json_response(self, HTTPStatus.OK, {"username": payload["sub"]})
                return

        if is_authorized(headers):
            _json_response(self, HTTPStatus.OK, {"username": "api-key-user"})
            return

        if creds is None and not os.environ.get("PODCASTER_API_KEY"):
            _json_response(
                self,
                HTTPStatus.NOT_IMPLEMENTED,
                {"error": "Simple auth is not configured"},
            )
            return

        _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "Invalid or missing credentials"})

    # ------------------------------------------------------------------

    def _credential_id_from_path(self) -> str | None:
        path = urlparse(self.path).path
        prefix = "/api/credentials/"
        if not path.startswith(prefix):
            return None
        credential_id = unquote(path[len(prefix) :]).strip()
        return credential_id or None

    def _is_request_authorized(self) -> bool:
        headers = {k: v for k, v in self.headers.items()}
        if is_authorized(headers):
            return True

        authorization = self.headers.get("Authorization", "")
        creds = get_credentials()
        if authorization.startswith("Bearer ") and creds is not None:
            import jwt as _jwt

            try:
                verify_token(authorization[7:], creds[2])
            except _jwt.PyJWTError:
                return False
            return True
        return False

    def _read_json_object(self) -> tuple[dict[str, Any] | None, str | None]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_REQUEST_BODY:
            return None, "request body too large"

        try:
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            return None, "request body must be valid JSON"
        if not isinstance(payload, dict):
            return None, "request body must be a JSON object"
        return payload, None

    def _handle_credentials_list(self) -> None:
        if not GenerateHandler._is_request_authorized(self):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            response = CredentialStore(create_storage_backend()).list_credentials()
        except RuntimeError as exc:
            status = (
                HTTPStatus.NOT_IMPLEMENTED
                if "UI_AUTH_SECRET" in str(exc)
                else HTTPStatus.INTERNAL_SERVER_ERROR
            )
            _json_response(self, status, {"error": str(exc)})
            return
        _json_response(self, HTTPStatus.OK, response)

    def _handle_credentials_create(self) -> None:
        if not GenerateHandler._is_request_authorized(self):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        payload, error = GenerateHandler._read_json_object(self)
        if error is not None:
            _json_response(
                self,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                if error == "request body too large"
                else HTTPStatus.BAD_REQUEST,
                {"error": error},
            )
            return
        assert payload is not None
        try:
            summary = CredentialStore(create_storage_backend()).create_credential(payload)
        except ValueError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except RuntimeError as exc:
            status = (
                HTTPStatus.NOT_IMPLEMENTED
                if "UI_AUTH_SECRET" in str(exc)
                else HTTPStatus.INTERNAL_SERVER_ERROR
            )
            _json_response(self, status, {"error": str(exc)})
            return
        _json_response(self, HTTPStatus.OK, summary)

    def _handle_credentials_update(self, credential_id: str) -> None:
        if not GenerateHandler._is_request_authorized(self):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        payload, error = GenerateHandler._read_json_object(self)
        if error is not None:
            _json_response(
                self,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                if error == "request body too large"
                else HTTPStatus.BAD_REQUEST,
                {"error": error},
            )
            return
        assert payload is not None
        try:
            summary = CredentialStore(create_storage_backend()).update_credential(
                credential_id,
                payload,
            )
        except ValueError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except RuntimeError as exc:
            status = (
                HTTPStatus.NOT_IMPLEMENTED
                if "UI_AUTH_SECRET" in str(exc)
                else HTTPStatus.INTERNAL_SERVER_ERROR
            )
            _json_response(self, status, {"error": str(exc)})
            return
        if summary is None:
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "credential not found"})
            return
        _json_response(self, HTTPStatus.OK, summary)

    def _handle_credentials_delete(self, credential_id: str) -> None:
        if not GenerateHandler._is_request_authorized(self):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            deleted = CredentialStore(create_storage_backend()).delete_credential(credential_id)
        except RuntimeError as exc:
            status = (
                HTTPStatus.NOT_IMPLEMENTED
                if "UI_AUTH_SECRET" in str(exc)
                else HTTPStatus.INTERNAL_SERVER_ERROR
            )
            _json_response(self, status, {"error": str(exc)})
            return
        if not deleted:
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "credential not found"})
            return
        _empty_response(self, HTTPStatus.NO_CONTENT)

    def _handle_podcast_config_get(self) -> None:
        if not GenerateHandler._is_request_authorized(self):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            response = PodcastConfigStore(create_storage_backend()).get()
        except RuntimeError as exc:
            _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        _json_response(self, HTTPStatus.OK, response)

    def _handle_podcast_config_save(self) -> None:
        if not GenerateHandler._is_request_authorized(self):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        payload, error = GenerateHandler._read_json_object(self)
        if error is not None:
            _json_response(
                self,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                if error == "request body too large"
                else HTTPStatus.BAD_REQUEST,
                {"error": error},
            )
            return
        assert payload is not None
        try:
            response = PodcastConfigStore(create_storage_backend()).save(payload)
        except ValueError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except RuntimeError as exc:
            _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        _json_response(self, HTTPStatus.OK, response)

    def _handle_generate(self, payload: dict[str, Any]) -> None:
        validation = validate_payload_details(payload)
        if validation.errors:
            response = failed_response(validation.errors, validation.warnings or None)
            _json_response(self, HTTPStatus.BAD_REQUEST, response)
            return

        try:
            result = run_generation_job(payload, validation_warnings=validation.warnings or None)
        except ReplayCollisionError:
            response = failed_response(["replay output already exists"])
            _json_response(self, HTTPStatus.CONFLICT, response)
            return
        except Exception:
            logger.exception("unhandled error in generation job")
            report_failure(
                container="podcaster-api",
                error_type="GenerateEndpointError",
                error_message="Unhandled exception in /api/generate",
            )
            response = failed_response(["internal server error"])
            _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, response)
            return

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

    def _handle_review(self, payload: dict[str, Any]) -> None:
        job_id = str(payload.get("job_id") or "").strip()
        reviewer = str(payload.get("reviewer") or "").strip()
        decision = str(payload.get("decision") or "").strip()
        notes = str(payload.get("notes") or "")
        run_url = str(payload.get("run_url") or "").strip() or None
        reviewed_at = str(payload.get("reviewed_at") or "").strip() or datetime.now(
            timezone.utc
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        publish_on_approval = payload.get("publish_on_approval", True) is not False
        errors: list[str] = []
        if not job_id:
            errors.append("job_id is required")
        if not reviewer:
            errors.append("reviewer is required")
        if decision not in {"approved", "changes_requested", "rejected"}:
            errors.append("decision must be approved, changes_requested, or rejected")
        if errors:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"errors": errors})
            return
        try:
            outcome = process_review_decision(
                job_id,
                reviewer=reviewer,
                decision=decision,
                reviewed_at=reviewed_at,
                notes=notes,
                run_url=run_url,
                publish_on_approval=publish_on_approval,
            )
        except ValueError as exc:
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        except Exception:
            logger.exception("unhandled error in review orchestration")
            report_failure(
                container="podcaster-api",
                error_type="ReviewEndpointError",
                error_message="Unhandled exception in /api/review",
            )
            _json_response(
                self,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "internal server error"},
            )
            return

        publish_result = outcome.publish_result
        _json_response(
            self,
            HTTPStatus.OK,
            {
                "job_id": job_id,
                "status": outcome.manifest.get("status"),
                "review_status": outcome.manifest.get("review_status"),
                "publish_status": publish_result.status if publish_result else None,
                "publish_error": publish_result.error if publish_result else None,
                "manifest": outcome.manifest,
            },
        )
        logger.info(
            "api_review job_id=%s decision=%s publish_status=%s",
            job_id,
            decision,
            publish_result.status if publish_result else "not_requested",
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
