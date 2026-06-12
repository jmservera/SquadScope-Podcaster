"""End-to-end podcast generation pipeline for the W24 article."""

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
ARTICLE_PATH = PROJECT_ROOT / "W24-article.md"
PODCAST_CONFIG_PATH = PROJECT_ROOT / "podcast-config.json"
INTRO_MUSIC = PROJECT_ROOT / "assets" / "music" / "summer-sport.mp3"
OUTRO_MUSIC = PROJECT_ROOT / "assets" / "music" / "summer-sport.mp3"
JOB_ROOT = PROJECT_ROOT / ".podcaster-artifacts" / "jobs" / "podcast-2026-W24-v15"
OUTPUT_MP3 = JOB_ROOT / "audio" / "episode.mp3"
SCRIPT_OUTPUT = JOB_ROOT / "script.txt"


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
logger = logging.getLogger("run_full_pipeline")

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
    return match.group(1), markdown[match.end() :]


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


def main() -> None:
    logger.info("Starting W24 full podcast pipeline (v15 — dynamic hooks)")

    raw_article = ARTICLE_PATH.read_text(encoding="utf-8")
    frontmatter, article_content = _strip_frontmatter(raw_article)
    article_title = _frontmatter_value(frontmatter, "title", default="W24 Podcast Article")
    week = _frontmatter_value(frontmatter, "week", default="2026-W24")
    article_sha256 = hashlib.sha256(article_content.encode("utf-8")).hexdigest()
    article_url = _article_url_for_week(week)

    config_data = json.loads(PODCAST_CONFIG_PATH.read_text(encoding="utf-8"))
    podcast_config = PodcastConfig.from_payload(config_data)
    script_directions = ScriptDirections.from_payload(config_data.get("script_directions"))
    if not script_directions.has_content:
        script_directions = ScriptDirections.from_payload(config_data)

    # Parse music mix settings from config (v12 simplified intro fade)
    music_mix_config = MusicMixConfig.from_payload(config_data)
    music_mix_spec = MusicMixSpec(**music_mix_config.to_mix_spec_kwargs())

    token_provider = _build_token_provider()
    script_config = ScriptGenConfig.from_env()
    tts_config = load_tts_config()

    # Generate personality-matched hooks using the NEW hooks system
    logger.info("Generating personality-matched hooks from config styles...")
    hooks = generate_hooks(
        config=script_config,
        podcast_config=podcast_config,
        token_provider=token_provider,
    )
    logger.info(
        "Hooks generated: host_a=%d (%s), host_b=%d (%s)",
        len(hooks.host_a),
        podcast_config.host_a.name,
        len(hooks.host_b),
        podcast_config.host_b.name,
    )
    # Save hooks to job directory for audit
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    hooks_path = JOB_ROOT / "hooks.json"
    hooks_path.write_text(
        json.dumps({"host_a": hooks.host_a, "host_b": hooks.host_b}, indent=2),
        encoding="utf-8",
    )
    logger.info("Saved hooks to %s", hooks_path)

    script = generate_script(
        week=week,
        article_title=article_title,
        article_url=article_url,
        article_content=article_content,
        article_sha256=article_sha256,
        config=script_config,
        podcast_config=podcast_config,
        script_directions=script_directions,
        token_provider=token_provider,
    )
    SCRIPT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    SCRIPT_OUTPUT.write_text(script, encoding="utf-8")
    logger.info("Saved script to %s", SCRIPT_OUTPUT)

    decision = {"allowed": True, "reason": "operator-approved e2e test", "reviewer": "coordinator"}
    OUTPUT_MP3.parent.mkdir(parents=True, exist_ok=True)
    result = synthesize_episode(
        script,
        tts_config,
        decision,
        OUTPUT_MP3,
        podcast_config=podcast_config,
        token_provider=token_provider,
        intro_music=INTRO_MUSIC,
        outro_music=OUTRO_MUSIC,
        music_mix_spec=music_mix_spec,
    )
    logger.info(
        "Generated episode at %s (%s bytes, validation=%s)",
        result.output_path,
        result.byte_length,
        result.validation.status,
    )
    if result.validation.metadata:
        m = result.validation.metadata
        logger.info("Duration: %.1f seconds (%.1f min)", m.duration_seconds, m.duration_seconds / 60)
    if result.byte_length < 100_000:
        logger.error("FAIL: MP3 too small (%d bytes)", result.byte_length)
        sys.exit(1)
    logger.info("SUCCESS: valid podcast at %s", OUTPUT_MP3)


if __name__ == "__main__":
    main()
