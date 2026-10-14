"""CLI: offline fixture-based investigation, no cluster required anywhere in this test file."""

from __future__ import annotations

import json

from k8s_investigator.cli import main

from .conftest import collect_and_build
from .fixtures import scenarios


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
