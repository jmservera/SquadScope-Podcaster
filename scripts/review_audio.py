"""Audio review of the generated podcast using gpt-audio-1.5.

Sends the first 45s and last 45s of the episode to gpt-audio-1.5 for
quality assessment. Checks for intro music, host presence, and outro music.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EPISODE_PATH = PROJECT_ROOT / ".podcaster-artifacts" / "jobs" / "podcast-2026-W24-e2e-test" / "audio" / "episode.mp3"
ENDPOINT = "https://podcaster-yqabcnkm2junu-openai.openai.azure.com/"


def get_token():
    return subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", "https://cognitiveservices.azure.com", "--query", "accessToken", "-o", "tsv"],
        text=True,
    ).strip()


def extract_segment(input_path: Path, start_seconds: float, duration_seconds: float) -> bytes:
    """Extract a segment from the audio file as MP3 bytes."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-y", "-i", str(input_path),
             "-ss", str(start_seconds), "-t", str(duration_seconds),
             "-codec:a", "libmp3lame", "-b:a", "64k", tmp_path],
            capture_output=True, check=True,
        )
        return Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def review_audio(audio_bytes: bytes, segment_label: str, token: str) -> dict:
    """Send audio to gpt-audio-1.5 for review."""
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    audio_b64 = base64.b64encode(audio_bytes).decode()

    url = f"{ENDPOINT}openai/deployments/gpt-audio-1.5/chat/completions?api-version=2024-12-01-preview"
    payload = {
        "messages": [
            {
                "role": "system",
                "content": "You are a podcast quality reviewer. Analyze the audio and answer the questions precisely."
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio_b64,
                            "format": "mp3",
                        },
                    },
                    {
                        "type": "text",
                        "text": f"""This is the {segment_label} of a podcast episode. Please analyze and answer:
1. Does this segment contain music? (yes/no, describe briefly)
2. Are there speaking voices present? If so, how many distinct voices?
3. Do the speakers identify themselves? What names?
4. Is there a welcome/intro or farewell/outro?
5. Rate the overall audio quality from 1-10 (clarity, pacing, production).
6. Any issues noticed? (artifacts, cuts, silence gaps, etc.)

Respond in JSON format with keys: has_music, voice_count, speaker_names, has_greeting_or_farewell, quality_rating, issues"""
                    },
                ],
            },
        ],
        "max_tokens": 500,
    }

    req = Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return {"success": True, "content": result["choices"][0]["message"]["content"]}
    except HTTPError as e:
        error_body = e.read().decode()[:500]
        return {"success": False, "error": f"HTTP {e.code}", "detail": error_body}
    except Exception as e:
        return {"success": False, "error": str(e)}


def main():
    if not EPISODE_PATH.exists():
        print("❌ Episode not found. Run run_full_pipeline.py first.")
        sys.exit(1)

    print(f"📻 Reviewing: {EPISODE_PATH}")
    print(f"   File size: {EPISODE_PATH.stat().st_size / 1024:.1f} KB")

    token = get_token()

    # Extract first 45 seconds (intro + opening)
    print("\n🎵 Extracting intro segment (0-45s)...")
    intro_audio = extract_segment(EPISODE_PATH, 0, 45)
    print(f"   Intro segment: {len(intro_audio)} bytes")

    # Extract last 45 seconds (outro + closing)
    print("🎵 Extracting outro segment (last 45s)...")
    # Get total duration
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(EPISODE_PATH)],
        capture_output=True, text=True,
    )
    total_duration = float(probe.stdout.strip())
    outro_start = max(0, total_duration - 45)
    outro_audio = extract_segment(EPISODE_PATH, outro_start, 45)
    print(f"   Outro segment: {len(outro_audio)} bytes")

    # Review intro
    print("\n📝 Reviewing intro with gpt-audio-1.5...")
    intro_result = review_audio(intro_audio, "OPENING (first 45 seconds)", token)
    if intro_result["success"]:
        print("   ✅ Intro review:")
        print(f"   {intro_result['content']}")
    else:
        print(f"   ⚠️  Intro review failed: {intro_result.get('error')}")
        if "detail" in intro_result:
            print(f"   Detail: {intro_result['detail'][:200]}")

    # Review outro
    print("\n📝 Reviewing outro with gpt-audio-1.5...")
    outro_result = review_audio(outro_audio, "CLOSING (last 45 seconds)", token)
    if outro_result["success"]:
        print("   ✅ Outro review:")
        print(f"   {outro_result['content']}")
    else:
        print(f"   ⚠️  Outro review failed: {outro_result.get('error')}")
        if "detail" in outro_result:
            print(f"   Detail: {outro_result['detail'][:200]}")

    # Summary
    print("\n" + "=" * 60)
    print("🎙️  PODCAST REVIEW SUMMARY")
    print("=" * 60)
    print(f"Episode: 2026-W24")
    print(f"Duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
    print(f"File size: {EPISODE_PATH.stat().st_size / 1024:.1f} KB")
    print(f"Intro review: {'✅ Complete' if intro_result['success'] else '⚠️  Failed'}")
    print(f"Outro review: {'✅ Complete' if outro_result['success'] else '⚠️  Failed'}")


if __name__ == "__main__":
    main()
