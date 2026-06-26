from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.tts_bakeoff_synthesize as cli  # noqa: E402
from podcaster.tts_bakeoff import (  # noqa: E402
    BakeoffCandidate,
    PRODUCTION_GUEST_VOICE,
    PRODUCTION_NARRATOR_VOICE,
    PRODUCTION_PROVIDER,
    SampleResult,
    build_manifest,
    build_plan,
    build_ssml,
    default_candidates,
    escape_ssml_text,
    french_candidates,
    native_voice_candidates,
    parse_segments,
    production_candidate,
    recommended_voice_pair,
    redact_url,
    script_sha256,
    spanish_candidates,
)

SCRIPT_PATH = REPO_ROOT / "docs" / "tts-bakeoff-test-script.txt"
SCRIPT_TEXT = SCRIPT_PATH.read_text(encoding="utf-8")


def test_reviewed_script_exists_and_has_two_speakers():
    segments = parse_segments(SCRIPT_TEXT)
    roles = {segment.role for segment in segments}
    assert "narrator" in roles
    assert "guest" in roles
    # Front-matter header must not leak into narration.
    assert not any(segment.text.startswith("Version:") for segment in segments)


def test_parse_segments_skips_frontmatter_and_groups_paragraphs():
    text = "Header line not labelled\nVersion: 1\nNARRATOR: First line.\ncontinued line.\n\nNARRATOR: Second paragraph.\nGUEST: A quote.\n"
    segments = parse_segments(text)
    assert [s.role for s in segments] == ["narrator", "narrator", "guest"]
    assert segments[0].text == "First line. continued line."
    assert segments[2].text == "A quote."


def test_script_sha256_is_stable():
    assert script_sha256("abc") == script_sha256("abc")
    assert script_sha256("abc") != script_sha256("abd")
    assert len(script_sha256("abc")) == 64


def test_build_plan_excludes_disabled_by_default():
    plan = build_plan(SCRIPT_TEXT, "2026-W23")
    providers = [spec.candidate.provider for spec in plan]
    assert "openai-tts" not in providers  # disabled candidate
    assert "azure-speech-standard" in providers


def test_build_plan_can_include_disabled():
    plan = build_plan(SCRIPT_TEXT, "2026-W23", include_disabled=True)
    providers = [spec.candidate.provider for spec in plan]
    assert "openai-tts" in providers


def test_production_candidate_is_fable_alloy_openai():
    candidate = production_candidate()
    assert candidate.provider == PRODUCTION_PROVIDER == "openai-tts"
    assert candidate.narrator_voice == PRODUCTION_NARRATOR_VOICE == "fable"  # host A
    assert candidate.guest_voice == PRODUCTION_GUEST_VOICE == "alloy"  # host B
    assert candidate.enabled is True
    assert candidate.is_production is True
    assert candidate.voice_for("narrator") == "fable"
    assert candidate.voice_for("guest") == "alloy"


def test_production_voices_match_generation_constants():
    from podcaster import generation

    candidate = production_candidate()
    assert candidate.narrator_voice == generation.HOST_A_VOICE
    assert candidate.guest_voice == generation.HOST_B_VOICE


def test_production_candidate_excluded_from_bakeoff_comparison_plan():
    # The private bakeoff comparison plan must never include the enabled
    # production config, so the #4/#41 spike cannot trigger unreviewed
    # production spend.
    plan = build_plan(SCRIPT_TEXT, "2026-W23", include_disabled=True)
    assert all(spec.candidate.is_production is False for spec in plan)


def test_blob_paths_are_deterministic_and_safe():
    plan = build_plan(SCRIPT_TEXT, "2026-W23")
    paths = [spec.blob_path for spec in plan]
    assert paths[0] == "bakeoff/2026-w23/azure-speech-standard/en-us/en-us-andrewmultilingualneural.mp3"
    for path in paths:
        assert path == path.lower()
        assert " " not in path
        assert path.endswith(".mp3")


def test_blob_paths_unique_across_languages_for_shared_voice_ids():
    # ElevenLabs es/fr placeholders share a voice id; locale in the path must
    # keep their blob paths distinct so samples/manifests never collide.
    from podcaster.tts_bakeoff import native_voice_candidates, blob_path_for

    es = [c for c in native_voice_candidates("es") if c.provider == "elevenlabs"]
    fr = [c for c in native_voice_candidates("fr") if c.provider == "elevenlabs"]
    assert es and fr
    es_path = blob_path_for("2026-W23", es[0])
    fr_path = blob_path_for("2026-W23", fr[0])
    assert es_path != fr_path


def test_build_plan_rejects_script_without_segments():
    with pytest.raises(ValueError):
        build_plan("no speaker labels here\njust text\n", "2026-W23")


def test_ssml_escapes_injection_characters():
    candidate = BakeoffCandidate(provider="p", narrator_voice="v")
    segments = parse_segments('NARRATOR: <break time="5s"/> & <script>alert("x")</script>\n')
    ssml = build_ssml(segments, candidate)
    assert "<break" not in ssml
    assert "<script>" not in ssml
    assert "&lt;break" in ssml
    assert "&amp;" in ssml
    assert ssml.startswith("<speak")


def test_escape_ssml_text_covers_all_xml_specials():
    assert escape_ssml_text("<>&\"'") == "&lt;&gt;&amp;&quot;&apos;"


def test_redact_url_strips_sas_query():
    url = "https://acct.blob.core.windows.net/c/bakeoff/x.mp3?sig=SECRETTOKEN&se=2026"
    redacted = redact_url(url)
    assert "SECRETTOKEN" not in redacted
    assert "sig=" not in redacted
    assert redacted.endswith("[redacted-query]")
    # URLs without query are returned unchanged.
    plain = "https://acct.blob.core.windows.net/c/bakeoff/x.mp3"
    assert redact_url(plain) == plain


def test_manifest_redacts_urls_and_records_hash():
    results = [
        SampleResult(
            provider="azure-speech-standard",
            narrator_voice="v1",
            guest_voice="v2",
            blob_path="bakeoff/w/azure-speech-standard/v1.mp3",
            status="stored",
            size_bytes=123,
            content_type="audio/mpeg",
            url="https://acct.blob.core.windows.net/c/x.mp3?sig=LEAK",
        )
    ]
    manifest = build_manifest("2026-W23", "docs/tts-bakeoff-test-script.txt", SCRIPT_TEXT, results, "execute")
    blob = json.dumps(manifest)
    assert "LEAK" not in blob
    assert manifest["script"]["sha256"] == script_sha256(SCRIPT_TEXT)
    assert manifest["schema"] == "podcaster.tts-bakeoff.manifest/v1"
    assert "not for publication" in manifest["purpose"]


def test_missing_execute_context_lists_exact_vars(monkeypatch):
    for name in ("AZURE_SPEECH_ENDPOINT", "AZURE_SPEECH_KEY", "AZURE_SPEECH_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    missing = cli.missing_execute_context()
    assert "AZURE_SPEECH_ENDPOINT" in missing
    assert any("AZURE_SPEECH_KEY" in item for item in missing)


def test_missing_execute_context_satisfied_with_token(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_ENDPOINT", "https://eastus.tts.speech.microsoft.com")
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.setenv("AZURE_SPEECH_ACCESS_TOKEN", "header-token")
    assert cli.missing_execute_context() == []


def test_cli_dry_run_writes_manifest(tmp_path, monkeypatch, capsys):
    out = tmp_path / "manifest.json"
    rc = cli.main(["--week", "2026-W23", "--manifest-out", str(out)])
    assert rc == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["mode"] == "dry-run"
    assert all(sample["status"] == "planned" for sample in manifest["samples"])
    assert all(sample["url"] is None for sample in manifest["samples"])


def test_cli_execute_without_context_refuses(monkeypatch, capsys):
    for name in ("AZURE_SPEECH_ENDPOINT", "AZURE_SPEECH_KEY", "AZURE_SPEECH_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    rc = cli.main(["--execute", "--week", "2026-W23"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "AZURE_SPEECH_ENDPOINT" in err
    assert "Refusing to synthesize" in err


def test_cli_execute_stores_via_injected_backend(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AZURE_SPEECH_ENDPOINT", "https://eastus.tts.speech.microsoft.com")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "unit-test-key")

    synthesized = {}

    def fake_synth(spec, timeout):
        synthesized[spec.candidate.provider] = spec.ssml
        return b"ID3-fake-mp3-bytes"

    monkeypatch.setattr(cli, "synthesize_via_azure_speech", fake_synth)
    monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(tmp_path / "store"))
    monkeypatch.delenv("PODCASTER_STORAGE_ACCOUNT_URL", raising=False)

    out = tmp_path / "manifest.json"
    rc = cli.main(["--execute", "--week", "2026-W23", "--manifest-out", str(out)])
    assert rc == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["mode"] == "execute"
    assert all(sample["status"] == "stored" for sample in manifest["samples"])
    assert all(sample["size_bytes"] == len(b"ID3-fake-mp3-bytes") for sample in manifest["samples"])
    # The API key must never appear in stdout, stderr, or the manifest.
    captured = capsys.readouterr()
    assert "unit-test-key" not in captured.out
    assert "unit-test-key" not in captured.err
    assert "unit-test-key" not in json.dumps(manifest)
    # Audio bytes were produced for each enabled candidate.
    assert synthesized


def test_default_candidates_disable_unreviewed_providers():
    by_provider = {c.provider: c for c in default_candidates()}
    assert by_provider["azure-speech-standard"].enabled is True
    assert by_provider["openai-tts"].enabled is False


def test_native_candidates_use_locale_and_multilingual_voices():
    es = spanish_candidates()
    fr = french_candidates()
    assert all(c.locale == "es-MX" for c in es)
    assert all(c.locale == "fr-FR" for c in fr)
    # Preferred (first) candidate is an enabled Azure multilingual neural pair.
    assert es[0].enabled is True
    assert es[0].provider == "azure-speech-standard"
    assert es[0].narrator_voice == "es-MX-JorgeMultilingualNeural"
    assert es[0].guest_voice == "es-MX-DaliaMultilingualNeural"
    assert fr[0].narrator_voice == "fr-FR-RemyMultilingualNeural"
    assert fr[0].guest_voice == "fr-FR-VivienneMultilingualNeural"
    # Host pairs must contrast (narrator != guest) for two-voice conversation.
    assert es[0].narrator_voice != es[0].guest_voice
    assert fr[0].narrator_voice != fr[0].guest_voice


def test_native_candidates_disable_unreviewed_elevenlabs():
    for candidates in (spanish_candidates(), french_candidates()):
        eleven = [c for c in candidates if c.provider == "elevenlabs"]
        assert eleven and all(c.enabled is False for c in eleven)


def test_native_voice_candidates_dispatch_and_reject_unknown():
    assert native_voice_candidates("es") == spanish_candidates()
    assert native_voice_candidates("fr") == french_candidates()
    with pytest.raises(ValueError):
        native_voice_candidates("de")


def test_recommended_voice_pair_feeds_config():
    es = recommended_voice_pair("es")
    assert es["locale"] == "es-MX"
    assert es["narrator_voice"] == "es-MX-JorgeMultilingualNeural"
    assert es["guest_voice"] == "es-MX-DaliaMultilingualNeural"
    fr = recommended_voice_pair("fr")
    assert fr["locale"] == "fr-FR"
    assert fr["narrator_voice"] == "fr-FR-RemyMultilingualNeural"
    # Returned mapping is a copy; mutating it must not corrupt the source.
    es["narrator_voice"] = "tampered"
    assert recommended_voice_pair("es")["narrator_voice"] == "es-MX-JorgeMultilingualNeural"
    with pytest.raises(ValueError):
        recommended_voice_pair("de")


def test_native_candidate_plan_builds_safe_blob_paths():
    plan = build_plan(SCRIPT_TEXT, "2026-W23", candidates=spanish_candidates())
    assert plan  # at least the enabled Azure pair
    for spec in plan:
        assert spec.blob_path == spec.blob_path.lower()
        assert spec.blob_path.endswith(".mp3")
        assert " " not in spec.blob_path
