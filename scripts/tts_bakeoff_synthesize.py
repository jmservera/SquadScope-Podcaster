#!/usr/bin/env python3
"""Generate private TTS bakeoff comparison samples (issue #41, feeds #4).

Dry-run (default) builds and prints the synthesis plan and manifest WITHOUT
contacting any provider or requiring any key, so it is safe to run anywhere
and in CI. Execute mode (``--execute``) synthesizes each candidate voice with
Azure Speech and stores the audio in the existing private storage account.

Stop rule (per #41): if execute mode is requested but the required Azure Speech
context is missing, this script prints the exact missing variable names and
exits non-zero instead of attempting a workaround.

Secrets are never printed: the API key is read from the environment only, and
SAS query strings are redacted from any URL in the manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcaster.tts_bakeoff import (  # noqa: E402
    BakeoffCandidate,
    SampleResult,
    SampleSpec,
    build_manifest,
    build_plan,
    default_candidates,
    redact_url,
    script_sha256,
)

DEFAULT_SCRIPT = "docs/tts-bakeoff-test-script.txt"
REQUIRED_EXECUTE_ENV = ("AZURE_SPEECH_ENDPOINT",)
# Exactly one auth mechanism must be present in execute mode.
AUTH_ENV_OPTIONS = ("AZURE_SPEECH_KEY", "AZURE_SPEECH_ACCESS_TOKEN")


class BakeoffError(Exception):
    pass


def load_candidates(path: str | None) -> list[BakeoffCandidate]:
    if not path:
        return default_candidates()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise BakeoffError(f"cannot read voices file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BakeoffError(f"voices file {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise BakeoffError("voices file must be a non-empty JSON array")
    candidates = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get("provider") or not item.get("narrator_voice"):
            raise BakeoffError(f"voices[{index}] requires provider and narrator_voice")
        candidates.append(
            BakeoffCandidate(
                provider=str(item["provider"]),
                narrator_voice=str(item["narrator_voice"]),
                locale=str(item.get("locale", "en-US")),
                guest_voice=(str(item["guest_voice"]) if item.get("guest_voice") else None),
                enabled=bool(item.get("enabled", True)),
                notes=str(item.get("notes", "")),
            )
        )
    return candidates


def missing_execute_context() -> list[str]:
    missing = [name for name in REQUIRED_EXECUTE_ENV if not os.environ.get(name)]
    if not any(os.environ.get(name) for name in AUTH_ENV_OPTIONS):
        missing.append(f"one of [{', '.join(AUTH_ENV_OPTIONS)}]")
    return missing


def synthesize_via_azure_speech(spec: SampleSpec, timeout: float) -> bytes:
    """Call Azure Speech REST text-to-speech and return MP3 bytes.

    Only used in execute mode. Imported lazily so dry-run needs no network deps.
    """

    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    endpoint = os.environ["AZURE_SPEECH_ENDPOINT"].rstrip("/")
    url = f"{endpoint}/cognitiveservices/v1"
    headers = {
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
        "User-Agent": "squadscope-podcaster-bakeoff",
    }
    token = os.environ.get("AZURE_SPEECH_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["Ocp-Apim-Subscription-Key"] = os.environ["AZURE_SPEECH_KEY"]

    request = Request(url, data=spec.ssml.encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        # Never surface the response body verbatim; it may echo request content.
        raise BakeoffError(f"Azure Speech returned HTTP {exc.code}") from None
    except URLError as exc:
        raise BakeoffError(f"Azure Speech request failed: {exc.reason}") from None
    except TimeoutError:
        raise BakeoffError("Azure Speech request timed out") from None


def build_storage_backend():
    """Resolve the existing private storage backend (reuses repo conventions).

    Execute mode targets the same Azure Blob account used by the generate
    pipeline when ``PODCASTER_STORAGE_ACCOUNT_URL`` is set, otherwise it falls
    back to the local artifact store so a dry-fit run never needs Azure.
    """

    from podcaster.storage import create_storage_backend

    return create_storage_backend()


def run_execute(plan: list[SampleSpec], timeout: float) -> list[SampleResult]:
    backend = build_storage_backend()
    results: list[SampleResult] = []
    for spec in plan:
        result = SampleResult(
            provider=spec.candidate.provider,
            narrator_voice=spec.candidate.narrator_voice,
            guest_voice=spec.candidate.guest_voice,
            blob_path=spec.blob_path,
            status="pending",
        )
        try:
            audio = synthesize_via_azure_speech(spec, timeout)
            stored = backend.put_bytes(spec.blob_path, audio, "audio/mpeg")
            result.status = "stored"
            result.size_bytes = stored.size_bytes
            result.content_type = stored.content_type
            result.url = stored.url
        except BakeoffError as exc:
            result.status = "failed"
            result.error = str(exc)
        results.append(result)
    return results


def plan_results(plan: list[SampleSpec]) -> list[SampleResult]:
    return [
        SampleResult(
            provider=spec.candidate.provider,
            narrator_voice=spec.candidate.narrator_voice,
            guest_voice=spec.candidate.guest_voice,
            blob_path=spec.blob_path,
            status="planned",
        )
        for spec in plan
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--script", default=DEFAULT_SCRIPT, help="Reviewed test script path.")
    parser.add_argument("--week", default="bakeoff", help="Label used in blob paths.")
    parser.add_argument("--voices", default=None, help="Optional JSON file overriding the candidate voices.")
    parser.add_argument("--include-disabled", action="store_true", help="Include candidates marked enabled=false.")
    parser.add_argument("--execute", action="store_true", help="Synthesize and store audio (requires Azure Speech context).")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout per synthesis in seconds.")
    parser.add_argument("--manifest-out", default=None, help="Write the manifest JSON to this path.")
    args = parser.parse_args(argv)

    try:
        script_text = Path(args.script).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read script {args.script}: {exc}", file=sys.stderr)
        return 2

    try:
        candidates = load_candidates(args.voices)
        plan = build_plan(script_text, args.week, candidates, include_disabled=args.include_disabled)
    except BakeoffError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not plan:
        print("no enabled candidate voices to synthesize; use --include-disabled to plan them", file=sys.stderr)
        return 2

    mode = "execute" if args.execute else "dry-run"
    if args.execute:
        missing = missing_execute_context()
        if missing:
            print(
                "::error::execute mode is missing required Azure Speech context: "
                + ", ".join(missing)
                + ". Refusing to synthesize without it.",
                file=sys.stderr,
            )
            return 3
        results = run_execute(plan, args.timeout)
    else:
        results = plan_results(plan)

    manifest = build_manifest(args.week, args.script, script_text, results, mode)

    print(f"mode={mode}")
    print(f"script_sha256={script_sha256(script_text)}")
    print(f"samples={len(results)}")
    for result in results:
        guest = result.guest_voice or "-"
        location = redact_url(result.url) if result.url else result.blob_path
        print(f"  [{result.status}] {result.provider} narrator={result.narrator_voice} guest={guest} -> {location}")

    if args.manifest_out:
        Path(args.manifest_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.manifest_out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"manifest_written={args.manifest_out}")
    else:
        print(json.dumps(manifest, indent=2))

    if any(result.status == "failed" for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
