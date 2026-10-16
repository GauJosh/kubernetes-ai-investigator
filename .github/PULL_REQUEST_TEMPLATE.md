## What does this change and why

## Checklist

- [ ] `pytest` passes locally (`pip install -e ".[dev]"` then `pytest`)
- [ ] `ruff check src tests` passes locally
- [ ] New/changed behavior has test coverage under `tests/`, using the existing
      `tests/fixtures/` scenario conventions where applicable
- [ ] No new code path mutates, creates, deletes, or `exec`s/`attach`es/`port-forward`s into a
      Kubernetes resource
- [ ] No new test requires a live cluster or a real AI-provider API call
- [ ] Deterministic findings, custom-knowledge guidance, and AI hypotheses remain distinct (if
      this PR touches any of `rules/`, `knowledge/`, or `ai/`)
- [ ] Documentation (README and/or docstrings) updated if user-facing behavior changed

## Related issue(s)
