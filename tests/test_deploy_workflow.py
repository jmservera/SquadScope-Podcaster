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
        "VIDEO_YOUTUBE_PLAYLIST_ID",
        "VIDEO_YOUTUBE_CLIENT_ID",
        "VIDEO_YOUTUBE_CLIENT_SECRET",
        "VIDEO_YOUTUBE_REFRESH_TOKEN",
    ):
        assert token in workflow
        assert token in aca_video_module

    for token in (
        "param videoYoutubeEnabled",
        "param videoYoutubeRequired",
        "param videoYoutubePlaylistId",
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
    assert "VIDEO_YOUTUBE_PRIVACY_VAR:-unlisted" in workflow
    assert "VIDEO_YOUTUBE_PLAYLIST_ID_VAR" in workflow
    assert '--parameters videoYoutubePlaylistId="${VIDEO_YOUTUBE_PLAYLIST_ID:-}"' in workflow
    # All four PRIVACY defaults in the workflow must use unlisted — never public.
    # This covers: (1) preflight check, (2) env-export step, (3) Bicep parameter.
    assert "VIDEO_YOUTUBE_PRIVACY_VAR:-public" not in workflow, (
        "env-export step must default VIDEO_YOUTUBE_PRIVACY to unlisted, not public"
    )
    assert (
        """echo "VIDEO_YOUTUBE_PRIVACY=$(printf '%s' "${VIDEO_YOUTUBE_PRIVACY_VAR:-unlisted}" """
        """| tr '[:upper:]' '[:lower:]')\"""" in workflow
    ), "env-export step must normalize the validated privacy value"
    assert 'VIDEO_YOUTUBE_PRIVACY:-public"' not in workflow, (
        "Bicep parameter must default videoYoutubePrivacy to unlisted, not public"
    )
    assert 'VIDEO_YOUTUBE_PRIVACY:-unlisted}"' in workflow, (
        "Bicep az-deploy parameter must default videoYoutubePrivacy to unlisted"
    )
    assert "videoYoutubeEnabled string = 'false'" in aca_video_module
    assert "videoYoutubeRequired string = 'false'" in aca_video_module
    assert "videoYoutubePrivacy string = 'unlisted'" in aca_video_module
    assert "videoYoutubePlaylistId string = ''" in aca_video_module
    assert "videoYoutubeEnabled string = 'false'" in main_bicep
    assert "videoYoutubeRequired string = 'false'" in main_bicep
    assert "videoYoutubePrivacy string = 'unlisted'" in main_bicep
    assert "videoYoutubePlaylistId string = ''" in main_bicep
    # Preflight must fail fast on the inconsistent required=true/enabled!=true config.
    assert "required delivery cannot be enforced while YouTube upload is disabled" in workflow
    assert (
        "VIDEO_YOUTUBE_PLAYLIST_ID must be configured when YouTube uploads are enabled" in workflow
    )
    assert (
        "VIDEO_YOUTUBE_PRIVACY must be unlisted or private when YouTube uploads are enabled"
        in workflow
    )


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


def test_api_app_name_is_derived_inside_its_module() -> None:
    """The API app name is an internal module detail, not a root deployment parameter."""
    bicep = BICEP.read_text(encoding="utf-8")
    api_module = (ROOT / "infra/modules/api.bicep").read_text(encoding="utf-8")

    assert "param apiAppName" not in bicep
    assert "apiAppName:" not in bicep
    assert "baseName: baseName" in bicep
    assert "param baseName string" in api_module
    assert "var apiAppName = '${baseName}-api'" in api_module


def test_reusable_deploy_workflow_defaults_location_to_eastus2() -> None:
    workflow = _reusable_workflow_text()

    assert "AZURE_LOCATION: ${{ vars.AZURE_LOCATION || 'eastus2' }}" in workflow
    assert "require_config AZURE_LOCATION" not in workflow


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


NETWORK_MODULE = ROOT / "infra/modules/network.bicep"
OPENAI_PE_MODULE = ROOT / "infra/modules/openai-private-endpoint.bicep"


def test_openai_private_by_default_in_vnet_mode() -> None:
    # #598: In VNet mode (deployVnet=true) the OpenAI account must be private-by-default —
    # public network access disabled and network ACLs default to Deny — mirroring the
    # Storage account. The public endpoint is only kept for local dev/test (deployVnet=false).
    module = OPENAI_MODULE.read_text(encoding="utf-8")
    main = BICEP.read_text(encoding="utf-8")

    assert "param deployVnet bool = false" in module, (
        "openai.bicep must accept deployVnet to gate its network posture"
    )
    assert "publicNetworkAccess: deployVnet ? 'Disabled' : 'Enabled'" in module, (
        "OpenAI public network access must be disabled in VNet mode (#598)"
    )
    assert "defaultAction: deployVnet ? 'Deny' : 'Allow'" in module, (
        "OpenAI network ACLs must default to Deny in VNet mode (#598)"
    )
    # main.bicep must actually thread deployVnet into the OpenAI module.
    assert re.search(
        r"module openAi 'modules/openai\.bicep'[\s\S]*?deployVnet: deployVnet", main
    ), "main.bicep must pass deployVnet to the OpenAI module"


def test_openai_private_endpoint_wired_in_vnet_mode() -> None:
    # #598: VNet mode must add a private endpoint + private DNS zone for OpenAI so the
    # ACA synthesis job reaches TTS/chat over the VNet instead of the public endpoint.
    network = NETWORK_MODULE.read_text(encoding="utf-8")
    pe_module = OPENAI_PE_MODULE.read_text(encoding="utf-8")
    main = BICEP.read_text(encoding="utf-8")

    assert re.search(r"privatelink\.openai\.azure\.com", network), (
        "network.bicep must create the OpenAI private DNS zone"
    )
    assert "output openAiDnsZoneId string" in network, (
        "network.bicep must expose the OpenAI DNS zone id for the PE module"
    )
    # Cognitive Services private endpoints use the 'account' group id — assert on the
    # actual groupIds entry, not the module comment which also mentions 'account'.
    assert re.search(r"groupIds:\s*\[\s*'account'\s*\]", pe_module)
    assert "Microsoft.Network/privateEndpoints@" in pe_module
    assert "privateDnsZoneGroups@" in pe_module
    # The PE module must be deployed only in VNet mode and fed the account + DNS zone.
    assert re.search(
        r"module openAiPrivateEndpoint 'modules/openai-private-endpoint\.bicep' "
        r"= if \(deployVnet\)",
        main,
    ), "main.bicep must deploy the OpenAI private endpoint module only in VNet mode"
    assert "openAiAccountId: openAi.outputs.accountId" in main
    assert "openAiDnsZoneId: network!.outputs.openAiDnsZoneId" in main


ACR_MODULE = ROOT / "infra/modules/acr.bicep"
ACR_PE_MODULE = ROOT / "infra/modules/acr-private-endpoint.bicep"


def test_acr_private_by_default_in_vnet_mode() -> None:
    # #598: In VNet mode (deployVnet=true) the container registry must be
    # private-by-default — public network access disabled — mirroring Storage and
    # OpenAI. Private endpoints require the Premium SKU, so VNet mode forces it.
    # The public endpoint + Basic SKU are kept only for local dev/test.
    module = ACR_MODULE.read_text(encoding="utf-8")
    main = BICEP.read_text(encoding="utf-8")

    assert "param deployVnet bool = false" in module, (
        "acr.bicep must accept deployVnet to gate its network posture"
    )
    assert "publicNetworkAccess: deployVnet ? 'Disabled' : 'Enabled'" in module, (
        "ACR public network access must be disabled in VNet mode (#598)"
    )
    assert "effectiveSkuName = deployVnet ? 'Premium' : skuName" in module, (
        "ACR must use the Premium SKU in VNet mode so a private endpoint can attach"
    )
    # main.bicep must actually thread deployVnet into the ACR module.
    assert re.search(r"module acr 'modules/acr\.bicep'[\s\S]*?deployVnet: deployVnet", main), (
        "main.bicep must pass deployVnet to the ACR module"
    )


def test_acr_private_endpoint_wired_in_vnet_mode() -> None:
    # #598: VNet mode must add a private endpoint + private DNS zone for the ACR so
    # ACA pulls images over the VNet instead of the public endpoint.
    network = NETWORK_MODULE.read_text(encoding="utf-8")
    pe_module = ACR_PE_MODULE.read_text(encoding="utf-8")
    main = BICEP.read_text(encoding="utf-8")

    assert "name: 'privatelink.azurecr.io'" in network, (
        "network.bicep must create the ACR private DNS zone"
    )
    assert "output acrDnsZoneId string" in network, (
        "network.bicep must expose the ACR DNS zone id for the PE module"
    )
    # ACR private endpoints use the 'registry' group id — assert on the actual
    # groupIds entry, not the module comment which also mentions 'registry'.
    assert re.search(r"groupIds:\s*\[\s*'registry'\s*\]", pe_module)
    assert "Microsoft.Network/privateEndpoints@" in pe_module
    assert "privateDnsZoneGroups@" in pe_module
    # The PE module must be deployed only when both VNet mode and ACR are enabled,
    # and fed the registry id + DNS zone.
    assert re.search(
        r"module acrPrivateEndpoint 'modules/acr-private-endpoint\.bicep' "
        r"= if \(deployVnet && deployAcr\)",
        main,
    ), "main.bicep must deploy the ACR private endpoint module only in VNet mode"
    assert "registryId: acr!.outputs.registryId" in main
    assert "acrDnsZoneId: network!.outputs.acrDnsZoneId" in main


def test_reusable_deploy_workflow_uses_vars_reference_for_youtube_playlist_id() -> None:
    workflow = _reusable_workflow_text()

    assert "VIDEO_YOUTUBE_PLAYLIST_ID_VAR: ${{ vars.VIDEO_YOUTUBE_PLAYLIST_ID }}" in workflow
    assert "VIDEO_YOUTUBE_PLAYLIST_ID_VAR: ${{ secrets.VIDEO_YOUTUBE_PLAYLIST_ID }}" not in workflow


def test_reusable_deploy_workflow_masks_youtube_credentials_but_not_playlist_id() -> None:
    workflow = _reusable_workflow_text()

    assert (
        '[ -n "${VIDEO_YOUTUBE_CLIENT_ID_SECRET:-}" ] '
        '&& echo "::add-mask::$VIDEO_YOUTUBE_CLIENT_ID_SECRET"'
    ) in workflow
    assert (
        '[ -n "${VIDEO_YOUTUBE_CLIENT_SECRET_SECRET:-}" ] '
        '&& echo "::add-mask::$VIDEO_YOUTUBE_CLIENT_SECRET_SECRET"'
    ) in workflow
    assert (
        '[ -n "${VIDEO_YOUTUBE_REFRESH_TOKEN_SECRET:-}" ] '
        '&& echo "::add-mask::$VIDEO_YOUTUBE_REFRESH_TOKEN_SECRET"'
    ) in workflow
    assert "VIDEO_YOUTUBE_PLAYLIST_ID_VAR" in workflow
    assert "::add-mask::$VIDEO_YOUTUBE_PLAYLIST_ID" not in workflow
    assert "::add-mask::$VIDEO_YOUTUBE_PLAYLIST_ID_VAR" not in workflow


def test_reusable_deploy_workflow_only_requires_youtube_secrets_when_enabled() -> None:
    workflow = _reusable_workflow_text()
    client_id_check = (
        'require_config VIDEO_YOUTUBE_CLIENT_ID_SECRET "when YouTube delivery is enabled"'
    )
    client_secret_check = (
        'require_config VIDEO_YOUTUBE_CLIENT_SECRET_SECRET "when YouTube delivery is enabled"'
    )
    refresh_token_check = (
        'require_config VIDEO_YOUTUBE_REFRESH_TOKEN_SECRET "when YouTube delivery is enabled"'
    )

    enabled_block = re.search(
        r'if \[ "\$youtube_enabled" = "true" \]; then(?P<body>.*?)\n          fi',
        workflow,
        re.DOTALL,
    )
    assert enabled_block, "YouTube preflight checks must be gated behind youtube_enabled=true"
    body = enabled_block.group("body")

    assert client_id_check in body
    assert client_secret_check in body
    assert refresh_token_check in body

    assert workflow.count(client_id_check) == 1
    assert workflow.count(client_secret_check) == 1
    assert workflow.count(refresh_token_check) == 1
