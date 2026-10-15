"""export_sanitization: --save-bundle's export-time redaction for ``EvidenceItem``/
``CollectionOutcome`` payloads, including the ``queries`` field (the newly-identified gap: R9's
sibling-Pod list payload is retained raw there, even though ``items`` already projects it down).

All synthetic fixtures. No cluster, no network, no real credential -- the same ``_FAKE_DSN``
convention ``test_ai_redaction.py`` already uses for an identical reason.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from k8s_investigator.canonical import compute_bundle_id, compute_evidence_item_id
from k8s_investigator.errors import BundleExportUnsupportedContradictionsError
from k8s_investigator.export_sanitization import sanitize_bundle_for_export
from k8s_investigator.models.evidence import (
    CollectionOutcome,
    CollectionStatus,
    ContradictionEdge,
    EvidenceBundle,
    EvidenceItem,
    EvidenceStatus,
)
from k8s_investigator.models.refs import ApiCallRef, ObjectRef, TimestampValue
from k8s_investigator.rules.engine import evaluate_bundle
from k8s_investigator.validation import validate_bundle_integrity, validate_findings

from .conftest import collect_and_build
from .fakes import FakeKubernetesClient
from .fixtures import scenarios
from .fixtures.builders import make_deployment, make_pod, make_replica_set, make_service_account

_FAKE_DSN = "postgresql://postgres:postgres@192.168.49.1:5432/devops_genai"


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _target() -> ObjectRef:
    return ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")


def _pod_ref() -> ObjectRef:
    return ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")


def _bundle(*items: EvidenceItem, queries: list[CollectionOutcome] | None = None) -> EvidenceBundle:
    target = _target()
    item_ids = [i.id for i in items]
    bundle_id = compute_bundle_id(
        target.model_dump(mode="json"), item_ids, {"namespace": "ns", "pod_name": "p"}
    )
    return EvidenceBundle(
        id=bundle_id,
        investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        items=list(items),
        queries=list(queries or []),
    )


def _pod_item_with_secret_env() -> EvidenceItem:
    ref = _pod_ref()
    payload = {
        "spec": {
            "containers": [
                {
                    "env": [
                        {"name": "PGVECTOR_DSN", "value": _FAKE_DSN},
                        {"name": "OPENAI_MODEL_DEFAULT", "value": "gpt-4o-mini"},
                    ]
                }
            ]
        },
        "status": {"containerStatuses": [{"state": {"waiting": {"reason": "CrashLoopBackOff"}}}]},
    }
    item_id = compute_evidence_item_id(ref.model_dump(mode="json"), payload)
    return EvidenceItem(id=item_id, source_ref=ref, collected_at=_ts(), payload=payload, status=EvidenceStatus.PRESENT)


def _pod_item_without_secret() -> EvidenceItem:
    ref = _pod_ref()
    payload = {"status": {"containerStatuses": [{"state": {"running": {}}}]}}
    item_id = compute_evidence_item_id(ref.model_dump(mode="json"), payload)
    return EvidenceItem(id=item_id, source_ref=ref, collected_at=_ts(), payload=payload, status=EvidenceStatus.PRESENT)


# --- items[].payload -----------------------------------------------------------------------


def test_sanitize_bundle_for_export_redacts_secret_env_in_items():
    bundle = _bundle(_pod_item_with_secret_env())
    sanitized = sanitize_bundle_for_export(bundle)
    serialized = sanitized.model_dump_json()
    assert _FAKE_DSN not in serialized
    assert "postgres:postgres" not in serialized
    assert "gpt-4o-mini" in serialized  # non-sensitive content preserved
    assert "CrashLoopBackOff" in serialized


def test_sanitize_bundle_for_export_handles_nested_duplicated_values():
    """The exact real-world shape test_ai_redaction.py already covers for redact_payload
    directly -- the same env var duplicated across Pod/ReplicaSet/Deployment-template-shaped
    nesting within ONE EvidenceItem's payload. Proves the export path inherits that coverage."""
    env_entry = {"name": "PGVECTOR_DSN", "value": _FAKE_DSN}
    payload = {
        "pod_spec": {"containers": [{"env": [dict(env_entry)]}]},
        "replicaset_template": {"spec": {"template": {"spec": {"containers": [{"env": [dict(env_entry)]}]}}}},
        "deployment_template": {"spec": {"template": {"spec": {"containers": [{"env": [dict(env_entry)]}]}}}},
    }
    ref = _pod_ref()
    item_id = compute_evidence_item_id(ref.model_dump(mode="json"), payload)
    item = EvidenceItem(id=item_id, source_ref=ref, collected_at=_ts(), payload=payload, status=EvidenceStatus.PRESENT)

    sanitized = sanitize_bundle_for_export(_bundle(item))
    serialized = sanitized.model_dump_json()
    assert _FAKE_DSN not in serialized
    assert "postgres:postgres" not in serialized


# --- queries[].payload (the newly-identified gap: R9's raw, pre-projection sibling-Pod list) --


def test_sanitize_bundle_for_export_redacts_queries_payload_too():
    """CollectionOutcome.payload in bundle.queries holds the RAW, pre-projection payload for
    every call -- unlike items, which bundle_builder._project_sibling_pod_fields already
    strips down to five safe fields for exactly this reason. Confirms the gap exists in the
    unsanitized bundle first, then that sanitize_bundle_for_export closes it."""
    sibling_ref = ApiCallRef(
        verb="list", path="/api/v1/namespaces/demo-ns/pods", params={"labelSelector": "app=demo-pod"}
    )
    raw_sibling_payload = [
        {
            "metadata": {"name": "demo-sibling-1", "namespace": "demo-ns", "labels": {"app": "demo-pod"}},
            "spec": {"nodeName": "worker-0", "containers": [{"env": [{"name": "PGVECTOR_DSN", "value": _FAKE_DSN}]}]},
            "status": {"phase": "Running"},
        }
    ]
    outcome = CollectionOutcome(
        requested=sibling_ref, status=CollectionStatus.PRESENT, payload=raw_sibling_payload, collected_at=datetime.now(timezone.utc)
    )
    bundle = _bundle(_pod_item_without_secret(), queries=[outcome])

    # Sanity check: the gap is real in the raw bundle before any fix is applied.
    assert _FAKE_DSN in bundle.model_dump_json()

    sanitized = sanitize_bundle_for_export(bundle)
    serialized = sanitized.model_dump_json()
    assert _FAKE_DSN not in serialized
    assert "postgres:postgres" not in serialized
    assert "demo-sibling-1" in serialized  # non-sensitive content preserved
    assert "worker-0" in serialized


def test_sanitize_bundle_for_export_redacts_raw_sibling_pod_in_a_real_pipeline_bundle():
    """Same gap, exercised through the real collector/bundle_builder pipeline (not a
    hand-built CollectionOutcome) -- the R9 follow-up's actual sibling-Pod list call."""
    c = FakeKubernetesClient()
    c.pod = make_pod(unscheduled=True)
    c.replica_sets["demo-rs"] = make_replica_set(match_labels={"app": "demo-pod"})
    c.deployments["demo-deploy"] = make_deployment()
    c.service_accounts["default"] = make_service_account()
    c.sibling_pods = [
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": "demo-sibling-1", "namespace": "demo-ns", "uid": "sib-1",
                "labels": {"app": "demo-pod"}, "ownerReferences": [], "resourceVersion": "9",
            },
            "spec": {"nodeName": "worker-0", "containers": [{"env": [{"name": "PGVECTOR_DSN", "value": _FAKE_DSN}]}]},
            "status": {"phase": "Running"},
        }
    ]
    bundle = collect_and_build(c)
    assert _FAKE_DSN in bundle.model_dump_json()  # confirms the real pipeline carries the gap

    sanitized = sanitize_bundle_for_export(bundle)
    assert _FAKE_DSN not in sanitized.model_dump_json()
    validate_bundle_integrity(sanitized)


# --- error_detail ----------------------------------------------------------------------------


def test_sanitize_bundle_for_export_redacts_error_detail_strings():
    ref = ObjectRef(api_group="", api_version="v1", kind="PersistentVolumeClaim", namespace="ns", name="pvc-1")
    item_id = compute_evidence_item_id(ref.model_dump(mode="json"), None)
    item = EvidenceItem(
        id=item_id, source_ref=ref, collected_at=_ts(), payload=None,
        status=EvidenceStatus.UNAVAILABLE,
        error_detail="Forbidden: service rejected request, token=sup3rSecr3t for upstream",
    )
    sanitized = sanitize_bundle_for_export(_bundle(item))
    sanitized_item = sanitized.items[0]
    assert "sup3rSecr3t" not in sanitized_item.error_detail
    assert "token=[REDACTED]" in sanitized_item.error_detail
    # error_detail is not part of content identity (canonical.py / compute_evidence_item_id
    # hashes only source_ref + payload) -- redacting it alone must not change the item's id.
    assert sanitized_item.id == item.id
    validate_bundle_integrity(sanitized)


# --- non-mutation of the original bundle ------------------------------------------------------


def test_sanitize_bundle_for_export_does_not_mutate_the_original_bundle():
    original = _bundle(_pod_item_with_secret_env())
    snapshot = copy.deepcopy(original.model_dump(mode="json"))

    sanitize_bundle_for_export(original)

    assert original.model_dump(mode="json") == snapshot
    assert _FAKE_DSN in original.items[0].payload["spec"]["containers"][0]["env"][0]["value"]


# --- identity / integrity ----------------------------------------------------------------------


def test_sanitize_bundle_for_export_recomputes_ids_when_content_changes():
    original = _bundle(_pod_item_with_secret_env())
    sanitized = sanitize_bundle_for_export(original)

    assert sanitized.items[0].id != original.items[0].id
    assert sanitized.id != original.id
    validate_bundle_integrity(sanitized)  # the file that would actually be written is self-consistent


def test_sanitize_bundle_for_export_preserves_id_when_nothing_to_redact():
    """A pure function of unchanged content: when no detector fires anywhere in the bundle,
    the sanitized bundle (and every item in it) keeps the exact same id as the original."""
    original = _bundle(_pod_item_without_secret())
    sanitized = sanitize_bundle_for_export(original)

    assert sanitized.id == original.id
    assert sanitized.items[0].id == original.items[0].id
    assert sanitized == original


# --- contradictions guard ---------------------------------------------------------------------


def test_sanitize_bundle_for_export_rejects_bundle_with_nonempty_contradictions():
    """A ContradictionEdge references two EvidenceItem ids by value; sanitization can change
    an item's id (it's a content hash of the now-redacted payload), which would leave a
    pre-existing contradiction edge pointing at a stale or nonexistent item. Must be rejected
    outright, never silently rewritten and never silently exported as-is."""
    item_a = _pod_item_without_secret()
    item_b = _pod_item_with_secret_env()
    edge = ContradictionEdge(
        evidence_a=item_a.id,
        evidence_b=item_b.id,
        rule_id="contradiction:synthetic_test",
        description="synthetic contradiction for this test only",
    )
    bundle = _bundle(item_a, item_b).model_copy(update={"contradictions": [edge]})

    with pytest.raises(BundleExportUnsupportedContradictionsError):
        sanitize_bundle_for_export(bundle)


# --- diagnostic equivalence across the current rule bank --------------------------------------


def test_sanitize_bundle_for_export_preserves_diagnostic_equivalence_for_current_rule_bank():
    """Evidence ids are content hashes (canonical.py): an item whose payload changes --
    including in a field no rule reads, like an added env var -- legitimately gets a new id in
    the sanitized bundle. A Finding's evidence_locators are therefore bundle-relative, not
    bundle-independent: re-running the rule engine against the sanitized bundle produces fresh
    locators pointing at the sanitized bundle's own (new) item ids. That is correct, not a
    defect -- validate_finding_references is designed to check a finding against the bundle it
    came from, never across two different bundles.

    What must hold, and is checked here directly rather than assumed:
    1. Each finding set validates cleanly against its OWN bundle (validate_findings).
    2. The actual diagnostic content -- which rule fired, what it said, and what text it
       quoted -- is identical between raw and sanitized replay, for a scenario whose Pod
       carries a credential-shaped env var alongside its real diagnostic signal.

    No rule in this project's current bank (R1-R11) reads `env` at all (confirmed by inspection
    of src/k8s_investigator/rules/), so this equivalence is expected; this test is the standing
    guard that will fail loudly the day a future rule's input happens to overlap a redacted
    field, rather than silently shipping a diverging offline replay.
    """
    client = scenarios.r1_dns_failure()
    client.pod = make_pod(waiting_reason="ErrImagePull", env=[{"name": "PGVECTOR_DSN", "value": _FAKE_DSN}])
    raw_bundle = collect_and_build(client)
    assert _FAKE_DSN in raw_bundle.model_dump_json()

    sanitized_bundle = sanitize_bundle_for_export(raw_bundle)
    assert _FAKE_DSN not in sanitized_bundle.model_dump_json()
    validate_bundle_integrity(sanitized_bundle)

    raw_findings, raw_contradictions = evaluate_bundle(raw_bundle)
    san_findings, san_contradictions = evaluate_bundle(sanitized_bundle)

    validate_findings(raw_findings, raw_bundle)
    validate_findings(san_findings, sanitized_bundle)

    def _comparable(findings):
        return [
            (f.produced_by, f.kind, f.statement, tuple(loc.quoted_text for loc in f.evidence_locators))
            for f in findings
        ]

    assert _comparable(raw_findings) == _comparable(san_findings)
    assert len(raw_contradictions) == len(san_contradictions) == 0
    assert any(f.produced_by == "rule:R1_dns_failure" for f in raw_findings)
