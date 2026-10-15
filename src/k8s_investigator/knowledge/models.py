"""The custom knowledge-pack schema (Phase 0.18).

Every type here describes untrusted, user-supplied YAML -- not evidence, not a finding, and
never executable. ``loader.py`` is the only place this schema is actually parsed from disk and
safety-checked; this module holds pure data shapes and the validation Pydantic can express
directly on them (bounds, required fields, the closed set of supported match operators).

Deliberately a small, closed set of match operators -- never a general-purpose query language
or expression engine (see ``RuleMatch``). A new operator is added only when it can be
implemented as a reliable, deterministic boolean check against the real ``EvidenceBundle``
shape (see ``matcher.py``); an operator that could only approximate its intended behavior is
left out entirely, not added anyway.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The only schema version this release understands. A knowledge pack declaring any other
#: value is rejected outright at load time -- never silently coerced or partially honored.
SUPPORTED_SCHEMA_VERSION = 1

#: Bounded, documented, non-configurable limits. A knowledge pack is untrusted input; these
#: exist so a malformed or oversized pack cannot make one investigation unboundedly slow or
#: produce an unreadable report -- the same spirit as this project's existing bounded log
#: collection (tail lines, time window), applied to this new input surface.
MAX_RULES = 100
MAX_MATCH_VALUES_PER_RULE = 20
MAX_NEXT_CHECKS_PER_RULE = 20
MAX_STRING_LENGTH = 2000  # description, summary, each next_check, each evidence_contains entry
MAX_PACK_FILE_BYTES = 1_000_000  # enforced by loader.py before the file is even parsed

_RULE_ID_PATTERN = r"^[a-zA-Z0-9_-]+$"


class RuleMatch(BaseModel):
    """A rule matches only when **every** operator it declares holds (AND across operators).
    Within ``evidence_contains``, matching **any** one listed substring is sufficient (OR
    within that one operator) -- it names a set of equivalent trigger phrases for one
    conceptual symptom, not a set of independent conditions.

    At least one operator must be set: an empty match block would match every investigation
    unconditionally, which is never a useful or intentional rule, and is rejected at load time
    rather than silently behaving as "always matches".
    """

    model_config = ConfigDict(extra="forbid")  # an unrecognized operator key is a hard load
    # error, never silently ignored (Phase 0.18 safety requirement)

    evidence_contains: list[str] | None = Field(default=None, max_length=MAX_MATCH_VALUES_PER_RULE)
    #: Case-insensitive substring search over this project's two existing free-text evidence
    #: sources -- bounded container logs and Event ``message`` fields -- never over structured
    #: bookkeeping fields (UIDs, resourceVersions, etc.), which would only produce noisy,
    #: uninterpretable matches. See ``matcher.py``'s ``_textual_evidence_items``.

    event_reason: str | None = None
    #: Exact match (case-sensitive, matching the real Kubernetes field) against any collected
    #: Event's own ``reason`` (e.g. ``"FailedMount"``, ``"BackOff"``).

    container_waiting_reason: str | None = None
    #: Exact match against any container's ``status.containerStatuses[].state.waiting.reason``
    #: on the investigated Pod (e.g. ``"CreateContainerConfigError"``) -- the same field R1-R4
    #: already read, reused here rather than re-derived.

    @field_validator("evidence_contains")
    @classmethod
    def _bounded_nonempty_substrings(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            for s in value:
                if not s or len(s) > MAX_STRING_LENGTH:
                    raise ValueError(
                        f"evidence_contains entries must be 1-{MAX_STRING_LENGTH} characters, "
                        f"never empty"
                    )
        return value

    @model_validator(mode="after")
    def _at_least_one_operator(self) -> "RuleMatch":
        if not any((self.evidence_contains, self.event_reason, self.container_waiting_reason)):
            raise ValueError(
                "a rule's 'match' block must set at least one operator -- evidence_contains, "
                "event_reason, or container_waiting_reason"
            )
        return self


class RuleGuidance(BaseModel):
    """Team-authored prose and (optionally) a runbook reference -- never evidence, never
    validated against the bundle, never executed. ``runbook`` is resolved and safety-checked
    by ``loader.py`` (relative to the pack's own directory; traversal and symlink escapes
    rejected; the file must actually exist) before this model is ever trusted."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=MAX_STRING_LENGTH)
    next_checks: list[str] = Field(default_factory=list, max_length=MAX_NEXT_CHECKS_PER_RULE)
    runbook: str | None = Field(default=None, max_length=1000)

    @field_validator("summary", mode="before")
    @classmethod
    def _strip_summary(cls, value: str) -> str:
        # YAML's folded/literal block scalars ('>'/'|') always keep one trailing newline --
        # stripped here so rendering never shows a stray blank line regardless of which
        # scalar style the pack's author used.
        return value.strip() if isinstance(value, str) else value

    @field_validator("next_checks")
    @classmethod
    def _bounded_nonempty_checks(cls, value: list[str]) -> list[str]:
        for s in value:
            if not s or len(s) > MAX_STRING_LENGTH:
                raise ValueError(f"next_checks entries must be 1-{MAX_STRING_LENGTH} characters, never empty")
        return value


class KnowledgeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200, pattern=_RULE_ID_PATTERN)
    description: str = Field(min_length=1, max_length=MAX_STRING_LENGTH)
    match: RuleMatch
    guidance: RuleGuidance

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class KnowledgePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    rules: list[KnowledgeRule] = Field(default_factory=list, max_length=MAX_RULES)

    @field_validator("version")
    @classmethod
    def _supported_version_only(cls, value: int) -> int:
        if value != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported knowledge-pack schema version {value!r} -- this version of kai "
                f"only understands version {SUPPORTED_SCHEMA_VERSION}"
            )
        return value

    @model_validator(mode="after")
    def _unique_rule_ids(self) -> "KnowledgePack":
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate rule id(s) in knowledge pack: {dupes}")
        return self
