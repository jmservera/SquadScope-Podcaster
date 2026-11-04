"""Deterministic grounding gate: rules, termination, determinism and replay."""

from __future__ import annotations

import json
import logging
import time

import pytest

from podcaster.claim_extraction import (
    _EMPIRICAL_ASSERTION,
    _QUALIFICATION,
    ClaimGroundingError,
    claims_to_ledger_json,
)
from podcaster.config import PodcastConfig, ScriptDirections
from podcaster.grounding_gate import (
    _LEAD_INS,
    check_script,
    claims_from_result,
    enforce,
    grounding_mode,
    requested_word_bounds,
    run_advisory,
)
from podcaster.grounding_spans import build_span_table
from podcaster.ownership_tone import find_violations
from podcaster.script_gen import spoken_turns

ARTICLE = (
    "# Pelican Weekly\n\n"
    "Acme Labs released Pelican 2.0 with a new planner module. "
    "The planner reduced failed runs by 12% in internal tests. "
    "Maintainers of Orchard said the review queue is not growing. "
    "The release ships a command line tool for replaying agent sessions. "
    "Contributors in Lisbon wrote the migration guide for Orchard users. "
    "Early adopters described the onboarding as slow but predictable.\n"
)
NEWS = "Breaking: Acme Labs published a security advisory for Pelican 1.9 plugins today."

CFG = PodcastConfig()
A, B = CFG.host_a.name, CFG.host_b.name
DISC = CFG.ai_voice_disclosure


def script(*turns: str, with_disclosure: bool = True) -> str:
    lines = [f"{A if i % 2 == 0 else B}: {t}" for i, t in enumerate(turns)]
    if with_disclosure:
        lines.append(f"{B}: {DISC}")
    return "Title: t\n---\n\n" + "\n".join(lines) + "\n\nHost outro: review\n"


def gate(text: str, **kwargs):
    kwargs.setdefault("breaking_news", NEWS)
    return check_script(text, article_content=ARTICLE, podcast_config=CFG, **kwargs)


GOOD = (
    "According to the authors, Acme Labs released Pelican 2.0 with a new planner module.",
    "The authors report that the planner reduced failed runs by 12% in internal tests.",
    "What do you think about that?",
)


def test_passing_script_and_ledger_shape():
    result = gate(script(*GOOD, "Welcome back to the show."))
    assert result.ok, result.summary()
    kinds = [t.kind for t in result.turns]
    assert kinds == ["fact", "fact", "chat", "chat", "disclosure"]
    assert result.turns[0].span_ids and result.turns[0].span_ids[0].startswith("A")
    claims = claims_from_result(result, "https://example.test/a", "construct")
    rows = json.loads(claims_to_ledger_json(claims))
    assert len(rows) == len(spoken_turns(script(*GOOD, "Welcome back to the show."), CFG))
    assert all(r["verified"] is False for r in rows)
    assert rows[0]["span_ids"] and rows[0]["grounding_mode"] == "construct"
    assert {"claim_id", "script_excerpt", "source_url", "source_quote", "source_kind"} <= set(
        rows[0]
    )


@pytest.mark.parametrize(
    ("turn", "failure"),
    [
        ("Acme Labs released Pelican 2.0 with a new planner module.", "qualification"),
        (
            "The authors report that the planner reduced failed runs by 18% in internal tests.",
            "unsupported",
        ),
        (
            "The authors report that Zephyr Corp released Pelican 2.0 with a planner module.",
            "unsupported",
        ),
        ("The authors report that the review queue is growing at Orchard.", "unsupported"),
        (
            "According to the article, Acme Labs released Pelican 2.0 with a new planner module.",
            "violation",
        ),
        ("The authors report a surge in planner adoption across Orchard teams.", "empirical"),
        ('According to the authors, "Acme Labs shipped Pelican 3.0 with a new planner".', "quote"),
        ("See https://example.test for the authors report on Pelican 2.0 planner.", "format"),
        (
            "Honestly the planner module makes everything faster than before for everyone.",
            "qualification",
        ),
        ("That is 40 percent better.", "qualification"),
    ],
)
def test_failure_classes(turn, failure):
    result = gate(script(GOOD[0], turn))
    assert result.failures == {2: failure}


def test_quote_turn_and_cross_source_rejected():
    ok = 'According to the authors, "Acme Labs released Pelican 2.0 with a new planner module."'
    assert gate(script(ok)).turns[0].kind == "quote"
    mixed = (
        'According to the authors, "Acme Labs released Pelican 2.0 with a new planner module." '
        'The authors report "Acme Labs published a security advisory for Pelican 1.9 plugins".'
    )
    assert gate(script(mixed)).failures == {1: "quote"}


def test_breaking_news_provenance():
    result = gate(
        script(
            "The authors report that Acme Labs published a security advisory "
            "for Pelican 1.9 plugins."
        )
    )
    assert result.ok and result.turns[0].source_kind == "breaking_news"
    assert result.turns[0].span_ids[0].startswith("N")


def test_global_rules_disclosure_and_fail_closed():
    assert gate(script(*GOOD, with_disclosure=False)).global_failures == ("ai_disclosure_count",)
    twice = script(f"{DISC}", f"{DISC}", *GOOD)
    assert "ai_disclosure_count" in gate(twice).global_failures
    with pytest.raises(ClaimGroundingError, match="empty source span table"):
        check_script(
            script(*GOOD), article_content="# Title\n", breaking_news=None, podcast_config=CFG
        )


def test_word_bounds_min_only_and_range():
    text = script(*GOOD)
    words = gate(text).words
    assert gate(text, word_bounds=(words, None)).ok
    assert gate(text, word_bounds=(words + 1, None)).global_failures == ("word_range",)
    assert gate(text, word_bounds=(1, words - 1)).global_failures == ("word_range",)
    assert gate(text, word_bounds=(words, words)).ok


def _directions(fmt: str) -> ScriptDirections:
    return ScriptDirections.from_payload({"script_directions": {"episode_style": {"format": fmt}}})


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("8-10 minutes, 1001-1700 spoken words", (1001, 1700)),
        ("at least 1001 spoken words", (1001, None)),
        ("1,001+ words", (1001, None)),
        ("minimum of 1001 words", (1001, None)),
        ("1001 words minimum", (1001, None)),
        ("8-10 minutes", None),
    ],
)
def test_requested_word_bounds(fmt, expected):
    assert requested_word_bounds(_directions(fmt)) == expected
    assert requested_word_bounds(None) is None


def test_attribution_templates_pass_both_regexes_per_locale():
    spans = build_span_table(ARTICLE)
    from podcaster.grounding_gate import render_quote_turn

    for lang in _LEAD_INS:
        text = render_quote_turn(spans[0], lang)
        assert _QUALIFICATION.search(text), lang
        assert not find_violations(f"{A}: {text}"), lang
        assert not _EMPIRICAL_ASSERTION.search(text)
    assert _LEAD_INS["es"].startswith("Según los autores")
    assert _LEAD_INS["fr"].startswith("Selon les auteurs")


@pytest.mark.parametrize("lang", ["en", "es", "fr"])
def test_downgrade_is_verbatim_deterministic_and_terminates(lang):
    bad = script(
        GOOD[0],
        "Totally invented claim about Zephyr shipping 99 products.",
        GOOD[1],
        "The authors report that the review queue is growing at Orchard.",
    )
    outcome = enforce(
        bad, article_content=ARTICLE, breaking_news=NEWS, podcast_config=CFG, language=lang
    )
    assert outcome.result.ok and outcome.downgraded == (2, 4)
    before, after = spoken_turns(bad, CFG), spoken_turns(outcome.script, CFG)
    assert len(before) == len(after)
    for i, (b, a) in enumerate(zip(before, after), 1):
        if i not in outcome.downgraded:
            assert a == b  # passing turns stay byte-identical
        else:
            assert outcome.result.turns[i - 1].kind == "quote"
            assert outcome.result.turns[i - 1].quote in ARTICLE
            assert _LEAD_INS[lang].split("{")[0] in a
    # Non-spoken lines (header/outro) are unchanged.
    assert outcome.script.splitlines()[0] == bad.splitlines()[0]
    assert outcome.script.endswith("Host outro: review\n")
    again = enforce(
        bad, article_content=ARTICLE, breaking_news=NEWS, podcast_config=CFG, language=lang
    )
    assert again.script == outcome.script


def test_downgrade_respects_word_floor_or_fails_closed():
    bad = script(GOOD[0], "Invented unsupported claim about Zephyr.")
    words = gate(bad).words
    outcome = enforce(
        bad,
        article_content=ARTICLE,
        breaking_news=NEWS,
        podcast_config=CFG,
        word_bounds=(words, None),
    )
    assert outcome.result.words >= words
    with pytest.raises(ClaimGroundingError, match="word_range"):
        enforce(
            bad,
            article_content=ARTICLE,
            breaking_news=NEWS,
            podcast_config=CFG,
            word_bounds=(10_000, None),
        )


def test_repair_bounded_and_wall_clock_budget(caplog):
    bad = script(GOOD[0], "Invented claim about Zephyr and 99 products.")
    calls = []

    def never_fixes(s, failing, findings):
        calls.append(sorted(failing))
        assert "Zephyr" not in findings  # findings carry classes, not turn text
        return None

    out = enforce(
        bad,
        article_content=ARTICLE,
        breaking_news=NEWS,
        podcast_config=CFG,
        repairer=never_fixes,
        max_repairs=3,
    )
    assert len(calls) == 3 and out.downgraded == (2,)

    calls.clear()
    out = enforce(
        bad,
        article_content=ARTICLE,
        breaking_news=NEWS,
        podcast_config=CFG,
        repairer=never_fixes,
        started=time.monotonic() - 1000,
    )
    assert calls == [] and out.downgraded == (2,)  # no LLM call after the budget


def test_good_repair_accepted_and_passing_turn_tampering_discarded():
    bad = script(GOOD[0], "Invented claim about Zephyr.")
    fixed = GOOD[1]

    def good(s, failing, findings):
        return s.replace("Invented claim about Zephyr.", fixed)

    out = enforce(
        bad, article_content=ARTICLE, breaking_news=NEWS, podcast_config=CFG, repairer=good
    )
    assert out.downgraded == () and out.repair_rounds == 1 and out.result.ok

    def tamper(s, failing, findings):
        return s.replace("Invented claim about Zephyr.", fixed).replace(
            "released Pelican 2.0", "released Pelican 2.0 today"
        )

    out = enforce(
        bad, article_content=ARTICLE, breaking_news=NEWS, podcast_config=CFG, repairer=tamper
    )
    assert out.downgraded == (2,) and "today" not in out.script


def test_provider_errors_propagate_from_repairer():
    def boom(s, failing, findings):
        raise RuntimeError("provider down")

    bad = script(GOOD[0], "Invented claim about Zephyr.")
    with pytest.raises(RuntimeError):
        enforce(bad, article_content=ARTICLE, breaking_news=NEWS, podcast_config=CFG, repairer=boom)


def test_determinism_100_runs_and_no_network(monkeypatch):
    import socket

    def blocked(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    text = script(*GOOD, "Invented claim about Zephyr 7 times.")
    first = gate(text)
    outputs = {repr(gate(text)) for _ in range(100)}
    assert outputs == {repr(first)}
    out = {
        enforce(text, article_content=ARTICLE, breaking_news=NEWS, podcast_config=CFG).script
        for _ in range(20)
    }
    assert len(out) == 1


def test_replay_fixture_scripts_stable_and_content_free_logs(caplog):
    caplog.set_level(logging.INFO, logger="podcaster.grounding_gate")
    fixtures = [
        script(*GOOD),
        script(
            "Honestly, what a week.",
            "The authors say Orchard maintainers said the queue is not growing.",
        ),
        script("Contributors in Lisbon wrote it.", "Early adopters described onboarding.", *GOOD),
    ]
    for text in fixtures:
        out = enforce(text, article_content=ARTICLE, breaking_news=NEWS, podcast_config=CFG)
        assert out.result.ok
        assert (
            enforce(
                out.script, article_content=ARTICLE, breaking_news=NEWS, podcast_config=CFG
            ).downgraded
            == ()
        )  # idempotent
    assert "Zephyr" not in caplog.text and "Orchard" not in caplog.text


def test_advisory_never_fails_but_filter_is_terminal():
    from podcaster.provider_filter import FilterSafetyError

    assert run_advisory(lambda t: 1 / 0).status == "failed"
    assert run_advisory(lambda t: [1, 2]).status == "ok"
    assert run_advisory(lambda t: time.sleep(2), timeout=0.05, grace=0.05).status == "timeout"
    assert run_advisory(
        lambda t: (_ for _ in ()).throw(ClaimGroundingError("x", failure_classes={1: "quote"}))
    ).detail == {"failed_turns": 1}

    def filtered(_transport):
        raise FilterSafetyError("blocked", request_id="r")

    with pytest.raises(FilterSafetyError):
        run_advisory(filtered)


def test_advisory_cancelled_after_cutoff_and_late_filter_error_is_terminal():
    from podcaster.grounding_gate import AdvisoryCancelled
    from podcaster.provider_filter import FilterSafetyError

    sent = []

    def worker(transport):
        time.sleep(0.2)
        try:
            transport(object())  # past the cutoff: must refuse, never reach the network
        except AdvisoryCancelled:
            sent.append("refused")
            raise FilterSafetyError("blocked", request_id="late") from None

    with pytest.raises(FilterSafetyError):
        run_advisory(worker, timeout=0.05, grace=2.0)
    assert sent == ["refused"]

    def cancelled(transport):
        time.sleep(0.2)
        transport(object())

    assert run_advisory(cancelled, timeout=0.05, grace=2.0).status == "timeout"


def test_short_quotations_are_validated():
    bad = gate(
        script(
            'According to the authors, Acme Labs released Pelican 2.0 with a new "autonomous" '
            "planner module."
        )
    )
    assert bad.failures.get(1) == "quote"
    ok = gate(
        script('According to the authors, Acme Labs released Pelican 2.0 with a new "planner".')
    )
    assert ok.turns[0].failure is None


def test_quote_shortcut_needs_single_quote_and_nothing_else():
    q = "Acme Labs released Pelican 2.0 with a new planner module."
    trailing = gate(script(f'According to the authors, "{q}" Version 99.'))
    assert trailing.failures.get(1) in {"unsupported", "quote"}
    two = gate(
        script(
            'According to the authors, "Acme Labs released Pelican 2.0" and '
            '"The planner reduced failed runs by 12% in internal tests."'
        )
    )
    assert two.turns[0].kind != "quote"
    single = gate(script(f'According to the authors, "{q}"'))
    assert single.turns[0].kind == "quote" and single.turns[0].failure is None


def test_host_name_inside_quotation_is_preserved():
    cfg = PodcastConfig()
    evidence = "Acme Labs credits {h} for releasing Pelican 2.0. Other text follows here."
    article = evidence.format(h=cfg.host_a.name)
    text = (
        f"Title: t\n---\n\n{A}: According to the authors, "
        f'"Acme Labs credits {A} for releasing Pelican 2.0."\n'
    )
    result = check_script(text, article_content=article, breaking_news=None, podcast_config=cfg)
    assert result.turns[0].failure is None and result.turns[0].kind == "quote"


def test_quote_scan_is_linear_on_pathological_input():
    from podcaster.grounding_gate import _quotes

    start = time.monotonic()
    assert _quotes("\u00ab" + " " * 200000) == []
    assert _quotes("\u00ab " * 100000) == []
    assert time.monotonic() - start < 2
    assert _quotes('say "a b" and \u201cc d\u201d and \u00ab e f \u00bb') == ["a b", "c d", "e f"]


def test_both_mode_provenance_replaced_together_from_gate():
    from podcaster.claim_extraction import Claim
    from podcaster.grounding_gate import apply_gate_provenance

    result = gate(script(*GOOD))
    turn = result.turns[0]
    stale = [
        Claim(
            f"c{i}",
            t.text,
            "",
            "stale",
            None,
            False,
            "",
            source_kind="breaking_news",
            span_ids=("N1",),
        )
        for i, t in enumerate(result.turns)
    ]
    fixed = apply_gate_provenance(stale, result, "https://example.test/a", "both")
    assert fixed[0].source_kind == "article" == turn.source_kind
    assert fixed[0].span_ids == turn.span_ids and fixed[0].span_ids[0].startswith("A")
    assert fixed[0].source_quote == turn.quote
    assert fixed[0].source_url == "https://example.test/a"
    assert all(c.grounding_mode == "both" for c in fixed)


def test_mode_flag_defaults_to_audit(monkeypatch):
    monkeypatch.delenv("GROUNDING_MODE", raising=False)
    assert grounding_mode() == "audit"
    for value, expected in [("construct", "construct"), (" Both ", "both"), ("bogus", "audit")]:
        monkeypatch.setenv("GROUNDING_MODE", value)
        assert grounding_mode() == expected


def _job(tmp_path, monkeypatch, mode, generator, advisory):
    from datetime import datetime, timezone
    from unittest.mock import Mock

    from podcaster import jobs
    from podcaster.storage import LocalStorageBackend

    monkeypatch.setenv("GROUNDING_MODE", mode)
    monkeypatch.setattr(jobs, "generate_script", generator)
    monkeypatch.setattr(jobs, "repair_turns_in_script", Mock(return_value=None))
    monkeypatch.setattr(jobs, "extract_claims", advisory)
    payload = {
        "week": "2026-W45",
        "article_url": "https://example.com/w45",
        "article_title": "Pelican Weekly",
        "article_content": ARTICLE,
        "breaking_news": NEWS,
    }
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    result = jobs.run_generation_job(
        payload, storage=storage, now=datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
    )
    ledger = json.loads(storage.get_bytes(f"jobs/{result.manifest['job_id']}/claim-ledger.json"))
    return result, ledger


@pytest.mark.usefixtures("offline_script_generation")
def test_construct_mode_gate_decides_and_audit_is_advisory(tmp_path, monkeypatch):
    from unittest.mock import Mock

    bad = script(*GOOD, "Invented claim about Zephyr shipping 99 products.")
    audit = Mock(side_effect=ClaimGroundingError("turn 4: unsupported", failure_classes={4: "x"}))
    result, ledger = _job(tmp_path, monkeypatch, "construct", Mock(return_value=bad), audit)
    generation = result.manifest["generation"]
    assert generation["grounding_mode"] == "construct"
    assert generation["grounding"]["downgraded_turns"] == 1
    assert generation["grounding"]["advisory_audit"]["gating"] is False
    assert generation["grounding"]["advisory_audit"]["status"] == "failed"
    assert audit.call_count == 1
    assert all(row["verified"] is False for row in ledger)
    assert all(row["grounding_mode"] == "construct" for row in ledger)
    assert result.manifest["status"] == "review_pending" or result.manifest["status"] == "dry_run"


@pytest.mark.usefixtures("offline_script_generation")
def test_audit_mode_default_unchanged(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from podcaster.claim_extraction import Claim

    monkeypatch.delenv("GROUNDING_MODE", raising=False)
    turns = spoken_turns(script(*GOOD), CFG)
    claims = [
        Claim(f"claim_{i:03d}", t, "https://example.com/w45", None, None, False, "n")
        for i, t in enumerate(turns, 1)
    ]
    result, ledger = _job(
        tmp_path, monkeypatch, "audit", Mock(return_value=script(*GOOD)), Mock(return_value=claims)
    )
    assert result.manifest["generation"]["grounding_mode"] == "audit"
    assert len(ledger) == len(turns) and "span_ids" not in ledger[0]


@pytest.mark.parametrize("name", ["Theodore", "Veranda", "Veracruz"])
def test_name_exemption_respects_word_boundaries(name):
    result = gate(script(GOOD[0], f"I wonder what {name} thinks about that."))
    assert result.failures == {2: "qualification"}


def test_plain_host_name_still_exempt_in_chat():
    result = gate(script(GOOD[0], f"I wonder what {A} thinks about that."))
    assert result.ok, result.summary()


def test_host_name_cannot_launder_unsupported_attribution():
    claim = f"According to the authors, {A} released Pelican 2.0 with a new planner module."
    assert gate(script(GOOD[0], claim)).failures == {2: "unsupported"}


def test_host_name_in_source_sentence_resolves_via_original_text():
    article = f"{ARTICLE}\n{A} released Pelican 3.0 with a new planner module.\n"
    claim = f"According to the authors, {A} released Pelican 3.0 with a new planner module."
    result = check_script(
        script(GOOD[0], claim), article_content=article, breaking_news=NEWS, podcast_config=CFG
    )
    assert result.ok, result.summary()
