"""integrations/kubectl/kubectl-kai -- the kubectl-plugin integration.

This is a thin, standalone shell script, not part of the `k8s_investigator` package, so it is
tested the same way a user actually runs it: as a real subprocess, via `bash`, with the already-
installed `kai` console script on PATH. No live cluster, no AI provider -- every invocation
below runs against the committed, synthetic, credential-free example bundle via `--from-bundle`.

Skipped entirely (not failed) on a machine with no `bash` on PATH, since the plugin itself
documents that it requires a POSIX shell (WSL/Git Bash on Windows) -- consistent with how this
project already treats "requires an external binary" as something to document, not silently
assume.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_PLUGIN_SCRIPT = _REPO_ROOT / "integrations" / "kubectl" / "kubectl-kai"
_COMMITTED_BUNDLE = _REPO_ROOT / "tests" / "fixtures" / "bundles" / "r1_dns_failure_bundle.json"

_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(_BASH is None, reason="kubectl-kai requires a POSIX shell (bash)")


def _venv_script_dir() -> Path:
    """The directory holding the currently-running interpreter's own console scripts (where
    the `kai` entry point this test environment installed actually lives) -- `Scripts` on
    Windows, `bin` elsewhere, mirroring how this project's own CLI is actually installed."""
    return Path(sys.executable).parent


def _run_plugin(args: list[str], *, path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [_BASH, str(_PLUGIN_SCRIPT), *args],
        capture_output=True, text=True, env=env,
    )


def _run_kai_directly(args: list[str]) -> subprocess.CompletedProcess:
    kai_path = _venv_script_dir() / ("kai.exe" if os.name == "nt" else "kai")
    return subprocess.run([str(kai_path), *args], capture_output=True, text=True)


def test_plugin_script_exists_with_a_posix_shebang():
    assert _PLUGIN_SCRIPT.exists()
    first_line = _PLUGIN_SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!")
    assert "bash" in first_line


def _without_nondeterministic_fields(report_json: str) -> dict:
    """Strip the two fields that are legitimately different on every single invocation
    (a fresh UUID and a fresh timestamp) -- everything else must match byte-for-byte."""
    report = json.loads(report_json)
    report.pop("investigation_id", None)
    report.pop("generated_at", None)
    return report


def test_plugin_forwards_arguments_and_produces_identical_output_to_kai_directly():
    """The core correctness property: the plugin must change nothing about kai's own output --
    it only makes kubectl able to find and launch it."""
    args = [
        "pod", "demo-pod", "-n", "demo-ns",
        "--from-bundle", str(_COMMITTED_BUNDLE), "--output", "json",
    ]
    venv_scripts = str(_venv_script_dir())
    plugin_result = _run_plugin(args, path=venv_scripts + os.pathsep + os.environ.get("PATH", ""))
    direct_result = _run_kai_directly(args)

    assert plugin_result.returncode == 0
    assert plugin_result.returncode == direct_result.returncode
    assert _without_nondeterministic_fields(plugin_result.stdout) == _without_nondeterministic_fields(
        direct_result.stdout
    )


def test_plugin_forwards_namespace_and_pod_name_correctly():
    args = [
        "pod", "demo-pod", "-n", "demo-ns",
        "--from-bundle", str(_COMMITTED_BUNDLE), "--output", "json",
    ]
    venv_scripts = str(_venv_script_dir())
    result = _run_plugin(args, path=venv_scripts + os.pathsep + os.environ.get("PATH", ""))
    out = json.loads(result.stdout)
    assert out["target"]["namespace"] == "demo-ns"
    assert out["target"]["name"] == "demo-pod"
    assert out["deterministic_findings"][0]["produced_by"] == "rule:R1_dns_failure"


def test_plugin_does_not_invoke_ai_unless_explicitly_requested():
    args = [
        "pod", "demo-pod", "-n", "demo-ns",
        "--from-bundle", str(_COMMITTED_BUNDLE), "--output", "json",
    ]
    venv_scripts = str(_venv_script_dir())
    result = _run_plugin(args, path=venv_scripts + os.pathsep + os.environ.get("PATH", ""))
    out = json.loads(result.stdout)
    assert out["ai_hypothesis_status"] == "NOT_ATTEMPTED"
    assert out["ai_hypotheses"] == []


def test_plugin_reports_a_clear_actionable_error_when_kai_is_not_on_path():
    """A minimal PATH with no kai/investigate anywhere on it -- the plugin must fail loudly
    with a specific, actionable message, never a bare 'command not found' traceback."""
    # A directory containing no executables at all, guaranteed not to resolve kai/investigate.
    empty_dir = str(_REPO_ROOT / "integrations")  # no kai/investigate binaries live here
    result = _run_plugin(["pod", "demo-pod", "-n", "demo-ns"], path=empty_dir)

    assert result.returncode != 0
    assert "kai" in result.stderr
    assert "not installed" in result.stderr or "not on PATH" in result.stderr


def test_plugin_preserves_existing_offline_cli_behavior_end_to_end():
    """The documented end-to-end example from the plugin's own usage comment, run for real."""
    venv_scripts = str(_venv_script_dir())
    result = _run_plugin(
        ["pod", "demo-pod", "-n", "demo-ns", "--from-bundle", str(_COMMITTED_BUNDLE)],
        path=venv_scripts + os.pathsep + os.environ.get("PATH", ""),
    )
    assert result.returncode == 0
    assert "DETERMINISTIC FINDINGS" in result.stdout
    assert "rule:R1_dns_failure" in result.stdout


@pytest.mark.parametrize("ai_flag", ["--with-ai", "--with-ai-hypotheses"])
def test_plugin_forwards_the_ai_flag_unchanged_under_either_spelling(ai_flag):
    """Phase 0.17: the plugin does exactly one thing -- exec kai "$@" -- so it never needs to
    know about a flag rename; both the new preferred spelling and the deprecated alias must
    reach kai exactly as typed. No AI provider is configured here, so this only needs to prove
    the flag was forwarded (an honest FAILED status), never that it succeeded."""
    venv_scripts = str(_venv_script_dir())
    env_path = venv_scripts + os.pathsep + os.environ.get("PATH", "")
    env = dict(os.environ)
    env["PATH"] = env_path
    env.pop("AI_HYPOTHESIS_PROVIDER", None)

    result = subprocess.run(
        [_BASH, str(_PLUGIN_SCRIPT), "pod", "demo-pod", "-n", "demo-ns",
         "--from-bundle", str(_COMMITTED_BUNDLE), "--output", "json", ai_flag],
        capture_output=True, text=True, env=env,
    )
    out = json.loads(result.stdout)
    assert result.returncode == 0
    assert out["ai_hypothesis_status"] == "FAILED"  # proves the flag reached kai, not ignored
    assert out["deterministic_findings"][0]["produced_by"] == "rule:R1_dns_failure"
