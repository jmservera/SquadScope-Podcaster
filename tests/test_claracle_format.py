from __future__ import annotations

from datetime import datetime, timezone

from podcaster.generation import (
    AI_VOICE_DISCLOSURE,
    HOST_A_NAME,
    HOST_A_VOICE,
    HOST_B_NAME,
    HOST_B_VOICE,
    PODCAST_NAME,
    PODCAST_SPOKEN_SITE,
    PODCAST_URL,
    generate_artifacts,
)


def _payload() -> dict[str, object]:
    return {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_sha256": "a" * 64,
    }


def _artifact(suffix: str) -> str:
    created_at = datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc)
    artifacts = generate_artifacts("podcast-2026-W23-deadbeef", _payload(), created_at)
    return next(a for a in artifacts if a.path.endswith(suffix)).content.decode("utf-8")


def test_operator_voice_constants_are_fable_and_alloy() -> None:
    assert HOST_A_VOICE == "fable"
    assert HOST_B_VOICE == "alloy"
    assert PODCAST_NAME == "Claracle"
    assert PODCAST_URL == "https://www.claracle.com"


def test_script_opens_naming_claracle_with_site_link() -> None:
    script = _artifact("script.txt")
    body = script.split("---", 1)[1]
    first_spoken = next(line for line in body.splitlines() if line.startswith(HOST_A_NAME + ":"))
    assert PODCAST_NAME in first_spoken
    # Operator feedback (#72): name the issue/week up front and introduce the host.
    assert "issue!" in first_spoken
    assert f"I'm {HOST_A_NAME}" in first_spoken


def test_spoken_body_uses_bare_domain_not_url_scheme() -> None:
    # Operator feedback (#72.3): never voice a URL scheme on-mic; written metadata
    # may still carry the full ``PODCAST_URL``.
    script = _artifact("script.txt")
    body = script.split("---", 1)[1]
    assert PODCAST_SPOKEN_SITE in body
    assert "https://" not in body
    assert PODCAST_URL not in body


def test_script_is_two_voice_with_named_hosts_and_fable_alloy() -> None:
    script = _artifact("script.txt")
    assert f"{HOST_A_NAME}:" in script
    assert f"{HOST_B_NAME}:" in script
    assert f"{HOST_A_NAME} = {HOST_A_VOICE}" in script
    assert f"{HOST_B_NAME} = {HOST_B_VOICE}" in script


def test_ai_voice_disclosure_appears_within_first_60_seconds() -> None:
    transcript = _artifact("transcript.txt")
    body = transcript.split("---", 1)[1]
    disclosure_lines = [line for line in body.splitlines() if AI_VOICE_DISCLOSURE in line]
    assert disclosure_lines, "AI-voice disclosure must appear in the transcript body"
    # Transcript stamps 15s per spoken line; disclosure must land inside the first minute.
    stamp = disclosure_lines[0].split("]", 1)[0].lstrip("[")
    minutes, seconds, _ = (int(part) for part in stamp.split(":"))
    assert minutes * 60 + seconds < 60


def test_show_notes_disclose_ai_voices_and_link_claracle() -> None:
    show_notes = _artifact("show-notes.md")
    assert AI_VOICE_DISCLOSURE in show_notes
    assert PODCAST_URL in show_notes
    assert HOST_A_VOICE in show_notes
    assert HOST_B_VOICE in show_notes


def test_format_stays_publication_blocked_and_dry_run_safe() -> None:
    script = _artifact("script.txt")
    assert "no audio has been synthesized." in script.lower()
    assert script.count("Host outro: Manual review is required before publishing.") == 1
