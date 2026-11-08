"""Operation-aware dispatch of separately reviewed, immutable private video."""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from podcaster import publish
from podcaster.distribution_outbox import (
    ArtifactReference,
    DistributionOutboxError,
    MutationIntentExpiredError,
    exact_verification_proof,
    media_review_allows_distribution,
    outbox_id_for,
    verify_artifact,
)
from podcaster.orchestration import load_manifest
from podcaster.publication_state import publication_identity
from podcaster.review import is_exact_script_job, validate_private_media_inputs
from podcaster.video import distribution, youtube_playlist, youtube_publish
from podcaster.video.owner_release import (
    owner_release_dispatch,
    owner_release_matches_selected,
)
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


def _file_version(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def dispatch_reviewed_video(repository, claim, document, *, config=None, transport=None):
    """Use existing provider adapters; never admit legacy or unreviewed outboxes."""
    from podcaster.video.recovery import RecoveryDenied, private_original

    identity_data = document.get("publication_identity")
    original_job = identity_data.get("accepted_job_id") if isinstance(identity_data, dict) else None
    private_release = private_original(original_job)
    if private_release and (
        repository is None or not owner_release_matches_selected(document, repository.storage)
    ):
        raise RecoveryDenied("private recovery originals have no provider dispatch transition")
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
    metadata = manifest["generation"]["video_runner"].get("publication_metadata")
    if (
        not isinstance(metadata, dict)
        or set(metadata) != {"title", "description"}
        or not all(isinstance(value, str) and value.strip() for value in metadata.values())
    ):
        raise DistributionOutboxError("reviewed publication metadata is missing")
    metadata_sha256 = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path = None
    local_version = None
    admitted_audio = None
    admitted_audio_sha256 = None

    def validate(
        provider, operation=None, *, verify_media=True, provider_io=True, require_expiry=False
    ):
        nonlocal admitted_audio, admitted_audio_sha256
        if provider_io and provider == "spotify" and not publish._spotify_reconcile_enabled():
            raise DistributionOutboxError("Spotify outbox requires strict reconcile-before-create")
        current = repository.authorize_dispatch(
            claim, provider=provider, operation=operation, provider_io=provider_io
        )
        latest = load_manifest(repository.storage, job_id)
        if not is_exact_script_job(latest):
            raise DistributionOutboxError("canonical synthesis proof is required")
        if verify_media:
            audio = validate_private_media_inputs(latest, repository.storage, job_id)
            admitted_audio = audio
            admitted_audio_sha256 = hashlib.sha256(audio.content).hexdigest()
        else:
            # The upload reads the verified local copy, not the blob again.
            # Retain live fencing, authorization, manifest and local-version
            # checks on every chunk/retry; rehash media at operation boundaries.
            if latest != manifest or admitted_audio is None:
                raise DistributionOutboxError("reviewed manifest changed during provider operation")
            audio = admitted_audio
        binding_checks = {
            "publication_identity": asdict(identity) == current["publication_identity"],
            "manifest_identity": publication_identity(latest, job_id, identity.publish_run_id)
            == identity,
            "outbox_identity": outbox_id_for(
                identity, media_kind="video", provider_objectives=current["provider_objectives"]
            )
            == claim.outbox_id,
            "artifact": current["artifact"] == document["artifact"],
            "canonical_artifact": current["canonical_artifact"] == document["canonical_artifact"],
            "publication_digest": current["publication_digest"] == document["publication_digest"],
            "media_admission": media_review_allows_distribution(current),
            "selected_owner_artifact": not private_release
            or not verify_media
            or owner_release_matches_selected(current, repository.storage),
            "manifest_outbox": latest["generation"]["video_runner"]["distribution"].get("outbox_id")
            == claim.outbox_id,
            "provider_runtime": current["providers"][provider]["context"].get("runtime_sha256")
            == distribution.provider_runtime_sha256(provider, config, language),
            "audio_path": current["providers"][provider]["context"].get("synthesis_audio_path")
            == audio.path,
            "audio_digest": current["providers"][provider]["context"].get("synthesis_audio_sha256")
            == admitted_audio_sha256,
            "publication_metadata_digest": current["providers"][provider]["context"].get(
                "publication_metadata_sha256"
            )
            == metadata_sha256,
            "publication_metadata": latest["generation"]["video_runner"].get("publication_metadata")
            == metadata,
            "expiry": (
                (
                    (operation is None and not require_expiry)
                    or (
                        datetime.fromisoformat(
                            current["providers"][provider]["verification_horizon_at"].replace(
                                "Z", "+00:00"
                            )
                        )
                        > repository.now()
                    )
                )
                if private_release
                else (
                    datetime.fromisoformat(latest["expires_at"].replace("Z", "+00:00"))
                    > repository.now()
                )
            ),
        }
        failed_bindings = [name for name, valid in binding_checks.items() if not valid]
        if failed_bindings:
            raise DistributionOutboxError(
                "reviewed video or provider binding changed: " + ", ".join(failed_bindings)
            )
        if verify_media:
            verify_artifact(repository.storage, artifact)
            if path is not None and _file_digest(path) != artifact.sha256:
                raise DistributionOutboxError("local reviewed MP4 changed")
        if path is not None and local_version is not None and _file_version(path) != local_version:
            raise DistributionOutboxError("local reviewed MP4 version changed")
        return current

    # Validate *all* destinations before the first provider call, not midway
    # through a multi-provider publication.
    active_providers = []
    for provider in document["providers"]:
        current = validate(provider, provider_io=False)
        leg = current["providers"][provider]
        if leg["result"] in {
            "externally_verified_public",
            "manual_handoff_required",
            "failed_terminal",
            "poisoned",
            "identity_conflict",
        }:
            continue
        horizon = leg.get("verification_horizon_at")
        if (
            not private_release
            and (
                not horizon
                or datetime.fromisoformat(horizon.replace("Z", "+00:00")) <= repository.now()
            )
        ) or leg["verification_attempt"] >= leg["verification_budget"]:
            repository.schedule_reconciliation(claim, provider=provider, due_at=repository.now())
            continue
        active_providers.append(provider)
    if not active_providers:
        return "reviewed_video_dispatched"
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
        local_version = _file_version(path)

        def mutate(provider, operation, call, *, provider_id=None):
            boundary_artifact_verified = False
            state = validate(provider, verify_media=False)
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
            owner_release_mutation = private_release and provider == "youtube"
            deadline = _owner_release_deadline(state) if owner_release_mutation else None
            if owner_release_mutation and _owner_release_horizon_expired(state, repository):
                raise _OwnerReleaseHorizonExpired(provider_item_id=provider_id)
            try:
                repository.persist_intent(
                    claim,
                    provider=provider,
                    operation=operation,
                    expected_provider_item_id=provider_id,
                    precondition_fingerprint=document["publication_digest"],
                    not_after=deadline,
                )
            except MutationIntentExpiredError as exc:
                if owner_release_mutation:
                    raise _OwnerReleaseHorizonExpired(provider_item_id=provider_id) from exc
                raise
            state = validate(provider, verify_media=False)
            if owner_release_mutation and _owner_release_horizon_expired(state, repository):
                raise _OwnerReleaseHorizonExpired(provider_item_id=provider_id)

            def authorize():
                nonlocal boundary_artifact_verified
                try:
                    verify_media = operation == "draft_upload" and not boundary_artifact_verified
                    current = validate(
                        provider,
                        operation,
                        verify_media=verify_media,
                    )
                    boundary_artifact_verified = boundary_artifact_verified or verify_media
                    if owner_release_mutation:
                        deadline = _owner_release_deadline(current)
                        if deadline is None or deadline - repository.now() <= timedelta(
                            seconds=330
                        ):
                            raise _OwnerReleaseHorizonExpired(provider_item_id=provider_id)
                except DistributionOutboxError as exc:
                    latest = repository.read(claim.outbox_id) or state
                    if owner_release_mutation and _owner_release_horizon_expired(
                        latest, repository
                    ):
                        raise _OwnerReleaseHorizonExpired(provider_item_id=provider_id) from exc
                    raise

            try:
                state = validate(
                    provider,
                    verify_media=False,
                    require_expiry=owner_release_mutation,
                )
                try:
                    repository.consume_intent(
                        claim,
                        provider=provider,
                        provider_timeout_seconds=300 if owner_release_mutation else 120,
                        receipt_margin_seconds=30,
                        not_after=(
                            _owner_release_deadline(state) if owner_release_mutation else None
                        ),
                    )
                except MutationIntentExpiredError as exc:
                    if owner_release_mutation:
                        raise _OwnerReleaseHorizonExpired(provider_item_id=provider_id) from exc
                    raise
                state = validate(provider, operation, verify_media=False)
                item_id, native_state = call(authorize)
                if not item_id:
                    raise DistributionOutboxError("provider did not return a confirmed identity")
                # Persist the provider's confirmed identity before any post-call work;
                # the authorization horizon may expire while the response is in flight.
                repository.record_receipt(
                    claim,
                    provider=provider,
                    transport_class="accepted",
                    provider_item_id=str(item_id),
                    native_state=native_state,
                )
                return str(item_id)
            except _OwnerReleaseHorizonExpired:
                intent_state = repository.read(claim.outbox_id) or {}
                intent = intent_state.get("providers", {}).get(provider, {}).get("intent") or {}
                if intent.get("consumed_at"):
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

        for provider in active_providers:
            state = validate(provider)
            if state["providers"][provider]["result"] == "externally_verified_public":
                continue
            leg = state["providers"][provider]
            token = leg.get("active_schedule_token")
            if token and leg.get("active_schedule_source") == "worker":
                due = leg.get("next_reconcile_at")
                if (
                    due is None
                    or datetime.fromisoformat(due.replace("Z", "+00:00")) > repository.now()
                ):
                    continue
                repository.consume_schedule_token(claim, provider=provider, token=token)
            if provider == "youtube" and config.youtube_enabled:
                scope = (
                    owner_release_dispatch(document, repository.storage, path)
                    if private_release
                    else nullcontext()
                )
                with scope:
                    try:
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
                            metadata,
                        )
                    except _OwnerReleaseHorizonExpired as exc:
                        _owner_release_expired_handoff(
                            repository,
                            claim,
                            source="owner_release_horizon_expired_before_mutation",
                            provider_item_id=exc.provider_item_id,
                            native_state=exc.native_state,
                        )
            elif provider == "spotify" and config.spotify_upload_enabled:
                _spotify(
                    repository,
                    claim,
                    state,
                    identity,
                    config,
                    path,
                    language,
                    validate,
                    mutate,
                    metadata,
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


def _owner_release_horizon_expired(document, repository):
    deadline = _owner_release_deadline(document)
    if deadline is None:
        return False
    return deadline <= repository.now()


def _owner_release_deadline(document):
    if not document.get("owner_release_authorization"):
        return None
    try:
        return datetime.fromisoformat(
            document["providers"]["youtube"]["verification_horizon_at"].replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (AttributeError, KeyError, TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _owner_release_expired_handoff(
    repository,
    claim,
    *,
    source,
    provider_item_id=None,
    native_state=None,
):
    repository.record_verification(
        claim,
        provider="youtube",
        result="manual_handoff_required",
        source=source,
        provider_item_id=provider_item_id,
        native_state=native_state,
        exhaustion_reason="provider_verification_horizon_exhausted",
    )


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


class _OwnerReleaseHorizonExpired(DistributionOutboxError):
    def __init__(self, *, provider_item_id=None, native_state="unknown"):
        self.provider_item_id = provider_item_id
        self.native_state = native_state
        super().__init__("owner release verification horizon is exhausted")


def _youtube(repository, claim, document, identity, config, path, validate, mutate, http, metadata):
    leg = document["providers"]["youtube"]
    video_id = _provider_id(leg)
    if video_id is None and _owner_release_horizon_expired(document, repository):
        _owner_release_expired_handoff(
            repository,
            claim,
            source="owner_release_horizon_expired_before_identity_readback",
            native_state="unknown",
        )
        return
    validate("youtube")
    token = distribution._get_youtube_access_token(config, http)
    try:
        if video_id is None:

            def authorize_identity_readback():
                validate("youtube")
                if _owner_release_horizon_expired(document, repository):
                    raise _OwnerReleaseHorizonExpired(native_state="unknown")

            identity_transport = _GuardedTransport(http, authorize_identity_readback)
        else:
            identity_transport = http
        status, body = identity_transport.request(
            "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
            headers={"Authorization": f"Bearer {token}"},
        )
    except _OwnerReleaseHorizonExpired:
        _owner_release_expired_handoff(
            repository,
            claim,
            source="owner_release_horizon_expired_before_identity_readback",
            native_state="unknown",
        )
        return
    channels = json.loads(body).get("items") if status == 200 else None
    if not isinstance(channels, list) or len(channels) != 1 or not channels[0].get("id"):
        raise DistributionOutboxError("YouTube owner identity readback is incomplete")
    channel_id = channels[0]["id"]
    tag = youtube_identity_tag(identity)
    provider_metadata = distribution.youtube_publication_metadata(
        metadata["title"],
        metadata["description"],
        publication_marker=f"Publication {document['publication_digest']}",
    )
    title, description = provider_metadata["title"], provider_metadata["description"]
    tags = [
        tag,
        f"sqmedia-{document['artifact']['sha256']}",
        f"sqreview-{document['publication_digest']}",
    ]
    if not video_id:
        try:
            found = reconcile_youtube_upload(
                tag,
                token,
                _GuardedTransport(http, authorize_identity_readback),
                not_before=datetime.fromisoformat(document["created_at"].replace("Z", "+00:00")),
            )
        except _OwnerReleaseHorizonExpired:
            _owner_release_expired_handoff(
                repository,
                claim,
                source="owner_release_horizon_expired_before_identity_readback",
                native_state="unknown",
            )
            return
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
        elif _owner_release_horizon_expired(document, repository):
            _owner_release_expired_handoff(
                repository,
                claim,
                source="owner_release_horizon_expired_before_upload",
                native_state="absent",
            )
            return
        else:

            def upload(authorize):
                video, _url = distribution.upload_to_youtube(
                    path,
                    title,
                    description,
                    config,
                    tags=tags,
                    identity_tag=tag,
                    job_id=identity.accepted_job_id,
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
            or item.get("snippet", {}).get("title") != title
            or item.get("snippet", {}).get("description") != description
        ):
            raise DistributionOutboxError("YouTube media identity mismatch")
        provider_metadata = {
            **item.get("snippet", {}),
            **item.get("status", {}),
            **item.get("processingDetails", {}),
        }
        return provider_metadata.get("privacyStatus"), youtube_publish.video_readiness_problems(
            provider_metadata, allow_public=True
        )

    privacy, readiness_problems = readback()
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
        owner_release_expired = _owner_release_horizon_expired(document, repository)
        if owner_release_expired and privacy != "public":
            _owner_release_expired_handoff(
                repository,
                claim,
                source="owner_release_horizon_expired_before_publication",
                provider_item_id=video_id,
                native_state=privacy,
            )
            return
        if privacy != "public":
            # Persist the observed ID before promotion, counting this readback
            # once. A final permitted private poll cannot admit more provider I/O.
            repository.record_verification(
                claim,
                provider="youtube",
                result="pending_provider",
                source="youtube_media_readback",
                provider_item_id=video_id,
                native_state=privacy,
            )
            latest_leg = repository.read(claim.outbox_id)["providers"]["youtube"]
            if latest_leg["verification_attempt"] >= latest_leg["verification_budget"]:
                repository.schedule_reconciliation(
                    claim, provider="youtube", due_at=repository.now() + timedelta(minutes=5)
                )
                return
        if not owner_release_expired:
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


def _spotify(
    repository, claim, document, identity, config, path, language, validate, mutate, metadata
):
    validate("spotify")
    show_id, sp_dc, sp_key = publish._get_credentials(language)
    leg = document["providers"]["spotify"]
    episode_id = _provider_id(leg)
    marker = f"Publication {document['publication_digest']}; MP4 {document['artifact']['sha256']}"
    title = f"{metadata['title']} [{document['publication_digest']}]"
    description = f"{metadata['description']}\n\n{marker}"
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
                description=description,
                show_id=show_id,
                sp_dc=sp_dc,
                sp_key=sp_key,
                season_number=leg["context"].get("season_number"),
                episode_number=leg["context"].get("episode_number"),
                before_mutation=authorize,
                publication_storage=repository.storage,
                publication_identity_context=identity,
                job_id=identity.accepted_job_id,
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
            or episode.get("description") != description
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
