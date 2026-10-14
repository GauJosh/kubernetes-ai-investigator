"""The AI-hypothesis layer (Phase 0.11).

A one-shot, non-agentic layer: given an already-validated EvidenceBundle and the deterministic
findings already produced over it, one model call proposes AI_HYPOTHESIS candidates, which are
then validated through the exact same evidence-reference gate every other Finding in this
project goes through before being accepted. Nothing in this package calls the Kubernetes API,
executes anything, or runs more than one request/response per investigation.

See docs/phase-0.11-ai-hypothesis-layer-design.md for the full design this implements.
"""

from __future__ import annotations
