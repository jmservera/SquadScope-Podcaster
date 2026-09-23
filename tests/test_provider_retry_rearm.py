from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from podcaster import provider_retry_rearm as rearm
from podcaster.provider_retry_rearm import (
    EXIT_CANDIDATE,
    EXIT_OK,
    EXIT_REFUSED,
    REARM_OPERATION,
    YouTubeAbsenceProver,
    main,
)
from podcaster.publication_state import (
    PUBLICATION_UNKNOWN,
    PublicationStateError,
    append_evidence,
    claim_evidence,
    publication_identity,
    read_evidence,
    retry_is_blocked,
)

JOB_ID = "podcast-2026-W39-abc"
TITLE = "Why Agents Ship Matters for AI | W39"
TOKEN = "ya29.never-print-me"
INTENT_AT = datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def get_bytes(self, path):
        return self.data.get(path)

    def update_bytes(self, path, content_type, update):
        self.data[path] = update(self.data.get(path))


def _manifest(runner_status="failed", youtube_id=None, title=TITLE):
    return {
        "generation": {
            "video_runner": {
                "status": runner_status,
                "distribution": {"youtube_id": youtube_id},
            }
        },
        "job_id": JOB_ID,
        "request": {
            "week": "2026-W39",
            "publish_run_id": "35561779454",
            "article_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "article_title": title,
        },
        "lifecycle": {"transitions": [{"to": "accepted"}]},
    }


def _identity():
    return publication_identity(_manifest(), JOB_ID, "")


def _storage(*, claim=True, claim_at=INTENT_AT, **manifest_kwargs):
    storage = MemoryStorage()
    storage.data[f"jobs/{JOB_ID}/manifest.json"] = json.dumps(_manifest(**manifest_kwargs)).encode()
    append_evidence(
        storage,
        _identity(),
        platform="spotify",
        media_kind="audio",
        operation="create_episode",
        outcome="draft_created",
        provider_artifact_id="126212203",
    )
    if claim:
        claim_evidence(
            storage,
            _identity(),
            platform="youtube",
            media_kind="video",
            operation="upload_intent",
            at=claim_at,
        )
    return storage


def _video(video_id, title, published="2026-09-01T10:00:00Z", privacy="public", upload="processed"):
    return {
        "id": video_id,
        "snippet": {"title": title, "description": "desc", "publishedAt": published},
        "status": {"privacyStatus": privacy, "uploadStatus": upload},
    }


class FakeYouTube:
    def __init__(self, videos, *, page_size=2, fail=None, total_delta=0, drop_ids=()):
        self.videos = videos
        self.page_size = page_size
        self.fail = fail or {}
        self.total_delta = total_delta
        self.drop_ids = set(drop_ids)
        self.calls = []

    def request(self, url, *, method="GET", headers=None, data=None):
        assert method == "GET"
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        parsed = urlparse(url)
        endpoint = parsed.path.rsplit("/", 1)[-1]
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.calls.append(endpoint)
        if endpoint in self.fail:
            outcome = self.fail[endpoint]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if endpoint == "channels":
            assert query["mine"] == "true"
            body = {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]}
        elif endpoint == "playlistItems":
            start = int(query.get("pageToken", "0"))
            chunk = self.videos[start : start + self.page_size]
            body = {
                "items": [{"contentDetails": {"videoId": v["id"]}} for v in chunk],
                "pageInfo": {"totalResults": len(self.videos) + self.total_delta},
            }
            if start + self.page_size < len(self.videos):
                body["nextPageToken"] = str(start + self.page_size)
        elif endpoint == "videos":
            wanted = query["id"].split(",")
            assert "status" in query["part"] and "processingDetails" in query["part"]
            body = {
                "items": [
                    v for v in self.videos if v["id"] in wanted and v["id"] not in self.drop_ids
                ]
            }
        else:
            raise AssertionError(endpoint)
        return 200, json.dumps(body).encode()


_CHANNEL_BODY = json.dumps(
    {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]}
).encode()

OTHER = [
    _video("aaaaaaaa1", "Old one | W37"),
    _video("aaaaaaaa2", "Old two | W38", published="2026-09-15T17:38:14Z"),
    _video("aaaaaaaa3", "Private thing", privacy="private"),
    _video("aaaaaaaa4", "Claracle weekly report", published="2026-08-01T00:00:00Z"),
    _video("aaaaaaaa5", "Failed upload | W36", privacy="private", upload="failed"),
]


def _run(storage, fake, *extra, capsys=None):
    return main(
        [
            "--job-id",
            JOB_ID,
            "--provider",
            "youtube",
            "--approved-by",
            "coordinator",
            "--reason",
            "W39 upload rejected pre-network",
            *extra,
        ],
        storage=storage,
        prover_factory=lambda: YouTubeAbsenceProver(fake, TOKEN),
    )


def _records(storage):
    return read_evidence(storage, JOB_ID)["records"]


def test_dry_run_proves_absence_and_writes_nothing(capsys):
    storage = _storage()
    before = dict(storage.data)
    fake = FakeYouTube(OTHER)
    assert _run(storage, fake) == EXIT_OK
    assert storage.data == before
    out = capsys.readouterr().out
    assert '"dry_run_absent"' in out and TOKEN not in out
    assert fake.calls.count("playlistItems") == 3


def test_apply_writes_exactly_one_retry_allowed_record(capsys):
    storage = _storage()
    assert _run(storage, FakeYouTube(OTHER), "--apply") == EXIT_OK
    records = _records(storage)
    new = records[-1]
    assert len(records) == 3
    assert new["operation"] == REARM_OPERATION
    assert new["retry_blocked"] is False
    assert new["outcome"] == PUBLICATION_UNKNOWN
    assert new["verification"] == "provider_readback"
    assert new["details"]["approved_by"] == "coordinator"
    assert new["details"]["reason"] == "W39 upload rejected pre-network"
    assert new["details"]["absence_uploads_scanned"] == len(OTHER)
    assert new["details"]["rearms_claim_seq"] == records[1]["seq"]
    assert "rearmed_at" in new["details"]
    assert TOKEN not in json.dumps(storage.data[f"publication-evidence/{JOB_ID}.json"].decode())
    assert TOKEN not in capsys.readouterr().out


def test_second_apply_is_idempotent_without_new_absence_check():
    storage = _storage()
    assert _run(storage, FakeYouTube(OTHER), "--apply") == EXIT_OK
    fake = FakeYouTube(OTHER)
    assert _run(storage, fake, "--apply") == EXIT_OK
    assert len(_records(storage)) == 3
    assert fake.calls == []


def test_rearm_is_consumed_exactly_once_by_the_video_claim():
    storage = _storage()
    doc = read_evidence(storage, JOB_ID)
    assert retry_is_blocked(doc, platform="youtube", media_kind="video")
    assert (
        claim_evidence(
            storage, _identity(), platform="youtube", media_kind="video", operation="upload_intent"
        )
        is None
    )
    assert _run(storage, FakeYouTube(OTHER), "--apply") == EXIT_OK
    assert not retry_is_blocked(
        read_evidence(storage, JOB_ID), platform="youtube", media_kind="video"
    )
    first = claim_evidence(
        storage, _identity(), platform="youtube", media_kind="video", operation="upload_intent"
    )
    second = claim_evidence(
        storage, _identity(), platform="youtube", media_kind="video", operation="upload_intent"
    )
    assert first is not None and second is None
    assert retry_is_blocked(read_evidence(storage, JOB_ID), platform="youtube", media_kind="video")


def test_consumed_rearm_can_be_rearmed_again_only_after_new_proof():
    storage = _storage()
    assert _run(storage, FakeYouTube(OTHER), "--apply") == EXIT_OK
    claim_evidence(
        storage,
        _identity(),
        platform="youtube",
        media_kind="video",
        operation="upload_intent",
        at=INTENT_AT + timedelta(hours=1),
    )
    fake = FakeYouTube(OTHER)
    assert _run(storage, fake, "--apply") == EXIT_OK
    assert "videos" in fake.calls
    assert [r["operation"] for r in _records(storage)][-2:] == ["upload_intent", REARM_OPERATION]


@pytest.mark.parametrize(
    "candidate",
    [
        _video("zzzzzzzz1", TITLE, privacy="unlisted", published="2026-09-20T22:05:00Z"),
        _video("zzzzzzzz1", "  why agents SHIP matters for ai | w39 ", privacy="private"),
        _video("zzzzzzzz1", TITLE.upper(), privacy="private", published="2025-01-01T00:00:00Z"),
        _video(
            "zzzzzzzz1", "Something else | W38", privacy="private", published="2026-09-20T23:00:00Z"
        ),
        _video("zzzzzzzz1", "Renamed | W39", privacy="private", upload="uploaded"),
        _video("zzzzzzzz1", "Untitled", privacy="private", published="2026-09-20T22:10:00Z"),
        _video("zzzzzzzz1", "Untitled", privacy="private", upload="failed", published=""),
        {
            "id": "zzzzzzzz1",
            "snippet": {
                "title": "x",
                "description": f"job {JOB_ID}",
                "publishedAt": "2026-01-01T00:00:00Z",
            },
            "status": {"privacyStatus": "private", "uploadStatus": "rejected"},
        },
    ],
)
def test_candidate_found_refuses_and_reports_video_id(candidate, capsys):
    storage = _storage()
    before = dict(storage.data)
    assert _run(storage, FakeYouTube([*OTHER, candidate]), "--apply") == EXIT_CANDIDATE
    assert storage.data == before
    assert "zzzzzzzz1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "fake",
    [
        FakeYouTube(OTHER, fail={"channels": (403, _CHANNEL_BODY)}),
        FakeYouTube(
            OTHER, fail={"playlistItems": (500, b'{"items": [], "pageInfo": {"totalResults": 0}}')}
        ),
        FakeYouTube(OTHER, fail={"videos": (200, b"not json")}),
        FakeYouTube(OTHER, fail={"playlistItems": (200, b'{"pageInfo": {"totalResults": 1}}')}),
        FakeYouTube(OTHER, fail={"playlistItems": TimeoutError()}),
        FakeYouTube(OTHER, total_delta=1),
        FakeYouTube(OTHER, drop_ids={"aaaaaaaa3"}),
        FakeYouTube(OTHER, fail={"channels": (200, b'{"items": []}')}),
    ],
    ids=[
        "channels-403",
        "playlist-500",
        "videos-bad-json",
        "playlist-no-items",
        "transport-error",
        "partial-pages",
        "unresolved-video",
        "no-channel",
    ],
)
def test_provider_errors_and_partial_readback_fail_closed(fake):
    storage = _storage()
    before = dict(storage.data)
    assert _run(storage, fake, "--apply") == EXIT_REFUSED
    assert storage.data == before


def test_refuses_without_claim():
    storage = _storage(claim=False)
    before = dict(storage.data)
    assert _run(storage, FakeYouTube(OTHER), "--apply") == EXIT_REFUSED
    assert storage.data == before


def test_refuses_when_provider_id_recorded():
    storage = _storage()
    append_evidence(
        storage,
        _identity(),
        platform="youtube",
        media_kind="video",
        operation="upload",
        outcome="draft_created",
        provider_artifact_id="abcdefgh1",
        retry_blocked=False,
    )
    assert claim_evidence(
        storage, _identity(), platform="youtube", media_kind="video", operation="upload_intent"
    )
    assert _records(storage)[-1]["operation"] == "upload_intent"
    before = dict(storage.data)
    fake = FakeYouTube(OTHER)
    assert _run(storage, fake, "--apply") == EXIT_REFUSED
    assert storage.data == before and fake.calls == []


def test_refuses_when_latest_record_is_success_or_not_blocked():
    storage = _storage()
    append_evidence(
        storage,
        _identity(),
        platform="youtube",
        media_kind="video",
        operation="publish",
        outcome="published",
        retry_blocked=True,
    )
    before = dict(storage.data)
    assert _run(storage, FakeYouTube(OTHER), "--apply") == EXIT_REFUSED
    assert storage.data == before


def test_unsupported_provider_is_rejected():
    with pytest.raises(SystemExit):
        main(
            [
                "--job-id",
                JOB_ID,
                "--provider",
                "spotify_video",
                "--approved-by",
                "a",
                "--reason",
                "r",
            ]
        )


def test_conditional_append_rejects_concurrent_change():
    storage = _storage()
    latest = _records(storage)[-1]["seq"]
    with pytest.raises(PublicationStateError):
        append_evidence(
            storage,
            _identity(),
            platform="youtube",
            media_kind="video",
            operation=REARM_OPERATION,
            outcome=PUBLICATION_UNKNOWN,
            retry_blocked=False,
            expected_latest_seq=latest - 1,
        )


def test_evidence_change_between_check_and_write_fails_closed(monkeypatch):
    storage = _storage()
    real = rearm.YouTubeAbsenceProver.prove_absent

    def racing(self, expected):
        proof = real(self, expected)
        claim_evidence(
            storage, _identity(), platform="youtube", media_kind="video", operation="upload_intent"
        )
        append_evidence(
            storage,
            _identity(),
            platform="youtube",
            media_kind="video",
            operation="upload",
            outcome="draft_created",
            provider_artifact_id="raced0001",
            retry_blocked=True,
        )
        return proof

    monkeypatch.setattr(rearm.YouTubeAbsenceProver, "prove_absent", racing)
    count = len(_records(storage))
    assert _run(storage, FakeYouTube(OTHER), "--apply") == EXIT_REFUSED
    assert all(r["operation"] != REARM_OPERATION for r in _records(storage))
    assert len(_records(storage)) == count + 1


def test_match_reasons_title_rule_is_independent():
    expected = rearm.ExpectedArtifact(JOB_ID, "2026-W39", TITLE, INTENT_AT)
    video = _video("zzzzzzzz1", TITLE, published="2025-01-01T00:00:00Z")
    assert rearm.match_reasons(video, expected) == ["title"]
    assert rearm.match_reasons(_video("x", "Old two | W38"), expected) == []


LONG_TITLE = "A" * 60 + " agents, governance and the long tail of platform engineering | W38 recap"


def test_truncated_long_title_is_a_candidate(capsys):
    assert len(LONG_TITLE) > 100
    storage = _storage(title=LONG_TITLE)
    uploaded = _video("zzzzzzzz1", LONG_TITLE[:100], privacy="unlisted")
    assert _run(storage, FakeYouTube([*OTHER, uploaded]), "--apply") == EXIT_CANDIDATE
    assert "zzzzzzzz1" in capsys.readouterr().out
    expected = rearm.ExpectedArtifact(JOB_ID, "2026-W39", LONG_TITLE, INTENT_AT)
    assert rearm.match_reasons(uploaded, expected) == ["title"]
    edited = _video("zzzzzzzz2", LONG_TITLE[:40])
    assert rearm.match_reasons(edited, expected) == ["title_prefix"]


def test_iso_year_boundary_week_rule():
    expected = rearm.ExpectedArtifact(JOB_ID, "2027-W01", "t", INTENT_AT)
    video = _video("x", "Show | W01", published="2026-12-29T09:00:00Z")
    assert "week" in rearm.match_reasons(video, expected)


def test_refuses_recent_claim_without_readback():
    storage = _storage(claim_at=datetime.now(timezone.utc))
    before = dict(storage.data)
    fake = FakeYouTube(OTHER)
    assert _run(storage, fake, "--apply") == EXIT_REFUSED
    assert storage.data == before and fake.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [{"runner_status": "running"}, {"runner_status": None}, {"youtube_id": "abcdefgh9"}],
)
def test_refuses_non_terminal_run_or_manifest_video_id(kwargs):
    storage = _storage(**kwargs)
    before = dict(storage.data)
    fake = FakeYouTube(OTHER)
    assert _run(storage, fake, "--apply") == EXIT_REFUSED
    assert storage.data == before and fake.calls == []


def test_min_claim_age_exceeds_video_job_replica_timeout():
    import re
    from pathlib import Path

    bicep = Path(__file__).resolve().parents[1] / "infra/modules/aca-video.bicep"
    text = bicep.read_text(encoding="utf-8")
    timeouts = [int(v) for v in re.findall(r"replicaTimeout\D{0,40}?(\d{3,})", text)]
    assert timeouts, "replica timeout not found in aca-video.bicep"
    assert rearm.MIN_CLAIM_AGE.total_seconds() > max(timeouts)


def test_refuses_claim_without_integer_seq():
    storage = _storage()
    path = f"publication-evidence/{JOB_ID}.json"
    doc = json.loads(storage.data[path])
    doc["records"][-1]["seq"] = "2"
    storage.data[path] = json.dumps(doc).encode()
    fake = FakeYouTube(OTHER)
    assert _run(storage, fake, "--apply") == EXIT_REFUSED
    assert fake.calls == []


def test_non_object_manifest_refuses():
    storage = _storage()
    storage.data[f"jobs/{JOB_ID}/manifest.json"] = b"[1, 2]"
    assert _run(storage, FakeYouTube(OTHER), "--apply") == EXIT_REFUSED


def test_token_refresh_failure_is_credential_exit_code():
    def failing():
        raise rearm.RearmRefused("credentials_error", "YouTube token refresh failed: X")

    storage = _storage()
    code = main(
        ["--job-id", JOB_ID, "--provider", "youtube", "--approved-by", "a", "--reason", "r"],
        storage=storage,
        prover_factory=failing,
    )
    assert code == rearm.EXIT_USAGE
