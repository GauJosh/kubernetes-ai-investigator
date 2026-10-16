# Contributing to kai (Kubernetes AI Investigator)

Thanks for considering a contribution. This project is young and the fastest way to have a
change accepted is to keep it small, tested, and aligned with the principles below.

## Before you start

- Open an issue or discussion first for anything beyond a small fix — especially a new
  deterministic rule, a new knowledge-pack match operator, or anything touching the AI
  hypothesis layer. This project deliberately keeps a small, closed set of behaviors (see
  "What this tool does not do" in the README); a PR that expands that surface without prior
  discussion is likely to need rework.
- This project is read-only by design. A contribution must not add any capability to mutate,
  create, delete, or `exec`/`attach`/`port-forward` into a Kubernetes resource.

## Development setup

```bash
git clone https://github.com/GauJosh/kubernetes-ai-investigator.git
cd kubernetes-ai-investigator
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Running the tests

```bash
pytest
```

The full suite runs entirely offline against synthetic `EvidenceBundle` fixtures and a fake
Kubernetes client (`tests/fakes.py`) — no live cluster, no AI-provider API key, and no network
access are needed or used. A PR's tests must keep this property: no new test may require a
live cluster or a real AI-provider call.

## What a good PR looks like

- Every new deterministic finding or AI hypothesis cites the specific evidence it came from
  (an `EvidenceLocator`) — see `src/k8s_investigator/rules/base.py` for the existing helpers.
- New behavior ships with new tests under `tests/`, using the existing fixture/scenario
  conventions in `tests/fixtures/`.
- Deterministic findings, team-defined knowledge-pack guidance, and AI hypotheses stay
  distinct — see the README's "Custom knowledge packs" and "Enabling AI hypotheses" sections
  for why this separation matters and must be preserved.
- No hardcoded test counts or other numbers in documentation that will silently go stale —
  prefer describing behavior over citing a specific count.
- Run the full test suite locally before opening a PR; CI (`.github/workflows/ci.yml`) runs it
  again across the supported Python/OS matrix.

## Reporting a security issue

See [SECURITY.md](SECURITY.md) — please do not open a public issue for a suspected
vulnerability.
