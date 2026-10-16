---
name: Feature request
about: Propose a change or addition to kai
title: ""
labels: enhancement
---

**What problem are you trying to solve?**

Describe the investigation scenario or workflow gap, not just the feature itself — this
project prefers solving a demonstrated problem over adding a capability speculatively.

**Proposed change**

**Does this fit the project's existing boundaries?**

Please check the relevant box(es); a proposal that needs one unchecked is still welcome, but
flag it explicitly so it can be discussed before any implementation work starts.

- [ ] Stays read-only (no new mutate/`exec`/`attach`/`port-forward` capability)
- [ ] Does not require a new AI provider or always-on model dependency
- [ ] Fits within the existing deterministic-findings / custom-knowledge-guidance / AI-hypothesis
      separation (see README's "Custom knowledge packs" and "Enabling AI hypotheses" sections)
- [ ] Does not require a general-purpose plugin system or a new expression/query language

**Alternatives considered**
