"""Regression tests for scripts/youtube_promote.py – Phase 2 CLI (#652).

Focus: ensure the CLI constructs a transport and passes it to
_get_youtube_access_token so the TypeError (missing positional arg) cannot
recur.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.youtube_promote as promote_module  # noqa: E402
from podcaster.video.distribution import (  # noqa: E402
    VideoDistributionConfig,
    _DefaultTransport,
)

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class _FakeTransport:
    """Minimal stub; never actually called in these tests."""

    def request(self, url, *, method="GET", headers=None, data=None):
        raise AssertionError("network should not be reached in unit tests")

    def request_with_headers(self, url, *, method="GET", headers=None, data=None):
        raise AssertionError("network should not be reached in unit tests")


def _minimal_config():
    return VideoDistributionConfig(
        youtube_enabled=True,
        youtube_client_id="cid",
        youtube_client_secret="csec",
        youtube_refresh_token="rtoken",
    )


# ---------------------------------------------------------------------------
# Transport-wiring regression
# ---------------------------------------------------------------------------


class TestPromoteScriptTransportWiring:
    """Verify the CLI passes a transport object to _get_youtube_access_token."""

    def test_access_token_receives_transport_arg(self, monkeypatch):
        """TypeError cannot recur: the call must include two positional args."""
        captured: list[object] = []

        def _fake_token(config, transport):
            captured.append(transport)
            return "fake-access-token"

        monkeypatch.setattr("podcaster.video.distribution._get_youtube_access_token", _fake_token)
        monkeypatch.setattr("scripts.youtube_promote._get_youtube_access_token", _fake_token)
        monkeypatch.setattr(
            "podcaster.video.youtube_publish.verify_draft_ready",
            lambda vid, tok, *, playlist_id="", transport=None: [],
        )
        monkeypatch.setattr(
            "scripts.youtube_promote.verify_draft_ready",
            lambda vid, tok, *, playlist_id="", transport=None: [],
        )
        monkeypatch.setattr(
            "scripts.youtube_promote.VideoDistributionConfig",
            type(
                "_Patched",
                (),
                {"from_env": staticmethod(lambda: _minimal_config())},
            ),
        )

        rc = promote_module.main(["--video-id", "vid123", "--check-only"])

        assert rc == 0
        assert len(captured) == 1, "token helper must be called exactly once"
        assert isinstance(captured[0], _DefaultTransport), (
            f"transport must be _DefaultTransport, got {type(captured[0])}"
        )

    def test_access_token_receives_two_positional_args(self, monkeypatch):
        """Calling with only config raises TypeError; two args must be present."""
        call_args: list[tuple] = []

        def _spy(config, transport):
            call_args.append((config, transport))
            return "tok"

        monkeypatch.setattr("scripts.youtube_promote._get_youtube_access_token", _spy)
        monkeypatch.setattr(
            "scripts.youtube_promote.verify_draft_ready",
            lambda vid, tok, *, playlist_id="", transport=None: [],
        )
        monkeypatch.setattr(
            "scripts.youtube_promote.VideoDistributionConfig",
            type(
                "_Patched",
                (),
                {"from_env": staticmethod(lambda: _minimal_config())},
            ),
        )

        promote_module.main(["--video-id", "vid123", "--check-only"])

        assert call_args, "spy never called"
        config_arg, transport_arg = call_args[0]
        assert isinstance(config_arg, VideoDistributionConfig)
        assert transport_arg is not None, "transport must not be None"

    def test_missing_credentials_returns_2_without_token_call(self, monkeypatch):
        """Guard: if credentials are absent the CLI exits before the token call."""
        called: list[bool] = []

        def _should_not_call(*a, **kw):
            called.append(True)
            return "tok"

        empty_config = VideoDistributionConfig()
        monkeypatch.setattr(
            "scripts.youtube_promote.VideoDistributionConfig",
            type(
                "_Patched",
                (),
                {"from_env": staticmethod(lambda: empty_config)},
            ),
        )
        monkeypatch.setattr("scripts.youtube_promote._get_youtube_access_token", _should_not_call)

        rc = promote_module.main(["--video-id", "vid123", "--check-only"])

        assert rc == 2
        assert not called, "token helper must not be called when credentials are missing"

    def test_no_approved_by_without_check_only_returns_2(self, monkeypatch):
        """Guard: --approved-by required for promote path."""
        monkeypatch.setattr(
            "scripts.youtube_promote.VideoDistributionConfig",
            type(
                "_Patched",
                (),
                {"from_env": staticmethod(lambda: _minimal_config())},
            ),
        )
        rc = promote_module.main(["--video-id", "vid123"])
        assert rc == 2

    def test_promotion_packet_uses_current_private_draft_privacy(self, monkeypatch):
        captured: dict[str, object] = {}

        monkeypatch.setattr(
            "scripts.youtube_promote.VideoDistributionConfig",
            type(
                "_Patched",
                (),
                {"from_env": staticmethod(lambda: _minimal_config())},
            ),
        )
        monkeypatch.setattr(
            "scripts.youtube_promote._get_youtube_access_token",
            lambda config, transport: "tok",
        )
        monkeypatch.setattr(
            "scripts.youtube_promote.verify_draft_ready",
            lambda video_id, token, *, playlist_id="": [],
        )
        monkeypatch.setattr(
            "scripts.youtube_promote.get_video_snippet",
            lambda video_id, token: {"privacyStatus": "private"},
        )

        def _build_packet(video_id, **kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr("scripts.youtube_promote.build_publishing_packet", _build_packet)
        monkeypatch.setattr(
            "scripts.youtube_promote.approve_and_publish",
            lambda packet, token, *, approved_by: type(
                "_Result",
                (),
                {"succeeded": True, "scheduled_publish_at": ""},
            )(),
        )

        rc = promote_module.main(["--video-id", "vid123", "--approved-by", "operator"])

        assert rc == 0
        assert captured["draft_privacy"] == "private"
