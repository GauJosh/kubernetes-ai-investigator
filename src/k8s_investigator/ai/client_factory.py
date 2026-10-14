"""Builds a real ``HypothesisModelClient`` from explicit environment configuration.

No credential is ever hard-coded here or anywhere else in this project -- every value this
factory reads comes from the process environment, named explicitly below, and a missing or
unrecognized configuration raises a typed, descriptive error rather than silently falling back
to anything. The caller (``cli.py``) is responsible for turning that error into an honest
``AIHypothesisRunStatus.FAILED`` report, never a silently-empty "successful" one
(docs/phase-0.12-real-model-integration-notes.md).

``AI_HYPOTHESIS_PROVIDER`` selects the provider; each provider's own required variables are
documented in its dispatch branch below and in that provider's own module. ``openai`` is the
only provider implemented so far (``openai_client.py``) -- adding another later means adding
one more ``elif`` branch here and one more adapter module; nothing in ``ai.hypothesis`` or any
caller needs to change, since both speak only the provider-neutral ``HypothesisModelClient``
Protocol.
"""

from __future__ import annotations

import os

from .model_client import HypothesisModelClient


class HypothesisClientConfigurationError(Exception):
    """AI hypothesis generation was requested but no usable client could be configured."""


def build_client_from_env() -> tuple[HypothesisModelClient, str]:
    """Returns ``(client, model_identifier)``. Raises ``HypothesisClientConfigurationError``
    if ``AI_HYPOTHESIS_PROVIDER`` is unset or names a provider with no implementation."""
    provider = os.environ.get("AI_HYPOTHESIS_PROVIDER")
    if not provider:
        raise HypothesisClientConfigurationError(
            "--with-ai-hypotheses was requested but AI_HYPOTHESIS_PROVIDER is not set. Set "
            "AI_HYPOTHESIS_PROVIDER=openai (the only provider implemented so far) and its "
            "required variables, or omit --with-ai-hypotheses for the deterministic-only path."
        )
    if provider == "openai":
        from .openai_client import build_openai_client_from_env

        return build_openai_client_from_env()
    raise HypothesisClientConfigurationError(
        f"AI_HYPOTHESIS_PROVIDER={provider!r} is not a recognized or implemented provider."
    )
