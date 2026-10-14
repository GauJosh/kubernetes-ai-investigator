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
from ..errors import (
    FindingStatementCitedAsEvidenceError,
    InvestigatorError,
    UnacknowledgedContradictionError,
    UngroundedConfidenceError,
)
from ..models.evidence import ContradictionEdge, EvidenceBundle
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


def _check_confidence_requires_deterministic_grounding(
    finding: Finding, findings: list[Finding]
) -> None:
    """A HIGH-confidence AI_HYPOTHESIS requires at least one DETERMINISTIC_FINDING already
    established for this investigation. A live evaluation showed the model producing
    HIGH-confidence causal claims from a single piece of generic evidence precisely in the
    scenarios where the deterministic rule bank itself found nothing definite -- a mechanically
    valid citation (real evidence, correctly quoted) is not the same as a substantively
    sufficient one, and this is a structural proxy for that distinction, not a judgment of the
    hypothesis's own wording. See errors.UngroundedConfidenceError.
    """
    if finding.confidence != Confidence.HIGH:
        return
    if not any(f.kind == FindingKind.DETERMINISTIC_FINDING for f in findings):
        raise UngroundedConfidenceError(finding.id)


def _check_contradiction_acknowledged(
    finding: Finding, contradictions: list[ContradictionEdge]
) -> None:
    """A hypothesis citing evidence on either side of a recorded contradiction must carry a
    non-empty 'limitation' -- checked by evidence_id membership only, never by what the
    hypothesis's prose says, so this generalizes to any future contradiction rather than
    recognizing specific wording. See errors.UnacknowledgedContradictionError.
    """
    if not contradictions:
        return
    contested_ids = {edge.evidence_a for edge in contradictions} | {
        edge.evidence_b for edge in contradictions
    }
    if set(finding.evidence_ids) & contested_ids and not finding.limitation:
        raise UnacknowledgedContradictionError(finding.id)


def _check_quote_not_a_finding_statement(finding: Finding, findings: list[Finding]) -> None:
    """A diagnostic-clarity check, not a new acceptance rule: if a locator's quoted_text is
    exactly some existing Finding's own rendered statement, flag that specifically rather
    than letting it fall through to a generic quote-mismatch error. Such a quote was already
    going to fail validate_finding_references's real content check -- a Finding's statement
    is rendered prose, essentially never identical to a raw evidence field value -- so this
    changes nothing about which candidates are accepted; it only names the mistake precisely.
    A structural equality comparison against known statements, not a judgment of what the
    hypothesis's own reasoning says. See errors.FindingStatementCitedAsEvidenceError.
    """
    statements = {f.statement for f in findings}
    for locator in finding.evidence_locators:
        if locator.quoted_text in statements:
            raise FindingStatementCitedAsEvidenceError(finding.id, locator.evidence_id, locator.quoted_text)


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
    contradictions: list[ContradictionEdge] | None = None,
) -> list[Finding]:
    """The full, opt-in orchestration: build input -> one model call -> parse -> promote ->
    validate -> dedup -> sort.

    ``contradictions`` is ``evaluate_bundle``'s own second return value -- pass it through so
    the model is actually shown what it is expected to acknowledge (see
    ``model_input.build_model_input``'s docstring). Omitting it is equivalent to no recorded
    contradictions, not an error.

    A candidate that fails ``Finding`` construction (e.g. no evidence_locators),
    ``validate_finding_references`` (e.g. an unknown evidence id, an out-of-bounds pointer, a
    stale quote), quotes a Finding's own rendered statement instead of real evidence content
    (``_check_quote_not_a_finding_statement``), a HIGH confidence claim with no deterministic
    grounding (``_check_confidence_requires_deterministic_grounding``), or citing contested
    evidence with no acknowledgement (``_check_contradiction_acknowledged``) is dropped
    silently -- rejected, not corrected and not surfaced, exactly like a rule-engine bug is
    never allowed to reach a report. An empty return value is this layer's abstention
    representation: a legitimate "the model produced no hypothesis meeting the bar" result,
    not an error.

    Raises ``HypothesisParseError`` if the model's response as a whole could not be understood
    at all -- deliberately not caught here; see that class's docstring for why.
    """
    contradictions = contradictions or []
    model_input = build_model_input(bundle, findings, contradictions=contradictions)
    raw_response = client.generate(model_input)
    candidates = parse_candidates(raw_response)

    accepted: list[Finding] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    for candidate in candidates:
        try:
            finding = make_ai_hypothesis_finding(candidate, model_identifier)
            # Checked before the generic content-match gate so this specific, diagnosable
            # mistake is reported precisely rather than falling through to a generic
            # mismatch -- it would fail that gate anyway (see the function's own docstring),
            # so this changes nothing about what is accepted, only why a rejection is named.
            _check_quote_not_a_finding_statement(finding, findings)
            validate_finding_references(finding, bundle)
            _check_confidence_requires_deterministic_grounding(finding, findings)
            _check_contradiction_acknowledged(finding, contradictions)
        except (ValueError, InvestigatorError):
            continue

        key = _dedup_key(finding)
        if key in seen:
            continue
        seen.add(key)
        accepted.append(finding)

    accepted.sort(key=lambda f: _CONFIDENCE_RANK[f.confidence])
    return accepted
