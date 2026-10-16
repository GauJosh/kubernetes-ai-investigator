---
name: Bug report
about: Something in kai behaved incorrectly
title: ""
labels: bug
---

**What happened**

**What you expected instead**

**How to reproduce**

A minimal, offline reproduction is strongly preferred — most of this project's own behavior
depends only on a Pod's collected evidence, not a live cluster. If you can, attach or describe
a synthetic/sanitized `EvidenceBundle` (never a real one containing cluster data) and the exact
`kai` command you ran, e.g.:

```bash
kai pod demo-pod -n demo-ns --from-bundle your-bundle.json
```

If reproducing requires a live cluster, describe the Pod's state (phase, container statuses,
relevant events) as specifically as you can instead of pasting raw cluster output.

**Environment**

- `kai` version / commit:
- Python version:
- OS:
- Installed extras (`[ai]`? none?):

**Did this involve `--with-ai`?**

If yes, note that this report should not include the AI provider's raw response or any
evidence sent to it — describe the observed vs. expected deterministic behavior instead.
