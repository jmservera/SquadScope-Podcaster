from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy-azure.yml"
REUSABLE_WORKFLOW = ROOT / ".github/workflows/reusable-deploy-azure.yml"
BICEP = ROOT / "infra/main.bicep"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _reusable_workflow_text() -> str:
    return REUSABLE_WORKFLOW.read_text(encoding="utf-8")


def test_deploy_workflow_stays_manual_only_for_pr_validation() -> None:
    workflow = _workflow_text()

    on_match = re.search(r"(?m)^on:\s*\n(?P<body>(?:^[ \t].*\n)*)", workflow)
    assert on_match, "deploy-azure.yml must define an `on:` block"
    body = on_match.group("body")

    dispatch_match = re.search(r"(?m)^(?P<indent>[ \t]+)workflow_dispatch:", body)
    assert dispatch_match, "deploy-azure.yml must be triggered only via workflow_dispatch"
    indent = dispatch_match.group("indent")

    assert not re.search(rf"(?m)^{re.escape(indent)}(?!workflow_dispatch:)\w+:", body)


def test_deploy_workflow_uses_oidc_auth() -> None:
    """Wrapper workflow must request OIDC and forward to the reusable deploy."""
    workflow = _workflow_text()

    assert re.search(r"(?m)^  id-token: write$", workflow), (
        "deploy-azure.yml must request id-token: write for OIDC"
    )
    assert "uses: ./.github/workflows/reusable-deploy-azure.yml" in workflow
    assert "secrets: inherit" in workflow


def test_reusable_deploy_workflow_uses_oidc_auth() -> None:
    """Reusable ACA deploy authenticates via OIDC (id-token: write) and azure/login."""
    workflow = _reusable_workflow_text()

    assert re.search(r"(?m)^  id-token: write$", workflow), (
        "reusable-deploy-azure.yml must request id-token: write for OIDC"
    )
    assert "azure/login@" in workflow, "reusable-deploy-azure.yml must use azure/login action"


def test_reusable_deploy_workflow_threads_spotify_publish_settings() -> None:
    workflow = _reusable_workflow_text()
    aca_module = (ROOT / "infra/modules/aca.bicep").read_text(encoding="utf-8")
    api_module = (ROOT / "infra/modules/api.bicep").read_text(encoding="utf-8")

    for token in (
        "SPOTIFY_PUBLISH_ENABLED",
        "SPOTIFY_SHOW_ID",
        "SP_DC",
        "SP_KEY",
        "PODCAST_AUTO_PUBLISH",
    ):
        assert token in workflow
        assert token in aca_module or token in api_module


def test_reusable_deploy_workflow_threads_required_youtube_settings() -> None:
    workflow = _reusable_workflow_text()
    aca_video_module = (ROOT / "infra/modules/aca-video.bicep").read_text(encoding="utf-8")
    main_bicep = BICEP.read_text(encoding="utf-8")

    for token in (
        "VIDEO_YOUTUBE_ENABLED",
        "VIDEO_YOUTUBE_REQUIRED",
        "VIDEO_YOUTUBE_CATEGORY_ID",
        "VIDEO_YOUTUBE_PRIVACY",
        "VIDEO_YOUTUBE_CLIENT_ID",
        "VIDEO_YOUTUBE_CLIENT_SECRET",
        "VIDEO_YOUTUBE_REFRESH_TOKEN",
    ):
        assert token in workflow
        assert token in aca_video_module

    for token in (
        "param videoYoutubeEnabled",
        "param videoYoutubeRequired",
        "param videoYoutubeClientId",
        "param videoYoutubeClientSecret",
        "param videoYoutubeRefreshToken",
    ):
        assert token in main_bicep

    assert "VIDEO_YOUTUBE_ENABLED must be true or false" in workflow
    assert "VIDEO_YOUTUBE_REQUIRED must be true or false" in workflow
    assert "tr '[:upper:]' '[:lower:]'" in workflow

    # YouTube delivery is opt-in: unset vars must default to disabled, matching the
    # application's from_env() runtime default and the spotifyPublishEnabled toggle.
    assert "VIDEO_YOUTUBE_ENABLED_VAR:-false" in workflow
    assert "VIDEO_YOUTUBE_REQUIRED_VAR:-false" in workflow
    assert "videoYoutubeEnabled string = 'false'" in aca_video_module
    assert "videoYoutubeRequired string = 'false'" in aca_video_module
    assert "videoYoutubeEnabled string = 'false'" in main_bicep
    assert "videoYoutubeRequired string = 'false'" in main_bicep
    # Preflight must fail fast on the inconsistent required=true/enabled!=true config.
    assert "required delivery cannot be enforced while YouTube upload is disabled" in workflow


def test_reusable_deploy_workflow_deploys_bicep_infrastructure() -> None:
    """Reusable ACA workflow deploys infra via az deployment group create with main.bicep."""
    workflow = _reusable_workflow_text()

    assert "az deployment group create" in workflow
    assert "infra/main.bicep" in workflow
    assert "deploymentPrincipalObjectId" in workflow


def test_deploy_workflow_does_not_leak_secrets() -> None:
    """No SAS tokens, account keys, or API keys should appear in workflow logs."""
    workflow = _workflow_text() + "\n" + _reusable_workflow_text()

    assert "package_sas" not in workflow
    assert "az storage blob generate-sas" not in workflow
    assert "--as-user" not in workflow
    assert "AZURE_OPENAI_API_KEY" not in workflow


def test_deploy_workflow_no_function_app_remnants() -> None:
    """ACA-only architecture must not reference Function App deployment steps."""
    workflow = _workflow_text() + "\n" + _reusable_workflow_text()

    assert "az functionapp" not in workflow
    assert "WEBSITE_RUN_FROM_PACKAGE" not in workflow
    assert "python-version" not in workflow or "actions/setup-python" not in workflow
    assert "az webapp deploy" not in workflow


def test_bicep_references_aca_module() -> None:
    """infra/main.bicep must deploy the ACA module as the primary compute."""
    bicep = BICEP.read_text(encoding="utf-8")

    assert re.search(r"module aca 'modules/aca\.bicep'", bicep), (
        "infra/main.bicep must reference modules/aca.bicep"
    )
    assert "containerAppsEnvName" in bicep
    assert "synthesisJobName" in bicep


def test_bicep_no_function_app_remnants() -> None:
    """ACA-only bicep must not contain Function App settings."""
    bicep = BICEP.read_text(encoding="utf-8")

    assert "FUNCTIONS_WORKER_RUNTIME" not in bicep
    assert "AzureWebJobsFeatureFlags" not in bicep
    assert "Microsoft.Web/sites" not in bicep


def test_bicep_assigns_deploy_identity_storage_data_plane_role() -> None:
    bicep = BICEP.read_text(encoding="utf-8")

    assert "deploymentPrincipalObjectId" in bicep
    # Storage Blob Data Contributor RBAC role GUID
    assert "ba92f5b4-2d11-453d-a403-e96b0029c9fe" in bicep


OPENAI_MODULE = ROOT / "infra/modules/openai.bicep"


def test_openai_module_always_deployed_in_aca_architecture() -> None:
    # #109: In the ACA-only architecture, OpenAI is always deployed (not opt-in).
    bicep = BICEP.read_text(encoding="utf-8")

    assert re.search(r"module openAi 'modules/openai\.bicep' =", bicep), (
        "infra/main.bicep must deploy the OpenAI module"
    )
    # It should NOT be conditional anymore
    assert "if (deployOpenAi)" not in bicep, (
        "OpenAI module should be unconditionally deployed in ACA-only architecture"
    )


def test_openai_module_uses_managed_identity_not_keys() -> None:
    # #30 acceptance: managed identity / RBAC is preferred; account keys must never be
    # written to app settings, logs, or deployment outputs.
    module = OPENAI_MODULE.read_text(encoding="utf-8")
    main = BICEP.read_text(encoding="utf-8")

    assert "kind: 'OpenAI'" in module
    assert "disableLocalAuth: true" in module, "OpenAI account must disable local (key) auth"
    assert "type: 'SystemAssigned'" in module
    # Cognitive Services OpenAI User role for the ACA job managed identity.
    assert "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd" in module
    assert "listKeys" not in module and "listKeys" not in main, "must not read OpenAI account keys"
    assert "AZURE_OPENAI_API_KEY" not in module and "AZURE_OPENAI_API_KEY" not in main, (
        "OpenAI account keys must never be wired into infrastructure"
    )


def test_openai_module_deploys_fable_and_alloy_tts() -> None:
    # #4 provider decision: OpenAI TTS with voices fable (host A) + alloy (host B).
    main = BICEP.read_text(encoding="utf-8")

    assert "param ttsVoiceHostA string = 'fable'" in main
    assert "param ttsVoiceHostB string = 'alloy'" in main


def test_aca_module_receives_openai_config() -> None:
    """ACA module must receive OpenAI endpoint and deployment names."""
    aca_module = (ROOT / "infra/modules/aca.bicep").read_text(encoding="utf-8")

    assert "AZURE_OPENAI_ENDPOINT" in aca_module
    assert "AZURE_OPENAI_TTS_DEPLOYMENT" in aca_module
    assert "AZURE_OPENAI_CHAT_DEPLOYMENT" in aca_module


def test_deploy_workflow_threads_opt_in_openai_flag() -> None:
    # The deploy_openai input exists in workflow_dispatch for future use.
    workflow = _workflow_text() + "\n" + _reusable_workflow_text()

    assert "deploy_openai:" in workflow


def test_reusable_deploy_workflow_has_prod_environment_concurrency_and_output() -> None:
    workflow = _reusable_workflow_text()

    assert re.search(r"(?m)^    environment: prod$", workflow)
    assert re.search(
        r"(?m)^    concurrency:\n^      group: prod-deploy\n^      cancel-in-progress: false$",
        workflow,
    )
    assert "api_app_fqdn:" in workflow
    assert "id: get_fqdn" in workflow
    assert '--query "properties.outputs.apiAppFqdn.value"' in workflow


def test_reusable_deploy_workflow_accepts_image_overrides() -> None:
    workflow = _reusable_workflow_text()

    assert "inputs.synthesis_image || format('{0}/{1}:latest'" in workflow
    assert "inputs.api_image || format('{0}/{1}:latest'" in workflow


def test_bicep_provisions_blob_lifecycle_cleanup_policy() -> None:
    # Regression guard for the artifact retention contract (#89): the job manifest
    # and podcaster/artifact_access.py promise expires_at / retention.cleanup_after
    # with cleanup_owner "operator_or_storage_lifecycle_policy". Storage must
    # actually enforce that so expired artifacts do not accumulate forever.
    bicep = BICEP.read_text(encoding="utf-8")

    assert "Microsoft.Storage/storageAccounts/managementPolicies@" in bicep, (
        "infra/main.bicep must provision a Storage management (lifecycle) policy"
    )
    assert "param artifactRetentionDays int = 7" in bicep, (
        "artifact retention must default to the documented 7-day manifest expiry"
    )
    assert re.search(
        r"@minValue\(1\)\s*\n\s*@maxValue\(365\)\s*\n\s*param artifactRetentionDays", bicep
    )

    # Artifact container must be covered by delete rules tied to the retention param.
    # The artifacts rule targets auto-generated output prefixes (jobs/, bakeoff/) but must
    # NOT target the bare container, so operator review artifacts under review/ are retained (#93).
    assert "param autoExpireArtifactPrefixes array" in bicep, (
        "auto-expire prefixes must be parametrised so review/ can be excluded"
    )
    assert re.search(
        (
            r"prefixMatch:\s*\[for prefix in autoExpireArtifactPrefixes: "
            r"'\$\{storageContainerName\}/\$\{prefix\}'\]"
        ),
        bicep,
    ), "artifacts lifecycle rule must target the parametrised auto-expire prefixes"
    assert "'jobs/'" in bicep and "'bakeoff/'" in bicep, (
        "auto-expire prefixes must cover generated job and bakeoff outputs"
    )
    assert "'review/'" not in bicep, (
        "operator review artifacts (review/) must not be subject to lifecycle auto-delete (#93)"
    )
    assert "prefixMatch: [\n          '${storageContainerName}/'" not in bicep and not re.search(
        r"prefixMatch:\s*\[\s*'\$\{storageContainerName\}/'\s*\]", bicep
    ), "artifacts rule must not match the whole container (would auto-delete review/ artifacts)"
    assert "daysAfterModificationGreaterThan: artifactRetentionDays" in bicep
