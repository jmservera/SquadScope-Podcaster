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

    def reject(message: str) -> None:
        raise FilterSafetyError(message, request_id=request_id)

    def results(value: Any) -> None:
        if not isinstance(value, dict) or not value:
            reject("malformed completion filter results")
        for category, annotation in value.items():
            if not isinstance(category, str) or not category:
                reject("malformed filter category")
            if category.lower() in ("error", "innererror", "status", "code", "error_details") or (
                category.lower().endswith("_error")
            ):
                reject("chat completion reported a filter error")
            if not isinstance(annotation, dict) or not annotation:
                reject("malformed filter category")
            for key, field in annotation.items():
                if key == "filtered":
                    if not isinstance(field, bool) or field:
                        reject("chat completion reported filtering")
                elif key == "detected":
                    if not isinstance(field, bool) or field:
                        reject("chat completion reported filtering")
                elif key == "severity":
                    if field not in ("safe", "low", "medium", "high"):
                        reject("malformed filter severity")
                else:
                    reject("unknown filter category field")

    for key in response:
        if isinstance(key, str) and (
            key.lower() in ("error", "status", "code", "error_details")
            or key.lower().endswith("_error")
        ):
            reject("chat completion reported an error")

    if "prompt_filter_results" in response:
        prompts = response["prompt_filter_results"]
        if not isinstance(prompts, list) or not prompts:
            reject("malformed prompt filter results")
        for prompt in prompts:
            if not isinstance(prompt, dict) or "content_filter_results" not in prompt:
                reject("malformed prompt filter results")
            if set(prompt) - {"prompt_index", "content_filter_results"}:
                reject("unknown prompt filter field")
            if "prompt_index" in prompt and (
                type(prompt["prompt_index"]) is not int or prompt["prompt_index"] < 0
            ):
                reject("malformed prompt filter index")
            results(prompt["content_filter_results"])
    if "content_filter_results" in response:
        results(response["content_filter_results"])
    choices = response.get("choices")
    if not isinstance(choices, list) or not all(isinstance(choice, dict) for choice in choices):
        reject("malformed chat completion choices")
    for choice in choices:
        if choice.get("finish_reason") in ("content_filter", "filtered", "filter_error"):
            reject("chat completion filtered")
        for key in choice:
            if isinstance(key, str) and (
                key.lower() in ("error", "status", "code", "error_details")
                or key.lower().endswith("_error")
            ):
                reject("chat completion reported a filter error")
        if "content_filter_results" in choice:
            results(choice["content_filter_results"])
