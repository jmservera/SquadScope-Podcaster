"""Fail closed on reported chat-completion filtering failures."""

from __future__ import annotations

import re
from typing import Any
from urllib.error import HTTPError

_KNOWN_CATEGORIES = frozenset(
    {
        "hate",
        "sexual",
        "violence",
        "self_harm",
        "profanity",
        "jailbreak",
        "indirect_attack",
        "custom_blocklists",
        "protected_material_text",
        "protected_material_code",
        "ungrounded_material",
    }
)
_SEVERITY_CATEGORIES = frozenset({"hate", "sexual", "violence", "self_harm"})
_CITATION_FIELDS = frozenset({"URL", "License", "license"})
_ANNOTATION_FIELDS = frozenset({"filtered", "detected", "severity", "citation", "details", "id"})
_BLOCKLIST_ENTRY_FIELDS = frozenset({"filtered", "id", "details"})
_OFFSET_FIELDS = frozenset({"check_offset", "start_offset", "end_offset"})
_DETAIL_FIELDS = frozenset(
    {
        "filtered",
        "detected",
        "id",
        "blocklist_id",
        "completion_end_offset",
        "completion_start_offset",
        "length",
        "offset",
        "text",
    }
)


class FilterSafetyError(ValueError):
    """A provider filtering failure must not produce a publishable artifact."""

    def __init__(
        self, message: str, *, request_id: str | None = None, path: str | None = None
    ) -> None:
        super().__init__(message)
        self.request_id = _safe_id(request_id)
        self.path = path

    @property
    def reason(self) -> str:
        """Content-free rejection reason: fixed message plus a sanitized key path."""
        return f"{self.args[0]} at {self.path}" if self.path else str(self.args[0])


def _safe_id(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        return value
    return None


def _safe_segment(segment: Any) -> str:
    if isinstance(segment, int) and not isinstance(segment, bool):
        return str(segment)
    if isinstance(segment, str) and re.fullmatch(r"[A-Za-z0-9_]{1,40}", segment):
        return segment
    return "?"


def _format_path(path: tuple[Any, ...]) -> str:
    return ".".join(_safe_segment(segment) for segment in path)


def raise_on_blocked_http(error: HTTPError) -> None:
    if error.code == 400:
        headers = error.headers
        request_id = headers.get("x-ms-request-id") if headers is not None else None
        raise FilterSafetyError(
            "chat completion rejected by provider (HTTP 400)", request_id=request_id
        ) from None


def _is_error_key(key: Any) -> bool:
    return isinstance(key, str) and (
        key.lower() in ("error", "innererror", "status", "code", "error_details")
        or key.lower().endswith("_error")
    )


def validate_chat_filter_response(response: Any) -> None:
    """Inspect reported annotations without requiring optional annotations to exist.

    Unknown fields and categories are tolerated only while they carry no decision:
    any error key or any ``filtered``/``detected`` value other than ``false`` is rejected.
    """
    if not isinstance(response, dict):
        raise FilterSafetyError("malformed chat completion")
    request_id = _safe_id(response.get("id"))

    def reject(message: str, path: tuple[Any, ...] = ()) -> None:
        raise FilterSafetyError(message, request_id=request_id, path=_format_path(path) or None)

    def scan_unknown(value: Any, path: tuple[Any, ...]) -> None:
        if isinstance(value, dict):
            for key, field in value.items():
                if _is_error_key(key):
                    reject("chat completion reported a filter error", (*path, key))
                if key in ("filtered", "detected") and (not isinstance(field, bool) or field):
                    reject("chat completion reported filtering", (*path, key))
                scan_unknown(field, (*path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                scan_unknown(item, (*path, index))

    def results(value: Any, path: tuple[Any, ...]) -> None:
        if not isinstance(value, dict):
            reject("malformed completion filter results", path)
        for category, annotation in value.items():
            where = (*path, category)
            if not isinstance(category, str) or not category:
                reject("malformed filter category", where)
            if _is_error_key(category):
                reject("chat completion reported a filter error", where)
            if category not in _KNOWN_CATEGORIES:
                scan_unknown({category: annotation}, path)
                continue
            if category == "custom_blocklists":
                as_list = isinstance(annotation, list)
                entries = annotation if as_list else [annotation]
                if not entries:
                    reject("malformed filter category", where)
                for index, entry in enumerate(entries):
                    blocklist_entry(entry, (*where, index) if as_list else where)
                continue
            if category in _SEVERITY_CATEGORIES and (
                not isinstance(annotation, dict) or not isinstance(annotation.get("filtered"), bool)
            ):
                reject("missing filter decision", where)
            annotation_fields(annotation, _ANNOTATION_FIELDS, where, require_decision=True)

    def blocklist_entry(entry: Any, path: tuple[Any, ...]) -> None:
        if not isinstance(entry, dict) or not isinstance(entry.get("filtered"), bool):
            reject("missing filter decision", path)
        annotation_fields(entry, _BLOCKLIST_ENTRY_FIELDS, path, require_decision=True)
        details = entry.get("details", [])
        for index, detail in enumerate(details if isinstance(details, list) else [details]):
            if not isinstance(detail, dict) or not isinstance(detail.get("filtered"), bool):
                reject("missing filter decision", (*path, "details", index))

    def offsets(value: Any, path: tuple[Any, ...]) -> None:
        if not isinstance(value, dict) or not value:
            reject("malformed filter offsets", path)
        for key, field in value.items():
            if key in _OFFSET_FIELDS:
                if type(field) is not int or field < 0:
                    reject("malformed filter offsets", (*path, key))
            else:
                scan_unknown({key: field}, path)

    def annotation_fields(
        annotation: Any,
        allowed: frozenset[str],
        path: tuple[Any, ...],
        *,
        require_decision: bool,
    ) -> None:
        if not isinstance(annotation, dict) or not annotation:
            reject("malformed filter category", path)
        if require_decision and not any(key in annotation for key in ("filtered", "detected")):
            reject("missing filter decision", path)
        for key, field in annotation.items():
            where = (*path, key)
            if _is_error_key(key):
                reject("chat completion reported a filter error", where)
            if key not in allowed:
                scan_unknown({key: field}, path)
                continue
            if key in ("filtered", "detected"):
                if not isinstance(field, bool) or field:
                    reject("chat completion reported filtering", where)
            elif key == "severity":
                if field not in ("safe", "low", "medium", "high"):
                    reject("malformed filter severity", where)
            elif key == "id":
                if not isinstance(field, str):
                    reject("malformed filter detail", where)
            elif key == "citation":
                if not isinstance(field, dict):
                    reject("malformed filter detail", where)
                for k, v in field.items():
                    if k in _CITATION_FIELDS:
                        if not isinstance(v, str):
                            reject("malformed filter detail", (*where, k))
                    else:
                        scan_unknown({k: v}, where)
            elif key == "details":
                if isinstance(field, dict):
                    entries = [field]
                elif isinstance(field, list):
                    entries = field
                else:
                    reject("malformed filter detail", where)
                for index, entry in enumerate(entries):
                    annotation_fields(
                        entry, _DETAIL_FIELDS, (*where, index), require_decision=False
                    )
            elif not isinstance(field, (str, int)) or isinstance(field, bool):
                reject("malformed filter detail", where)

    for key in response:
        if _is_error_key(key):
            reject("chat completion reported an error", (key,))

    for prompt_key in ("prompt_filter_results", "prompt_annotations"):
        if prompt_key not in response:
            continue
        prompts = response[prompt_key]
        if not isinstance(prompts, list) or not prompts:
            reject("malformed prompt filter results", (prompt_key,))
        for index, prompt in enumerate(prompts):
            where = (prompt_key, index)
            if not isinstance(prompt, dict) or "content_filter_results" not in prompt:
                reject("malformed prompt filter results", where)
            for key, field in prompt.items():
                if key not in ("prompt_index", "content_filter_results"):
                    scan_unknown({key: field}, where)
            if "prompt_index" in prompt and (
                type(prompt["prompt_index"]) is not int or prompt["prompt_index"] < 0
            ):
                reject("malformed prompt filter index", (*where, "prompt_index"))
            results(prompt["content_filter_results"], (*where, "content_filter_results"))
    if "content_filter_results" in response:
        results(response["content_filter_results"], ("content_filter_results",))
    choices = response.get("choices")
    if (
        not isinstance(choices, list)
        or not choices
        or not all(isinstance(choice, dict) for choice in choices)
    ):
        reject("malformed chat completion choices", ("choices",))
    for index, choice in enumerate(choices):
        where = ("choices", index)
        if choice.get("finish_reason") in ("content_filter", "filtered", "filter_error"):
            reject("chat completion filtered", (*where, "finish_reason"))
        for key in choice:
            if _is_error_key(key):
                reject("chat completion reported a filter error", (*where, key))
        if "content_filter_results" in choice:
            results(choice["content_filter_results"], (*where, "content_filter_results"))
        if "content_filter_offsets" in choice:
            offsets(choice["content_filter_offsets"], (*where, "content_filter_offsets"))
