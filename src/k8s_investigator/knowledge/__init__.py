"""Custom knowledge packs (Phase 0.18): team-defined YAML rules and Markdown runbooks,
evaluated deterministically against already-collected evidence -- entirely offline, with AI
fully disabled. See ``models.py`` for the schema, ``loader.py`` for loading and the safety
checks applied to this untrusted, user-supplied input, and ``matcher.py`` for how a rule's
``match`` block is evaluated against a real ``EvidenceBundle``.

Deliberately separate from ``rules/`` (the built-in, code-reviewed R1-R11 rule bank) and from
``ai/`` (the optional AI-hypothesis layer) -- a knowledge-pack match is its own
``FindingKind.CUSTOM_GUIDANCE``, never merged into either, and this package has no import
relationship with ``ai/`` at all: nothing here is, or is ever passed to, an AI prompt.
"""
