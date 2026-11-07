"""Exact-identity owner authorization for the W44/W45 private video originals."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator, Mapping

from podcaster.publication_state import PublicationIdentity

OWNER_RELEASE_SCHEMA = "owner-authorized-video-release-v1"
OWNER_RELEASE_OWNER = "jmservera"
OWNER_RELEASE_AUTHORIZED_AT = "2026-11-07T20:08:59Z"
W45_MP4_SHA256 = "bb3eefa8a09b2a0d5175dcc658f7e6e5085982e374c8e1ff4f348678d00a67ec"

_IDENTITIES = {
    "podcast-2026-W44-00bc06dfb6e2": {
        "week": "2026-W44",
        "publish_run_id": "40269058395",
        "article_sha256": "2a34575cd7be0d0a6ec36b4a808a1909ae7f793d00a8511da464ec063cf2bfa0",
        "manifest_sha256": "5c1fc333deb75ea2bfff990c1222f97a12acb2c3234e19df26717a27e8ff6f56",
        "mp4_sha256": None,
    },
    "podcast-2026-W45-9798e93edc06": {
        "week": "2026-W45",
        "publish_run_id": "41395629030",
        "article_sha256": "fc4023679d8b2ba90481f783ebb9c57e61e059db3790c9c9d3c4a996b0242e34",
        "manifest_sha256": "a64c9c664a510b94207a566c92c3cc0465af241a5304109b845d1317284f5d3b",
        "mp4_sha256": W45_MP4_SHA256,
    },
}
_ACTIVE_RELEASE: ContextVar[Mapping[str, str] | None] = ContextVar(
    "owner_authorized_video_release", default=None
)


def _policy(identity: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]] | None:
    job_id = identity.get("accepted_job_id")
    policy = _IDENTITIES.get(job_id) if isinstance(job_id, str) else None
    if policy is None:
        return None
    expected = {"accepted_job_id": job_id, **policy}
    expected.pop("mp4_sha256")
    actual = {key: identity.get(key) for key in expected}
    return (job_id, policy) if actual == expected else None


def owner_release_evidence(
    identity: PublicationIdentity | Mapping[str, Any],
    selected: Mapping[str, Any],
    artifact: Mapping[str, Any],
    canonical_artifact: Mapping[str, Any],
    *,
    publication_metadata_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Build the separate owner-authorization record for an exact selected artifact."""
    identity_data = (
        asdict(identity) if isinstance(identity, PublicationIdentity) else dict(identity)
    )
    match = _policy(identity_data)
    if match is None:
        return None
    job_id, policy = match
    selected_identity = selected.get("identity")
    digest = selected.get("mp4_sha256")
    size = selected.get("size_bytes")
    if (
        selected.get("job_id") != job_id
        or selected_identity != identity_data
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or type(size) is not int
        or size < 1024
        or (policy["mp4_sha256"] is not None and digest != policy["mp4_sha256"])
        or artifact.get("media_kind") != "video"
        or artifact.get("sha256") != digest
        or artifact.get("size_bytes") != size
        or canonical_artifact.get("selected") is not True
        or canonical_artifact.get("sha256") != digest
        or not isinstance(selected.get("attempt_id"), str)
        or not isinstance(selected.get("proof_sha256"), str)
        or (
            publication_metadata_sha256 is not None
            and (
                len(publication_metadata_sha256) != 64
                or any(
                    character not in "0123456789abcdef" for character in publication_metadata_sha256
                )
            )
        )
    ):
        return None
    evidence = {
        "schema_version": OWNER_RELEASE_SCHEMA,
        "source": "owner_authorized_release_exception",
        "owner": OWNER_RELEASE_OWNER,
        "authorized_at": OWNER_RELEASE_AUTHORIZED_AT,
        "publication_identity": identity_data,
        "selected_artifact": {
            "attempt_id": selected["attempt_id"],
            "epoch_id": selected.get("epoch_id"),
            "proof_sha256": selected["proof_sha256"],
            "mp4_sha256": digest,
            "size_bytes": size,
        },
        "canonical_artifact": {
            "artifact_id": canonical_artifact.get("artifact_id"),
            "selection_version": canonical_artifact.get("selection_version"),
            "selected": True,
            "sha256": digest,
        },
    }
    if publication_metadata_sha256 is not None:
        evidence["publication_metadata_sha256"] = publication_metadata_sha256
    return evidence


def _publication_metadata_digest(document: Mapping[str, Any]) -> str | None:
    providers = document.get("providers")
    youtube = providers.get("youtube") if isinstance(providers, Mapping) else None
    context = youtube.get("context") if isinstance(youtube, Mapping) else None
    digest = context.get("publication_metadata_sha256") if isinstance(context, Mapping) else None
    return digest if isinstance(digest, str) else None


def _file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def owner_release_record_is_valid(document: Mapping[str, Any]) -> bool:
    """Validate persisted owner evidence against the exact outbox identity and artifact."""
    identity = document.get("publication_identity")
    evidence = document.get("owner_release_authorization")
    selected = evidence.get("selected_artifact") if isinstance(evidence, Mapping) else None
    artifact = document.get("artifact")
    canonical = document.get("canonical_artifact")
    if not all(isinstance(value, Mapping) for value in (identity, selected, artifact, canonical)):
        return False
    metadata_digest = _publication_metadata_digest(document)
    providers = document.get("providers")
    if providers is not None and (
        not isinstance(providers, Mapping)
        or set(providers) != {"youtube"}
        or metadata_digest is None
        or len(metadata_digest) != 64
        or any(character not in "0123456789abcdef" for character in metadata_digest)
    ):
        return False
    candidate = {
        "job_id": identity.get("accepted_job_id"),
        "identity": dict(identity),
        **dict(selected),
    }
    expected = owner_release_evidence(
        identity,
        candidate,
        artifact,
        canonical,
        publication_metadata_sha256=metadata_digest,
    )
    return expected is not None and evidence == expected


def owner_release_matches_selected(document: Mapping[str, Any], storage: Any) -> bool:
    """Revalidate selected private bytes and manifest binding from durable storage."""
    from podcaster.video.recovery import read_selected

    identity = document.get("publication_identity")
    if not isinstance(identity, Mapping):
        return False
    try:
        selected = read_selected(storage, str(identity["accepted_job_id"]))
    except (KeyError, TypeError, ValueError):
        return False
    expected = owner_release_evidence(
        identity,
        selected,
        document.get("artifact", {}),
        document.get("canonical_artifact", {}),
        publication_metadata_sha256=_publication_metadata_digest(document),
    )
    return (
        expected is not None
        and expected == document.get("owner_release_authorization")
        and owner_release_record_is_valid(document)
    )


@contextmanager
def owner_release_dispatch(
    document: Mapping[str, Any], storage: Any, video_path: Path
) -> Iterator[None]:
    """Scope adapter admission to the selected bytes staged for normal dispatch."""
    if not owner_release_matches_selected(document, storage):
        raise ValueError("owner release authorization or selected artifact is invalid")
    identity = document["publication_identity"]
    selected = document["owner_release_authorization"]["selected_artifact"]
    artifact = document["artifact"]
    path = Path(video_path)
    if (
        artifact.get("sha256") != selected.get("mp4_sha256")
        or artifact.get("size_bytes") != selected.get("size_bytes")
        or not path.is_file()
    ):
        raise ValueError("owner release artifact does not match the selected media")
    digest, size = _file_sha256(path)
    if digest != selected["mp4_sha256"] or size != selected["size_bytes"]:
        raise ValueError("owner release staged media does not match the selected bytes")
    token: Token[Mapping[str, str] | None] = _ACTIVE_RELEASE.set(
        {
            "accepted_job_id": str(identity["accepted_job_id"]),
            "artifact_sha256": str(selected["mp4_sha256"]),
            "video_path": str(path.resolve()),
        }
    )
    try:
        yield
    finally:
        _ACTIVE_RELEASE.reset(token)


def owner_release_active(job_id: str, video_path: Path) -> bool:
    """Return whether this exact staged artifact is inside owner-release dispatch."""
    dispatch = _ACTIVE_RELEASE.get()
    path = Path(video_path)
    if (
        not isinstance(dispatch, Mapping)
        or dispatch.get("accepted_job_id") != job_id
        or dispatch.get("video_path") != str(path.resolve())
        or not path.is_file()
    ):
        return False
    try:
        return _file_sha256(path)[0] == dispatch.get("artifact_sha256")
    except OSError:
        return False
