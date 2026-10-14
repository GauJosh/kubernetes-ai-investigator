"""Turning an untrusted model response into validated AI_HYPOTHESIS Findings.

The model's output is never trusted directly as a ``Finding``. It is first parsed into
``RawHypothesisCandidate`` -- reusing ``EvidenceLocator``/``Confidence`` verbatim, no parallel
schema -- then promoted into a real ``Finding`` with code, never the model, assigning
``kind``/``id``/``produced_by`` (docs/phase-0.11-ai-hypothesis-layer-design.md §3.2). Every
candidate is then run through the exact same ``validate_finding_references`` gate every other
Finding in this project goes through before being accepted; a candidate that fails is dropped,
never surfaced as if it were trustworthy -- the same discipline
``report_builder.build_report`` already applies to rule-produced findings.

``generate_ai_hypotheses`` is the only entry point a caller needs. It is never called by any
default pipeline path -- a caller must explicitly invoke it with a real
``HypothesisModelClient`` (none exists in this phase; see ``model_client.py``).
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, ValidationError

from ..canonical import content_hash
from ..errors import InvestigatorError
from ..models.evidence import EvidenceBundle
from ..models.findings import Confidence, Finding, FindingKind
from ..models.locators import EvidenceLocator
from ..validation import validate_finding_references
from .model_client import HypothesisModelClient
from .model_input import build_model_input


class HypothesisParseError(Exception):
    """The model's raw response, as a whole, could not be understood at all (not JSON, or not
    a JSON array) -- distinct from one malformed candidate within an otherwise-good array,
    which is dropped individually rather than voiding the whole response (see
    ``parse_candidates``). Deliberately not caught inside ``generate_ai_hypotheses`` -- a
    caller must decide how to degrade (e.g. ship the deterministic report with a visible
    warning), the same way ``cli.py`` already decides how to handle an ``InvestigatorError`` at
    its own boundary, rather than this layer silently collapsing "the response was broken" into
    the same empty-list signal as "the model legitimately found nothing."""


class RawHypothesisCandidate(BaseModel):
    """The untrusted shape the model is asked to produce.

    Deliberately narrower than ``Finding``: no ``id``, ``kind``, or ``produced_by`` field
    exists here at all, because those three are always code-assigned in
    ``make_ai_hypothesis_finding``, never model-assigned (docs/phase-0.11-ai-hypothesis-layer-
    design.md §3.2) -- there is nothing for a model response to fill in that could impersonate
    a deterministic finding or claim a false identity.
    """

    model_config = ConfigDict(frozen=True)

    statement: str
    evidence_locators: list[EvidenceLocator]
    confidence: Confidence
    limitation: str | None = None
    contradicts: list[str] | None = None


def parse_candidates(raw_response: str) -> list[RawHypothesisCandidate]:
    """Parse the model's raw text into typed candidates.

    Raises ``HypothesisParseError`` only when the response as a whole cannot be understood
    (invalid JSON, or valid JSON that is not an array). An individual array element that does
    not match ``RawHypothesisCandidate`` is dropped and the rest of the array is still
    processed -- one malformed candidate must not void an otherwise-usable response.
    """
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise HypothesisParseError(f"model response is not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise HypothesisParseError(
            f"model response must be a JSON array of candidates, got {type(data).__name__}"
        )

    candidates: list[RawHypothesisCandidate] = []
    for raw_candidate in data:
        try:
            candidates.append(RawHypothesisCandidate.model_validate(raw_candidate))
        except ValidationError:
            continue
    return candidates


def make_ai_hypothesis_finding(candidate: RawHypothesisCandidate, model_identifier: str) -> Finding:
    """Promote one parsed candidate into a real Finding.

    ``kind``/``id``/``produced_by`` are assigned here, by code -- ``RawHypothesisCandidate``
    has no such fields to read them from in the first place. ``id`` is derived from
    ``{produced_by, evidence_ids, confidence}`` only -- deliberately *not* from ``statement``
    text, unlike ``rules.base.make_finding_id``. An LLM's prose is not guaranteed
    byte-identical across calls even at temperature 0, so hashing it would make ``id`` look
    like a stable, content-addressed identifier while actually being arbitrary. What *is*
    reproducible and is hashed: which model produced the hypothesis, over exactly which
    evidence, at what confidence (docs/phase-0.11-ai-hypothesis-layer-design.md §3.3).
    """
    evidence_ids = sorted({locator.evidence_id for locator in candidate.evidence_locators})
    digest = content_hash(
        {
            "produced_by": model_identifier,
            "evidence_ids": evidence_ids,
            "confidence": candidate.confidence.value,
        }
    )
    return Finding(
        id=f"finding:{digest}",
        kind=FindingKind.AI_HYPOTHESIS,
        statement=candidate.statement,
        evidence_locators=candidate.evidence_locators,
        produced_by=model_identifier,
        limitation=candidate.limitation,
        confidence=candidate.confidence,
        contradicts=candidate.contradicts,
    )


# HIGH ranks first, LOW last -- the sort is stable, so hypotheses within the same confidence
# tier keep the model's own returned order (docs/phase-0.11-ai-hypothesis-layer-design.md §3.4
# "ranking" row). No separate numeric score is introduced.
_CONFIDENCE_RANK = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}


def _dedup_key(finding: Finding) -> tuple[str, frozenset[str]]:
    """Exact-duplicate key: the same model call citing the exact same evidence set.

    Deliberately narrow -- two differently-worded hypotheses making the same underlying claim
    (a near-duplicate, not an exact one) are NOT collapsed here; semantic deduplication is an
    out-of-scope, measured-not-solved evaluation dimension
    (docs/phase-0.11-ai-hypothesis-layer-design.md §3.4, §5).
    """
    return (finding.produced_by, frozenset(finding.evidence_ids))


def generate_ai_hypotheses(
    bundle: EvidenceBundle,
    findings: list[Finding],
    client: HypothesisModelClient,
    *,
    model_identifier: str,
) -> list[Finding]:
    """The full, opt-in orchestration: build input -> one model call -> parse -> promote ->
    validate -> dedup -> sort.

    A candidate that fails ``Finding`` construction (e.g. no evidence_locators) or
    ``validate_finding_references`` (e.g. an unknown evidence id, an out-of-bounds pointer, a
    stale quote) is dropped silently -- rejected, not corrected and not surfaced, exactly like
    a rule-engine bug is never allowed to reach a report. An empty return value is this layer's
    abstention representation: a legitimate "the model produced no hypothesis meeting the
    evidence bar" result, not an error.

    Raises ``HypothesisParseError`` if the model's response as a whole could not be understood
    at all -- deliberately not caught here; see that class's docstring for why.
    """
    model_input = build_model_input(bundle, findings)
    raw_response = client.generate(model_input)
    candidates = parse_candidates(raw_response)

    accepted: list[Finding] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    for candidate in candidates:
        try:
            finding = make_ai_hypothesis_finding(candidate, model_identifier)
            validate_finding_references(finding, bundle)
        except (ValueError, InvestigatorError):
            continue

        key = _dedup_key(finding)
        if key in seen:
            continue
        seen.add(key)
        accepted.append(finding)

    accepted.sort(key=lambda f: _CONFIDENCE_RANK[f.confidence])
    return accepted
