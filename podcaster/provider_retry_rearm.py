"""Operator re-arm of a fenced provider retry after an authoritative absence proof.

A video distribution attempt durably claims ``upload_intent`` *before* it calls
the provider. When the attempt then dies without persisting an outcome, the
claim stays as a retry-blocked ``publication_unknown`` record so that no later
run can upload a second copy. That fence is correct, but it is permanent unless
an operator proves the provider holds no artifact for the publication identity.

This module is that supported operator path. It:

1. validates the durable evidence so that the *latest* record for the provider
   is exactly a retry-blocked claim without a provider ID, and that no record
   for the provider has ever carried a provider ID;
2. proves absence with an authenticated, fully paginated read of the channel's
   own uploads (private, unlisted, public, processing, failed and rejected
   videos) and fails closed on any error, partial page, unresolved video or
   matching candidate;
3. only with ``--apply`` appends exactly one ``retry_blocked=false``
   authorization record. The next ``claim_evidence`` consumes it exactly once.

Run in-boundary (the container image ships the ``podcaster`` package only)::

    python -m podcaster.provider_retry_rearm \\
        --job-id podcast-2026-W39-4c18d88190f2 --provider youtube \\
        --approved-by <github-actor> --reason "<why>"          # dry-run
    ... --apply                                                  # write

Exit codes: 0 success/no-op, 1 refused (fail closed), 2 argument/credential
error, 3 candidate found (adopt it instead of re-uploading).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from podcaster.publication_state import (
    PUBLICATION_UNKNOWN,
    REARMABLE_CLAIM_OPERATIONS,
    PublicationStateError,
    append_evidence,
    publication_identity,
    read_evidence,
)

logger = logging.getLogger(__name__)

REARM_OPERATION = "operator_retry_rearm"
REARM_CODE = "operator_absence_verified"
PROOF_VERSION = "youtube-uploads-absence-v1"

# provider flag -> (evidence platform, media kind, claim operation).
# Spotify video is intentionally absent: its readback proof is a separate,
# listing-shape dependent design and must be added explicitly.
SUPPORTED_PROVIDERS: dict[str, tuple[str, str, str]] = {
    "youtube": ("youtube", "video", "upload_intent"),
}

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_PAGE_SIZE = 50
_MAX_PAGES = 200
_WINDOW_SLACK = timedelta(minutes=30)
# Longer than the video job's replica timeout (5400s): the claim is written
# after the run starts, so no attempt that wrote it can still be uploading, and
# the uploads playlist has had ample time to reflect a finalized upload.
MIN_CLAIM_AGE = timedelta(hours=2)
_YOUTUBE_TITLE_LIMIT = 100
_MIN_PREFIX_MATCH = 20
_TERMINAL_VIDEO_STATUSES = ("failed", "partial", "completed", "skipped")
_WEEK_TOKEN_RE = re.compile(r"\bW(\d{2})\b", re.IGNORECASE)
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_CANDIDATE = 3


class RearmRefused(Exception):
    """Fail-closed refusal. Nothing was written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CandidateFound(RearmRefused):
    """The provider already holds a plausible artifact; adopt it, never re-upload."""

    def __init__(self, video_ids: list[str], reasons: dict[str, list[str]]) -> None:
        super().__init__(
            "candidate_found",
            "provider holds candidate artifact(s): " + ", ".join(video_ids),
        )
        self.video_ids = video_ids
        self.reasons = reasons


@dataclass(frozen=True)
class ExpectedArtifact:
    job_id: str
    week: str
    title: str
    intent_at: datetime | None


@dataclass
class AbsenceProof:
    proof_version: str
    checked_at: str
    uploads_scanned: int
    pages: int
    total_results: int
    statuses: dict[str, int] = field(default_factory=dict)

    def as_details(self) -> dict[str, Any]:
        return {
            "absence_proof_version": self.proof_version,
            "absence_checked_at": self.checked_at,
            "absence_uploads_scanned": self.uploads_scanned,
            "absence_pages": self.pages,
            "absence_total_results": self.total_results,
            "absence_candidates": 0,
        }


@dataclass
class RearmResult:
    status: str
    record_seq: int | None = None
    proof: AbsenceProof | None = None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_title(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _identity_matches(record: Mapping[str, Any], identity: Any) -> bool:
    return (
        record.get("job_id") == identity.accepted_job_id
        and record.get("week") == identity.week
        and record.get("publish_run_id") == identity.publish_run_id
        and record.get("article_sha256") == identity.article_sha256
        and record.get("manifest_sha256") == identity.manifest_sha256
    )


def check_preconditions(
    document: Mapping[str, Any] | None,
    identity: Any,
    *,
    platform: str,
    media_kind: str,
    claim_operation: str,
    manifest: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[str, Mapping[str, Any]]:
    """Return ``("rearm", claim)`` or ``("already_armed", record)``; else refuse."""
    if not isinstance(document, Mapping) or document.get("corrupt"):
        raise RearmRefused("evidence_unreadable", "publication evidence is missing or corrupt")
    records = document.get("records")
    if not isinstance(records, list):
        raise RearmRefused("evidence_unreadable", "publication evidence has no records list")
    scoped: list[Mapping[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise RearmRefused("evidence_malformed", "publication evidence has a malformed record")
        if record.get("platform") == platform and record.get("media_kind") == media_kind:
            scoped.append(record)
    if not scoped:
        raise RearmRefused("no_claim", f"no {platform}:{media_kind} evidence to re-arm")
    for record in scoped:
        if not _identity_matches(record, identity):
            raise RearmRefused(
                "identity_mismatch",
                f"{platform}:{media_kind} evidence belongs to a different publication identity",
            )
        if record.get("provider_id") or record.get("provider_artifact_id"):
            raise RearmRefused(
                "provider_id_recorded",
                f"{platform}:{media_kind} evidence records provider artifact "
                f"{record.get('provider_id') or record.get('provider_artifact_id')}; adopt it",
            )
    latest = scoped[-1]
    if latest.get("operation") == REARM_OPERATION and latest.get("retry_blocked") is False:
        return "already_armed", latest
    if (
        latest.get("operation") != claim_operation
        or claim_operation not in REARMABLE_CLAIM_OPERATIONS
        or latest.get("outcome") != PUBLICATION_UNKNOWN
        or latest.get("retry_blocked") is not True
        or latest.get("mutation_attempted") is not False
        or latest.get("code") != "mutation_intent"
    ):
        raise RearmRefused(
            "latest_not_blocked_claim",
            f"latest {platform}:{media_kind} record (seq={latest.get('seq')}, "
            f"operation={latest.get('operation')}, outcome={latest.get('outcome')}) "
            "is not a retry-blocked claim",
        )
    claim_seq = latest.get("seq")
    if not isinstance(claim_seq, int) or isinstance(claim_seq, bool):
        raise RearmRefused("evidence_malformed", "latest claim has no integer seq")
    claim_at = _parse_time(latest.get("at"))
    if claim_at is None:
        raise RearmRefused("claim_time_unknown", "claim timestamp is missing or malformed")
    current = now or datetime.now(timezone.utc)
    if current - claim_at < MIN_CLAIM_AGE:
        raise RearmRefused(
            "claim_too_recent",
            f"claim seq={latest.get('seq')} is younger than {MIN_CLAIM_AGE}; "
            "an attempt may still be running or not yet listed",
        )
    generation = manifest.get("generation") if isinstance(manifest, Mapping) else None
    runner = generation.get("video_runner") if isinstance(generation, Mapping) else None
    if not isinstance(runner, Mapping) or runner.get("status") not in _TERMINAL_VIDEO_STATUSES:
        raise RearmRefused(
            "video_run_not_terminal", "video runner state is missing or not terminal"
        )
    distribution = runner.get("distribution")
    if isinstance(distribution, Mapping) and distribution.get("youtube_id"):
        raise RearmRefused(
            "provider_id_recorded",
            f"manifest records YouTube video {distribution.get('youtube_id')}; adopt it",
        )
    video_publish = generation.get("video_publish") if isinstance(generation, Mapping) else None
    prior_publish = video_publish.get(platform) if isinstance(video_publish, Mapping) else None
    if isinstance(prior_publish, Mapping) and (
        prior_publish.get("video_id") or prior_publish.get("provider_id")
    ):
        raise RearmRefused(
            "provider_id_recorded", "manifest video_publish records a provider artifact; adopt it"
        )
    return "rearm", latest


class YouTubeAbsenceProver:
    """Authenticated, fail-closed scan of the channel's own uploads."""

    def __init__(self, transport: Any, access_token: str) -> None:
        self._transport = transport
        self._headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    def _get(self, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        url = f"{YOUTUBE_API}/{path}?{urlencode(params)}"
        try:
            status, body = self._transport.request(url, method="GET", headers=self._headers)
        except Exception as exc:  # noqa: BLE001 - any transport fault fails closed
            raise RearmRefused(
                "provider_readback_error", f"YouTube {path} request failed: {type(exc).__name__}"
            ) from None
        if status != 200:
            raise RearmRefused("provider_readback_error", f"YouTube {path} returned HTTP {status}")
        try:
            payload = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
        except (UnicodeDecodeError, ValueError):
            raise RearmRefused(
                "provider_readback_error", f"YouTube {path} returned invalid JSON"
            ) from None
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RearmRefused("provider_readback_incomplete", f"YouTube {path} page has no items")
        return payload

    def _uploads_playlist(self) -> str:
        payload = self._get("channels", {"part": "contentDetails", "mine": "true"})
        items = payload["items"]
        if len(items) != 1:
            raise RearmRefused(
                "provider_readback_incomplete",
                f"expected exactly one authenticated channel, got {len(items)}",
            )
        details = items[0].get("contentDetails") if isinstance(items[0], dict) else None
        related = details.get("relatedPlaylists") if isinstance(details, dict) else None
        uploads = related.get("uploads") if isinstance(related, dict) else None
        if not isinstance(uploads, str) or not uploads:
            raise RearmRefused("provider_readback_incomplete", "channel has no uploads playlist")
        return uploads

    def _list_upload_ids(self, playlist_id: str) -> tuple[list[str], int, int]:
        ids: list[str] = []
        page_token: str | None = None
        total_results: int | None = None
        pages = 0
        while True:
            pages += 1
            if pages > _MAX_PAGES:
                raise RearmRefused("provider_readback_incomplete", "uploads exceed page cap")
            params: dict[str, Any] = {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": _PAGE_SIZE,
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("playlistItems", params)
            info = payload.get("pageInfo")
            page_total = info.get("totalResults") if isinstance(info, dict) else None
            if not isinstance(page_total, int) or isinstance(page_total, bool) or page_total < 0:
                raise RearmRefused(
                    "provider_readback_incomplete", "uploads page lacks totalResults"
                )
            if total_results is None:
                total_results = page_total
            elif page_total != total_results:
                raise RearmRefused("provider_readback_incomplete", "uploads changed during scan")
            for item in payload["items"]:
                details = item.get("contentDetails") if isinstance(item, dict) else None
                video_id = details.get("videoId") if isinstance(details, dict) else None
                if not isinstance(video_id, str) or not _VIDEO_ID_RE.fullmatch(video_id):
                    raise RearmRefused("provider_readback_incomplete", "upload item lacks videoId")
                ids.append(video_id)
            next_token = payload.get("nextPageToken")
            if next_token is None:
                break
            if not isinstance(next_token, str) or not next_token or next_token == page_token:
                raise RearmRefused("provider_readback_incomplete", "invalid uploads page token")
            if not payload["items"]:
                raise RearmRefused("provider_readback_incomplete", "empty page with next token")
            page_token = next_token
        assert total_results is not None
        if len(ids) != total_results or len(set(ids)) != len(ids):
            raise RearmRefused(
                "provider_readback_incomplete",
                f"scanned {len(ids)} unique={len(set(ids))} uploads but totalResults="
                f"{total_results}",
            )
        return ids, pages, total_results

    def _videos(self, ids: list[str]) -> list[dict[str, Any]]:
        videos: list[dict[str, Any]] = []
        for start in range(0, len(ids), _PAGE_SIZE):
            batch = ids[start : start + _PAGE_SIZE]
            payload = self._get(
                "videos",
                {
                    "part": "snippet,status,processingDetails",
                    "id": ",".join(batch),
                    "maxResults": _PAGE_SIZE,
                },
            )
            returned = {
                item.get("id"): item
                for item in payload["items"]
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            missing = [video_id for video_id in batch if video_id not in returned]
            if missing:
                raise RearmRefused(
                    "provider_readback_incomplete",
                    "videos.list did not resolve uploads: " + ", ".join(missing),
                )
            videos.extend(returned[video_id] for video_id in batch)
        return videos

    def prove_absent(self, expected: ExpectedArtifact) -> AbsenceProof:
        uploads = self._uploads_playlist()
        ids, pages, total = self._list_upload_ids(uploads)
        videos = self._videos(ids)
        statuses: dict[str, int] = {}
        reasons: dict[str, list[str]] = {}
        for video in videos:
            status = video.get("status") if isinstance(video.get("status"), dict) else {}
            key = f"{status.get('privacyStatus', '?')}/{status.get('uploadStatus', '?')}"
            statuses[key] = statuses.get(key, 0) + 1
            matched = match_reasons(video, expected)
            if matched:
                reasons[str(video["id"])] = matched
        if reasons:
            raise CandidateFound(sorted(reasons), reasons)
        return AbsenceProof(
            proof_version=PROOF_VERSION,
            checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            uploads_scanned=len(videos),
            pages=pages,
            total_results=total,
            statuses=statuses,
        )


def match_reasons(video: Mapping[str, Any], expected: ExpectedArtifact) -> list[str]:
    """Conservative candidate rules; any hit means "do not re-upload"."""
    snippet = video.get("snippet") if isinstance(video.get("snippet"), dict) else {}
    title = str(snippet.get("title") or "")
    description = str(snippet.get("description") or "")
    reasons: list[str] = []
    actual_title = _normalize_title(title)
    expected_title = _normalize_title(expected.title)
    uploaded_title = _normalize_title(expected.title[:_YOUTUBE_TITLE_LIMIT])
    if expected_title and actual_title in (expected_title, uploaded_title):
        reasons.append("title")
    elif (
        expected_title
        and min(len(actual_title), len(expected_title)) >= _MIN_PREFIX_MATCH
        and (expected_title.startswith(actual_title) or actual_title.startswith(expected_title))
    ):
        reasons.append("title_prefix")
    if expected.job_id and (expected.job_id in title or expected.job_id in description):
        reasons.append("job_id")
    year, _, week_part = expected.week.partition("-W")
    week_tokens = {match.group(1) for match in _WEEK_TOKEN_RE.finditer(title)}
    published_at = _parse_time(snippet.get("publishedAt"))
    try:
        week_start = datetime.combine(
            date.fromisocalendar(int(year), int(week_part), 1), datetime.min.time(), timezone.utc
        )
    except ValueError:
        week_start = None
    if week_part in week_tokens and (
        week_start is None
        or published_at is None
        or published_at >= week_start - timedelta(days=180)
    ):
        reasons.append("week")
    if published_at is None:
        reasons.append("no_published_at")
    elif expected.intent_at is None or published_at >= expected.intent_at - _WINDOW_SLACK:
        reasons.append("uploaded_after_intent")
    return reasons


def expected_artifact(
    manifest: Mapping[str, Any], job_id: str, claim: Mapping[str, Any]
) -> ExpectedArtifact:
    from podcaster.config import PodcastConfig
    from podcaster.video.job_runner import _resolve_video_title

    request = manifest.get("request")
    request = request if isinstance(request, dict) else {}
    brand_name = PodcastConfig.from_payload(request).name
    title, _ = _resolve_video_title(request, brand_name=brand_name, job_id=job_id)
    return ExpectedArtifact(
        job_id=job_id,
        week=str(request.get("week") or ""),
        title=title,
        intent_at=_parse_time(claim.get("at")),
    )


def rearm_provider_retry(
    storage: Any,
    *,
    job_id: str,
    provider: str,
    approved_by: str,
    reason: str,
    apply: bool,
    prover_factory: Callable[[], YouTubeAbsenceProver],
) -> RearmResult:
    if provider not in SUPPORTED_PROVIDERS:
        raise RearmRefused("unsupported_provider", f"provider {provider!r} is not supported")
    platform, media_kind, claim_operation = SUPPORTED_PROVIDERS[provider]
    if not approved_by.strip() or not reason.strip():
        raise RearmRefused("usage", "--approved-by and --reason are required")
    from podcaster.video.job_runner import manifest_path

    raw = storage.get_bytes(manifest_path(job_id))
    if raw is None:
        raise RearmRefused("manifest_missing", f"no manifest for job_id={job_id}")
    try:
        manifest = json.loads(raw.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise RearmRefused("identity_invalid", "manifest is not a JSON object")
        identity = publication_identity(manifest, job_id, "")
    except (UnicodeDecodeError, ValueError, PublicationStateError) as exc:
        raise RearmRefused("identity_invalid", f"publication identity invalid: {exc}") from None

    document = read_evidence(storage, job_id)
    state, record = check_preconditions(
        document,
        identity,
        platform=platform,
        media_kind=media_kind,
        claim_operation=claim_operation,
        manifest=manifest,
    )
    if state == "already_armed":
        return RearmResult(status="already_armed", record_seq=record.get("seq"))

    proof = prover_factory().prove_absent(expected_artifact(manifest, job_id, record))
    if not apply:
        return RearmResult(status="dry_run_absent", proof=proof)

    claim_seq = record.get("seq")
    details: dict[str, Any] = {
        "approved_by": approved_by.strip()[:128],
        "reason": reason.strip()[:256],
        "rearms_claim_seq": claim_seq,
        "rearmed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **proof.as_details(),
    }
    written = append_evidence(
        storage,
        identity,
        platform=platform,
        media_kind=media_kind,
        operation=REARM_OPERATION,
        outcome=PUBLICATION_UNKNOWN,
        mutation_attempted=False,
        verification="provider_readback",
        evidence_source="operator_absence_proof",
        retry_blocked=False,
        code=REARM_CODE,
        details=details,
        expected_latest_seq=claim_seq,
    )
    if written is None:
        return RearmResult(status="already_armed", proof=proof)
    return RearmResult(status="rearmed", record_seq=written.seq, proof=proof)


def _youtube_prover_factory() -> YouTubeAbsenceProver:
    from podcaster.video.distribution import (
        VideoDistributionConfig,
        _DefaultTransport,
        _get_youtube_access_token,
    )

    config = VideoDistributionConfig.from_env()
    if not (
        config.youtube_client_id and config.youtube_client_secret and config.youtube_refresh_token
    ):
        raise RearmRefused("credentials_missing", "YouTube OAuth credentials are not configured")
    transport = _DefaultTransport()
    try:
        token = _get_youtube_access_token(config, transport)
    except Exception as exc:  # noqa: BLE001 - never surface token material
        raise RearmRefused(
            "credentials_error", f"YouTube token refresh failed: {type(exc).__name__}"
        ) from None
    return YouTubeAbsenceProver(transport, token)


def main(
    argv: list[str] | None = None,
    *,
    storage: Any = None,
    prover_factory: Callable[[], YouTubeAbsenceProver] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Re-arm a fenced provider retry after an authoritative absence proof.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--provider", required=True, choices=sorted(SUPPORTED_PROVIDERS))
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true", help="Write the re-arm record.")
    args = parser.parse_args(argv)

    if storage is None:
        from podcaster.storage import create_storage_backend

        storage = create_storage_backend()
    try:
        result = rearm_provider_retry(
            storage,
            job_id=args.job_id,
            provider=args.provider,
            approved_by=args.approved_by,
            reason=args.reason,
            apply=args.apply,
            prover_factory=prover_factory or _youtube_prover_factory,
        )
    except CandidateFound as exc:
        print(f"REFUSED candidate_found: {exc}; reasons={json.dumps(exc.reasons, sort_keys=True)}")
        print("Nothing written. Adopt the existing video instead of re-uploading.")
        return EXIT_CANDIDATE
    except RearmRefused as exc:
        print(f"REFUSED {exc.code}: {exc}")
        print("Nothing written.")
        return (
            EXIT_USAGE
            if exc.code in ("usage", "credentials_missing", "credentials_error")
            else EXIT_REFUSED
        )
    except PublicationStateError as exc:
        print(f"REFUSED evidence_changed: {exc}")
        print("Nothing written.")
        return EXIT_REFUSED

    summary: dict[str, Any] = {"status": result.status, "job_id": args.job_id}
    if result.record_seq is not None:
        summary["record_seq"] = result.record_seq
    if result.proof is not None:
        summary["proof"] = {**result.proof.as_details(), "statuses": result.proof.statuses}
    print(json.dumps(summary, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
