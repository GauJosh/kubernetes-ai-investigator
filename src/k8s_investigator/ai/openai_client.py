"""A real ``HypothesisModelClient`` backed by the OpenAI Chat Completions API.

This is an *adapter*, not a coupling point: ``ai/hypothesis.py`` and everything upstream of it
only ever see the provider-neutral ``HypothesisModelClient`` Protocol (``generate(model_input)
-> str``, returning text ``ai.hypothesis.parse_candidates`` can parse into a bare JSON array).
Nothing outside this module imports the ``openai`` package, and that import itself is lazy
(inside ``OpenAIHypothesisModelClient.__init__``) so the deterministic pipeline, and this
module's own offline tests, never require it to be installed -- it is an optional extra
(``pip install -e ".[ai]"``, see ``pyproject.toml``).

No credential is hard-coded anywhere in this file. ``build_openai_client_from_env`` reads
``OPENAI_API_KEY`` and a configurable model-name variable (``AI_HYPOTHESIS_MODEL``) from the
process environment only, and raises a clear, typed error (never a silent fallback) when either
is missing.

Structured output: this client asks for OpenAI's strict, JSON-Schema-constrained response
format (``response_format={"type": "json_schema", ...}``), built directly from the same
``RawHypothesisCandidate``/``EvidenceLocator``/``Confidence`` shapes ``ai.hypothesis`` already
defines -- not a second, independently-maintained schema. OpenAI's Structured Outputs feature
requires a top-level JSON *object*, so the model is asked to wrap the candidate array under a
single ``"hypotheses"`` key; ``_unwrap_candidate_array`` undoes that wrapping before the text is
handed back, so ``parse_candidates`` downstream still only ever has to understand "a bare JSON
array" -- a provider-specific transport quirk, contained entirely in this adapter.

Provider-side schema enforcement is a convenience, not a trust boundary: whatever this client
returns is still parsed by ``parse_candidates`` and every resulting candidate is still run
through the exact same ``validate_finding_references`` gate as everything else in this project
(``ai.hypothesis.generate_ai_hypotheses``) -- this module adds no shortcut around that.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .client_factory import HypothesisClientConfigurationError

_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {
                        "type": "string",
                        "description": "The hypothesis, in plain prose.",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["LOW", "MEDIUM", "HIGH"],
                    },
                    "limitation": {
                        "type": ["string", "null"],
                        "description": "What this hypothesis does NOT establish, if anything.",
                    },
                    "contradicts": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "ids of findings (from the input) this hypothesis conflicts with, if any.",
                    },
                    "evidence_locators": {
                        "type": "array",
                        "description": (
                            "At least one. Every evidence_id MUST be a key that actually "
                            "appears in the input's 'evidence' object -- never invent one."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "evidence_id": {"type": "string"},
                                "quoted_text": {
                                    "type": "string",
                                    "description": "The exact substring/value the pointer resolves to -- copy it verbatim, do not paraphrase.",
                                },
                                "pointer": {
                                    "anyOf": [
                                        {
                                            "type": "object",
                                            "properties": {
                                                "pointer_type": {"const": "field_path"},
                                                "json_pointer": {
                                                    "type": "string",
                                                    "description": "RFC 6901 JSON Pointer into the evidence item's structured payload, e.g. /status/phase.",
                                                },
                                            },
                                            "required": ["pointer_type", "json_pointer"],
                                            "additionalProperties": False,
                                        },
                                        {
                                            "type": "object",
                                            "properties": {
                                                "pointer_type": {"const": "line_range"},
                                                "start_line": {"type": "integer"},
                                                "end_line": {"type": "integer"},
                                            },
                                            "required": ["pointer_type", "start_line", "end_line"],
                                            "additionalProperties": False,
                                        },
                                    ]
                                },
                            },
                            "required": ["evidence_id", "quoted_text", "pointer"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["statement", "confidence", "limitation", "contradicts", "evidence_locators"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["hypotheses"],
    "additionalProperties": False,
}

_SYSTEM_INSTRUCTIONS = """\
You are a Kubernetes failure-investigation assistant. You are given, as JSON: the evidence
already collected for one Pod investigation (keyed by evidence_id), and the facts/deterministic
findings already established over that evidence. Your job is ONLY to propose zero or more
additional hypotheses about the underlying cause -- you never collect new evidence, you never
suggest or perform any remediation action, and you never contradict a deterministic finding
without clearly saying so in 'limitation'.

Rules, strictly enforced by code after you respond (a violation causes your hypothesis to be
silently rejected, not corrected):
- Every evidence_locators[].evidence_id you cite MUST be a key that literally appears in the
  input's "evidence" object. Never invent one.
- Every quoted_text MUST be an exact copy of real content at that pointer -- never paraphrase,
  never fabricate.
- Every hypothesis MUST have at least one evidence_locators entry and a confidence value.

If the evidence is insufficient to support any hypothesis, return an empty "hypotheses" array.
Returning nothing is correct and expected when you are not confident -- it is always preferable
to a fabricated or weakly-supported guess.
"""


def _unwrap_candidate_array(content: str) -> str:
    """OpenAI's structured-output mode requires a top-level JSON object, so the model is asked
    to nest the candidate array under a "hypotheses" key. This undoes that nesting, returning a
    bare JSON array string -- the shape ai.hypothesis.parse_candidates expects, and the shape
    every provider's client should return regardless of that provider's own transport quirks.
    Passes ``content`` through unchanged if it does not look like the expected wrapper, so
    parse_candidates still gets a chance to raise its own specific, clear error."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content
    if isinstance(parsed, dict) and isinstance(parsed.get("hypotheses"), list):
        return json.dumps(parsed["hypotheses"])
    return content


class OpenAIHypothesisModelClient:
    """Implements ``HypothesisModelClient.generate()`` against OpenAI's Chat Completions API.

    ``raw_client`` is an injection point for tests only -- anything with a
    ``.chat.completions.create(...)`` method shaped like the real SDK's. A real caller should
    use ``build_openai_client_from_env()`` instead of constructing this directly.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        raw_client: Any | None = None,
    ) -> None:
        self._model = model
        if raw_client is not None:
            self._client = raw_client
        else:
            from openai import OpenAI  # lazy -- optional dependency, see module docstring

            self._client = OpenAI(api_key=api_key, timeout=timeout_seconds)

    def generate(self, model_input: dict[str, Any]) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "ai_hypothesis_candidates",
                    "schema": _RESPONSE_JSON_SCHEMA,
                    "strict": True,
                },
            },
            messages=[
                {"role": "system", "content": _SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": json.dumps(model_input)},
            ],
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("OpenAI response contained no message content")
        return _unwrap_candidate_array(content)


def build_openai_client_from_env() -> tuple[OpenAIHypothesisModelClient, str]:
    """Returns ``(client, model_identifier)``. Raises ``HypothesisClientConfigurationError``
    if ``OPENAI_API_KEY`` or ``AI_HYPOTHESIS_MODEL`` is unset -- never falls back to a default
    model or a hard-coded key."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HypothesisClientConfigurationError(
            "AI_HYPOTHESIS_PROVIDER=openai requires OPENAI_API_KEY to be set in the environment."
        )
    model = os.environ.get("AI_HYPOTHESIS_MODEL")
    if not model:
        raise HypothesisClientConfigurationError(
            "AI_HYPOTHESIS_PROVIDER=openai requires AI_HYPOTHESIS_MODEL to be set (e.g. "
            "'gpt-4o-mini') -- the model name is configurable, never hard-coded."
        )
    client = OpenAIHypothesisModelClient(api_key=api_key, model=model)
    return client, f"model:openai/{model}"
