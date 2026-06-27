from __future__ import annotations

from podcaster.config import LanguageConfig
from podcaster.localization_qa import (
    LEAKAGE_THRESHOLD,
    LocalizationQAResult,
    evaluate_localization,
    extract_spoken_text,
    localization_gate,
)
from podcaster.script_gen import GenerationContext
from podcaster.validation import evaluate_localization as exported_evaluate

# --- Script fixtures ---------------------------------------------------------

ES = LanguageConfig.default_for("es")
FR = LanguageConfig.default_for("fr")
EN = LanguageConfig.default_for("en")


def _es_script(
    *, include_disclosure: bool = True, include_cta: bool = True, leak: bool = False
) -> str:
    disclosure = ES.disclosure if include_disclosure else "Bienvenidos al programa de esta semana."
    cta = ES.cta if include_cta else "Gracias por acompañarnos hoy."
    leak_line = (
        "HOST_A: This week we found that the project really stands out and they would agree."
        if leak
        else "HOST_A: Esta semana encontramos un proyecto que destaca por su arquitectura."
    )
    return (
        "Voices: HOST_A=es-MX-JorgeMultilingualNeural; HOST_B=es-MX-DaliaMultilingualNeural\n"
        "---\n"
        f"HOST_B: {disclosure}\n"
        "## Section: Frameworks de IA\n"
        f"{leak_line}\n"
        "HOST_B: Exactamente, lo que más nos llamó la atención fue su integración con "
        "GitHub y la API.\n"
        "HOST_A: Coincido, el equipo usa CI/CD de forma ejemplar.\n"
        "HOST_B: Para cerrar, un recordatorio importante.\n"
        f"HOST_A: {cta}\n"
    )


def _en_script() -> str:
    return (
        "Voices: HOST_A=en-US-AndrewMultilingualNeural; HOST_B=en-US-AvaMultilingualNeural\n"
        "---\n"
        f"HOST_B: {EN.disclosure}\n"
        "## Section: AI Frameworks\n"
        "HOST_A: This week we found three projects that stand out.\n"
        "HOST_B: Exactly, what stood out to us was the GitHub integration.\n"
        f"HOST_A: {EN.cta}\n"
    )


# --- extract_spoken_text -----------------------------------------------------


def test_extract_spoken_text_ignores_sections_and_metadata() -> None:
    turns = extract_spoken_text(_es_script(), ["HOST_A", "HOST_B"])
    speakers = {label for label, _ in turns}
    assert speakers == {"HOST_A", "HOST_B"}
    # The non-spoken section header and the Voices/--- metadata are excluded.
    assert all("Section" not in text and "Voices" not in text for _, text in turns)


# --- Happy paths -------------------------------------------------------------


def test_clean_spanish_script_passes() -> None:
    result = evaluate_localization(_es_script(), config=ES)
    assert isinstance(result, LocalizationQAResult)
    assert result.passed
    assert result.errors == ()
    assert result.checks["no_english_leakage"]
    assert result.checks["disclosure_present"]
    assert result.checks["cta_present"]


def test_clean_french_script_passes() -> None:
    script = (
        "Voices: HOST_A=fr-FR-RemyMultilingualNeural; HOST_B=fr-FR-VivienneMultilingualNeural\n"
        "---\n"
        f"HOST_B: {FR.disclosure}\n"
        "## Section: Cadres d'IA\n"
        "HOST_A: Cette semaine, nous avons repéré un projet remarquable sur GitHub.\n"
        "HOST_B: Son intégration avec l'API est vraiment soignée.\n"
        f"HOST_A: {FR.cta}\n"
    )
    result = evaluate_localization(script, config=FR)
    assert result.passed, result.errors


def test_english_default_locale_unaffected() -> None:
    result = evaluate_localization(_en_script(), config=EN)
    assert result.passed, result.errors
    # Leakage detection is skipped for the default language even though the
    # dialogue is full of English function words.
    assert result.checks["no_english_leakage"] is True


def test_generation_context_is_accepted() -> None:
    ctx = GenerationContext.from_language_config(ES)
    result = evaluate_localization(_es_script(), config=ctx)
    assert result.passed, result.errors


# --- Hard-fail: leakage ------------------------------------------------------


def test_untranslated_english_leakage_fails_gate() -> None:
    result = evaluate_localization(_es_script(leak=True), config=ES)
    assert not result.passed
    assert not result.checks["no_english_leakage"]
    assert any("untranslated English leakage" in err for err in result.errors)


def test_technical_english_terms_do_not_trip_leakage() -> None:
    # GitHub, API, CI/CD appear in the clean script and must NOT fail the gate.
    result = evaluate_localization(_es_script(), config=ES)
    assert result.checks["no_english_leakage"]


def test_leakage_threshold_constant_is_respected() -> None:
    # A single stray English token stays under threshold and does not fail.
    script = (
        "Voices: HOST_A=es-MX-JorgeMultilingualNeural; HOST_B=es-MX-DaliaMultilingualNeural\n"
        "---\n"
        f"HOST_B: {ES.disclosure}\n"
        "## Section: Frameworks\n"
        "HOST_A: Esta semana encontramos un proyecto the cual destaca.\n"
        "HOST_B: Su integración con GitHub es notable.\n"
        f"HOST_A: {ES.cta}\n"
    )
    result = evaluate_localization(script, config=ES)
    assert LEAKAGE_THRESHOLD == 2
    assert result.passed, result.errors


# --- Hard-fail: disclosure / CTA --------------------------------------------


def test_missing_disclosure_fails_gate() -> None:
    result = evaluate_localization(_es_script(include_disclosure=False), config=ES)
    assert not result.passed
    assert not result.checks["disclosure_present"]
    assert any("disclosure" in err for err in result.errors)


def test_missing_cta_fails_gate() -> None:
    result = evaluate_localization(_es_script(include_cta=False), config=ES)
    assert not result.passed
    assert not result.checks["cta_present"]
    assert any("call-to-action" in err for err in result.errors)


def test_early_site_mention_does_not_satisfy_closing_cta() -> None:
    # Regression: production scripts mention the site (www.claracle.com) in the
    # opening welcome/disclosure (see script_gen.py / episode.py). An early
    # mention must NOT satisfy the *closing* CTA requirement — only a CTA in the
    # closing turns should pass the gate.
    script = (
        "Voices: HOST_A=es-MX-JorgeMultilingualNeural; HOST_B=es-MX-DaliaMultilingualNeural\n"
        "---\n"
        f"HOST_B: {ES.disclosure}\n"
        "HOST_A: Bienvenidos, pueden seguirnos en www.claracle.com cada semana.\n"
        "## Section: Frameworks de IA\n"
        "HOST_B: Esta semana encontramos un proyecto que destaca por su arquitectura.\n"
        "HOST_A: Su integración con GitHub y la API es notable.\n"
        "HOST_B: El equipo usa CI/CD de forma ejemplar.\n"
        "HOST_A: Gracias por acompañarnos hoy, nos vemos la próxima semana.\n"
    )
    result = evaluate_localization(script, config=ES)
    assert not result.passed
    assert not result.checks["cta_present"]
    assert any("call-to-action" in err for err in result.errors)


# --- Advisory checks ---------------------------------------------------------


def test_host_persona_inconsistency_warns_not_fails() -> None:
    script = (
        "Voices: HOST_A=es-MX-JorgeMultilingualNeural; HOST_B=es-MX-DaliaMultilingualNeural\n"
        "---\n"
        f"HOST_B: {ES.disclosure}\n"
        "## Section: Frameworks\n"
        "Narrador: Esta semana encontramos un proyecto que destaca.\n"
        "HOST_B: Su integración con GitHub es notable.\n"
        f"HOST_A: {ES.cta}\n"
    )
    result = evaluate_localization(script, config=ES, host_a_name="HOST_A", host_b_name="HOST_B")
    # Still passes (advisory), but flags the unexpected speaker.
    assert result.passed, result.errors
    assert result.checks["host_persona_consistent"] is False
    assert any("unexpected speaker" in w for w in result.warnings)


def test_overgeneralization_is_flagged_for_rai() -> None:
    script = (
        "Voices: HOST_A=es-MX-JorgeMultilingualNeural; HOST_B=es-MX-DaliaMultilingualNeural\n"
        "---\n"
        f"HOST_B: {ES.disclosure}\n"
        "## Section: Frameworks\n"
        "HOST_A: Todos los desarrolladores piensan exactamente igual sobre esto.\n"
        "HOST_B: Su integración con GitHub es notable.\n"
        f"HOST_A: {ES.cta}\n"
    )
    result = evaluate_localization(script, config=ES)
    assert result.passed, result.errors  # advisory, not a hard fail
    assert result.checks["rai_review_clear"] is False
    assert result.flags
    assert any("localization-rai-checklist.md" in f for f in result.flags)


def test_extra_flag_terms_are_detected() -> None:
    result = evaluate_localization(_es_script(), config=ES, flag_terms=["arquitectura"])
    assert result.checks["rai_review_clear"] is False
    assert result.flags


# --- Gate aggregation --------------------------------------------------------


def test_localization_gate_blocks_on_any_failure() -> None:
    ok = evaluate_localization(_es_script(), config=ES)
    bad = evaluate_localization(_es_script(leak=True), config=FR, language="fr", locale="fr-FR")
    gate = localization_gate([ok, bad])
    assert gate["passed"] is False
    assert "localization_qa_failed:fr-FR" in gate["blocked_by"]
    assert "localization_qa_failed:es-419" not in gate["blocked_by"]
    assert set(gate["per_locale"]) == {"es-419", "fr-FR"}


def test_localization_gate_passes_when_all_pass() -> None:
    ok_es = evaluate_localization(_es_script(), config=ES)
    ok_en = evaluate_localization(_en_script(), config=EN)
    gate = localization_gate([ok_es, ok_en])
    assert gate["passed"] is True
    assert gate["blocked_by"] == []


# --- Surface re-export -------------------------------------------------------


def test_validation_reexports_localization_entrypoint() -> None:
    assert exported_evaluate is evaluate_localization
