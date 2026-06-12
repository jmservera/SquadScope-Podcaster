#!/usr/bin/env python3
"""Review the generated podcast audio using gpt-audio-1.5 (audio-capable model).

Sends the episode MP3 as input_audio in a chat completion and asks for quality review.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def get_az_token(resource: str) -> str:
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def main():
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    deployment = "gpt-audio-1.5"
    api_version = "2025-04-01-preview"

    episode_path = (
        Path(__file__).resolve().parent.parent
        / ".podcaster-artifacts" / "jobs" / "podcast-2026-W24-e2e" / "audio" / "episode.mp3"
    )

    if not episode_path.exists():
        print(f"❌ Episode not found at {episode_path}")
        sys.exit(1)

    file_size = episode_path.stat().st_size
    print(f"📄 Episode: {episode_path} ({file_size / 1024:.0f} KB)")

    # Encode audio as base64
    print("🔊 Encoding audio...")
    audio_b64 = base64.b64encode(episode_path.read_bytes()).decode("ascii")
    print(f"   Base64 length: {len(audio_b64):,} chars")

    # Get token
    token = get_az_token("https://cognitiveservices.azure.com")

    # Build request
    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You are a podcast quality reviewer. Listen to this podcast episode and answer:\n"
                            "1. Does it open with music (an intro stinger)?\n"
                            "2. Are both hosts present and distinguishable (two different voices)?\n"
                            "3. Does it end with music (an outro stinger)?\n"
                            "4. Is the audio quality good (no glitches, distortion, or silence gaps)?\n"
                            "5. Overall quality rating from 1-10?\n"
                            "6. Any other observations about the episode?\n\n"
                            "Be specific and concise in your answers."
                        ),
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio_b64,
                            "format": "mp3",
                        },
                    },
                ],
            }
        ],
        "max_tokens": 1000,
        "temperature": 0.3,
    }

    print(f"\n🤖 Sending to {deployment} for review...")
    print(f"   URL: {url[:80]}...")

    import urllib.request

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        print(f"❌ HTTP {e.code}: {body}")
        sys.exit(1)

    # Extract review
    review = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    print(f"\n{'='*60}")
    print("🎧 PODCAST QUALITY REVIEW")
    print(f"{'='*60}")
    print(review)
    print(f"{'='*60}")

    # Save review
    review_path = episode_path.parent.parent / "review.txt"
    review_path.write_text(review, encoding="utf-8")
    print(f"\n📝 Review saved to: {review_path}")


if __name__ == "__main__":
    main()
