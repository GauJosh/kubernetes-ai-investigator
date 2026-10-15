"""CLI: offline fixture-based investigation, no cluster required anywhere in this test file."""

from __future__ import annotations

import json
from pathlib import Path

from k8s_investigator.cli import main
from k8s_investigator.models.evidence import EvidenceBundle
from k8s_investigator.validation import validate_bundle_integrity

from .conftest import collect_and_build
from .fakes import FakeKubernetesClient
from .fixtures import scenarios
from .fixtures.builders import make_pod

_COMMITTED_BUNDLE = (
    Path(__file__).parent / "fixtures" / "bundles" / "r1_dns_failure_bundle.json"
)

_FAKE_DSN = "postgresql://postgres:postgres@192.168.49.1:5432/devops_genai"


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


def _client_with_secret_env() -> FakeKubernetesClient:
    client = scenarios.r1_dns_failure()
    client.pod = make_pod(waiting_reason="ErrImagePull", env=[{"name": "PGVECTOR_DSN", "value": _FAKE_DSN}])
    return client


def test_cli_save_bundle_default_sanitizes_known_credential(tmp_path, monkeypatch):
    """Phase 0.16: --save-bundle must write a sanitized bundle by default -- the exact
    live-cluster finding (a plaintext connection string in a Pod's own env var) must not
    reach disk unless the raw export is explicitly requested."""
    raw_bundle = collect_and_build(_client_with_secret_env())
    monkeypatch.setattr("k8s_investigator.cli._load_or_collect_bundle", lambda args: raw_bundle)

    save_path = tmp_path / "sanitized.json"
    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--save-bundle", str(save_path), "--output", "json",
        ]
    )
    assert exit_code == 0
    saved_text = save_path.read_text(encoding="utf-8")
    assert _FAKE_DSN not in saved_text
    assert "postgres:postgres" not in saved_text

    reloaded = EvidenceBundle.model_validate_json(saved_text)
    validate_bundle_integrity(reloaded)  # the saved file is a genuinely valid, reloadable bundle


def test_cli_save_bundle_raw_opt_in_writes_unsanitized_and_warns(tmp_path, monkeypatch, capsys):
    raw_bundle = collect_and_build(_client_with_secret_env())
    monkeypatch.setattr("k8s_investigator.cli._load_or_collect_bundle", lambda args: raw_bundle)

    save_path = tmp_path / "raw.json"
    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--save-bundle", str(save_path), "--allow-unsanitized-bundle-export", "--output", "json",
        ]
    )
    assert exit_code == 0
    saved_text = save_path.read_text(encoding="utf-8")
    assert _FAKE_DSN in saved_text  # raw, exactly as requested

    err = capsys.readouterr().err
    assert "warning:" in err
    assert "UNSANITIZED" in err
    assert _FAKE_DSN not in err  # the warning itself never reproduces the secret


def test_cli_allow_unsanitized_bundle_export_without_save_bundle_is_a_usage_error(tmp_path, capsys):
    bundle_path = _write_bundle(tmp_path, scenarios.r1_dns_failure())
    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", bundle_path, "--allow-unsanitized-bundle-export",
        ]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "only valid together with --save-bundle" in err


def test_cli_save_bundle_sanitization_failure_never_writes_a_raw_fallback(tmp_path, monkeypatch, capsys):
    """If sanitization/integrity-validation fails, nothing is written -- never a silent raw
    fallback -- and the reported error never includes the underlying exception's own message
    (which could, in principle, echo back a fragment of the payload)."""
    raw_bundle = collect_and_build(_client_with_secret_env())
    monkeypatch.setattr("k8s_investigator.cli._load_or_collect_bundle", lambda args: raw_bundle)
    monkeypatch.setattr(
        "k8s_investigator.cli.sanitize_bundle_for_export",
        lambda bundle: (_ for _ in ()).throw(RuntimeError(f"simulated failure leaking {_FAKE_DSN}")),
    )

    save_path = tmp_path / "should_not_exist.json"
    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--save-bundle", str(save_path), "--output", "json",
        ]
    )
    assert exit_code == 4
    assert not save_path.exists()

    err = capsys.readouterr().err
    assert "refusing to write a raw fallback" in err
    assert _FAKE_DSN not in err
    assert "simulated failure" not in err  # only the exception's type name is ever reported


def test_cli_save_bundle_sanitized_write_failure_is_reported_safely_and_distinctly(
    tmp_path, monkeypatch, capsys
):
    """A genuine OS-level write failure (not a sanitization failure) in the default, sanitized
    export path -- the error must describe a *write* failure, not claim sanitization failed."""
    raw_bundle = collect_and_build(_client_with_secret_env())
    monkeypatch.setattr("k8s_investigator.cli._load_or_collect_bundle", lambda args: raw_bundle)

    # tmp_path is a directory, not a file -- writing text to it is a genuine, portable failure.
    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--save-bundle", str(tmp_path), "--output", "json",
        ]
    )
    assert exit_code == 4
    err = capsys.readouterr().err
    assert "error:" in err
    assert "failed to write the sanitized evidence bundle to disk" in err
    assert "refusing to write a raw fallback" not in err  # that phrase is sanitization-specific
    assert _FAKE_DSN not in err
    assert "postgres:postgres" not in err


def test_cli_save_bundle_raw_write_failure_is_reported_safely_and_distinctly(
    tmp_path, monkeypatch, capsys
):
    """Same genuine write failure, but with --allow-unsanitized-bundle-export -- the error must
    accurately describe a RAW bundle write failure, never a sanitization failure, and never
    claim a raw fallback was refused (raw export was exactly what was requested)."""
    raw_bundle = collect_and_build(_client_with_secret_env())
    monkeypatch.setattr("k8s_investigator.cli._load_or_collect_bundle", lambda args: raw_bundle)

    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--save-bundle", str(tmp_path), "--allow-unsanitized-bundle-export",
            "--output", "json",
        ]
    )
    assert exit_code == 4
    err = capsys.readouterr().err
    assert "error:" in err
    assert "failed to write the raw (unsanitized) evidence bundle to disk" in err
    assert "failed to sanitize" not in err.lower()  # must never be reported as a sanitization failure
    assert "refusing to write a raw fallback" not in err  # does not apply -- raw was requested
    assert _FAKE_DSN not in err
    assert "postgres:postgres" not in err


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
