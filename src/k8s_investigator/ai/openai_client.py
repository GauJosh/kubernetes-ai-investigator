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
                            "Structured (payload_shape: \"structured\") evidence only. Every "
                            "evidence_id MUST be a key that actually appears in the input's "
                            "'evidence' object -- never invent one. For line-indexed text "
                            "(payload_shape: \"line_indexed_text\"), use 'line_citations' "
                            "instead -- do not use this field for that evidence shape."
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
                                    "type": "object",
                                    "properties": {
                                        "pointer_type": {"type": "string", "const": "field_path"},
                                        "json_pointer": {
                                            "type": "string",
                                            "description": "RFC 6901 JSON Pointer into the evidence item's structured payload, e.g. /status/phase.",
                                        },
                                    },
                                    "required": ["pointer_type", "json_pointer"],
                                    "additionalProperties": False,
                                },
                            },
                            "required": ["evidence_id", "quoted_text", "pointer"],
                            "additionalProperties": False,
                        },
                    },
                    "line_citations": {
                        "type": "array",
                        "description": (
                            "Line-indexed text (payload_shape: \"line_indexed_text\") only. "
                            "Each entry selects one or more pre-issued 'ref' ids from that "
                            "evidence item's payload.lines[] instead of constructing a line "
                            "range or transcribing a quote -- code resolves the exact text. "
                            "For structured evidence, use 'evidence_locators' instead."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "evidence_id": {
                                    "type": "string",
                                    "description": "The evidence item every ref below must actually belong to.",
                                },
                                "refs": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "One or more 'ref' values copied verbatim from that "
                                        "evidence item's payload.lines[]. For a multi-line "
                                        "quote (e.g. an error message continuing onto an "
                                        "indented line), list every included line's ref -- "
                                        "they must be contiguous, ascending lines from the "
                                        "SAME evidence item. Never invent a ref."
                                    ),
                                },
                            },
                            "required": ["evidence_id", "refs"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["statement", "confidence", "limitation", "contradicts", "evidence_locators", "line_citations"],
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

The input has two kinds of text, marked explicitly so you never have to guess which is which:
every entry in "evidence" carries "citable": true -- only text inside such an entry's own
"payload" is real, literal source content you may quote. Every entry in "findings" carries
"citable": false -- its "statement" is a human/code-written SUMMARY for your reasoning context
only, not a substring of any evidence payload, and must NEVER appear as quoted_text, even if it
describes the same fact you want to cite. If a "findings" entry says something you agree with
and want to cite, use the SAME evidence_id listed in that finding's own "evidence_ids" and
quote the actual raw value at a real pointer into that evidence's "payload" -- never the
finding's own wording.

Every evidence item carries an explicit "payload_shape": either "line_indexed_text" or
"structured". Check this field, not the shape of the payload itself, to decide how to cite
it -- do not guess from appearance, and do not mix the two citation mechanisms below.

For "payload_shape": "line_indexed_text" (logs), the payload looks like {"lines": [{"line": 0,
"text": "...", "ref": "<id>"}, {"line": 1, "text": "...", "ref": "<id>"}, ...], "lines_total":
N}. Put a "line_citations" entry on your hypothesis instead of an "evidence_locators" entry --
{"evidence_id": "<that evidence item's id>", "refs": ["<ref>", ...]} -- naming the "ref" value
of every line you mean, copied verbatim. You do not construct a line range and you do not
transcribe a quote yourself for this shape at all; code resolves the exact text from the refs
you select. For a quote spanning more than one line (e.g. an error message continuing onto an
indented continuation line), list every included line's ref, in any order -- they must be a
contiguous, ascending run of lines from that one evidence item. A line with no "ref" key (it
was withheld or cut by length limits) cannot be cited at all -- do not invent one.

For "payload_shape": "structured" (e.g. a Pod, Event, or Node object), use "evidence_locators"
exactly as before: pointer_type "field_path" with an RFC 6901 JSON Pointer *relative to that
payload object directly* -- do NOT include a leading "/payload" segment. For example, if the
evidence entry is {"payload_shape": "structured", "payload": {"message": "..."}}, the correct
json_pointer for that field is "/message", NOT "/payload/message". A field_path pointer must
resolve to a single scalar value (a string, number, or boolean) -- never point at a nested
object or array; drill down to the specific field you mean (e.g. "/status/phase", not
"/status"). You still transcribe quoted_text yourself for this shape, exactly as before.

If a "contradictions" array is present and non-empty, each entry names two evidence_ids
(evidence_a, evidence_b) that disagree, with a rule_id and description. If your hypothesis
cites either evidence_id from any contradiction entry, you MUST say so in 'limitation' -- do
not silently treat one side of an unresolved disagreement as settled fact.

Rules, strictly enforced by code after you respond (a violation causes your hypothesis to be
silently rejected, not corrected):
- Every evidence_locators[].evidence_id you cite MUST be a key that literally appears in the
  input's "evidence" object. Never invent one.
- Every quoted_text MUST be an exact copy of real content at that pointer -- never paraphrase,
  never fabricate, and never include a line-number label you were shown for reference only.
- quoted_text identical to any "findings" entry's "statement" is REJECTED -- a finding's
  statement is marked "citable": false for exactly this reason; quote the real evidence value
  instead, even when it describes the same fact.
- Every line_citations[].refs value MUST be a "ref" that actually appears in that evidence
  item's payload.lines[] -- an unknown ref, a ref that actually belongs to a different
  evidence item than the one named, or a selection that is not a contiguous ascending run of
  lines (including a duplicate) is REJECTED, not repaired or substituted.
- confidence "HIGH" is REJECTED unless at least one DETERMINISTIC_FINDING is present in the
  input's "findings" array. If "findings" contains no DETERMINISTIC_FINDING entry, the
  deterministic rule bank itself reached no confirmed conclusion -- use "MEDIUM" or "LOW"
  instead; a hypothesis is still welcome, just not asserted with unwarranted certainty.
- A hypothesis citing evidence named in any "contradictions" entry with an empty 'limitation'
  is rejected (see above).
- Every hypothesis MUST have at least one evidence_locators or line_citations entry (or both)
  and a confidence value.

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

    ``last_usage`` is a side-channel for evaluation/observability only -- it is not part of
    the ``HypothesisModelClient`` Protocol (``generate`` still only returns ``str``) and no
    caller in this project's own pipeline reads it. It holds the most recent response's
    ``usage`` object (token counts) exactly as the SDK returned it, or ``None`` before any
    call or if the response didn't carry one -- read via ``getattr`` so it never raises if a
    test's injected ``raw_client`` response has no ``usage`` attribute at all.

    ``max_retries`` defaults to 0: this project's design is one request, one response, per
    investigation (docs/phase-0.11-ai-hypothesis-layer-design.md §4) -- the SDK's default
    automatic retrying would silently turn "one call" into several on a transient error,
    which conflicts with that design and would also misreport cost/latency/request-count for
    any caller trying to measure them honestly. A failure surfaces as a failure.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 0,
        raw_client: Any | None = None,
    ) -> None:
        self._model = model
        self.last_usage: Any | None = None
        if raw_client is not None:
            self._client = raw_client
        else:
            from openai import OpenAI  # lazy -- optional dependency, see module docstring

            self._client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=max_retries)

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
        self.last_usage = getattr(response, "usage", None)
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
