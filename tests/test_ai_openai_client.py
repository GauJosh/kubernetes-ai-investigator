"""ai.openai_client -- the first real HypothesisModelClient adapter.

Every test here injects a fake ``raw_client`` (anything shaped like
``.chat.completions.create(...) -> response``) -- the real ``openai`` package is never
imported, never required to be installed, and no network call is ever made. This file proves
the adapter's own logic (structured-output unwrapping, error surfacing, configuration
validation) in complete isolation from the actual OpenAI service.
"""

from __future__ import annotations

import json

import pytest

from k8s_investigator.ai.client_factory import HypothesisClientConfigurationError
from k8s_investigator.ai.hypothesis import generate_ai_hypotheses
from k8s_investigator.ai.openai_client import (
    OpenAIHypothesisModelClient,
    _unwrap_candidate_array,
    build_openai_client_from_env,
)
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import FindingKind
from k8s_investigator.models.refs import ObjectRef, TimestampValue
from datetime import datetime, timezone


# --- test doubles for the OpenAI SDK's response shape -----------------------------------------


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self._content = content
        self._error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeRawClient:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.completions = _FakeCompletions(content, error)
        self.chat = _FakeChat(self.completions)


# --- _unwrap_candidate_array --------------------------------------------------------------------


def test_unwrap_turns_wrapped_object_into_bare_array_text():
    wrapped = json.dumps({"hypotheses": [{"statement": "x"}]})
    unwrapped = _unwrap_candidate_array(wrapped)
    assert json.loads(unwrapped) == [{"statement": "x"}]


def test_unwrap_passes_through_already_bare_array():
    bare = json.dumps([{"statement": "x"}])
    assert _unwrap_candidate_array(bare) == bare


def test_unwrap_passes_through_non_json_content_unchanged():
    garbage = "not json at all {"
    assert _unwrap_candidate_array(garbage) == garbage


def test_unwrap_passes_through_object_without_hypotheses_key_unchanged():
    odd = json.dumps({"something_else": []})
    assert _unwrap_candidate_array(odd) == odd


# --- OpenAIHypothesisModelClient.generate: normal response -------------------------------------


def test_generate_calls_chat_completions_with_json_schema_response_format():
    raw = _FakeRawClient(content=json.dumps({"hypotheses": []}))
    client = OpenAIHypothesisModelClient(api_key="unused", model="gpt-test", raw_client=raw)

    result = client.generate({"evidence": {}, "findings": []})

    assert result == "[]"
    call = raw.completions.calls[0]
    assert call["model"] == "gpt-test"
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True


def test_generate_unwraps_a_normal_hypotheses_response():
    candidate = {
        "statement": "x", "confidence": "MEDIUM", "limitation": None, "contradicts": None,
        "evidence_locators": [
            {"evidence_id": "e1", "quoted_text": "v", "pointer": {"pointer_type": "field_path", "json_pointer": "/a"}}
        ],
    }
    raw = _FakeRawClient(content=json.dumps({"hypotheses": [candidate]}))
    client = OpenAIHypothesisModelClient(api_key="unused", model="gpt-test", raw_client=raw)

    result = client.generate({"evidence": {}, "findings": []})

    assert json.loads(result) == [candidate]


# --- OpenAIHypothesisModelClient.generate: malformed/error responses ---------------------------


def test_generate_raises_when_message_content_is_none():
    raw = _FakeRawClient(content=None)
    client = OpenAIHypothesisModelClient(api_key="unused", model="gpt-test", raw_client=raw)
    with pytest.raises(ValueError):
        client.generate({"evidence": {}, "findings": []})


def test_generate_propagates_provider_errors():
    class _ProviderError(Exception):
        pass

    raw = _FakeRawClient(error=_ProviderError("rate limited"))
    client = OpenAIHypothesisModelClient(api_key="unused", model="gpt-test", raw_client=raw)
    with pytest.raises(_ProviderError):
        client.generate({"evidence": {}, "findings": []})


# --- build_openai_client_from_env: configuration errors, fail clearly --------------------------


def test_build_from_env_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_HYPOTHESIS_MODEL", "gpt-test")
    with pytest.raises(HypothesisClientConfigurationError):
        build_openai_client_from_env()


def test_build_from_env_missing_model_raises(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.delenv("AI_HYPOTHESIS_MODEL", raising=False)
    with pytest.raises(HypothesisClientConfigurationError):
        build_openai_client_from_env()


def test_build_from_env_with_both_set_does_not_import_the_real_openai_package(monkeypatch):
    """Constructing the real client without injecting raw_client lazily imports `openai`,
    which is not installed in this environment -- proving it is truly optional, not silently
    required, by observing the expected ModuleNotFoundError rather than a successful import."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("AI_HYPOTHESIS_MODEL", "gpt-test")
    with pytest.raises(ModuleNotFoundError):
        build_openai_client_from_env()


# --- End-to-end through generate_ai_hypotheses: provider-side schema enforcement does not ------
# --- replace application validation -------------------------------------------------------------


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _bundle_with_one_item() -> tuple[EvidenceBundle, EvidenceItem]:
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    item = EvidenceItem(
        id="item-1", source_ref=ref, collected_at=_ts(), payload={"status": {"phase": "Pending"}},
        status=EvidenceStatus.PRESENT,
    )
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    bundle = EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)), items=[item],
    )
    return bundle, item


def test_end_to_end_valid_openai_style_response_produces_an_accepted_hypothesis():
    bundle, item = _bundle_with_one_item()
    candidate = {
        "statement": "Grounded hypothesis.", "confidence": "HIGH", "limitation": None, "contradicts": None,
        "evidence_locators": [
            {"evidence_id": item.id, "quoted_text": "Pending", "pointer": {"pointer_type": "field_path", "json_pointer": "/status/phase"}}
        ],
    }
    raw = _FakeRawClient(content=json.dumps({"hypotheses": [candidate]}))
    client = OpenAIHypothesisModelClient(api_key="unused", model="gpt-test", raw_client=raw)

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:openai/gpt-test")

    assert len(results) == 1
    assert results[0].kind == FindingKind.AI_HYPOTHESIS


def test_end_to_end_schema_passing_but_hallucinated_citation_is_still_rejected():
    """Even a response that is perfectly well-formed JSON matching the schema is rejected if
    its citation does not actually resolve -- provider-side schema enforcement constrains
    *shape*, never *truth*; application validation is what catches a hallucinated evidence_id
    or a wrong quote, and this test proves that gate still runs for OpenAI responses."""
    bundle, item = _bundle_with_one_item()
    candidate = {
        "statement": "Hallucinated citation.", "confidence": "HIGH", "limitation": None, "contradicts": None,
        "evidence_locators": [
            {"evidence_id": "does-not-exist", "quoted_text": "Pending", "pointer": {"pointer_type": "field_path", "json_pointer": "/status/phase"}}
        ],
    }
    raw = _FakeRawClient(content=json.dumps({"hypotheses": [candidate]}))
    client = OpenAIHypothesisModelClient(api_key="unused", model="gpt-test", raw_client=raw)

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:openai/gpt-test")

    assert results == []
