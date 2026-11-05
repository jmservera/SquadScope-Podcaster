"""Fail-closed recovery Key Vault authorization and production metadata guards."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from infra import recovery_deploy_guard as guard
from podcaster import recovery_operator_key as recovery
from podcaster.monitoring import app

URI = "https://podrv123456789abcd.vault.azure.net/secrets/video-recovery-operator-key"
CLIENT_ID = "c089ed5d-96a2-411b-bf5d-2f0789586713"
OLD_SECRET = "unit-test-old-secret-not-a-real-credential"
NEW_SECRET = "unit-test-new-secret-not-a-real-credential"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("PODCASTER_VIDEO_RECOVERY_OPERATOR_SECRET_URI", URI)
    monkeypatch.setenv("PODCASTER_VIDEO_RECOVERY_IDENTITY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AZURE_CLIENT_ID", "4285c70a-b297-4a05-ac10-0386aeeb32a4")
    monkeypatch.delenv("PODCASTER_VIDEO_RECOVERY_OPERATOR_KEY", raising=False)


@pytest.fixture
def provider():
    instance = recovery.RecoveryOperatorKey()
    yield instance
    instance.close()


@pytest.mark.parametrize(
    "uri",
    [
        URI + "/version",
        URI + "?api-version=1",
        URI + "#fragment",
        URI.replace("podrv123456789abcd", "hyperforge"),
        URI.replace("https://", "http://"),
        URI.replace("vault.azure.net", "vault.azure.net@attacker.example"),
    ],
)
def test_invalid_uri_is_unavailable(configured, monkeypatch, provider, uri):
    monkeypatch.setenv("PODCASTER_VIDEO_RECOVERY_OPERATOR_SECRET_URI", uri)
    with pytest.raises(recovery.RecoveryKeyUnavailable):
        provider.get()


def test_partial_legacy_and_shared_identity_fail_closed(configured, monkeypatch, provider):
    monkeypatch.delenv("PODCASTER_VIDEO_RECOVERY_IDENTITY_CLIENT_ID")
    with pytest.raises(recovery.RecoveryKeyUnavailable):
        provider.get()
    monkeypatch.setenv("PODCASTER_VIDEO_RECOVERY_IDENTITY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PODCASTER_VIDEO_RECOVERY_OPERATOR_KEY", OLD_SECRET)
    assert provider.configured() is False
    with pytest.raises(recovery.RecoveryKeyUnavailable):
        provider.get()
    monkeypatch.delenv("PODCASTER_VIDEO_RECOVERY_OPERATOR_KEY")
    monkeypatch.setenv("AZURE_CLIENT_ID", CLIENT_ID)
    with pytest.raises(recovery.RecoveryKeyUnavailable):
        provider.get()


def test_dedicated_identity_latest_secret_and_attributes(configured):
    secret = SimpleNamespace(
        value=OLD_SECRET,
        properties=SimpleNamespace(enabled=True, not_before=None, expires_on=None),
    )
    with (
        patch.object(recovery, "RequestsTransport") as transport,
        patch.object(recovery, "ManagedIdentityCredential") as credential,
        patch.object(recovery, "SecretClient") as client,
    ):
        transport.return_value.__enter__.return_value = transport.return_value
        client.return_value.get_secret.return_value = secret
        value, deadline = recovery._fetch_secret(URI.split("/secrets/")[0], CLIENT_ID)
        assert value == OLD_SECRET and 0 < deadline - time.monotonic() <= 60
        assert credential.call_args.kwargs["client_id"] == CLIENT_ID
        assert client.call_args.kwargs["vault_url"] == URI.split("/secrets/")[0]
        client.return_value.get_secret.assert_called_once_with("video-recovery-operator-key")
        for attributes in (
            SimpleNamespace(enabled=False, not_before=None, expires_on=None),
            SimpleNamespace(
                enabled=True,
                not_before=datetime.now(timezone.utc) + timedelta(minutes=5),
                expires_on=None,
            ),
            SimpleNamespace(
                enabled=True,
                not_before=None,
                expires_on=datetime.now(timezone.utc) - timedelta(seconds=1),
            ),
        ):
            secret.properties = attributes
            with pytest.raises(recovery.RecoveryKeyUnavailable):
                recovery._fetch_secret(URI.split("/secrets/")[0], CLIENT_ID)
        secret.properties = SimpleNamespace(enabled=True, not_before=None, expires_on=None)
        secret.value = "non-ascii-☂"
        with pytest.raises(recovery.RecoveryKeyUnavailable):
            recovery._fetch_secret(URI.split("/secrets/")[0], CLIENT_ID)
        secret.value = ""
        with pytest.raises(recovery.RecoveryKeyUnavailable):
            recovery._fetch_secret(URI.split("/secrets/")[0], CLIENT_ID)


def test_single_flight_rotation_and_failure_clear(configured, provider, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    fetch = Mock()
    clock = [100.0]
    monkeypatch.setattr(recovery.time, "monotonic", lambda: clock[0])

    def first(_vault, _client):
        started.set()
        assert release.wait(2)
        return OLD_SECRET, clock[0] + 60

    calls = iter(
        (
            first,
            lambda *_: (NEW_SECRET, clock[0] + 60),
            lambda *_: (_ for _ in ()).throw(RuntimeError(OLD_SECRET)),
        )
    )
    fetch.side_effect = lambda *args: next(calls)(*args)
    with patch.object(recovery, "_fetch_secret", fetch):
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(provider.get) for _ in range(8)]
            assert started.wait(2)
            release.set()
            assert {future.result(timeout=3) for future in futures} == {OLD_SECRET}
        assert fetch.call_count == 1
        clock[0] += 61
        assert provider.get() == NEW_SECRET
        clock[0] += 61
        with pytest.raises(recovery.RecoveryKeyUnavailable) as failure:
            provider.get()
        assert OLD_SECRET not in str(failure.value)
        assert provider._value is None


def test_expiry_never_outlives_secret(configured, provider, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(recovery.time, "monotonic", lambda: clock[0])
    with patch.object(
        recovery, "_fetch_secret", side_effect=[(OLD_SECRET, 100.01), (NEW_SECRET, 160.02)]
    ):
        assert provider.get() == OLD_SECRET
        clock[0] += 0.02
        assert provider.get() == NEW_SECRET


def test_expired_result_rejected_before_authorization(configured, provider, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(recovery.time, "monotonic", lambda: clock[0])
    with patch.object(
        recovery, "_fetch_secret", side_effect=[(OLD_SECRET, 99.99), (NEW_SECRET, 160)]
    ):
        with pytest.raises(recovery.RecoveryKeyUnavailable):
            provider.get()
        assert provider.get() == NEW_SECRET


def test_timed_out_fetch_never_authorizes_late_old_value(configured, provider, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    monkeypatch.setattr(recovery, "_FETCH_TIMEOUT_SECONDS", 0.02)

    def late(_vault, _client):
        started.set()
        assert release.wait(2)
        return OLD_SECRET, time.monotonic() + 60

    results = iter((late, lambda *_: (NEW_SECRET, time.monotonic() + 60)))
    with patch.object(recovery, "_fetch_secret", side_effect=lambda *args: next(results)(*args)):
        with pytest.raises(recovery.RecoveryKeyUnavailable):
            provider.get()
        assert started.is_set()
        with pytest.raises(recovery.RecoveryKeyUnavailable):
            provider.get()
        release.set()
        for _ in range(100):
            if provider._pending is None:
                break
            time.sleep(0.01)
        assert provider._pending is None
        assert provider.get() == NEW_SECRET


def test_reconciler_fetch_failure_never_opens_storage(configured):
    with (
        patch.object(
            recovery.recovery_operator_key, "get", side_effect=recovery.RecoveryKeyUnavailable
        ),
        patch("podcaster.monitoring.get_storage") as storage,
    ):
        with TestClient(app):
            time.sleep(0.05)
        storage.assert_not_called()


def test_guard_rejects_drift_partial_and_unsafe_network(monkeypatch):
    inactive = {
        "identity": {"userAssignedIdentities": {"/id/podcaster-api-shared-id": {}}},
        "properties": {"template": {"containers": [{"env": []}]}, "configuration": {"secrets": []}},
    }

    def az(*args):
        if args[:2] == ("containerapp", "list"):
            return [{"name": "podcaster-api"}]
        if args[:2] == ("containerapp", "show"):
            return inactive
        if args[:2] == ("network", "vnet"):
            return [{"name": "existing-private-vnet"}]
        if args[:2] == ("identity", "show"):
            return {
                "id": "/id/podcaster-api-recovery-id",
                "clientId": CLIENT_ID,
                "principalId": "operator-principal",
            }
        if args[:2] == ("keyvault", "list"):
            return [
                {
                    "name": "podrv123456789abcd",
                    "id": "/id/podrv123456789abcd",
                    "properties": {"publicNetworkAccess": "Disabled"},
                }
            ]
        if args[:3] == ("role", "assignment", "list"):
            assert "--scope" in args
            assert "--all" not in args
            return [
                {
                    "scope": "/id/podrv123456789abcd/secrets/video-recovery-operator-key",
                    "roleDefinitionId": "/id/4633458b-17de-408a-b874-0445c86b69e6",
                    "principalId": "operator-principal",
                }
            ]
        raise AssertionError(args)

    monkeypatch.setattr(guard, "_az", az)
    assert guard.live_state("rg") == "inactive"
    with pytest.raises(ValueError, match="existing VNet"):
        guard.check("rg", "inactive", "inactive", "false", ("", "", ""), "false")
    with pytest.raises(ValueError, match="existing VNet"):
        guard.check("rg", "inactive", "inactive", "false", ("", "", ""), "FALSE")
    with pytest.raises(ValueError, match="Invalid deployVnet"):
        guard.check("rg", "inactive", "inactive", "false", ("", "", ""), "no")
    with pytest.raises(ValueError, match="differs"):
        guard.check("rg", "active", "active", "false", ("", "", ""), "true")
    with pytest.raises(ValueError, match="pinned"):
        guard.check("rg", "inactive", "active", "true", ("latest", "", ""), "true")
    inactive["identity"]["userAssignedIdentities"]["/id/podcaster-api-recovery-id"] = {}
    with pytest.raises(ValueError, match="Partial"):
        guard.live_state("rg")
    inactive["properties"]["template"]["containers"][0]["env"] = [
        {"name": guard.RECOVERY_URI, "value": URI},
        {"name": guard.RECOVERY_CLIENT, "value": CLIENT_ID},
    ]
    assert guard.live_state("rg") == "active"
    with pytest.raises(ValueError, match="pinned image digests"):
        guard.check("rg", "active", "active", "false", ("latest", "latest", "latest"), "true")
    inactive["properties"]["template"]["containers"][0]["env"][0]["value"] = URI + "/old-version"
    with pytest.raises(ValueError, match="dedicated private vault"):
        guard.live_state("rg")


@pytest.mark.skipif(shutil.which("az") is None, reason="Azure CLI Bicep compiler unavailable")
def test_compiled_bicep_has_conditional_api_only_boundary():
    def build(path):
        return json.loads(
            subprocess.run(
                ["az", "bicep", "build", "--file", path, "--stdout"],
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            ).stdout
        )

    api = build("infra/modules/api.bicep")
    app = next(item for item in api["resources"] if item["type"] == "Microsoft.App/containerApps")
    assert "union(" in app["identity"]["userAssignedIdentities"]
    assert "recoveryEnabled" in app["identity"]["userAssignedIdentities"]
    assert "recoveryEnvironment" in app["properties"]["template"]["containers"][0]["env"]
    assert "if(variables('recoveryEnabled')" in api["variables"]["recoveryEnvironment"]
    assert api["variables"]["recoveryEnvironment"].endswith("createArray())]")
    assert app["identity"]["userAssignedIdentities"].endswith("createObject()))]")
    assert "secretRef" not in api["variables"]["recoveryEnvironment"]
    main = build("infra/main.bicep")
    modules = [
        item for item in main["resources"] if item["type"] == "Microsoft.Resources/deployments"
    ]
    assert all("recovery-bootstrap" not in str(item) for item in modules)
    assert all("recovery-secret-rbac" not in str(item) for item in modules)
    bootstrap = build("infra/recovery-bootstrap.bicep")
    vault = next(
        item for item in bootstrap["resources"] if item["type"] == "Microsoft.KeyVault/vaults"
    )
    assert vault["properties"]["publicNetworkAccess"] == "Disabled"
    assert vault["properties"]["enableRbacAuthorization"] is True
    assert vault["properties"]["networkAcls"] == {"bypass": "None", "defaultAction": "Deny"}
    assert any(
        item["type"] == "Microsoft.Network/privateEndpoints/privateDnsZoneGroups"
        for item in bootstrap["resources"]
    )
    link = next(
        item
        for item in bootstrap["resources"]
        if item["type"] == "Microsoft.Network/privateDnsZones/virtualNetworkLinks"
    )
    endpoint = next(
        item
        for item in bootstrap["resources"]
        if item["type"] == "Microsoft.Network/privateEndpoints"
    )
    assert link["properties"]["registrationEnabled"] is False
    assert endpoint["properties"]["privateLinkServiceConnections"][0]["properties"]["groupIds"] == [
        "vault"
    ]
    assert all(
        item["type"] != "Microsoft.Authorization/roleAssignments" for item in bootstrap["resources"]
    )
    rbac = build("infra/recovery-secret-rbac.bicep")
    role = next(
        item
        for item in rbac["resources"]
        if item["type"] == "Microsoft.Authorization/roleAssignments"
    )
    assert "vaults/secrets" in role["scope"]
    assert "4633458b-17de-408a-b874-0445c86b69e6" in str(rbac["variables"])
