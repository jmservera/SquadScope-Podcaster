"""Shared pytest fixtures for the unit test suite.

The monitoring/admin API fails **closed** when no authentication is configured
(#604): without any ``UI_AUTH_*`` or ``MONITORING_API_KEY``/``PODCASTER_API_KEY``
env vars it rejects every request with ``401`` unless an operator explicitly
opts out via ``MONITORING_AUTH_DISABLED=true`` for local development.

Most unit tests exercise endpoint behaviour without configuring auth, so they
run in that explicit local-dev mode by default. Tests that assert the auth gate
itself simply override the env var via their own ``monkeypatch``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _local_dev_auth(monkeypatch):
    monkeypatch.setenv("MONITORING_AUTH_DISABLED", "true")
    yield
