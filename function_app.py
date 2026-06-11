from __future__ import annotations

import json
import logging
from typing import Any

import azure.functions as func

from podcaster.jobs import failed_response, run_generation_job
from podcaster.validation import empty_error_response, is_authorized, validate_payload_details

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="generate", methods=["POST"])
def generate(req: func.HttpRequest) -> func.HttpResponse:
    if not is_authorized(dict(req.headers)):
        return _json_response(empty_error_response(["unauthorized"]), status_code=401)

    try:
        payload: Any = req.get_json()
    except ValueError:
        return _json_response(empty_error_response(["request body must be valid JSON"]), status_code=400)

    validation = validate_payload_details(payload)
    if validation.errors:
        return _json_response(empty_error_response(validation.errors, validation.warnings), status_code=400)

    logging.info("accepted podcaster request for week=%s", payload.get("week"))
    try:
        result = run_generation_job(payload, validation_warnings=validation.warnings)
    except Exception:
        logging.exception("podcaster generation failed for week=%s", payload.get("week"))
        return _json_response(failed_response(["generation failed; retry later or contact operator"]), status_code=500)

    return _json_response(result.response, status_code=202)


def _json_response(body: dict[str, Any], status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body),
        status_code=status_code,
        mimetype="application/json",
    )
