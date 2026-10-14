"""The hypothesis layer's narrow boundary to a model.

Mirrors ``collector.client.KubernetesClient`` exactly: a structural Protocol with one method,
so the orchestration code in ``ai.hypothesis`` cannot call anything beyond "give me a raw
response for this input" -- no tool use, no multi-turn state, no agentic loop, and no way for
the model to request more evidence than it was already given
(docs/phase-0.11-ai-hypothesis-layer-design.md §4).

No real implementation of this Protocol exists in this phase -- only this interface and a fake
test double (``tests/fakes.py::FakeHypothesisModelClient``), per the Phase 0.11 instruction to
finish the offline implementation before proposing real-model integration.
"""

from __future__ import annotations

from typing import Any, Protocol


class HypothesisModelClient(Protocol):
    """One method: given ``ai.model_input.build_model_input``'s output, return the model's raw
    response text -- expected to be a JSON array of candidate objects matching
    ``ai.hypothesis.RawHypothesisCandidate`` (parsed and validated by that module, never
    trusted directly)."""

    def generate(self, model_input: dict[str, Any]) -> str: ...
