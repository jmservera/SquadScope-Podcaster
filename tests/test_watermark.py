"""Tests for the bundled Claracle DOG watermark asset (W36 regression).

W36 rendered without a watermark because the configured logo URL
(``https://www.claracle.com/images/claracle.jpeg``) 301'd to the apex host and
then returned ``404`` — that path is never published by the Claracle Hugo site.
These tests pin the packaging and the canonical-URL resolution that make the
watermark independent of that mutable URL.
"""

from __future__ import annotations

import podcaster.watermark as watermark

# The exact URL SquadScope's config/podcast.json hands off, and which 404'd.
W36_HANDOFF_URL = "https://www.claracle.com/images/claracle.jpeg"


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
