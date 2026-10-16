# Architecture Alternatives

**Phase:** 0.5 — Validate the opportunity and resolve critical unknowns
**Date:** 2026-10-13
**Scope:** Investigations 2, 3, and 4 from the Phase 0.5 brief. No application code. No cluster
access. No architecture is finalized here — this document compares alternatives and recommends,
per `CLAUDE.md`'s workflow requirement ("compare architectural alternatives… explain alternatives
and tradeoffs").

Evidence grading follows `docs/research/landscape-research.md` §2: **[S]** source-inspected,
**[D]** primary documentation, **[C]** vendor claim, **[P]** preprint abstract-level, **[I]**
inference.

---

## Investigation 2 — Enforceable evidence correctness

### 2.1 Why this comes first

Every architecture choice in Investigations 3 and 4 is downstream of one question: **what, exactly,
can be checked by code, and what can only be requested of a model?** Getting this wrong in either
direction is a real failure mode — over-claiming mechanical enforcement produces false confidence;
under-using it wastes the one layer that doesn't depend on model quality (and `docs/gap-analysis.md`
§2.6 documents that model quality is reported not to fix the relevant failure modes anyway).

### 2.2 A minimal typed model

Pseudocode only — types, not an implementation:

```
EvidenceItem:
  id: str                     # content-addressed, e.g. hash of (source_ref, collected_at, payload)
  source_ref: SourceRef        # see below — must resolve to something re-fetchable/auditable
  collected_at: datetime
  payload: JSON | redacted-excerpt
  status: EvidenceStatus        # PRESENT | NOT_FOUND | UNAVAILABLE | EXPIRED | STALE

SourceRef:                      # discriminated union — every item must carry ONE of:
  ObjectRef(group, version, kind, namespace, name, resourceVersion?)
  ApiCallRef(verb, path, params, response_status)

EvidenceQuery:                  # the *attempt* to collect, distinct from what was found
  id: str
  requested: SourceRef | SourceSelector   # a selector when "did this exist at all" is itself the question
  requested_at: datetime
  result: EvidenceStatus
  reason: str?                  # e.g. "events API returned none (TTL ~1h; incident was 3h ago)"

Finding:
  id: str
  kind: OBSERVED_FACT | DETERMINISTIC_FINDING | AI_HYPOTHESIS   # closed enum — CLAUDE.md principle 2
  statement: str
  evidence_ids: list[EvidenceItem.id]     # MUST be non-empty for DETERMINISTIC_FINDING
  produced_by: RuleId | ModelCallId
  confidence: Categorical?                # AI_HYPOTHESIS only; never a bare float presented as precise
  contradicts: list[Finding.id]?

ContradictionEdge:
  evidence_a: EvidenceItem.id
  evidence_b: EvidenceItem.id
  rule_id: str                  # which comparator detected this — contradiction detection is rule-bank-bounded, not generic
  description: str

EvidenceBundle:                 # the unit that makes a run reproducible
  id: str                       # hash of all EvidenceItem ids + collection parameters
  investigation_target: ObjectRef
  collected_at_range: (datetime, datetime)
  items: list[EvidenceItem]
  queries: list[EvidenceQuery]   # includes queries that found nothing — the absence is evidence too
```

### 2.3 What each required property maps to, and what class of check it is

| Required property | Mechanism | Check class |
|---|---|---|
| Every finding references evidence IDs that actually exist | `Finding.evidence_ids ⊆ EvidenceBundle.items[*].id` — a referential-integrity check, identical in kind to a foreign-key constraint | **Fully mechanical.** Trivial to unit-test: construct a `Finding` with a fabricated id, assert rejection. |
| Evidence references identify the source object/observation | `SourceRef` is a required, non-null discriminated union on every `EvidenceItem`; schema validation rejects an item without one | **Fully mechanical** (schema-level), but only proves *an* identifier exists, not that it's the *right* one — see 2.4. |
| Unsupported claims can be detected or rejected | `DETERMINISTIC_FINDING` with empty `evidence_ids` is a schema violation, rejected before it is ever surfaced. `AI_HYPOTHESIS` with empty `evidence_ids` is accepted but **flagged** as ungrounded — it cannot be silently treated as evidence-backed | **Mechanical for deterministic findings. Partial for AI hypotheses** — emptiness is mechanical to detect; *relevance* of non-empty citations is not (2.4). |
| Missing, unavailable, stale, contradictory evidence are distinct states | `EvidenceStatus` enum (`NOT_FOUND` / `UNAVAILABLE` / `EXPIRED` / `STALE`) lives on the *query*, not just the item, because "missing" is a property of an attempt, not an object; `ContradictionEdge` is a separate relation between two *present* items, never conflated with absence | **Fully mechanical to represent** (it's just a type system decision — the gap identified in `gap-analysis.md` G5 is that nobody has *built* this, not that it's hard to build). **Detecting** staleness/contradiction is rule-bounded (2.4). |
| Hypotheses are distinguishable from observed facts | Closed `kind` enum, never a free-text label or a markdown convention | **Fully mechanical.** A renderer/API that cannot express an unlabeled claim cannot ship one. |
| Deterministic findings reproduce from identical evidence | Contract: a `DETERMINISTIC_FINDING`-producing rule must be a **pure function** of `EvidenceBundle` — no network I/O, no wall-clock reads beyond what's inside the bundle, no randomness | **Fully mechanical and testable in CI**: hash the bundle, run the rule N times, assert identical output hash. This is the one property in this table that is provably, not just practically, enforceable. |
| AI conclusions can be checked against the evidence they cite | Two tiers: (a) **referential** — citation exists (mechanical, as above); (b) **grounding** — the cited evidence's content plausibly supports the claim | (a) mechanical. (b) **irreducibly partial** — see 2.4. |

### 2.4 Where mechanical enforcement stops, precisely

This is the honest boundary, not a hand-wave:

1. **Citation existence vs. citation relevance.** Code can prove a hypothesis cites real evidence.
   It cannot prove, in general, that the cited evidence *actually supports* the specific claim made
   about it — that is a natural-language entailment problem. The auditable-graph paper's own
   `evidence grounding` check only gets partway: it checks that *a* concrete record exists for the
   cited fault signal, not that the hypothesis's prose is an accurate characterization of that
   record. **Mitigation, not a solution:** a bounded bank of deterministic cross-checks (the
   paper's three: temporal precedence, evidence grounding, cross-modal consistency) catches *known*
   contradiction shapes. New failure shapes slip through uncaught — this is a closed-world
   limitation shared by every system surveyed, including this project's eventual one.
2. **Contradiction detection is enumerable, not generic.** `ContradictionEdge` requires a
   `rule_id` because "these two facts disagree" is domain knowledge (e.g., "container state
   `Terminated` and container state `Running` for the same container+time window is a
   contradiction" is a rule someone has to write). A generic contradiction detector over arbitrary
   Kubernetes evidence does not exist and this project will not build one in the MVP; it will build
   a small, named, growable rule bank, and anything outside that bank is unflagged.
3. **Hypothesis ranking and plausibility remain probabilistic by nature**, not merely by
   current limitation. "Which of three evidence-grounded hypotheses is most likely" is a judgment
   call even for a human SRE; a categorical confidence label is honest, a precise probability is
   false precision.
4. **Completeness of the deterministic rule bank is itself unverifiable from inside the system.**
   A rule bank can be tested against *known* scenarios (Investigation 5) but cannot certify it has
   no blind spots against unknown ones. This should be stated in every investigation's output, not
   buried in documentation — it is the mechanical analogue of the paper's Table II discipline
   (`gap-analysis.md` §6 point 4).
5. **LLM-as-judge evaluation (if used at all) inherits everything in `gap-analysis.md` §2.5–2.6:**
   non-deterministic, must be same-judge-only across comparisons, and is a scoring convenience for
   evaluation, never a substitute for the mechanical checks above at investigation time.

### 2.5 Summary position

The evidence-correctness model above is not a novel invention — it is a direct generalization of
what the auditable-graph paper already does ad hoc (§2.3 deterministic validator) into a persisted,
typed, unit-testable schema, closing `gap-analysis.md` G2 and G5 together. The mechanically
enforceable core is: referential integrity, non-null source identification, closed fact/hypothesis
typing, and reproducibility of deterministic findings. Everything past that — relevance of a
citation, completeness of contradiction rules, hypothesis ranking — is probabilistic and must be
labeled as such in the output, not resolved away.

---

## Investigation 3 — Live queries vs. frozen evidence bundles vs. hybrid

### 3.1 The three approaches, and what was actually inspected

- **A. Live:** query the Kubernetes API (and optionally logs/metrics/traces) directly during the
  investigation. Represented by HolmesGPT (`landscape-research.md` §4.2) and kubectl-ai.
- **B. Frozen snapshot:** collect a bounded evidence bundle first, then investigate only that
  bundle. Represented by **Troubleshoot.sh** (support bundles, `[S]` inspected in Phase 0) and
  **Cloud-OpsBench** (`metadata.json` + `tool_cache.json` + `raw_data/{k8s_states.json, logs.json,
  alert.json, metrics.csv}`, `[D]` inspected in Phase 0).
- **C. Hybrid:** an initial snapshot plus explicitly recorded additional collection steps taken
  during the investigation. Represented, concretely and inspected in code this phase, by the
  auditable-graph paper's **`TopologyBackend` protocol** (`[S]`, `orchestration/graph_builder.py`):
  a single interface (`list_workloads`, `list_pods`, `list_services`, `list_configmaps`,
  `list_pvcs`, `list_nodes`, `list_events`, `list_crds`, `get_call_edges`) with two interchangeable
  implementations — `LiveK8sBackend` (real cluster) and a snapshot-reading backend
  (`tools/snapshot_backend.py`, confirmed reading ITBench TSVs) — swapped by one environment
  variable, with the *same* graph-construction and agent code running against either.

### 3.2 Comparison

| Dimension | A. Live | B. Frozen snapshot | C. Hybrid |
|---|---|---|---|
| **Freshness** | Best — always current cluster state | Worst — a snapshot ages the moment it's taken; Kubernetes state (and especially Events, TTL ~1h, Phase 0 §6.1) can be gone by the time anyone reads it | Good — starts fresh, can re-query for specifics mid-investigation |
| **Reproducibility** | **None by construction** — re-running against a live cluster, even seconds later, can see different state. `gap-analysis.md` §2.5 confirms even the research prototype's live trials were unusable for scoring for this exact reason | **Full, by construction** — identical bundle in, identical findings out is testable in CI (Investigation 2 §2.3) | **Partial** — the initial bundle is reproducible; any additional recorded collection step is itself logged and replayable only if it, too, is captured as a typed `EvidenceQuery`/`EvidenceItem` (Investigation 2) |
| **Latency** | Depends on cluster/API responsiveness per call; can be slow under load or with large clusters (confirmed concern HolmesGPT engineers for — pagination, `label_selector` scoping, `[S]` Phase 0 §4.2) | Fast after collection; collection itself is a bounded, parallelizable burst (the paper's `LiveK8sBackend` uses `asyncio.gather` across all layers, `[S]`) | Same as B for the initial pass; A's latency profile for each follow-up query |
| **Permissions** | Needs standing read access for the whole investigation's duration, including whatever a reasoning loop decides to ask for — harder to scope tightly in advance | Needs read access only during a bounded, enumerable collection step — easiest to express as a narrow, auditable ServiceAccount/RBAC grant (aligns directly with `CLAUDE.md`'s least-privilege requirement) | Needs a narrow grant for the snapshot plus an explicit, smaller allow-list for follow-ups — still far narrower than A |
| **Data size** | No storage concern, but no bound on how much a reasoning loop queries over a long investigation | Bounded and known at collection time; Cloud-OpsBench and Troubleshoot.sh bundles are explicitly sized artifacts | Bounded for the base case; follow-ups add a small, logged increment |
| **Missing evidence (TTL, rotation)** | Suffers in real time — if events expired before the investigation started, live querying will not recover them either; A has **no advantage over B** here, because the data is simply gone from the API regardless of query timing | Same loss, but the loss is **visible and timestamped** (the bundle records when collection happened and what was/wasn't found) rather than discovered mid-reasoning | Same visibility as B for the base bundle; any follow-up query against a still-missing resource surfaces the same `NOT_FOUND`/`EXPIRED` state explicitly |
| **Historical/timeline limitations** | Worst — repeated live queries during one investigation can observe *different* states of the same object (a pod restarts mid-investigation) without that change being tracked as a change | Best within its single point in time — nothing moves under the investigation while it runs | Base bundle is stable; follow-ups must be timestamped and diffed against the base explicitly or the same problem as A reappears for just those queries |
| **Testing** | Hard — requires a live or faithfully mocked cluster for every test; test flakiness from real cluster timing is a known class of problem | **Easy** — a fixed bundle is a test fixture; this is exactly how Cloud-OpsBench and the auditable-graph paper's own reproducibility (§2.2 of `gap-analysis.md`) work | Easy for the base-bundle path; follow-up logic needs its own fixtures (a small set of "what if the agent asks for X next" cases) but is still boundedly testable |
| **Engineering complexity** | Lowest to start (no bundle format to design), highest to make reliable (the paper's own live-trial failures were engineering failures: truncated output, orphaned port-forward, stale cache — `gap-analysis.md` §5 point 3) | Low — collect, store, analyze offline; Troubleshoot.sh has done this since 2019 with no AI involved | Highest design cost up front (need a clean backend abstraction, as the paper shows is doable) but pays for itself in testability |

### 3.3 Recommendation for the MVP

**Frozen snapshot (B) for the MVP, with the backend abstraction built from day one so hybrid (C) is
a non-breaking later addition — not live (A).**

Reasons:

1. **Reproducibility is the project's primary differentiator** (`gap-analysis.md` §6 point 3). Live
   querying forecloses it by construction; this is not a tradeoff to balance, it is a direct
   contradiction of the stated goal.
2. **Testability compounds.** Every later investigation (correlation rules, Investigation 2's
   mechanical checks, evaluation scenarios in Investigation 5) is dramatically easier to write and
   CI-test against a fixed bundle than against a live or mocked-live cluster. This is the single
   biggest lever on near-term engineering velocity for a solo project (`CLAUDE.md` principle 5:
   "prefer simple, testable components").
3. **Least-privilege is easiest to express and audit for a bounded collection step** — a
   time-boxed `investigate pod/<name>` collection pass needs a narrower, more reviewable RBAC grant
   than an open-ended reasoning loop that can query anything, whenever it wants, for however long
   the investigation runs.
4. **Precedent exists at both ends of this project's reference set**: Troubleshoot.sh (pure
   deterministic tooling) and the auditable-graph paper (LLM-driven) both reach for a swappable,
   snapshot-capable backend — this is not an unusual or academic choice, it is what both the
   simplest and most sophisticated systems surveyed actually do.

**Acknowledged tradeoffs, stated rather than hidden:**

- A frozen bundle **cannot** recover evidence that expired before collection ran (events gone after
  ~1h, Phase 0 §6.1) — but live querying has the identical limitation for the identical reason
  (the evidence is gone from the API, not merely un-fetched), so this is not actually a cost of
  choosing B over A. It is a cost of starting an investigation late, which no architecture fixes.
- A single frozen bundle **cannot** answer a question the initial collection didn't anticipate
  (e.g., "what about this ConfigMap mounted by the pod?" if ConfigMaps weren't collected). This is
  the real argument for building toward C: design `EvidenceQuery` (Investigation 2) so that a
  follow-up collection step is just another typed, logged, bundle-extending operation, not an
  architectural rewrite. **Do not build C's follow-up-query machinery in the MVP** — build the
  seam for it (the backend-protocol pattern the paper demonstrates) so it isn't a rewrite later.
- Freshness is genuinely worse than live for a cluster under rapid change. This matters most for
  the failure categories that resolve themselves quickly (transient scheduling retries). It matters
  least for the categories `CLAUDE.md`'s initial scope targets (pod status, restarts, ownership) —
  these tend to be stable enough within an investigation's timeframe for a snapshot to be
  representative. If this assumption proves wrong in Investigation 5's scenario testing, that is a
  reason to revisit, not a reason to default to A now.

---

## Investigation 4 — Investigation strategy: rules / causal inference / agentic / hybrid

### 4.1 The four strategies, grounded in inspected systems

1. **Deterministic rules over a typed relationship graph.** Build the graph (Investigation 3/
   Investigation 2's `SourceRef`-identified nodes and typed edges — `owns`/`hosts`/`selects`/
   `mounts`/`fronts`/`calls`, directly reusable from the paper, `[S]`), then run hand-written rules
   over it (à la K8sGPT's per-kind checks, `[S]`, but with correlation the graph adds that K8sGPT
   lacks). No LLM required for diagnosis; an LLM, if used at all, only narrates the rule's output
   (K8sGPT's actual role, `[S]`, Phase 0 §4.1).
2. **Probabilistic causal inference.** Build a topology graph, instantiate a library of causal
   models against it to produce a probability-weighted causality DAG (Causely's architecture,
   `[D]`, Phase 0 §4.12) — reasoning is a graph/probability computation, not natural-language
   inference, and (per the one page inspected) involves **no LLM at all**.
3. **Agentic LLM tool-calling.** An LLM plans which evidence to collect and interprets it in a loop
   (HolmesGPT `[S]`; the auditable-graph paper's investigator-agent role within its larger
   constrained pipeline `[S]`).
4. **Hybrid.** Deterministic graph construction and validation bound and check an LLM that handles
   interpretation only where correlation/rules run out — this is, concretely, what the
   auditable-graph paper already builds (§2.3 above): deterministic graph + deterministic
   validator + LLM only for triage and ambiguous-node classification.

### 4.2 Comparison

| Dimension | 1. Rules over graph | 2. Causal inference | 3. Agentic LLM | 4. Hybrid |
|---|---|---|---|---|
| **Diagnostic coverage** | Bounded by what rules were written — zero coverage of anything not anticipated (K8sGPT's ~35 analyzers cover common cases, `[S]`, nothing else) | Bounded by the causal-model library's breadth; Causely claims "tens of thousands of causes" `[D]` from combining a model library with topology, but this requires someone to have authored those causal models for the domain in question — same closed-world limit as rules, at larger pre-built scale | Broadest *in principle* — an LLM can reason about a failure mode nobody wrote a rule for. **But** Kim et al. (`gap-analysis.md` §2.6, `[P]`) found hallucinated evidence interpretation and incomplete exploration persist across model tiers — breadth is not free, it comes with a documented, model-tier-independent failure mode | Same bound as 1 for the deterministic core, with LLM reasoning available as a fallback for genuinely ambiguous cases — the paper's own validator existing specifically *because* unconstrained LLM verdicts were not trusted alone (§IV-D, `[S]`) |
| **Behavior on unexpected failures (not anticipated by rules)** | **Fails silently or not at all** — no rule fires, no finding is produced. Honest (nothing false is claimed) but unhelpful | Same failure shape as rules, at the causal-model layer — an un-modeled failure mode produces no causality edges | Can still attempt an answer — this is a double-edged behavior: useful when right, and exactly where hallucination risk concentrates when wrong | Falls back to 3's behavior only for nodes/cases the deterministic layer couldn't classify — bounded exposure to 3's risk, not eliminated |
| **Explainability** | High — a rule firing is a direct, inspectable "if X then Y" | Medium — a probability-weighted DAG edge is explainable in principle but the *library* of causal models is the real source of truth, and that library's content is not shown in the one page inspected, `[D]` | Low by default (free-text explanation, no guaranteed structure) unless constrained — HolmesGPT's `ChatResponse{analysis: str, tool_calls}` is a transcript, not a structured explanation (`[S]`, Phase 0 §4.2) | High for the deterministic portion, same as 3's limitation for the LLM portion — but the LLM portion is smaller and its boundary is explicit |
| **Evidence traceability** | Natural — a rule's inputs are its evidence by construction | Depends on topology/causal-model provenance being surfaced; not confirmed from the one page inspected | Weak by default (prompt-requested, not enforced — `gap-analysis.md` §2.7) unless Investigation 2's model is layered on top | Strong where the deterministic validator runs (the paper's `evidence grounding` check, `[S]`); only as strong as the validator's rule bank where LLM reasoning is used |
| **Reproducibility** | **Full** — a pure function of the graph, directly satisfying Investigation 2 §2.3's reproducibility contract | Full for the probability computation given a fixed topology snapshot; depends on causal-model versioning being tracked, which was not confirmed | **None** without constraints (LLM sampling) | Full for the deterministic path; the LLM path needs the same-judge/ablation discipline (`gap-analysis.md` §2.5) to even evaluate, let alone reproduce |
| **Operational complexity** | **Lowest** — no model hosting, no prompt engineering, no LLM-specific failure modes to manage | Medium-high — requires building and maintaining a causal-model library, which is itself a significant, ongoing authoring effort (this is Causely's core IP, not a side feature) | Medium — needs LLM access/hosting and prompt/tool-schema maintenance, but no bespoke model-authoring effort | Medium-high — needs both the deterministic layer *and* LLM integration, i.e., the union of 1's and 3's operational surface |
| **Evaluation difficulty** | Easiest — deterministic output, straightforward pass/fail against ground truth | Hard to evaluate independently of the causal-model library's own correctness, which is opaque from outside | Hardest — non-deterministic, judge-dependent, benchmark-coupling-prone (exactly `gap-analysis.md` §2.5's lesson) | Same as 3 for the LLM-touched cases; same as 1 for the rest — evaluation must be able to separate the two, which is itself a design requirement (Investigation 5) |
| **Maintenance cost** | Grows linearly with the number of rules; well-understood, low-surprise maintenance (this is what K8sGPT and Troubleshoot.sh have sustained for years, `[S]`) | Grows with the causal-model library and must track Kubernetes/application semantics as they evolve; higher-skill maintenance | Lower code-maintenance burden per se, but carries prompt-drift and model-version-drift risk indefinitely, and (per Kim et al.) upgrading the model does not retire the risk | Sum of 1's rule-maintenance cost and 3's prompt/model-drift risk, but each is smaller in scope than if deployed alone |

### 4.3 Recommendation: the simplest architecture that meets the MVP

**Strategy 4 (hybrid), with the LLM's role deliberately minimized to the smallest slice that
strategies 1/2 cannot cover — not strategy 3 (pure agentic), and not strategy 1 alone.**

Reasoning, directly from the comparison:

- **Strategy 1 alone cannot meet `CLAUDE.md`'s mission** ("evaluates possible causes," "AI-generated
  hypotheses") — a pure rule engine produces deterministic findings only; there is no hypothesis
  generation step without some form of reasoning over what the rules couldn't resolve.
- **Strategy 2 (causal inference) is not ruled out on merit** — it is the most rigorous
  non-LLM alternative found in this research (`landscape-research.md` §4.12) — **but it is ruled out
  on proportionality.** Building and maintaining a causal-model library is a substantial, ongoing
  authoring investment that is disproportionate to a solo-engineer MVP scoped to
  `investigate pod/<name>`. This is the right call for a company the size of Causely; it is not the
  right call here. Revisit only if the rule bank from strategy 1 grows large and brittle enough that
  probability-weighted edges would clearly out-perform a long if/elif chain — not before.
- **Strategy 3 (pure agentic) is ruled out specifically because of §4.2's "unexpected failures" and
  "evaluation difficulty" rows**, which are not abstract concerns — they are exactly what
  `gap-analysis.md` §2.6 found actually happened to the most rigorous agentic system surveyed (most
  of its gain was prompt-tuning; its live trials failed for engineering reasons before reasoning
  quality was even tested).
- **Strategy 4 is simplest among the options that can meet the mission**, not merely the most
  sophisticated: deterministic graph construction and rules do the bulk of the work and are cheap to
  build, test, and reason about (lowest rows in "operational complexity" and "evaluation
  difficulty"); the LLM is scoped to a narrow, bounded role — generating and ranking hypotheses
  **only** over findings the deterministic layer has already produced and evidence-bound, not
  deciding what evidence to collect or interpreting raw cluster state directly. This is a
  meaningfully smaller LLM responsibility than either HolmesGPT's or the auditable-graph paper's
  investigator-agent role, and it is smaller specifically because `CLAUDE.md` principle 1 ("collect
  deterministic evidence before invoking AI reasoning") already settles where the boundary goes —
  this recommendation does not need to invent that boundary, only implement it.

**What this recommendation explicitly defers, not rejects:**
- LLM-driven *evidence collection* (deciding what to fetch next) — Investigation 3 §3.3 already
  recommends against this for the MVP on separate (freshness/reproducibility) grounds; strategy 3's
  risk profile is a second, independent reason not to add it now.
- A causal-model library (strategy 2) — proportionality, not merit, is the objection; revisit if the
  rule bank's maintenance cost crosses over.
- Multi-agent coordination of any kind — not evaluated in the table above because nothing in this
  research program's MVP scope (`investigate pod/<name>`) plausibly needs more than one reasoning
  role, and MAST's findings (`gap-analysis.md` §2.6) are a direct argument against adding
  coordination complexity that isn't needed.

---

## Cross-references

- Evidence-correctness schema (Investigation 2) is the mechanism that makes `gap-analysis.md` §6's
  differentiation claims testable, not aspirational.
- The frozen-bundle recommendation (Investigation 3) is also the recommended fixture format for
  `docs/research/evaluation-strategy.md`'s scenario suite — one design decision serves both needs.
- The hybrid, LLM-minimized strategy (Investigation 4) is what `docs/research/evaluation-strategy.md`
  must evaluate in two separable parts (deterministic findings; AI hypotheses) — see that document's
  "Separating evaluation dimensions" section.
