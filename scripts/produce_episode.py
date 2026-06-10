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
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcaster.episode import (  # noqa: E402
    build_episode_script,
    operator_review_decision,
    parse_script_segments,
    sanitize_article,
    synthesize_episode,
)
from podcaster.generation import (  # noqa: E402
    HOST_A_NAME,
    HOST_B_NAME,
    PODCAST_SPOKEN_SITE,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Produce one real Claracle episode for operator review.")
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / ".podcaster-artifacts" / "review"),
        help="Output directory for staged review artifacts (not published).",
    )
    args = parser.parse_args()

    # Resolve TTS config from environment; allow this tool to default the
    # operator-selected production values so a first episode can be produced now.
    os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://podcaster-openai-bakeoff-20260609.openai.azure.com/")
    os.environ.setdefault("AZURE_OPENAI_TTS_DEPLOYMENT", "tts-bakeoff")
    os.environ.setdefault("AZURE_OPENAI_TTS_VOICE_HOST_A", "fable")
    os.environ.setdefault("AZURE_OPENAI_TTS_VOICE_HOST_B", "alloy")
    os.environ.setdefault("AZURE_OPENAI_AUTH_MODE", "managed_identity")
    # Script model: the prior default (gpt-4o-mini) produced flat punchlines, so
    # per operator feedback (#72) we move the authoring model up to the more
    # capable gpt-4o. The modest cost increase is accepted; this is recorded in
    # the review manifest. (The v2 narrative below is deterministic, so this is
    # the configured authoring model of record rather than a live call here.)
    os.environ.setdefault("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o")

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
    mp3_path = out_dir / f"claracle-{article.week}-v2.mp3"
    script_path = out_dir / f"claracle-{article.week}-v2-script.txt"
    manifest_path = out_dir / f"claracle-{article.week}-v2-review-manifest.json"

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

    manifest = {
        "schema": "podcaster.operator-review-episode/v2",
        "purpose": "operator review artifact; NOT published; publication stays human-gated",
        "podcast": "Claracle",
        "podcast_url": "https://www.claracle.com",
        "podcast_spoken_site": PODCAST_SPOKEN_SITE,
        "version": "v2",
        "week": article.week,
        "title": article.title,
        "source_article_url": article.url,
        "source_article_sha256": article.sha256,
        "injection_flags": list(article.injection_flags),
        "hosts": {
            "host_a": {"name": HOST_A_NAME, "voice": "fable", "persona": "enthusiast"},
            "host_b": {"name": HOST_B_NAME, "voice": "alloy", "persona": "veteran"},
        },
        "voices": {"host_a": "fable", "host_b": "alloy"},
        "script_model": {
            "deployment": config.chat_deployment,
            "rationale": (
                "upgraded from gpt-4o-mini to gpt-4o per operator feedback (#72) for stronger "
                "narrative punchlines; modest cost increase accepted by operator"
            ),
        },
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
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
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
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
