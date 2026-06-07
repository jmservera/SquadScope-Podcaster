from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy-azure.yml"
BICEP = ROOT / "infra/main.bicep"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_deploy_workflow_stays_manual_only_for_pr_validation() -> None:
    workflow = _workflow_text()

    assert re.search(r"(?m)^on:\s*\n(?:^[ \t].*\n)*^[ \t]+workflow_dispatch:", workflow)
    assert not re.search(r"(?m)^  (push|pull_request|pull_request_target):", workflow)


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


def test_bicep_assigns_deploy_identity_storage_data_plane_role() -> None:
    bicep = BICEP.read_text(encoding="utf-8")

    assert "param packageContainerName string = 'function-packages'" in bicep
    assert "deploymentPrincipalObjectId" in bicep
    assert "deploymentBlobDataContributor" in bicep
    assert "ba92f5b4-2d11-453d-a403-e96b0029c9fe" in bicep
