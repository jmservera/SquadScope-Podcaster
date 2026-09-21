"""One-item provider distribution worker backed by the durable outbox."""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from podcaster.distribution_outbox import (
    DistributionOutboxRepository,
    aggregate_exit_code,
    utc_now,
    verify_artifact,
)
from podcaster.distribution_telemetry import signal_rows
from podcaster.publish import read_spotify_video_publication_state
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
from podcaster.video.youtube_publish import (
    PRIVACY_PUBLIC,
    get_video_snippet,
    publish_video,
)

logger = logging.getLogger(__name__)


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
    if privacy == PRIVACY_PUBLIC and processing_status == "succeeded":
        repository.record_verification(
            claim,
            provider="youtube",
            result="externally_verified_public",
            source="youtube_videos_list",
            provider_item_id=video_id,
            native_state=privacy,
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
    result = publish_video(video_id, access_token, privacy_status=PRIVACY_PUBLIC)
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
    )


def _process_spotify(repository, claim, document: Mapping[str, Any]) -> None:
    leg = document["providers"]["spotify"]
    if leg.get("result") == "externally_verified_public":
        return
    provider_id = _provider_item_id(leg)
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
    repository.record_verification(
        claim,
        provider="spotify",
        result="manual_handoff_required",
        source="unsupported_public_mutation",
        provider_item_id=provider_id,
        native_state="manual_handoff_required",
        exhaustion_reason="authoritative_creator_mutation_contract_unavailable",
    )


def process_message(
    message: QueueMessage,
    *,
    queue: QueueBackend,
    storage: StorageBackend,
    config: VideoDistributionConfig | None = None,
) -> dict[str, Any]:
    outbox_id = parse_distribution_outbox_id(message.body)
    repository = DistributionOutboxRepository(storage)
    document = repository.read(outbox_id)
    if document is None:
        raise RuntimeError("distribution outbox item is missing")
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
            _process_spotify(repository, claim, document)
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
