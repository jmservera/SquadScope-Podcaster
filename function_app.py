from __future__ import annotations

import json
import logging
from typing import Any

import azure.functions as func

from podcaster.validation import build_stub_response, empty_error_response, is_authorized, validate_payload

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="generate", methods=["POST"])
def generate(req: func.HttpRequest) -> func.HttpResponse:
    if not is_authorized(dict(req.headers)):
        return _json_response(empty_error_response(["unauthorized"]), status_code=401)

    try:
        payload: Any = req.get_json()
    except ValueError:
        return _json_response(empty_error_response(["request body must be valid JSON"]), status_code=400)

    errors = validate_payload(payload)
    if errors:
        return _json_response(empty_error_response(errors), status_code=400)

    logging.info("accepted podcaster request for week=%s", payload.get("week"))
    return _json_response(build_stub_response(payload), status_code=202)


def _json_response(body: dict[str, Any], status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body),
        status_code=status_code,
        mimetype="application/json",
    )
