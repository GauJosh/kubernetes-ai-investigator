"""Phase 0.12 (live-cluster follow-up): ai.redaction -- credentials must never appear in the
model-input projection, duplicated or not, while ordinary diagnostic content survives untouched.

All synthetic fixtures. No cluster, no network, no real credential.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from k8s_investigator.ai.model_input import build_model_input
from k8s_investigator.ai.redaction import redact_payload, redact_string_value
from k8s_investigator.canonical import content_hash
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.refs import ApiCallRef, ObjectRef, TimestampValue

_FAKE_DSN = "postgresql://postgres:postgres@192.168.49.1:5432/devops_genai"


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


# --- redact_string_value: value-pattern detection, independent of any field name --------------


def test_redact_string_value_redacts_url_userinfo_credentials():
    text = f"connecting with dsn={_FAKE_DSN} now"
    redacted, categories = redact_string_value(text)
    assert "postgres:postgres" not in redacted
    assert "192.168.49.1:5432/devops_genai" in redacted  # host/path preserved
    assert "credential_url" in categories


def test_redact_string_value_redacts_keyword_assignment_in_free_text():
    text = "startup failed: password=sup3rSecr3t while connecting"
    redacted, categories = redact_string_value(text)
    assert "sup3rSecr3t" not in redacted
    assert "password=[REDACTED]" in redacted
    assert "credential_assignment" in categories


def test_redact_string_value_leaves_ordinary_diagnostic_text_untouched():
    text = (
        'psycopg.OperationalError: connection failed: connection to server at '
        '"192.168.49.1", port 5432 failed: Connection refused'
    )
    redacted, categories = redact_string_value(text)
    assert redacted == text
    assert categories == set()


def test_redact_string_value_leaves_ordinary_non_credential_url_untouched():
    """A plain internal service URL with no embedded userinfo is not a credential and must
    survive -- over-redaction would turn useful, harmless context into apparent absence."""
    text = "INFERENCE_ROUTER_URL=http://inference-router:8000"
    redacted, categories = redact_string_value(text)
    assert redacted == text
    assert categories == set()


# --- redact_payload: name-based detection, and "not exclusively names" ------------------------


def test_redact_payload_redacts_env_entry_by_sensitive_name_regardless_of_value_shape():
    payload = {"spec": {"containers": [{"env": [{"name": "DB_PASSWORD", "value": "not-url-shaped-at-all"}]}]}}
    redacted, categories = redact_payload(payload)
    value = redacted["spec"]["containers"][0]["env"][0]["value"]
    assert "not-url-shaped-at-all" not in value
    assert "sensitive_env_name" in categories


def test_redact_payload_redacts_credential_shaped_value_even_with_a_generic_name():
    """Proves detection is NOT exclusively name-based: a value matching a credential pattern
    is redacted even when its field name gives no hint at all."""
    payload = {"spec": {"containers": [{"env": [{"name": "SOME_SETTING", "value": _FAKE_DSN}]}]}}
    redacted, categories = redact_payload(payload)
    value = redacted["spec"]["containers"][0]["env"][0]["value"]
    assert "postgres:postgres" not in value
    assert "credential_url" in categories
    assert "sensitive_env_name" not in categories  # the name itself didn't match


def test_redact_payload_leaves_non_sensitive_env_entries_completely_unchanged():
    payload = {"spec": {"containers": [{"env": [
        {"name": "OPENAI_MODEL_DEFAULT", "value": "gpt-4o-mini"},
        {"name": "INFERENCE_ROUTER_URL", "value": "http://inference-router:8000"},
    ]}]}}
    redacted, categories = redact_payload(payload)
    assert redacted == payload
    assert categories == set()


def test_redact_payload_redacts_duplicates_across_pod_replicaset_deployment_shaped_nesting():
    """The exact real-world case this follow-up was triggered by: the same env var duplicated
    in a Pod's own spec and in its owning ReplicaSet's/Deployment's pod template."""
    env_entry = {"name": "PGVECTOR_DSN", "value": _FAKE_DSN}
    payload = {
        "pod_spec": {"containers": [{"env": [dict(env_entry)]}]},
        "replicaset_template": {"spec": {"template": {"spec": {"containers": [{"env": [dict(env_entry)]}]}}}},
        "deployment_template": {"spec": {"template": {"spec": {"containers": [{"env": [dict(env_entry)]}]}}}},
    }
    redacted, categories = redact_payload(payload)
    full_text = str(redacted)
    assert "postgres:postgres" not in full_text
    assert _FAKE_DSN not in full_text
    assert "sensitive_env_name" in categories


def test_redact_payload_does_not_mutate_the_original_payload():
    original = {"spec": {"containers": [{"env": [{"name": "DB_PASSWORD", "value": "do-not-touch"}]}]}}
    snapshot = copy.deepcopy(original)
    redact_payload(original)
    assert original == snapshot


def test_redact_payload_redacts_sensitive_content_in_an_event_message():
    payload = {"reason": "Failed", "message": "auth failed: token=abcDEF123xyz for upstream"}
    redacted, categories = redact_payload(payload)
    assert "abcDEF123xyz" not in redacted["message"]
    assert redacted["reason"] == "Failed"  # unrelated fields untouched
    assert "credential_assignment" in categories


def test_redact_payload_passes_through_scalars_and_none_unchanged():
    payload = {"count": 3, "ready": True, "note": None}
    redacted, categories = redact_payload(payload)
    assert redacted == payload
    assert categories == set()


def test_redact_payload_handles_a_bare_string_log_payload():
    log_text = f"line1\nconnecting: dsn={_FAKE_DSN}\nline3 Connection refused\n"
    redacted, categories = redact_payload(log_text)
    assert "postgres:postgres" not in redacted
    assert "line1" in redacted and "Connection refused" in redacted
    assert "credential_url" in categories


# --- Integration via build_model_input: no mutation of the original bundle, redaction flagged -


def _bundle_with_items(*items: EvidenceItem) -> EvidenceBundle:
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    return EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)), items=list(items),
    )


def _pod_item_with_secret() -> EvidenceItem:
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    payload = {
        "spec": {"containers": [{"env": [
            {"name": "PGVECTOR_DSN", "value": _FAKE_DSN},
            {"name": "OPENAI_MODEL_DEFAULT", "value": "gpt-4o-mini"},
        ]}]},
        "status": {"containerStatuses": [{"state": {"waiting": {"reason": "CrashLoopBackOff"}}}]},
    }
    return EvidenceItem(id="item-pod", source_ref=ref, collected_at=_ts(), payload=payload, status=EvidenceStatus.PRESENT)


def _log_item_with_secret() -> EvidenceItem:
    ref = ApiCallRef(verb="get", path="/pods/p/log", params={})
    text = f"starting up\nconnection failed: dsn={_FAKE_DSN}\nConnection refused\n"
    return EvidenceItem(id="item-log", source_ref=ref, collected_at=_ts(), payload=text, status=EvidenceStatus.PRESENT)


def test_build_model_input_contains_no_secret_anywhere_in_the_serialized_text():
    pod = _pod_item_with_secret()
    log = _log_item_with_secret()
    bundle = _bundle_with_items(pod, log)

    model_input = build_model_input(bundle, [])
    serialized = str(model_input)

    assert _FAKE_DSN not in serialized
    assert "postgres:postgres" not in serialized


def test_build_model_input_preserves_non_sensitive_diagnostic_content():
    pod = _pod_item_with_secret()
    log = _log_item_with_secret()
    bundle = _bundle_with_items(pod, log)

    model_input = build_model_input(bundle, [])

    assert model_input["evidence"][pod.id]["payload"]["status"]["containerStatuses"][0]["state"]["waiting"]["reason"] == "CrashLoopBackOff"
    assert "Connection refused" in model_input["evidence"][log.id]["payload"]
    assert "starting up" in model_input["evidence"][log.id]["payload"]


def test_build_model_input_flags_which_evidence_items_were_redacted_and_why():
    pod = _pod_item_with_secret()
    log = _log_item_with_secret()
    bundle = _bundle_with_items(pod, log)

    model_input = build_model_input(bundle, [])

    assert model_input["evidence"][pod.id]["redacted"] is True
    assert "sensitive_env_name" in model_input["evidence"][pod.id]["redaction_categories"]
    assert model_input["evidence"][log.id]["redacted"] is True
    assert "credential_url" in model_input["evidence"][log.id]["redaction_categories"]


def test_build_model_input_does_not_mutate_the_original_bundle():
    pod = _pod_item_with_secret()
    bundle = _bundle_with_items(pod)
    snapshot = copy.deepcopy(pod.payload)

    build_model_input(bundle, [])

    assert bundle.item_by_id(pod.id).payload == snapshot
    assert _FAKE_DSN in str(bundle.item_by_id(pod.id).payload)  # the REAL bundle still has it


def test_build_model_input_reproducibility_is_unaffected_by_redaction():
    pod = _pod_item_with_secret()
    bundle = _bundle_with_items(pod)
    first = content_hash(build_model_input(bundle, []))
    second = content_hash(build_model_input(bundle, []))
    assert first == second
