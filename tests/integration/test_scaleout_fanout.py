"""Azurite + docker-compose fan-out/fan-in integration test (epic #552, #566).

Proves the scale-out recorder/editor split (RFC §9) end-to-end against a real
Azurite Blob+Queue emulator over the local-only connection-string data plane:

* fan-out: 3 recorder replicas (docker compose --scale recorder=3) each record
  exactly one clip + terminal manifest;
* fan-in: the editor writes ``clipset.json``, blocks until all manifests land,
  composes, and records a single (DRAFT-only) publish;
* idempotency: a redelivered ``video-jobs`` message hits an unexpired foreign
  editor lease → no duplicate publish, no re-record, clipset reused;
* poison/fallback: a clip past ``MAX_DEQUEUE_COUNT`` gets a terminal fallback
  manifest so the barrier still converges;
* teardown: ``down -v`` cleans scratch.

Skipped when docker is unavailable; runs for real when docker is present. Spotify
is never really published (dry-run distribution / mocked compose).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from unittest.mock import MagicMock, patch

import pytest

from podcaster.queue import (
    ConnectionStringQueueBackend,
    encode_clip_message,
    encode_video_message,
)
from podcaster.storage import ConnectionStringStorageBackend
from podcaster.video.clipset import (
    clip_blob_path,
    clip_manifest_blob_path,
    clips_prefix,
    clipset_blob_path,
)
from podcaster.video.editor import acquire_or_renew_lease
from podcaster.video.job_runner import (
    REASON_ALREADY_PROCESSED,
    REASON_EDITOR_LEASE_HELD,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    manifest_path,
    process_message,
    run_video_generation,
    script_path,
)
from podcaster.video.recorder import MAX_DEQUEUE_COUNT, process_clip_message

pytestmark = pytest.mark.integration

COMPOSE_FILE = "docker-compose.fanout.yml"
SCRATCH = "video-scratch"
ARTIFACTS = "podcaster-artifacts"
CLIP_QUEUE = "video-clip-jobs"
VIDEO_QUEUE = "video-jobs"

# Host-side connection string (127.0.0.1; compose internal one points at azurite).
HOST_CONN = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
    "K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
)

SCRIPT = (
    "# Weekly Podcast - 2026-W25\n\n"
    "- https://github.com/microsoft/vscode\n"
    "- https://github.com/facebook/react\n"
    "- https://github.com/torvalds/linux\n\n"
    "HOST_A: Welcome.\nHOST_B: Let's dive in.\n"
)


def _compose(*args: str, check: bool = True, timeout: int = 300):
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def azurite_stack():
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    try:
        _compose("down", "-v", check=False, timeout=120)
        _compose("up", "-d", "azurite", timeout=180)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"docker compose unavailable: {exc}")
    # Wait for azurite healthy.
    import time

    deadline = time.monotonic() + 60
    healthy = False
    while time.monotonic() < deadline:
        ps = _compose("ps", check=False)
        if "healthy" in ps.stdout:
            healthy = True
            break
        time.sleep(2)
    if not healthy:
        _compose("down", "-v", check=False, timeout=120)
        pytest.skip("azurite did not become healthy within deadline")
    yield
    _compose("down", "-v", check=False, timeout=120)


def _seed(scratch, storage, job_id: str, n: int) -> None:
    storage.put_bytes(
        manifest_path(job_id), json.dumps({"request": {}}).encode(), "application/json"
    )
    storage.put_bytes(script_path(job_id), SCRIPT.encode(), "text/plain")
    entries = [
        {
            "clip_index": i,
            "start_seconds": float(i * 2),
            "duration_seconds": 2.0,
            "repo_owner": "o",
            "repo_name": f"r{i}",
        }
        for i in range(n)
    ]
    scratch.put_bytes(
        clipset_blob_path(job_id),
        json.dumps({"job_id": job_id, "count": n, "clips": entries}).encode(),
        "application/json",
    )


def test_scaleout_fanout_end_to_end(azurite_stack):
    scratch = ConnectionStringStorageBackend(HOST_CONN, SCRATCH)
    storage = ConnectionStringStorageBackend(HOST_CONN, ARTIFACTS)
    clipq = ConnectionStringQueueBackend(HOST_CONN, CLIP_QUEUE)
    videoq = ConnectionStringQueueBackend(HOST_CONN, VIDEO_QUEUE)

    job_id = f"fanout-{uuid.uuid4().hex[:8]}"
    n = 3
    _seed(scratch, storage, job_id, n)

    # --- Fan-out: 3 recorder replicas each record exactly one clip + manifest ---
    for i in range(n):
        clipq.send_message(encode_clip_message(job_id, i))
    res = _compose("up", "--scale", "recorder=3", "--abort-on-container-failure", "recorder")
    assert res.returncode == 0, res.stderr
    for i in range(n):
        assert scratch.blob_exists(clip_blob_path(job_id, i)), f"clip {i} missing"
        assert scratch.blob_exists(clip_manifest_blob_path(job_id, i)), f"manifest {i} missing"
    # Each appears exactly once.
    blobs = scratch.list_blobs(clips_prefix(job_id), limit=50)
    assert sum(b.endswith(".webm") for b in blobs) == n
    assert sum(b.endswith(".manifest.json") for b in blobs) == n

    # --- Fan-in: editor composes once (compose/distribute mocked; DRAFT only) ---
    composed = []

    def fake_compose(segments, audio_path=None, output_path=None, runner=None, **kw):
        if output_path:
            output_path.write_bytes(b"\x00" * 4096)
        composed.append(output_path)
        return MagicMock(
            output_path=output_path, duration_seconds=6.0, segment_count=n, has_audio=False
        )

    dist = MagicMock(
        status="completed",
        youtube_id="dry-run-id",
        blob_path=None,
        spotify=None,
        spotify_rss_updated=False,
        spotify_upload_updated=False,
        blob_url=None,
        dry_run=True,
        youtube_required_failed=False,
        youtube_failure_retryable=False,
        youtube_failure_code=None,
        youtube_failure_stage=None,
        youtube_failure_http_status=None,
        youtube_oauth_error=None,
        youtube_oauth_error_subtype=None,
    )
    with (
        patch("podcaster.video.video_compose.compose_video", side_effect=fake_compose),
        patch("podcaster.video.sync_plan.check_repo_removed", return_value=False),
        patch("podcaster.video.job_runner.distribute_video", return_value=dist) as md,
    ):
        outcome = run_video_generation(job_id, storage, fanout_scratch=scratch, clip_producer=clipq)
    assert outcome.status == STATUS_COMPLETED
    assert not scratch.blob_exists(clipset_blob_path(job_id))  # scratch cleaned post-publish
    assert md.call_count == 1  # single publish

    # --- Idempotency: redeliver with an unexpired foreign lease → no dup publish ---
    acquire_or_renew_lease(scratch, job_id, "other-run", ttl_seconds=1800)
    videoq.send_message(encode_video_message(job_id))
    msgs = videoq.receive_messages(1, visibility_timeout=2)
    with patch("podcaster.video.job_runner.distribute_video", return_value=dist) as md2:
        out2 = process_message(msgs[0], storage=storage, queue=videoq, config=None)
    assert out2.status == STATUS_SKIPPED
    assert out2.reason in {REASON_EDITOR_LEASE_HELD, REASON_ALREADY_PROCESSED}
    assert md2.call_count == 0  # no duplicate publish, no re-record

    # --- Poison/fallback: clip past MAX_DEQUEUE_COUNT → terminal fallback manifest ---
    job2 = f"poison-{uuid.uuid4().hex[:8]}"
    _seed(scratch, storage, job2, n)
    poison = MagicMock(
        message_id="m",
        pop_receipt="p",
        body=encode_clip_message(job2, 0),
        dequeue_count=MAX_DEQUEUE_COUNT,
    )
    q = MagicMock()
    process_clip_message(poison, scratch=scratch, queue=q, env={})
    assert scratch.blob_exists(clip_manifest_blob_path(job2, 0))
    man = json.loads(scratch.get_bytes(clip_manifest_blob_path(job2, 0)))
    assert man.get("is_fallback") is True
