"""The release must stop on protected drift, including between partial updates."""

import copy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "code_only_release", Path(__file__).parents[1] / "infra" / "code_only_release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def snapshot():
    result = {}
    for target in release.TARGETS:
        container = {
            "name": target,
            "image": (
                f"{release.REGISTRY}/podcaster-api@{release.OLD_API}"
                if target == "api"
                else f"{release.REGISTRY}/podcaster-synthesis@{release.OLD_WORKER}"
            ),
            "env": [
                {"name": name, "value": value}
                for name, value in release.HOLDS.get(target, {}).items()
            ]
            + [{"name": "KEY", "secretRef": "existing-key", "value": None}],
            "resources": {"cpu": 0.5, "memory": "1Gi"},
            "command": ["python"],
            "args": ["-m", "existing_worker"],
        }
        result[target] = {
            "location": "eastus2",
            "identity": {"type": "UserAssigned", "userAssignedIdentities": {"existing": {}}},
            "properties": {
                "provisioningState": "Succeeded",
                "configuration": {
                    "triggerType": "Schedule" if target == "dsched" else "Event",
                    "registries": [{"server": release.REGISTRY, "identity": "existing"}],
                    "secrets": [{"name": "existing-key"}],
                },
                "template": {"containers": [container], "revisionSuffix": ""},
                "environmentId": "existing-vnet-environment",
            },
        }
    return result


def test_patch_preserves_full_container_and_does_not_replay_configuration():
    before = snapshot()["api"]
    patch = release.patch_body(before, "new-image")
    expected = copy.deepcopy(before["properties"]["template"]["containers"])
    expected[0]["image"] = "new-image"
    assert patch == {"properties": {"template": {"containers": expected}}}
    assert before["properties"]["template"]["containers"][0]["image"] != "new-image"
    assert patch["properties"]["template"]["containers"][0]["env"][-1] == {
        "name": "KEY",
        "secretRef": "existing-key",
        "value": None,
    }


@pytest.mark.parametrize("field", ["identity", "environment", "flag", "secret", "image"])
def test_drift_is_not_hidden(field):
    before = snapshot()
    after = copy.deepcopy(before)
    resource = after["video"]
    if field == "identity":
        resource["identity"]["type"] = "SystemAssigned"
    elif field == "environment":
        resource["properties"]["environmentId"] = "other"
    elif field == "flag":
        resource["properties"]["template"]["containers"][0]["env"][0]["value"] = "true"
    elif field == "secret":
        resource["properties"]["configuration"]["secrets"].append({"name": "other"})
    else:
        resource["properties"]["template"]["containers"][0]["image"] = "other"
    with pytest.raises(RuntimeError, match="Concurrent drift"):
        release.require_unchanged(before, after)


def test_only_readonly_revision_metadata_and_unset_suffix_are_normalized():
    before = snapshot()
    after = copy.deepcopy(before)
    after["api"]["properties"]["latestReadyRevisionName"] = "new-server-revision"
    after["api"]["properties"]["template"].pop("revisionSuffix")
    release.require_unchanged(before, after)
    after["api"]["properties"]["template"]["revisionSuffix"] = "explicit-other"
    with pytest.raises(RuntimeError):
        release.require_unchanged(before, after)


def test_hold_and_trigger_failure_rejects():
    before = snapshot()
    before["dist"]["properties"]["template"]["containers"][0]["env"][0]["value"] = "true"
    with pytest.raises(RuntimeError, match="Held configuration"):
        release.check_holds(before)
    before = snapshot()
    before["video"]["properties"]["configuration"]["triggerType"] = "Manual"
    with pytest.raises(RuntimeError, match="Unexpected trigger"):
        release.check_holds(before)


def test_drift_after_first_update_stops_without_second_write(monkeypatch):
    class FakeArm:
        def __init__(self):
            self.state = snapshot()
            self.writes = []

        def snapshots(self):
            return copy.deepcopy(self.state)

        def request(self, method, url, body=None):
            target = next(item for item in release.TARGETS if release.PREFIX + item + "?" in url)
            if method == "PATCH":
                self.writes.append(target)
                self.state[target]["properties"]["template"]["containers"] = body["properties"][
                    "template"
                ]["containers"]
                self.state["video"]["properties"]["environmentId"] = "competing-update"
            return copy.deepcopy(self.state[target])

    arm = FakeArm()
    monkeypatch.setattr(release, "require_quiet_jobs", lambda: None)
    with pytest.raises(RuntimeError, match="Concurrent drift"):
        release.release(arm, "api-new", "worker-new", apply=True, sleep=lambda _: None)
    assert arm.writes == ["synth"]


def test_success_updates_workers_before_api_and_keeps_holds(monkeypatch):
    class FakeArm:
        def __init__(self):
            self.state = snapshot()
            self.writes = []

        def snapshots(self):
            return copy.deepcopy(self.state)

        def request(self, method, url, body=None):
            target = next(item for item in release.TARGETS if release.PREFIX + item + "?" in url)
            if method == "PATCH":
                self.writes.append(target)
                self.state[target]["properties"]["template"]["containers"] = body["properties"][
                    "template"
                ]["containers"]
            return copy.deepcopy(self.state[target])

    arm = FakeArm()
    monkeypatch.setattr(release, "require_quiet_jobs", lambda: None)
    arm.state["api"]["properties"]["latestReadyRevisionName"] = "healthy-new"
    arm.state["api"]["properties"]["latestRevisionName"] = "healthy-new"
    monkeypatch.setattr(release, "require_api_health", lambda _: "healthy-new")
    final = release.release(arm, "api-new", "worker-new", apply=True, sleep=lambda _: None)
    assert arm.writes == ["synth", "video", "recorder", "dist", "dsched", "api"]
    release.check_holds(final)


def test_old_healthy_api_revision_cannot_satisfy_new_image(monkeypatch):
    revisions = [
        {
            "name": "old",
            "properties": {
                "active": True,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "trafficWeight": 100,
                "template": {"containers": [{"image": "old-image"}]},
            },
        }
    ]
    monkeypatch.setattr(release, "cli_json", lambda _: revisions)
    monkeypatch.setattr(release.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="New API image"):
        release.require_api_health("new-image")
    revisions[0]["properties"]["template"]["containers"][0]["image"] = "new-image"
    assert release.require_api_health("new-image") == "old"
