"""Operation-aware dispatch of separately reviewed, immutable private video."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from podcaster import publish
from podcaster.distribution_outbox import (
    ArtifactReference,
    DistributionOutboxError,
    exact_verification_proof,
    media_review_allows_distribution,
    outbox_id_for,
    verify_artifact,
)
from podcaster.orchestration import load_manifest
from podcaster.publication_state import publication_identity
from podcaster.review import is_exact_script_job, validate_private_media_inputs
from podcaster.video import distribution, youtube_playlist, youtube_publish
from podcaster.video.youtube_reconcile import (
    ABSENT,
    reconcile_youtube_upload,
    youtube_identity_tag,
)


def _file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dispatch_reviewed_video(repository, claim, document, *, config=None, transport=None):
    """Use existing provider adapters; never admit legacy or unreviewed outboxes."""
    if "media_review" not in document:
        return "legacy_provider_dispatch_not_authorized"
    if not media_review_allows_distribution(document):
        raise DistributionOutboxError("separate media review is missing or stale")
    config = config or distribution.VideoDistributionConfig.from_env()
    if config.dry_run:
        return "provider_dry_run"
    job_id = document["publication_identity"]["accepted_job_id"]
    manifest = load_manifest(repository.storage, job_id)
    language = manifest["request"].get("language", "en")
    identity = publication_identity(
        manifest, job_id, document["publication_identity"]["publish_run_id"]
    )
    artifact = ArtifactReference(**document["artifact"])
    path = None

    def validate(provider, operation=None):
        if provider == "spotify" and not publish._spotify_reconcile_enabled():
            raise DistributionOutboxError("Spotify outbox requires strict reconcile-before-create")
        current = repository.authorize_dispatch(claim, provider=provider, operation=operation)
        latest = load_manifest(repository.storage, job_id)
        if not is_exact_script_job(latest):
            raise DistributionOutboxError("canonical synthesis proof is required")
        audio = validate_private_media_inputs(latest, repository.storage, job_id)
        if (
            asdict(identity) != current["publication_identity"]
            or publication_identity(latest, job_id, identity.publish_run_id) != identity
            or outbox_id_for(
                identity, media_kind="video", provider_objectives=current["provider_objectives"]
            )
            != claim.outbox_id
            or current["artifact"] != document["artifact"]
            or current["canonical_artifact"] != document["canonical_artifact"]
            or current["publication_digest"] != document["publication_digest"]
            or not media_review_allows_distribution(current)
            or latest["generation"]["video_runner"]["distribution"]["outbox_id"] != claim.outbox_id
            or current["providers"][provider]["context"].get("runtime_sha256")
            != distribution.provider_runtime_sha256(provider, config, language)
            or current["providers"][provider]["context"].get("synthesis_audio_path") != audio.path
            or current["providers"][provider]["context"].get("synthesis_audio_sha256")
            != hashlib.sha256(audio.content).hexdigest()
            or datetime.fromisoformat(latest["expires_at"].replace("Z", "+00:00"))
            <= repository.now()
        ):
            raise DistributionOutboxError("reviewed video or provider binding changed")
        verify_artifact(repository.storage, artifact)
        if path is not None and _file_digest(path) != artifact.sha256:
            raise DistributionOutboxError("local reviewed MP4 changed")
        return current

    # Validate *all* destinations before the first provider call, not midway
    # through a multi-provider publication.
    for provider in document["providers"]:
        validate(provider)
    root = Path(".scratch") / "distribution" / claim.claim_id
    root.mkdir(parents=True, mode=0o700)
    path = root / "reviewed.mp4"
    try:
        if (
            artifact.media_kind != "video"
            or not repository.storage.download_file(artifact.path, path)
            or path.stat().st_size != artifact.size_bytes
            or _file_digest(path) != artifact.sha256
        ):
            raise DistributionOutboxError("reviewed MP4 integrity verification failed")

        def mutate(provider, operation, call, *, provider_id=None):
            state = validate(provider)
            leg = state["providers"][provider]
            accepted = [
                receipt
                for receipt in leg["receipts"]
                if receipt["operation"] == operation
                and receipt["transport_class"] == "accepted"
                and not receipt["ambiguous"]
            ]
            if accepted:
                return accepted[-1]["provider_item_id"]
            if claim.read_only:
                raise DistributionOutboxError("ambiguous dispatch is reconciliation-only")
            repository.persist_intent(
                claim,
                provider=provider,
                operation=operation,
                expected_provider_item_id=provider_id,
                precondition_fingerprint=document["publication_digest"],
            )
            repository.consume_intent(
                claim, provider=provider, provider_timeout_seconds=120, receipt_margin_seconds=30
            )

            def authorize():
                validate(provider, operation)

            authorize()
            try:
                item_id, native_state = call(authorize)
                if not item_id:
                    raise DistributionOutboxError("provider did not return a confirmed identity")
            except Exception:
                repository.record_receipt(
                    claim,
                    provider=provider,
                    transport_class="unknown",
                    provider_item_id=provider_id,
                    ambiguous=True,
                    code="provider_operation_unknown",
                )
                repository.record_verification(
                    claim,
                    provider=provider,
                    result="publication_unknown",
                    source="provider_operation_readback_required",
                    provider_item_id=provider_id,
                )
                raise
            repository.record_receipt(
                claim,
                provider=provider,
                transport_class="accepted",
                provider_item_id=str(item_id),
                native_state=native_state,
            )
            return str(item_id)

        for provider in document["providers"]:
            state = validate(provider)
            if state["providers"][provider]["result"] == "externally_verified_public":
                continue
            if provider == "youtube" and config.youtube_enabled:
                _youtube(
                    repository,
                    claim,
                    state,
                    identity,
                    config,
                    path,
                    validate,
                    mutate,
                    transport or distribution._DefaultTransport(),
                )
            elif provider == "spotify" and config.spotify_upload_enabled:
                _spotify(
                    repository, claim, state, identity, config, path, language, validate, mutate
                )
            else:
                repository.record_verification(
                    claim,
                    provider=provider,
                    result="manual_handoff_required",
                    source="provider_runtime_not_enabled",
                )
        return "reviewed_video_dispatched"
    finally:
        path.unlink(missing_ok=True)
        root.rmdir()


def _provider_id(leg):
    ids = {
        receipt["provider_item_id"]
        for receipt in leg["receipts"]
        if receipt.get("provider_item_id")
    }
    verification_id = (leg.get("verification") or {}).get("provider_item_id")
    if verification_id:
        ids.add(verification_id)
    if len(ids) > 1:
        raise DistributionOutboxError("conflicting provider identities")
    return next(iter(ids), None)


def _pending(repository, claim, provider, item_id, native_state, source):
    repository.record_verification(
        claim,
        provider=provider,
        result="pending_provider",
        source=source,
        provider_item_id=item_id,
        native_state=native_state,
    )
    repository.schedule_reconciliation(
        claim, provider=provider, due_at=repository.now() + timedelta(minutes=5)
    )


class _GuardedTransport:
    def __init__(self, transport, authorize):
        self.transport, self.authorize = transport, authorize

    def request(self, *args, **kwargs):
        self.authorize()
        return self.transport.request(*args, **kwargs)

    def request_with_headers(self, *args, **kwargs):
        self.authorize()
        return self.transport.request_with_headers(*args, **kwargs)


def _youtube(repository, claim, document, identity, config, path, validate, mutate, http):
    validate("youtube")
    token = distribution._get_youtube_access_token(config, http)
    status, body = http.request(
        "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    channels = json.loads(body).get("items") if status == 200 else None
    if not isinstance(channels, list) or len(channels) != 1 or not channels[0].get("id"):
        raise DistributionOutboxError("YouTube owner identity readback is incomplete")
    channel_id = channels[0]["id"]
    tag = youtube_identity_tag(identity)
    tags = [
        tag,
        f"sqmedia-{document['artifact']['sha256']}",
        f"sqreview-{document['publication_digest']}",
    ]
    leg = document["providers"]["youtube"]
    video_id = _provider_id(leg)
    if not video_id:
        found = reconcile_youtube_upload(
            tag,
            token,
            http,
            not_before=datetime.fromisoformat(document["created_at"].replace("Z", "+00:00")),
        )
        if found.matched:
            video_id = found.video_id
        elif (
            found.status != ABSENT
            or claim.read_only
            or (leg.get("intent") or {}).get("consumed_at")
        ):
            repository.record_verification(
                claim,
                provider="youtube",
                result="publication_unknown",
                source="youtube_identity_readback_unresolved",
            )
            return
        else:

            def upload(authorize):
                video, _url = distribution.upload_to_youtube(
                    path,
                    f"Podcast {identity.week}",
                    f"Publication {document['publication_digest']}",
                    config,
                    tags=tags,
                    identity_tag=tag,
                    raise_on_failure=True,
                    transport=_GuardedTransport(http, authorize),
                )
                return video, config.youtube_privacy

            video_id = mutate("youtube", "draft_upload", upload)

    def readback():
        validate("youtube")
        status, body = http.request(
            "https://www.googleapis.com/youtube/v3/videos?"
            + urlencode({"part": "snippet,status,processingDetails", "id": video_id}),
            headers={"Authorization": f"Bearer {token}"},
        )
        data = json.loads(body) if status == 200 else {}
        items = data.get("items")
        if not isinstance(items, list) or len(items) != 1:
            raise DistributionOutboxError("YouTube authoritative readback is incomplete")
        item = items[0]
        if (
            item.get("id") != video_id
            or not set(tags).issubset(item.get("snippet", {}).get("tags", []))
            or item.get("snippet", {}).get("channelId") != channel_id
        ):
            raise DistributionOutboxError("YouTube media identity mismatch")
        metadata = {
            **item.get("snippet", {}),
            **item.get("status", {}),
            **item.get("processingDetails", {}),
        }
        return metadata.get("privacyStatus"), youtube_publish.video_readiness_problems(
            metadata, allow_public=True
        )

    privacy, readiness_problems = readback()
    # The observed ID is persisted before promotion and reused on redelivery.
    repository.record_verification(
        claim,
        provider="youtube",
        result="pending_provider",
        source="youtube_media_readback",
        provider_item_id=video_id,
        native_state=privacy,
    )
    if privacy not in {"public", "private", "unlisted"}:
        raise DistributionOutboxError("YouTube privacy readback is unknown")
    if readiness_problems:
        _pending(repository, claim, "youtube", video_id, privacy, "youtube_processing_not_ready")
        return
    if claim.read_only:
        if privacy != "public":
            _pending(repository, claim, "youtube", video_id, privacy, "youtube_media_readback")
            return
    else:
        playlist = youtube_playlist.resolve_playlist_id(config, leg["context"].get("locale"))
        if playlist and not youtube_playlist.playlist_contains_video(
            playlist, video_id, token, transport=http, raise_on_error=True
        ):

            def insert(authorize):
                result = youtube_playlist.add_video_to_playlist(
                    playlist, video_id, token, transport=_GuardedTransport(http, authorize)
                )
                return (video_id if result.succeeded else None), privacy

            mutate("youtube", "playlist_insert", insert, provider_id=video_id)
        if privacy != "public":

            def promote(authorize):
                result = youtube_publish.publish_video(
                    video_id, token, transport=_GuardedTransport(http, authorize)
                )
                return (video_id if result.succeeded else None), result.privacy_status

            mutate("youtube", "public_promotion", promote, provider_id=video_id)
        privacy, readiness_problems = readback()
    if readiness_problems:
        _pending(repository, claim, "youtube", video_id, privacy, "youtube_processing_not_ready")
        return
    if privacy == "public":
        repository.record_verification(
            claim,
            provider="youtube",
            result="externally_verified_public",
            source="youtube_videos_list",
            provider_item_id=video_id,
            native_state=privacy,
            proof=exact_verification_proof(
                repository.read(claim.outbox_id), provider_item_id=video_id
            ),
        )
    else:
        _pending(repository, claim, "youtube", video_id, privacy, "youtube_media_readback")


def _spotify(repository, claim, document, identity, config, path, language, validate, mutate):
    validate("spotify")
    show_id, sp_dc, sp_key = publish._get_credentials(language)
    leg = document["providers"]["spotify"]
    episode_id = _provider_id(leg)
    marker = f"Publication {document['publication_digest']}; MP4 {document['artifact']['sha256']}"
    title = f"Podcast {identity.week} [{document['publication_digest']}]"
    if not episode_id:
        if claim.read_only:
            repository.record_verification(
                claim,
                provider="spotify",
                result="publication_unknown",
                source="spotify_identity_readback_required",
            )
            return

        def upload(authorize):
            result = publish.upload_video_to_episode(
                path,
                title=title,
                description=marker,
                show_id=show_id,
                sp_dc=sp_dc,
                sp_key=sp_key,
                season_number=leg["context"].get("season_number"),
                episode_number=leg["context"].get("episode_number"),
                before_mutation=authorize,
                publication_storage=repository.storage,
                publication_identity_context=identity,
                require_reconciliation=True,
            )
            return (
                str(result.anchor_episode_id)
                if result.status == "draft" and not result.dry_run and result.anchor_episode_id
                else None,
                "draft",
            )

        episode_id = mutate("spotify", "create", upload)
    session = publish._build_session(sp_dc, sp_key, show_id)
    _station_id, user_id = publish._resolve_legacy_ids(session, show_id)

    def readback():
        validate("spotify")
        published, episode = publish._read_episode_overview(session, int(episode_id), user_id)
        if (
            published is None
            or not isinstance(episode, dict)
            or publish._episode_anchor_id(episode) != int(episode_id)
            or episode.get("title") != title
            or marker not in str(episode.get("description") or "")
        ):
            raise DistributionOutboxError("Spotify media identity readback is incomplete")
        return published

    published = readback()
    if not published and not claim.read_only:

        def promote(authorize):
            result = publish.promote_spotify_video_draft(
                int(episode_id),
                spotify_video_publish_mode=config.spotify_video_publish_mode,
                job_id=identity.accepted_job_id,
                run_id=identity.publish_run_id,
                show_id=show_id,
                sp_dc=sp_dc,
                sp_key=sp_key,
                before_mutation=authorize,
            )
            if result.terminal_state == "draft_gate_denied":
                return None, "draft"
            return (episode_id if result.is_published is True else None), "published"

        # A denied live flag is a real manual handoff, never an upload or
        # implicit permission to promote because the script was approved.
        if (
            config.spotify_video_publish_mode != "live"
            or not publish._spotify_video_allow_live_publish()
        ):
            repository.record_verification(
                claim,
                provider="spotify",
                result="manual_handoff_required",
                source="spotify_live_gate_denied",
                provider_item_id=episode_id,
                native_state="draft",
            )
            return
        mutate("spotify", "public_promotion", promote, provider_id=episode_id)
        published = readback()
    if published:
        repository.record_verification(
            claim,
            provider="spotify",
            result="externally_verified_public",
            source="spotify_overview_readback",
            provider_item_id=episode_id,
            native_state="published",
            proof=exact_verification_proof(
                repository.read(claim.outbox_id), provider_item_id=episode_id
            ),
        )
    else:
        _pending(repository, claim, "spotify", episode_id, "draft", "spotify_overview_readback")
