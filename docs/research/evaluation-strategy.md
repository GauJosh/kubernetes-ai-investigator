# Evaluation Strategy

**Phase:** 0.5 — Validate the opportunity and resolve critical unknowns
**Date:** 2026-10-13
**Scope:** Investigation 5. Proposes a **small initial evaluation suite**, not a benchmarking
platform. No application code is implemented here.

Evidence grading follows `docs/research/landscape-research.md` §2.

---

## 1. Can Cloud-OpsBench or ITBench serve as an initial benchmark?

### 1.1 Licensing and practical reuse — verified this phase, not assumed

| Dataset | License | Verification |
|---|---|---|
| **Cloud-OpsBench** | **MIT**, real `LICENSE` file present | `gh api repos/LLM4Ops/Cloud-OpsBench/contents/LICENSE` returns an actual file (decoded: standard MIT text, copyright 2026 Yilun Wang) `[S]`. Freely reusable, including for a project with undecided future licensing. |
| **ITBench** (framework + evaluation code) | **Apache-2.0** | `gh api repos/IBM/ITBench` → `"lic":"Apache-2.0"` `[D]`. |
| **ITBench-Lite** (dataset) | **Apache-2.0** per the HuggingFace dataset card `[D]` | No additional usage restriction found on the card. |

Both are safe to reuse. This stands in deliberate contrast to the auditable-graph paper's own
artifact repository, which **states** an Apache-2.0 intent in its README but ships **no actual
`LICENSE` file** (`gap-analysis.md` §2.2) — a reminder to verify the file, not just the README
prose, before relying on any dataset or tool.

### 1.2 Accessibility of datasets and evaluation code

- **Cloud-OpsBench**: fully public on GitHub, no gating, no account required. 754 cases across two
  systems (Online Boutique 550, Train-Ticket 204), MIT. Ships reference agents, golden
  trajectories, and an MCP server (Phase 0 §5.1).
- **ITBench / ITBench-Lite**: framework public on GitHub (Apache-2.0); dataset public on
  HuggingFace (Apache-2.0), 31 GB total across SRE/FinOps/CISO, with a dedicated 35-scenario SRE
  subset containing `k8s_objects_raw.tsv`, `k8s_events_raw.tsv`, traces, logs, and metrics `[D]`.
  The evaluation/judge harness (`ITBench-Evaluations`) is the same one the auditable-graph paper
  wraps (`gap-analysis.md` §2.2) — it requires a configured LLM judge to score free-text output,
  which is an external, non-deterministic dependency this project would be taking on, not a
  deterministic scoring function.
- **Published leaderboard numbers exist for calibration** (`IBM/ITBench`, `LEADERBOARD_SRE.md`,
  `[D]`, checked this phase): single-trial agents score **0.14–0.33** NTAM fault-localization and
  **0–25%** resolution — confirming the task is genuinely hard and giving an external anchor
  independent of this project's own future numbers.

### 1.3 Alignment with a Kubernetes-API-evidence-first investigator

This is the crux question and the answer is **partial, not full**, for both:

- **Cloud-OpsBench** `raw_data/` ships four files per case: `k8s_states.json`, `logs.json`,
  `alert.json`, `metrics.csv` (Phase 0 §5.1, `[D]`). Only `k8s_states.json` (and, for events
  specifically, whatever `k8s_states.json` or the alert stream carries) is pure Kubernetes-API
  evidence in the sense `CLAUDE.md` scopes (`investigate pod/<name>`: status, container states,
  restarts, events, ownership). **Whether a given Cloud-OpsBench fault case is solvable from
  `k8s_states.json` + logs alone, without `metrics.csv`/`alert.json`, has not been verified this
  phase** — it is an open, cheap, and important thing to check before relying on this dataset as a
  primary MVP benchmark (§4 below proposes exactly this check as the first evaluation task).
- **ITBench SRE** scenarios likewise bundle `k8s_objects_raw.tsv`/`k8s_events_raw.tsv` alongside
  traces/logs/metrics, and the auditable-graph paper's own reasoning explicitly leans on
  Prometheus and Jaeger evidence, not Kubernetes-API evidence alone (`gap-analysis.md` §2.3). Using
  ITBench scenarios that require trace/metric correlation would silently expand this project's
  scope beyond `CLAUDE.md`'s stated MVP.
- **By fault taxonomy, not just by data file**, a meaningful share of Cloud-OpsBench is plausibly
  K8s-API-resolvable: the **Scheduling** (164 cases: node affinity/selector/taint/PVC-binding
  mismatches), **Startup** (86 cases: image reference, pull secrets, mount permissions), and part of
  **Runtime** (141 cases, the subset that is probe/resource-limit/zero-replica rather than
  in-application DB faults) categories describe failures that Kubernetes object state, events, and
  container status are *designed* to surface, independent of logs or metrics. **Performance** (76),
  most of **Service Routing** (91), and **Application Code Defect** (98) plausibly need
  metrics/traces/logs to resolve and should not be assumed in scope.

**Conclusion:** Both datasets are usable as *sources of labelled scenarios and as a format
reference*, but neither can be adopted wholesale as "the benchmark" without first partitioning
which specific cases are answerable from Kubernetes-API evidence alone. Doing that partition is
itself the first concrete evaluation task (§4).

### 1.4 Can labels and evidence-acquisition metrics be reused directly?

- **Cloud-OpsBench's ground-truth label format** (`metadata.json`: fault label, namespace, query,
  difficulty, ground-truth diagnosis) is directly reusable as-is for diagnostic-accuracy scoring —
  no judge model needed, it's a label comparison. **This is a meaningfully simpler scoring path
  than ITBench's**, which requires an LLM judge (`gap-analysis.md` §2.5's qwen-plus) to score
  free-text propagation/reasoning quality.
- **Cloud-OpsBench's milestone-based process labels** (`process-label/`, added 2026-07-16, Phase 0
  §5.1) grade *whether the agent acquired the required diagnostic evidence* — this maps directly
  onto this project's "evidence acquisition quality" dimension (§3 below) and is the single most
  directly reusable artifact found in either dataset.
- **ITBench's four judge dimensions** (root-cause entity, propagation chain, fault localization,
  reasoning quality) are a reasonable model for what to *measure*, but reusing them *as scored*
  requires adopting an LLM-judge dependency this project should not take on casually — see §2's
  overfitting discussion and `gap-analysis.md` §2.5's methodology requirements (same-judge-only,
  ablation-tested) if it ever is adopted.

---

## 2. Avoiding overfitting to synthetic fault injection

This is not a hypothetical risk — it is the exact, documented failure mode of the closest prior
art, read in full this phase (`gap-analysis.md` §2.6): the auditable-graph paper's headline F1 gain
collapsed from +30.4pp to about +1.2pp once scenario-specific prompt hints were stripped, and the
retained signal was explicitly diagnosed by its own authors as *"selection of the correct official
injector object in the graph,"* not recovery of a hidden cause from symptoms.

**Concrete mitigations for this project's evaluation suite, directly answering that failure mode:**

1. **Never let the ground-truth-labelled object be trivially the only unusual node in the evidence
   graph.** Cloud-OpsBench and ITBench's ChaosMesh-injected scenarios, like the paper's, can place
   the fault object itself in the evidence (e.g., a visible `PodChaos` CRD). Where this project uses
   such scenarios, it must check — and report — whether the fault object is directly visible in
   the Kubernetes-API evidence it collects, and treat "found the labelled object because it's the
   only odd thing present" as a **distinct, lesser** outcome from "correctly traced symptoms to a
   cause that required correlation to find."
2. **Hold out a prompt/rule-ablated run as a standard practice, not an afterthought** — directly
   adopting the paper's own corrective (`LEVEL_0` ablation). For this project: run the deterministic
   rule layer alone (Investigation 4's strategy 1) against every scenario before adding the AI
   hypothesis layer, and report the *delta*, not just the AI-assisted final score. If the
   deterministic layer alone already solves a scenario, that scenario is not evidence of AI
   capability and must be labelled as such in results.
3. **Add independently authored, manually reviewed scenarios that are not ChaosMesh-shaped.** Fault
   injection tools produce a visible injector artifact by design; real incidents (bad `kubectl
   apply`, an expired image-pull-secret, a typo'd label selector) do not. §4 proposes a small set of
   exactly this kind, built by hand against a local cluster, not generated by a fault-injection
   framework.
4. **Prefer labels defined at the level `CLAUDE.md` actually targets** (the owning
   workload / the specific misconfiguration), not at the level of "which chaos object was injected"
   — this sidesteps the paper's own identified future-work gap (injector-object-visible vs.
   production-realistic, downstream-symptom-only labelling, `gap-analysis.md` §2.6) by not
   inheriting it in the first place, for the scenarios this project authors itself.

---

## 3. Separating evaluation dimensions

Directly required by the brief, and directly justified by Investigation 4's architecture
recommendation (`architecture-alternatives.md` §4.3), which produces outputs of genuinely different
character that a single pass/fail score would blur:

| Dimension | What it measures | How it's scored | Depends on |
|---|---|---|---|
| **Diagnostic accuracy** | Did the final answer name the correct root cause (or correct category, for "no clean single cause" cases)? | Label comparison against ground truth (Cloud-OpsBench-style; no LLM judge needed for this dimension) | Nothing else — can be scored even for a rules-only run |
| **Evidence acquisition quality** | Did the investigation actually collect the evidence needed to support its conclusion, independent of whether the conclusion was right? | Milestone/checklist comparison (Cloud-OpsBench `process-label`-style): for each scenario, a fixed list of "evidence a correct investigation must have looked at" (e.g., container termination reason, the specific event, the owning workload) | A per-scenario evidence checklist, authored alongside the ground-truth label |
| **Evidence traceability** | Does every finding/hypothesis the system outputs actually cite evidence that exists and is relevant, per Investigation 2's model? | **Mechanical** — referential-integrity check (100% automatable, no judge needed) for citation existence; a **small manual spot-check** for citation relevance (Investigation 2 §2.4 — relevance is not fully automatable) | The evidence-correctness schema in `architecture-alternatives.md` §Investigation 2 |
| **Next-step recommendation quality** | Are the suggested next investigation steps useful, safe, and non-redundant with what was already checked? | **Human/manual review**, scored on a small rubric (useful / redundant / unsafe-if-followed / irrelevant) — deliberately *not* folded into diagnostic accuracy | Nothing automatable found in this research; see below |

**Why recommendation quality gets its own, lower-rigor evaluation track:** ARGUS
(`landscape-research.md` §5.4, `[P]`) reported a *"diagnostic/prescriptive asymmetry"* —
practitioners trusted the system's identification of *what failed* far more than its *recommended
remediation actions*. This project is read-only by `CLAUDE.md` mandate, so "recommendation quality"
here means *recommended next investigation steps*, not remediation — but the lesson generalizes:
**do not let a good diagnostic-accuracy score imply the recommendations are good; they need their
own, separately reported evaluation**, and a naive automated score for this dimension was not found
to exist anywhere in this research program — it should stay manual-review-based for the MVP rather
than force a metric that doesn't have a credible automated form yet.

---

## 4. Proposed initial evaluation suite (small, not a platform)

**Tier 1 — reused, labelled, cheap to start with (target: ~15–20 cases)**
Select from Cloud-OpsBench's **Scheduling** and **Startup** categories (Easy difficulty, MIT
licensed, 164 + 86 = 250 available cases to choose from) specifically because these are the
categories most plausibly resolvable from `k8s_states.json` + events alone, per §1.3. **First
action under this tier, before building anything else:** take 5–10 cases from each category, strip
`metrics.csv` and `alert.json` from the evidence available to a trial run of just the deterministic
rule layer, and check whether the ground-truth diagnosis is still reachable. This is the cheap
validation step `gap-analysis.md` §7 names as the condition that could change the "proceed"
recommendation — it belongs here, as the literal first thing this evaluation suite does.

**Tier 2 — independently authored, manually reviewed (target: 8–12 cases)**
Built by hand against a local cluster (kind/minikube), each covering one of `CLAUDE.md`'s named MVP
evidence types (pod status, container state, restart count, events, logs, ownership) and
deliberately **not** shaped like a fault-injection artifact (§2 point 3):
- A `CrashLoopBackOff` from a genuine application bug (not an injected chaos fault).
- An `ImagePullBackOff` from a typo'd image tag.
- A pod `Pending` from an unsatisfiable resource request, with the owning Deployment misconfigured
  directly (not via a chaos CRD).
- A readiness-probe failure with a healthy-looking `Running` phase (the exact case K8sGPT's pod
  analyzer only catches via a single latest event, `landscape-research.md` §4.1 — a good
  differential test).
- At least two **negative controls**: a genuinely healthy pod (correct answer: "no issue found"),
  and a pod with **expired events** (collected >1h after symptom onset, per the verified TTL,
  `landscape-research.md` §6.1) where the correct answer names the evidence gap explicitly rather
  than guessing.

**Tier 3 — held-out ablation check (not new scenarios, a required practice)**
For every case in Tiers 1–2, report the deterministic-rules-only score **and** the
AI-hypothesis-assisted score, never only the latter — directly operationalizing §2 point 2.

**Explicitly out of scope for the initial suite:** Performance, Service Routing, and Application
Code Defect categories (need metrics/traces this project's MVP does not collect); multi-cluster or
multi-namespace scenarios; anything requiring the ITBench LLM-judge harness (deferred until the
project has its own diagnostic-accuracy results to decide whether that dependency is worth taking
on).

### Acceptance criteria for the MVP (proposed, not finalized)

- **Diagnostic accuracy:** correct root-cause category on **all negative controls** (no false
  positives on the healthy pod) is a hard gate — a system that invents problems on a healthy pod has
  failed regardless of its score elsewhere.
- **Evidence traceability:** **100%** referential integrity (every citation resolves) is a hard
  gate — this is Investigation 2's one fully-mechanical property; anything less is a shipped bug,
  not a tuning issue.
- **Evidence acquisition quality and diagnostic accuracy on Tier 1/2 cases:** no numeric target
  proposed yet — the published ITBench-SRE leaderboard (0.14–0.33, §1.2) and the auditable-graph
  paper's ablated, benchmark-honest number (~0.70 F1 on its harder, three-way subset,
  `gap-analysis.md` §2.5) are the external calibration points; a first MVP result should be reported
  against both without claiming to beat either until compared under matching conditions (same
  scenario subset, same scoring method) — exactly the discipline `gap-analysis.md` §2.5 extracted
  from the paper's own methodology section.
- **Next-step recommendation quality:** no numeric target — manual rubric review only, reported
  qualitatively per `gap-analysis.md` §7's constraint against over-claiming this dimension.
