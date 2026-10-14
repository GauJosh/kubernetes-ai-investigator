"""Canonical serialization and content-identity hashing -- correction 3 of revision 2."""

from __future__ import annotations

from k8s_investigator.canonical import canonical_bytes, compute_bundle_id, compute_evidence_item_id, content_hash


def test_key_order_does_not_affect_hash():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert content_hash(a) == content_hash(b)


def test_none_valued_keys_are_omitted_consistently():
    """A field that was never set and a field explicitly cleared must canonicalize identically."""
    present_as_none = {"name": "x", "resourceVersion": None, "optional": None}
    never_set = {"name": "x"}
    assert content_hash(present_as_none) == content_hash(never_set)


def test_resource_version_excluded_from_hash():
    """docs/phase-0.8-mvp-blueprint.md §D.1: resourceVersion must never participate in content
    identity -- it is a whole-object counter that can change for reasons this project does not
    collect or care about."""
    a = {"metadata": {"name": "pod-1", "resourceVersion": "100"}}
    b = {"metadata": {"name": "pod-1", "resourceVersion": "999999"}}
    assert content_hash(a) == content_hash(b)


def test_managed_fields_excluded_from_hash():
    a = {"metadata": {"name": "pod-1", "managedFields": [{"manager": "kubectl"}]}}
    b = {"metadata": {"name": "pod-1"}}
    assert content_hash(a) == content_hash(b)


def test_genuinely_different_payloads_hash_differently():
    a = {"status": {"phase": "Running"}}
    b = {"status": {"phase": "Pending"}}
    assert content_hash(a) != content_hash(b)


def test_evidence_item_id_excludes_collected_at():
    """Two independent 'collections' of the identical object, at different wall-clock times,
    must produce the same content identity -- collected_at is provenance, never a function
    argument to compute_evidence_item_id at all, so two calls with the same source_ref/payload
    always match regardless of when each was actually computed."""
    source_ref = {
        "ref_type": "object", "api_group": "", "api_version": "v1",
        "kind": "Pod", "namespace": "ns", "name": "p",
    }
    payload = {"status": {"phase": "Running"}}

    id_at_time_1 = compute_evidence_item_id(source_ref, payload)
    id_at_time_2 = compute_evidence_item_id(source_ref, payload)  # simulates a second, later collection
    assert id_at_time_1 == id_at_time_2


def test_identical_source_and_payload_same_id():
    source_ref = {"ref_type": "object", "api_group": "", "api_version": "v1", "kind": "Pod", "namespace": "ns", "name": "p"}
    payload = {"status": {"phase": "Running"}}
    assert compute_evidence_item_id(source_ref, payload) == compute_evidence_item_id(dict(source_ref), dict(payload))


def test_bundle_id_deterministic_for_same_inputs():
    target = {"ref_type": "object", "api_group": "", "api_version": "v1", "kind": "Pod", "namespace": "ns", "name": "p"}
    ids = ["b", "a", "c"]
    params = {"namespace": "ns", "pod_name": "p"}
    assert compute_bundle_id(target, ids, params) == compute_bundle_id(target, list(reversed(ids)), params)


def test_canonical_bytes_has_no_insignificant_whitespace():
    data = canonical_bytes({"a": 1, "b": [1, 2]})
    assert b" " not in data
    assert b"\n" not in data
