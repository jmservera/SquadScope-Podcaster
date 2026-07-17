from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from podcaster.article_validation import validate_article_inputs
from podcaster.artifact_access import ACCESS_MODEL, artifact_access_metadata
from podcaster.audio import placeholder_audio_validation
from podcaster.claim_extraction import claims_to_ledger_json, extract_claims
from podcaster.config import HistoricalContext, PodcastConfig, ScriptDirections
from podcaster.costs import (
    USD_ZERO,
    evaluate_monthly_guardrail,
    load_monthly_ledger,
    monthly_budget_inputs,
    monthly_ledger_path,
    reserve_monthly_ledger_entry,
    update_monthly_ledger,
)
from podcaster.generation import checksum, generate_artifacts, manifest_bytes
from podcaster.prior_episodes import fetch_prior_episode_themes
from podcaster.queue import enqueue_synthesis_job
from podcaster.script_gen import ScriptGenConfig, generate_script
from podcaster.sections import parse_script_sections, sections_to_metadata
from podcaster.storage import StorageBackend, StoredArtifact, create_storage_backend
from podcaster.validation import RESPONSE_KEYS


@dataclass(frozen=True)
class JobResult:
    response: dict[str, Any]
    manifest: dict[str, Any]


class MonthlyBudgetExceeded(RuntimeError):
    def __init__(self, budget: dict[str, Any]) -> None:
        super().__init__("monthly podcast budget exceeded; explicit operator override required")
        self.budget = budget


class ReplayCollisionError(RuntimeError):
    """Raised when a replay job would overwrite existing output for the same job_id.

    Existing artifacts are never silently overwritten. The caller must acknowledge
    the collision (inspect the existing manifest) or submit a new job with different
    inputs (which will produce a distinct job_id due to the content/config hash).
    """

    def __init__(self, job_id: str) -> None:
        super().__init__(
            f"replay collision: job_id={job_id} already has a manifest; "
            "existing outputs are not overwritten"
        )
        self.job_id = job_id


def _replay_config_hash(payload: dict[str, Any]) -> str:
    """Hash replay-relevant configuration for stable job identity.

    Only includes keys that materially affect the generated output:

    * ``podcast_config`` — show name, hosts, voices; changes the script and audio.
    * ``script_directions`` — LLM prompt shaping; changes the generated script.
    * ``backchannels`` — per-section audio threading overrides; changes synthesis audio.

    Other synthesis parameters (e.g. ``tts_provider``) are intentionally excluded
    because they are resolved from ``podcast_config`` defaults and therefore already
    covered by the ``podcast_config`` hash.  Keys absent from the payload are omitted
    so callers who rely on defaults always hash identically to one another.
    """
    relevant: dict[str, Any] = {}
    for key in ("podcast_config", "script_directions", "backchannels"):
        value = payload.get(key)
        if value is not None:
            relevant[key] = value
    data = json.dumps(relevant, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:12]


def build_job_id(payload: dict[str, Any]) -> str:
    """Derive a stable, content-addressed job identifier from immutable inputs.

    Identity covers:
    * ``week`` and ``article_url`` — the external primary key supplied by the caller.
    * ``article_sha256`` — content identity: different article bytes → different job.
    * ``article_title`` — displayed in the script header; changes the generated output.
    * ``breaking_news`` — changes the script tone and urgency framing.  The value is
      serialised with ``json.dumps(sort_keys=True)`` so the hash is stable for any
      JSON-compatible payload (str, bool, dict, list, int, float, None).  Custom
      objects are deliberately not supported; ``default=str`` is omitted here to
      raise loudly if a non-serialisable type is ever passed, rather than silently
      producing an unstable or non-reproducible hash.
    * replay-relevant config (``podcast_config``, ``script_directions``,
      ``backchannels``) — config changes that materially affect the script or audio
      must produce a distinct job.

    Identical pinned inputs always produce the same job_id, so the generation pipeline
    is safely idempotent; any input change (content, title, news flag, or config)
    produces a new job_id, preventing silent collision between historically distinct
    runs.
    """
    week = str(payload["week"]).strip()
    article_url = str(payload["article_url"]).strip()
    content_sha = str(payload.get("article_sha256") or "")
    config_sha = _replay_config_hash(payload)
    article_title = str(payload.get("article_title") or "")
    breaking_news_raw = payload.get("breaking_news")
    breaking_news = (
        json.dumps(breaking_news_raw, sort_keys=True, ensure_ascii=False)
        if breaking_news_raw is not None
        else ""
    )
    identity = f"{week}|{article_url}|{content_sha}|{config_sha}|{article_title}|{breaking_news}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    safe_week = re.sub(r"[^A-Za-z0-9_.-]", "-", week)
    return f"podcast-{safe_week}-{digest}"


def _reserve_namespace_or_raise(
    existing: bytes | None,
    *,
    job_id: str,
    is_dry_run: bool,
) -> bytes:
    """Atomically claim a job namespace or raise ReplayCollisionError.

    Must be invoked as the callback of ``storage.update_bytes`` so the
    read-check-write is executed under the backend's concurrency guard
    (file lock for local storage; ETag CAS for Azure Blob).

    Rules:
    * No existing manifest → reserve (write placeholder, no collision).
    * Existing ``dry_run`` manifest + dry-run request → allow repeat.
    * Existing ``dry_run`` manifest + non-dry-run request → collision.
    * Existing non-``dry_run`` manifest (accepted, reserving, …) + any request
      → collision; dry runs must never mutate an accepted namespace.
    """
    if existing is not None:
        existing_status: str | None = None
        try:
            existing_status = json.loads(existing).get("status")
        except Exception:
            pass
        # Only a repeated dry run on an existing dry-run namespace is safe to
        # allow.  Every other combination risks overwriting real output.
        if not (is_dry_run and existing_status == "dry_run"):
            raise ReplayCollisionError(job_id)
    return json.dumps({"status": "reserving", "job_id": job_id}).encode("utf-8")


def run_generation_job(
    payload: dict[str, Any],
    storage: StorageBackend | None = None,
    now: datetime | None = None,
    enqueue: Callable[[str], bool] | None = None,
    validation_warnings: list[str] | None = None,
) -> JobResult:
    current = now or datetime.now(timezone.utc)
    expires_at = (
        (current + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )

    # Normalise: when article_content is provided, compute the authoritative SHA-256
    # from the *actual* content bytes so the manifest's article_sha256 always covers
    # what its name implies — never a summary or a caller-supplied hash of different
    # bytes. Do this before build_job_id so the content hash is part of identity.
    article_content_raw = payload.get("article_content")
    if isinstance(article_content_raw, str) and article_content_raw:
        computed_article_sha = hashlib.sha256(article_content_raw.encode("utf-8")).hexdigest()
        if payload.get("article_sha256") != computed_article_sha:
            payload = dict(payload)
            payload["article_sha256"] = computed_article_sha

    job_id = build_job_id(payload)
    podcast_config = PodcastConfig.from_payload(payload)
    if not PodcastConfig.payload_provides_identity(payload):
        logging.warning(
            "podcast_config identity absent in payload job_id=%s; using default "
            "hosts %r/%r and show name %r — supply payload.podcast_config "
            "(name/host_a/host_b) to override (issue #545)",
            job_id,
            podcast_config.host_a.name,
            podcast_config.host_b.name,
            podcast_config.name,
        )
    if "article_title" in payload or "article_content" in payload:
        validate_article_inputs(payload.get("article_title"), payload.get("article_content"))
    storage = storage or create_storage_backend()

    # Atomically reserve the job namespace before consuming any budget or
    # writing artifacts.  _reserve_namespace_or_raise raises ReplayCollisionError
    # under the backend's concurrency guard (file lock / ETag CAS), so two
    # concurrent identical submissions cannot both proceed past this point.
    manifest_path = f"jobs/{job_id}/manifest.json"
    is_dry_run = bool(payload.get("dry_run"))
    try:
        storage.update_bytes(
            manifest_path,
            "application/json; charset=utf-8",
            lambda existing: _reserve_namespace_or_raise(
                existing,
                job_id=job_id,
                is_dry_run=is_dry_run,
            ),
        )
    except ReplayCollisionError:
        logging.warning(
            "replay collision detected job_id=%s; existing manifest not overwritten",
            job_id,
        )
        raise

    month = current.strftime("%Y-%m")
    monthly_path = monthly_ledger_path(month)
    cost_override = _cost_override(payload)
    budget_context: dict[str, Any] = {}

    def reserve_monthly_budget(content: bytes | None) -> bytes:
        monthly_ledger = load_monthly_ledger(content, month=month)
        prior_episode_count, prior_monthly_spend = monthly_budget_inputs(
            monthly_ledger, job_id=job_id
        )
        # Detect retry: if the job_id already has a ledger entry, this is a
        # re-submission and the budget slot is already allocated. Skip the
        # budget guard so retries of the same job are not blocked by other
        # episodes that appeared after the initial run.
        is_retry = _job_already_in_ledger(monthly_ledger, job_id)
        budget = evaluate_monthly_guardrail(
            prior_episode_count=prior_episode_count,
            prior_monthly_spend_usd=prior_monthly_spend,
            projected_episode_cost_usd=USD_ZERO,
            override=cost_override,
        )
        if not payload.get("dry_run") and budget["status"] == "over_budget" and not is_retry:
            raise MonthlyBudgetExceeded(budget)
        budget_context["prior_episode_count"] = prior_episode_count
        budget_context["prior_monthly_spend"] = prior_monthly_spend
        return manifest_bytes(
            reserve_monthly_ledger_entry(
                monthly_ledger,
                job_id=job_id,
                week=str(payload["week"]),
                budget=budget,
            )
        )

    try:
        storage.update_bytes(
            monthly_path, "application/json; charset=utf-8", reserve_monthly_budget
        )
    except MonthlyBudgetExceeded as exc:
        logging.warning(
            "podcaster job blocked by monthly budget job_id=%s week=%s "
            "projected_episode_count=%s projected_monthly_spend_usd=%s",
            job_id,
            payload.get("week"),
            exc.budget["projected_episode_count"],
            exc.budget["projected_monthly_spend_usd"],
        )
        # Remove the reserving placeholder so the namespace is not permanently
        # blocked and a later retry (after a budget override) can proceed.
        try:
            storage.delete_blob(manifest_path)
        except Exception:
            logging.warning(
                "failed to clean up reserving manifest after budget failure job_id=%s", job_id
            )
        return JobResult(
            response=failed_response(
                ["monthly podcast budget exceeded; explicit operator override required"]
            ),
            manifest={"job_id": job_id, "status": "failed", "budget": exc.budget},
        )
    prior_episode_count = int(budget_context["prior_episode_count"])
    prior_monthly_spend = budget_context["prior_monthly_spend"]

    warnings = [
        *(validation_warnings or []),
        "artifact URLs are private operator paths, not public publishing links",
    ]
    if payload.get("callback"):
        warnings.append("callback accepted by contract but not invoked yet")

    # When article_content is provided, attempt LLM script generation (#140)
    # and claim extraction (#141).
    llm_script: str | None = None
    llm_sections_json: str | None = None
    llm_claims_json: str | None = None
    llm_generation_engine = "local-deterministic-placeholder"
    script_directions = ScriptDirections.from_payload(payload)
    historical_context: HistoricalContext | None = (
        script_directions.historical_context
        if script_directions.historical_context.has_content
        else None
    )
    if not script_directions.historical_context.prior_episode_themes:
        try:
            prior_episode_themes = fetch_prior_episode_themes(storage, job_id)
        except Exception:
            logging.exception("prior episode theme extraction failed job_id=%s", job_id)
            prior_episode_themes = ()
        if prior_episode_themes:
            historical_context = HistoricalContext(
                summary=script_directions.historical_context.summary,
                month_synthesis=script_directions.historical_context.month_synthesis,
                yearly_narrative=script_directions.historical_context.yearly_narrative,
                prior_episode_themes=prior_episode_themes,
            )
    if payload.get("article_content") and isinstance(payload["article_content"], str):
        script_config = ScriptGenConfig.from_env()
        if script_config.ready:
            try:
                llm_script = generate_script(
                    week=str(payload["week"]),
                    article_title=str(payload.get("article_title") or ""),
                    article_url=str(payload["article_url"]),
                    article_content=payload["article_content"],
                    article_sha256=str(payload.get("article_sha256") or ""),
                    config=script_config,
                    podcast_config=podcast_config,
                    script_directions=script_directions,
                    historical_context=historical_context,
                    breaking_news=payload.get("breaking_news") or None,
                )
                llm_generation_engine = "llm-script-gen"
                sections = parse_script_sections(llm_script, podcast_config)
                if sections:
                    llm_sections_json = (
                        json.dumps(
                            {"sections": sections_to_metadata(sections)},
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n"
                    )
                logging.info("podcaster job using LLM-generated script job_id=%s", job_id)
            except Exception:
                logging.exception(
                    "LLM script generation failed job_id=%s; falling back to placeholder",
                    job_id,
                )
                warnings.append("LLM script generation failed; using placeholder script")
                llm_script = None
            # Extract claims from article content (#141)
            try:
                claims = extract_claims(
                    article_content=payload["article_content"],
                    article_url=str(payload["article_url"]),
                    config=script_config,
                )
                if claims:
                    llm_claims_json = claims_to_ledger_json(claims)
                    logging.info("podcaster job extracted %d claims job_id=%s", len(claims), job_id)
            except Exception:
                logging.exception("claim extraction failed job_id=%s; using stub ledger", job_id)
                warnings.append("claim extraction failed; using stub claim ledger")
        else:
            warnings.append(
                "article_content provided but chat endpoint not configured; using "
                "placeholder script"
            )
    if llm_script is None:
        warnings.append("audio is a deterministic placeholder pending TTS implementation")

    stored: dict[str, StoredArtifact] = {}
    checksums: dict[str, str] = {}
    cost_ledger: dict[str, Any] | None = None
    audio_validation = None
    for artifact in generate_artifacts(
        job_id,
        payload,
        current,
        expires_at,
        prior_monthly_episode_count=prior_episode_count,
        prior_monthly_spend_usd=prior_monthly_spend,
        cost_override=cost_override,
        config=podcast_config,
    ):
        # Replace the deterministic script with the LLM-generated one if available.
        if llm_script and artifact.path.endswith("/script.txt"):
            from podcaster.generation import GeneratedArtifact

            artifact = GeneratedArtifact(
                artifact.path, llm_script.encode("utf-8"), artifact.content_type
            )
        # Replace the stub claim ledger with LLM-extracted claims if available.
        if llm_claims_json and artifact.path.endswith("/claim-ledger.json"):
            from podcaster.generation import GeneratedArtifact

            artifact = GeneratedArtifact(
                artifact.path, llm_claims_json.encode("utf-8"), artifact.content_type
            )
        artifact_checksum = checksum(artifact.content)
        if artifact.path.endswith(".mp3"):
            audio_validation = placeholder_audio_validation(
                byte_length=len(artifact.content), sha256=artifact_checksum
            )
        stored_artifact = storage.put_bytes(artifact.path, artifact.content, artifact.content_type)
        stored[artifact.path] = stored_artifact
        checksums[artifact.path] = artifact_checksum
        if artifact.path.endswith("/cost-ledger.json"):
            loaded_cost_ledger = json.loads(artifact.content.decode("utf-8"))
            if not isinstance(loaded_cost_ledger, dict):
                raise RuntimeError("generated cost ledger was not a JSON object")
            cost_ledger = loaded_cost_ledger
    if llm_sections_json:
        from podcaster.generation import GeneratedArtifact

        artifact = GeneratedArtifact(
            f"jobs/{job_id}/sections.json",
            llm_sections_json.encode("utf-8"),
            "application/json; charset=utf-8",
        )
        artifact_checksum = checksum(artifact.content)
        stored_artifact = storage.put_bytes(artifact.path, artifact.content, artifact.content_type)
        stored[artifact.path] = stored_artifact
        checksums[artifact.path] = artifact_checksum

    # Store the article content as a pinned blob so the video pipeline can replay
    # against the exact bytes captured at generation time rather than re-fetching
    # the live URL (which may have changed or been removed since enqueue).
    if isinstance(article_content_raw, str) and article_content_raw:
        article_blob_path = f"jobs/{job_id}/article.txt"
        storage.put_bytes(
            article_blob_path,
            article_content_raw.encode("utf-8"),
            "text/plain; charset=utf-8",
        )

    if cost_ledger is None:
        raise RuntimeError("generated artifacts did not include cost-ledger.json")
    if audio_validation is None:
        audio_validation = placeholder_audio_validation(byte_length=0, sha256="")

    created_at = current.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    auto_publish = os.environ.get("PODCAST_AUTO_PUBLISH", "").lower() == "true"
    manifest_status = "dry_run" if payload.get("dry_run") else "accepted"
    manifest = {
        "schema_version": "squadscope-podcaster-job-v1",
        "job_id": job_id,
        "status": manifest_status,
        "created_at": created_at,
        "expires_at": expires_at,
        "request": _request_metadata(payload),
        "lifecycle": _lifecycle_metadata(payload, created_at, manifest_status),
        "review": _review_metadata(payload),
        "cost_ledger": cost_ledger,
        "generation": {
            "engine": llm_generation_engine,
            "deterministic": llm_script is None,
            "audio_mode": "placeholder",
            "tts_provider": None,
            "tts_voice": None,
            "tts_synthesis": {
                "status": "blocked" if payload.get("dry_run") else "queued",
                "allowed": bool(not payload.get("dry_run")),
                "blocked_by": ["dry_run"] if payload.get("dry_run") else [],
                "dry_run_bypass_allowed": bool(payload.get("dry_run")),
            },
            "audio_validation": audio_validation.to_manifest(),
            "synthesis_queue": {
                "status": "not_requested" if payload.get("dry_run") else "pending",
                "enqueued_at": None,
                "detail": None,
            },
        },
        "publishing": {
            "mode": "auto" if auto_publish else "review_gate",
            "auto_publish_enabled": auto_publish,
            "packet_ready": False,
            "eligible": False,
            "blocked_by": [
                "human_review",
                "synthesis_not_completed",
                "audio_validation_not_passed",
            ],
            "readiness_checks": {
                "cost_ledger_complete": bool(cost_ledger.get("readiness", {}).get("complete"))
                if isinstance(cost_ledger.get("readiness"), dict)
                else False,
                "budget_status": cost_ledger.get("budget", {}).get("status")
                if isinstance(cost_ledger.get("budget"), dict)
                else "unknown",
                "editorial_review_complete": False,
                "real_audio_available": False,
                "audio_validation_passed": False,
            },
            "public_url": None,
        },
        "artifact_access": artifact_access_metadata(job_id, created_at, expires_at),
        "artifacts": {
            path: {
                "url": artifact.url,
                "access_model": ACCESS_MODEL,
                "publicly_accessible": False,
                "size_bytes": artifact.size_bytes,
                "content_type": artifact.content_type,
                "sha256": checksums[path],
            }
            for path, artifact in stored.items()
        },
        "observability": {
            "correlation_id": job_id,
            "log_schema_version": "2026-06-07",
            "safe_log_fields": ["job_id", "week", "status", "artifact_count", "dry_run"],
        },
        "warnings": warnings,
    }
    manifest_artifact = storage.put_bytes(
        manifest_path, manifest_bytes(manifest), "application/json; charset=utf-8"
    )

    def finalize_monthly_budget(content: bytes | None) -> bytes:
        monthly_ledger = load_monthly_ledger(content, month=month)
        updated_monthly_ledger = update_monthly_ledger(
            monthly_ledger, job_id=job_id, episode_ledger=cost_ledger
        )
        return manifest_bytes(updated_monthly_ledger)

    storage.update_bytes(monthly_path, "application/json; charset=utf-8", finalize_monthly_budget)
    logging.info(
        "podcaster job staged job_id=%s status=%s dry_run=%s artifact_count=%s",
        job_id,
        manifest["status"],
        bool(payload.get("dry_run")),
        len(stored) + 1,
    )

    if not payload.get("dry_run"):
        enqueue_state = _enqueue_synthesis(job_id, enqueue)
        if enqueue_state["warning"]:
            warnings.append(enqueue_state["warning"])
        manifest["generation"]["synthesis_queue"] = {
            "status": enqueue_state["status"],
            "enqueued_at": enqueue_state["enqueued_at"],
            "detail": enqueue_state["detail"],
        }
        if enqueue_state["warning"] and enqueue_state["warning"] not in manifest["warnings"]:
            manifest["warnings"].append(enqueue_state["warning"])
        _record_enqueue_state(storage, manifest_path, enqueue_state)

    response = _response_from_artifacts(
        job_id=job_id,
        status="dry_run" if payload.get("dry_run") else "accepted",
        manifest_url=manifest_artifact.url,
        artifacts=stored,
        expires_at=expires_at,
        warnings=warnings,
    )
    return JobResult(response=response, manifest=manifest)


def failed_response(errors: list[str], warnings: list[str] | None = None) -> dict[str, Any]:
    return dict(
        zip(
            RESPONSE_KEYS,
            [None, "failed", None, None, None, None, None, None, None, warnings or [], errors],
            strict=True,
        )
    )


def _enqueue_synthesis(job_id: str, enqueue: Callable[[str], bool] | None) -> dict[str, Any]:
    """Best-effort enqueue of the synthesis message once gates have passed.

    Failures (including an unconfigured queue) never break the stable async 202
    contract: the request is already staged and the placeholder/publication block
    remains in force until the ACA Job is provisioned.
    """

    send = enqueue or enqueue_synthesis_job
    current = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        enqueued = bool(send(job_id))
        if enqueued:
            return {
                "status": "enqueued",
                "enqueued_at": current,
                "detail": None,
                "warning": None,
            }
        return {
            "status": "not_configured",
            "enqueued_at": current,
            "detail": "synthesis queue not configured",
            "warning": (
                "synthesis queue not configured; job will remain staged until synthesis is replayed"
            ),
        }
    except Exception:
        logging.exception(
            "synthesis enqueue failed job_id=%s; continuing with staged placeholder", job_id
        )
        return {
            "status": "failed",
            "enqueued_at": current,
            "detail": "synthesis enqueue failed",
            "warning": "synthesis enqueue failed; job remains staged until synthesis is replayed",
        }


def _job_already_in_ledger(monthly_ledger: dict[str, Any], job_id: str) -> bool:
    """Return True if job_id already has an entry in the monthly ledger.

    Used to detect retries: if the job already consumed a budget slot on a
    prior run, a re-submission should not be blocked by budget limits that
    were exceeded by OTHER jobs between the original run and the retry.
    """
    episodes = monthly_ledger.get("episodes")
    if not isinstance(episodes, list):
        return False
    return any(isinstance(ep, dict) and ep.get("job_id") == job_id for ep in episodes)


def _request_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    callback = payload.get("callback") if isinstance(payload.get("callback"), dict) else {}
    callback_url = callback.get("url") if isinstance(callback, dict) else None
    cost_override = _cost_override(payload)
    request = {
        "week": payload.get("week"),
        "article_url": payload.get("article_url"),
        "article_sha256": payload.get("article_sha256"),
        "article_title": payload.get("article_title"),
        "article_content_provided": bool(payload.get("article_content")),
        "source_artifacts": payload.get("source_artifacts", []),
        "dry_run": bool(payload.get("dry_run")),
        "force": bool(payload.get("force")),
        "cost_override": {
            "recorded": cost_override is not None,
            "actor": cost_override.get("actor") if cost_override else None,
            "recorded_at": cost_override.get("recorded_at") if cost_override else None,
        },
        "callback": {
            "requested": bool(payload.get("callback")),
            "url_host": urlparse(callback_url).netloc if isinstance(callback_url, str) else None,
            "secret_name_provided": bool(callback.get("secret_name"))
            if isinstance(callback, dict)
            else False,
        },
    }
    if isinstance(payload.get("podcast_config"), dict):
        request["podcast_config"] = payload["podcast_config"]
    if isinstance(payload.get("script_directions"), dict):
        request["script_directions"] = payload["script_directions"]
    if isinstance(payload.get("backchannels"), dict):
        request["backchannels"] = payload["backchannels"]
    if isinstance(payload.get("spotify_publish"), dict):
        request["spotify_publish"] = payload["spotify_publish"]
    return request


def _cost_override(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("force") is not True:
        return None
    override = payload.get("cost_override")
    if not isinstance(override, dict):
        return None
    actor = override.get("actor")
    reason = override.get("reason")
    recorded_at = override.get("recorded_at")
    if all(
        isinstance(value, str) and bool(value.strip()) for value in (actor, reason, recorded_at)
    ):
        return {"actor": actor, "reason": reason, "recorded_at": recorded_at}
    return None


def _lifecycle_metadata(payload: dict[str, Any], created_at: str, status: str) -> dict[str, Any]:
    transitions = [
        {
            "at": created_at,
            "to": "dry_run" if payload.get("dry_run") else "accepted",
            "reason": "request_validated",
        }
    ]
    return {
        "status": status,
        "revision": 1,
        "force": bool(payload.get("force")),
        "transitions": transitions,
    }


def _review_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "required": True,
        "required_for_tts": False,
        "status": "pending",
        "mechanism": "github_environment",
        "environment": "podcast-review",
        "workflow": ".github/workflows/podcast-review-gate.yml",
        "approved_by": None,
        "approved_at": None,
        "audit_trail": [],
        "artifacts_for_review": [
            "script.txt",
            "claim-ledger.json",
            "cost-ledger.json",
            "transcript.txt",
            "show-notes.md",
            "review-checklist.md",
            "manifest.json",
            "publishing-packet.zip",
        ],
        "gate": {
            "status": "dry_run_bypass" if payload.get("dry_run") else "blocked",
            "approval_required_before": "spotify_publication",
            "checks": [
                "script_accuracy",
                "claim_verification",
                "citation_link_integrity",
                "transcript_readiness",
                "tts_readiness",
                "rights_attribution",
            ],
        },
    }


def _response_from_artifacts(
    *,
    job_id: str,
    status: str,
    manifest_url: str,
    artifacts: dict[str, StoredArtifact],
    expires_at: str,
    warnings: list[str],
) -> dict[str, Any]:
    def find(suffix: str) -> str | None:
        for path, artifact in artifacts.items():
            if path.endswith(suffix):
                return artifact.url
        return None

    return dict(
        zip(
            RESPONSE_KEYS,
            [
                job_id,
                status,
                manifest_url,
                find(".mp3"),
                find(".wav"),
                find("transcript.txt"),
                find("show-notes.md"),
                find(".zip"),
                expires_at,
                warnings,
                [],
            ],
            strict=True,
        )
    )


def _record_enqueue_state(
    storage: StorageBackend, manifest_path: str, state: dict[str, Any]
) -> None:
    content = storage.get_bytes(manifest_path)
    document = json.loads(content.decode("utf-8")) if content else {}
    if not isinstance(document, dict):
        document = {}
    generation = document.setdefault("generation", {})
    if isinstance(generation, dict):
        generation["synthesis_queue"] = {
            "status": state["status"],
            "enqueued_at": state["enqueued_at"],
            "detail": state["detail"],
        }
    warnings = document.get("warnings")
    if state.get("warning") and isinstance(warnings, list) and state["warning"] not in warnings:
        warnings.append(state["warning"])
    lifecycle = document.setdefault("lifecycle", {})
    if isinstance(lifecycle, dict) and isinstance(lifecycle.get("transitions"), list):
        lifecycle["transitions"].append(
            {
                "at": state["enqueued_at"],
                "to": "accepted",
                "reason": f"synthesis_queue_{state['status']}",
            }
        )
        lifecycle["revision"] = int(lifecycle.get("revision") or 1) + 1
    storage.put_bytes(manifest_path, manifest_bytes(document), "application/json; charset=utf-8")
