from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from podcaster.artifact_access import artifact_access_metadata
from podcaster.audio import placeholder_audio_validation
from podcaster.costs import build_cost_ledger
from podcaster.sanitization import FIELD_LIMITS, sanitize_source_artifact


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
    audio_validation = placeholder_audio_validation(byte_length=len(audio_placeholder), sha256=checksum(audio_placeholder)).to_manifest()
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
    metadata = _metadata(job_id, payload, created_at, expires_at, cost_ledger, audio_validation)
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
            "Safety: source artifact text is untrusted data, fenced, and never executed as instructions.",
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
    sanitized = sanitize_source_artifact(item)
    parts = [sanitized.reference]
    if sanitized.role:
        parts.insert(0, f"{sanitized.role}:")
    if sanitized.sha256:
        parts.append(f"sha256={sanitized.sha256}")
    if sanitized.flags:
        parts.append(f"[untrusted-content-flagged: {', '.join(sanitized.flags)}; not executed]")
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
            "Publication is blocked for this placeholder packet.",
            "Verify real audio, source material rights, TTS provider rights, and human editorial approval before distribution.",
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
    audio_validation: dict[str, object],
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
            "audio_validation": audio_validation,
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
            "blocked_by": ["human_review", "real_tts_not_implemented", "audio_validation_not_passed"],
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
        "safety": _safety_summary(payload),
        "observability": {"correlation_id": job_id, "safe_log_fields": ["job_id", "week", "status", "artifact_count"]},
    }


def _safety_summary(payload: dict[str, object]) -> dict[str, object]:
    source_artifacts = payload.get("source_artifacts") or []
    detected = sorted({flag for item in source_artifacts for flag in sanitize_source_artifact(item).flags})
    return {
        "schema_version": "squadscope-podcaster-safety-v1",
        "untrusted_inputs_fenced": True,
        "fenced_fields": [
            "source_artifacts.role",
            "source_artifacts.reference",
            "source_artifacts.name",
            "source_artifacts.sha256",
        ],
        "field_allowlist": ["role", "url", "href", "uri", "path", "name", "sha256"],
        "field_length_caps": dict(FIELD_LIMITS),
        "injection_markers_detected": detected,
        "obeys_external_instructions": False,
        "human_review_required": True,
        "content_scanner": {
            "status": "not_yet_integrated",
            "decision_record": "docs/prompt-injection-audit.md",
            "candidates": ["azure_prompt_shields", "llm_guard"],
            "required_before": "llm_or_tts_generation_from_untrusted_text",
        },
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
            "  • PUBLISHING-GUIDE.txt — Publication blocker checklist for placeholder output",
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
            "  5. Keep this packet out of publication until PUBLISHING-GUIDE.txt lists every blocker as resolved",
            "",
            "SUPPORT:",
            "  See PUBLISHING-GUIDE.txt for the publication blocker checklist.",
            "  Questions? Contact your distribution team.",
            "",
        ]
    )


def _publishing_guide() -> str:
    return "\n".join(
        [
            "===========================================",
            "  PUBLICATION BLOCKED - PLACEHOLDER PACKET",
            "===========================================",
            "",
            "This generated packet contains deterministic placeholder audio and is not publishable.",
            "Do not upload the MP3, submit an RSS item, expose artifact URLs, or publish to any podcast platform.",
            "",
            "---",
            "",
            "REQUIRED BLOCKERS",
            "---",
            "Publication remains blocked until all of these are true:",
            "  [ ] Editorial review is approved with reviewer identity and timestamp",
            "  [ ] Claim ledger verifies every factual claim against the source article",
            "  [ ] Real TTS audio is generated with the selected provider and voice",
            "  [ ] TTS provider rights, attribution, privacy, and retention terms are confirmed",
            "  [ ] Audio validation passes for codec, duration, loudness, and integrity",
            "  [ ] Show notes, transcript, rights text, and manifest are final",
            "  [ ] Operator approval explicitly authorizes manual publication",
            "",
            "---",
            "",
            "CURRENT PLACEHOLDER STATE",
            "---",
            "The packet manifest should show:",
            "  • publishing.eligible = false",
            "  • publishing.packet_ready = false",
            "  • generation.audio_placeholder = true",
            "  • generation.tts_synthesis.allowed = false",
            "",
            "---",
            "",
            "OPERATOR ACTION",
            "---",
            "Use this packet only for internal review and regression evidence.",
            "If publication is requested, stop and create a reviewed production packet after the blockers above are resolved.",
            "",
            "---",
            "",
            "VERIFICATION BEFORE ANY FUTURE PUBLICATION GUIDE",
            "---",
            "",
            "Before replacing this blocker checklist with platform instructions, verify:",
            "  • CHECKSUMS.txt matches the final packet files",
            "  • MANIFEST.json records approval, provider, voice, rights, and audit trail",
            "  • The audio file is real synthesized output, not placeholder bytes",
            "  • Artifact URLs are intentionally accessible under the approved access model",
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
