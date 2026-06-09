from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from podcaster.artifact_access import artifact_access_metadata
from podcaster.costs import build_cost_ledger


@dataclass(frozen=True)
class GeneratedArtifact:
    path: str
    content: bytes
    content_type: str


ZIP_TIMESTAMP = (2026, 6, 7, 0, 0, 0)


def generate_artifacts(
    job_id: str,
    payload: dict[str, object],
    created_at: datetime,
    expires_at: str | None = None,
    prior_monthly_episode_count: int = 0,
    prior_monthly_spend_usd: Decimal = Decimal("0.00"),
    cost_override: dict[str, object] | None = None,
) -> list[GeneratedArtifact]:
    generated_at_str = created_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if expires_at is None:
        expires_at = (created_at + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    script = _script(job_id, payload, generated_at_str)
    transcript = _transcript(script)
    show_notes = _show_notes(payload, generated_at_str)
    audio_placeholder = _audio_placeholder(job_id, payload)
    claim_ledger = _claim_ledger(payload)
    review_checklist = _review_checklist(job_id, payload)
    rights = _rights_and_attribution()
    pre_packet_bytes = [
        script.encode("utf-8"),
        claim_ledger.encode("utf-8"),
        transcript.encode("utf-8"),
        show_notes.encode("utf-8"),
        review_checklist.encode("utf-8"),
        audio_placeholder,
    ]
    cost_ledger = build_cost_ledger(
        week=str(payload["week"]),
        month=created_at.astimezone(timezone.utc).strftime("%Y-%m"),
        provider="not_selected",
        voice="not_selected",
        voice_config_hash=checksum(b"provider:not_selected|voice:not_selected"),
        billable_characters=len(script),
        duration_seconds=0,
        audio_byte_length=len(audio_placeholder),
        staged_byte_length=sum(len(content) for content in pre_packet_bytes),
        prior_episode_count=prior_monthly_episode_count,
        prior_monthly_spend_usd=prior_monthly_spend_usd,
        override=cost_override,
    )
    cost_ledger_json = json.dumps(cost_ledger, sort_keys=True, indent=2) + "\n"
    metadata = _metadata(job_id, payload, created_at, expires_at, cost_ledger)
    packet = _packet(
        script=script,
        transcript=transcript,
        show_notes=show_notes,
        metadata=metadata,
        claim_ledger=claim_ledger,
        cost_ledger=cost_ledger_json,
        review_checklist=review_checklist,
        rights=rights,
        audio_placeholder=audio_placeholder,
    )

    prefix = f"jobs/{job_id}"
    return [
        GeneratedArtifact(f"{prefix}/script.txt", script.encode("utf-8"), "text/plain; charset=utf-8"),
        GeneratedArtifact(f"{prefix}/claim-ledger.json", claim_ledger.encode("utf-8"), "application/json; charset=utf-8"),
        GeneratedArtifact(f"{prefix}/cost-ledger.json", cost_ledger_json.encode("utf-8"), "application/json; charset=utf-8"),
        GeneratedArtifact(f"{prefix}/transcript.txt", transcript.encode("utf-8"), "text/plain; charset=utf-8"),
        GeneratedArtifact(f"{prefix}/show-notes.md", show_notes.encode("utf-8"), "text/markdown; charset=utf-8"),
        GeneratedArtifact(f"{prefix}/review-checklist.md", review_checklist.encode("utf-8"), "text/markdown; charset=utf-8"),
        GeneratedArtifact(f"{prefix}/audio/{job_id}.mp3", audio_placeholder, "audio/mpeg"),
        GeneratedArtifact(f"{prefix}/packets/{job_id}.zip", packet, "application/zip"),
    ]


def manifest_bytes(manifest: dict[str, object]) -> bytes:
    return (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")


def checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _script(job_id: str, payload: dict[str, object], generated_at: str) -> str:
    week = str(payload["week"])
    article_url = str(payload["article_url"])
    article_sha256 = str(payload.get("article_sha256") or "computed-on-retrieval")
    source_artifacts = payload.get("source_artifacts") or []
    source_artifact_lines = [_source_artifact_line(item) for item in source_artifacts] or ["Source Artifact: none supplied"]

    return "\n".join(
        [
            f"Title: SquadScope Podcast – Week {week}",
            f"Episode: {week}",
            f"Source URL: {article_url}",
            f"Source SHA256: {article_sha256}",
            f"Generated: {generated_at}",
            "Generator: squad-podcaster v0.1-stub",
            *source_artifact_lines,
            "---",
            "",
            "This script is a deterministic production-path placeholder pending editorial generation from the source article.",
            "",
            "Host intro: Welcome to the SquadScope Podcast.",
            "Segment 1: [Editorial content to be added from source article.]",
            "Host outro: Manual review is required before publishing.",
            "",
        ]
    )


def _source_artifact_line(item: object) -> str:
    if isinstance(item, str):
        return f"Source Artifact: {item}"
    if not isinstance(item, dict):
        return f"Source Artifact: {item}"

    role = item.get("role")
    reference = item.get("url") or item.get("href") or item.get("uri") or item.get("path") or item.get("name") or "unspecified"
    sha256 = item.get("sha256")
    parts = [str(reference)]
    if isinstance(role, str) and role.strip():
        parts.insert(0, f"{role}:")
    if isinstance(sha256, str) and sha256.strip():
        parts.append(f"sha256={sha256}")
    return f"Source Artifact: {' '.join(parts)}"


def _transcript(script: str) -> str:
    lines = script.split("\n")

    # Extract metadata from script header
    title = ""
    episode = ""
    source_url = ""
    generated = ""
    for line in lines:
        if line.startswith("Title:"):
            title = line.replace("Title:", "").strip()
        elif line.startswith("Episode:"):
            episode = line.replace("Episode:", "").strip()
        elif line.startswith("Source URL:"):
            source_url = line.replace("Source URL:", "").strip()
        elif line.startswith("Generated:"):
            generated = line.replace("Generated:", "").strip()

    published = generated.split("T")[0] if generated else "unknown"

    # Placeholder duration for stub
    duration = "15:42"

    header = "\n".join([
        f"Title: {title}",
        f"Episode: {episode}",
        f"Published: {published}",
        f"Source: {source_url}",
        f"Duration: {duration}",
        "TTS Provider: [pending provider selection]",
        "License: CC-BY-4.0",
        "---",
        "",
    ])

    # Extract body (after "---" in script) and add timestamps
    body_start = script.find("---") + 3
    body = script[body_start:].strip()

    # Add placeholder timestamps to body lines
    timestamped_body = ""
    current_time_sec = 0
    for line in body.split("\n"):
        if line.strip():
            timestamped_body += f"[{current_time_sec//60:02d}:{current_time_sec%60:02d}:00] {line}\n"
            current_time_sec += 15  # Estimate 15 seconds per line

    return header + timestamped_body


def _show_notes(payload: dict[str, object], generated_at: str) -> str:
    week = str(payload["week"])
    article_url = str(payload["article_url"])
    published = generated_at.split("T")[0]

    return "\n".join(
        [
            f"# SquadScope Podcast — Week {week}",
            "",
            f"**Episode:** {week}",
            f"**Published:** {published}",
            "**Duration:** 15:42",
            "**Read by:** [TTS voice pending provider selection]",
            "",
            "## Show notes",
            "",
            "This episode covers key developments from the SquadScope curated articles for this week.",
            "",
            "### Segment 1: [Topic to be added from source article]",
            "",
            f"- **Article:** [Title TBD]({article_url}) — Editorial synopsis pending",
            f"- **Source:** SquadScope, {published}",
            "- **Timestamp:** [Pending audio generation]",
            "",
            "## Quick links",
            "",
            "- [SquadScope main site](https://squadscope.example)",
            f"- [Original article]({article_url})",
            "",
            "## Transcript",
            "",
            "See full transcript below or download from the publishing packet.",
            "",
            "## License",
            "",
            "This podcast, transcript, and show notes are available under CC-BY-4.0.",
            "Verify all source material rights before distribution.",
            "",
        ]
    )


def _audio_placeholder(job_id: str, payload: dict[str, object]) -> bytes:
    text = f"Audio placeholder for {job_id}; source={payload['article_url']}\n"
    return text.encode("utf-8")


def _metadata(
    job_id: str,
    payload: dict[str, object],
    created_at: datetime,
    expires_at: str,
    cost_ledger: dict[str, object],
) -> dict[str, object]:
    created_ts = created_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    return {
        "schema_version": "squadscope-podcaster-packet-v1",
        "job_id": job_id,
        "week": payload["week"],
        "article_url": payload["article_url"],
        "article_sha256": payload.get("article_sha256"),
        "source_artifacts": payload.get("source_artifacts", []),
        "generated_at": created_ts,
        "expires_at": expires_at,
        "reviewed_at": None,
        "reviewer": None,
        "cost_ledger": cost_ledger,
        "review": {
            "status": "pending",
            "required": True,
            "mechanism": "github_environment",
            "environment": "podcast-review",
            "workflow": ".github/workflows/podcast-review-gate.yml",
            "approved_by": None,
            "approved_at": None,
            "audit_trail": [],
            "artifact": "review-checklist.md",
            "gate": {
                "status": "blocked",
                "approval_required_before": "non_dry_run_tts_synthesis",
                "checks": [
                    "script_accuracy",
                    "claim_verification",
                    "citation_link_integrity",
                    "transcript_readiness",
                    "tts_readiness",
                    "rights_attribution",
                ],
            },
        },
        "review_status": "pending",
        "review_required": True,
        "generation": {
            "engine": "local-deterministic-placeholder",
            "deterministic": True,
            "tts_provider": None,
            "tts_voice": None,
            "audio_placeholder": True,
            "tts_synthesis": {
                "status": "blocked",
                "allowed": False,
                "blocked_by": ["provider_not_selected"] if payload.get("dry_run") else ["human_review", "provider_not_selected"],
                "dry_run_bypass_allowed": bool(payload.get("dry_run")),
            },
            "duration_seconds": None,
        },
        "tts_provider": None,
        "tts_voice": None,
        "duration_seconds": None,
        "license": "pending-review",
        "publishing": {
            "mode": "manual",
            "packet_format": "squadscope-podcaster-packet-v1",
            "packet_ready": False,
            "eligible": False,
            "blocked_by": ["human_review", "real_tts_not_implemented"],
            "readiness_checks": {
                "cost_ledger_complete": bool(cost_ledger.get("readiness", {}).get("complete"))
                if isinstance(cost_ledger.get("readiness"), dict)
                else False,
                "budget_status": cost_ledger.get("budget", {}).get("status")
                if isinstance(cost_ledger.get("budget"), dict)
                else "unknown",
                "editorial_review_complete": False,
                "real_audio_available": False,
            },
            "public_url": None,
        },
        "artifact_access": artifact_access_metadata(job_id, created_ts, expires_at),
        "observability": {"correlation_id": job_id, "safe_log_fields": ["job_id", "week", "status", "artifact_count"]},
    }


def _claim_ledger(payload: dict[str, object]) -> str:
    return json.dumps(
        [
            {
                "claim_id": "stub_000",
                "script_excerpt": "[Script content placeholder — pending editorial generation from source article]",
                "source_url": payload["article_url"],
                "source_quote": None,
                "verified": False,
                "editor_notes": "Deterministic stub awaiting real article content. Claim ledger will be populated during editorial generation. Human review and verification required before publication.",
            }
        ],
        sort_keys=True,
        indent=2,
    ) + "\n"


def _review_checklist(job_id: str, payload: dict[str, object]) -> str:
    return "\n".join(
        [
            "# Podcaster human review checklist",
            "",
            f"- Job ID: `{job_id}`",
            f"- Week: `{payload['week']}`",
            f"- Source article: {payload['article_url']}",
            "- Review mechanism: GitHub Environment `podcast-review` via `.github/workflows/podcast-review-gate.yml`",
            "",
            "Reviewers must inspect `script.txt`, `claim-ledger.json`, `COST-LEDGER.json`, `transcript.txt`, `show-notes.md`, `MANIFEST.json`, and the publishing packet before approving.",
            "",
            "## Required checks",
            "",
            "- [ ] Script accuracy: every claim is represented in the claim ledger and unresolved editorial placeholders are rejected.",
            "- [ ] Claim verification: at least three major claims are spot-checked against the source article.",
            "- [ ] Citation/link integrity: show-note URLs resolve and point to the cited resources.",
            "- [ ] Transcript readiness: transcript metadata is complete and matches the script/audio plan.",
            "- [ ] TTS readiness: provider constraints and licensing are satisfied before non-dry-run synthesis.",
            "- [ ] Rights/attribution: source and future TTS provider attribution are documented.",
            "",
            "## Enforcement",
            "",
            "Non-dry-run TTS synthesis remains blocked until the review workflow records an approved decision with reviewer identity and timestamp.",
            "Dry-run and non-publishing validation may run without approval, but cannot publish generated audio.",
            "",
        ]
    )


def _operator_readme(metadata: dict[str, object]) -> str:
    week = metadata.get("week", "unknown")
    article_url = metadata.get("article_url", "")
    review_status = metadata.get("review_status", "unknown")

    return "\n".join(
        [
            "===========================================",
            "  PODCASTER PUBLISHING PACKET - OPERATOR GUIDE",
            "===========================================",
            "",
            f"Episode Week: {week}",
            f"Source Article: {article_url}",
            "",
            "PACKET STATUS:",
            f"  Current Review Status: {review_status}",
            "  ⚠️  REVIEW REQUIRED: This is a placeholder packet. Do not publish until:",
            "      1. Editorial review is complete",
            "      2. Audio synthesis is active (currently placeholder)",
            "      3. All metadata is verified",
            "",
            "CONTENTS:",
            "  • README.txt (this file)",
            "  • MANIFEST.json — Packet metadata and review tracking",
            "  • REVIEW-CHECKLIST.md — Required editorial approval checklist",
            "  • PUBLISHING-GUIDE.txt — Step-by-step publishing instructions",
            "  • script.txt — Episode script",
            "  • COST-LEDGER.json — Episode cost and monthly budget evidence",
            "  • transcript.txt — Full transcript",
            "  • show-notes.md — Markdown for podcast platform metadata",
            "  • audio/episode-{week}.mp3 — Audio file (currently placeholder)",
            "  • claim-ledger.json — Claim-to-source mapping",
            "  • RIGHTS-AND-ATTRIBUTION.txt — Licensing and attribution templates",
            "  • CHECKSUMS.txt — File integrity verification",
            "",
            "NEXT STEPS:",
            "  1. Open MANIFEST.json to verify episode metadata",
            "  2. Review script.txt and transcript.txt for accuracy",
            "  3. Check show-notes.md for podcast platform metadata",
            "  4. Confirm RIGHTS-AND-ATTRIBUTION.txt before distribution",
            "  5. When ready, follow PUBLISHING-GUIDE.txt for your platform",
            "",
            "SUPPORT:",
            "  See PUBLISHING-GUIDE.txt for platform-specific instructions.",
            "  Questions? Contact your distribution team.",
            "",
        ]
    )


def _publishing_guide() -> str:
    return "\n".join(
        [
            "===========================================",
            "  PUBLISHING GUIDE FOR PODCAST PLATFORMS",
            "===========================================",
            "",
            "This packet is ready for manual publishing once editorial review is complete.",
            "Choose your publishing platform below.",
            "",
            "---",
            "",
            "PUBLISHING TO SPOTIFY",
            "---",
            "Spotify for Creators does not accept direct API uploads.",
            "Instead, submit via RSS feed or Anchor (Spotify's hosting partner).",
            "",
            "Option A: Use Anchor (easiest for new podcasts)",
            "  1. Visit https://anchor.fm and sign in with your Spotify account",
            "  2. Create a new podcast if needed",
            "  3. Click 'Upload' and select the MP3 from audio/episode-{week}.mp3",
            "  4. Paste the show-notes.md content as episode description",
            "  5. Set publication date and click Publish",
            "  6. Spotify automatically indexes within 24–48 hours",
            "",
            "Option B: Submit existing RSS feed",
            "  1. If you have your own RSS feed, add this episode as a new <item>:",
            "     - Title: [from show-notes.md]",
            "     - Description: [from show-notes.md]",
            "     - Audio URL: [external link to your audio hosting]",
            "     - Publication date: [ISO 8601 format]",
            "  2. Submit feed URL to Spotify for Creators",
            "  3. Spotify validates and indexes within 24–48 hours",
            "",
            "---",
            "",
            "PUBLISHING TO APPLE PODCASTS",
            "---",
            "Apple Podcasts requires an RSS feed.",
            "",
            "  1. Ensure your podcast RSS feed includes this episode",
            "  2. Submit or update feed in Apple Podcasts Connect",
            "  3. Apple may require manual approval; allow 24–48 hours",
            "  4. Once approved, episode appears in Apple Podcasts apps",
            "",
            "---",
            "",
            "PUBLISHING TO GOOGLE PODCASTS",
            "---",
            "Google Podcasts uses Spotify/Apple feeds for indexing.",
            "",
            "  1. Publish to Spotify or Apple Podcasts first (see above)",
            "  2. Google Podcasts automatically indexes Spotify/Apple feeds",
            "  3. No separate submission required",
            "",
            "---",
            "",
            "PUBLISHING TO YOUR OWN RSS FEED",
            "---",
            "",
            "  1. Host the MP3 file on your web server or storage (e.g., AWS S3, Azure Blob)",
            "  2. Add this <item> to your RSS feed <channel>:",
            "",
            "    <item>",
            "      <title>[Episode Title from show-notes.md]</title>",
            "      <description>[Episode Description from show-notes.md]</description>",
            "      <link>[Article URL]</link>",
            "      <pubDate>[Publication Date in RFC 2822 format]</pubDate>",
            "      <enclosure url=\"[MP3 URL]\" type=\"audio/mpeg\" length=\"[file size in bytes]\"/>",
            "      <guid isPermaLink=\"true\">[Unique episode URL or identifier]</guid>",
            "    </item>",
            "",
            "  3. Publish your updated feed",
            "  4. Submit feed URL to Spotify, Apple Podcasts, and other platforms",
            "",
            "---",
            "",
            "VERIFICATION",
            "---",
            "",
            "After publishing, verify integrity:",
            "  • Open CHECKSUMS.txt and verify file hashes match your local files",
            "  • Spot-check 30 seconds of audio on the platform",
            "  • Confirm episode metadata (title, description, date) matches show-notes.md",
            "  • Verify attribution in show notes matches RIGHTS-AND-ATTRIBUTION.txt",
            "",
            "---",
            "",
            "ARCHIVAL",
            "---",
            "",
            "Store this packet locally for audit and rollback:",
            "  • Save the entire ZIP to a date-stamped folder",
            "  • Keep MANIFEST.json and CHECKSUMS.txt for verification",
            "  • If removal/correction is needed, reference the job_id in MANIFEST.json",
            "",
        ]
    )


def _rights_and_attribution() -> str:
    return "\n".join(
        [
            "===========================================",
            "  RIGHTS AND ATTRIBUTION",
            "===========================================",
            "",
            "This file documents the licensing and attribution requirements.",
            "Include the relevant sections in your episode description on each platform.",
            "",
            "---",
            "",
            "AUDIO GENERATION",
            "---",
            "",
            "⚠️  PLACEHOLDER AUDIO: This packet includes a placeholder audio file.",
            "   When audio synthesis is active, update this section with:",
            "   • TTS provider (e.g., Microsoft Azure Speech Services)",
            "   • Voice name and license",
            "   • Required attribution text",
            "",
            "Example (when TTS is active):",
            "  'Episode audio generated using Microsoft Azure Speech Services.",
            "   Voice: [Voice Name]. For details, see",
            "   https://learn.microsoft.com/en-us/azure/cognitive-services/speech-service/'",
            "",
            "---",
            "",
            "SOURCE ARTICLE ATTRIBUTION",
            "---",
            "",
            "Include in episode description:",
            "  • Source: [Article Title]",
            "  • URL: [Article URL from MANIFEST.json]",
            "  • License: [Article License if specified]",
            "",
            "Attribution template (copy-paste ready):",
            "  \"This episode is based on an article from SquadScope.",
            "   Read the full story: [Article URL]\"",
            "",
            "---",
            "",
            "SHOW NOTES AND LINKS",
            "---",
            "",
            "Show notes (show-notes.md) may include third-party links.",
            "Respect copyright and linking terms for all referenced sources.",
            "",
            "---",
            "",
            "DISTRIBUTION RESTRICTIONS",
            "---",
            "",
            "This packet is for manual publishing by authorized operators only.",
            "Do not:",
            "  • Distribute the packet to unauthorized parties",
            "  • Publish to platforms not explicitly approved",
            "  • Remove or modify attribution headers",
            "",
            "---",
            "",
            "QUESTIONS?",
            "---",
            "",
            "Contact your distribution team or legal review before publishing.",
            "",
        ]
    )


def _packet(
    *,
    script: str,
    transcript: str,
    show_notes: str,
    metadata: dict[str, object],
    claim_ledger: str,
    cost_ledger: str,
    review_checklist: str,
    rights: str,
    audio_placeholder: bytes,
) -> bytes:
    week = str(metadata["week"])
    readme = _operator_readme(metadata)
    files: dict[str, bytes] = {
        "README.txt": readme.encode("utf-8"),
        "MANIFEST.json": (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        "REVIEW-CHECKLIST.md": review_checklist.encode("utf-8"),
        "PUBLISHING-GUIDE.txt": _publishing_guide().encode("utf-8"),
        "script.txt": script.encode("utf-8"),
        "claim-ledger.json": claim_ledger.encode("utf-8"),
        "COST-LEDGER.json": cost_ledger.encode("utf-8"),
        "transcript.txt": transcript.encode("utf-8"),
        "show-notes.md": show_notes.encode("utf-8"),
        f"audio/episode-{week}.mp3": audio_placeholder,
        "RIGHTS-AND-ATTRIBUTION.txt": rights.encode("utf-8"),
    }
    checksums = "".join(f"{checksum(content)}  {name}\n" for name, content in sorted(files.items()))
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as packet:
        for name, content in sorted(files.items()):
            packet.writestr(_zip_info(name), content)
        packet.writestr(_zip_info("CHECKSUMS.txt"), checksums)
    return buffer.getvalue()


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=ZIP_TIMESTAMP)
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info
