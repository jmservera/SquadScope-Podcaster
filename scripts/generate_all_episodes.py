"""Batch podcast generation for all weeks (W21–W24).

W21 gets a special welcome sentence prepended to the script directions.
All episodes use the same config/pipeline as run_full_pipeline.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
PODCAST_CONFIG_PATH = PROJECT_ROOT / "podcast-config.json"
INTRO_MUSIC = PROJECT_ROOT / "assets" / "music" / "summer-sport.mp3"
OUTRO_MUSIC = PROJECT_ROOT / "assets" / "music" / "summer-sport.mp3"

WEEKS = ["W21", "W22", "W23", "W24"]

# Special first-episode welcome for W21
W21_WELCOME = (
    "Welcome everyone to the very first episode of Claracle! "
    "This is a brand new adventure where we bring you weekly AI-powered "
    "analysis of what's trending on GitHub, set against the biggest tech "
    "stories of the week. Let's dive in."
)


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env(ENV_PATH)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("generate_all_episodes")

from podcaster.audio import MusicMixSpec
from podcaster.config import MusicMixConfig, PodcastConfig, ScriptDirections
from podcaster.episode import synthesize_episode
from podcaster.hooks import generate_hooks
from podcaster.script_gen import ScriptGenConfig, generate_script
from podcaster.storage import ManagedIdentityTokenCredential
from podcaster.tts import load_tts_config


def _strip_frontmatter(markdown: str) -> tuple[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n?", markdown, re.DOTALL)
    if not match:
        return "", markdown
    return match.group(1), markdown[match.end():]


def _frontmatter_value(frontmatter: str, key: str, *, default: str = "") -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+)$", frontmatter)
    if not match:
        return default
    return match.group(1).strip().strip('"').strip("'")


def _article_url_for_week(week: str) -> str:
    year, _, week_suffix = week.partition("-W")
    return f"https://claracle.com/weekly/{year}/w{week_suffix.lower()}/"


def _az_cli_token(scope: str) -> str:
    resource = scope.removesuffix("/.default")
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("az account get-access-token returned an empty token")
    return token


def _build_token_provider():
    managed_identity_get_token = ManagedIdentityTokenCredential().get_token

    def token_provider(scope: str) -> str:
        try:
            return managed_identity_get_token(scope)
        except Exception as exc:
            logger.warning("Managed identity token lookup failed; falling back to az CLI (%s)", exc)
            return _az_cli_token(scope)

    return token_provider


def generate_episode(week_id: str, token_provider, script_config, tts_config,
                     podcast_config, script_directions, music_mix_spec) -> Path:
    """Generate a single episode for the given week. Returns the output MP3 path."""
    article_path = PROJECT_ROOT / f"{week_id}-article.md"
    if not article_path.exists():
        raise FileNotFoundError(f"Article not found: {article_path}")

    job_name = f"podcast-2026-{week_id}"
    job_root = PROJECT_ROOT / ".podcaster-artifacts" / "jobs" / job_name
    output_mp3 = job_root / "audio" / "episode.mp3"
    script_output = job_root / "script.txt"

    raw_article = article_path.read_text(encoding="utf-8")
    frontmatter, article_content = _strip_frontmatter(raw_article)
    article_title = _frontmatter_value(frontmatter, "title", default=f"{week_id} Podcast Article")
    week = _frontmatter_value(frontmatter, "week", default=f"2026-{week_id}")
    article_sha256 = hashlib.sha256(article_content.encode("utf-8")).hexdigest()
    article_url = _article_url_for_week(week)

    # For W21, prepend the welcome sentence to show_intro
    directions_for_episode = script_directions
    if week_id == "W21":
        logger.info("W21: Adding first-episode welcome sentence")
        # Create a modified directions with the welcome prepended
        import dataclasses
        directions_for_episode = dataclasses.replace(
            script_directions,
            show_intro=(W21_WELCOME + " " + script_directions.show_intro)
            if script_directions.show_intro
            else W21_WELCOME,
        )

    # Generate hooks
    logger.info("[%s] Generating personality-matched hooks...", week_id)
    hooks = generate_hooks(
        config=script_config,
        podcast_config=podcast_config,
        token_provider=token_provider,
    )
    job_root.mkdir(parents=True, exist_ok=True)
    hooks_path = job_root / "hooks.json"
    hooks_path.write_text(
        json.dumps({"host_a": hooks.host_a, "host_b": hooks.host_b}, indent=2),
        encoding="utf-8",
    )

    # Generate script
    logger.info("[%s] Generating script...", week_id)
    script = generate_script(
        week=week,
        article_title=article_title,
        article_url=article_url,
        article_content=article_content,
        article_sha256=article_sha256,
        config=script_config,
        podcast_config=podcast_config,
        script_directions=directions_for_episode,
        token_provider=token_provider,
    )
    script_output.parent.mkdir(parents=True, exist_ok=True)
    script_output.write_text(script, encoding="utf-8")
    logger.info("[%s] Script saved (%d chars)", week_id, len(script))

    # Synthesize episode
    logger.info("[%s] Synthesizing audio...", week_id)
    decision = {"allowed": True, "reason": "batch generation", "reviewer": "coordinator"}
    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    result = synthesize_episode(
        script,
        tts_config,
        decision,
        output_mp3,
        podcast_config=podcast_config,
        token_provider=token_provider,
        intro_music=INTRO_MUSIC,
        outro_music=OUTRO_MUSIC,
        music_mix_spec=music_mix_spec,
    )
    logger.info(
        "[%s] Generated: %s (%s bytes, validation=%s)",
        week_id, result.output_path, result.byte_length, result.validation.status,
    )
    if result.validation.metadata:
        m = result.validation.metadata
        logger.info("[%s] Duration: %.1f seconds (%.1f min)", week_id, m.duration_seconds, m.duration_seconds / 60)
    if result.byte_length < 100_000:
        logger.error("[%s] FAIL: MP3 too small (%d bytes)", week_id, result.byte_length)
        return None
    return output_mp3


def main() -> None:
    logger.info("=== Batch podcast generation: %s ===", ", ".join(WEEKS))

    config_data = json.loads(PODCAST_CONFIG_PATH.read_text(encoding="utf-8"))
    podcast_config = PodcastConfig.from_payload(config_data)
    script_directions = ScriptDirections.from_payload(config_data.get("script_directions"))
    if not script_directions.has_content:
        script_directions = ScriptDirections.from_payload(config_data)
    music_mix_config = MusicMixConfig.from_payload(config_data)
    music_mix_spec = MusicMixSpec(**music_mix_config.to_mix_spec_kwargs())

    token_provider = _build_token_provider()
    script_config = ScriptGenConfig.from_env()
    tts_config = load_tts_config()

    results = {}
    for week_id in WEEKS:
        logger.info("\n{'='*60}\n[%s] Starting episode generation\n{'='*60}", week_id)
        try:
            mp3_path = generate_episode(
                week_id, token_provider, script_config, tts_config,
                podcast_config, script_directions, music_mix_spec,
            )
            results[week_id] = str(mp3_path) if mp3_path else "FAILED"
        except Exception as exc:
            logger.error("[%s] FAILED: %s", week_id, exc, exc_info=True)
            results[week_id] = f"ERROR: {exc}"

    logger.info("\n=== RESULTS ===")
    for week_id, result in results.items():
        status = "✅" if "FAILED" not in str(result) and "ERROR" not in str(result) else "❌"
        logger.info("  %s %s: %s", status, week_id, result)


if __name__ == "__main__":
    main()
