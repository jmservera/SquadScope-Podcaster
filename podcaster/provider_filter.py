"""Fail closed on reported chat-completion filtering failures."""

from __future__ import annotations

import re
from typing import Any
from urllib.error import HTTPError


class FilterSafetyError(ValueError):
    """A provider filtering failure must not produce a publishable artifact."""

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = _safe_id(request_id)


def _safe_id(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        return value
    return None


def raise_on_blocked_http(error: HTTPError) -> None:
    if error.code == 400:
        headers = error.headers
        request_id = headers.get("x-ms-request-id") if headers is not None else None
        raise FilterSafetyError(
            "chat completion rejected by provider (HTTP 400)", request_id=request_id
        ) from None


def validate_chat_filter_response(response: Any) -> None:
    """Inspect reported annotations without requiring optional annotations to exist."""
    if not isinstance(response, dict):
        raise FilterSafetyError("malformed chat completion")
    request_id = _safe_id(response.get("id"))
    if "error" in response:
        raise FilterSafetyError("chat completion reported an error", request_id=request_id)

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise FilterSafetyError("malformed filter annotation", request_id=request_id)
                name = key.lower()
                if name == "error" or name.endswith("_error") or name == "innererror":
                    raise FilterSafetyError(
                        "chat completion reported a filter error", request_id=request_id
                    )
                if name == "filtered":
                    if not isinstance(child, bool) or child:
                        raise FilterSafetyError(
                            "chat completion reported filtering", request_id=request_id
                        )
                else:
                    inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    if "prompt_filter_results" in response:
        results = response["prompt_filter_results"]
        if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
            raise FilterSafetyError("malformed prompt filter results", request_id=request_id)
        inspect(results)
    choices = response.get("choices")
    if not isinstance(choices, list) or not all(isinstance(choice, dict) for choice in choices):
        raise FilterSafetyError("malformed chat completion choices", request_id=request_id)
    for choice in choices:
        if choice.get("finish_reason") == "content_filter":
            raise FilterSafetyError("chat completion filtered", request_id=request_id)
        if "content_filter_results" in choice:
            results = choice["content_filter_results"]
            if not isinstance(results, dict):
                raise FilterSafetyError(
                    "malformed completion filter results", request_id=request_id
                )
            inspect(results)
