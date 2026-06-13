from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcaster import episode, job_runner, tts
from podcaster.generation import manifest_bytes
from podcaster.queue import QueueMessage, encode_synthesis_message, parse_job_id
from podcaster.storage import StoredArtifact
from podcaster.tts import load_tts_config

JOB_ID = "podcast-2026-W24-abcdef012345"


class FakeStorage:
    """In-memory StorageBackend for runner tests."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        self.blobs[path] = content
        return StoredArtifact(path=path, url=f"https://test.invalid/{path}", size_bytes=len(content), content_type=content_type)

    def get_bytes(self, path: str) -> bytes | None:
        return self.blobs.get(path)

    def update_bytes(self, path, content_type, update):
        updated = update(self.blobs.get(path))
        self.blobs[path] = updated
        return StoredArtifact(path=path, url=f"https://test.invalid/{path}", size_bytes=len(updated), content_type=content_type)


class FakeQueue:
    def __init__(self, messages: list[QueueMessage] | None = None) -> None:
        self._messages = list(messages or [])
        self.deleted: list[str] = []

    def receive_messages(self, max_messages: int = 1, *, visibility_timeout: int = 600) -> list[QueueMessage]:
        batch = self._messages[:max_messages]
        self._messages = self._messages[max_messages:]
        return batch

    def delete_message(self, message: QueueMessage) -> None:
        self.deleted.append(message.message_id)


def _production_config():
    return load_tts_config(
        {
            "AZURE_OPENAI_ENDPOINT": "https://podcaster-openai.openai.azure.com/",
            "AZURE_OPENAI_TTS_DEPLOYMENT": "tts-prod",
            "AZURE_OPENAI_TTS_VOICE_HOST_A": "fable",
            "AZURE_OPENAI_TTS_VOICE_HOST_B": "alloy",
            "AZURE_OPENAI_AUTH_MODE": "managed_identity",
        }
    )


def _two_voice_script() -> str:
    article = episode.sanitize_article(
        week="2026-W24",
        title="Skills go vertical",
        url="https://claracle.com/weekly/2026/w24/",
        sha256="",
        summary="A summary of the week's signal and noise.",
        beats=[{"topic": "agent skills go vertical", "points": ["point one", "point two"]}],
    )
    return episode.build_episode_script(article)


def _placeholder_script() -> str:
    # Mirrors the deterministic placeholder format (Host A (fable): ...) which
    # episode.parse_script_segments does not recognise as spoken turns.
    return "\n".join(
        [
            "Title: Claracle Podcast",
            "---",
            "",
            "Host A (fable): Welcome to Claracle.",
            "Host B (alloy): A quick heads-up before we dive in.",
            "Host outro: Manual review is required before publishing.",
        ]
    )


def _base_manifest(status: str = "review_pending") -> dict:
    mp3_path = f"jobs/{JOB_ID}/audio/{JOB_ID}.mp3"
    wav_path = f"jobs/{JOB_ID}/audio/{JOB_ID}.wav"
    return {
        "schema_version": "squadscope-podcaster-job-v1",
        "job_id": JOB_ID,
        "status": status,
        "generation": {
            "engine": "local-deterministic-placeholder",
            "audio_mode": "placeholder",
            "tts_provider": None,
            "tts_voice": None,
            "tts_synthesis": {"status": "blocked", "allowed": False, "blocked_by": ["human_review", "provider_not_selected"]},
            "audio_validation": {"status": "placeholder", "ready": False},
        },
        "publishing": {
            "mode": "manual",
            "eligible": False,
            "blocked_by": ["human_review", "real_tts_not_implemented", "audio_validation_not_passed"],
            "readiness_checks": {"editorial_review_complete": False, "real_audio_available": False},
            "public_url": None,
        },
        "lifecycle": {"status": status, "revision": 1, "transitions": [{"at": "t0", "to": "review_pending", "reason": "artifacts_staged"}]},
        "artifacts": {
            mp3_path: {"url": f"https://test.invalid/{mp3_path}", "publicly_accessible": False, "size_bytes": 1, "content_type": "audio/mpeg", "sha256": "0" * 64},
            wav_path: {"url": f"https://test.invalid/{wav_path}", "publicly_accessible": False, "size_bytes": 1, "content_type": "audio/wav", "sha256": "0" * 64},
        },
    }


def _stage(storage: FakeStorage, manifest: dict, script: str) -> None:
    storage.put_bytes(job_runner.manifest_path(JOB_ID), manifest_bytes(manifest), "application/json; charset=utf-8")
    storage.put_bytes(job_runner.script_path(JOB_ID), script.encode("utf-8"), "text/plain; charset=utf-8")


def _patch_audio(monkeypatch) -> None:
    def fake_render(segments, wav_out, out, runner=None, **kwargs):
        Path(wav_out).write_bytes(b"stitched-wav-bytes")
        Path(out).write_bytes(b"stitched-mp3-bytes")
        return Path(wav_out), Path(out)

    def fake_probe(path, sha256, runner=None):
        from podcaster.audio import AudioMetadata

        if str(path).endswith(".wav"):
            return AudioMetadata(
                duration_seconds=300.0,
                loudness_lufs=-16.0,
                sample_rate_hz=44100,
                bitrate_bps=705600,
                channels=1,
                content_type="audio/wav",
                byte_length=Path(path).stat().st_size,
                sha256=sha256,
                codec_name="pcm_s16le",
            )
        return AudioMetadata(
            duration_seconds=300.0,
            loudness_lufs=-16.0,
            sample_rate_hz=44100,
            bitrate_bps=192000,
            channels=1,
            content_type="audio/mpeg",
            byte_length=Path(path).stat().st_size,
            sha256=sha256,
        )

    monkeypatch.setattr(episode, "render_distribution_audio", fake_render)
    monkeypatch.setattr(episode, "probe_audio", fake_probe)


def _patch_tts_network(monkeypatch) -> None:
    # Replace the managed-identity token + HTTP transport defaults so process_message/
    # drain (which do not inject these) never touch the network.
    monkeypatch.setattr(tts.ManagedIdentityTokenCredential, "get_token", lambda self, *scopes: "token")
    monkeypatch.setattr(tts, "_default_transport", lambda request: b"segment-bytes")


# --- message parsing -------------------------------------------------------


def test_parse_job_id_accepts_base64_encoded_message():
    assert parse_job_id(encode_synthesis_message(JOB_ID)) == JOB_ID


def test_parse_job_id_accepts_raw_json():
    assert parse_job_id(json.dumps({"job_id": JOB_ID})) == JOB_ID


def test_parse_job_id_rejects_message_without_job_id():
    with pytest.raises(ValueError):
        parse_job_id(json.dumps({"week": "2026-W24"}))


def test_parse_job_id_rejects_empty_message():
    with pytest.raises(ValueError):
        parse_job_id("   ")


# --- run_synthesis happy path ---------------------------------------------


def test_run_synthesis_completes_and_never_makes_publishable(monkeypatch):
    _patch_audio(monkeypatch)
    storage = FakeStorage()
    _stage(storage, _base_manifest(), _two_voice_script())

    outcome = job_runner.run_synthesis(
        JOB_ID, storage, _production_config(), token_provider=lambda scope: "token", transport=lambda request: b"segment-bytes"
    )

    assert outcome.status == job_runner.STATUS_COMPLETED
    assert outcome.segment_count and outcome.segment_count > 0

    manifest = json.loads(storage.get_bytes(job_runner.manifest_path(JOB_ID)).decode("utf-8"))
    gen = manifest["generation"]
    assert gen["audio_mode"] == "synthesized"
    assert gen["tts_provider"] == "openai-tts"
    assert sorted(gen["tts_voice"]) == ["alloy", "fable"]
    assert gen["synthesis_runner"]["status"] == "completed"
    assert gen["synthesis_runner"]["job_id"] == JOB_ID
    # Audio bytes were staged and the manifest artifact checksum updated.
    mp3_path = gen["synthesis_runner"]["audio"]["path"]
    wav_path = gen["synthesis_runner"]["audio"]["artifacts"]["wav"]["path"]
    assert storage.get_bytes(mp3_path) == b"stitched-mp3-bytes"
    assert storage.get_bytes(wav_path) == b"stitched-wav-bytes"
    assert manifest["artifacts"][mp3_path]["sha256"] == gen["synthesis_runner"]["audio"]["sha256"]
    assert manifest["artifacts"][wav_path]["sha256"] == gen["synthesis_runner"]["audio"]["artifacts"]["wav"]["sha256"]
    assert gen["synthesis_runner"]["audio"]["upload_format"] == "wav"

    pub = manifest["publishing"]
    # Human-review gate is preserved: never publication-eligible from the runner.
    assert pub["eligible"] is False
    assert "human_review" in pub["blocked_by"]
    assert "real_tts_not_implemented" not in pub["blocked_by"]
    assert pub["readiness_checks"]["real_audio_available"] is True
    assert pub["readiness_checks"]["editorial_review_complete"] is False


def test_run_synthesis_does_not_log_secrets(monkeypatch, caplog):
    _patch_audio(monkeypatch)
    storage = FakeStorage()
    _stage(storage, _base_manifest(), _two_voice_script())
    with caplog.at_level("INFO"):
        job_runner.run_synthesis(JOB_ID, storage, _production_config(), token_provider=lambda scope: "token", transport=lambda request: b"bytes")
    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "openai.azure.com" not in combined  # full endpoint never logged
    assert "Bearer" not in combined


# --- idempotency & skip paths ---------------------------------------------


def test_run_synthesis_is_idempotent_on_duplicate_delivery(monkeypatch):
    _patch_audio(monkeypatch)
    storage = FakeStorage()
    manifest = _base_manifest()
    manifest["generation"]["synthesis_runner"] = {"status": "completed", "job_id": JOB_ID}
    _stage(storage, manifest, _two_voice_script())

    def exploding_transport(request):
        raise AssertionError("must not re-synthesize an already-completed job")

    outcome = job_runner.run_synthesis(JOB_ID, storage, _production_config(), transport=exploding_transport)
    assert outcome.status == job_runner.STATUS_SKIPPED
    assert outcome.reason == job_runner.REASON_ALREADY_SYNTHESIZED


def test_run_synthesis_skips_placeholder_script(monkeypatch):
    _patch_audio(monkeypatch)
    storage = FakeStorage()
    _stage(storage, _base_manifest(), _placeholder_script())

    outcome = job_runner.run_synthesis(
        JOB_ID, storage, _production_config(), transport=lambda request: pytest.fail("must not synthesize placeholder")
    )
    assert outcome.status == job_runner.STATUS_SKIPPED
    assert outcome.reason == job_runner.REASON_NOT_TWO_VOICE
    manifest = json.loads(storage.get_bytes(job_runner.manifest_path(JOB_ID)).decode("utf-8"))
    assert manifest["publishing"]["eligible"] is False
    assert manifest["generation"]["audio_mode"] == "placeholder"


def test_run_synthesis_skips_when_tts_not_configured():
    storage = FakeStorage()
    _stage(storage, _base_manifest(), _two_voice_script())
    outcome = job_runner.run_synthesis(JOB_ID, storage, load_tts_config({}))
    assert outcome.status == job_runner.STATUS_SKIPPED
    assert outcome.reason == job_runner.REASON_TTS_NOT_CONFIGURED


# --- failure handling ------------------------------------------------------


def test_run_synthesis_failure_does_not_make_publishable(monkeypatch):
    _patch_audio(monkeypatch)
    storage = FakeStorage()
    _stage(storage, _base_manifest(), _two_voice_script())

    def failing_transport(request):
        raise RuntimeError("tts endpoint unreachable")

    with pytest.raises(job_runner.TransientSynthesisError):
        job_runner.run_synthesis(JOB_ID, storage, _production_config(), token_provider=lambda scope: "token", transport=failing_transport)

    manifest = json.loads(storage.get_bytes(job_runner.manifest_path(JOB_ID)).decode("utf-8"))
    assert manifest["publishing"]["eligible"] is False
    assert manifest["publishing"]["readiness_checks"]["real_audio_available"] is False
    assert manifest["generation"]["synthesis_runner"]["status"] == "failed"
    # No real audio was staged on failure.
    assert storage.get_bytes(f"jobs/{JOB_ID}/audio/{JOB_ID}.mp3") is None
    assert storage.get_bytes(f"jobs/{JOB_ID}/audio/{JOB_ID}.wav") is None


def test_run_synthesis_missing_manifest_is_transient():
    storage = FakeStorage()
    with pytest.raises(job_runner.TransientSynthesisError):
        job_runner.run_synthesis(JOB_ID, storage, _production_config())


# --- queue message processing ---------------------------------------------


def _message(job_id: str = JOB_ID, message_id: str = "m1") -> QueueMessage:
    return QueueMessage(message_id=message_id, pop_receipt="pr", body=encode_synthesis_message(job_id), dequeue_count=1)


def test_process_message_deletes_on_completion(monkeypatch, caplog):
    _patch_audio(monkeypatch)
    _patch_tts_network(monkeypatch)
    storage = FakeStorage()
    _stage(storage, _base_manifest(), _two_voice_script())
    queue = FakeQueue()
    msg = _message()

    with caplog.at_level("INFO"):
        outcome = job_runner.process_message(msg, storage=storage, queue=queue, config=_production_config())
    assert outcome.status == job_runner.STATUS_COMPLETED
    assert queue.deleted == ["m1"]
    audit = " ".join(record.getMessage() for record in caplog.records if "synthesis audit" in record.getMessage())
    assert "event=start" in audit
    assert "event=success" in audit
    assert JOB_ID in audit


def test_process_message_deletes_on_skip(monkeypatch):
    _patch_audio(monkeypatch)
    storage = FakeStorage()
    _stage(storage, _base_manifest(), _placeholder_script())
    queue = FakeQueue()
    outcome = job_runner.process_message(_message(), storage=storage, queue=queue, config=_production_config())
    assert outcome.status == job_runner.STATUS_SKIPPED
    assert queue.deleted == ["m1"]


def test_process_message_leaves_message_on_transient_failure(monkeypatch):
    _patch_audio(monkeypatch)
    storage = FakeStorage()  # no manifest staged -> transient
    queue = FakeQueue()
    outcome = job_runner.process_message(_message(), storage=storage, queue=queue, config=_production_config())
    assert outcome.status == job_runner.STATUS_FAILED
    assert queue.deleted == []  # left for redelivery


def test_process_message_deletes_after_retry_exhaustion(monkeypatch):
    _patch_audio(monkeypatch)
    storage = FakeStorage()  # no manifest staged -> transient until poison threshold
    queue = FakeQueue()
    msg = QueueMessage(
        message_id="m-poison",
        pop_receipt="pr",
        body=encode_synthesis_message(JOB_ID),
        dequeue_count=job_runner.MAX_DEQUEUE_COUNT,
    )
    outcome = job_runner.process_message(msg, storage=storage, queue=queue, config=_production_config())
    assert outcome.status == job_runner.STATUS_FAILED
    assert outcome.reason == job_runner.REASON_RETRY_EXHAUSTED
    assert queue.deleted == ["m-poison"]


def test_process_message_discards_poison_message():
    storage = FakeStorage()
    queue = FakeQueue()
    poison = QueueMessage(message_id="p1", pop_receipt="pr", body="not-json", dequeue_count=9)
    outcome = job_runner.process_message(poison, storage=storage, queue=queue, config=_production_config())
    assert outcome.status == job_runner.STATUS_FAILED
    assert queue.deleted == ["p1"]


def test_drain_processes_until_empty(monkeypatch):
    _patch_audio(monkeypatch)
    _patch_tts_network(monkeypatch)
    storage = FakeStorage()
    _stage(storage, _base_manifest(), _two_voice_script())
    queue = FakeQueue([_message(message_id="m1"), _message(message_id="m2")])

    outcomes = job_runner.drain(queue, storage, _production_config())
    # Two deliveries of the same job: first completes, second is idempotent skip.
    assert [o.status for o in outcomes] == [job_runner.STATUS_COMPLETED, job_runner.STATUS_SKIPPED]
    assert queue.deleted == ["m1", "m2"]
