from __future__ import annotations

import json
from typing import Any

from podcaster.storage import StorageBackend

_PODCAST_CONFIG_BLOB_PATH = "config/podcast-config.json"


def default_podcast_config() -> dict[str, Any]:
    return {
        "name": "",
        "intro_music_url": None,
        "outro_music_url": None,
        "publish_targets": [],
        "auto_publish": False,
        "schedule": None,
    }


def validate_podcast_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")

    normalized = default_podcast_config()
    normalized["name"] = name.strip()

    for key in ("intro_music_url", "outro_music_url", "schedule"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{key} must be a string or null")
        normalized[key] = value.strip() if isinstance(value, str) else None

    publish_targets = payload.get("publish_targets", [])
    if not isinstance(publish_targets, list):
        raise ValueError("publish_targets must be an array")
    normalized_targets: list[dict[str, Any]] = []
    for index, target in enumerate(publish_targets):
        if not isinstance(target, dict):
            raise ValueError(f"publish_targets[{index}] must be an object")
        target_type = target.get("type")
        if not isinstance(target_type, str) or not target_type.strip():
            raise ValueError(f"publish_targets[{index}].type is required")
        config = target.get("config")
        if not isinstance(config, dict):
            raise ValueError(f"publish_targets[{index}].config must be an object")
        normalized_targets.append({"type": target_type.strip(), "config": config})
    normalized["publish_targets"] = normalized_targets

    auto_publish = payload.get("auto_publish")
    if not isinstance(auto_publish, bool):
        raise ValueError("auto_publish must be a boolean")
    normalized["auto_publish"] = auto_publish
    return normalized


class PodcastConfigStore:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def get(self) -> dict[str, Any]:
        raw = self._storage.get_bytes(_PODCAST_CONFIG_BLOB_PATH)
        if raw is None:
            return default_podcast_config()
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("stored podcast config is unreadable") from exc
        if not isinstance(document, dict):
            raise RuntimeError("stored podcast config is invalid")
        return validate_podcast_config_payload(document)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        document = validate_podcast_config_payload(payload)
        self._storage.put_bytes(
            _PODCAST_CONFIG_BLOB_PATH,
            json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            "application/json; charset=utf-8",
        )
        return document
