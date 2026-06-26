from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from podcaster.storage import StorageBackend

_CREDENTIALS_BLOB_PATH = "config/credentials.json"
_CREDENTIAL_TYPES = frozenset({"spotify", "youtube", "api_key"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_set(values: dict[str, Any]) -> bool:
    return any(
        value is not None and (not isinstance(value, str) or value.strip() != "")
        for value in values.values()
    )


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    values = record.get("values")
    return {
        "id": record["id"],
        "type": record["type"],
        "label": record["label"],
        "is_set": _is_set(values if isinstance(values, dict) else {}),
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }


def _require_secret() -> str:
    secret = os.environ.get("UI_AUTH_SECRET", "").strip()
    if not secret:
        raise RuntimeError("UI_AUTH_SECRET is required for credential encryption")
    return secret


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _load_records(current: bytes | None, secret: str) -> list[dict[str, Any]]:
    if current is None:
        return []
    try:
        plaintext = _fernet(secret).decrypt(current)
        document = json.loads(plaintext.decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("stored credentials are unreadable") from exc
    if not isinstance(document, dict):
        raise RuntimeError("stored credentials document is invalid")
    records = document.get("credentials")
    if not isinstance(records, list):
        raise RuntimeError("stored credentials document is invalid")
    normalized: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("stored credentials document is invalid")
        normalized.append(record)
    return normalized


def _dump_records(records: list[dict[str, Any]], secret: str) -> bytes:
    document = json.dumps(
        {"credentials": records},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _fernet(secret).encrypt(document)


def validate_credential_payload(payload: dict[str, Any]) -> dict[str, Any]:
    credential_type = payload.get("type")
    if not isinstance(credential_type, str) or credential_type not in _CREDENTIAL_TYPES:
        raise ValueError("type must be one of: spotify, youtube, api_key")

    label = payload.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("label is required")

    values = payload.get("values")
    if not isinstance(values, dict):
        raise ValueError("values must be an object")

    return {
        "type": credential_type,
        "label": label.strip(),
        "values": values,
    }


class CredentialStore:
    def __init__(self, storage: StorageBackend, *, secret: str | None = None) -> None:
        self._storage = storage
        self._secret = secret if secret is not None else _require_secret()

    def list_credentials(self) -> dict[str, list[dict[str, Any]]]:
        records = _load_records(self._storage.get_bytes(_CREDENTIALS_BLOB_PATH), self._secret)
        return {"credentials": [_summary(record) for record in records]}

    def create_credential(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = validate_credential_payload(payload)
        summary: dict[str, Any] | None = None

        def _update(current: bytes | None) -> bytes:
            nonlocal summary
            records = _load_records(current, self._secret)
            now = _utc_now()
            record = {
                "id": uuid.uuid4().hex,
                "type": data["type"],
                "label": data["label"],
                "values": data["values"],
                "created_at": now,
                "updated_at": now,
            }
            records.append(record)
            summary = _summary(record)
            return _dump_records(records, self._secret)

        self._storage.update_bytes(
            _CREDENTIALS_BLOB_PATH,
            "application/octet-stream",
            _update,
        )
        assert summary is not None
        return summary

    def update_credential(
        self,
        credential_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        data = validate_credential_payload(payload)
        summary: dict[str, Any] | None = None

        def _update(current: bytes | None) -> bytes:
            nonlocal summary
            records = _load_records(current, self._secret)
            for record in records:
                if record.get("id") != credential_id:
                    continue
                record["type"] = data["type"]
                record["label"] = data["label"]
                record["values"] = data["values"]
                record["updated_at"] = _utc_now()
                summary = _summary(record)
                break
            return _dump_records(records, self._secret)

        self._storage.update_bytes(
            _CREDENTIALS_BLOB_PATH,
            "application/octet-stream",
            _update,
        )
        return summary

    def delete_credential(self, credential_id: str) -> bool:
        deleted = False

        def _update(current: bytes | None) -> bytes:
            nonlocal deleted
            records = _load_records(current, self._secret)
            remaining = [record for record in records if record.get("id") != credential_id]
            deleted = len(remaining) != len(records)
            return _dump_records(remaining, self._secret)

        self._storage.update_bytes(
            _CREDENTIALS_BLOB_PATH,
            "application/octet-stream",
            _update,
        )
        return deleted
