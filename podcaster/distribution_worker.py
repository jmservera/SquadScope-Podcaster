"""One-item provider distribution worker backed by the durable outbox."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request

from podcaster.distribution_outbox import (
    DistributionOutboxRepository,
    aggregate_exit_code,
    exact_verification_proof,
    provider_approval_is_valid,
    utc_now,
    verify_artifact,
)
from podcaster.distribution_telemetry import signal_rows
from podcaster.publish import (
    promote_spotify_video_draft,
    read_spotify_video_publication_state,
    upload_video_to_episode,
)
from podcaster.queue import (
    QueueBackend,
    QueueMessage,
    create_distribution_queue_backend,
    parse_distribution_outbox_id,
)
from podcaster.ssrf import safe_urlopen
from podcaster.storage import StorageBackend, create_storage_backend
from podcaster.video.distribution import (
    VideoDistributionConfig,
    YouTubeDeliveryError,
    _escape_xml,
    _get_youtube_access_token,
    upload_to_youtube,
)
from podcaster.video.youtube_playlist import (
    add_video_to_playlist,
    playlist_contains_video,
    resolve_playlist_id,
)
from podcaster.video.youtube_publish import (
    PRIVACY_PUBLIC,
    get_video_snippet,
    publish_video,
)

logger = logging.getLogger(__name__)

MAX_DEQUEUE_COUNT = 5
_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


def _manifest_metadata(
    storage: StorageBackend,
    job_id: str,
    *,
    outbox_id: str | None = None,
) -> tuple[str, str]:
    raw = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    if raw is None:
        return "SquadScope Podcast", ""
    import json

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "SquadScope Podcast", ""
    request = manifest.get("request")
    if not isinstance(request, Mapping):
        return "SquadScope Podcast", ""
    title = str(request.get("article_title") or "SquadScope Podcast")[:100]
    description = str(request.get("article_summary") or "")[:5000]
    if outbox_id:
        marker = f"distribution-{outbox_id}"
        description = f"{description[: 5000 - len(marker) - 2]}\n\n{marker}".strip()
    return title, description


def _provider_item_id(leg: Mapping[str, Any]) -> str | None:
    verification = leg.get("verification")
    if isinstance(verification, Mapping) and verification.get("provider_item_id"):
        return str(verification["provider_item_id"])
    receipts = leg.get("receipts")
    if isinstance(receipts, list):
        for receipt in reversed(receipts):
            if isinstance(receipt, Mapping) and receipt.get("provider_item_id"):
                return str(receipt["provider_item_id"])
    return None


def _accepted_receipt_for_operation(leg: Mapping[str, Any], operation: str) -> bool:
    intent = leg.get("intent")
    receipts = leg.get("receipts")
    return bool(
        isinstance(intent, Mapping)
        and intent.get("operation") == operation
        and intent.get("consumed_at")
        and isinstance(receipts, list)
        and any(
            isinstance(receipt, Mapping)
            and receipt.get("intent_id") == intent.get("intent_id")
            and receipt.get("transport_class") == "accepted"
            and receipt.get("ambiguous") is not True
            for receipt in receipts
        )
    )


def _youtube_reconcile_create(
    access_token: str,
    outbox_id: str,
    *,
    transport: object,
) -> tuple[str, str | None]:
    marker = f"distribution-{outbox_id}"
    params = urlencode(
        {
            "part": "snippet",
            "forMine": "true",
            "type": "video",
            "maxResults": "5",
            "q": marker,
        }
    )
    try:
        status, body = transport.request(
            f"{_YOUTUBE_SEARCH_URL}?{params}",
            method="GET",
            headers={"Authorization": "******"},
        )
    except Exception:
        return "unknown", None
    if status != 200:
        return "unknown", None
    try:
        payload = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
        items = payload.get("items")
        if not isinstance(items, list):
            return "unknown", None
        matches = []
        for item in items:
            if not isinstance(item, Mapping):
                return "unknown", None
            identifier = item.get("id")
            video_id = identifier.get("videoId") if isinstance(identifier, Mapping) else None
            snippet = item.get("snippet")
            description = snippet.get("description") if isinstance(snippet, Mapping) else None
            if isinstance(video_id, str) and isinstance(description, str) and marker in description:
                matches.append(video_id)
    except (AttributeError, UnicodeDecodeError, ValueError):
        return "unknown", None
    unique = sorted(set(matches))
    if len(unique) > 1:
        return "conflict", None
    if unique:
        return "found", unique[0]
    return "absent", None


def _youtube_playlist_ready(
    repository: DistributionOutboxRepository,
    claim,
    document: Mapping[str, Any],
    *,
    video_id: str,
    access_token: str,
    config: VideoDistributionConfig,
    transport: object,
) -> bool:
    leg = document["providers"]["youtube"]
    context = leg.get("context")
    locale = str(context.get("locale") or "en") if isinstance(context, Mapping) else "en"
    context_playlist = str(context.get("playlist_id") or "") if isinstance(context, Mapping) else ""
    playlist_id = context_playlist or resolve_playlist_id(config, locale)
    if not playlist_id:
        return True
    try:
        present = playlist_contains_video(
            playlist_id,
            video_id,
            access_token,
            transport=transport,
            raise_on_error=True,
        )
    except RuntimeError:
        repository.record_verification(
            claim,
            provider="youtube",
            result="pending_provider",
            source="youtube_playlist_readback",
            provider_item_id=video_id,
            native_state="playlist_unknown",
            next_reconcile_at=utc_now() + timedelta(minutes=5),
        )
        repository.schedule_reconciliation(
            claim,
            provider="youtube",
            due_at=utc_now() + timedelta(minutes=5),
        )
        return False
    if present:
        return True
    if not provider_approval_is_valid(document, "youtube"):
        repository.record_verification(
            claim,
            provider="youtube",
            result="manual_handoff_required",
            source="youtube_human_approval_required",
            provider_item_id=video_id,
            native_state="draft",
            exhaustion_reason="playlist_and_public_promotion_require_human_approval",
        )
        return False
    if _accepted_receipt_for_operation(leg, "playlist_insert"):
        repository.record_verification(
            claim,
            provider="youtube",
            result="publication_unknown",
            source="youtube_playlist_readback",
            provider_item_id=video_id,
            native_state="playlist_absent_after_accepted_insert",
            exhaustion_reason="accepted_playlist_insert_not_externally_visible",
        )
        return False
    if claim.read_only:
        repository.record_verification(
            claim,
            provider="youtube",
            result="publication_unknown",
            source="youtube_playlist_readback",
            provider_item_id=video_id,
            native_state="playlist_absent",
            exhaustion_reason="consumed_playlist_intent_not_verified",
        )
        return False
    repository.persist_intent(
        claim,
        provider="youtube",
        operation="playlist_insert",
        expected_provider_item_id=video_id,
        precondition_fingerprint=hashlib.sha256(playlist_id.encode()).hexdigest(),
    )
    repository.consume_intent(
        claim,
        provider="youtube",
        provider_timeout_seconds=30,
        receipt_margin_seconds=30,
    )
    result = add_video_to_playlist(
        playlist_id,
        video_id,
        access_token,
        transport=transport,
    )
    repository.record_receipt(
        claim,
        provider="youtube",
        transport_class="accepted" if result.succeeded else "ambiguous",
        provider_item_id=video_id,
        native_state="playlist_inserted" if result.succeeded else "playlist_unknown",
        ambiguous=not result.succeeded,
        code=None if result.succeeded else "youtube_playlist_insert_unconfirmed",
    )
    try:
        verified = playlist_contains_video(
            playlist_id,
            video_id,
            access_token,
            transport=transport,
            raise_on_error=True,
        )
    except RuntimeError:
        verified = False
    if not verified:
        repository.record_verification(
            claim,
            provider="youtube",
            result="publication_unknown",
            source="youtube_playlist_readback",
            provider_item_id=video_id,
            native_state="playlist_unverified",
            exhaustion_reason="playlist_insert_not_externally_verified",
        )
        return False
    return True


def _process_youtube(
    repository: DistributionOutboxRepository,
    claim,
    storage: StorageBackend,
    document: Mapping[str, Any],
    local_artifact: Path,
    config: VideoDistributionConfig,
) -> None:
    leg = document["providers"]["youtube"]
    video_id = _provider_item_id(leg)
    transport = _DefaultTransportProxy()
    access_token = _get_youtube_access_token(config, transport)
    title, description = _manifest_metadata(
        storage,
        str(document["publication_identity"]["accepted_job_id"]),
        outbox_id=str(document["outbox_id"]),
    )

    if video_id is None:
        reconcile_result, reconciled_id = _youtube_reconcile_create(
            access_token,
            str(document["outbox_id"]),
            transport=transport,
        )
        if reconcile_result == "conflict":
            repository.record_verification(
                claim,
                provider="youtube",
                result="identity_conflict",
                source="youtube_create_reconcile",
                exhaustion_reason="multiple_videos_match_distribution_identity",
            )
            return
        if reconcile_result == "unknown":
            repository.record_verification(
                claim,
                provider="youtube",
                result="publication_unknown",
                source="youtube_create_reconcile",
                exhaustion_reason="create_reconcile_incomplete",
            )
            return
        if reconciled_id is not None:
            video_id = reconciled_id
            repository.record_verification(
                claim,
                provider="youtube",
                result="pending_provider",
                source="youtube_create_reconcile",
                provider_item_id=video_id,
                native_state="reconciled",
            )
        if video_id is None and claim.read_only:
            repository.record_verification(
                claim,
                provider="youtube",
                result="publication_unknown",
                source="youtube_identity_unprovable",
                exhaustion_reason="consumed_upload_intent_has_no_provider_identity",
            )
            return
        if video_id is None:
            repository.persist_intent(claim, provider="youtube", operation="draft_upload")
            repository.consume_intent(
                claim,
                provider="youtube",
                provider_timeout_seconds=120,
                receipt_margin_seconds=30,
            )
            try:
                video_id, _url = upload_to_youtube(
                    local_artifact,
                    title,
                    description,
                    config,
                    transport=transport,
                    raise_on_failure=True,
                )
            except YouTubeDeliveryError as exc:
                repository.record_receipt(
                    claim,
                    provider="youtube",
                    transport_class="ambiguous" if exc.mutation_ambiguous else "failed",
                    ambiguous=exc.mutation_ambiguous,
                    code=exc.code,
                )
                repository.record_verification(
                    claim,
                    provider="youtube",
                    result="publication_unknown" if exc.mutation_ambiguous else "failed_terminal",
                    source="youtube_upload",
                )
                return
            if not video_id:
                repository.record_receipt(
                    claim,
                    provider="youtube",
                    transport_class="failed",
                    code="youtube_upload_missing_id",
                )
                repository.record_verification(
                    claim,
                    provider="youtube",
                    result="publication_unknown",
                    source="youtube_upload",
                )
                return
            repository.record_receipt(
                claim,
                provider="youtube",
                transport_class="accepted",
                provider_item_id=video_id,
                native_state=config.youtube_privacy,
            )

    state = get_video_snippet(video_id, access_token)
    if state is None:
        repository.record_verification(
            claim,
            provider="youtube",
            result="pending_provider",
            source="youtube_videos_list",
            provider_item_id=video_id,
            next_reconcile_at=utc_now() + timedelta(minutes=5),
        )
        repository.schedule_reconciliation(
            claim,
            provider="youtube",
            due_at=utc_now() + timedelta(minutes=5),
        )
        return
    upload_status = str(state.get("uploadStatus") or "").lower()
    processing_status = str(state.get("processingStatus") or "").lower()
    privacy = str(state.get("privacyStatus") or "").lower()
    playlist_ready = _youtube_playlist_ready(
        repository,
        claim,
        document,
        video_id=video_id,
        access_token=access_token,
        config=config,
        transport=transport,
    )
    if not playlist_ready:
        return
    if privacy == PRIVACY_PUBLIC and processing_status == "succeeded":
        repository.record_verification(
            claim,
            provider="youtube",
            result="externally_verified_public",
            source="youtube_videos_list",
            provider_item_id=video_id,
            native_state=privacy,
            proof=exact_verification_proof(document, provider_item_id=video_id),
        )
        return
    if upload_status not in ("uploaded", "processed") or processing_status not in (
        "succeeded",
        "failed",
    ):
        due = utc_now() + timedelta(minutes=5)
        repository.record_verification(
            claim,
            provider="youtube",
            result="pending_provider",
            source="youtube_processing_readback",
            provider_item_id=video_id,
            native_state=privacy or processing_status,
            next_reconcile_at=due,
        )
        repository.schedule_reconciliation(claim, provider="youtube", due_at=due)
        return
    if processing_status == "failed":
        repository.record_verification(
            claim,
            provider="youtube",
            result="failed_terminal",
            source="youtube_processing_readback",
            provider_item_id=video_id,
            native_state=processing_status,
        )
        return

    if not provider_approval_is_valid(document, "youtube"):
        repository.record_verification(
            claim,
            provider="youtube",
            result="manual_handoff_required",
            source="youtube_human_approval_required",
            provider_item_id=video_id,
            native_state=privacy or processing_status,
            exhaustion_reason="public_promotion_requires_human_approval",
        )
        return

    if claim.read_only:
        repository.record_verification(
            claim,
            provider="youtube",
            result="publication_unknown",
            source="youtube_promotion_identity_readback",
            provider_item_id=video_id,
            native_state=privacy or processing_status,
            exhaustion_reason="consumed_promotion_intent_not_public",
        )
        return

    repository.persist_intent(
        claim,
        provider="youtube",
        operation="public_promotion",
        expected_provider_item_id=video_id,
    )
    repository.consume_intent(
        claim,
        provider="youtube",
        provider_timeout_seconds=30,
        receipt_margin_seconds=30,
    )
    result = publish_video(
        video_id,
        access_token,
        privacy_status=PRIVACY_PUBLIC,
        transport=transport,
    )
    repository.record_receipt(
        claim,
        provider="youtube",
        transport_class="accepted" if result.succeeded else "ambiguous",
        provider_item_id=video_id,
        native_state=result.privacy_status,
        ambiguous=not result.succeeded,
        code="youtube_promotion_unconfirmed" if result.error else None,
    )
    repository.record_verification(
        claim,
        provider="youtube",
        result="externally_verified_public" if result.succeeded else "publication_unknown",
        source="youtube_privacy_readback",
        provider_item_id=video_id,
        native_state=result.privacy_status,
        proof=(
            exact_verification_proof(document, provider_item_id=video_id)
            if result.succeeded
            else None
        ),
    )


def _process_spotify(
    repository,
    claim,
    document: Mapping[str, Any],
    local_artifact: Path,
    storage: StorageBackend,
    config: VideoDistributionConfig,
) -> None:
    leg = document["providers"]["spotify"]
    if leg.get("result") == "externally_verified_public":
        return
    provider_id = _provider_item_id(leg)
    if provider_id is not None:
        try:
            numeric_provider_id = int(provider_id)
        except ValueError:
            repository.record_verification(
                claim,
                provider="spotify",
                result="identity_conflict",
                source="spotify_episode_readback",
                provider_item_id=provider_id,
                exhaustion_reason="spotify_provider_identity_is_not_numeric",
            )
            return
        published = read_spotify_video_publication_state(numeric_provider_id)
        if published is True:
            repository.record_verification(
                claim,
                provider="spotify",
                result="externally_verified_public",
                source="spotify_episode_readback",
                provider_item_id=provider_id,
                native_state="published",
                proof=exact_verification_proof(document, provider_item_id=provider_id),
            )
            return
        if published is None:
            repository.record_verification(
                claim,
                provider="spotify",
                result="publication_unknown",
                source="spotify_episode_readback",
                provider_item_id=provider_id,
            )
            return
    title, description = _manifest_metadata(
        storage,
        str(document["publication_identity"]["accepted_job_id"]),
    )
    context = leg.get("context")
    audio_anchor_id = context.get("audio_anchor_id") if isinstance(context, Mapping) else None
    season_number = context.get("season_number") if isinstance(context, Mapping) else None
    episode_number = context.get("episode_number") if isinstance(context, Mapping) else None
    if provider_id is None:
        if claim.read_only:
            repository.record_verification(
                claim,
                provider="spotify",
                result="publication_unknown",
                source="spotify_identity_unprovable",
                exhaustion_reason="consumed_upload_intent_has_no_provider_identity",
            )
            return
        repository.persist_intent(
            claim,
            provider="spotify",
            operation="create_episode_intent",
        )
        repository.consume_intent(
            claim,
            provider="spotify",
            provider_timeout_seconds=300,
            receipt_margin_seconds=30,
        )
        upload = upload_video_to_episode(
            local_artifact,
            audio_anchor_id,
            title=title,
            description=description,
            season_number=season_number,
            episode_number=episode_number,
        )
        if upload.status == "failed" or upload.anchor_episode_id is None:
            repository.record_receipt(
                claim,
                provider="spotify",
                transport_class="ambiguous",
                ambiguous=True,
                code="spotify_upload_unconfirmed",
            )
            repository.record_verification(
                claim,
                provider="spotify",
                result="publication_unknown",
                source="spotify_upload_reconcile",
            )
            return
        provider_id = str(upload.anchor_episode_id)
        repository.record_receipt(
            claim,
            provider="spotify",
            transport_class="accepted",
            provider_item_id=provider_id,
            native_state="draft",
        )
    published = read_spotify_video_publication_state(int(provider_id))
    if published is True:
        repository.record_verification(
            claim,
            provider="spotify",
            result="externally_verified_public",
            source="spotify_episode_readback",
            provider_item_id=provider_id,
            native_state="published",
            proof=exact_verification_proof(document, provider_item_id=provider_id),
        )
        return
    if published is None:
        repository.record_verification(
            claim,
            provider="spotify",
            result="publication_unknown",
            source="spotify_episode_readback",
            provider_item_id=provider_id,
        )
        return
    if (
        not provider_approval_is_valid(document, "spotify")
        or config.spotify_video_publish_mode != "live"
    ):
        repository.record_verification(
            claim,
            provider="spotify",
            result="manual_handoff_required",
            source="spotify_human_approval_required",
            provider_item_id=provider_id,
            native_state="draft",
            exhaustion_reason="public_promotion_requires_approval_and_live_mode",
        )
        return
    if claim.read_only:
        repository.record_verification(
            claim,
            provider="spotify",
            result="publication_unknown",
            source="spotify_promotion_readback",
            provider_item_id=provider_id,
            native_state="draft",
            exhaustion_reason="consumed_promotion_intent_not_public",
        )
        return
    repository.persist_intent(
        claim,
        provider="spotify",
        operation="public_promotion",
        expected_provider_item_id=provider_id,
    )
    repository.consume_intent(
        claim,
        provider="spotify",
        provider_timeout_seconds=30,
        receipt_margin_seconds=30,
    )
    promoted = promote_spotify_video_draft(
        int(provider_id),
        audio_anchor_id=audio_anchor_id,
        spotify_video_publish_mode="live",
        job_id=str(document["publication_identity"]["accepted_job_id"]),
        run_id=str(document["publication_identity"]["publish_run_id"]),
    )
    confirmed = read_spotify_video_publication_state(int(provider_id))
    accepted = promoted.terminal_state in ("published", "already_published") and confirmed is True
    repository.record_receipt(
        claim,
        provider="spotify",
        transport_class="accepted" if accepted else "ambiguous",
        provider_item_id=provider_id,
        native_state=promoted.terminal_state,
        ambiguous=not accepted,
        code=None if accepted else "spotify_promotion_unconfirmed",
    )
    repository.record_verification(
        claim,
        provider="spotify",
        result="externally_verified_public" if accepted else "publication_unknown",
        source="spotify_episode_readback",
        provider_item_id=provider_id,
        native_state="published" if accepted else promoted.terminal_state,
        proof=(
            exact_verification_proof(document, provider_item_id=provider_id) if accepted else None
        ),
    )


def _public_url(value: str, *, origin: bool = False) -> str | None:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    if origin and parsed.path not in ("", "/"):
        return None
    return value.strip().rstrip("/") if origin else value.strip()


def _verify_public_media(url: str, expected_sha256: str, expected_size: int) -> bool:
    request = Request(url, method="GET", headers={"User-Agent": "SquadScope-Podcaster/1"})
    digest = hashlib.sha256()
    size = 0
    try:
        with safe_urlopen(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                return False
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
                if size > expected_size:
                    return False
    except Exception:
        return False
    return size == expected_size and digest.hexdigest() == expected_sha256


def _read_public_feed(url: str) -> str | None:
    request = Request(url, method="GET", headers={"User-Agent": "SquadScope-Podcaster/1"})
    try:
        with safe_urlopen(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                return None
            body = response.read(2 * 1024 * 1024 + 1)
    except Exception:
        return None
    if len(body) > 2 * 1024 * 1024:
        return None
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _rss_contains(feed: str | None, *, outbox_id: str, media_url: str) -> bool:
    return bool(
        feed
        and f'<guid isPermaLink="false">{outbox_id}</guid>' in feed
        and f'url="{_escape_xml(media_url)}"' in feed
    )


def _process_spotify_rss(
    repository,
    claim,
    document: Mapping[str, Any],
    storage: StorageBackend,
    config: VideoDistributionConfig,
) -> None:
    leg = document["providers"]["spotify_rss"]
    artifact = document["artifact"]
    origin = _public_url(config.spotify_rss_public_media_origin, origin=True)
    feed_url = _public_url(config.spotify_rss_public_feed_url)
    context = leg.get("context")
    feed_path = (
        str(context.get("feed_path") or "") if isinstance(context, Mapping) else ""
    ) or config.spotify_rss_feed_path
    if not origin or not feed_url or not feed_path:
        repository.record_verification(
            claim,
            provider="spotify_rss",
            result="manual_handoff_required",
            source="spotify_rss_public_origin",
            exhaustion_reason="immutable_public_origin_or_feed_unconfigured",
        )
        return
    media_url = f"{origin}/{quote(str(artifact['path']), safe='/')}"
    provider_item_id = str(artifact["sha256"])
    media_verified = _verify_public_media(
        media_url,
        provider_item_id,
        int(artifact["size_bytes"]),
    )
    existing_feed = _read_public_feed(feed_url)
    if media_verified and _rss_contains(
        existing_feed,
        outbox_id=str(document["outbox_id"]),
        media_url=media_url,
    ):
        repository.record_verification(
            claim,
            provider="spotify_rss",
            result="externally_verified_public",
            source="spotify_rss_external_readback",
            provider_item_id=provider_item_id,
            native_state="public",
            proof=exact_verification_proof(document, provider_item_id=provider_item_id),
        )
        return
    if not media_verified:
        repository.record_verification(
            claim,
            provider="spotify_rss",
            result="manual_handoff_required",
            source="spotify_rss_media_readback",
            provider_item_id=provider_item_id,
            exhaustion_reason="immutable_public_media_unavailable_or_identity_mismatch",
        )
        return
    if not provider_approval_is_valid(document, "spotify_rss"):
        repository.record_verification(
            claim,
            provider="spotify_rss",
            result="manual_handoff_required",
            source="spotify_rss_human_approval_required",
            provider_item_id=provider_item_id,
            exhaustion_reason="rss_publication_requires_human_approval",
        )
        return
    if _accepted_receipt_for_operation(leg, "rss_feed_publish"):
        due = utc_now() + timedelta(minutes=5)
        repository.record_verification(
            claim,
            provider="spotify_rss",
            result="pending_provider",
            source="spotify_rss_external_readback",
            provider_item_id=provider_item_id,
            native_state="propagating",
            next_reconcile_at=due,
        )
        repository.schedule_reconciliation(
            claim,
            provider="spotify_rss",
            due_at=due,
        )
        return
    if claim.read_only:
        repository.record_verification(
            claim,
            provider="spotify_rss",
            result="publication_unknown",
            source="spotify_rss_external_readback",
            provider_item_id=provider_item_id,
            exhaustion_reason="consumed_rss_intent_not_externally_verified",
        )
        return
    repository.persist_intent(
        claim,
        provider="spotify_rss",
        operation="rss_feed_publish",
        expected_provider_item_id=provider_item_id,
        precondition_fingerprint=hashlib.sha256(
            f"{feed_url}|{media_url}|{document['outbox_id']}".encode()
        ).hexdigest(),
    )
    repository.consume_intent(
        claim,
        provider="spotify_rss",
        provider_timeout_seconds=30,
        receipt_margin_seconds=30,
    )
    title, description = _manifest_metadata(
        storage,
        str(document["publication_identity"]["accepted_job_id"]),
    )
    item = (
        "  <item>\n"
        f"    <title>{_escape_xml(title)}</title>\n"
        f"    <description>{_escape_xml(description)}</description>\n"
        f'    <guid isPermaLink="false">{document["outbox_id"]}</guid>\n'
        f'    <enclosure url="{_escape_xml(media_url)}" '
        f'length="{artifact["size_bytes"]}" type="video/mp4" />\n'
        "  </item>\n"
    )

    def _update_feed(raw: bytes | None) -> bytes:
        current = (
            raw.decode("utf-8")
            if raw
            else (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<rss version="2.0"><channel>\n'
                "  <title>SquadScope Video Podcast</title>\n"
                "</channel></rss>\n"
            )
        )
        if _rss_contains(
            current,
            outbox_id=str(document["outbox_id"]),
            media_url=media_url,
        ):
            return current.encode("utf-8")
        if "</channel>" not in current:
            raise RuntimeError("Spotify RSS feed is malformed")
        return current.replace("</channel>", f"{item}</channel>", 1).encode("utf-8")

    storage.update_bytes(
        feed_path,
        "application/rss+xml; charset=utf-8",
        _update_feed,
    )
    repository.record_receipt(
        claim,
        provider="spotify_rss",
        transport_class="accepted",
        provider_item_id=provider_item_id,
        native_state="feed_updated",
    )
    verified_feed = _read_public_feed(feed_url)
    verified = _rss_contains(
        verified_feed,
        outbox_id=str(document["outbox_id"]),
        media_url=media_url,
    ) and _verify_public_media(media_url, provider_item_id, int(artifact["size_bytes"]))
    repository.record_verification(
        claim,
        provider="spotify_rss",
        result="externally_verified_public" if verified else "pending_provider",
        source="spotify_rss_external_readback",
        provider_item_id=provider_item_id,
        native_state="public" if verified else "propagating",
        next_reconcile_at=None if verified else utc_now() + timedelta(minutes=5),
        proof=(
            exact_verification_proof(document, provider_item_id=provider_item_id)
            if verified
            else None
        ),
    )
    if not verified:
        repository.schedule_reconciliation(
            claim,
            provider="spotify_rss",
            due_at=utc_now() + timedelta(minutes=5),
        )


def process_message(
    message: QueueMessage,
    *,
    queue: QueueBackend,
    storage: StorageBackend,
    config: VideoDistributionConfig | None = None,
) -> dict[str, Any]:
    try:
        outbox_id = parse_distribution_outbox_id(message.body)
    except Exception:
        logger.error(
            "discarding malformed distribution message message_id=%s dequeue_count=%s",
            message.message_id,
            message.dequeue_count,
        )
        queue.delete_message(message)
        return {
            "state": "malformed_discarded",
            "aggregate": {
                "result": "failed_terminal",
                "externally_verified_public": False,
            },
            "providers": {},
        }
    repository = DistributionOutboxRepository(storage)
    document = repository.read(outbox_id)
    if document is None:
        raise RuntimeError("distribution outbox item is missing")
    claim = None
    try:
        claim = repository.claim(
            outbox_id,
            owner="distribution-worker",
            execution_id=uuid.uuid4().hex,
            lease_seconds=900,
        )
        now = utc_now()
        for provider, leg in document["providers"].items():
            token = leg.get("active_schedule_token")
            due_at = leg.get("next_reconcile_at")
            if token and due_at:
                due = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
                if due <= now:
                    repository.consume_schedule_token(
                        claim,
                        provider=str(provider),
                        token=str(token),
                    )
        artifact = repository.read(outbox_id)
        assert artifact is not None
        from podcaster.distribution_outbox import _artifact_from

        reference = _artifact_from(artifact)
        verify_artifact(storage, reference)
        with tempfile.TemporaryDirectory(prefix="distribution-worker-") as directory:
            local = Path(directory) / f"artifact{Path(reference.path).suffix}"
            if not storage.download_file(reference.path, local):
                raise RuntimeError("distribution artifact download failed")
            active_config = config or VideoDistributionConfig.from_env()
            if "youtube" in document["providers"]:
                _process_youtube(repository, claim, storage, document, local, active_config)
            if "spotify" in document["providers"]:
                _process_spotify(
                    repository,
                    claim,
                    document,
                    local,
                    storage,
                    active_config,
                )
            if "spotify_rss" in document["providers"]:
                _process_spotify_rss(
                    repository,
                    claim,
                    document,
                    storage,
                    active_config,
                )
        final = repository.release(claim)
    except Exception:
        if claim is None or message.dequeue_count < MAX_DEQUEUE_COUNT:
            raise
        logger.exception(
            "distribution message exhausted retries outbox_id=%s dequeue_count=%s",
            outbox_id,
            message.dequeue_count,
        )
        current = repository.read(outbox_id)
        if current is None:
            raise
        for provider, leg in current["providers"].items():
            if leg.get("result") == "externally_verified_public":
                continue
            repository.record_verification(
                claim,
                provider=str(provider),
                result="poisoned",
                source="distribution_worker_poison",
                provider_item_id=_provider_item_id(leg),
                exhaustion_reason="maximum_dequeue_count_exhausted",
            )
        final = repository.release(claim)
    for row in signal_rows([final]):
        logger.info("distribution_signal %s", json.dumps(row, sort_keys=True))
    queue.delete_message(message)
    return final


class _DefaultTransportProxy:
    def __new__(cls):
        from podcaster.video.distribution import _DefaultTransport

        return _DefaultTransport()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    queue = create_distribution_queue_backend()
    if queue is None:
        logger.error("distribution queue is not configured")
        return 2
    messages = queue.receive_messages(max_messages=1, visibility_timeout=900)
    if not messages:
        logger.error("expected one distribution message but queue was empty")
        return 1
    final = process_message(messages[0], queue=queue, storage=create_storage_backend())
    return aggregate_exit_code([final])


if __name__ == "__main__":
    raise SystemExit(main())
