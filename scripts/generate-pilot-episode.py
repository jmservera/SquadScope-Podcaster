#!/usr/bin/env python3
"""Generate the first real Claracle pilot episode for operator review (#34).

Uses the live Azure OpenAI TTS endpoint (managed identity) to synthesize a
two-voice (fable/alloy) episode from a sample SquadScope article. The result
is staged locally for operator review — NOT published.

Usage:
    python3 scripts/generate-pilot-episode.py

Requirements:
    - az login --identity (managed identity with Cognitive Services access)
    - ffmpeg available on PATH
    - AZURE_OPENAI_ENDPOINT set or auto-discovered from RG
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from podcaster.episode import (
    Article,
    DiscussionBeat,
    EpisodeAudio,
    build_episode_script,
    operator_review_decision,
    sanitize_article,
    synthesize_episode,
)
from podcaster.generation import HOST_A_STYLE, HOST_B_STYLE
from podcaster.tts import TtsConfig

# --- Configuration ---
OPENAI_ENDPOINT = "https://podcaster-yqabcnkm2junu-openai.openai.azure.com/"
TTS_DEPLOYMENT = "tts"
CHAT_DEPLOYMENT = "chat"
VOICE_HOST_A = "fable"
VOICE_HOST_B = "alloy"
AUTH_MODE = "managed_identity"

OUTPUT_DIR = Path(".podcaster-artifacts/review/pilot-001")
MUSIC_PATH = Path("assets/music/claracle-theme.mp3")


def get_managed_identity_token(scope: str) -> str:
    """Get an access token using the az CLI managed identity."""
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", scope.rstrip("/.default"), "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# Sample article content based on a real SquadScope issue topic
SAMPLE_ARTICLE = {
    "week": "2026-W24",
    "title": "GitHub Copilot Gets Multi-File Editing and Background Agents",
    "url": "https://github.blog/changelog/2026-06-copilot-multi-file-background-agents",
    "sha256": "sample-pilot-episode-not-from-real-retrieval",
    "summary": (
        "GitHub shipped two headline features this week: Copilot can now edit multiple files "
        "in a single pass with full context awareness, and background agents can run autonomously "
        "on tasks while developers focus elsewhere. The multi-file capability means refactors "
        "that used to take careful manual coordination now happen in one shot."
    ),
    "beats": [
        {
            "topic": "Multi-file editing changes how developers think about refactoring",
            "points": [
                "Instead of file-by-file changes, Copilot now sees the whole dependency graph and "
                "makes coordinated edits across interfaces, implementations, and tests simultaneously.",
                "Early adopters report 40-60% fewer broken imports and type errors after large "
                "renames because the tool understands the ripple effects before committing.",
                "The interesting constraint: it works best on repos under 50k lines where the "
                "full context window can hold the relevant graph. Larger codebases still benefit "
                "but need explicit file scoping.",
            ],
        },
        {
            "topic": "Background agents blur the line between tool and teammate",
            "points": [
                "A background agent can run a full test suite, triage failures, fix the obvious "
                "ones, and open PRs — all while the developer is in a meeting or asleep.",
                "The trust model is the fascinating part: agents operate with the developer's "
                "permissions but add a review gate before any merge. It's delegation with guardrails.",
                "The cost question nobody's answering yet: each background run burns tokens at "
                "scale. Teams are discovering that an always-on agent can cost more per month "
                "than the developer's IDE license.",
            ],
        },
        {
            "topic": "What this means for the open-source ecosystem",
            "points": [
                "Maintainers are already using agents to auto-triage incoming issues and label "
                "PRs, freeing up review bandwidth for the work that needs human judgment.",
                "The flip side: some projects report a wave of low-quality agent-generated PRs "
                "that pass CI but miss architectural intent. The signal-to-noise ratio in "
                "contributions is shifting and not always upward.",
            ],
        },
    ],
}


def main() -> None:
    print("=== Claracle Pilot Episode Generation (#34) ===\n")

    # Build TTS config
    config = TtsConfig(
        endpoint=OPENAI_ENDPOINT,
        tts_deployment=TTS_DEPLOYMENT,
        chat_deployment=CHAT_DEPLOYMENT,
        voice_host_a=VOICE_HOST_A,
        voice_host_b=VOICE_HOST_B,
        auth_mode=AUTH_MODE,
        style_host_a=HOST_A_STYLE,
        style_host_b=HOST_B_STYLE,
    )

    print(f"Config: {json.dumps(config.safe_summary(), indent=2)}")
    assert config.production_ready, "TTS config is not production-ready"

    # Sanitize article
    article = sanitize_article(**SAMPLE_ARTICLE)
    print(f"\nArticle: {article.title} ({article.week})")
    print(f"  Beats: {len(article.beats)}")
    if article.injection_flags:
        print(f"  ⚠ Injection flags: {article.injection_flags}")

    # Build script
    script = build_episode_script(article)
    print(f"\nScript length: {len(script)} chars")
    print(f"Script preview:\n{'='*60}")
    for line in script.splitlines()[:15]:
        print(f"  {line}")
    print(f"{'='*60}\n")

    # Operator review decision (allows synthesis for review artifact)
    decision = operator_review_decision(config)
    print(f"Synthesis decision: {decision['status']}")
    if not decision["allowed"]:
        print(f"  BLOCKED: {decision['blocked_by']}")
        sys.exit(1)

    # Prepare output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "pilot-001.mp3"
    script_path = OUTPUT_DIR / "pilot-001-script.txt"
    manifest_path = OUTPUT_DIR / "pilot-001-manifest.json"

    # Save script
    script_path.write_text(script, encoding="utf-8")
    print(f"Script saved: {script_path}")

    # Synthesize with managed identity
    print("\nSynthesizing two-voice episode (fable + alloy)...")
    print("  Using managed identity token...")

    intro_music = MUSIC_PATH if MUSIC_PATH.exists() else None
    outro_music = MUSIC_PATH if MUSIC_PATH.exists() else None
    if intro_music:
        print(f"  Intro/outro music: {intro_music}")

    episode: EpisodeAudio = synthesize_episode(
        script=script,
        config=config,
        decision=decision,
        output_path=output_path,
        token_provider=get_managed_identity_token,
        intro_music=intro_music,
        outro_music=outro_music,
        manual_duration_override=True,
    )

    # Save manifest
    manifest = {
        "schema_version": "squadscope-podcaster-pilot-v1",
        "episode": article.week,
        "title": article.title,
        "output_path": str(episode.output_path),
        "sha256": episode.sha256,
        "byte_length": episode.byte_length,
        "segment_count": episode.segment_count,
        "voices": list(episode.voices),
        "validation": episode.validation.to_manifest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "operator_review_artifact",
        "publication_eligible": False,
        "music": {"intro": str(intro_music), "outro": str(outro_music)},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Report
    print(f"\n{'='*60}")
    print("✅ PILOT EPISODE GENERATED SUCCESSFULLY")
    print(f"{'='*60}")
    print(f"  Audio: {episode.output_path} ({episode.byte_length / 1024:.1f} KB)")
    print(f"  SHA256: {episode.sha256}")
    print(f"  Segments: {episode.segment_count}")
    print(f"  Validation: {episode.validation.status} (ready={episode.validation.ready})")
    if episode.validation.errors:
        print(f"  ⚠ Errors: {episode.validation.errors}")
    if episode.validation.warnings:
        print(f"  ⚠ Warnings: {episode.validation.warnings}")
    print(f"  Script: {script_path}")
    print(f"  Manifest: {manifest_path}")
    print(f"\n  PURPOSE: operator review artifact — NOT eligible for publication")
    print(f"  Review this episode and approve/reject before any distribution.")


if __name__ == "__main__":
    main()
