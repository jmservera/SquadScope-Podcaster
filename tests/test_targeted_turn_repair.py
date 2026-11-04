import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from podcaster import claim_extraction, jobs, script_gen
from podcaster.claim_extraction import ClaimGroundingError
from podcaster.config import PodcastConfig
from podcaster.jobs import ScriptGenerationFailed, run_generation_job
from podcaster.script_gen import merge_turn_replacements, repair_turns_in_script
from podcaster.storage import LocalStorageBackend
from tests.test_jobs import VALID_ARTICLE_CONTENT
from tests.test_script_gen import _fake_token_provider, _mock_config

pytestmark = pytest.mark.usefixtures("offline_script_generation")
CONFIG = PodcastConfig()
SCRIPT = (
    "Title: Original\nWeek: 2026-W44\n---\n"
    "## Section: Intro\n"
    "Theo: The article describes a product launch with several engineering tradeoffs.\n"
    "Vera: Teams were surprised by the speed of the rollout this week overall.\n"
    "Theo: Customers reacted to the platform changes in many different ways.\n"
)
FAILING = {2: "Teams were surprised by the speed of the rollout this week overall."}
GOOD = "According to the authors, teams saw a fast rollout of the product launch."


def merge(replacements, *, script=SCRIPT, failing=None, word_range=None):
    return merge_turn_replacements(
        script,
        replacements,
        failing_turns=FAILING if failing is None else failing,
        podcast_config=CONFIG,
        word_range=word_range,
    )


def test_merge_replaces_only_failing_turn_byte_identical():
    merged = merge({2: GOOD})
    assert merged == SCRIPT.replace(FAILING[2], GOOD)
    assert merged.count("\n") == SCRIPT.count("\n")


def test_merge_preserves_indentation_and_crlf():
    script = SCRIPT.replace("\nVera:", "\n  Vera:").replace("overall.\n", "overall.\r\n")
    assert merge({2: GOOD}, script=script) == script.replace(FAILING[2], GOOD)


@pytest.mark.parametrize(
    "replacements",
    [
        {},
        {1: GOOD},
        {3: GOOD},
        {2: "line one\nline two with several more words here"},
        {2: "According to the authors, see https://evil.example/x for the rollout news."},
        {2: "According to the authors,\x07 teams saw a fast rollout of the launch."},
        {2: " " + GOOD},
        {2: "According to the article, teams saw a fast rollout of the product launch."},
        {2: "Customers reacted to the platform changes in many different ways."},
        {2: "Too short."},
        {2: GOOD + " " + "extra " * 30},
        {2: 5},
        {"2": GOOD},
    ],
)
def test_merge_rejects_unsafe_patch(replacements):
    assert merge(replacements) is None


def test_merge_rejects_total_word_range_violation():
    total = sum(len(t.split()) for t in script_gen.spoken_turns(SCRIPT, CONFIG))
    assert merge({2: GOOD}, word_range=(total - 3, total + 3)) is not None
    assert merge({2: GOOD}, word_range=(total + 20, total + 40)) is None


def _transport(content, seen=None):
    def transport(request):
        if seen is not None:
            seen.append(json.loads(request.data))
        return json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    return transport


def _repair(content, seen=None):
    return repair_turns_in_script(
        SCRIPT,
        failing_turns=FAILING,
        findings="turn 2: quote",
        week="2026-W44",
        article_title="Title",
        article_content=VALID_ARTICLE_CONTENT,
        config=_mock_config(),
        podcast_config=CONFIG,
        token_provider=_fake_token_provider,
        transport=_transport(content, seen),
    )


def test_repair_turns_in_script_one_json_call_and_fenced_article():
    seen = []
    merged = _repair(json.dumps({"replacements": [{"turn": 2, "text": GOOD}]}), seen)
    assert merged == SCRIPT.replace(FAILING[2], GOOD)
    assert len(seen) == 1
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert script_gen.FENCE_OPEN in seen[0]["messages"][1]["content"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"replacements": []}),
        json.dumps({"replacements": [{"turn": 1, "text": GOOD}]}),
        json.dumps({"replacements": [{"turn": 2, "text": GOOD, "extra": 1}]}),
        json.dumps({"replacements": [{"turn": 2, "text": GOOD}, {"turn": 2, "text": GOOD + " x"}]}),
    ],
)
def test_repair_turns_in_script_rejects_malformed_patch(content):
    assert _repair(content) is None


def test_targeted_repairable_requires_only_repairable_classes():
    turns = {1: "a"}
    assert ClaimGroundingError(
        "x", repair_turns=turns, failure_classes={1: "quote"}
    ).targeted_repairable
    assert not ClaimGroundingError(
        "x", repair_turns=turns, failure_classes={1: "quote", 2: "empirical"}
    ).targeted_repairable
    assert not ClaimGroundingError("x", repair_turns=turns).targeted_repairable


PAYLOAD = {
    "week": "2026-W44",
    "article_url": "https://example.com/w44",
    "article_title": "Source-grounded review",
    "article_content": VALID_ARTICLE_CONTENT,
}
NOW = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)


def _error(index=1, cls="quote"):
    return ClaimGroundingError(
        f"turn {index}: {cls}", repair_turns={index: "t"}, failure_classes={index: cls}
    )


def _run(tmp_path, monkeypatch, audits, repairs, generations):
    generator = Mock(side_effect=generations)
    repairer = Mock(side_effect=repairs)
    audit = Mock(side_effect=audits)
    monkeypatch.setattr(jobs, "generate_script", generator)
    monkeypatch.setattr(jobs, "repair_turns_in_script", repairer)
    monkeypatch.setattr(jobs, "extract_claims", audit)
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    return generator, repairer, storage


def _claim():
    return [
        claim_extraction.Claim(
            claim_id="claim_001",
            script_excerpt="x",
            source_url="",
            source_quote="q",
            source_paragraph=None,
            verified=False,
            editor_notes="",
        )
    ]


SCRIPT_A = "Title: A\n---\nTheo: The article describes one.\nVera: Both voices are synthetic.\n"
SCRIPT_B = SCRIPT_A.replace("one", "two")
SCRIPT_C = SCRIPT_A.replace("one", "three")


def test_targeted_repair_succeeds_without_regenerating(tmp_path, monkeypatch):
    generator, repairer, storage = _run(
        tmp_path,
        monkeypatch,
        audits=[_error(), _error(1, "qualification"), _claim()],
        repairs=[SCRIPT_B, SCRIPT_C],
        generations=[SCRIPT_A],
    )
    run_generation_job(PAYLOAD, storage=storage, now=NOW)
    assert generator.call_count == 1
    assert repairer.call_count == 2
    assert repairer.call_args.args[0] == SCRIPT_B
    from podcaster.jobs import build_job_id

    assert storage.get_bytes(f"jobs/{build_job_id(PAYLOAD)}/script.txt") == SCRIPT_C.encode()


def test_rejected_patch_falls_back_to_full_regeneration(tmp_path, monkeypatch):
    generator, repairer, storage = _run(
        tmp_path,
        monkeypatch,
        audits=[_error(), _claim()],
        repairs=[None],
        generations=[SCRIPT_A, SCRIPT_B],
    )
    run_generation_job(PAYLOAD, storage=storage, now=NOW)
    assert repairer.call_count == 1
    assert generator.call_count == 2
    assert generator.call_args.kwargs["previous_script"] == SCRIPT_A


def test_non_targetable_error_uses_full_regeneration_only(tmp_path, monkeypatch):
    generator, repairer, storage = _run(
        tmp_path,
        monkeypatch,
        audits=[ClaimGroundingError("turn 1: empirical"), _claim()],
        repairs=[],
        generations=[SCRIPT_A, SCRIPT_B],
    )
    run_generation_job(PAYLOAD, storage=storage, now=NOW)
    repairer.assert_not_called()
    assert generator.call_count == 2


def test_targeted_repairs_exhausted_fail_closed(tmp_path, monkeypatch):
    budget = jobs.MAX_TARGETED_REPAIRS
    generator, repairer, storage = _run(
        tmp_path,
        monkeypatch,
        audits=[_error()] * (budget + 1 + jobs.MAX_GROUNDING_REPAIRS),
        repairs=[SCRIPT_B] * budget,
        generations=[SCRIPT_A] + [SCRIPT_C] * jobs.MAX_GROUNDING_REPAIRS,
    )
    with pytest.raises(ScriptGenerationFailed, match="exhausted"):
        run_generation_job(PAYLOAD, storage=storage, now=NOW)
    assert repairer.call_count == budget
    assert generator.call_count == 1 + jobs.MAX_GROUNDING_REPAIRS


def test_targeted_repair_deadline_stops_new_repairs(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "GROUNDING_REPAIR_DEADLINE_SECONDS", -1.0)
    generator, repairer, storage = _run(
        tmp_path,
        monkeypatch,
        audits=[_error()] * 10,
        repairs=[SCRIPT_B] * 10,
        generations=[SCRIPT_A],
    )
    with pytest.raises(ScriptGenerationFailed, match="exhausted 2"):
        run_generation_job(PAYLOAD, storage=storage, now=NOW)
    assert generator.call_count == 1
    assert repairer.call_count == 2
