from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from podcaster import storage
from podcaster.video.intermediates import StorageOperationTimeout, run_storage_operation


class Response(BytesIO):
    status = 200

    def __init__(self, content, etag='"original"'):
        super().__init__(content)
        self.headers = {"ETag": etag, "x-ms-request-id": "1234-5678"}


def backend():
    result = storage.AzureBlobStorageBackend("https://example.invalid", "artifacts")
    result._credential = SimpleNamespace(get_token=lambda *args: "test-token")
    return result


def test_authority_media_reads_use_conditional_get_not_repeated_full_wav(monkeypatch):
    client = backend()
    payload = b"original-wav-content"
    digest = hashlib.sha256(payload).hexdigest()
    requests = []

    def get(request, *, timeout):
        requests.append(request)
        if request.get_header("If-none-match") == '"original"':
            raise HTTPError(request.full_url, 304, "unchanged", {}, None)
        return Response(payload)

    monkeypatch.setattr(storage, "urlopen", get)
    assert client.get_verified_bytes("jobs/original/audio.wav", digest, len(payload)) == payload
    for _ in range(10):
        assert client.get_verified_bytes("jobs/original/audio.wav", digest, len(payload)) == payload
    assert requests[0].get_header("If-none-match") is None
    assert all(r.get_header("If-none-match") == '"original"' for r in requests[1:])


def test_selected_archive_streams_once_and_reuses_only_unchanged_etag(monkeypatch):
    from podcaster.video.recovery import _verify_candidate_archive

    client = backend()
    payload = b"selected-private-video"
    digest = hashlib.sha256(payload).hexdigest()
    etags = iter(['"original"', '"original"', '"original"', '"changed"', '"changed"'])
    requests = []
    downloads = []

    def head(request, *, timeout):
        requests.append(request)
        assert request.get_method() == "HEAD"
        response = Response(b"", next(etags))
        response.headers["Content-Length"] = str(len(payload))
        return response

    def download(_path, target):
        downloads.append(target)
        target.write_bytes(payload)
        return True

    monkeypatch.setattr(storage, "urlopen", head)
    monkeypatch.setattr(client, "blob_size", lambda _path: len(payload))
    monkeypatch.setattr(client, "download_file", download)
    for _ in range(2):
        _verify_candidate_archive(client, "jobs/original/selected.mp4", digest, len(payload))
    assert len(downloads) == 1
    _verify_candidate_archive(client, "jobs/original/selected.mp4", digest, len(payload))
    assert len(downloads) == 2
    assert all(request.get_method() == "HEAD" for request in requests)


@pytest.mark.parametrize("size", [11_664_239, 12_170_805])
def test_large_audio_proofs_are_verified_without_memory_cache(monkeypatch, size):
    client = backend()
    payload = b"A" * size
    digest = hashlib.sha256(payload).hexdigest()
    requests = []

    def get(request, *, timeout):
        requests.append(request)
        return Response(payload)

    monkeypatch.setattr(storage, "urlopen", get)
    assert client.get_verified_bytes("jobs/original/audio.mp3", digest, size) == payload
    assert requests[0].get_header("If-none-match") is None
    assert client._verified_reads == {}
    assert client._verified_reads_bytes == 0


@pytest.mark.parametrize("event", ["modified", "missing", "transport"])
def test_cached_original_media_fails_closed_on_changed_or_unavailable_get(monkeypatch, event):
    client = backend()
    payload = b"original"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(storage, "urlopen", lambda *args, **kwargs: Response(payload))
    client.get_verified_bytes("jobs/original/audio.mp3", digest, len(payload))

    def changed(request, **kwargs):
        if event == "modified":
            return Response(b"tampered", '"changed"')
        if event == "missing":
            raise HTTPError(request.full_url, 404, "absent", {}, None)
        raise OSError("network unavailable")

    monkeypatch.setattr(storage, "urlopen", changed)
    with pytest.raises(storage.StorageUnavailableError):
        client.get_verified_bytes("jobs/original/audio.mp3", digest, len(payload))


def test_forked_authority_transport_inherits_one_shared_deadline(monkeypatch):
    client = backend()

    def finite_get(request, *, timeout):
        assert 0 < timeout <= 0.3
        time.sleep(0.01)
        return Response(b"manifest")

    monkeypatch.setattr(storage, "urlopen", finite_get)
    started = time.monotonic()
    assert (
        run_storage_operation(lambda: client.get_bytes("jobs/original/manifest.json"), 0.3)
        == b"manifest"
    )
    assert time.monotonic() - started < 3


def test_original_wav_get_timeout_stops_exact_owned_transport_child(monkeypatch):
    client = backend()
    child_pid = multiprocessing.get_context("fork").Value("i", 0)
    observed_timeout = multiprocessing.get_context("fork").Value("d", 0)

    def blocked_get(request, *, timeout):
        assert request.get_method() == "GET"
        assert request.full_url.endswith("/audio.wav")
        child_pid.value = os.getpid()
        observed_timeout.value = timeout
        time.sleep(20)

    monkeypatch.setattr(storage, "urlopen", blocked_get)
    started = time.monotonic()
    with pytest.raises(StorageOperationTimeout):
        run_storage_operation(lambda: client.get_bytes("jobs/original/audio.wav"), 0.1)
    assert 0 < observed_timeout.value <= 0.1
    assert time.monotonic() - started < 3
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid.value, 0)


def test_concurrent_forked_readers_reuse_verified_original_without_inherited_sdk(monkeypatch):
    client = backend()
    payload = b"original-wav"
    digest = hashlib.sha256(payload).hexdigest()
    requests = multiprocessing.get_context("fork").Value("i", 0)

    def get(request, *, timeout):
        with requests.get_lock():
            requests.value += 1
        if request.get_header("If-none-match") is not None:
            raise HTTPError(request.full_url, 304, "unchanged", {}, None)
        return Response(payload)

    monkeypatch.setattr(storage, "urlopen", get)
    client.get_verified_bytes("jobs/original/audio.wav", digest, len(payload))

    def read(_index):
        return run_storage_operation(
            lambda: client.get_verified_bytes("jobs/original/audio.wav", digest, len(payload)), 1
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(read, range(4))) == [payload] * 4
    assert requests.value == 5


def test_forked_reader_reinitializes_cache_lock_held_by_another_thread(monkeypatch):
    client = backend()
    payload = b"original-wav"
    digest = hashlib.sha256(payload).hexdigest()
    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_cache_lock():
        with client._verified_cache_lock:
            lock_held.set()
            release_lock.wait(timeout=2)

    monkeypatch.setattr(storage, "urlopen", lambda *args, **kwargs: Response(payload))
    with ThreadPoolExecutor(max_workers=1) as pool:
        lock_holder = pool.submit(hold_cache_lock)
        try:
            assert lock_held.wait(timeout=1)
            assert (
                run_storage_operation(
                    lambda: client.get_verified_bytes(
                        "jobs/original/audio.wav", digest, len(payload)
                    ),
                    1,
                )
                == payload
            )
        finally:
            release_lock.set()
        lock_holder.result(timeout=1)


def test_timeout_diagnostics_expose_only_verb_object_status_request_id(monkeypatch, caplog):
    client = backend()
    monkeypatch.setattr(storage, "urlopen", lambda *args, **kwargs: Response(b"content"))
    with caplog.at_level("INFO", logger="podcaster.storage"), storage.storage_operation_budget(0.5):
        assert client.get_bytes("jobs/original/private-name.wav") == b"content"
    facts = [
        json.loads(record.message.split("facts=", 1)[1])
        for record in caplog.records
        if "facts=" in record.message
    ]
    assert facts[0]["verb"] == "GET"
    assert facts[0]["object_class"] == "original_wav"
    assert 0 < facts[0]["client_timeout_seconds"] <= 0.5
    assert facts[-1]["status"] == 200
    assert facts[-1]["storage_service_request_id"] == "1234-5678"
    assert "private-name" not in caplog.text
    assert "test-token" not in caplog.text
    assert "example.invalid" not in caplog.text


def test_identity_retries_cannot_outlive_shared_operation_budget(monkeypatch):
    timeouts = []

    def unavailable(request, *, timeout):
        timeouts.append(timeout)
        raise OSError("unavailable")

    monkeypatch.setattr(storage, "urlopen", unavailable)
    started = time.monotonic()
    with storage.storage_operation_budget(0.03), pytest.raises(TimeoutError):
        storage._request_managed_identity_token("https://storage.azure.com")
    assert time.monotonic() - started < 1
    assert len(timeouts) == 1
    assert 0 < timeouts[0] <= 0.03
