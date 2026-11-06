"""Bounded six-resource image release under the existing prod-deploy mutex.

This is JSON Merge Patch, not CAS. The owner accepts an assessed exclusive
window; pre/post comparisons detect drift but cannot eliminate its race window.
Full snapshots and credentials remain in process memory, never in evidence.
"""

import argparse
import copy
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

SUBSCRIPTION = "99d2c976-e2ca-4e54-ad12-0325e6a62b9e"
GROUP = "squadscope-podcaster"
PREFIX = "podcaster-yqabcnkm2junu-"
REGISTRY = "podcasteryqabcnkm2junu.azurecr.io"
SOURCE = "5a526f7165c3e18b825a1d5a98e6c5864882a933"
API_VERSION = "2025-01-01"
TARGETS = ("api", "synth", "video", "recorder", "dist", "dsched")
OLD_API = "sha256:a685db5ee3078e11ad6010bfdabfbbb2f6c52d190b102418ff44ce1c6391ca98"
OLD_WORKER = "sha256:a51299e9cfbf4df86b7af7cb35deab9cf33f54792c979f91fbcb615df2ac37cf"
HOLDS = {
    "api": {"GROUNDING_MODE": "construct", "SPOTIFY_PUBLISH_ENABLED": "false"},
    "synth": {
        "SPOTIFY_PUBLISH_ENABLED": "false",
        "SPOTIFY_VIDEO_PUBLISH_MODE": "draft",
        "SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH": "false",
    },
    "video": {
        "DISTRIBUTION_OUTBOX_ENABLED": "false",
        "SPOTIFY_VIDEO_PUBLISH_MODE": "draft",
        "SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH": "false",
    },
    "dist": {
        "DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED": "false",
        "SPOTIFY_PUBLISH_ENABLED": "false",
        "SPOTIFY_VIDEO_PUBLISH_MODE": "draft",
        "SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH": "false",
    },
}
READ_ONLY = {
    "provisioningState",
    "runningStatus",
    "latestRevisionName",
    "latestReadyRevisionName",
    "latestRevisionFqdn",
    "eventStreamEndpoint",
    "outboundIpAddresses",
}


def cli_json(args):
    result = subprocess.run(
        ["az", *args, "--only-show-errors", "-o", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Azure CLI operation failed; raw output withheld")
    return json.loads(result.stdout)


def resource_url(target):
    kind = "containerApps" if target == "api" else "jobs"
    return (
        "https://management.azure.com/subscriptions/"
        f"{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.App/"
        f"{kind}/{PREFIX}{target}?api-version={API_VERSION}"
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def trusted_arm_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "management.azure.com"
        or parsed.fragment
        or not parsed.path.startswith(f"/subscriptions/{SUBSCRIPTION}/")
    ):
        raise RuntimeError("Untrusted ARM operation URL; details withheld")
    return url


def retry_delay(headers):
    value = headers.get("Retry-After", "")
    return min(max(int(value), 1), 60) if value.isdigit() else 5


class Arm:
    def __init__(self):
        self.token = cli_json(
            ["account", "get-access-token", "--resource", "https://management.azure.com/"]
        )["accessToken"]
        self.opener = urllib.request.build_opener(NoRedirect())

    def response(self, method, url, body=None):
        trusted_arm_url(url)
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        with self.opener.open(request, timeout=90) as response:
            raw = response.read()
            payload = json.loads(raw) if raw.strip() else {}
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected ARM response shape; details withheld")
            return response.status, response.headers, payload

    def poll_operation(self, url, headers, location=False):
        trusted_arm_url(url)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            time.sleep(min(retry_delay(headers), max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
            try:
                code, headers, payload = self.response("GET", url)
            except urllib.error.HTTPError as error:
                if error.code not in (408, 429, 500, 502, 503, 504):
                    raise RuntimeError(
                        f"ARM operation polling failed HTTP {error.code}; body withheld"
                    ) from None
                headers = error.headers
                continue
            except (urllib.error.URLError, TimeoutError):
                headers = {}
                continue
            if time.monotonic() >= deadline:
                break
            state = payload.get("status") or payload.get("properties", {}).get("provisioningState")
            if state in ("Failed", "Canceled"):
                raise RuntimeError("Terminal ARM operation failure: " + state)
            if state == "Succeeded":
                return
            if location and code in (200, 204) and not state:
                return
            if not state and not (location and code == 202):
                raise RuntimeError("ARM operation response lacks terminal status; details withheld")
        raise RuntimeError("ARM operation polling timeout; accepted mutation not certified")

    def request(self, method, url, body=None):
        try:
            _, headers, payload = self.response(method, url, body)
            if method == "PATCH":
                operation = headers.get("Azure-AsyncOperation") or headers.get("Location")
                if operation:
                    self.poll_operation(
                        operation, headers, location=not bool(headers.get("Azure-AsyncOperation"))
                    )
            return payload
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"ARM {method} failed HTTP {error.code}; body withheld") from None

    def snapshots(self):
        return {target: self.request("GET", resource_url(target)) for target in TARGETS}


def protected(resource):
    result = {
        key: copy.deepcopy(resource[key])
        for key in ("location", "identity", "tags")
        if key in resource
    }
    properties = copy.deepcopy(resource["properties"])
    for key in READ_ONLY:
        properties.pop(key, None)
    # Both represent an unset suffix. Any nonempty suffix remains protected.
    template = properties["template"]
    if template.get("revisionSuffix") in (None, ""):
        template.pop("revisionSuffix", None)
    for container in template["containers"]:
        container.pop("image")
    result["properties"] = properties
    return result


def images(resource):
    return [item["image"] for item in resource["properties"]["template"]["containers"]]


def require_unchanged(expected, observed):
    changed = [
        target
        for target in TARGETS
        if protected(expected[target]) != protected(observed[target])
        or images(expected[target]) != images(observed[target])
    ]
    if changed:
        raise RuntimeError("Concurrent drift or unexpected image on: " + ",".join(changed))


def check_holds(snapshot):
    for target, expected in HOLDS.items():
        containers = snapshot[target]["properties"]["template"]["containers"]
        if len(containers) != 1:
            raise RuntimeError("Unexpected container count: " + target)
        values = {item["name"]: item.get("value") for item in containers[0].get("env", [])}
        for key, value in expected.items():
            if values.get(key) != value:
                raise RuntimeError("Held configuration mismatch: " + target + "/" + key)
    for target in TARGETS:
        resource = snapshot[target]
        if resource["properties"]["provisioningState"] != "Succeeded":
            raise RuntimeError("Resource not ready: " + target)
        if target != "api":
            trigger = resource["properties"]["configuration"]["triggerType"]
            if trigger != ("Schedule" if target == "dsched" else "Event"):
                raise RuntimeError("Unexpected trigger: " + target)


def patch_body(resource, image):
    containers = copy.deepcopy(resource["properties"]["template"]["containers"])
    if len(containers) != 1:
        raise RuntimeError("Unexpected container count")
    containers[0]["image"] = image
    return {"properties": {"template": {"containers": containers}}}


def evidence(snapshot, **extra):
    return {
        **extra,
        "resources": [
            {
                "target": target,
                "images": images(snapshot[target]),
                "state": snapshot[target]["properties"]["provisioningState"],
                "revision": snapshot[target]["properties"].get("latestReadyRevisionName"),
                "holds": HOLDS.get(target, {}),
            }
            for target in TARGETS
        ],
    }


def require_quiet_jobs():
    for target in TARGETS[1:]:
        executions = cli_json(
            ["containerapp", "job", "execution", "list", "-g", GROUP, "-n", PREFIX + target]
        )
        active = [
            item["name"]
            for item in executions
            if item.get("properties", {}).get("status") in ("Running", "Processing", "Pending")
        ]
        # The existing scheduled reconciler is safe under preserved provider holds.
        if active and target != "dsched":
            raise RuntimeError("Active event-worker execution; defer release: " + target)


def wait_ready(arm, target, expected_image):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        resource = arm.request("GET", resource_url(target))
        if time.monotonic() >= deadline:
            break
        state = resource["properties"]["provisioningState"]
        if state == "Succeeded" and images(resource) == [expected_image]:
            return resource
        if state in ("Failed", "Canceled"):
            raise RuntimeError("Terminal deployment failure: " + target)
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise RuntimeError("Deployment timeout: " + target)


def require_api_health(expected_image):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        revisions = cli_json(
            ["containerapp", "revision", "list", "-g", GROUP, "-n", PREFIX + "api"]
        )
        if time.monotonic() >= deadline:
            break
        healthy = [
            item
            for item in revisions
            if item["properties"].get("active")
            and item["properties"].get("healthState") == "Healthy"
            and item["properties"].get("provisioningState") == "Provisioned"
            and item["properties"].get("trafficWeight") == 100
            and [container["image"] for container in item["properties"]["template"]["containers"]]
            == [expected_image]
        ]
        if len(healthy) == 1:
            return healthy[0]["name"]
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise RuntimeError("New API image does not have one healthy 100% traffic revision")


def release(arm, api_image, worker_image, apply=False, sleep=time.sleep):
    baseline = arm.snapshots()
    check_holds(baseline)
    for target in TARGETS:
        old = OLD_API if target == "api" else OLD_WORKER
        repository = "podcaster-api" if target == "api" else "podcaster-synthesis"
        if images(baseline[target]) != [f"{REGISTRY}/{repository}@{old}"]:
            raise RuntimeError("Expected original rollback image not live: " + target)
    print(json.dumps(evidence(baseline, milestone="before", source=SOURCE)), flush=True)
    require_quiet_jobs()
    sleep(30)
    require_unchanged(baseline, arm.snapshots())
    if not apply:
        return baseline
    expected = copy.deepcopy(baseline)
    # Update event worker templates before exposing the new API admission path.
    for target in (*TARGETS[1:], "api"):
        require_quiet_jobs()
        require_unchanged(expected, arm.snapshots())
        image = api_image if target == "api" else worker_image
        arm.request("PATCH", resource_url(target), patch_body(expected[target], image))
        wait_ready(arm, target, image)
        expected[target]["properties"]["template"]["containers"][0]["image"] = image
        observed = arm.snapshots()
        require_unchanged(expected, observed)
        check_holds(observed)
        print(json.dumps(evidence(observed, milestone="updated", target=target)), flush=True)
    healthy_revision = require_api_health(api_image)
    final = arm.snapshots()
    require_unchanged(expected, final)
    if (
        final["api"]["properties"].get("latestReadyRevisionName") != healthy_revision
        or final["api"]["properties"].get("latestRevisionName") != healthy_revision
    ):
        raise RuntimeError("API latest/ready revision differs from healthy new image")
    print(
        json.dumps(evidence(final, milestone="complete", api_healthy_revision=healthy_revision)),
        flush=True,
    )
    return final


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-digest", required=True)
    parser.add_argument("--worker-digest", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for digest in (args.api_digest, args.worker_digest):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            parser.error("Expected immutable sha256 digest")
    try:
        release(
            Arm(),
            f"{REGISTRY}/podcaster-api@{args.api_digest}",
            f"{REGISTRY}/podcaster-synthesis@{args.worker_digest}",
            args.apply,
        )
    except (RuntimeError, KeyError, ValueError, urllib.error.URLError) as error:
        # Never serialize exception response objects or a protected snapshot.
        print(
            json.dumps(
                {
                    "milestone": "stopped",
                    "error_type": type(error).__name__,
                    "reason": str(error) if isinstance(error, RuntimeError) else "details withheld",
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
