"""Tests for the bundled Claracle DOG watermark asset (W36 regression).

W36 rendered without a watermark because the configured logo URL
(``https://www.claracle.com/images/claracle.jpeg``) 301'd to the apex host and
then returned ``404`` — that path is never published by the Claracle Hugo site.
These tests pin the packaging and the canonical-URL resolution that make the
watermark independent of that mutable URL.
"""

from __future__ import annotations

import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import podcaster.watermark as watermark
from podcaster import ssrf
from podcaster.video import video_compose as vc

# The exact URL SquadScope's config/podcast.json hands off, and which 404'd.
W36_HANDOFF_URL = "https://www.claracle.com/images/claracle.jpeg"


def _start_truncated_image_server(
    body: bytes, *, declared_length: int
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(declared_length))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)

        def log_message(self, format, *args):  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class TestBundledAssetPackaging:
    def test_logo_is_packaged_in_the_repo(self):
        assert watermark.LOGO_PATH.exists(), (
            "assets/images/claracle.jpeg must be committed so the synthesis image "
            "can brand episodes without a network fetch"
        )
        assert watermark.LOGO_PATH.stat().st_size > 0

    def test_logo_lives_under_the_assets_dir_copied_into_the_image(self):
        # Containerfile does `COPY assets ./assets`, so the asset must sit under
        # <repo root>/assets for it to land in the synthesis image.
        assert watermark.LOGO_PATH.parent == watermark.ASSET_DIR
        assert watermark.ASSET_DIR.parent == watermark.REPO_ROOT / "assets"

    def test_containerfile_copies_assets_and_asserts_the_logo(self):
        containerfile = (watermark.REPO_ROOT / "Containerfile").read_text(encoding="utf-8")
        assert "COPY assets ./assets" in containerfile
        assert "assets/images/claracle.jpeg" in containerfile, (
            "the synthesis image build must assert the watermark is packaged"
        )

    def test_logo_is_a_jpeg(self):
        assert watermark.LOGO_PATH.read_bytes()[:3] == b"\xff\xd8\xff"

    def test_attribution_is_documented(self):
        assert watermark.ATTRIBUTION_PATH.exists()
        assert "jmservera" in watermark.ATTRIBUTION_PATH.read_text(encoding="utf-8")

    def test_canonical_logo_path_resolves(self):
        assert watermark.canonical_logo_path() == watermark.LOGO_PATH

    def test_canonical_logo_path_is_none_when_asset_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watermark, "LOGO_PATH", tmp_path / "absent.jpeg")
        assert watermark.canonical_logo_path() is None

    def test_canonical_logo_path_is_none_when_asset_empty(self, tmp_path, monkeypatch):
        empty = tmp_path / "claracle.jpeg"
        empty.touch()
        monkeypatch.setattr(watermark, "LOGO_PATH", empty)
        assert watermark.canonical_logo_path() is None

    def test_logo_sha256_is_stable(self):
        assert len(watermark.logo_sha256()) == 64


class TestIsCanonicalLogoUrl:
    def test_w36_handoff_url_is_canonical(self):
        assert watermark.is_canonical_logo_url(W36_HANDOFF_URL)

    def test_apex_and_www_hosts_both_match(self):
        assert watermark.is_canonical_logo_url("https://claracle.com/images/claracle.jpeg")
        assert watermark.is_canonical_logo_url("https://WWW.Claracle.COM/Images/Claracle.JPEG")

    def test_squadscope_raw_default_is_canonical(self):
        from podcaster.video.video_compose import DEFAULT_DOG_LOGO_URL

        assert watermark.is_canonical_logo_url(DEFAULT_DOG_LOGO_URL)

    def test_github_blob_and_raw_variants_are_canonical(self):
        assert watermark.is_canonical_logo_url(
            "https://github.com/jmservera/SquadScope/raw/main/assets/images/claracle.jpeg"
        )
        assert watermark.is_canonical_logo_url(
            "https://github.com/jmservera/SquadScope/blob/main/assets/images/claracle.jpeg"
        )

    def test_third_party_logo_is_not_canonical(self):
        assert not watermark.is_canonical_logo_url("https://example.com/images/claracle.jpeg")
        assert not watermark.is_canonical_logo_url("https://evil.test/logo.png")

    def test_lookalike_host_suffix_is_not_canonical(self):
        assert not watermark.is_canonical_logo_url(
            "https://claracle.com.evil.test/images/claracle.jpeg"
        )
        assert not watermark.is_canonical_logo_url("https://notclaracle.com/images/claracle.jpeg")

    def test_non_http_schemes_are_not_canonical(self):
        assert not watermark.is_canonical_logo_url("file:///etc/passwd")
        assert not watermark.is_canonical_logo_url("ftp://claracle.com/images/claracle.jpeg")

    def test_empty_and_non_string_inputs(self):
        assert not watermark.is_canonical_logo_url("")
        assert not watermark.is_canonical_logo_url("   ")
        assert not watermark.is_canonical_logo_url(None)  # type: ignore[arg-type]


class TestModuleDocstringAccuracy:
    """The module docstring is the contract a future maintainer reads first.

    It used to state that "any remote fetch failure falls back" to the bundled
    logo, which is the opposite of what the resolver does for a third-party URL:
    substituting Claracle artwork there would misbrand the episode, so the fetch
    failure is raised instead. A docstring that contradicts the code is how the
    misbranding fallback gets reintroduced.
    """

    @staticmethod
    def _doc() -> str:
        return " ".join((watermark.__doc__ or "").split())

    def test_does_not_claim_a_blanket_fetch_failure_fallback(self):
        assert "any remote fetch failure falls back" not in self._doc()

    def test_documents_the_no_substitution_rule_for_third_party_urls(self):
        doc = self._doc().lower()
        assert "third-party" in doc
        assert "never" in doc

    def test_documented_canonical_only_behaviour_matches_the_code(self):
        """Pin the claim itself: only canonical URLs map to the bundled asset."""
        assert watermark.is_canonical_logo_url("https://www.claracle.com/images/claracle.jpeg")
        assert not watermark.is_canonical_logo_url("https://example.com/images/claracle.jpeg")


class TestRemoteWatermarkFetchIntegrity:
    def test_truncated_remote_body_is_transient_redacted_and_not_cached(
        self, tmp_path, monkeypatch
    ):
        cache_dir = tmp_path / "dogcache"
        partial_jpeg = b"\xff\xd8\xff\xe0" + (b"\x00" * 96)
        server, thread = _start_truncated_image_server(partial_jpeg, declared_length=10000)
        url = f"http://127.0.0.1:{server.server_port}/logo.jpeg?sig=abc123#frag"
        monkeypatch.setattr(vc, "classify_host", lambda _host: ssrf.HostVerdict.ALLOWED)
        monkeypatch.setattr(
            vc,
            "safe_urlopen",
            lambda remote_url, *, timeout: urllib.request.urlopen(remote_url, timeout=timeout),
        )

        try:
            with pytest.raises(vc.WatermarkTransientError) as excinfo:
                vc._fetch_dog_logo_remote(url, cache_dir)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        exc = excinfo.value
        rendered = f"{exc} {exc.details}"
        assert exc.reason == vc.WATERMARK_REASON_FETCH_TRANSIENT
        assert exc.details["failure_kind"] == "truncated_response"
        assert exc.details["logo_url"] == f"http://127.0.0.1:{server.server_port}/logo.jpeg"
        assert "abc123" not in rendered
        assert url not in rendered
        assert not list(cache_dir.glob("*"))

    def test_canonical_claracle_url_still_uses_bundled_asset(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            vc,
            "safe_urlopen",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network must not be used")),
        )
        assert vc._fetch_dog_logo(W36_HANDOFF_URL, tmp_path / "dogcache") == watermark.LOGO_PATH
