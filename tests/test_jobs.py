from __future__ import annotations

import hashlib
import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import urlparse
from zipfile import ZipFile

import pytest

from podcaster.costs import monthly_ledger_path
from podcaster.generation import generate_artifacts, manifest_bytes
from podcaster.jobs import (
    ReplayCollisionError,
    ScriptGenerationFailed,
    build_job_id,
    replay_identity_sha256,
    run_generation_job,
)
from podcaster.provider_filter import FilterSafetyError
from podcaster.storage import (
    AzureBlobStorageBackend,
    LocalStorageBackend,
    _managed_identity_resource,
    _token_expires_on,
    create_storage_backend,
    normalize_artifact_base_url,
)
from podcaster.validation import RESPONSE_KEYS

VALID_ARTICLE_CONTENT = (
    "This article explains a real product rollout, the engineering tradeoffs behind it, the "
    "customer impact, and the competitive context the hosts should react to in detail this week."
)

pytestmark = pytest.mark.usefixtures("offline_script_generation")


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("stage", ["script", "grounding"])
def test_filter_failure_never_falls_back_or_repairs(tmp_path, monkeypatch, dry_run, stage):
    from podcaster import jobs

    payload = {
        "week": "2026-W44",
        "article_url": "https://example.invalid/article",
        "article_title": "Source-grounded review",
        "article_content": VALID_ARTICLE_CONTENT,
        "dry_run": dry_run,
    }
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    generator = (
        Mock(side_effect=FilterSafetyError("filter failure"))
        if stage == "script"
        else Mock(return_value="Title: Test\n---\nTheo: Factual source.\nVera: A response.")
    )
    grounding = Mock(side_effect=FilterSafetyError("filter failure"))
    monkeypatch.setattr(jobs, "generate_script", generator)
    monkeypatch.setattr(jobs, "extract_claims", grounding)
    artifacts = Mock(wraps=jobs.generate_artifacts)
    monkeypatch.setattr(jobs, "generate_artifacts", artifacts)
    with pytest.raises(ScriptGenerationFailed, match="filtering failed") as caught:
        run_generation_job(payload, storage=storage)
    assert caught.value.quality_reason == "filter_safety"
    assert generator.call_count == 1
    assert grounding.call_count == (0 if stage == "script" else 1)
    artifacts.assert_not_called()
    job_id = build_job_id(payload)
    assert storage.get_bytes(f"jobs/{job_id}/manifest.json") is None
    assert storage.get_bytes(f"jobs/{job_id}/script.txt") is None
    assert json.loads(storage.get_bytes(f"jobs/{job_id}/filter-failure.json"))["status"] == "failed"
    if dry_run:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        assert storage.get_bytes(monthly_ledger_path(month)) is None
    with pytest.raises(ReplayCollisionError):
        run_generation_job(payload, storage=storage)
    assert generator.call_count == 1


def test_budget_block_cannot_delete_sibling_dry_run_filter_marker(tmp_path, monkeypatch):
    from podcaster import jobs

    payload = {
        "week": "2026-W44",
        "article_url": "https://example.invalid/article",
        "article_title": "Source-grounded review",
        "article_content": VALID_ARTICLE_CONTENT,
        "dry_run": True,
    }

    class BlobPrefixStorage(LocalStorageBackend):
        def delete_prefix(self, prefix):
            raise AssertionError(f"raw prefix cleanup could delete sibling job: {prefix}")

    storage = BlobPrefixStorage(tmp_path, "https://example.invalid/artifacts")
    generator = Mock(side_effect=FilterSafetyError("filter failure"))
    monkeypatch.setattr(jobs, "generate_script", generator)
    with pytest.raises(ScriptGenerationFailed, match="filtering failed"):
        run_generation_job(payload, storage=storage)
    dry_id = build_job_id(payload)
    marker = storage.get_bytes(f"jobs/{dry_id}/filter-failure.json")
    replay = storage.get_bytes(f"jobs/{dry_id}/inputs/replay.json")
    assert marker and replay

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    storage.put_bytes(
        monthly_ledger_path(month),
        manifest_bytes(
            {
                "schema_version": "squadscope-podcaster-monthly-cost-ledger-v1",
                "month": month,
                "episodes": [
                    {"job_id": f"other-{index}", "week": "2026-W42", "estimated_total_usd": "0.00"}
                    for index in range(10)
                ],
            }
        ),
        "application/json; charset=utf-8",
    )
    production = {**payload, "dry_run": False}
    result = run_generation_job(production, storage=storage)
    assert result.response["status"] == "failed"
    assert generator.call_count == 1
    assert storage.get_bytes(f"jobs/{dry_id}/filter-failure.json") == marker
    assert storage.get_bytes(f"jobs/{dry_id}/inputs/replay.json") == replay
    with pytest.raises(ReplayCollisionError):
        run_generation_job(payload, storage=storage)


def test_malformed_provider_annotation_stops_job_without_raw_logging_or_replay(
    tmp_path, monkeypatch, caplog
):
    from podcaster import jobs
    from podcaster.script_gen import _request_dialogue

    payload = {
        "week": "2026-W44",
        "article_url": "https://example.invalid/article",
        "article_title": "Source-grounded review",
        "article_content": VALID_ARTICLE_CONTENT,
        "dry_run": True,
    }
    response = {
        "id": "unsafe\nprovider-secret",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": "unsafe provider-secret"},
                "content_filter_results": {"hate": {"error_details": {"code": "unknown"}}},
            }
        ],
    }

    def generate(*args, **kwargs):
        return _request_dialogue(
            [{"role": "user", "content": "test"}],
            url="https://example.invalid/chat",
            token="fake",
            transport=lambda request: json.dumps(response).encode(),
        )

    generator = Mock(side_effect=generate)
    monkeypatch.setattr(jobs, "generate_script", generator)
    grounding = Mock()
    monkeypatch.setattr(jobs, "extract_claims", grounding)
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    with pytest.raises(ScriptGenerationFailed, match="filtering failed"):
        run_generation_job(payload, storage=storage)
    marker = storage.get_bytes(f"jobs/{build_job_id(payload)}/filter-failure.json")
    assert marker is not None
    assert b"provider-secret" not in marker
    assert "provider-secret" not in caplog.text
    grounding.assert_not_called()
    with pytest.raises(ReplayCollisionError):
        run_generation_job(payload, storage=storage)
    assert generator.call_count == 1


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_grounding_repair_stages_only_exact_corrected_script_or_fails_closed(
    tmp_path, monkeypatch, repair_succeeds
):
    from podcaster import claim_extraction, jobs
    from podcaster.episode import parse_script_segments

    payload = {
        "week": "2026-W44",
        "article_url": "https://example.com/w44",
        "article_title": "Source-grounded review",
        "article_content": VALID_ARTICLE_CONTENT,
    }
    original = (
        "Title: Original\n---\n"
        "Theo: We witnessed a surge of exploit repositories.\n"
        "Vera: Both voices are synthetic.\n"
    )
    corrected = (
        "Title: Corrected\n---\n"
        "Theo: The article explains a real product rollout and its engineering tradeoffs.\n"
        "Vera: Both voices are synthetic.\n"
    )
    final = corrected if repair_succeeds else original
    generator = Mock(side_effect=[original, final])
    artifacts = Mock(wraps=jobs.generate_artifacts)
    audits = []

    def audit_transport(request):
        body = json.loads(request.data)
        audits.append(body)
        script = original if len(audits) == 1 else final
        turns = parse_script_segments(script, jobs.PodcastConfig())
        rows = [
            {
                "script_excerpt": text,
                "grounding": "supported" if index == 0 else "nonfactual",
                "source_quote": VALID_ARTICLE_CONTENT if index == 0 else None,
                "verified": True,
            }
            for index, (_, text) in enumerate(turns)
        ]
        return json.dumps(
            {"choices": [{"message": {"content": json.dumps({"claims": rows})}}]}
        ).encode()

    monkeypatch.setattr(jobs, "generate_script", generator)
    monkeypatch.setattr(jobs, "generate_artifacts", artifacts)
    monkeypatch.setattr(jobs, "extract_claims", claim_extraction.extract_claims)
    monkeypatch.setattr(claim_extraction, "_default_transport", audit_transport)
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    now = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
    job_id = build_job_id(payload)
    if repair_succeeds:
        result = run_generation_job(payload, storage=storage, now=now)
        assert storage.get_bytes(f"jobs/{job_id}/script.txt") == corrected.encode()
        ledger = json.loads(storage.get_bytes(f"jobs/{job_id}/claim-ledger.json"))
        assert [row["script_excerpt"] for row in ledger] == [
            text for _, text in parse_script_segments(corrected, jobs.PodcastConfig())
        ]
        assert all(not row["verified"] for row in ledger)
        assert result.manifest["status"] == "review_pending"
        assert result.manifest["review"]["approved_by"] is None
        assert result.manifest["review"]["gate"]["status"] == "blocked"
        assert "rights_attribution" in result.manifest["review"]["gate"]["checks"]
        assert result.manifest["generation"]["tts_synthesis"]["allowed"] is False
        assert result.manifest["publishing"]["eligible"] is False
        artifacts.assert_called_once()
    else:
        with pytest.raises(ScriptGenerationFailed, match="evidence limit.*exhausted 1") as caught:
            run_generation_job(payload, storage=storage, now=now)
        assert "turn 1" in caught.value.quality_reason
        artifacts.assert_not_called()
        assert storage.get_bytes(f"jobs/{job_id}/manifest.json") is None
        assert storage.get_bytes(f"jobs/{job_id}/script.txt") is None
        assert storage.get_bytes(f"jobs/{job_id}/packets/{job_id}.zip") is None
        monthly = json.loads(storage.get_bytes(monthly_ledger_path("2026-10")))
        assert not any(
            row["job_id"] == job_id and row.get("state") == "reserved"
            for row in monthly["episodes"]
        )
    assert generator.call_count == len(audits) == 2
    first, second = [call.kwargs for call in generator.call_args_list]
    assert "previous_script" not in first
    assert second["previous_script"] == original
    assert "turn 1" in second["repair_feedback"]


def test_disjoint_breaking_news_survives_production_generation_and_alignment(tmp_path, monkeypatch):
    from podcaster import claim_extraction, jobs, script_gen

    news = "Orion released version 4 today."
    article = VALID_ARTICLE_CONTENT.replace(
        "This article explains", "The deployment report explains"
    )
    dialogue = (
        "## Section: Hot off the press\n"
        + "\n".join(f"{host}: {news}" for host in ("Theo", "Vera", "Theo", "Vera"))
        + "\n## Section: Deployment details\n"
        + "\n".join(f"{host}: {article}" for host in ("Theo", "Vera", "Theo", "Vera"))
    )
    prompts = []
    audited_turns = []

    def transport(request):
        body = json.loads(request.data)
        prompts.append(body)
        if body.get("response_format"):
            assert "BREAKING NEWS EVIDENCE" in body["messages"][1]["content"]
            from podcaster.sanitization import FENCE_CLOSE, FENCE_OPEN

            turns = json.loads(
                body["messages"][1]["content"].split("CANONICAL SPOKEN TURNS:\n")[1][
                    len(FENCE_OPEN) : -len(FENCE_CLOSE)
                ]
            )
            audited_turns.extend(turns)
            assert 2000 <= body["max_tokens"] <= 16384
            rows = [
                {
                    "script_excerpt": turn,
                    "grounding": "supported" if turn in (news, article) else "nonfactual",
                    "source_quote": turn if turn in (news, article) else None,
                    "source_kind": "breaking_news" if turn == news else "article",
                }
                for turn in turns
            ]
            content = json.dumps({"claims": rows})
        else:
            content = dialogue
        return json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    generated = []

    def generate(**kwargs):
        script = script_gen.generate_script(**kwargs, transport=transport)
        generated.append(script)
        return script

    monkeypatch.setattr(jobs, "generate_script", generate)
    monkeypatch.setattr(jobs, "extract_claims", claim_extraction.extract_claims)
    monkeypatch.setattr(claim_extraction, "_default_transport", transport)
    payload = {
        "week": "2026-W44",
        "article_url": "https://example.com/article",
        "article_title": "Deployment report",
        "article_content": article,
        "breaking_news": news,
    }
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    result = run_generation_job(payload, storage=storage)
    ledger = json.loads(storage.get_bytes(f"jobs/{result.response['job_id']}/claim-ledger.json"))
    supplemental = [row for row in ledger if row["source_kind"] == "breaking_news"]
    assert 2 <= len(prompts) <= 9
    assert audited_turns == script_gen.spoken_turns(generated[0], jobs.PodcastConfig())
    assert len(supplemental) == 4
    assert all(row["source_quote"] == news and row["source_url"] == "" for row in supplemental)
    assert all(not row["verified"] for row in ledger)
    assert result.manifest["status"] == "review_pending"
    assert result.manifest["generation"]["tts_synthesis"]["allowed"] is False
    assert result.manifest["publishing"]["eligible"] is False


def test_publication_identity_is_persisted_without_changing_legacy_job_id(tmp_path) -> None:
    base_payload = {
        "week": "2026-W37",
        "article_url": "https://example.com/article",
        "article_title": "Reviewed weekly signal",
        "article_content": VALID_ARTICLE_CONTENT,
        "article_sha256": "a" * 64,
    }
    payload = {
        **base_payload,
        "publish_run_id": "12345",
        "manifest_sha256": "b" * 64,
    }
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")

    result = run_generation_job(payload, storage=storage)

    assert result.response["job_id"] == build_job_id(base_payload)
    assert result.manifest["request"]["publish_run_id"] == "12345"
    assert result.manifest["request"]["manifest_sha256"] == "b" * 64
    assert result.manifest["request"]["publication_identity_mode"] == "canonical"


def test_partial_canonical_identity_is_never_marked_legacy(tmp_path) -> None:
    payload = {
        "week": "2026-W37",
        "article_url": "https://example.com/article",
        "article_title": "Reviewed weekly signal",
        "article_content": VALID_ARTICLE_CONTENT,
        "publish_run_id": "12345",
    }
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    result = run_generation_job(payload, storage=storage)
    assert result.manifest["request"]["publication_identity_mode"] == "canonical"


def test_generation_job_warns_when_podcast_identity_absent(caplog) -> None:
    # Issue #545: when the payload omits podcast_config identity, the pipeline
    # falls back to default host/show names and must log that case so the
    # operator can tell config was missing (not wrong).
    artifact_root = Path(".test-artifacts-545a")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    with caplog.at_level(logging.WARNING):
        run_generation_job(
            {
                "week": "2026-W23",
                "article_url": "https://example.com/article",
                "article_title": "Reviewed weekly signal",
                "article_content": VALID_ARTICLE_CONTENT,
            },
            storage=storage,
            now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
        )
    shutil.rmtree(artifact_root, ignore_errors=True)
    assert any("podcast_config identity absent" in r.getMessage() for r in caplog.records)


def test_generation_job_whitespace_article_title_raises_value_error() -> None:
    artifact_root = Path(".test-artifacts-545c")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    with pytest.raises(ValueError, match="article_title is missing or empty"):
        run_generation_job(
            {
                "week": "2026-W23",
                "article_url": "https://example.com/article",
                "article_title": "   ",
                "article_content": VALID_ARTICLE_CONTENT,
            },
            storage=storage,
            now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
        )
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_generation_job_whitespace_article_content_raises_value_error() -> None:
    artifact_root = Path(".test-artifacts-545d")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    with pytest.raises(ValueError, match="article_content is missing or empty"):
        run_generation_job(
            {
                "week": "2026-W23",
                "article_url": "https://example.com/article",
                "article_title": "A Real Title",
                "article_content": "   ",
            },
            storage=storage,
            now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
        )
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_generation_job_short_article_content_raises_value_error() -> None:
    artifact_root = Path(".test-artifacts-545e")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    with pytest.raises(ValueError, match="article_content is too short"):
        run_generation_job(
            {
                "week": "2026-W23",
                "article_url": "https://example.com/article",
                "article_title": "A Real Title",
                "article_content": "too short",
            },
            storage=storage,
            now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
        )
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_generation_job_silent_when_podcast_identity_present(caplog) -> None:
    artifact_root = Path(".test-artifacts-545b")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    with caplog.at_level(logging.WARNING):
        run_generation_job(
            {
                "week": "2026-W23",
                "article_url": "https://example.com/article",
                "article_title": "A Real Title",
                "podcast_config": {
                    "name": "My Show",
                    "host_a": {"name": "Ada"},
                    "host_b": {"name": "Bo"},
                },
                "article_content": VALID_ARTICLE_CONTENT,
            },
            storage=storage,
            now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
        )
    shutil.rmtree(artifact_root, ignore_errors=True)
    assert not any("podcast_config identity absent" in r.getMessage() for r in caplog.records)
    assert not any("article_title absent" in r.getMessage() for r in caplog.records)


def test_generation_job_stages_manifest_review_gate_and_packet() -> None:
    artifact_root = Path(".test-artifacts")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "article_title": "Reviewed weekly signal",
            "article_content": VALID_ARTICLE_CONTENT,
        },
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert tuple(result.response.keys()) == RESPONSE_KEYS
    assert result.response["status"] == "accepted"
    assert result.manifest["status"] == "review_pending"
    assert result.manifest["review"]["status"] == "pending"
    assert result.manifest["review"]["required"] is True
    assert result.manifest["review"]["mechanism"] == "github_environment"
    assert result.manifest["review"]["environment"] == "podcast-review"
    assert result.manifest["review"]["workflow"] == ".github/workflows/podcast-review-gate.yml"
    assert result.manifest["review"]["gate"]["status"] == "blocked"
    assert result.manifest["review"]["gate"]["approval_required_before"] == "tts_synthesis"
    assert result.manifest["cost_ledger"]["budget"]["status"] == "within_budget"
    assert result.manifest["cost_ledger"]["readiness"]["complete"] is True
    assert result.manifest["generation"]["tts_synthesis"] == {
        "status": "blocked",
        "allowed": False,
        "blocked_by": [
            "human_review",
            "provider_not_selected",
            "provider_privacy_review_required",
            "rai_security_signoff_required",
        ],
        "dry_run_bypass_allowed": False,
    }
    assert result.manifest["generation"]["audio_validation"]["status"] == "blocked"
    assert result.manifest["generation"]["audio_validation"]["ready"] is False
    assert (
        result.manifest["generation"]["audio_validation"]["metadata"]["content_type"]
        == "audio/mpeg"
    )
    assert result.manifest["artifact_access"]["model"] == "private_operator_path"
    assert result.manifest["artifact_access"]["response_urls"] == {
        "publicly_accessible": False,
        "requires_operator_credentials": True,
        "signed_urls": False,
        "query_strings_allowed": False,
        "credential_material_allowed": False,
    }
    assert (
        result.manifest["artifact_access"]["retention"]["cleanup_after"]
        == result.response["expires_at"]
    )
    assert (
        result.manifest["artifact_access"]["audit"]["correlation_id"] == result.response["job_id"]
    )
    assert result.response["publishing_packet_url"].endswith(".zip")
    assert "human review is required before publishing" not in result.response["warnings"]
    assert (
        "artifact URLs are private operator paths, not public publishing links"
        in result.response["warnings"]
    )

    job_dir = artifact_root / "jobs" / result.response["job_id"]
    packet_file = job_dir / "packets" / f"{result.response['job_id']}.zip"
    assert (job_dir / "script.txt").exists()
    assert (job_dir / "claim-ledger.json").exists()
    assert (job_dir / "cost-ledger.json").exists()
    assert (job_dir / "review-checklist.md").exists()
    assert packet_file.exists()

    # Verify packet manifest uses flat structure per editorial standards section 7.2
    with ZipFile(packet_file) as packet:
        packet_manifest = json.loads(packet.read("MANIFEST.json"))
        assert packet_manifest["review_status"] == "pending"
        assert packet_manifest["review"]["environment"] == "podcast-review"
        assert packet_manifest["review"]["gate"]["status"] == "blocked"
        assert packet_manifest["review"]["gate"]["approval_required_before"] == "tts_synthesis"
        assert packet_manifest["generation"]["audio_validation"]["status"] == "blocked"
        assert packet_manifest["cost_ledger"]["budget"]["status"] == "within_budget"
        assert packet_manifest["publishing"]["packet_ready"] is False
        assert packet_manifest["publishing"]["readiness_checks"]["cost_ledger_complete"] is True
        assert packet_manifest["artifact_access"]["model"] == "private_operator_path"
        assert packet_manifest["artifact_access"]["publication"]["eligible"] is False

    shutil.rmtree(artifact_root, ignore_errors=True)


def test_dry_run_preserves_response_shape_and_review_metadata() -> None:
    artifact_root = Path(".test-artifacts-dry-run")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "dry_run": True,
            "callback": {"url": "https://example.com/cb", "secret_name": "CALLBACK_SECRET"},
        },
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert tuple(result.response.keys()) == RESPONSE_KEYS
    assert result.response["status"] == "dry_run"
    assert result.manifest["request"]["dry_run"] is True
    assert result.manifest["status"] == "dry_run"
    assert result.manifest["request"]["callback"] == {
        "requested": True,
        "url_host": "example.com",
        "secret_name_provided": True,
    }
    assert result.manifest["review"]["required"] is True
    assert result.manifest["review"]["required_for_tts"] is True
    assert result.manifest["review"]["status"] == "pending"
    assert result.manifest["review"]["gate"]["status"] == "blocked"
    assert result.manifest["generation"]["tts_synthesis"]["dry_run_bypass_allowed"] is False
    assert result.manifest["generation"]["tts_synthesis"]["allowed"] is False
    assert result.manifest["generation"]["tts_synthesis"]["blocked_by"] == ["dry_run"]
    assert "callback accepted by contract but not invoked yet" in result.response["warnings"]
    assert "CALLBACK_SECRET" not in json.dumps(result.manifest)
    assert result.response["job_id"].endswith("-dry-run")
    assert not (artifact_root / monthly_ledger_path("2026-06")).exists()
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_replay_identity_covers_content_title_breaking_news_and_config() -> None:
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_title": "Pinned title",
        "article_content": VALID_ARTICLE_CONTENT,
        "breaking_news": "Pinned correction",
        "podcast_config": {"name": "Pinned show"},
        "script_directions": {"episode_style": {"tone": "analytical"}},
        "backchannels": {"enabled": True},
    }

    assert build_job_id(payload) == build_job_id(dict(payload))
    for field, changed in (
        ("article_title", "Changed title"),
        ("article_content", VALID_ARTICLE_CONTENT + " Changed."),
        ("breaking_news", "Changed correction"),
        ("podcast_config", {"name": "Changed show"}),
        ("script_directions", {"episode_style": {"tone": "playful"}}),
        ("backchannels", {"enabled": False}),
    ):
        changed_payload = dict(payload)
        changed_payload[field] = changed
        assert build_job_id(changed_payload) != build_job_id(payload)

    dry_payload = {**payload, "dry_run": True}
    assert replay_identity_sha256(dry_payload) == replay_identity_sha256(payload)
    assert build_job_id(dry_payload).endswith("-dry-run")
    assert build_job_id(dry_payload) != build_job_id(payload)


def test_generation_pins_exact_article_and_summary_hashes(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    summary = "Exact historical summary.\nSecond line."
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_title": "Pinned title",
        "article_content": VALID_ARTICLE_CONTENT,
        "script_directions": {"historical_context": {"summary": summary}},
    }

    result = run_generation_job(
        payload,
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    replay = result.manifest["request"]["replay"]
    article_bytes = VALID_ARTICLE_CONTENT.encode("utf-8")
    summary_bytes = summary.encode("utf-8")
    assert storage.get_bytes(replay["article_path"]) == article_bytes
    assert storage.get_bytes(replay["summary_path"]) == summary_bytes
    assert replay["article_sha256"] == hashlib.sha256(article_bytes).hexdigest()
    assert replay["summary_sha256"] == hashlib.sha256(summary_bytes).hexdigest()
    assert result.manifest["request"]["article_sha256"] == replay["article_sha256"]


def test_replay_collision_refuses_overwrite_before_budget_or_artifacts(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_title": "Pinned title",
        "article_content": VALID_ARTICLE_CONTENT,
    }
    first = run_generation_job(
        payload,
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )
    manifest_path = tmp_path / "jobs" / first.response["job_id"] / "manifest.json"
    manifest_before = manifest_path.read_bytes()
    ledger_before = (tmp_path / monthly_ledger_path("2026-06")).read_bytes()

    with pytest.raises(ReplayCollisionError):
        run_generation_job(
            payload,
            storage=storage,
            now=datetime(2026, 6, 7, 20, 0, 0, tzinfo=timezone.utc),
        )

    assert manifest_path.read_bytes() == manifest_before
    assert (tmp_path / monthly_ledger_path("2026-06")).read_bytes() == ledger_before


def test_concurrent_identical_replays_reserve_namespace_atomically(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_title": "Pinned title",
        "article_content": VALID_ARTICLE_CONTENT,
    }

    def run_once() -> str:
        try:
            return run_generation_job(
                payload,
                storage=storage,
                now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
            ).response["status"]
        except ReplayCollisionError:
            return "collision"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: run_once(), range(2)))

    assert sorted(outcomes) == ["accepted", "collision"]


def test_dry_run_does_not_mutate_accepted_namespace_or_monthly_ledger(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_title": "Pinned title",
        "article_content": VALID_ARTICLE_CONTENT,
    }
    accepted = run_generation_job(
        payload,
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )
    accepted_manifest_path = tmp_path / "jobs" / accepted.response["job_id"] / "manifest.json"
    manifest_before = accepted_manifest_path.read_bytes()
    ledger_path = tmp_path / monthly_ledger_path("2026-06")
    ledger_before = ledger_path.read_bytes()

    dry_run = run_generation_job(
        {**payload, "dry_run": True},
        storage=storage,
        now=datetime(2026, 6, 7, 20, 0, 0, tzinfo=timezone.utc),
    )

    assert dry_run.response["status"] == "dry_run"
    assert dry_run.response["job_id"] != accepted.response["job_id"]
    assert accepted_manifest_path.read_bytes() == manifest_before
    assert ledger_path.read_bytes() == ledger_before


def test_repeated_dry_runs_are_idempotent_and_do_not_collide(tmp_path: Path) -> None:
    """Dry runs are a repeatable preview mode: re-submitting the same request
    must not raise ReplayCollisionError (issue #609 review)."""
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_title": "Pinned title",
        "article_content": VALID_ARTICLE_CONTENT,
        "dry_run": True,
    }
    first = run_generation_job(
        payload,
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )
    second = run_generation_job(
        payload,
        storage=storage,
        now=datetime(2026, 6, 7, 20, 0, 0, tzinfo=timezone.utc),
    )

    assert first.response["status"] == "dry_run"
    assert second.response["status"] == "dry_run"
    assert first.response["job_id"] == second.response["job_id"]
    """Phase B wiring: a top-level ``backchannels`` payload reaches the request manifest."""

    artifact_root = Path(".test-artifacts-backchannels")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")
    backchannels = {"enabled": True, "min_gap_seconds": 30, "max_gap_seconds": 40}

    with_bc = run_generation_job(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "dry_run": True,
            "backchannels": backchannels,
        },
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )
    assert with_bc.manifest["request"]["backchannels"] == backchannels

    without_bc = run_generation_job(
        {"week": "2026-W23", "article_url": "https://example.com/article", "dry_run": True},
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )
    assert "backchannels" not in without_bc.manifest["request"]
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_publishing_packet_extracts_with_required_files_and_checksums() -> None:
    artifact_root = Path(".test-artifacts-packet")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "article_title": "Reviewed weekly signal",
            "article_content": VALID_ARTICLE_CONTENT,
        },
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    packet_file = (
        artifact_root
        / "jobs"
        / result.response["job_id"]
        / "packets"
        / f"{result.response['job_id']}.zip"
    )
    with ZipFile(packet_file) as packet:
        names = set(packet.namelist())
        required = {
            "README.txt",
            "MANIFEST.json",
            "REVIEW-CHECKLIST.md",
            "PUBLISHING-GUIDE.txt",
            "script.txt",
            "claim-ledger.json",
            "COST-LEDGER.json",
            "transcript.txt",
            "show-notes.md",
            "audio/episode-2026-W23.mp3",
            "RIGHTS-AND-ATTRIBUTION.txt",
            "CHECKSUMS.txt",
        }
        assert required <= names
        manifest = json.loads(packet.read("MANIFEST.json"))
        assert manifest["job_id"] == result.response["job_id"]
        # Verify flat structure per editorial standards section 7.2
        assert manifest["review_status"] == "pending"
        assert manifest["publishing"]["eligible"] is False
        assert manifest["publishing"]["packet_ready"] is False
        assert manifest["generation"]["audio_placeholder"] is True
        assert manifest["generation"]["tts_synthesis"]["allowed"] is False
        cost_ledger = json.loads(packet.read("COST-LEDGER.json"))
        assert cost_ledger == manifest["cost_ledger"]
        readme = packet.read("README.txt").decode("utf-8")
        publishing_guide = packet.read("PUBLISHING-GUIDE.txt").decode("utf-8")
        show_notes = packet.read("show-notes.md").decode("utf-8")
        assert "Publication is blocked" in show_notes
        assert "Publication blocker checklist" in readme
        assert "PUBLICATION BLOCKED - PLACEHOLDER PACKET" in publishing_guide
        assert "Do not upload the MP3" in publishing_guide
        assert "PUBLISHING TO SPOTIFY" not in publishing_guide
        assert "Click 'Upload'" not in publishing_guide
        assert "<enclosure" not in publishing_guide
        assert "Submit feed URL" not in publishing_guide
        checksums = _parse_checksums(packet.read("CHECKSUMS.txt").decode("utf-8"))
        assert set(checksums) == names - {"CHECKSUMS.txt"}
        for name, expected in checksums.items():
            assert hashlib.sha256(packet.read(name)).hexdigest() == expected

    shutil.rmtree(artifact_root, ignore_errors=True)


def test_generation_outputs_are_deterministic_and_documented() -> None:
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_sha256": "a" * 64,
    }
    created_at = datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc)
    job_id = build_job_id(payload)

    first = generate_artifacts(job_id, payload, created_at)
    second = generate_artifacts(job_id, payload, created_at)

    assert [(artifact.path, artifact.content, artifact.content_type) for artifact in first] == [
        (artifact.path, artifact.content, artifact.content_type) for artifact in second
    ]
    assert [artifact.path for artifact in first] == [
        f"jobs/{job_id}/script.txt",
        f"jobs/{job_id}/claim-ledger.json",
        f"jobs/{job_id}/cost-ledger.json",
        f"jobs/{job_id}/transcript.txt",
        f"jobs/{job_id}/show-notes.md",
        f"jobs/{job_id}/review-checklist.md",
        f"jobs/{job_id}/audio/{job_id}.mp3",
        f"jobs/{job_id}/packets/{job_id}.zip",
    ]
    script = first[0].content.decode("utf-8")
    cost_ledger = json.loads(first[2].content.decode("utf-8"))
    transcript = first[3].content.decode("utf-8")
    show_notes = first[4].content.decode("utf-8")
    assert "deterministic production-path placeholder" in script
    assert cost_ledger["budget"]["status"] == "within_budget"
    assert cost_ledger["costs"]["staging_storage"]["estimated_usd"] == "0.00"
    assert "Title: Claracle Podcast" in transcript
    assert "Original article](https://example.com/article)" in show_notes
    assert first[6].content.startswith(f"Audio placeholder for {job_id}".encode("utf-8"))
    assert first[7].content_type == "application/zip"


def test_local_storage_backend_rejects_traversal_project_relative_paths(monkeypatch) -> None:
    artifact_root = Path(".test-artifacts-storage")
    shutil.rmtree(artifact_root, ignore_errors=True)
    monkeypatch.delenv("PODCASTER_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(artifact_root))
    monkeypatch.setenv("PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/base/")

    storage = create_storage_backend()

    assert isinstance(storage, LocalStorageBackend)
    with pytest.raises(ValueError, match="traversal"):
        storage.put_bytes("../jobs/./podcast-safe/../manifest.json", b"{}", "application/json")
    assert not (artifact_root / "jobs" / "podcast-safe" / "manifest.json").exists()
    assert not Path("manifest.json").exists()
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_managed_identity_scope_is_converted_to_resource() -> None:
    assert (
        _managed_identity_resource("https://storage.azure.com/.default")
        == "https://storage.azure.com"
    )
    assert _managed_identity_resource("https://storage.azure.com") == "https://storage.azure.com"


def test_managed_identity_expiry_accepts_epoch_or_expires_in(monkeypatch) -> None:
    assert _token_expires_on({"expires_on": "1800000000"}) == 1800000000
    monkeypatch.setattr("podcaster.storage.time.time", lambda: 1000)
    assert _token_expires_on({"expires_in": "60"}) == 1060


def test_artifact_urls_are_private_operator_paths_without_query_credentials() -> None:
    artifact_root = Path(".test-artifacts-private-urls")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(
        artifact_root, "https://storage.example.invalid/private-artifacts"
    )

    result = run_generation_job(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "article_title": "Reviewed weekly signal",
            "article_content": VALID_ARTICLE_CONTENT,
        },
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    response_urls = [
        result.response[key]
        for key in (
            "manifest_url",
            "mp3_url",
            "transcript_url",
            "show_notes_url",
            "publishing_packet_url",
        )
    ]
    manifest_urls = [details["url"] for details in result.manifest["artifacts"].values()]
    for url in response_urls + manifest_urls:
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.query == ""
        assert parsed.fragment == ""
        assert parsed.username is None
        assert parsed.password is None

    assert result.manifest["artifact_access"]["response_urls"]["signed_urls"] is False
    assert (
        result.manifest["artifact_access"]["operator_access"]["method"]
        == "Azure RBAC or local filesystem access"
    )
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_artifact_base_urls_reject_embedded_credentials_and_signed_queries() -> None:
    assert (
        normalize_artifact_base_url("https://storage.example.invalid/base/")
        == "https://storage.example.invalid/base"
    )
    for unsafe_url in (
        "https://storage.example.invalid/base?sig=secret",
        "https://user:pass@storage.example.invalid/base",
        "https://storage.example.invalid/base#token",
    ):
        try:
            normalize_artifact_base_url(unsafe_url)
        except ValueError as exc:
            assert "must not contain credentials" in str(exc)
        else:
            raise AssertionError(f"unsafe artifact base URL was accepted: {unsafe_url}")


def test_job_lifecycle_metadata_observability_and_manifest_serialization(caplog) -> None:
    artifact_root = Path(".test-artifacts-lifecycle")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_sha256": "b" * 64,
        "article_title": "Reviewed weekly signal",
        "article_content": VALID_ARTICLE_CONTENT,
        "source_artifacts": ["https://example.com/source.json"],
        "force": True,
    }

    with caplog.at_level(logging.INFO):
        result = run_generation_job(
            payload,
            storage=storage,
            now=datetime(2026, 6, 7, 19, 7, 49, 816000, tzinfo=timezone.utc),
        )

    manifest = result.manifest
    job_id = build_job_id(payload)
    assert result.response["job_id"] == job_id
    assert manifest["job_id"] == job_id
    assert manifest["status"] == "review_pending"
    assert manifest["created_at"] == "2026-06-07T19:07:49Z"
    assert manifest["expires_at"] == result.response["expires_at"] == "2026-06-14T19:07:49Z"
    assert manifest["request"] == {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_sha256": hashlib.sha256(VALID_ARTICLE_CONTENT.encode()).hexdigest(),
        "article_title": "Reviewed weekly signal",
        "article_content_provided": True,
        "source_artifacts": ["https://example.com/source.json"],
        "dry_run": False,
        "force": True,
        "cost_override": {"recorded": False, "actor": None, "recorded_at": None},
        "callback": {"requested": False, "url_host": None, "secret_name_provided": False},
        "replay": {
            "schema_version": "squadscope-podcaster-replay-input-v1",
            "identity_sha256": replay_identity_sha256(payload),
            "input_manifest_path": f"jobs/{job_id}/inputs/replay.json",
            "article_path": f"jobs/{job_id}/inputs/article.txt",
            "article_sha256": hashlib.sha256(VALID_ARTICLE_CONTENT.encode()).hexdigest(),
            "summary_path": None,
            "summary_sha256": None,
        },
        "publication_identity_mode": "legacy",
    }
    assert manifest["lifecycle"]["force"] is True
    assert manifest["lifecycle"]["transitions"][-1]["to"] == "review_pending"
    assert manifest["publishing"]["blocked_by"] == [
        "human_review",
        "synthesis_not_completed",
        "audio_validation_not_passed",
    ]
    assert manifest["review"]["artifacts_for_review"] == [
        "script.txt",
        "claim-ledger.json",
        "cost-ledger.json",
        "transcript.txt",
        "show-notes.md",
        "review-checklist.md",
        "manifest.json",
        "publishing-packet.zip",
    ]
    assert manifest["generation"]["tts_synthesis"]["allowed"] is False
    assert manifest["artifact_access"]["publication"]["blocked_by"] == [
        "human_review",
        "synthesis_not_completed",
    ]
    assert manifest["publishing"]["packet_ready"] is False
    assert manifest["publishing"]["readiness_checks"] == {
        "cost_ledger_complete": True,
        "budget_status": "within_budget",
        "editorial_review_complete": False,
        "real_audio_available": False,
        "audio_validation_passed": False,
    }
    assert manifest["cost_ledger"]["week"] == "2026-W23"
    assert manifest["cost_ledger"]["provider"] == "not_selected"
    assert manifest["cost_ledger"]["duration_seconds"] == 0
    assert manifest["cost_ledger"]["privacy"]["secrets_recorded"] is False
    assert manifest["observability"]["correlation_id"] == job_id
    assert all(
        details["url"].startswith("https://example.invalid/artifacts/jobs/")
        for details in manifest["artifacts"].values()
    )
    assert all(
        details["access_model"] == "private_operator_path"
        and details["publicly_accessible"] is False
        and details["size_bytes"] > 0
        and details["content_type"]
        and len(details["sha256"]) == 64
        for details in manifest["artifacts"].values()
    )
    serialized = json.loads(manifest_bytes(manifest).decode("utf-8"))
    assert serialized == manifest
    assert (
        f"podcaster job staged job_id={job_id} status=review_pending "
        "dry_run=False artifact_count=12" in caplog.text
    )
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_non_dry_run_fails_closed_when_monthly_episode_limit_exceeded() -> None:
    artifact_root = Path(".test-artifacts-budget")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")
    storage.put_bytes(
        monthly_ledger_path("2026-06"),
        manifest_bytes(
            {
                "schema_version": "squadscope-podcaster-monthly-cost-ledger-v1",
                "month": "2026-06",
                "episodes": [
                    {
                        "job_id": f"existing-{index}",
                        "week": f"2026-W2{index}",
                        "estimated_total_usd": "0.00",
                    }
                    for index in range(10)
                ],
            }
        ),
        "application/json; charset=utf-8",
    )

    result = run_generation_job(
        {"week": "2026-W29", "article_url": "https://example.com/new-article"},
        storage=storage,
        now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "failed"
    assert result.response["errors"] == [
        "monthly podcast budget exceeded; explicit operator override required"
    ]
    assert result.manifest["budget"]["status"] == "over_budget"
    assert not any(
        path.is_file()
        for path in (artifact_root / "jobs" / str(result.manifest["job_id"])).rglob("*")
    )
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_retry_of_existing_job_bypasses_monthly_budget_limit() -> None:
    """A retry of an existing job_id should not be blocked by other episodes
    that were added after the original run."""
    artifact_root = Path(".test-artifacts-retry-budget")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    # Pre-populate ledger with 10 other episodes (at limit) PLUS this job's entry.
    job_payload = {
        "week": "2026-W29",
        "article_url": "https://example.com/retry-article",
        "article_title": "Reviewed weekly signal",
        "article_content": VALID_ARTICLE_CONTENT,
    }
    from podcaster.jobs import build_job_id

    target_job_id = build_job_id(job_payload)

    storage.put_bytes(
        monthly_ledger_path("2026-06"),
        manifest_bytes(
            {
                "schema_version": "squadscope-podcaster-monthly-cost-ledger-v1",
                "month": "2026-06",
                "episodes": [
                    {
                        "job_id": f"other-{index}",
                        "week": f"2026-W2{index}",
                        "estimated_total_usd": "0.00",
                    }
                    for index in range(10)
                ]
                + [
                    {"job_id": target_job_id, "week": "2026-W29", "estimated_total_usd": "0.00"},
                ],
            }
        ),
        "application/json; charset=utf-8",
    )

    # Without the retry bypass, this would return "failed" (11 total episodes > 10 max).
    # With the fix, it should succeed because the job already has a ledger slot.
    result = run_generation_job(
        job_payload,
        storage=storage,
        now=datetime(2026, 6, 30, 20, 53, 0, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "accepted"
    shutil.rmtree(artifact_root, ignore_errors=True)
    artifact_root = Path(".test-artifacts-budget-reservation")
    shutil.rmtree(artifact_root, ignore_errors=True)

    class TrackingStorage(LocalStorageBackend):
        def __init__(self, root: Path, base_url: str) -> None:
            super().__init__(root, base_url)
            self.monthly_updates: list[str] = []

        def update_bytes(self, path, content_type, update):
            self.monthly_updates.append(path)
            return super().update_bytes(path, content_type, update)

        def put_bytes(self, path, content, content_type):
            if path.startswith("jobs/") and not self.monthly_updates:
                raise AssertionError("job artifacts were staged before monthly budget reservation")
            return super().put_bytes(path, content, content_type)

    storage = TrackingStorage(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {
            "week": "2026-W29",
            "article_url": "https://example.com/new-article",
            "article_title": "Reviewed weekly signal",
            "article_content": VALID_ARTICLE_CONTENT,
        },
        storage=storage,
        now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "accepted"
    assert storage.monthly_updates == [
        f"jobs/{result.response['job_id']}/inputs/replay.json",
        monthly_ledger_path("2026-06"),
        monthly_ledger_path("2026-06"),
        f"jobs/{result.response['job_id']}/manifest.json",
    ]
    monthly = json.loads(
        (artifact_root / monthly_ledger_path("2026-06")).read_text(encoding="utf-8")
    )
    assert monthly["episodes"][-1].get("state") != "reserved"
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_concurrent_jobs_share_atomic_monthly_budget_reservation() -> None:
    artifact_root = Path(".test-artifacts-concurrent-budget")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    payloads = [
        {
            "week": f"2026-W{10 + index:02d}",
            "article_url": f"https://example.com/article-{index}",
            "article_title": "Reviewed weekly signal",
            "article_content": VALID_ARTICLE_CONTENT,
        }
        for index in range(13)
    ]

    try:
        with ThreadPoolExecutor(max_workers=13) as executor:
            results = list(
                executor.map(
                    lambda payload: run_generation_job(
                        payload,
                        storage=storage,
                        now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
                    ),
                    payloads,
                )
            )

        statuses = [result.response["status"] for result in results]
        assert statuses.count("accepted") == 10
        assert statuses.count("failed") == 3

        monthly = json.loads(
            (artifact_root / monthly_ledger_path("2026-06")).read_text(encoding="utf-8")
        )
        assert len(monthly["episodes"]) == 10
        assert len({episode["job_id"] for episode in monthly["episodes"]}) == 10
        assert all(episode.get("state") != "reserved" for episode in monthly["episodes"])

        staged_job_ids = {
            path.name
            for path in (artifact_root / "jobs").iterdir()
            if path.is_dir() and (path / "manifest.json").exists()
        }
        accepted_job_ids = {
            build_job_id(payload)
            for payload, result in zip(payloads, results, strict=True)
            if result.response["status"] == "accepted"
        }
        failed_job_ids = {build_job_id(payload) for payload in payloads} - accepted_job_ids
        assert staged_job_ids == accepted_job_ids
        assert not any(
            path.is_file()
            for job_id in failed_job_ids
            for path in (artifact_root / "jobs" / job_id).rglob("*")
        )
    finally:
        shutil.rmtree(artifact_root, ignore_errors=True)


def test_non_dry_run_allows_explicit_operator_cost_override() -> None:
    artifact_root = Path(".test-artifacts-budget-override")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")
    storage.put_bytes(
        monthly_ledger_path("2026-06"),
        manifest_bytes(
            {
                "schema_version": "squadscope-podcaster-monthly-cost-ledger-v1",
                "month": "2026-06",
                "episodes": [
                    {
                        "job_id": f"existing-{index}",
                        "week": f"2026-W2{index}",
                        "estimated_total_usd": "0.00",
                    }
                    for index in range(10)
                ],
            }
        ),
        "application/json; charset=utf-8",
    )

    result = run_generation_job(
        {
            "week": "2026-W29",
            "article_url": "https://example.com/new-article",
            "force": True,
            "article_title": "Reviewed weekly signal",
            "article_content": VALID_ARTICLE_CONTENT,
            "cost_override": {
                "actor": "hermes",
                "reason": "approved launch exception",
                "recorded_at": "2026-06-09T11:00:00Z",
            },
        },
        storage=storage,
        now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "accepted"
    assert result.manifest["cost_ledger"]["budget"]["status"] == "override_recorded"
    assert result.manifest["cost_ledger"]["budget"]["override"] == {
        "actor": "hermes",
        "reason": "approved launch exception",
        "recorded_at": "2026-06-09T11:00:00Z",
    }
    monthly = json.loads(
        (artifact_root / monthly_ledger_path("2026-06")).read_text(encoding="utf-8")
    )
    assert len(monthly["episodes"]) == 11
    assert monthly["episodes"][-1]["budget_status"] == "override_recorded"
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_azure_conditional_update_retries_412_conflicts_then_succeeds(monkeypatch) -> None:
    backend = AzureBlobStorageBackend("https://storage.example.invalid", "podcaster-artifacts")
    states = [(b'{"attempt": 1}', '"etag-1"'), (b'{"attempt": 2}', '"etag-2"')]
    put_attempts: list[dict[str, str | None]] = []

    def get_blob_state(safe_path: str) -> tuple[bytes | None, str | None]:
        assert safe_path == monthly_ledger_path("2026-06")
        return states.pop(0)

    def put_blob(
        path: str,
        content: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        put_attempts.append(
            {
                "path": path,
                "content": content.decode("utf-8"),
                "if_match": if_match,
                "if_none_match": if_none_match,
            }
        )
        if len(put_attempts) == 1:
            raise HTTPError(
                "https://storage.example.invalid/blob",
                412,
                "Precondition Failed",
                hdrs=None,
                fp=None,
            )

    monkeypatch.setattr(backend, "_get_blob_state", get_blob_state)
    monkeypatch.setattr(backend, "_put_blob", put_blob)

    stored = backend.update_bytes(
        monthly_ledger_path("2026-06"),
        "application/json; charset=utf-8",
        lambda content: content.replace(b"attempt", b"updated") if content else b"{}",
    )

    assert stored.path == monthly_ledger_path("2026-06")
    assert [attempt["if_match"] for attempt in put_attempts] == ['"etag-1"', '"etag-2"']
    assert [attempt["if_none_match"] for attempt in put_attempts] == [None, None]
    assert put_attempts[-1]["content"] == '{"updated": 2}'


def test_azure_conditional_update_fails_after_412_retry_exhaustion(monkeypatch) -> None:
    backend = AzureBlobStorageBackend("https://storage.example.invalid", "podcaster-artifacts")
    get_attempts: list[str] = []
    put_attempts: list[str] = []

    def get_blob_state(safe_path: str) -> tuple[bytes | None, str | None]:
        get_attempts.append(safe_path)
        return b'{"episodes": []}', '"stale-etag"'

    def put_blob(
        path: str,
        content: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        put_attempts.append(f"{path}:{if_match}:{if_none_match}")
        raise HTTPError(
            "https://storage.example.invalid/blob", 412, "Precondition Failed", hdrs=None, fp=None
        )

    monkeypatch.setattr(backend, "_get_blob_state", get_blob_state)
    monkeypatch.setattr(backend, "_put_blob", put_blob)

    with pytest.raises(RuntimeError, match="concurrent updates did not settle"):
        backend.update_bytes(
            monthly_ledger_path("2026-06"),
            "application/json; charset=utf-8",
            lambda content: content or b"{}",
        )

    assert get_attempts == [monthly_ledger_path("2026-06")] * 5
    assert put_attempts == [f'{monthly_ledger_path("2026-06")}:"stale-etag":None'] * 5


def test_azure_conditional_update_retry_exhaustion_stops_before_artifacts() -> None:
    class AlwaysConflictingAzureStorage(AzureBlobStorageBackend):
        def __init__(self) -> None:
            self._account_url = "https://storage.example.invalid"
            self._container_name = "podcaster-artifacts"
            self.get_attempts = 0
            self.put_attempts = 0
            self.artifact_puts: list[str] = []

        def put_bytes(self, path, content, content_type):
            self.artifact_puts.append(path)
            return super().put_bytes(path, content, content_type)

        def _get_blob_state(self, safe_path: str):
            self.get_attempts += 1
            return None, None

        def _put_blob(self, path, content, content_type, *, if_match=None, if_none_match=None):
            self.put_attempts += 1
            raise HTTPError(
                "https://storage.example.invalid/blob",
                412,
                "Precondition Failed",
                hdrs=None,
                fp=None,
            )

    storage = AlwaysConflictingAzureStorage()

    with pytest.raises(RuntimeError, match="concurrent updates did not settle"):
        run_generation_job(
            {"week": "2026-W29", "article_url": "https://example.com/new-article"},
            storage=storage,
            now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
        )

    # Legacy manifest and terminal filter marker reads precede five reservation attempts.
    assert storage.get_attempts == 7
    assert storage.put_attempts == 5
    assert storage.artifact_puts == []


def test_artifacts_do_not_include_api_secret_marker() -> None:
    artifact_root = Path(".test-artifacts-secret-scan")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {
            "week": "2026-W23",
            "article_url": "https://example.com/article",
            "article_title": "Reviewed weekly signal",
            "article_content": VALID_ARTICLE_CONTENT,
        },
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    serialized_response = json.dumps(result.response)
    serialized_manifest = json.dumps(result.manifest)
    packet_bytes = (
        artifact_root
        / "jobs"
        / result.response["job_id"]
        / "packets"
        / f"{result.response['job_id']}.zip"
    ).read_bytes()
    assert "dont-leak-me" not in serialized_response
    assert "dont-leak-me" not in serialized_manifest
    assert b"dont-leak-me" not in packet_bytes
    shutil.rmtree(artifact_root, ignore_errors=True)


def _parse_checksums(content: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in content.splitlines():
        digest, name = line.split("  ", 1)
        parsed[name] = digest
    return parsed


def test_llm_script_generation_replaces_placeholder_when_article_content_provided(
    monkeypatch,
) -> None:
    """When article_content is present and chat endpoint is configured, the LLM script is used."""
    import json as _json
    from urllib.request import Request

    artifact_root = Path(".test-artifacts-llm-gen")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    # Configure the chat endpoint via environment
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")

    # Mock the managed identity token and HTTP transport
    fake_dialogue = (
        "Theo: Welcome to Claracle! Let's dive into AI.\n"
        "Vera: Both hosts on this show are AI-generated synthetic voices, "
        "not human presenters. Let's go.\n"
        "## Section: AI Frameworks Showdown\n"
        "Theo: First up, frameworks fought hard for the spotlight this week here.\n"
        "Vera: The contrast in developer experience was genuinely striking to me.\n"
        "Theo: Their different philosophies made the article especially interesting today.\n"
        "Vera: Exactly, and community response tells a useful story this week.\n"
        "## Section: Agents Move Into Production\n"
        "Theo: Agents are finally shipping into real production workflows now.\n"
        "Vera: Teams have moved well past demos into measurable developer impact.\n"
        "Theo: The reliability improvements over the quarter are remarkable to watch.\n"
        "Vera: It is a genuine shift in how software teams build together.\n"
    )

    def fake_transport(request: Request) -> bytes:
        return _json.dumps({"choices": [{"message": {"content": fake_dialogue}}]}).encode()

    def fake_token_provider(scope: str) -> str:
        return "fake-token"

    # Patch the script_gen module to use our fakes
    from podcaster import script_gen

    monkeypatch.setattr(script_gen, "_default_transport", fake_transport)
    monkeypatch.setattr(
        script_gen.ManagedIdentityTokenCredential, "get_token", staticmethod(fake_token_provider)
    )

    result = run_generation_job(
        {
            "week": "2026-W24",
            "article_url": "https://example.com/ai-article",
            "article_title": "AI Revolution",
            "article_content": VALID_ARTICLE_CONTENT,
        },
        storage=storage,
        now=datetime(2026, 6, 12, 10, 0, 0, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "accepted"
    assert result.manifest["generation"]["engine"] == "llm-script-gen"
    assert result.manifest["generation"]["deterministic"] is False

    # The script artifact should contain the LLM-generated dialogue
    job_id = result.response["job_id"]
    script_file = artifact_root / "jobs" / job_id / "script.txt"
    script_content = script_file.read_text()
    assert "Theo: Welcome to Claracle!" in script_content
    assert "AI-generated synthetic voices" in script_content
    assert "## Section: AI Frameworks Showdown" in script_content
    assert "---" in script_content  # Header separator present
    transcript = (artifact_root / "jobs" / job_id / "transcript.txt").read_text(encoding="utf-8")
    assert "Theo: Welcome to Claracle!" in transcript
    assert "Vera: The contrast in developer experience was genuinely striking to me." in transcript
    packet_path = artifact_root / "jobs" / job_id / "packets" / f"{job_id}.zip"
    with ZipFile(packet_path) as packet:
        assert packet.read("script.txt") == script_file.read_bytes()
        assert packet.read("transcript.txt").decode("utf-8") == transcript
        checksums = _parse_checksums(packet.read("CHECKSUMS.txt").decode("utf-8"))
        for name in ("script.txt", "transcript.txt"):
            assert checksums[name] == hashlib.sha256(packet.read(name)).hexdigest()

    sections_file = artifact_root / "jobs" / job_id / "sections.json"
    sections_doc = json.loads(sections_file.read_text(encoding="utf-8"))
    assert [s["title"] for s in sections_doc["sections"]] == [
        "AI Frameworks Showdown",
        "Agents Move Into Production",
    ]
    assert sections_doc["sections"][0]["title_card"]["duration_seconds"] == 0.75
    assert f"jobs/{job_id}/sections.json" in result.manifest["artifacts"]

    shutil.rmtree(artifact_root, ignore_errors=True)


def test_llm_script_generation_falls_back_on_failure(monkeypatch) -> None:
    """Dry runs keep the deterministic placeholder when the LLM call fails."""
    from urllib.request import Request

    artifact_root = Path(".test-artifacts-llm-fallback")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")

    def failing_transport(request: Request) -> bytes:
        raise RuntimeError("LLM endpoint unavailable")

    def fake_token_provider(scope: str) -> str:
        return "fake-token"

    from podcaster import script_gen

    monkeypatch.setattr(script_gen, "_default_transport", failing_transport)
    monkeypatch.setattr(
        script_gen.ManagedIdentityTokenCredential, "get_token", staticmethod(fake_token_provider)
    )

    result = run_generation_job(
        {
            "week": "2026-W24",
            "article_url": "https://example.com/ai-article",
            "article_title": "AI Revolution",
            "article_content": VALID_ARTICLE_CONTENT,
            "dry_run": True,
        },
        storage=storage,
        now=datetime(2026, 6, 12, 10, 0, 0, tzinfo=timezone.utc),
    )

    # Falls back to placeholder
    assert result.response["status"] == "dry_run"
    assert result.manifest["generation"]["engine"] == "local-deterministic-placeholder"
    assert result.manifest["generation"]["deterministic"] is True
    assert any("LLM script generation failed" in w for w in result.response["warnings"])

    shutil.rmtree(artifact_root, ignore_errors=True)


def _configure_failing_llm(monkeypatch) -> None:
    from urllib.request import Request

    from podcaster import script_gen

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")

    def failing_transport(request: Request) -> bytes:
        raise RuntimeError("LLM endpoint unavailable")

    monkeypatch.setattr(script_gen, "_default_transport", failing_transport)
    monkeypatch.setattr(
        script_gen.ManagedIdentityTokenCredential, "get_token", staticmethod(lambda scope: "t")
    )


def test_configured_llm_failure_fails_closed_and_releases_reservation(
    monkeypatch, tmp_path: Path
) -> None:
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    _configure_failing_llm(monkeypatch)
    payload = {
        "week": "2026-W24",
        "article_url": "https://example.com/ai-article",
        "article_title": "AI Revolution",
        "article_content": VALID_ARTICLE_CONTENT,
    }
    now = datetime(2026, 6, 12, 10, 0, 0, tzinfo=timezone.utc)

    job_id = build_job_id(payload)
    storage.put_bytes(f"jobs/{job_id}-dry-run/inputs/replay.json", b"preview", "text/plain")
    with pytest.raises(ScriptGenerationFailed):
        run_generation_job(payload, storage=storage, now=now)

    assert storage.get_bytes(f"jobs/{job_id}/manifest.json") is None
    assert storage.get_bytes(f"jobs/{job_id}/inputs/replay.json") is None
    ledger = json.loads(storage.get_bytes(monthly_ledger_path("2026-06")) or b"{}")
    assert all(e.get("job_id") != job_id for e in ledger.get("episodes", []))

    assert storage.get_bytes(f"jobs/{job_id}-dry-run/inputs/replay.json") == b"preview"

    # An identical re-run is not blocked by a replay collision.
    with pytest.raises(ScriptGenerationFailed):
        run_generation_job(payload, storage=storage, now=now)


def test_unconfigured_endpoint_fails_closed_for_non_dry_run(monkeypatch, tmp_path) -> None:
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_CHAT_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    payload = {
        "week": "2026-W24",
        "article_url": "https://example.com/ai-article",
        "article_title": "AI Revolution",
        "article_content": VALID_ARTICLE_CONTENT,
    }
    with pytest.raises(ScriptGenerationFailed, match="no placeholder allowed"):
        run_generation_job(
            payload,
            storage=storage,
            now=datetime(2026, 6, 12, 10, 0, 0, tzinfo=timezone.utc),
        )
    assert storage.get_bytes(f"jobs/{build_job_id(payload)}/manifest.json") is None


def test_regeneration_gets_distinct_job_id_and_is_recorded(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    base = {
        "week": "2026-W43",
        "article_url": "https://example.com/a",
        "article_title": "Reviewed weekly signal",
        "article_content": VALID_ARTICLE_CONTENT,
    }
    regen = {
        **base,
        "regeneration": {
            "supersedes_job_id": "podcast-2026-W43-9d737c6d013c",
            "reason": "placeholder script was published",
        },
    }
    assert build_job_id(base) != build_job_id(regen)

    now = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
    first = run_generation_job(base, storage=storage, now=now)
    with pytest.raises(ReplayCollisionError):
        run_generation_job(base, storage=storage, now=now)
    second = run_generation_job(regen, storage=storage, now=now)

    assert second.response["job_id"] != first.response["job_id"]
    assert second.manifest["request"]["regeneration"] == regen["regeneration"]
