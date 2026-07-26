"""Manual publishing packet generation for operator distribution (#6).

Assembles a self-contained ZIP packet from a completed, reviewed episode that
contains everything an operator needs to manually upload to Spotify/podcast
hosts. The packet structure follows ``backlog/manual-publishing-packet.md``.

The packet is generated ONLY after human review approval is recorded — this
module does not bypass the review gate.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from podcaster.sanitization import normalize_weekly_url

ZIP_TIMESTAMP = (2026, 6, 7, 0, 0, 0)


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=ZIP_TIMESTAMP)
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def build_publishing_packet(
    *,
    week: str,
    job_id: str,
    script: str,
    transcript: str,
    show_notes: str,
    audio_mp3: bytes,
    manifest: dict[str, object],
    claim_ledger: str | None = None,
    rights: str | None = None,
    cost_ledger: str | None = None,
) -> bytes:
    """Build the full publishing packet ZIP for an approved episode.

    All inputs must come from a reviewed and approved episode. The caller is
    responsible for ensuring the review gate has been passed before calling.
    """

    if rights is None:
        rights = _default_rights(manifest)
    if claim_ledger is None:
        claim_ledger = _placeholder_claim_ledger(week)

    files: dict[str, bytes] = {
        "MANIFEST.json": (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        "script.txt": script.encode("utf-8"),
        "transcript.txt": transcript.encode("utf-8"),
        "show-notes.md": show_notes.encode("utf-8"),
        f"audio/episode-{week}.mp3": audio_mp3,
        "RIGHTS-AND-ATTRIBUTION.txt": rights.encode("utf-8"),
    }
    if claim_ledger:
        files["claim-ledger.json"] = claim_ledger.encode("utf-8")
    if cost_ledger:
        files["COST-LEDGER.json"] = cost_ledger.encode("utf-8")

    checksums_text = "".join(
        f"{checksum(content)}  {name}\n" for name, content in sorted(files.items())
    )
    files["CHECKSUMS.txt"] = checksums_text.encode("utf-8")

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as zf:
        for name, content in sorted(files.items()):
            zf.writestr(_zip_info(name), content)
    return buffer.getvalue()


def generate_transcript(script: str, *, week: str, duration_seconds: float) -> str:
    """Generate a plain-text transcript from the episode script.

    Extracts spoken lines (host-labelled) and formats them as a readable
    transcript with metadata header. Timestamps are approximate (evenly
    distributed) since exact per-segment timing is not yet available.
    """

    lines: list[str] = []
    lines.append(f"Claracle Podcast — Week {week}")
    lines.append(f"Duration: {duration_seconds:.0f}s")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("Voices: Theo (fable) + Vera (alloy) — AI-generated synthetic voices")
    lines.append("")
    lines.append("---")
    lines.append("")

    _, _, body = script.partition("\n---")
    source = body if body else script
    spoken_lines = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Theo:") or line.startswith("Vera:"):
            spoken_lines.append(line)

    if not spoken_lines:
        return "\n".join(lines) + "\n[No spoken content found]\n"

    avg_duration = duration_seconds / len(spoken_lines) if spoken_lines else 0
    for i, spoken in enumerate(spoken_lines):
        timestamp = avg_duration * i
        minutes = int(timestamp // 60)
        seconds = int(timestamp % 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {spoken}")

    lines.append("")
    return "\n".join(lines)


def generate_show_notes(
    *,
    week: str,
    title: str,
    article_url: str,
    podcast_url: str = "https://www.claracle.com",
    hosts: dict[str, str] | None = None,
) -> str:
    """Generate markdown show notes for the episode."""

    if hosts is None:
        hosts = {"host_a": "Theo (fable)", "host_b": "Vera (alloy)"}

    article_url = normalize_weekly_url(article_url)

    return f"""# Claracle — Week {week}

## {title}

**Podcast:** [Claracle]({podcast_url})
**Source article:** [{title}]({article_url})
**Hosts:** {hosts.get("host_a", "Theo")} & {hosts.get("host_b", "Vera")}

---

### About this episode

A joyful, dynamic conversation between two AI hosts who comment on the most
relevant and surprising parts of the week's article. They do NOT read the article
verbatim — they react, contextualize, and debate.

### AI Voice Disclosure

Both hosts on this show are AI-generated synthetic voices (OpenAI TTS), not human
presenters. This disclosure appears in the first 60 seconds of every episode and
in these show notes.

### Links

- Full article: {article_url}
- Weekly issues & extended info: {podcast_url}
- Corrections/contact: {podcast_url}

### Credits

- Script generation: Claracle pipeline (SquadScope-Podcaster)
- TTS: Azure OpenAI gpt-4o-mini-tts (voices: fable + alloy)
- Music: Summer Sport by AudioCoffee (CC BY-SA 3.0; see assets/music/ATTRIBUTION.md)
"""


def _default_rights(manifest: dict[str, object]) -> str:
    """Generate default rights and attribution text."""

    return """RIGHTS AND ATTRIBUTION
======================

Audio Generation
----------------
Generated with Azure OpenAI Text-to-Speech (gpt-4o-mini-tts).
Voices: fable (Theo, Host A) and alloy (Vera, Host B).
Usage governed by the Azure OpenAI Service terms.

Source Article
--------------
Original article copyright belongs to its respective authors.
Commentary and analysis in this podcast constitutes fair use / transformative work.

Music
-----
Intro/outro music: Summer Sport by AudioCoffee.
Attribution details: assets/music/ATTRIBUTION.md

Distribution
------------
This episode may be distributed on podcast platforms by authorized operators only.
AI-voice disclosure is mandatory in the first 60 seconds and in show notes.

Generated by SquadScope-Podcaster (https://github.com/jmservera/SquadScope-Podcaster).
"""


def _placeholder_claim_ledger(week: str) -> str:
    """Placeholder claim ledger until automated claim extraction is implemented."""

    ledger = [
        {
            "note": f"Claim ledger for {week} — automated claim extraction not yet implemented.",
            "claims": [],
            "status": "placeholder",
        }
    ]
    return json.dumps(ledger, indent=2) + "\n"
