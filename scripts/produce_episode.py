"""Produce ONE real Claracle podcast episode locally for operator review (#60/#34).

This is a one-shot operator tool, not part of the public publishing path. It:

1. Builds a sanitized :class:`podcaster.episode.Article` from a real published
   SquadScope weekly article (default: 2026-W24).
2. Authors the joyful two-voice Claracle conversation script (fable + alloy)
   with the Claracle intro and the AI-voice disclosure in the first exchange.
3. Synthesizes real audio via the Azure OpenAI ``tts-bakeoff`` deployment using
   a managed-identity / Azure AD bearer token (the account has local-auth
   disabled), stitches the per-voice turns, and runs the ffmpeg validation gate.
4. STAGES the MP3, script, and a review manifest under an output directory and
   prints a safe summary (path, duration, cost). Never prints the bearer token.

The produced audio is a REVIEW artifact for the operator (the reviewer for the
first episode); it stays ineligible for public publication until the human
review gate records approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcaster.artifact_access import (  # noqa: E402
    operator_download_access_metadata,
    sas_download_record,
)
from podcaster.episode import (  # noqa: E402
    build_episode_script,
    operator_review_decision,
    parse_script_segments,
    sanitize_article,
    synthesize_episode,
)
from podcaster.storage import (  # noqa: E402
    StorageBackend,
    create_storage_backend,
)
from podcaster.tts import OPENAI_SCOPE, load_tts_config  # noqa: E402

# OpenAI text-to-speech list price (USD per 1M input characters). Used only for
# a transparent cost estimate in the review manifest.
TTS_USD_PER_MILLION_CHARS = 15.0


def w24_article() -> dict[str, object]:
    """Curated, real talking points from SquadScope Week 2026-W24.

    Source: https://claracle.com/weekly/2026/w24/ — "Skills Go Vertical,
    Hardware Gets Smart, and the Spam Floor Gets Louder". The hosts comment on
    these beats; they are not a verbatim reading of the article.
    """

    return {
        "week": "2026-W24",
        "title": "Skills go vertical, hardware gets smart, and the spam floor gets louder",
        "url": "https://claracle.com/weekly/2026/w24/",
        "sha256": "",
        "summary": (
            "Week 24 deepens two trends from the week before — agent skills going vertical and "
            "local-sovereignty tooling — while a breakout hardware project anchors the week's real "
            "creativity against a noticeably heavier noise floor of coordinated spam and fraud repos."
        ),
        "beats": [
            {
                "topic": "agent skills are growing up from generic helpers into professional vertical tools",
                "points": [
                    "The skills landing this week target specific practitioner communities instead of "
                    "general developers — design mockups, quality gates for AI-generated code, shared "
                    "coding standards across Claude Code, Codex, Cursor and Copilot, and trace capture "
                    "for evaluation.",
                    "OpenAI itself shipped official role-specific Codex plugin templates, which is the "
                    "clearest institutional signal yet that the skills format is a supported, first-class "
                    "distribution layer.",
                    "The interesting question flipped: it's no longer whether skills are a real format, "
                    "it's how fast domain-specific professional packs displace the generic prompt bundles.",
                ],
            },
            {
                "topic": "hardware-and-software crossover hit a new high-water mark this week",
                "points": [
                    "The breakout was skylight, a project that uses a software-defined radio to project "
                    "live aircraft overhead onto your ceiling in real time, alongside the sun, moon, stars "
                    "and the space station — and the star velocity looks like genuine enthusiasm, not hype.",
                    "Even Linus Torvalds published a tiny magnetic-sensor scroll-wheel toy, which is a fun "
                    "reminder that real-time physical sensing is now casual, weekend hobbyist territory.",
                ],
            },
            {
                "topic": "local-first and self-sovereignty tooling is in active inventory expansion",
                "points": [
                    "We saw self-hosted dev sandboxes with preview URLs in a single command aimed right at "
                    "coding agents, an offline wearable companion that keeps all health data on-device with "
                    "no cloud and no subscription, and a local-first AI memory layer for any model backend.",
                    "The throughline is the sovereignty impulse showing up in more and more form factors — "
                    "people increasingly want their tools to run on their own hardware, on their terms.",
                ],
            },
            {
                "topic": "the blind spot nobody is addressing — supply-chain security for agent skills",
                "points": [
                    "Skills packs are now a genuine distribution format with packs shipping weekly, but there "
                    "is no tooling to audit what a skill file actually does when an agent executes it, whether "
                    "it phones home, or whether its instructions can be hijacked by an upstream change.",
                    "Prompt-injection defense tooling is conspicuously absent from developer activity too — the "
                    "attack surface for prompt-injected agent actions is growing faster than the defenses, and "
                    "that gap will get exploited before most people see it coming.",
                ],
            },
            {
                "topic": "the noise floor, because telling signal from spam is half the job",
                "points": [
                    "This week's noise was heavier than last — a wave of trading-bot repos with implausibly "
                    "inflated fork counts, and clusters of game-cheat and software-activator repos posting "
                    "suspiciously uniform star counts within hours of each other from brand-new accounts.",
                    "The honest takeaway is that the platform's filtering job is getting harder, not easier, "
                    "so a little healthy skepticism when you're browsing trending charts goes a long way.",
                ],
            },
        ],
    }


def az_cli_token_provider(scope: str) -> str:
    """Fetch an Azure AD bearer token via the Azure CLI managed identity.

    The token value is never logged or printed. Used for local production where
    the host's managed identity has data-plane access to the OpenAI account.
    """

    resource = scope.removesuffix("/.default")
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv"],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("az returned an empty access token")
    return token


def _safe_summary_transport_logger():  # pragma: no cover - trivial
    return None


def estimate_cost_usd(billable_characters: int) -> float:
    return round(billable_characters / 1_000_000 * TTS_USD_PER_MILLION_CHARS, 6)


# Content types for the artifacts uploaded for operator review.
_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _content_type_for(name: str) -> str:
    for suffix, content_type in _CONTENT_TYPES.items():
        if name.endswith(suffix):
            return content_type
    return "application/octet-stream"


def stage_review_upload(
    storage: StorageBackend,
    *,
    prefix: str,
    week: str,
    mp3_bytes: bytes,
    script_text: str,
    base_manifest: dict[str, object],
    generated_at: str,
    expiry: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    """Upload review artifacts and mint user-delegation SAS download URLs.

    Uploads the MP3, script, and review manifest to ``prefix`` in the configured
    storage container, then mints a read-only, time-limited *user-delegation* SAS
    download URL (managed identity, no account keys) for each.

    Returns ``(local_manifest, storage_manifest)``:

    * ``storage_manifest`` is uploaded to storage and is **SAS-free** so no
      credential material is persisted in shared storage (honors #18).
    * ``local_manifest`` includes the SAS download URLs for the operator and is
      written only to the gitignored local output directory.
    """

    safe_prefix = prefix.strip("/")
    blob_mp3 = f"{safe_prefix}/claracle-{week}.mp3"
    blob_script = f"{safe_prefix}/claracle-{week}-script.txt"
    blob_manifest = f"{safe_prefix}/claracle-{week}-review-manifest.json"

    stored_mp3 = storage.put_bytes(blob_mp3, mp3_bytes, _content_type_for(blob_mp3))
    stored_script = storage.put_bytes(blob_script, script_text.encode("utf-8"), _content_type_for(blob_script))

    expires_at = base_manifest.get("expires_at")
    if not isinstance(expires_at, str):
        expires_at = expiry.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    artifact_storage = {
        "container": os.environ.get("PODCASTER_STORAGE_CONTAINER", "podcaster-artifacts"),
        "prefix": safe_prefix,
        "uploaded_with": "managed_identity",
        "account_key_used": False,
        "objects": [
            {"path": stored_mp3.path, "size_bytes": stored_mp3.size_bytes, "content_type": stored_mp3.content_type, "sha256": base_manifest.get("audio", {}).get("sha256")},
            {"path": stored_script.path, "size_bytes": stored_script.size_bytes, "content_type": stored_script.content_type},
            {"path": blob_manifest, "content_type": _content_type_for(blob_manifest)},
        ],
        "access": operator_download_access_metadata(generated_at, expires_at),
    }

    signed_mp3 = storage.generate_download_url(blob_mp3, expiry=expiry)
    signed_script = storage.generate_download_url(blob_script, expiry=expiry)
    signed_manifest = storage.generate_download_url(blob_manifest, expiry=expiry)
    signed = {"audio_mp3": signed_mp3, "script_txt": signed_script, "review_manifest": signed_manifest}

    storage_manifest = dict(base_manifest)
    storage_manifest["artifact_storage"] = artifact_storage
    # SAS URLs are secrets: the stored manifest records the method/expiry only.
    storage_manifest["download"] = {
        "note": "Signed SAS download URLs are secrets and are NOT stored here; request them out-of-band.",
        "method": signed_mp3.method,
        "expires_at": expires_at,
        "urls": {key: sas_download_record(value, include_url=False) for key, value in signed.items()},
    }

    # Upload the SAS-free manifest to shared storage.
    storage.put_bytes(blob_manifest, _manifest_bytes(storage_manifest), _content_type_for(blob_manifest))

    local_manifest = dict(base_manifest)
    local_manifest["artifact_storage"] = artifact_storage
    local_manifest["download"] = {
        "note": "Short-lived user-delegation SAS download URLs (secret; do not commit or forward).",
        "method": signed_mp3.method,
        "expires_at": expires_at,
        "urls": {key: sas_download_record(value, include_url=True) for key, value in signed.items()},
    }
    return local_manifest, storage_manifest


def _manifest_bytes(manifest: dict[str, object]) -> bytes:
    return (json.dumps(manifest, indent=2) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Produce one real Claracle episode for operator review.")
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / ".podcaster-artifacts" / "review"),
        help="Output directory for staged review artifacts (not published).",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip uploading artifacts to storage and minting SAS download URLs.",
    )
    parser.add_argument(
        "--storage-prefix",
        default="review",
        help="Blob path prefix for uploaded review artifacts (default: review).",
    )
    parser.add_argument(
        "--sas-expiry-days",
        type=int,
        default=7,
        help="Lifetime (days) of the minted user-delegation SAS download URLs (default: 7).",
    )
    args = parser.parse_args()

    # Resolve TTS config from environment; allow this tool to default the
    # operator-selected production values so a first episode can be produced now.
    os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://podcaster-openai-bakeoff-20260609.openai.azure.com/")
    os.environ.setdefault("AZURE_OPENAI_TTS_DEPLOYMENT", "tts-bakeoff")
    os.environ.setdefault("AZURE_OPENAI_TTS_VOICE_HOST_A", "fable")
    os.environ.setdefault("AZURE_OPENAI_TTS_VOICE_HOST_B", "alloy")
    os.environ.setdefault("AZURE_OPENAI_AUTH_MODE", "managed_identity")

    config = load_tts_config()
    if not config.production_ready:
        print("ERROR: TTS config is not production-ready; check AZURE_OPENAI_* settings.", file=sys.stderr)
        print(json.dumps(config.safe_summary(), indent=2), file=sys.stderr)
        return 2

    article = sanitize_article(**w24_article())
    script = build_episode_script(article)
    segments = parse_script_segments(script)
    billable_characters = sum(len(text) for _, text in segments)

    decision = operator_review_decision(config)
    if not decision["allowed"]:
        print(f"ERROR: synthesis blocked: {decision['blocked_by']}", file=sys.stderr)
        return 3

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / f"claracle-{article.week}.mp3"
    script_path = out_dir / f"claracle-{article.week}-script.txt"
    manifest_path = out_dir / f"claracle-{article.week}-review-manifest.json"

    script_path.write_text(script, encoding="utf-8")

    print(f"Synthesizing {len(segments)} segments via Azure OpenAI TTS (deployment={config.tts_deployment})...")
    episode = synthesize_episode(
        script,
        config,
        decision,
        mp3_path,
        token_provider=az_cli_token_provider,
    )

    duration = episode.validation.metadata.duration_seconds if episode.validation.metadata else 0.0
    cost_usd = estimate_cost_usd(billable_characters)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    expiry = datetime.now(timezone.utc) + timedelta(days=max(1, args.sas_expiry_days))
    expires_at = expiry.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    manifest = {
        "schema": "podcaster.operator-review-episode/v1",
        "purpose": "operator review artifact; NOT published; publication stays human-gated",
        "podcast": "Claracle",
        "podcast_url": "https://www.claracle.com",
        "week": article.week,
        "title": article.title,
        "source_article_url": article.url,
        "source_article_sha256": article.sha256,
        "injection_flags": list(article.injection_flags),
        "voices": {"host_a": "fable", "host_b": "alloy"},
        "tts": config.safe_summary(),
        "synthesis_decision": decision,
        "audio": {
            "path": str(mp3_path.resolve()),
            "sha256": episode.sha256,
            "byte_length": episode.byte_length,
            "segment_count": episode.segment_count,
            "duration_seconds": duration,
            "validation": episode.validation.to_manifest(),
        },
        "cost": {
            "billable_characters": billable_characters,
            "usd_per_million_chars": TTS_USD_PER_MILLION_CHARS,
            "estimated_usd": cost_usd,
        },
        "expires_at": expires_at,
        "generated_at": generated_at,
    }

    download_urls: dict[str, str] = {}
    upload_error: str | None = None
    if not args.no_upload:
        try:
            storage = create_storage_backend()
            print(f"Uploading review artifacts to storage (prefix={args.storage_prefix}) and minting SAS...")
            local_manifest, _storage_manifest = stage_review_upload(
                storage,
                prefix=args.storage_prefix,
                week=article.week,
                mp3_bytes=mp3_path.read_bytes(),
                script_text=script,
                base_manifest=manifest,
                generated_at=generated_at,
                expiry=expiry,
            )
            manifest = local_manifest
            for key, record in manifest.get("download", {}).get("urls", {}).items():
                url = record.get("url")
                if isinstance(url, str):
                    download_urls[key] = url
        except Exception as exc:  # noqa: BLE001 - surface upload issues without aborting the staged episode
            upload_error = str(exc)
            print(f"WARNING: artifact upload / SAS generation failed: {upload_error}", file=sys.stderr)

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print("=" * 72)
    print(f"Episode staged (review-only, not published):")
    print(f"  MP3:        {mp3_path.resolve()}")
    print(f"  Script:     {script_path.resolve()}")
    print(f"  Manifest:   {manifest_path.resolve()}")
    print(f"  Duration:   {duration:.1f}s")
    print(f"  Segments:   {episode.segment_count}")
    print(f"  Size:       {episode.byte_length} bytes")
    print(f"  Validation: {episode.validation.status} (ready={episode.validation.ready})")
    if episode.validation.errors:
        print(f"  Errors:     {episode.validation.errors}")
    if episode.validation.warnings:
        print(f"  Warnings:   {episode.validation.warnings}")
    print(f"  Est. cost:  ${cost_usd} ({billable_characters} chars)")
    if download_urls:
        print("-" * 72)
        print(f"  Download SAS URLs (expire {expires_at}; SECRET — do not commit/forward):")
        for key, url in download_urls.items():
            print(f"    {key}: {url}")
    elif not args.no_upload and upload_error is None:
        print("  Upload: completed (no signed SAS URL returned by storage backend)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
