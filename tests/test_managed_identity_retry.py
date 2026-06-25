from __future__ import annotations

import json
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from podcaster import job_runner, storage

TOKEN_PAYLOAD = {"access_token": "fake-token", "expires_on": "9999999999"}
STORAGE_SCOPE = "https://storage.azure.com/.default"
STORAGE_RESOURCE = "https://storage.azure.com"


class _JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _JsonResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.fixture(autouse=True)
def _clear_identity_env(monkeypatch):
    for name in ("IDENTITY_ENDPOINT", "IDENTITY_HEADER", "AZURE_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sleep_mock(monkeypatch):
    mock = Mock()
    monkeypatch.setattr(storage.time, "sleep", mock)
    return mock


def _http_error(status: int) -> HTTPError:
    return HTTPError(url="http://test", code=status, msg="error", hdrs={}, fp=None)


def _install_urlopen(monkeypatch, *events: object) -> Mock:
    side_effect = [event if isinstance(event, Exception) else _JsonResponse(event) for event in events]
    mock = Mock(side_effect=side_effect)
    monkeypatch.setattr(storage, "urlopen", mock)
    return mock


def test_retry_succeeds_on_second_attempt(monkeypatch, sleep_mock):
    urlopen_mock = _install_urlopen(monkeypatch, _http_error(400), TOKEN_PAYLOAD)

    payload = storage._request_managed_identity_token(STORAGE_RESOURCE)

    assert payload == TOKEN_PAYLOAD
    assert urlopen_mock.call_count == 2
    assert [call.args[0] for call in sleep_mock.call_args_list] == pytest.approx([1.0])


def test_retry_succeeds_on_fourth_attempt(monkeypatch, sleep_mock):
    urlopen_mock = _install_urlopen(
        monkeypatch, _http_error(500), _http_error(500), _http_error(500), TOKEN_PAYLOAD
    )

    payload = storage._request_managed_identity_token(STORAGE_RESOURCE)

    assert payload == TOKEN_PAYLOAD
    assert urlopen_mock.call_count == 4
    assert [call.args[0] for call in sleep_mock.call_args_list] == pytest.approx([1.0, 2.0, 4.0])


def test_retry_exhausted_raises(monkeypatch, sleep_mock):
    urlopen_mock = _install_urlopen(
        monkeypatch, _http_error(400), _http_error(400), _http_error(400), _http_error(400)
    )

    with pytest.raises(RuntimeError, match="managed identity token request failed"):
        storage._request_managed_identity_token(STORAGE_RESOURCE)

    assert urlopen_mock.call_count == 4
    assert [call.args[0] for call in sleep_mock.call_args_list] == pytest.approx([1.0, 2.0, 4.0])


def test_no_retry_on_non_retryable_status(monkeypatch, sleep_mock):
    urlopen_mock = _install_urlopen(monkeypatch, _http_error(403))

    with pytest.raises(RuntimeError, match="HTTP 403"):
        storage._request_managed_identity_token(STORAGE_RESOURCE)

    assert urlopen_mock.call_count == 1
    sleep_mock.assert_not_called()


def test_retry_on_url_error(monkeypatch, sleep_mock):
    urlopen_mock = _install_urlopen(monkeypatch, URLError("connection refused"), TOKEN_PAYLOAD)

    payload = storage._request_managed_identity_token(STORAGE_RESOURCE)

    assert payload == TOKEN_PAYLOAD
    assert urlopen_mock.call_count == 2
    assert [call.args[0] for call in sleep_mock.call_args_list] == pytest.approx([1.0])


def test_retry_backoff_timing(monkeypatch, sleep_mock):
    urlopen_mock = _install_urlopen(
        monkeypatch, _http_error(429), _http_error(503), _http_error(500), TOKEN_PAYLOAD
    )

    payload = storage._request_managed_identity_token(STORAGE_RESOURCE)

    assert payload == TOKEN_PAYLOAD
    assert urlopen_mock.call_count == 4
    assert [call.args[0] for call in sleep_mock.call_args_list] == pytest.approx([1.0, 2.0, 4.0])


def test_health_check_passes(monkeypatch):
    queue = object()
    backend = object()
    config = object()
    credential = Mock()
    credential.get_token.return_value = "fake-token"
    credential_cls = Mock(return_value=credential)
    drain_mock = Mock(return_value=[])

    monkeypatch.setenv("PODCASTER_STORAGE_ACCOUNT_URL", "https://acct.blob.core.windows.net")
    monkeypatch.setattr(job_runner, "create_queue_backend", lambda: queue)
    monkeypatch.setattr(job_runner, "create_storage_backend", lambda: backend)
    monkeypatch.setattr(job_runner, "load_tts_config", lambda: config)
    monkeypatch.setattr(job_runner, "ManagedIdentityTokenCredential", credential_cls, raising=False)
    monkeypatch.setattr(job_runner, "drain", drain_mock)

    assert job_runner.main() == 0
    credential_cls.assert_called_once_with()
    credential.get_token.assert_called_once_with(STORAGE_SCOPE)
    drain_mock.assert_called_once_with(queue, backend, config)


def test_health_check_fails_exits_3(monkeypatch):
    queue = object()
    backend = object()
    config = object()
    credential = Mock()
    credential.get_token.side_effect = RuntimeError("identity unavailable")
    credential_cls = Mock(return_value=credential)
    drain_mock = Mock()

    monkeypatch.setenv("PODCASTER_STORAGE_ACCOUNT_URL", "https://acct.blob.core.windows.net")
    monkeypatch.setattr(job_runner, "create_queue_backend", lambda: queue)
    monkeypatch.setattr(job_runner, "create_storage_backend", lambda: backend)
    monkeypatch.setattr(job_runner, "load_tts_config", lambda: config)
    monkeypatch.setattr(job_runner, "ManagedIdentityTokenCredential", credential_cls, raising=False)
    monkeypatch.setattr(job_runner, "drain", drain_mock)

    assert job_runner.main() == 3
    credential_cls.assert_called_once_with()
    credential.get_token.assert_called_once_with(STORAGE_SCOPE)
    drain_mock.assert_not_called()


def test_sdk_blob_credential_returns_access_token(monkeypatch):
    """The azure-storage-blob credential adapter wraps the managed-identity token
    flow into an azure-core AccessToken (identity-only, no account key)."""
    from azure.core.credentials import AccessToken

    monkeypatch.setattr(
        storage, "_request_managed_identity_token", lambda resource: dict(TOKEN_PAYLOAD)
    )
    cred = storage._SdkBlobCredential()
    token = cred.get_token(STORAGE_SCOPE)
    assert isinstance(token, AccessToken)
    assert token.token == "fake-token"
    assert token.expires_on == 9999999999


def test_sdk_blob_credential_rejects_empty_token(monkeypatch):
    monkeypatch.setattr(
        storage, "_request_managed_identity_token", lambda resource: {"expires_on": "1"}
    )
    cred = storage._SdkBlobCredential()
    with pytest.raises(RuntimeError):
        cred.get_token(STORAGE_SCOPE)
