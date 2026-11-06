"""The release must stop on protected drift, including between partial updates."""

import copy
import importlib.util
import io
import json
import urllib.error
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
    clock = [0]
    monkeypatch.setattr(release.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        release.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
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
    with pytest.raises(RuntimeError, match="New API image"):
        release.require_api_health("new-image")
    revisions[0]["properties"]["template"]["containers"][0]["image"] = "new-image"
    assert release.require_api_health("new-image") == "old"


OPERATION = (
    f"https://management.azure.com/subscriptions/{release.SUBSCRIPTION}/"
    "providers/Microsoft.App/locations/eastus2/operations/test?api-version=2025-01-01"
)


def scripted_arm(monkeypatch, responses):
    clock = [0]
    monkeypatch.setattr(release.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        release.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.method, request.full_url, request.data))
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            code, headers, payload = item
            response = io.BytesIO(
                payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            )
            response.status = code
            response.headers = headers
            return response

    arm = release.Arm.__new__(release.Arm)
    arm.token = "test-only"
    arm.opener = Opener()
    return arm, calls, clock


def test_empty_202_waits_for_async_success_then_fresh_expected_image(monkeypatch):
    before = snapshot()["video"]
    after = copy.deepcopy(before)
    after["properties"]["template"]["containers"][0]["image"] = "worker-new"
    arm, calls, clock = scripted_arm(
        monkeypatch,
        [
            (202, {"Azure-AsyncOperation": OPERATION, "Retry-After": "2"}, b""),
            (200, {"Retry-After": "3"}, {"status": "Running"}),
            (200, {}, {"status": "Succeeded"}),
            (200, {}, before),
            (200, {}, after),
        ],
    )
    assert (
        arm.request(
            "PATCH", release.resource_url("video"), release.patch_body(before, "worker-new")
        )
        == {}
    )
    assert [method for method, _, _ in calls] == ["PATCH", "GET", "GET"]
    assert clock[0] == 5
    result = release.wait_ready(arm, "video", "worker-new")
    assert release.images(result) == ["worker-new"]
    assert len(calls) == 5


@pytest.mark.parametrize("terminal", ["Failed", "Canceled"])
def test_async_terminal_failure_stops_before_resource_readback(monkeypatch, terminal):
    arm, calls, _ = scripted_arm(
        monkeypatch,
        [
            (202, {"Azure-AsyncOperation": OPERATION}, b""),
            (200, {}, {"status": terminal, "error": {"message": "withheld-test-detail"}}),
        ],
    )
    with pytest.raises(RuntimeError, match="Terminal ARM operation failure") as error:
        arm.request("PATCH", release.resource_url("video"), {})
    assert "withheld-test-detail" not in str(error.value)
    assert [method for method, _, _ in calls] == ["PATCH", "GET"]


@pytest.mark.parametrize("failure", [408, 429, 500, 502, 503, 504, "transport", "timeout"])
def test_transient_poll_failure_retries_only_get(monkeypatch, failure):
    if failure == "transport":
        transient = urllib.error.URLError("withheld-test-detail")
    elif failure == "timeout":
        transient = TimeoutError("withheld-test-detail")
    else:
        transient = urllib.error.HTTPError(OPERATION, failure, "withheld-test-detail", {}, None)
    arm, calls, _ = scripted_arm(
        monkeypatch,
        [
            (202, {"Azure-AsyncOperation": OPERATION}, b""),
            transient,
            (200, {}, {"status": "Succeeded"}),
        ],
    )
    assert arm.request("PATCH", release.resource_url("video"), {}) == {}
    assert [method for method, _, _ in calls] == ["PATCH", "GET", "GET"]


def test_async_timeout_does_not_certify_accepted_mutation(monkeypatch):
    arm, calls, clock = scripted_arm(
        monkeypatch,
        [(202, {"Azure-AsyncOperation": OPERATION, "Retry-After": "60"}, b"")]
        + [(200, {"Retry-After": "60"}, {"status": "Running"})] * 10,
    )
    with pytest.raises(RuntimeError, match="polling timeout"):
        arm.request("PATCH", release.resource_url("video"), {})
    assert clock[0] == 600
    assert sum(method == "PATCH" for method, _, _ in calls) == 1


@pytest.mark.parametrize(
    "location,code,payload",
    [(False, 200, {"status": "Succeeded"}), (True, 200, {}), (True, 204, {})],
)
@pytest.mark.parametrize("elapsed", [600, 650])
def test_late_terminal_poll_response_is_not_certified(
    monkeypatch, location, code, payload, elapsed
):
    arm, calls, clock = scripted_arm(monkeypatch, [])

    def late_response(method, url):
        calls.append((method, url, None))
        clock[0] = elapsed
        return code, {}, payload

    monkeypatch.setattr(arm, "response", late_response)
    with pytest.raises(RuntimeError, match="polling timeout"):
        arm.poll_operation(OPERATION, {}, location=location)
    assert len(calls) == 1
    assert clock[0] == elapsed


@pytest.mark.parametrize("elapsed", [600, 658])
def test_late_expected_image_readiness_response_is_not_certified(monkeypatch, elapsed):
    arm, calls, clock = scripted_arm(monkeypatch, [])
    resource = snapshot()["video"]
    resource["properties"]["template"]["containers"][0]["image"] = "worker-new"

    def late_read(method, url):
        calls.append((method, url, None))
        clock[0] = elapsed
        return resource

    monkeypatch.setattr(arm, "request", late_read)
    with pytest.raises(RuntimeError, match="Deployment timeout"):
        release.wait_ready(arm, "video", "worker-new")
    assert len(calls) == 1


@pytest.mark.parametrize("elapsed", [600, 658])
def test_late_healthy_api_revision_response_is_not_certified(monkeypatch, elapsed):
    _, _, clock = scripted_arm(monkeypatch, [])
    calls = []

    def late_revisions(args):
        calls.append(args)
        clock[0] = elapsed
        return [
            {
                "name": "healthy-new",
                "properties": {
                    "active": True,
                    "healthState": "Healthy",
                    "provisioningState": "Provisioned",
                    "trafficWeight": 100,
                    "template": {"containers": [{"image": "api-new"}]},
                },
            }
        ]

    monkeypatch.setattr(release, "cli_json", late_revisions)
    with pytest.raises(RuntimeError, match="New API image"):
        release.require_api_health("api-new")
    assert len(calls) == 1


@pytest.mark.parametrize("code", [200, 204])
def test_location_fallback_waits_for_completion(monkeypatch, code):
    arm, calls, _ = scripted_arm(
        monkeypatch,
        [
            (202, {"Location": OPERATION}, b""),
            (202, {}, b""),
            (code, {}, b""),
        ],
    )
    assert arm.request("PATCH", release.resource_url("video"), {}) == {}
    assert [method for method, _, _ in calls] == ["PATCH", "GET", "GET"]


def test_async_header_takes_precedence_and_empty_status_fails_closed(monkeypatch):
    arm, calls, _ = scripted_arm(
        monkeypatch,
        [
            (
                202,
                {"Azure-AsyncOperation": OPERATION, "Location": "https://untrusted.invalid"},
                b"",
            ),
            (200, {}, b""),
        ],
    )
    with pytest.raises(RuntimeError, match="lacks terminal status"):
        arm.request("PATCH", release.resource_url("video"), {})
    assert calls[1][1] == OPERATION


def test_missing_headers_requires_fresh_image_and_readiness(monkeypatch):
    before = snapshot()["video"]
    after = copy.deepcopy(before)
    after["properties"]["template"]["containers"][0]["image"] = "worker-new"
    arm, calls, _ = scripted_arm(monkeypatch, [(202, {}, b""), (200, {}, before), (200, {}, after)])
    assert arm.request("PATCH", release.resource_url("video"), {}) == {}
    assert release.images(release.wait_ready(arm, "video", "worker-new")) == ["worker-new"]
    assert len(calls) == 3


def test_missing_headers_old_succeeded_image_times_out(monkeypatch):
    arm, calls, _ = scripted_arm(
        monkeypatch, [(202, {}, b"")] + [(200, {}, snapshot()["video"])] * 120
    )
    arm.request("PATCH", release.resource_url("video"), {})
    with pytest.raises(RuntimeError, match="Deployment timeout"):
        release.wait_ready(arm, "video", "worker-new")
    assert len(calls) == 121


@pytest.mark.parametrize(
    "url",
    [
        "https://untrusted.invalid/secret-query",
        OPERATION.replace("https:", "http:"),
        OPERATION.replace("management.azure.com", "management.azure.com.untrusted.invalid"),
        OPERATION.replace("management.azure.com", "user@management.azure.com"),
        OPERATION.replace("management.azure.com", "management.azure.com:444"),
        OPERATION.replace(release.SUBSCRIPTION, "other-subscription"),
        OPERATION + "#fragment",
    ],
)
def test_untrusted_poll_url_rejected_before_authenticated_get(monkeypatch, url):
    arm, calls, _ = scripted_arm(monkeypatch, [(202, {"Azure-AsyncOperation": url}, b"")])
    with pytest.raises(RuntimeError, match="Untrusted ARM") as error:
        arm.request("PATCH", release.resource_url("video"), {})
    assert url not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("code", [302, 401, 403])
def test_poll_redirect_or_permission_failure_not_retried_or_leaked(monkeypatch, code):
    arm, calls, _ = scripted_arm(
        monkeypatch,
        [
            (202, {"Azure-AsyncOperation": OPERATION}, b""),
            urllib.error.HTTPError(OPERATION, code, "withheld-test-detail", {}, None),
        ],
    )
    with pytest.raises(RuntimeError, match=f"HTTP {code}") as error:
        arm.request("PATCH", release.resource_url("video"), {})
    assert OPERATION not in str(error.value)
    assert "withheld-test-detail" not in str(error.value)
    assert len(calls) == 2
    assert (
        release.NoRedirect().redirect_request(None, None, 302, "", {}, "https://untrusted.invalid")
        is None
    )
