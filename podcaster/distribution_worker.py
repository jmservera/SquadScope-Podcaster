"""One-item provider distribution worker backed by the durable outbox."""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from xml.sax.saxutils import escape

from podcaster.distribution_outbox import (
    DistributionOutboxRepository,
    aggregate_exit_code,
    exact_verification_proof,
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
from podcaster.storage import StorageBackend, create_storage_backend
from podcaster.video.distribution import (
    VideoDistributionConfig,
    YouTubeDeliveryError,
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


def _manifest_metadata(storage: StorageBackend, job_id: str) -> tuple[str, str]:
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
    return title, description


def _manifest(storage: StorageBackend, job_id: str) -> dict[str, Any]:
    raw = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    if raw is None:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _operator_approval(
    document: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    provider: str,
) -> dict[str, str] | None:
    review = manifest.get("review")
    if not isinstance(review, Mapping) or review.get("status") != "approved":
        return None
    approved_by = review.get("approved_by")
    approved_at = review.get("approved_at")
    audit = review.get("audit_trail")
    if (
        not isinstance(approved_by, str)
        or not approved_by.strip()
        or approved_by.startswith("system:")
        or not isinstance(approved_at, str)
        or not approved_at.strip()
        or not isinstance(audit, list)
        or not any(
            isinstance(item, Mapping)
            and item.get("actor") == approved_by
            and item.get("at") == approved_at
            and item.get("decision") == "approved"
            for item in audit
        )
    ):
        return None
    identity = document["publication_identity"]
    if manifest.get("job_id") != identity["accepted_job_id"]:
        return None
    request = manifest.get("request")
    if (
        not isinstance(request, Mapping)
        or request.get("publish_run_id") != identity["publish_run_id"]
        or request.get("manifest_sha256") != identity["manifest_sha256"]
    ):
        return None
    return {
        "approved_by": approved_by,
        "approved_at": approved_at,
        "decision": "approved",
        "provider": provider,
    }


def _record_approval_or_handoff(
    repository: DistributionOutboxRepository,
    claim,
    document: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    provider: str,
    provider_item_id: str | None = None,
) -> bool:
    evidence = _operator_approval(document, manifest, provider=provider)
    if evidence is None:
        repository.record_verification(
            claim,
            provider=provider,
            result="manual_handoff_required",
            source="operator_approval_required",
            provider_item_id=provider_item_id,
            native_state="approval_required",
            exhaustion_reason="identity_bound_human_approval_missing",
        )
        return False
    repository.record_operator_approval(claim, provider=provider, evidence=evidence)
    return True


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
    transport = None
    access_token = _get_youtube_access_token(config, transport or _DefaultTransportProxy())
    title, description = _manifest_metadata(
        storage, str(document["publication_identity"]["accepted_job_id"])
    )
    manifest = _manifest(storage, str(document["publication_identity"]["accepted_job_id"]))

    if video_id is None:
        if claim.read_only:
            repository.record_verification(
                claim,
                provider="youtube",
                result="publication_unknown",
                source="youtube_identity_unprovable",
                exhaustion_reason="consumed_upload_intent_has_no_provider_identity",
            )
            return
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

    request = manifest.get("request")
    locale = str(request.get("language", "en")) if isinstance(request, Mapping) else "en"
    playlist_id = resolve_playlist_id(config, locale)
    if playlist_id:
        try:
            in_playlist = playlist_contains_video(
                playlist_id,
                video_id,
                access_token,
                raise_on_error=True,
            )
        except RuntimeError:
            repository.record_verification(
                claim,
                provider="youtube",
                result="publication_unknown",
                source="youtube_playlist_readback",
                provider_item_id=video_id,
                native_state="unknown",
                exhaustion_reason="playlist_membership_readback_failed",
            )
            return
        if not in_playlist:
            if claim.read_only:
                repository.record_verification(
                    claim,
                    provider="youtube",
                    result="publication_unknown",
                    source="youtube_playlist_readback",
                    provider_item_id=video_id,
                    native_state="absent",
                    exhaustion_reason="consumed_playlist_intent_not_verified",
                )
                return
            repository.persist_intent(
                claim,
                provider="youtube",
                operation="playlist_insert",
                expected_provider_item_id=video_id,
                precondition_fingerprint=playlist_id,
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
            )
            if not result.succeeded:
                repository.record_receipt(
                    claim,
                    provider="youtube",
                    transport_class="ambiguous",
                    provider_item_id=video_id,
                    native_state="playlist_unknown",
                    ambiguous=True,
                    code="youtube_playlist_insert_unconfirmed",
                )
                repository.record_verification(
                    claim,
                    provider="youtube",
                    result="publication_unknown",
                    source="youtube_playlist_insert",
                    provider_item_id=video_id,
                    native_state="unknown",
                )
                return
            repository.record_receipt(
                claim,
                provider="youtube",
                transport_class="accepted",
                provider_item_id=video_id,
                native_state="playlist_inserted",
            )
            try:
                in_playlist = playlist_contains_video(
                    playlist_id,
                    video_id,
                    access_token,
                    raise_on_error=True,
                )
            except RuntimeError:
                in_playlist = False
            if not in_playlist:
                repository.record_verification(
                    claim,
                    provider="youtube",
                    result="publication_unknown",
                    source="youtube_playlist_post_insert_readback",
                    provider_item_id=video_id,
                    native_state="unknown",
                    exhaustion_reason="playlist_insert_not_verified",
                )
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

    if not _record_approval_or_handoff(
        repository,
        claim,
        document,
        manifest,
        provider="youtube",
        provider_item_id=video_id,
    ):
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
    result = publish_video(video_id, access_token, privacy_status=PRIVACY_PUBLIC)
    confirmed_state = get_video_snippet(video_id, access_token) if result.succeeded else None
    confirmed_public = (
        isinstance(confirmed_state, Mapping)
        and str(confirmed_state.get("privacyStatus") or "").lower() == PRIVACY_PUBLIC
        and str(confirmed_state.get("processingStatus") or "").lower() == "succeeded"
    )
    repository.record_receipt(
        claim,
        provider="youtube",
        transport_class="accepted" if confirmed_public else "ambiguous",
        provider_item_id=video_id,
        native_state=PRIVACY_PUBLIC if confirmed_public else result.privacy_status,
        ambiguous=not confirmed_public,
        code=None if confirmed_public else "youtube_promotion_unconfirmed",
    )
    repository.record_verification(
        claim,
        provider="youtube",
        result="externally_verified_public" if confirmed_public else "publication_unknown",
        source="youtube_privacy_readback",
        provider_item_id=video_id,
        native_state=PRIVACY_PUBLIC if confirmed_public else result.privacy_status,
        proof=(
            exact_verification_proof(document, provider_item_id=video_id)
            if confirmed_public
            else None
        ),
    )


def _audio_anchor_id(manifest: Mapping[str, Any]) -> int | None:
    generation = manifest.get("generation")
    publish_result = generation.get("publish_result") if isinstance(generation, Mapping) else None
    value = publish_result.get("anchor_id") if isinstance(publish_result, Mapping) else None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _process_spotify(
    repository,
    claim,
    storage: StorageBackend,
    document: Mapping[str, Any],
    local_artifact: Path,
    config: VideoDistributionConfig,
) -> None:
    leg = document["providers"]["spotify"]
    if leg.get("result") == "externally_verified_public":
        return
    provider_id = _provider_item_id(leg)
    manifest = _manifest(storage, str(document["publication_identity"]["accepted_job_id"]))
    if provider_id is not None:
        try:
            published = read_spotify_video_publication_state(int(provider_id))
        except ValueError:
            published = None
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
        if claim.read_only:
            repository.record_verification(
                claim,
                provider="spotify",
                result="publication_unknown",
                source="spotify_episode_readback",
                provider_item_id=provider_id,
                native_state="draft",
                exhaustion_reason="consumed_spotify_intent_not_public",
            )
            return

    if provider_id is None:
        if claim.read_only:
            repository.record_verification(
                claim,
                provider="spotify",
                result="publication_unknown",
                source="spotify_identity_unprovable",
                exhaustion_reason="consumed_spotify_upload_has_no_identity",
            )
            return
        if not _record_approval_or_handoff(
            repository, claim, document, manifest, provider="spotify"
        ):
            return
        repository.persist_intent(claim, provider="spotify", operation="create_video_draft")
        repository.consume_intent(
            claim,
            provider="spotify",
            provider_timeout_seconds=600,
            receipt_margin_seconds=60,
        )
        title, description = _manifest_metadata(
            storage, str(document["publication_identity"]["accepted_job_id"])
        )
        result = upload_video_to_episode(
            local_artifact,
            _audio_anchor_id(manifest),
            title=title,
            description=description,
            content_type="video/mp4",
        )
        provider_id = str(result.anchor_episode_id) if result.anchor_episode_id else None
        if result.status == "failed" or provider_id is None:
            repository.record_receipt(
                claim,
                provider="spotify",
                transport_class="ambiguous",
                provider_item_id=provider_id,
                native_state="upload_unknown",
                ambiguous=True,
                code="spotify_video_upload_unconfirmed",
            )
            repository.record_verification(
                claim,
                provider="spotify",
                result="publication_unknown",
                source="spotify_video_upload",
                provider_item_id=provider_id,
                native_state="unknown",
            )
            return
        repository.record_receipt(
            claim,
            provider="spotify",
            transport_class="accepted",
            provider_item_id=provider_id,
            native_state="draft",
        )

    if config.spotify_video_publish_mode != "live":
        repository.record_verification(
            claim,
            provider="spotify",
            result="manual_handoff_required",
            source="spotify_live_gate",
            provider_item_id=provider_id,
            native_state="draft",
            exhaustion_reason="spotify_live_mode_not_requested",
        )
        return
    if not _record_approval_or_handoff(
        repository,
        claim,
        document,
        manifest,
        provider="spotify",
        provider_item_id=provider_id,
    ):
        return
    repository.persist_intent(
        claim,
        provider="spotify",
        operation="live_promotion",
        expected_provider_item_id=provider_id,
    )
    repository.consume_intent(
        claim,
        provider="spotify",
        provider_timeout_seconds=60,
        receipt_margin_seconds=30,
    )
    try:
        promote = promote_spotify_video_draft(
            int(provider_id),
            audio_anchor_id=_audio_anchor_id(manifest),
            spotify_video_publish_mode="live",
            job_id=str(document["publication_identity"]["accepted_job_id"]),
            run_id=str(document["publication_identity"]["publish_run_id"]),
        )
    except Exception:
        repository.record_receipt(
            claim,
            provider="spotify",
            transport_class="ambiguous",
            provider_item_id=provider_id,
            native_state="promotion_unknown",
            ambiguous=True,
            code="spotify_promotion_unconfirmed",
        )
        repository.record_verification(
            claim,
            provider="spotify",
            result="publication_unknown",
            source="spotify_live_promotion",
            provider_item_id=provider_id,
            native_state="unknown",
        )
        return
    if promote.is_published is not True:
        terminal = str(promote.terminal_state or "manual_handoff_required")
        repository.record_receipt(
            claim,
            provider="spotify",
            transport_class="failed",
            provider_item_id=provider_id,
            native_state=terminal,
            code="spotify_live_gate_denied",
        )
        repository.record_verification(
            claim,
            provider="spotify",
            result=(
                "manual_handoff_required"
                if terminal
                in {
                    "draft_gate_denied",
                    "manual_handoff_required",
                    "blocked_protected_historical_draft",
                }
                else "publication_unknown"
            ),
            source="spotify_live_promotion",
            provider_item_id=provider_id,
            native_state=terminal,
        )
        return
    published = read_spotify_video_publication_state(int(provider_id))
    repository.record_receipt(
        claim,
        provider="spotify",
        transport_class="accepted" if published is True else "ambiguous",
        provider_item_id=provider_id,
        native_state="published" if published is True else "promotion_unknown",
        ambiguous=published is not True,
        code=None if published is True else "spotify_post_promotion_readback_failed",
    )
    repository.record_verification(
        claim,
        provider="spotify",
        result="externally_verified_public" if published is True else "publication_unknown",
        source="spotify_episode_readback",
        provider_item_id=provider_id,
        native_state="published" if published is True else "unknown",
        proof=(
            exact_verification_proof(document, provider_item_id=provider_id)
            if published is True
            else None
        ),
    )


def _process_spotify_rss(
    repository,
    claim,
    storage: StorageBackend,
    document: Mapping[str, Any],
    config: VideoDistributionConfig,
) -> None:
    provider = "spotify_rss"
    if not config.spotify_rss_feed_path:
        repository.record_verification(
            claim,
            provider=provider,
            result="manual_handoff_required",
            source="spotify_rss_config",
            native_state="feed_path_missing",
            exhaustion_reason="spotify_rss_feed_path_missing",
        )
        return
    feed_path = config.spotify_rss_feed_path
    item_id = document["outbox_id"]
    marker = f'<guid isPermaLink="false">{item_id}</guid>'
    raw = storage.get_bytes(feed_path)
    if raw is not None and marker.encode("utf-8") in raw:
        repository.record_verification(
            claim,
            provider=provider,
            result="externally_verified_public",
            source="spotify_rss_readback",
            provider_item_id=item_id,
            native_state="published",
            proof=exact_verification_proof(document, provider_item_id=item_id),
        )
        return
    if claim.read_only:
        repository.record_verification(
            claim,
            provider=provider,
            result="publication_unknown",
            source="spotify_rss_readback",
            provider_item_id=item_id,
            native_state="absent",
            exhaustion_reason="consumed_rss_intent_not_verified",
        )
        return
    manifest = _manifest(storage, str(document["publication_identity"]["accepted_job_id"]))
    if not _record_approval_or_handoff(
        repository, claim, document, manifest, provider=provider, provider_item_id=item_id
    ):
        return
    download = storage.generate_download_url(
        str(document["artifact"]["path"]),
        expiry=utc_now() + timedelta(days=7),
    )
    if download.signed:
        repository.record_verification(
            claim,
            provider=provider,
            result="manual_handoff_required",
            source="spotify_rss_media_url",
            provider_item_id=item_id,
            native_state="private_artifact",
            exhaustion_reason="rss_requires_durable_public_media_url",
        )
        return
    repository.persist_intent(
        claim,
        provider=provider,
        operation="rss_insert",
        expected_provider_item_id=item_id,
        precondition_fingerprint=item_id,
    )
    repository.consume_intent(
        claim,
        provider=provider,
        provider_timeout_seconds=30,
        receipt_margin_seconds=30,
    )
    title, description = _manifest_metadata(
        storage, str(document["publication_identity"]["accepted_job_id"])
    )
    item = (
        "  <item>\n"
        f"    <title>{escape(title)}</title>\n"
        f"    <description>{escape(description)}</description>\n"
        f"    {marker}\n"
        f'    <enclosure url="{escape(download.url)}" type="video/mp4" '
        f'length="{int(document["artifact"]["size_bytes"])}" />\n'
        "  </item>\n"
    )

    def _insert(existing: bytes | None) -> bytes:
        text = existing.decode("utf-8") if existing else "<rss><channel></channel></rss>"
        if marker in text:
            return text.encode("utf-8")
        position = text.rfind("</channel>")
        if position < 0:
            raise ValueError("RSS feed is malformed")
        return (text[:position] + item + text[position:]).encode("utf-8")

    try:
        storage.update_bytes(feed_path, "application/rss+xml", _insert)
    except Exception:
        repository.record_receipt(
            claim,
            provider=provider,
            transport_class="ambiguous",
            provider_item_id=item_id,
            native_state="rss_unknown",
            ambiguous=True,
            code="spotify_rss_update_unconfirmed",
        )
        repository.record_verification(
            claim,
            provider=provider,
            result="publication_unknown",
            source="spotify_rss_update",
            provider_item_id=item_id,
            native_state="unknown",
        )
        return
    repository.record_receipt(
        claim,
        provider=provider,
        transport_class="accepted",
        provider_item_id=item_id,
        native_state="published",
    )
    verified = storage.get_bytes(feed_path)
    present = verified is not None and marker.encode("utf-8") in verified
    repository.record_verification(
        claim,
        provider=provider,
        result="externally_verified_public" if present else "publication_unknown",
        source="spotify_rss_readback",
        provider_item_id=item_id,
        native_state="published" if present else "unknown",
        proof=exact_verification_proof(document, provider_item_id=item_id) if present else None,
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
    except ValueError:
        logger.error(
            "discarding malformed distribution message message_id=%s dequeue_count=%s",
            message.message_id,
            message.dequeue_count,
        )
        queue.delete_message(message)
        return {
            "providers": {},
            "aggregate": {
                "result": "poisoned",
                "externally_verified_public": False,
                "terminal_reason": "malformed_message",
            },
        }
    repository = DistributionOutboxRepository(storage)
    document = repository.read(outbox_id)
    if document is None:
        logger.error(
            "distribution outbox item missing outbox_id=%s dequeue_count=%s",
            outbox_id,
            message.dequeue_count,
        )
        if message.dequeue_count >= MAX_DEQUEUE_COUNT:
            queue.delete_message(message)
            return {
                "providers": {},
                "aggregate": {
                    "result": "poisoned",
                    "externally_verified_public": False,
                    "terminal_reason": "outbox_missing",
                },
            }
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
            if "spotify_rss" in document["providers"]:
                _process_spotify_rss(repository, claim, storage, document, active_config)
            if "spotify" in document["providers"]:
                _process_spotify(
                    repository,
                    claim,
                    storage,
                    document,
                    local,
                    active_config,
                )
        final = repository.release(claim)
    except Exception:
        if message.dequeue_count < MAX_DEQUEUE_COUNT:
            raise
        logger.exception(
            "distribution retry exhausted outbox_id=%s dequeue_count=%s",
            outbox_id,
            message.dequeue_count,
        )
        if claim is None:
            raise
        current = repository.read(outbox_id) or document
        for provider, leg in current["providers"].items():
            if leg.get("result") == "externally_verified_public":
                continue
            repository.record_verification(
                claim,
                provider=str(provider),
                result="poisoned",
                source="distribution_worker_retry_exhausted",
                provider_item_id=_provider_item_id(leg),
                native_state="poisoned",
                exhaustion_reason="queue_dequeue_limit_reached",
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
