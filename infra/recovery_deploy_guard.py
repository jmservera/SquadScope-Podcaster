"""Fail closed on recovery-state drift before/after a serialized production deployment."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

RECOVERY_URI = "PODCASTER_VIDEO_RECOVERY_OPERATOR_SECRET_URI"
RECOVERY_CLIENT = "PODCASTER_VIDEO_RECOVERY_IDENTITY_CLIENT_ID"
LEGACY_KEY = "PODCASTER_VIDEO_RECOVERY_OPERATOR_KEY"
_DIGEST = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


def _az(*args: str) -> object:
    try:
        result = subprocess.run(
            ["az", *args, "-o", "json"], capture_output=True, text=True, check=True, timeout=30
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("Azure metadata preflight unavailable") from exc


def _api_name(resource_group: str) -> str | None:
    apps = _az("containerapp", "list", "-g", resource_group)
    names = [app["name"] for app in apps if app.get("name", "").endswith("-api")]
    if not names:
        return None
    if len(names) != 1:
        raise ValueError("Ambiguous API container apps")
    return names[0]


def _recovery_resources(resource_group: str, api_name: str) -> tuple[str, str, str]:
    identity = _az("identity", "show", "-g", resource_group, "-n", f"{api_name}-recovery-id")
    vaults = [
        vault
        for vault in _az("keyvault", "list", "-g", resource_group)
        if re.fullmatch(r"podrv[a-z0-9]{13}", vault.get("name", ""))
    ]
    if len(vaults) != 1:
        raise ValueError("Dedicated recovery vault metadata is unavailable or ambiguous")
    vault = vaults[0]
    if vault.get("properties", {}).get("publicNetworkAccess") != "Disabled":
        raise ValueError("Recovery vault must have public network access disabled")
    secret_scope = f"{vault['id']}/secrets/video-recovery-operator-key"
    roles = _az(
        "role",
        "assignment",
        "list",
        "--assignee-object-id",
        identity["principalId"],
        "--scope",
        secret_scope,
        "--all",
    )
    if not any(
        role.get("scope", "").lower() == secret_scope.lower()
        and role.get("roleDefinitionId", "")
        .lower()
        .endswith("/4633458b-17de-408a-b874-0445c86b69e6")
        and role.get("principalId", "").lower() == identity["principalId"].lower()
        for role in roles
    ):
        raise ValueError("Exact-secret recovery role assignment is unavailable")
    uri = f"https://{vault['name']}.vault.azure.net/secrets/video-recovery-operator-key"
    return identity["id"], identity["clientId"], uri


def live_state(resource_group: str) -> str:
    name = _api_name(resource_group)
    if name is None:
        return "absent"
    app = _az("containerapp", "show", "-g", resource_group, "-n", name)
    identities = (app.get("identity") or {}).get("userAssignedIdentities") or {}
    recovery_ids = [
        key for key in identities if key.lower().endswith(f"/{name}-recovery-id".lower())
    ]
    env = (
        (app.get("properties") or {}).get("template", {}).get("containers", [{}])[0].get("env", [])
    )
    recovery_env = [item for item in env if item.get("name") in {RECOVERY_URI, RECOVERY_CLIENT}]
    if any(item.get("name") == LEGACY_KEY for item in env):
        raise ValueError("Legacy recovery key is present in the API environment")
    if (app.get("properties") or {}).get("configuration", {}).get("secrets"):
        if any(
            "recovery" in item.get("name", "").lower()
            for item in app["properties"]["configuration"]["secrets"]
        ):
            raise ValueError("Unexpected recovery secretRef in API configuration")
    if not recovery_ids and not recovery_env:
        return "inactive"
    if (
        len(recovery_ids) != 1
        or len(recovery_env) != 2
        or {item["name"] for item in recovery_env} != {RECOVERY_URI, RECOVERY_CLIENT}
        or any(not item.get("value") or item.get("secretRef") for item in recovery_env)
    ):
        raise ValueError("Partial recovery identity/environment state")
    identity_id, client_id, uri = _recovery_resources(resource_group, name)
    values = {item["name"]: item["value"] for item in recovery_env}
    if (
        recovery_ids[0].lower() != identity_id.lower()
        or values[RECOVERY_CLIENT].lower() != client_id.lower()
        or values[RECOVERY_URI] != uri
    ):
        raise ValueError("Recovery API does not match the dedicated private vault/identity")
    return "active"


def check(
    resource_group: str,
    expected: str,
    desired: str,
    transition_approved: str,
    images: tuple[str, str, str],
    deploy_vnet: str,
) -> None:
    if expected not in {"inactive", "active"} or desired not in {"inactive", "active"}:
        raise ValueError("Protected recovery state and expected-live state must be explicit")
    live = live_state(resource_group)
    if live != expected and not (live == "absent" and expected == "inactive"):
        raise ValueError("Recovery API live state differs from protected expected-live state")
    if desired != expected:
        if transition_approved != "true" or not all(_DIGEST.fullmatch(image) for image in images):
            raise ValueError("Recovery transition requires separate approval and pinned images")
        if live == "absent":
            raise ValueError("Recovery activation requires an existing API")
    if desired == "active":
        name = _api_name(resource_group)
        if name is None:
            raise ValueError("Recovery activation requires an existing API")
        _recovery_resources(resource_group, name)
    if deploy_vnet == "false":
        vnets = _az("network", "vnet", "list", "-g", resource_group)
        if vnets:
            raise ValueError("Refusing full-stack deployment without the existing VNet")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--desired", required=True)
    parser.add_argument("--transition-approved", required=True)
    parser.add_argument("--synthesis-image", required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--ui-image", required=True)
    parser.add_argument("--deploy-vnet", required=True)
    args = parser.parse_args()
    try:
        check(
            args.resource_group,
            args.expected,
            args.desired,
            args.transition_approved,
            (args.synthesis_image, args.api_image, args.ui_image),
            args.deploy_vnet,
        )
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print("Recovery desired-state and network preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
