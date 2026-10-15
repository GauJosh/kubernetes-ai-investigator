"""CLI: offline fixture-based investigation, no cluster required anywhere in this test file."""

from __future__ import annotations

import json
from pathlib import Path

from k8s_investigator.cli import main

from .conftest import collect_and_build
from .fixtures import scenarios

_COMMITTED_BUNDLE = (
    Path(__file__).parent / "fixtures" / "bundles" / "r1_dns_failure_bundle.json"
)


def _write_bundle(tmp_path, client) -> str:
    bundle = collect_and_build(client, namespace="demo-ns", name="demo-pod")
    path = tmp_path / "bundle.json"
    path.write_text(bundle.model_dump_json(), encoding="utf-8")
    return str(path)


def test_cli_from_bundle_json_output(tmp_path, capsys):
    bundle_path = _write_bundle(tmp_path, scenarios.r1_dns_failure())
    exit_code = main(
        ["investigate", "pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", bundle_path, "--output", "json"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["target"]["name"] == "demo-pod"
    assert len(report["deterministic_findings"]) == 1
    assert report["deterministic_findings"][0]["produced_by"] == "rule:R1_dns_failure"


def test_cli_from_bundle_text_output_contains_sections(tmp_path, capsys):
    bundle_path = _write_bundle(tmp_path, scenarios.healthy_pod())
    exit_code = main(
        ["investigate", "pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", bundle_path, "--output", "text"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    for section in ("DETERMINISTIC FINDINGS", "OBSERVED FACTS", "EVIDENCE GAPS", "NEXT STEPS"):
        assert section in out


def test_cli_writes_to_output_file(tmp_path):
    bundle_path = _write_bundle(tmp_path, scenarios.r4_log_pattern_match())
    out_path = tmp_path / "report.json"
    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", bundle_path, "--output", "json", "--out", str(out_path),
        ]
    )
    assert exit_code == 0
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["deterministic_findings"][0]["produced_by"] == "rule:R4_log_pattern_match"


def test_cli_missing_bundle_file_reports_error_not_traceback(capsys):
    exit_code = main(
        ["investigate", "pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", "/does/not/exist.json"]
    )
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err


def test_cli_save_bundle_and_from_bundle_combined_is_a_usage_error(tmp_path, capsys):
    """Phase 0.14: --save-bundle has nothing freshly collected to save when --from-bundle is
    also given -- this must be a clear, immediate usage error, never a silent no-op."""
    bundle_path = _write_bundle(tmp_path, scenarios.r1_dns_failure())
    save_path = tmp_path / "should_not_be_written.json"
    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", bundle_path, "--save-bundle", str(save_path),
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "cannot be combined" in err
    assert not save_path.exists()


def test_cli_documented_offline_example_against_the_committed_bundle(capsys):
    """The exact command README.md documents as the offline-usage example, run against the
    real, committed EvidenceBundle fixture (not a bundle built ad hoc in this test) -- proves
    a fresh clone can follow that example verbatim with no cluster, no internal test helper,
    and no undocumented step."""
    assert _COMMITTED_BUNDLE.exists(), f"README's documented fixture is missing: {_COMMITTED_BUNDLE}"

    base_args = [
        "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
        "--from-bundle", str(_COMMITTED_BUNDLE),
    ]

    exit_code = main(base_args + ["--output", "text"])
    assert exit_code == 0
    text_out = capsys.readouterr().out
    assert "DNS resolution failure" in text_out
    assert "rule:R1_dns_failure" in text_out

    exit_code = main(base_args + ["--output", "json"])
    assert exit_code == 0
    json_out = capsys.readouterr().out
    report = json.loads(json_out)  # must remain valid, parseable JSON
    assert len(report["deterministic_findings"]) == 1
    assert report["deterministic_findings"][0]["produced_by"] == "rule:R1_dns_failure"
