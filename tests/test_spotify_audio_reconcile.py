"""#679: exact Spotify audio draft reconciliation after an ambiguous create."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import podcaster.publish as pub
from podcaster.config import SpotifyPublishConfig
from podcaster.publication_state import PublicationIdentity

IDENTITY = PublicationIdentity("job-1", "2026-W37", "run-1", "a" * 64, "b" * 64)


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def get_bytes(self, path):
        return self.data.get(path)

    def update_bytes(self, path, content_type, update):
        self.data[path] = update(self.data.get(path))


def _records(storage: MemoryStorage) -> list[dict]:
    raw = storage.get_bytes(f"publication-evidence/{IDENTITY.accepted_job_id}.json")
    assert raw is not None
    return json.loads(raw.decode())["records"]


def _draft(episode_id: int, title: str | None = None, **state) -> dict:
    return {"episodeId": episode_id, "title": title, **(state or {"isDraft": True})}


def _published(episode_id: int, title: str) -> dict:
    return {"episodeId": episode_id, "title": title, "isDraft": False}


def _listing(*episodes: dict) -> dict:
    return {"episodes": list(episodes)}


@pytest.fixture
def audio_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SPOTIFY_SHOW_ID", "show-1")
    monkeypatch.setenv("SP_DC", "dc-secret")
    monkeypatch.setenv("SP_KEY", "key-secret")
    monkeypatch.delenv("PODCASTER_SPOTIFY_RECONCILE", raising=False)
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 64)

    mocks = {
        # Built per test: other suites reload podcaster.publish, which replaces its classes.
        "create": MagicMock(side_effect=pub.SpotifyDraftCreateAmbiguousError("response lost")),
        "listing": MagicMock(),
        "sleep": MagicMock(),
        "upload_url": MagicMock(return_value=("signed", "up-1")),
        "upload": MagicMock(return_value="etag"),
        "process": MagicMock(return_value=None),
        "metadata": MagicMock(return_value=None),
    }
    monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
    monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("station-1", "user-1"))
    monkeypatch.setattr(pub, "_create_episode", mocks["create"])
    monkeypatch.setattr(pub, "_fetch_episode_listing", mocks["listing"])
    monkeypatch.setattr(pub.time, "sleep", mocks["sleep"])
    monkeypatch.setattr(pub, "_get_upload_url", mocks["upload_url"])
    monkeypatch.setattr(pub, "_upload_audio", mocks["upload"])
    monkeypatch.setattr(pub, "_process_upload", mocks["process"])
    monkeypatch.setattr(pub, "_set_metadata", mocks["metadata"])
    mocks["mp3"] = mp3
    return mocks


def _publish(mocks, storage):
    return pub.publish_episode(
        mocks["mp3"],
        "W37 Title",
        "<p>notes</p>",
        spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
        publication_storage=storage,
        publication_identity_context=IDENTITY,
    )


def _assert_fail_closed(result, mocks, *, reason: str):
    assert result.status == "failed"
    assert result.outcome == "publication_unknown"
    assert result.anchor_episode_id is None
    assert result.details["retry_blocked"] is True
    assert result.details["code"] == "ambiguous_create"
    assert result.details["create_verification"]["reason"].startswith(reason)
    mocks["create"].assert_called_once()
    mocks["upload_url"].assert_not_called()
    mocks["metadata"].assert_not_called()


def test_exact_new_untitled_draft_is_adopted_and_resumes_upload(audio_env):
    storage = MemoryStorage()
    before = _listing(_published(10, "W36 Title"))
    after = _listing(_published(10, "W36 Title"), _draft(11))
    audio_env["listing"].side_effect = [before, after]

    result = _publish(audio_env, storage)

    assert result.status == "draft"
    assert result.outcome == "draft_created"
    assert result.anchor_episode_id == 11
    audio_env["create"].assert_called_once()
    assert audio_env["listing"].call_count == 2
    audio_env["sleep"].assert_not_called()
    assert audio_env["upload_url"].call_args.args[1] == 11
    audio_env["process"].assert_called_once()
    assert audio_env["metadata"].call_args.args[1] == 11

    records = _records(storage)
    create_record = next(r for r in records if r["operation"] == "create_episode")
    assert create_record["provider_artifact_id"] == "11"
    assert create_record["code"] == "provider_artifact_reconciled"
    assert create_record["details"]["reconciled_from"] == "ambiguous_create"
    assert create_record["job_id"] == IDENTITY.accepted_job_id
    assert create_record["publish_run_id"] == IDENTITY.publish_run_id
    assert create_record["article_sha256"] == IDENTITY.article_sha256
    serialized = json.dumps(records)
    assert "dc-secret" not in serialized and "key-secret" not in serialized


def test_settling_create_is_adopted_on_bounded_second_read(audio_env):
    audio_env["listing"].side_effect = [_listing(), _listing(), _listing(_draft(21))]

    result = _publish(audio_env, MemoryStorage())

    assert result.anchor_episode_id == 21
    assert result.outcome == "draft_created"
    audio_env["create"].assert_called_once()
    audio_env["sleep"].assert_called_once_with(pub._AMBIGUOUS_CREATE_SETTLE_SECONDS)


def test_no_new_draft_after_bounded_reads_never_sends_second_post(audio_env):
    audio_env["listing"].side_effect = [_listing(), _listing(), _listing()]

    result = _publish(audio_env, MemoryStorage())

    _assert_fail_closed(result, audio_env, reason="no_new_draft_observed")
    assert audio_env["listing"].call_count == 1 + pub._AMBIGUOUS_CREATE_READS
    assert result.details["create_verification"]["reads"] == pub._AMBIGUOUS_CREATE_READS


def test_multiple_candidates_remain_unknown(audio_env):
    audio_env["listing"].side_effect = [_listing(), _listing(_draft(31), _draft(32))]

    result = _publish(audio_env, MemoryStorage())

    _assert_fail_closed(result, audio_env, reason="multiple_or_unclassifiable_candidates")
    assert result.details["create_verification"]["candidates"] == [31, 32]
    audio_env["sleep"].assert_not_called()


def test_contradictory_candidate_state_remains_unknown(audio_env):
    contradictory = _draft(41, isDraft=True, status="published")
    audio_env["listing"].side_effect = [_listing(), _listing(_draft(42), contradictory)]

    result = _publish(audio_env, MemoryStorage())

    _assert_fail_closed(result, audio_env, reason="multiple_or_unclassifiable_candidates")
    assert result.details["create_verification"]["unclassifiable"] == 1


def test_candidate_without_any_title_field_is_unclassifiable(audio_env):
    no_title_field = {"episodeId": 45, "isDraft": True}
    audio_env["listing"].side_effect = [_listing(), _listing(no_title_field)]

    result = _publish(audio_env, MemoryStorage())

    _assert_fail_closed(result, audio_env, reason="multiple_or_unclassifiable_candidates")
    assert result.details["create_verification"]["candidates"] == []
    assert result.details["create_verification"]["unclassifiable"] == 1


def test_new_draft_matching_title_alone_is_not_adopted(audio_env):
    audio_env["listing"].side_effect = [
        _listing(),
        _listing(_draft(51, "W37 Title")),
        _listing(_draft(51, "W37 Title")),
    ]

    result = _publish(audio_env, MemoryStorage())

    _assert_fail_closed(result, audio_env, reason="no_new_draft_observed")


@pytest.mark.parametrize(
    "pre_create",
    [
        pytest.param("reconcile_error", id="unusable"),
        pytest.param(_listing({"title": None, "isDraft": True}), id="incomplete"),
    ],
)
def test_untrusted_pre_create_snapshot_never_adopts(audio_env, pre_create):
    if pre_create == "reconcile_error":
        pre_create = pub.SpotifyDraftReconcileError("unreadable listing")
    audio_env["listing"].side_effect = [pre_create, _listing(_draft(61))]

    result = _publish(audio_env, MemoryStorage())

    _assert_fail_closed(result, audio_env, reason="pre_create_snapshot_unusable")
    assert audio_env["listing"].call_count == 1


def test_reconcile_disabled_skips_snapshot_and_stays_unknown(audio_env, monkeypatch):
    monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")

    result = _publish(audio_env, MemoryStorage())

    _assert_fail_closed(result, audio_env, reason="pre_create_snapshot_unusable")
    audio_env["listing"].assert_not_called()


def test_credential_expiry_during_verification_stays_retry_blocked(audio_env):
    audio_env["listing"].side_effect = [
        _listing(),
        pub.SpotifyCredentialExpiredError("401 during verification"),
    ]

    result = _publish(audio_env, MemoryStorage())

    _assert_fail_closed(result, audio_env, reason="verification_read_failed")
    assert "credentials_expired" not in result.details


def test_unknown_outcome_blocks_redelivery_mutation(audio_env):
    storage = MemoryStorage()
    audio_env["listing"].side_effect = [_listing(), _listing(), _listing()]
    first = _publish(audio_env, storage)
    assert first.outcome == "publication_unknown"

    audio_env["create"].reset_mock()
    audio_env["listing"].reset_mock()
    second = _publish(audio_env, storage)

    assert second.outcome == "publication_unknown"
    assert second.details["retry_blocked"] is True
    audio_env["create"].assert_not_called()
    audio_env["listing"].assert_not_called()


@pytest.mark.parametrize("step", ["process", "metadata"])
def test_post_adoption_ambiguity_never_repeats_mutation(audio_env, step):
    audio_env["listing"].side_effect = [_listing(), _listing(_draft(71))]
    audio_env[step].side_effect = pub.SpotifyPublishError(f"{step} timed out")

    result = _publish(audio_env, MemoryStorage())

    assert result.status == "failed"
    assert result.anchor_episode_id == 71
    assert result.outcome in {"publication_unknown", "uploaded"}
    assert result.details["retry_blocked"] is True
    audio_env["create"].assert_called_once()
    audio_env["process"].assert_called_once()
    assert audio_env["metadata"].call_count == (1 if step == "metadata" else 0)


def test_unambiguous_create_keeps_original_evidence_code(audio_env):
    storage = MemoryStorage()
    audio_env["create"].side_effect = None
    audio_env["create"].return_value = 81
    audio_env["listing"].side_effect = [_listing()]

    result = _publish(audio_env, storage)

    assert result.anchor_episode_id == 81
    assert audio_env["listing"].call_count == 1
    create_record = next(r for r in _records(storage) if r["operation"] == "create_episode")
    assert create_record["code"] == "provider_artifact_created"
    assert "reconciled_from" not in create_record.get("details", {})
