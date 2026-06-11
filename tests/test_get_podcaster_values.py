"""Discovery/dry-run tests for scripts/get-podcaster-values.sh.

The script shells out to the Azure CLI (`az`). These tests put a fake `az` on
PATH that returns canned resource-group data, then assert the script emits the
correct `gh secret set` commands with the discovered values — without contacting
Azure and without leaking secrets into CI outputs.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "get-podcaster-values.sh"

FAKE_AZ = r"""#!/usr/bin/env bash
# Fake Azure CLI returning deterministic resource-group discovery data.
set -euo pipefail

# Resolve the --query value (mimics az JMESPath projection for our calls).
query=""
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [ "${args[$i]}" = "--query" ]; then
    query="${args[$((i+1))]}"
  fi
done

case "$1 $2" in
  "account show") echo '{"id":"sub-1"}'; exit 0;;
esac

cmd="$1 $2 ${3:-}"
case "$cmd" in
  "storage account list")
    [ "$query" = "[0].name" ] && echo "podcasterfakestg"; exit 0;;
  "storage account show")
    case "$query" in
      "primaryEndpoints.blob") echo "https://podcasterfakestg.blob.core.windows.net/"; exit 0;;
      "primaryEndpoints.queue") echo "https://podcasterfakestg.queue.core.windows.net/"; exit 0;;
    esac
    exit 0;;
  "cognitiveservices account list")
    if [ "$query" = "[?kind=='OpenAI'].name | [0]" ]; then echo "podcaster-fake-openai"; fi
    if [ "$query" = "[0].name" ]; then echo "podcaster-fake-openai"; fi
    exit 0;;
  "cognitiveservices account show")
    [ "$query" = "properties.endpoint" ] && echo "https://podcaster-fake-openai.openai.azure.com/"; exit 0;;
  "cognitiveservices account keys")
    [ "$query" = "key1" ] && echo "fake-openai-key-xyz789"; exit 0;;
  "containerapp job list")
    [ "$query" = "[0].name" ] && echo "podcaster-fake-synth"; exit 0;;
  "containerapp job show")
    [ "$query" = "properties.template.containers[0].env[?name=='PODCASTER_API_KEY'].value | [0]" ] && echo "fake-podcaster-key-abc123"; exit 0;;
esac
exit 0
"""


def _make_fake_az(tmp_path: Path) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    az = bindir / "az"
    az.write_text(FAKE_AZ)
    az.chmod(az.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    # Ensure CI guard does not trip inside the test runner.
    env.pop("CI", None)
    env.pop("GITHUB_ACTIONS", None)
    return env


def _run(tmp_path: Path, *args: str):
    env = _make_fake_az(tmp_path)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_emits_squadscope_caller_secret_commands(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert (
        "gh secret set PODCASTER_API_KEY --repo jmservera/SquadScope "
        "--body 'fake-podcaster-key-abc123'"
    ) in out


def test_emits_azure_openai_secret_commands(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert (
        "gh variable set AZURE_OPENAI_ENDPOINT --repo jmservera/SquadScope-Podcaster "
        "--body 'https://podcaster-fake-openai.openai.azure.com/'"
    ) in out
    assert (
        "gh secret set AZURE_OPENAI_API_KEY --repo jmservera/SquadScope-Podcaster "
        "--body 'fake-openai-key-xyz789'"
    ) in out


def test_emits_queue_endpoint_variable(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert (
        "gh variable set PODCASTER_QUEUE_ENDPOINT --repo jmservera/SquadScope "
        "--body 'https://podcasterfakestg.queue.core.windows.net/'"
    ) in out


def test_custom_repos_and_resource_group(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "--resource-group",
        "my-rg",
        "--squadscope-repo",
        "acme/Scope",
        "--podcaster-repo",
        "acme/Pod",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Discovered in resource group: my-rg" in out
    assert "--repo acme/Scope --body" in out
    assert "--repo acme/Pod --body" in out


def test_out_file_keeps_secrets_off_stdout(tmp_path: Path) -> None:
    out_file = tmp_path / "secrets.sh"
    result = _run(tmp_path, "--out", str(out_file))
    assert result.returncode == 0, result.stderr
    # Secret values must not appear on stdout when --out is used.
    assert "fake-podcaster-key-abc123" not in result.stdout
    assert "fake-openai-key-xyz789" not in result.stdout
    contents = out_file.read_text()
    assert "fake-podcaster-key-abc123" in contents
    assert "fake-openai-key-xyz789" in contents
    # File should be created with owner-only permissions (umask 077).
    mode = stat.S_IMODE(out_file.stat().st_mode)
    assert mode & 0o077 == 0, oct(mode)


def test_refuses_to_run_in_ci_without_force(tmp_path: Path) -> None:
    env = _make_fake_az(tmp_path)
    env["GITHUB_ACTIONS"] = "true"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0
    assert "Refusing to run in CI" in result.stderr
    # No secret values leaked to stdout/stderr.
    assert "fake-podcaster-key-abc123" not in (result.stdout + result.stderr)


def test_succeeds_without_aca_job(tmp_path: Path) -> None:
    """Script should not crash if no ACA job is found — just warns."""
    env = _make_fake_az(tmp_path)
    az = tmp_path / "bin" / "az"
    az.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'case "$1 $2" in\n'
        '  "account show") echo "{}"; exit 0;;\n'
        '  "storage account") echo "podcasterfakestg"; exit 0;;\n'
        "esac\n"
        "exit 0\n"
    )
    az.chmod(az.stat().st_mode | stat.S_IXUSR)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    # Should succeed (exit 0) even without ACA job — just emits what it can.
    assert result.returncode == 0, result.stderr
