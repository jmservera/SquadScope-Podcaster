"""Tests for podcaster.packaging — publishing packet generation (#6)."""

from __future__ import annotations

import json
from io import BytesIO
from zipfile import ZipFile

from podcaster.packaging import (
    build_publishing_packet,
    generate_show_notes,
    generate_transcript,
)


def test_generate_transcript_contains_spoken_lines():
    script = "Title: Test\n---\nTheo: Welcome to the show!\nVera: Thanks for having me."
    result = generate_transcript(script, week="2026-W24", duration_seconds=60.0)
    assert "Theo: Welcome to the show!" in result
    assert "Vera: Thanks for having me." in result
    assert "2026-W24" in result


def test_generate_show_notes_contains_metadata():
    result = generate_show_notes(
        week="2026-W24",
        title="Test Episode",
        article_url="https://example.com/article",
    )
    assert "2026-W24" in result
    assert "Test Episode" in result
    assert "https://example.com/article" in result
    assert "AI Voice Disclosure" in result
    assert "claracle.com" in result


def test_build_publishing_packet_zip_structure():
    transcript = generate_transcript(
        "---\nTheo: Hi\nVera: Hello", week="2026-W24", duration_seconds=30.0
    )
    notes = generate_show_notes(week="2026-W24", title="T", article_url="https://x.com")
    manifest = {"week": "2026-W24", "status": "review"}

    packet_bytes = build_publishing_packet(
        week="2026-W24",
        job_id="test-job-1",
        script="Theo: Hi\nVera: Hello",
        transcript=transcript,
        show_notes=notes,
        audio_mp3=b"\xff\xfb\x90\x00" * 100,
        manifest=manifest,
    )

    with ZipFile(BytesIO(packet_bytes), "r") as zf:
        names = set(zf.namelist())
        assert "MANIFEST.json" in names
        assert "script.txt" in names
        assert "transcript.txt" in names
        assert "show-notes.md" in names
        assert "audio/episode-2026-W24.mp3" in names
        assert "RIGHTS-AND-ATTRIBUTION.txt" in names
        assert "CHECKSUMS.txt" in names
        assert "claim-ledger.json" in names

        # Manifest is valid JSON
        manifest_data = json.loads(zf.read("MANIFEST.json"))
        assert manifest_data["week"] == "2026-W24"

        # Checksums file references all other files
        checksums_content = zf.read("CHECKSUMS.txt").decode("utf-8")
        for name in names:
            if name != "CHECKSUMS.txt":
                assert name in checksums_content


def test_build_publishing_packet_checksums_are_sha256():
    transcript = generate_transcript("---\nTheo: Test", week="W01", duration_seconds=10.0)
    notes = generate_show_notes(week="W01", title="X", article_url="https://x.com")

    packet_bytes = build_publishing_packet(
        week="W01",
        job_id="j1",
        script="Theo: Test",
        transcript=transcript,
        show_notes=notes,
        audio_mp3=b"audio",
        manifest={"week": "W01"},
    )

    with ZipFile(BytesIO(packet_bytes), "r") as zf:
        checksums_content = zf.read("CHECKSUMS.txt").decode("utf-8")
        for line in checksums_content.strip().splitlines():
            hash_part, _ = line.split("  ", 1)
            assert len(hash_part) == 64  # SHA-256 hex length
