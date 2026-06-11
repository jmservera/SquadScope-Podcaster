from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy-azure.yml"
BICEP = ROOT / "infra/main.bicep"
HOST_JSON = ROOT / "host.json"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_deploy_workflow_stays_manual_only_for_pr_validation() -> None:
    workflow = _workflow_text()

    on_match = re.search(r"(?m)^on:\s*\n(?P<body>(?:^[ \t].*\n)*)", workflow)
    assert on_match, "deploy-azure.yml must define an `on:` block"
    body = on_match.group("body")

    dispatch_match = re.search(r"(?m)^(?P<indent>[ \t]+)workflow_dispatch:", body)
    assert dispatch_match, "deploy-azure.yml must be triggered only via workflow_dispatch"
    indent = dispatch_match.group("indent")

    assert not re.search(rf"(?m)^{re.escape(indent)}(?!workflow_dispatch:)\w+:", body)

def test_deploy_workflow_packages_run_from_private_blob_with_oidc() -> None:
    workflow = _workflow_text()

    assert re.search(r"(?m)^  id-token: write$", workflow)
    assert "azure/login@" in workflow
    assert re.search(r"python-version:\s*['\"]?3\.11['\"]?", workflow)
    assert re.search(
        r"(?:python -m )?pip install\b[^\n]*--target[= ]['\"]?\.python_packages/lib/site-packages['\"]?[^\n]*-r requirements\.txt",
        workflow,
    )
    assert 'zipfile.ZipFile("app.zip", "w", zipfile.ZIP_DEFLATED)' in workflow
    assert 'Path("function_app.py")' in workflow
    assert 'Path("host.json")' in workflow
    assert 'Path("podcaster")' in workflow
    assert 'Path(".python_packages")' in workflow

    assert "az functionapp deployment source config-zip" not in workflow
    assert "az webapp deploy" not in workflow
    assert "az storage blob upload" in workflow
    assert "--auth-mode login" in workflow
    assert "az storage blob generate-sas" not in workflow
    assert "--as-user" not in workflow
    assert "WEBSITE_RUN_FROM_PACKAGE" in workflow
    assert "WEBSITE_USE_MANAGED_IDENTITY=true" in workflow
    assert "WEBSITE_RUN_FROM_PACKAGE_BLOB_MI_RESOURCE_ID=SystemAssigned" in workflow
    assert "az functionapp config appsettings set" in workflow
    assert "az functionapp restart" in workflow


def test_deploy_workflow_does_not_emit_package_secrets() -> None:
    workflow = _workflow_text()

    assert "package_sas" not in workflow
    assert "add-mask::$package_url" not in workflow
    assert "no package SAS or storage key was emitted" in workflow


def test_deploy_workflow_smokes_generate_without_printing_api_key() -> None:
    workflow = _workflow_text()

    assert "Smoke deployed generate endpoint" in workflow
    assert "PODCASTER_GENERATE_URL: ${{ steps.deploy_bicep.outputs.endpoint }}" in workflow
    assert "python scripts/smoke_generate.py" in workflow
    assert "refusing to smoke with an empty key" in workflow
    assert "API key was not printed" in workflow
    assert "--api-key" not in workflow


def test_deploy_workflow_excludes_local_and_secret_files_from_package() -> None:
    workflow = _workflow_text()

    for excluded in (
        '".git"',
        '".github"',
        '".venv"',
        '".pytest_cache"',
        '"__pycache__"',
        '"local.settings.json"',
        'path.name.startswith(".env")',
        'path.suffix == ".pyc"',
    ):
        assert excluded in workflow


def test_host_json_declares_extension_bundle_for_python_v2_indexing() -> None:
    # Regression guard for the /api/generate HTTP 404 (PR #54, #51): the Python
    # v2 (decorator) model only registers @app.route triggers when host.json
    # declares the extension bundle. Without it the host serves no routes.
    host = json.loads(HOST_JSON.read_text(encoding="utf-8"))

    bundle = host.get("extensionBundle")
    assert isinstance(bundle, dict), "host.json must declare an extensionBundle for Python v2 worker indexing"
    assert bundle.get("id") == "Microsoft.Azure.Functions.ExtensionBundle"
    assert bundle.get("version"), "host.json extensionBundle must pin a version range"


def test_bicep_enables_python_v2_worker_indexing() -> None:
    # Regression guard for the /api/generate HTTP 404 (PR #54, #51): the runtime
    # only indexes @app.route-decorated functions when these app settings are
    # present. Removing either one reintroduces the 404 on deploy.
    bicep = BICEP.read_text(encoding="utf-8")

    assert re.search(r"name:\s*'FUNCTIONS_WORKER_RUNTIME'\s*\n\s*value:\s*'python'", bicep), (
        "infra/main.bicep must set FUNCTIONS_WORKER_RUNTIME=python"
    )
    assert re.search(
        r"name:\s*'AzureWebJobsFeatureFlags'\s*\n\s*value:\s*'EnableWorkerIndexing'", bicep
    ), "infra/main.bicep must set AzureWebJobsFeatureFlags=EnableWorkerIndexing for Python v2 indexing"


def test_deploy_workflow_gates_smoke_on_function_indexing() -> None:
    # Regression guard for the /api/generate HTTP 404 (PR #54, #51): the deploy
    # must wait until the host registers the 'generate' trigger before the strict
    # smoke, so an unindexed/unmounted package fails with a precise error instead
    # of a misleading response-shape failure.
    workflow = _workflow_text()

    wait_index = workflow.find("Wait for Function App to index functions")
    smoke_index = workflow.find("Smoke deployed generate endpoint")
    assert wait_index != -1, "deploy-azure.yml must wait for the function host to index before smoke"
    assert smoke_index != -1, "deploy-azure.yml must keep the smoke step"
    assert wait_index < smoke_index, "indexing wait must run before the strict smoke step"
    assert "az functionapp function list" in workflow
    assert "AzureWebJobsFeatureFlags" in workflow


def test_bicep_assigns_deploy_identity_storage_data_plane_role() -> None:
    bicep = BICEP.read_text(encoding="utf-8")

    assert "param packageContainerName string = 'function-packages'" in bicep
    assert "deploymentPrincipalObjectId" in bicep
    assert "deploymentBlobDataContributor" in bicep
    assert "ba92f5b4-2d11-453d-a403-e96b0029c9fe" in bicep


OPENAI_MODULE = ROOT / "infra/modules/openai.bicep"


def test_openai_infra_is_opt_in_and_defaults_off() -> None:
    # #30: the production Azure OpenAI TTS infrastructure must be opt-in so the core
    # storage + Function App deploy stays green where the TTS model is unavailable.
    bicep = BICEP.read_text(encoding="utf-8")

    assert "param deployOpenAi bool = false" in bicep, (
        "deployOpenAi must default to false to protect the green deploy"
    )
    assert re.search(r"module openAi 'modules/openai\.bicep' = if \(deployOpenAi\)", bicep), (
        "OpenAI resources must deploy conditionally via the modules/openai.bicep module"
    )


def test_openai_module_uses_managed_identity_not_keys() -> None:
    # #30 acceptance: managed identity / RBAC is preferred; account keys must never be
    # written to Function App settings, logs, or deployment outputs.
    module = OPENAI_MODULE.read_text(encoding="utf-8")
    main = BICEP.read_text(encoding="utf-8")

    assert "kind: 'OpenAI'" in module
    assert "disableLocalAuth: true" in module, "OpenAI account must disable local (key) auth"
    assert "type: 'SystemAssigned'" in module
    # Cognitive Services OpenAI User role for the Function App managed identity.
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
    assert "AZURE_OPENAI_TTS_DEPLOYMENT" in main
    assert "AZURE_OPENAI_CHAT_DEPLOYMENT" in main
    assert "AZURE_OPENAI_ENDPOINT" in main


def test_deploy_workflow_threads_opt_in_openai_flag() -> None:
    # The opt-in flag must be plumbed through workflow_dispatch into the bicep deploy,
    # defaulting to false so unattended/normal deploys never provision OpenAI.
    workflow = _workflow_text()

    assert "deploy_openai:" in workflow
    assert "DEPLOY_OPENAI: ${{ inputs.deploy_openai }}" in workflow
    assert 'deployOpenAi="${DEPLOY_OPENAI:-false}"' in workflow
