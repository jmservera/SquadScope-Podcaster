"""Fail-closed recovery Key Vault authorization and production metadata guards."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx2 as httpx
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


def test_scoped_reconciler_does_not_depend_on_vault_key(configured):
    with (
        patch.object(
            recovery.recovery_operator_key, "get", side_effect=recovery.RecoveryKeyUnavailable
        ),
        patch("podcaster.monitoring.get_storage") as storage,
    ):
        with TestClient(app):
            time.sleep(0.05)
        assert storage.call_count >= 1
        recovery.recovery_operator_key.get.assert_not_called()


@pytest.mark.parametrize("blocked_stage", ["queue", "storage", "reconcile"])
def test_scoped_reconciler_initialization_keeps_startup_and_health_responsive(
    monkeypatch, blocked_stage
):
    from podcaster import monitoring, queue
    from podcaster.video import recovery as video_recovery

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    backend = Mock()
    storage = Mock()
    threads = {}

    def operation(stage, result):
        threads[stage] = threading.get_ident()
        if stage == blocked_stage:
            started.set()
            try:
                assert release.wait(3), "test must release the blocked Azure operation"
            finally:
                finished.set()
        return result

    monkeypatch.setattr(queue, "create_video_queue_backend", lambda: operation("queue", backend))
    monkeypatch.setattr(monitoring, "get_storage", lambda: operation("storage", storage))
    monkeypatch.setattr(
        video_recovery, "reconcile_pending", lambda *_args, **_kwargs: operation("reconcile", None)
    )

    async def exercise():
        event_loop_thread = threading.get_ident()
        try:
            async with monitoring._private_recovery_lifespan(app):
                assert await asyncio.to_thread(started.wait, 2)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await asyncio.wait_for(client.get("/healthz"), timeout=1)
                assert response.status_code == 200
                assert response.json() == {"status": "healthy"}
                assert not finished.is_set()
                assert all(thread != event_loop_thread for thread in threads.values())
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)

    asyncio.run(exercise())


@pytest.mark.parametrize("failed_stage", ["queue", "storage", "reconcile"])
def test_scoped_reconciler_retries_initialization_and_reconciliation(monkeypatch, failed_stage):
    from podcaster import monitoring, queue
    from podcaster.video import recovery as video_recovery

    backend = Mock()
    storage = Mock()
    rounds = []
    completed = threading.Event()
    sleep_calls = []
    original_sleep = asyncio.sleep
    factories = {}
    for stage, result in (("queue", backend), ("storage", storage)):
        factories[stage] = Mock(
            side_effect=[RuntimeError("storage unavailable"), result]
            if stage == failed_stage
            else [result, result]
        )
    monkeypatch.setattr(queue, "create_video_queue_backend", factories["queue"])
    monkeypatch.setattr(monitoring, "get_storage", factories["storage"])

    def reconcile(actual_storage, send, *, scoped_only):
        assert actual_storage is storage
        assert scoped_only is True
        rounds.append(send)
        if failed_stage == "queue" and len(rounds) == 1:
            with pytest.raises(RuntimeError, match="video queue backend unavailable"):
                send("same pending epoch")
        elif failed_stage == "reconcile" and len(rounds) == 1:
            raise RuntimeError("storage unavailable")
        else:
            send("same pending epoch")
            completed.set()

    async def retry_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) == 1:
            await original_sleep(0)
        else:
            await asyncio.Event().wait()

    monkeypatch.setattr(video_recovery, "reconcile_pending", reconcile)
    monkeypatch.setattr(monitoring.asyncio, "sleep", retry_sleep)

    async def exercise():
        async with monitoring._private_recovery_lifespan(app):
            assert await asyncio.to_thread(completed.wait, 2)

    asyncio.run(exercise())
    assert factories["queue"].call_count == 2
    assert factories["storage"].call_count == 2
    assert sleep_calls == [60, 60]
    backend.send_message.assert_called_once_with("same pending epoch")


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
    assert role["properties"]["principalId"] == "[parameters('recoveryPrincipalId')]"
    assert "4633458b-17de-408a-b874-0445c86b69e6" in str(rbac["variables"])
