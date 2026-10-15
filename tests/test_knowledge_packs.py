"""Phase 0.18: custom knowledge packs -- team-defined YAML rules and Markdown runbooks,
evaluated deterministically against already-collected evidence, entirely offline, with AI
fully disabled and no OpenAI dependency involved anywhere in this file.

All synthetic fixtures and on-disk scratch packs built in ``tmp_path`` -- no real cluster, no
network, no AI provider.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from k8s_investigator.cli import main
from k8s_investigator.knowledge.loader import KnowledgePackError, load_knowledge_pack
from k8s_investigator.knowledge.matcher import evaluate_knowledge_pack
from k8s_investigator.knowledge.models import KnowledgePack
from k8s_investigator.models.findings import FindingKind
from k8s_investigator.report_builder import build_report
from k8s_investigator.rules.engine import evaluate_bundle

from .conftest import collect_and_build
from .fixtures import scenarios

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PACK_PATH = str(REPO_ROOT / ".kai" / "knowledge.yaml")


def _postgres_bundle():
    return collect_and_build(
        scenarios.custom_knowledge_postgres_connection_refused(), namespace="demo-ns", name="demo-pod"
    )


def _configmap_bundle():
    return collect_and_build(
        scenarios.unfamiliar_failure_no_matching_rule(), namespace="demo-ns", name="demo-pod"
    )


def _write_pack(tmp_path, data: dict, name: str = "knowledge.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _minimal_rule(rule_id="r1", **match_kwargs):
    return {
        "id": rule_id,
        "description": "test rule",
        "match": match_kwargs,
        "guidance": {"summary": "test summary", "next_checks": ["do a thing"]},
    }


# --- The repository's own example pack ---------------------------------------------------


def test_example_pack_in_repo_loads_and_matches_a_synthetic_connection_refused_failure():
    pack, pack_dir = load_knowledge_pack(EXAMPLE_PACK_PATH)
    assert pack.version == 1
    bundle = _postgres_bundle()
    matches = evaluate_knowledge_pack(pack, bundle)

    ids = [m.finding.produced_by for m in matches]
    assert "knowledge:internal-postgres-connectivity" in ids
    match = next(m for m in matches if m.finding.produced_by == "knowledge:internal-postgres-connectivity")
    assert match.finding.kind == FindingKind.CUSTOM_GUIDANCE
    assert match.runbook == "runbooks/database-connectivity.md"
    assert match.next_checks


def test_example_pack_second_rule_matches_the_configmap_scenario_via_container_waiting_reason():
    pack, _ = load_knowledge_pack(EXAMPLE_PACK_PATH)
    bundle = _configmap_bundle()
    matches = evaluate_knowledge_pack(pack, bundle)
    ids = [m.finding.produced_by for m in matches]
    assert "knowledge:missing-configmap-or-secret-reference" in ids


# --- A match contributes guidance without altering built-in findings ---------------------


def test_matching_custom_rule_does_not_change_deterministic_findings_or_observed_facts():
    bundle = _postgres_bundle()
    findings, contradictions = evaluate_bundle(bundle)
    deterministic_before = list(findings)

    pack, _ = load_knowledge_pack(EXAMPLE_PACK_PATH)
    custom_guidance = evaluate_knowledge_pack(pack, bundle)
    assert custom_guidance  # sanity: this scenario is designed to match

    report_without = build_report(bundle, findings, contradictions)
    report_with = build_report(
        bundle, findings, contradictions, custom_guidance=custom_guidance, knowledge_pack_source=EXAMPLE_PACK_PATH
    )

    assert report_with.deterministic_findings == report_without.deterministic_findings
    assert report_with.observed_facts == report_without.observed_facts
    assert report_with.next_steps == report_without.next_steps
    assert findings == deterministic_before  # evaluate_bundle's own output is untouched
    assert report_with.custom_guidance and not report_without.custom_guidance


def test_custom_guidance_match_is_never_a_deterministic_finding_and_carries_no_confidence():
    bundle = _postgres_bundle()
    pack, _ = load_knowledge_pack(EXAMPLE_PACK_PATH)
    [match] = [
        m for m in evaluate_knowledge_pack(pack, bundle)
        if m.finding.produced_by == "knowledge:internal-postgres-connectivity"
    ]
    assert match.finding.kind == FindingKind.CUSTOM_GUIDANCE
    assert match.finding.kind != FindingKind.DETERMINISTIC_FINDING
    assert match.finding.confidence is None


# --- No-match behavior ---------------------------------------------------------------------


def test_no_match_produces_empty_custom_guidance_not_an_error():
    bundle = collect_and_build(scenarios.healthy_pod(), namespace="demo-ns", name="demo-pod")
    pack, _ = load_knowledge_pack(EXAMPLE_PACK_PATH)
    matches = evaluate_knowledge_pack(pack, bundle)
    assert matches == []


def test_cli_text_output_reports_no_match_without_noise(tmp_path, capsys):
    bundle = collect_and_build(scenarios.healthy_pod(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code = main(
        ["pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", str(bundle_path),
         "--knowledge", EXAMPLE_PACK_PATH]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "CUSTOM KNOWLEDGE GUIDANCE" in out
    assert "none -- no custom rule" in out


# --- Multiple matching custom rules ---------------------------------------------------------


def test_multiple_rules_in_one_pack_can_each_match_independently(tmp_path):
    pack_path = _write_pack(
        tmp_path,
        {
            "version": 1,
            "rules": [
                _minimal_rule("r-postgres", evidence_contains=["connection refused"]),
                _minimal_rule("r-crash", event_reason="BackOff"),
            ],
        },
    )
    pack, _ = load_knowledge_pack(pack_path)

    bundle = _postgres_bundle()
    matches = evaluate_knowledge_pack(pack, bundle)
    ids = {m.finding.produced_by for m in matches}
    assert "knowledge:r-postgres" in ids
    # r-crash may or may not fire depending on whether a BackOff event exists in this fixture;
    # the point of this test is that both rules are independently evaluated against the same
    # bundle without one affecting the other's outcome.
    assert len(matches) == len(set(ids))  # no duplicate findings for the same rule


# --- A runbook is resolved and displayed correctly -------------------------------------------


def test_runbook_path_is_resolved_relative_to_pack_directory_and_shown_in_rendering(tmp_path, capsys):
    runbook_dir = tmp_path / "runbooks"
    runbook_dir.mkdir()
    (runbook_dir / "db.md").write_text("# runbook\n", encoding="utf-8")
    pack_path = _write_pack(
        tmp_path,
        {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "description": "test",
                    "match": {"evidence_contains": ["connection refused"]},
                    "guidance": {
                        "summary": "s",
                        "next_checks": ["check it"],
                        "runbook": "runbooks/db.md",
                    },
                }
            ],
        },
    )

    bundle = _postgres_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code = main(
        ["pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", str(bundle_path),
         "--knowledge", str(pack_path)]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "runbook: runbooks/db.md" in out


# --- Missing / malformed / unsupported knowledge-pack configuration --------------------------


def test_missing_pack_file_raises_actionable_error(tmp_path):
    with pytest.raises(KnowledgePackError, match="not found"):
        load_knowledge_pack(tmp_path / "does-not-exist.yaml")


def test_unsupported_schema_version_is_rejected(tmp_path):
    pack_path = _write_pack(tmp_path, {"version": 999, "rules": []})
    with pytest.raises(KnowledgePackError, match="unsupported knowledge-pack schema version"):
        load_knowledge_pack(pack_path)


def test_unsupported_match_operator_is_rejected_not_silently_ignored(tmp_path):
    pack_path = _write_pack(
        tmp_path,
        {"version": 1, "rules": [_minimal_rule(not_a_real_operator="x")]},
    )
    with pytest.raises(KnowledgePackError, match="extra_forbidden|not permitted"):
        load_knowledge_pack(pack_path)


def test_empty_match_block_is_rejected():
    with pytest.raises(ValidationError, match="at least one operator"):
        KnowledgePack.model_validate({"version": 1, "rules": [_minimal_rule()]})


def test_duplicate_rule_ids_are_rejected(tmp_path):
    pack_path = _write_pack(
        tmp_path,
        {
            "version": 1,
            "rules": [
                _minimal_rule("dup", event_reason="BackOff"),
                _minimal_rule("dup", event_reason="Unhealthy"),
            ],
        },
    )
    with pytest.raises(KnowledgePackError, match="duplicate rule id"):
        load_knowledge_pack(pack_path)


def test_non_mapping_top_level_is_rejected(tmp_path):
    pack_path = tmp_path / "list.yaml"
    pack_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(KnowledgePackError, match="mapping"):
        load_knowledge_pack(pack_path)


def test_invalid_yaml_syntax_is_rejected(tmp_path):
    pack_path = tmp_path / "broken.yaml"
    pack_path.write_text("version: 1\nrules: [this is not: valid: yaml:\n", encoding="utf-8")
    with pytest.raises(KnowledgePackError, match="not valid YAML"):
        load_knowledge_pack(pack_path)


def test_oversized_pack_file_is_rejected(tmp_path, monkeypatch):
    import k8s_investigator.knowledge.loader as loader_module

    monkeypatch.setattr(loader_module, "MAX_PACK_FILE_BYTES", 10)
    pack_path = _write_pack(tmp_path, {"version": 1, "rules": []})
    with pytest.raises(KnowledgePackError, match="exceeding"):
        load_knowledge_pack(pack_path)


def test_cli_reports_invalid_pack_as_actionable_error_with_nonzero_exit(tmp_path, capsys):
    pack_path = _write_pack(tmp_path, {"version": 1, "rules": [_minimal_rule(bogus="x")]})
    bundle = _postgres_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code = main(
        ["pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", str(bundle_path),
         "--knowledge", str(pack_path)]
    )
    assert exit_code == 5
    err = capsys.readouterr().err
    assert "knowledge pack" in err


# --- Invalid / escaping runbook paths ---------------------------------------------------------


def test_absolute_runbook_path_is_rejected(tmp_path):
    pack_path = _write_pack(
        tmp_path,
        {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "description": "test",
                    "match": {"event_reason": "BackOff"},
                    "guidance": {"summary": "s", "runbook": "/etc/passwd"},
                }
            ],
        },
    )
    with pytest.raises(KnowledgePackError, match="absolute"):
        load_knowledge_pack(pack_path)


def test_traversal_runbook_path_is_rejected(tmp_path, tmp_path_factory):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    pack_path = _write_pack(
        pack_dir,
        {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "description": "test",
                    "match": {"event_reason": "BackOff"},
                    "guidance": {"summary": "s", "runbook": "../outside.md"},
                }
            ],
        },
    )
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    with pytest.raises(KnowledgePackError, match="outside"):
        load_knowledge_pack(pack_path)


def test_symlink_escaping_runbook_path_is_rejected(tmp_path, tmp_path_factory):
    outside_dir = tmp_path_factory.mktemp("knowledge_outside")
    outside_file = outside_dir / "secret.md"
    outside_file.write_text("x", encoding="utf-8")

    runbook_dir = tmp_path / "runbooks"
    runbook_dir.mkdir()
    link = runbook_dir / "escape.md"
    try:
        link.symlink_to(outside_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted in this environment")

    pack_path = _write_pack(
        tmp_path,
        {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "description": "test",
                    "match": {"event_reason": "BackOff"},
                    "guidance": {"summary": "s", "runbook": "runbooks/escape.md"},
                }
            ],
        },
    )
    with pytest.raises(KnowledgePackError, match="outside"):
        load_knowledge_pack(pack_path)


def test_missing_runbook_file_is_a_hard_error_not_a_silent_omission(tmp_path):
    pack_path = _write_pack(
        tmp_path,
        {
            "version": 1,
            "rules": [
                {
                    "id": "r1",
                    "description": "test",
                    "match": {"event_reason": "BackOff"},
                    "guidance": {"summary": "s", "runbook": "runbooks/does-not-exist.md"},
                }
            ],
        },
    )
    with pytest.raises(KnowledgePackError, match="does not exist"):
        load_knowledge_pack(pack_path)


# --- Deterministic output for identical inputs -------------------------------------------------


def test_identical_pack_and_bundle_produce_byte_identical_custom_guidance_twice():
    pack, _ = load_knowledge_pack(EXAMPLE_PACK_PATH)
    bundle = _postgres_bundle()

    matches_1 = evaluate_knowledge_pack(pack, bundle)
    matches_2 = evaluate_knowledge_pack(pack, bundle)

    assert [m.model_dump() for m in matches_1] == [m.model_dump() for m in matches_2]


def test_cli_json_output_is_byte_identical_across_two_runs_with_knowledge(tmp_path, capsys):
    bundle = _postgres_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")
    args = [
        "pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", str(bundle_path),
        "--knowledge", EXAMPLE_PACK_PATH, "--output", "json",
    ]

    main(args)
    out_1 = capsys.readouterr().out
    report_1 = json.loads(out_1)

    main(args)
    out_2 = capsys.readouterr().out
    report_2 = json.loads(out_2)

    # investigation_id/generated_at are expected to differ run-to-run; everything else,
    # custom_guidance included, must not.
    for key in ("custom_guidance", "knowledge_pack_source", "deterministic_findings", "observed_facts"):
        assert report_1[key] == report_2[key]


# --- Offline bundle replay with custom knowledge ------------------------------------------------


def test_offline_replay_combined_with_knowledge_flag_works_end_to_end(tmp_path, capsys):
    bundle = _postgres_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code = main(
        ["pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", str(bundle_path),
         "--knowledge", EXAMPLE_PACK_PATH, "--output", "json"]
    )
    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["knowledge_pack_source"] == EXAMPLE_PACK_PATH
    assert any(
        m["finding"]["produced_by"] == "knowledge:internal-postgres-connectivity"
        for m in report["custom_guidance"]
    )


# --- Existing report/CLI behavior unchanged when no knowledge pack is supplied -----------------


def test_cli_output_unchanged_when_knowledge_flag_omitted(tmp_path, capsys):
    bundle = _postgres_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    main(["pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", str(bundle_path), "--output", "json"])
    report = json.loads(capsys.readouterr().out)
    assert report["custom_guidance"] == []
    assert report["knowledge_pack_source"] is None
