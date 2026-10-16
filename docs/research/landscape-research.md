# Landscape Research: Kubernetes Troubleshooting, Diagnostics, and AI-Assisted Investigation

**Phase:** 0 — Research and discovery
**Date:** 2026-10-13
**Status:** Landscape research only. No gap analysis conclusions, architecture decisions, or MVP commitments are made here.

---

## 1. Purpose and scope

This document establishes what already exists in the Kubernetes troubleshooting and AI-assisted
investigation space, so that any later claim of differentiation for this project is grounded in
evidence rather than assumption.

For each significant candidate the research tried to answer four questions:

1. What does it actually do?
2. How does it gather evidence?
3. Does it correlate Kubernetes objects?
4. How does it use AI, if at all?

### Explicitly out of scope for this document

- Gap analysis and differentiation strategy (next phase).
- Architecture proposal and MVP scope decisions (next phase).
- Any implementation.

---

## 2. Method and evidence grading

Research was conducted by direct retrieval: GitHub API metadata, raw source files read from the
projects' default branches, vendor documentation, and arXiv abstracts. No Kubernetes cluster was
inspected and no commercial product was exercised hands-on.

Every finding below carries a grade. **Read the grades — they are the point.**

| Grade | Meaning |
|---|---|
| **[S]** | **Source-inspected.** I read the actual source file and am describing what the code does. |
| **[D]** | **Primary documentation.** Stated in the project's or vendor's own docs/README. |
| **[C]** | **Vendor claim.** Marketing or vendor-authored blog assertion. Not independently verified. |
| **[P]** | **Preprint claim.** From an arXiv abstract. Full papers were not read; results are self-reported and mostly not independently replicated. |
| **[I]** | **My inference.** Reasoned from the above, not directly stated anywhere. Treat as a hypothesis. |

### Known limitations of this research

- **Commercial platforms are the weakest evidence.** Komodor, Datadog, Causely, Cleric, Resolve.ai,
  Traversal, and NeuBird were assessed from vendor documentation and press material only. Their
  internal evidence-collection and correlation mechanics are not externally verifiable. Accuracy
  figures they publish (e.g. "82% RCA accuracy") have no published methodology and should be
  treated as unverified.
- One intended primary source, Komodor's own technical comparison blog post, returned **HTTP 403**
  and could not be read. Komodor's architecture claims here come from its other public pages.
- arXiv items were read at **abstract level only**. Reported metrics are authors' own.
- Source inspection was targeted (pod analyzer, prompts, tool definitions, result models), not a
  full audit of any codebase. Conclusions about absent capabilities are bounded by what was read —
  stated explicitly where relevant.
- The AI-SRE vendor market is moving fast. Repository metadata was captured 2026-10-13 and vendor
  capabilities may have changed since the cited pages were published.

---

## 3. Taxonomy of the landscape

The space is not one category. Five distinct groups exist, and they fail in different ways:

| # | Category | What characterises it | Examples |
|---|---|---|---|
| A | **Deterministic rule scanners** | Hard-coded checks over cluster/manifest state. No AI. Reliable, shallow. | Popeye, kube-score, Polaris |
| B | **Deterministic evidence-collection frameworks** | Declarative collectors + analyzers, redaction, portable bundles. No AI. | Troubleshoot.sh, Sloop |
| C | **Deterministic detectors + LLM explainer** | Local rules find the problem; LLM rewrites it in prose. | K8sGPT |
| D | **Agentic LLM investigators** | LLM drives evidence gathering via tools in a loop. | HolmesGPT, kubectl-ai, Datadog Bits, Komodor Klaudia, GKE Gemini Cloud Assist |
| E | **Model-driven causal engines** | Pre-built causal/topology models, probabilistic inference. LLM optional or absent. | Causely, Grafana Sift/Asserts |

Plus **agent platforms** (kagent) and **tool-access layers** (MCP servers) that are infrastructure
rather than diagnostic logic, and an **academic/benchmark** cluster that is the most
methodologically rigorous part of the landscape.

---

## 4. Candidate findings

### 4.1 K8sGPT — Category C

- **Repo:** [k8sgpt-ai/k8sgpt](https://github.com/k8sgpt-ai/k8sgpt) · Apache-2.0 · Go
- **Status [D]:** 8,243 stars; created 2023-03-21; last push 2026-10-13; latest release `v0.4.40`
  (2026-10-09). Very actively maintained. CNCF Sandbox.

**What it does [S]:** Ships ~35 resource-specific analyzers (`pkg/analyzer/`: `pod.go`,
`deployment.go`, `service.go`, `node.go`, `pvc.go`, `hpa.go`, `ingress.go`, `netpol.go`, `pdb.go`,
`gateway.go`, `httproute.go`, CRD/OLM analyzers, security and admission-policy analyzers, etc.).
Each scans one resource kind cluster-wide and emits failure strings.

**How it gathers evidence [S]** — read from
[`pkg/analyzer/pod.go`](https://github.com/k8sgpt-ai/k8sgpt/blob/main/pkg/analyzer/pod.go):

- Lists pods via the typed client, then inspects `pod.Status` fields only: `Phase == "Pending"`,
  `PodScheduled`/`Unschedulable` and `SchedulingGated` conditions, `Status.Reason == "Evicted"`, and
  container/init-container statuses.
- Container state checks match on `State.Waiting.Reason` against a fixed list
  (`CrashLoopBackOff`, `ImagePullBackOff`, `CreateContainerConfigError`, `ErrImagePull`,
  `InvalidImageName`, …), on non-zero `State.Terminated.ExitCode`, and on `!Ready` while `Running`.
- **Events:** only `util.FetchLatestEvent` — a single most-recent event, selected by
  `involvedObject.name/kind/uid`. There is no event timeline, no ordering, no windowing.
- **Logs:** the pod analyzer collects **no logs at all**. A separate `log.go` analyzer exists.
- Output is `common.Failure{Text: string}` — flat prose strings with a `Sensitive` field used for
  anonymisation.

**Object correlation [S]:** Minimal and one-directional. `util.GetParent` walks `OwnerReferences`
recursively (Pod → ReplicaSet → Deployment, StatefulSet, DaemonSet, …) and returns a **single
string** such as `"Deployment/web"`, attached as `Result.ParentObject`. That is the extent of
correlation: no sibling-pod comparison, no Service/Endpoint/PVC/Node/ConfigMap graph, no
cross-analyzer joining. Each analyzer runs in isolation over one kind.

**How it uses AI [S]** — this is the decisive finding, read from
[`pkg/ai/prompts.go`](https://github.com/k8sgpt-ai/k8sgpt/blob/main/pkg/ai/prompts.go):

```
Simplify the following Kubernetes error message delimited by triple dashes; --- %s ---.
Provide the most possible solution in a step by step style in no more than 280 characters.
Write the output in the following format:
Error: {Explain error here}
Solution: {Step by step solution here}
```

The LLM receives **only the already-extracted failure string** and is asked to simplify it in
≤280 characters. It does not see the cluster, choose what to collect, weigh alternatives, or reason
across objects. **K8sGPT's AI is a text simplifier, not an investigator [S].** Backends include
OpenAI, Azure, Cohere, Bedrock, SageMaker, Gemini, Vertex, and local models [D].

**Limitations [S]:** No multi-hypothesis output. No confidence or uncertainty representation. No
machine-readable link from a finding to the specific evidence that produced it (the evidence *is*
the string). No contradictory-evidence handling. Single-event-only collection is a material
evidence gap for crash-loop and scheduling investigations.

**Related:** [k8sgpt-operator](https://github.com/k8sgpt-ai/k8sgpt-operator) (485 stars, pushed
2026-10-10) runs scans continuously in-cluster [D].

---

### 4.2 HolmesGPT (Robusta) — Category D · strongest OSS candidate

- **Repo:** [robusta-dev/holmesgpt](https://github.com/robusta-dev/holmesgpt) · Apache-2.0 · Python
- **Status [D]:** 3,585 stars; created 2024-05-30; last push 2026-10-13. CNCF Sandbox (accepted
  Oct 2025 [C]). Described as "SRE Agent".

**What it does [S/D]:** An agentic investigator. ~40 toolsets under
`holmes/plugins/toolsets/`, spanning Kubernetes core, Kubernetes logs, Prometheus, Grafana, Loki,
Tempo, Datadog, New Relic, Elasticsearch, Coralogix, VictoriaLogs, ArgoCD, Helm, Cilium, Istio/
OpenShift, Kafka, RabbitMQ, MongoDB/MySQL/Postgres, Inspektor Gadget, AKS node health, Confluence,
Slab, ServiceNow, and a generic `bash` toolset. Breadth far exceeds K8sGPT's.

**How it gathers evidence [S]** — read from
[`holmes/plugins/toolsets/kubernetes.yaml`](https://github.com/robusta-dev/holmesgpt/blob/master/holmes/plugins/toolsets/kubernetes.yaml):

- Tools are **declarative YAML wrapping shell scripts**, exposed to the LLM as callable functions.
  Core Kubernetes tools are `kubernetes_jq_query`, `kubernetes_tabular_query`, `kubernetes_count`,
  plus `kubectl_top_pods`/`kubectl_top_nodes` and a kube-prometheus-stack target tool.
- Collection is **LLM-driven**: the model decides which tool to call with which parameters, in a
  loop. There is no deterministic pre-collection pass.
- Real engineering maturity is visible in the tool scripts: server-side `label_selector`/
  `field_selector` filtering, API pagination to avoid context overflow, correct API-group
  disambiguation when a CRD shadows a built-in plural (falls back to `/apis` discovery order),
  namespace-name regex validation to stop API-path injection, and explicit `shlex.quote`
  sanitisation discipline documented inline. Large outputs are optionally compressed via an
  `llm_summarize` transformer.
- `expose_remotely` and a platform MCP allow cross-cluster tool calls [S].

**Object correlation [S]:** Instructed, not structural. The system prompt
([`holmes/plugins/prompts/generic_ask.jinja2`](https://github.com/robusta-dev/holmesgpt/blob/master/holmes/plugins/prompts/generic_ask.jinja2))
tells the model to *"walk the ownership chain (e.g. deployment → replicaset → pod)"*, apply
*"five whys"*, and check Services/Ingresses. There is **no topology or evidence graph data
structure** — correlation happens inside the LLM's reasoning over tool outputs.

**How it uses AI [S]:** Fully agentic. The LLM plans, selects tools, iterates, and writes the
conclusion. A `TodoWrite` task tool (`holmes/plugins/toolsets/investigator/`) provides planning
scaffolding — but inspection of
[`investigator/model.py`](https://github.com/robusta-dev/holmesgpt/blob/master/holmes/plugins/toolsets/investigator/model.py)
shows it is only `Task{id, content, status}` with a `TaskStatus` enum. **It is a to-do list, not an
evidence or hypothesis model [S].**

**Critically important for this project's differentiation claims [S]:** HolmesGPT's prompt *already
mandates* most of the discipline this project's `CLAUDE.md` treats as its design principles:

- *"Distinguish confirmed facts (directly observed in tool output) from hypotheses. Use hedging
  language … if the analysis is inconclusive, say so."*
- *"trace each claim to specific tool output and hedge anything unverified"*
- *"If there are multiple possible causes, list them in a numbered list."*
- An extensive **blast-radius section**: name an entity as affected only on its own evidence; *"Never
  widen the blast radius by inference"*; *"Coincidence in time is not causation"*; sort every named
  entity into **Affected / Checked-healthy / Changed for another reason / Not checked**; quote the
  exact reason token (`Evicted`, `OOMKilled`, `BackOff`) that proves impact.
- *"If you cannot read a value (e.g. a Secret), say you couldn't verify it rather than guessing."*

This is a sophisticated, well-reasoned treatment of exactly the facts-vs-hypotheses and
missing-information problems named in this project's mission.

**The architectural limitation [S]:** All of it is enforced **by prompt text addressed to a
non-deterministic model**, not by a data model, schema, or verification step. The result model
confirms this: `holmes/core/models.py` defines `ChatResponse{analysis: str, conversation_history,
tool_calls: List[ToolCallResult], follow_up_actions, pending_approvals, metadata}`. The
investigation output is **free-text prose plus a tool-call transcript**. The transcript is a
genuine audit trail — but no individual finding is programmatically bound to the specific evidence
item supporting it, and no confidence value or contradiction is represented in a typed way [S].

**Other notable mechanics [S]:** `holmes/core/safeguards.py` refuses a tool call with
byte-identical parameters already made in the session, with an inline code comment noting this is
*"only reasonable … if Holmes is read only and does not mutate resources"* — i.e. read-only
operation is an assumption of its design, not an enforced boundary. Namespace-scoped RBAC is
supported and surfaced in the prompt. Secrets are excluded from the kubernetes/core toolset by
description [S].

---

### 4.3 Troubleshoot.sh (Replicated) — Category B · strongest architectural prior art

- **Repo:** [replicatedhq/troubleshoot](https://github.com/replicatedhq/troubleshoot) · Apache-2.0 · Go
- **Status [D]:** 587 stars; created 2019-07-08; last push 2026-10-13. Low stars, high relevance.

**What it does [D]:** Preflight checks and support bundles for Kubernetes applications. Not an AI
tool and not marketed as troubleshooting-with-AI — which is likely why it is routinely missed in
AI-SRE comparisons.

**Why it matters most here [S]:** It implements, in production, the exact architectural separation
this project's core design principle #1 describes — **deterministic evidence collection strictly
before any analysis**:

- `pkg/collect/` — ~80 collectors: `cluster_resources.go`, `cluster_info.go`, `logs.go`,
  `configmap.go`, `secret.go`, `rbac.go`, `dns.go`, `etcd.go`, `k8s_metrics.go`,
  `k8s_node_metrics.go`, `http.go`, `exec.go`, `run_pod.go`, `run_daemonset.go`,
  `remote_collector.go`, plus a large host-level set (CPU, memory, disk, sysctl, kernel modules,
  cgroups, journald, TCP/UDP port status, certificates, filesystem performance, network namespace
  connectivity) and datastore collectors (Postgres, MySQL, MSSQL, Redis, ClickHouse, Ceph,
  Longhorn).
- `pkg/analyze/` — ~75 analyzers operating **over the collected bundle**, not over a live cluster:
  `cluster_pod_statuses.go`, `cluster_container_statuses.go`, `deployment_status.go`,
  `replicaset_status.go`, `statefulset_status.go`, `job_status.go`, `event.go`, `ingress.go`,
  `image_pull_secret.go`, `node_resources.go`, `storage_class.go`, `text_analyze.go`,
  `yaml_compare.go`, `json_compare.go`.
- `pkg/collect/redact.go` — redaction is a first-class pipeline stage, not an afterthought [S].

**Properties this yields [I]:** collect-once/analyze-many, offline and asynchronous analysis,
reproducible re-analysis of a frozen snapshot, and a portable artifact that can be shared without
cluster access. These are strong properties that no Category C/D tool surveyed provides.

**Object correlation [S]:** Weak. Analyzers are largely independent assertions producing
pass/warn/fail outcomes. There is no correlation graph joining collected objects.

**AI usage [S]:** Essentially none, but notable: `pkg/analyze/ollama_helper.go` exists and contains
installation/lifecycle management for a local Ollama binary (`IsInstalled`, `IsRunning`,
platform-specific download URLs). The project is evidently experimenting with **local-model**
analysis. The exact feature surface was not traced — flagged as an open question.

**Related [D]:** [salesforce/sloop](https://github.com/salesforce/sloop) (1,585 stars, pushed
2026-02-17) records and visualises Kubernetes resource and event *history*, directly addressing the
event-retention constraint in §6.

---

### 4.4 kubectl-ai (Google Cloud) — Category D

- **Repo:** [GoogleCloudPlatform/kubectl-ai](https://github.com/GoogleCloudPlatform/kubectl-ai) · Apache-2.0 · Go
- **Status [D]:** 7,584 stars; created 2025-01-20; **last push 2026-07-15** — roughly three months
  without a commit at time of writing. Highest star count among OSS candidates, but the least
  recent activity. Momentum should be re-checked before treating it as a live comparator.

**What it does [D]:** A terminal AI assistant that turns natural language into kubectl operations;
interactive mode maintains conversational context. Supports Gemini, Vertex AI, Azure OpenAI,
OpenAI, Grok, and others.

**How it gathers evidence [S]:** `pkg/tools/` contains `kubectl_tool.go`, `bash_tool.go`,
`mcp_tool.go`, `custom_tool.go`. The model issues kubectl and shell commands. Fully LLM-driven, no
deterministic collection.

**Object correlation [I]:** None structurally. Whatever the model infers from command output.

**Safety mechanism worth borrowing — and its weakness [S]:**
[`pkg/tools/kubectl_filter.go`](https://github.com/GoogleCloudPlatform/kubectl-ai/blob/main/pkg/tools/kubectl_filter.go)
parses the command with a real shell parser (`mvdan.cc/sh/v3/syntax`), walks the AST, and classifies
each call against explicit `readOnlyOps` / `writeOps` maps, including sub-operation handling for
`rollout` (`history`/`status` read vs `pause`/`restart`/`resume`/`undo` write). Returns
`"unknown"` on parse failure.

Notably, it classifies `exec`, `debug`, `attach`, and `cp` as write operations. **However [I]:** the
same package ships a general `bash_tool.go`. Command-string classification is a usability guardrail,
not a security boundary; anything reachable through a general shell tool bypasses it. **Lesson for
this project: enforce read-only via RBAC and the client surface, not by inspecting command strings.**

---

### 4.5 kagent — agent platform, not a diagnostic engine

- **Repo:** [kagent-dev/kagent](https://github.com/kagent-dev/kagent) · Apache-2.0 · Go
- **Status [D]:** 4,012 stars; created 2025-01-21; last push 2026-10-13. CNCF Sandbox. Contributed
  by Solo.io [C].

**What it actually is [S]:** Inspection of `helm/kagent/templates/` shows a controller deployment,
CRDs, `modelconfig`, `toolserver-api`/`toolserver-kagent`, a substrate worker pool, UI, and RBAC —
i.e. **a runtime for building and operating agents on Kubernetes**, declaratively via CRDs, with
MCP-based tool integration. It does not ship Kubernetes diagnostic logic comparable to K8sGPT's
analyzers or HolmesGPT's toolsets; `helm/tools/` contains only `grafana-mcp`.

**Safety posture [S]:** `builtin-prompts-configmap.yaml` provides a prompt library —
`tool-usage-best-practices` and `safety-guardrails` — with instructions like *"Read before write"*,
*"No destructive operations without confirmation"*, *"Rollback planning"*, and *"Least privilege"*.
These are **prompt-level guardrails for an explicitly mutation-capable system**, the opposite of
this project's structurally read-only stance.

**Relevance [I]:** A potential deployment/runtime substrate, or a comparator for "how not to do
safety". Not a functional overlap with the investigation engine itself.

---

### 4.6 kubernetes-mcp-server — tool-access layer with the best OSS safety model found

- **Repo:** [containers/kubernetes-mcp-server](https://github.com/containers/kubernetes-mcp-server) · Apache-2.0 · Go
- **Status [D]:** 2,158 stars; created 2025-02-11; last push 2026-10-13.

**What it does [D]:** An MCP server exposing Kubernetes and OpenShift to any MCP client, with
toolsets (`core`, `config`, `helm`, `kcp`, `kiali`, `kubevirt`, `tekton`) that can be selectively
enabled to reduce context size and improve tool-selection accuracy.

**Safety model [S/D]** — directly relevant to this project's safety requirements:

```toml
read_only = true
toolsets = ["core", "config", "helm", "kubevirt"]

# Deny access to sensitive resources
[[denied_resources]]
group = ""
version = "v1"
kind = "Secret"
```

A declarative `read_only` flag plus **resource-kind denylisting that can exclude Secrets entirely**
is a stronger and more auditable boundary than kubectl-ai's command-string filter, and it maps
closely to this project's "avoid retrieving Secret values" requirement. It also documents a
dedicated-ServiceAccount read-only setup [D], and ships eval scenarios under `evals/tasks`
(Kubernetes 32, KubeVirt 27, Kiali 16, Istio 5, Helm 3) [D].

**Correlation / AI [I]:** None of either. It is plumbing — the client's model does all reasoning.
Relevant as a possible evidence-access layer and as a safety-design reference, not as a competitor.

---

### 4.7 Deterministic rule scanners — Category A

| Project | Stars | Last push | What it does | AI |
|---|---|---|---|---|
| [derailed/popeye](https://github.com/derailed/popeye) | 6,442 | 2025-12-08 | Cluster resource sanitizer; scans live resources against built-in rules, reports a graded score | None |
| [zegl/kube-score](https://github.com/zegl/kube-score) | 3,102 | 2026-05-20 | Static analysis of Kubernetes YAML/Charts for reliability and security | None |
| [FairwindsOps/polaris](https://github.com/FairwindsOps/polaris) | 3,396 | 2026-10-08 | Best-practice validation in clusters and CI | None |

All metadata **[D]**. These are **configuration-hygiene and best-practice tools, not incident
investigators [I]**. They answer "is this manifest well-formed and sane?", not "why is this specific
pod failing right now?". They are relevant as (a) proof that deterministic rule engines are a solved,
commoditised layer, and (b) a source of codified failure-mode knowledge. Popeye's ~10-month gap in
activity is worth noting.

---

### 4.8 Botkube — likely dormant

- **Repo:** [kubeshop/botkube](https://github.com/kubeshop/botkube) · MIT · Go
- **Status [D]:** 2,313 stars; **last push 2024-12-11** — nearly two years stale.

Event-driven cluster monitoring and ChatOps notifications to Slack/Teams/Discord with
recommendations [D]. **[I]** Given the push date, this should be treated as effectively unmaintained
and excluded from active comparison. Frequently still listed in "Kubernetes AI tools" roundups,
which is a useful caution about the reliability of such listicles.

---

### 4.9 Google Cloud — Gemini Cloud Assist Investigations · the most direct conceptual overlap

**This is the finding that most directly challenges this project's assumed novelty.**

**Source [D]:** [Troubleshoot issues with Gemini Cloud Assist investigations](https://docs.cloud.google.com/cloud-assist/investigations)
and [Accelerate diagnosis with Gemini Cloud Assist (GKE)](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/introduction-gemini).

**What it does [D]:** A root-cause-analysis tool for diagnosing issues across infrastructure and
applications. From the GKE console it diagnoses pod and cluster issues, and reaches across adjacent
Google Cloud services — nodes, IAM, load balancers.

**Its output model [D]** — compare against this project's design principles 2, 3, and 4:

- **Observations** — *"insights about your environment's state that are most relevant to the issue"*,
  derived from logs, configurations, and metrics via *"procedural runbooks, broad signal analysis, or
  tools"*. They are **ranked and filtered**. Each observation includes *"links to the source data
  that informs each observation, which lets you further investigate and fact check."*
- **Hypotheses** — hypothesis cards synthesised from the observations using domain-specific
  knowledge to identify *probable* root causes. *"When there is uncertainty, multiple root causes can
  be provided as hypotheses."*

So: **separation of observations from hypotheses, evidence linked to source data, ranking, and
explicit multi-hypothesis representation of uncertainty already exist in a shipping product [D].**
Design principles 2–4 of this project are **not novel**.

**Stated limitations [D]** — and these are where real opportunity may lie:

- *"Investigations are dynamic"* — *"re-running the same investigation can produce results with
  small differences."* **Not reproducible.**
- Subject to *"general limitations of AI technology."*
- Scoped to a **single Google Cloud project** or App Hub application.
- Users are told not to investigate *"data subject to residency or jurisdictional regulatory
  compliances."*
- **Preview**, with access restricted to **Premium Support contract holders** as of 2026-04-10.

**[I]** Practical constraints: GCP/GKE-only, closed, gated behind a premium support tier, and
non-deterministic by the vendor's own admission. A portable, cluster-agnostic, reproducible
equivalent is not obviously covered — but the *concept* is demonstrably occupied.

---

### 4.10 Komodor (Klaudia AI) — Category D, commercial

**Evidence quality caveat:** Komodor's own technical-comparison post returned **HTTP 403** and could
not be read. What follows is from Komodor's product and blog pages and is **vendor-authored [C]**.

**Claims [C]:** Klaudia *"correlates Kubernetes events, logs, recent changes, and resource state to
produce evidence-based hypotheses rather than another alert summary"*; combines ML models with
Komodor's dataset of past investigation flows, historical changes, events, and metrics plus
real-time data; autonomously investigates until satisfied; analyses impact across environments to
prioritise; performs configuration and dependency checks and *"provide[s] evidence for its
conclusions"*. A 2026 multi-agent architecture coordinates domain-specialist sub-agents
(autoscalers, Argo CD, NVIDIA GPUs, Istio, Airflow), with per-customer indexing of past root causes
and remediations [C].

**[I] Assessment:** Two claims, if real, are genuinely hard to replicate and matter strategically:
(1) **durable change history** — Komodor's core product has always been a Kubernetes change/event
timeline, which structurally solves the event-retention problem in §6; (2) **learning from prior
investigations within a customer's environment**. The "evidence-based hypotheses" language is
directionally identical to this project's stated goals. None of the mechanics are externally
verifiable.

---

### 4.11 Datadog Bits AI SRE — Category D, commercial

**Claims [C]** (Datadog engineering blog and docs): *"mimics the way human SREs think: by forming
hypotheses, testing them using live telemetry data, and following promising evidence to a root
cause"*; *"dynamically generates multiple root cause hypotheses and tests them by querying data
across your environment"*; at each step the agent decides which tool to call and what data to fetch.
Kubernetes remediation covers CrashLoopBackOff, OOMKilled, ImagePullBackOff with generated
remediation guidance and automated workflows. Investigations reported at ~3–4 minutes [C].

**[I]** Architecturally this is an agentic investigator over Datadog's telemetry; multi-hypothesis
generation-and-test is explicitly claimed. Requires Datadog as the data platform. Notably, Datadog
has published on building an **evaluation platform for autonomous SRE agents** — relevant to this
project's acceptance-criteria work and worth reading in the next phase.

---

### 4.12 Causely — Category E, commercial

**Source [D]:** [How Causely works](https://docs.causely.ai/getting-started/how-causely-works/).

**Mechanism [D]:** Three data structures.
- **Topology Graph** — continuously discovered services, infrastructure, and interconnections,
  distinguishing connectivity (horizontal), layering (vertical), and composition (internal).
- **Causality Graph + Codebook** — a DAG whose nodes are root causes and symptoms and whose
  **edges carry probabilities**; the codebook is a root-cause × symptom matrix.
- **Attribute Dependency Graph** — a DAG over attributes (e.g. service CPU, queue length) with
  labelled functional relationships.

Built-in **causal models** instantiate against the discovered topology to produce an
environment-specific causality graph, *"often tens of thousands of causes"*, with no configuration
required [D]. A mediation layer distils telemetry locally into entities, relations, and signals;
*"Raw logs, full traces, and bulk metrics stay in your datacenter"* [D].

**AI usage [D]:** The documentation page reviewed **makes no mention of LLMs**. This appears to be
model-driven probabilistic inference, not generative AI.

**[I] Relevance:** The most rigorous non-LLM approach found. It demonstrates that explicit
probabilistic causal modelling over a live topology graph is a viable alternative to LLM reasoning —
a genuine architectural alternative this project must compare against rather than ignore. Costs:
requires a pre-built causal knowledge library, and leans on OpenTelemetry-grade telemetry rather
than plain Kubernetes API evidence. No limitations are stated on the page, which is itself a caution.

---

### 4.13 Grafana Sift / Asserts — Category E, commercial

**Source [D]:** [Sift analyses](https://grafana.com/docs/grafana-cloud/ai-tools/machine-learning/sift/analyses/).

**What it does [D]:** Runs a fixed set of named automated checks scoped to a cluster and namespace:

| Analysis | Detects | Data source |
|---|---|---|
| Error Pattern Logs | Groups similar log lines; flags groups with significantly increased rates | Loki |
| HTTP Error Series | Series with elevated HTTP errors in a cluster/namespace | Prometheus |
| Kube Crashes | Recent container crashes with cause (Error, OOMKill) | Kubernetes metrics |
| Noisy Neighbors | Over-saturated hosts where load exceeds CPU core count | Infra metrics |
| Recent Deployments | Resources that recently changed (service/config updates) | Kubernetes |
| Resource Contention | Containers with significant CPU throttling or packet loss | Kubernetes metrics |
| Log Query / Metric Query | Configurable LogQL/PromQL executed and rendered | Loki / Prometheus |

**Method [D]:** Statistical/ML, not LLM, for the detection checks. Sift is *"powered by Grafana
Machine Learning"*. Investigations are *"currently focused on Kubernetes-centered stacks and require
a cluster and namespace"* and are deeply tied to the LGTM stack (Loki, Tempo, Mimir). A separate
**Grafana Assistant** layer adds LLM interaction, and **Asserts** contributes a knowledge graph [D].

**[I] Relevance:** A good model of a **bounded, named, individually-explainable check set** — each
check is a discrete, reviewable unit with a known data dependency. That reviewability is a property
agentic LLM investigators lack. Weakness for this project's purposes: it is metrics/logs-first and
requires the Grafana stack; it is not Kubernetes-API-evidence-first.

---

### 4.14 Red Hat OpenShift Lightspeed

**Status [D]:** Generally available. A GenAI virtual assistant in the OpenShift web console. Users
can attach alert messages or YAML to the chat and ask what to do [D].

**Key verified constraint [D]:** *"While OpenShift Lightspeed cannot directly interrogate the
cluster, it can help users quickly understand cluster resources. The assistant will return a command
that users can copy and run."* So the base assistant is **knowledge-and-guidance, not evidence
collection** — it does not gather cluster evidence itself. Red Hat publishes an
[AI System Card](https://access.redhat.com/ai/system-card/red-hat-openshift-lightspeed) for it,
which is good practice worth emulating for transparency.

A separate 2026 Red Hat article describes **MCP-server-based** fleet-health querying with Advanced
Cluster Management, where the MCP server is described as *"inherently a read-only tool because it
focuses strictly on querying, filtering, and retrieving resource data rather than modifying cluster
state"* [D]. **[I]** Capability is therefore split: the console assistant advises; MCP integration
supplies the actual cluster access.

---

### 4.15 AI-SRE startups — Cleric, Resolve.ai, Traversal, NeuBird

**All [C] — vendor/press claims via third-party aggregators. Weakest evidence in this document.**

| Vendor | Claim | Note |
|---|---|---|
| **Cleric** | Runs **read-only by default**, write access only on explicit customer opt-in | The read-only-first posture matches this project's safety stance; a useful precedent to cite |
| **Resolve.ai** | Multi-agent investigation, *"specialized agents pursuing multiple hypotheses in parallel"*, claimed >2× root-cause accuracy improvement; $125M raise at $1B valuation, Feb 2026 | Parallel multi-hypothesis pursuit is architecturally the same idea as this project's "evaluate possible causes" |
| **Traversal** | Claimed 32% MTTR reduction and 82% RCA accuracy at American Express over 250B log lines/day | No published methodology; treat the figure as unverified |
| **NeuBird (Hawkeye)** | Autonomous SRE agent; claimed 230,000 alerts autonomously resolved, $1.8M saved, 2025 | Vendor-reported |

**[I]** The strategic signal is clear and should temper any novelty claim: **well-funded commercial
teams are converging on multi-agent, multi-hypothesis, evidence-driven investigation, and at least
one has adopted read-only-by-default as a product principle.** None are Kubernetes-API-evidence
specialists; most are telemetry-platform-centric and closed.

---

## 5. Academic and benchmark prior art — the most important section

This is the most methodologically rigorous part of the landscape and the part most often absent from
tool comparisons. **All items [P] — abstract-level reading only, authors' own self-reported results.**

### 5.1 Cloud-OpsBench — directly usable for this project's evaluation requirement

- **Repo [D]:** [LLM4Ops/Cloud-OpsBench](https://github.com/LLM4Ops/Cloud-OpsBench) · MIT ·
  42 stars · created 2026-02-28 · last push 2026-10-13.

**What it is [D]:** A reproducible benchmark for **agentic** RCA in Kubernetes systems. Built on a
**state snapshot paradigm**: *"instead of requiring a live cluster, each fault case stores the
cluster state, alerts, logs, and tool cache needed for deterministic diagnosis and replay."*

- **754 fault cases, 57 fault types**, two systems (Online Boutique 550, Train-Ticket 204).
- Each case: `metadata.json` (fault label, namespace, query, difficulty, **ground-truth
  diagnosis**), `tool_cache.json` (cached tool outputs for deterministic simulation),
  `raw_data/{k8s_states.json, logs.json, alert.json, metrics.csv}`, and `code/`.
- **Milestone-based process evaluation** via `process-label/` — grades *whether the agent acquired
  the required diagnostic evidence*, not just whether the final answer was right.
- Ships `golden-trajectory/`, reference agents, and an MCP server (`cloudopsbench_mcp/`).
- Adoption [D]: integrated by OpenSRE; referenced by Alibaba Cloud STAROps and by a Jaeger AI
  Assistant evaluation issue; CloudWise reports 82.8% root-cause accuracy on all 204 Train-Ticket
  cases [C].

**Fault taxonomy [D]** — a ready-made, externally-validated failure-scenario catalogue directly
relevant to this project's design principle 7 and MVP scoping:

| Category | Example fault types | Difficulty | Cases |
|---|---|---|---|
| Scheduling | NodeAffinityMismatch, NodeSelectorMismatch, TaintTolerationMismatch, PodAntiAffinityConflict, CPU/MemoryCapacityMismatch, PVC{Selector,StorageClass,Capacity,AccessMode}Mismatch, PVBindingOccupied, NodeCordonMismatch | Easy | 164 |
| Runtime | ContainerMemoryLimitTooLow, Liveness/ReadinessProbe{Protocol,Port,Timing}, ServiceSidecarPortConflict, MysqlInvalidCredentials/Port, DBReadOnly, DBConnectionExhaustion, DeploymentZeroReplicas | Easy | 141 |
| Application Code Defect | CodeMissingParameter, CodeBusyLoop, CodeMemoryLeak, CodeWrongReturn, CodeArtificialDelay, CodeExcessiveFileReads/Writes, CodeWrongArgumentOrder | Hard | 98 |
| Service Routing | ServiceSelectorMismatch, ServicePortMappingMismatch, ServiceProtocolMismatch, ServiceEnvVarAddressMismatch, GatewayMisroute, ServiceDNSResolutionFailure | Medium | 91 |
| Startup | IncorrectImageReference, MissingImagePullSecret, MissingSecretBinding, VolumeMountPermissionDenied, ImageRegistryDNSFailure | Easy | 86 |
| Performance | PodCPUOverload, PodNetworkDelay, NodeNetworkDelay, NodeNetworkPacketLoss | Hard | 76 |
| Admission Control | Namespace{CPU,Memory,Pod,Service,Storage}QuotaExceeded, MissingServiceAccount | Medium | 58 |
| Infrastructure | ContainerdUnavailable, KubeletUnavailable, KubeProxyUnavailable, KubeSchedulerUnavailable | Medium | 40 |

**[I] Significance:** This changes the evaluation question. The project does not need to invent
reproducible failure scenarios from scratch — a 754-case, snapshot-replayable, ground-truth-labelled
benchmark with *evidence-acquisition process grading* already exists under MIT licence. Its snapshot
paradigm is also an architectural hint: a frozen evidence bundle is both testable and reproducible,
converging with Troubleshoot.sh's support-bundle model (§4.3).

### 5.2 "Auditable Graph-Guided Root Cause Analysis for Kubernetes Incidents" — near-identical design to this project's intent

**[P]** Kuvshinova & Jin, arXiv [2606.08590](https://arxiv.org/abs/2606.08590), submitted
2026-06-07.

A "Graph Traversal Agent" with stated operational constraints: **read-only evidence collection,
propagation-aware diagnosis, bounded execution, and independently validated verdicts**, mapped onto
a **typed incident graph** plus a separate validation stage. The LLM reasons over the graph while
*"deterministic graph and tool operations collect evidence, bound the search, and check proposed
verdicts."* Each conclusion must be validated against the graph's evidence nodes before a root cause
is accepted.

**Reported results [P]:** ITBench F1 improved 0.6087 → 0.9130 across 23 scenarios.

**Authors' own stated limitations [P]:** gains concentrated on **artificial fault-injection
scenarios**; live-cluster trials showed **unstable alert/trace data**; and they explicitly *"make no
production-readiness or mean-time-to-repair claim."*

**[I] This is the single most important novelty check in this document.** Read-only deterministic
evidence collection, a typed graph, deterministic bounding of LLM search, and verdicts validated
against evidence nodes is, to a first approximation, the architecture implied by this project's
`CLAUDE.md`. It has been published, implemented, and measured. The honest framing for the next phase
is therefore **"productise and harden a published research architecture that no shipping OSS tool
implements"**, not "invent a new approach". The authors' candour about fault-injection bias and
live-cluster instability is also a direct warning about this project's own evaluation design.

### 5.3 SynergyRCA / StateGraph

**[P]** Xiang, Chen, Zeng, Yin, Liu, Li, Xu — arXiv [2506.02490](https://arxiv.org/abs/2506.02490),
submitted 2025-06-03, *"Simplifying Root Cause Analysis in Kubernetes with StateGraph and LLM"*.

A **StateGraph** capturing spatial and temporal relationships between components, plus a
**MetaGraph** of entity connections. Deterministic graph construction combined with an LLM through
retrieval-augmented generation: the LLM predicts the most pertinent resource, then both graphs are
queried for context-specific diagnostic information. Reported: ~2 minutes average detection,
precision ~0.90, on production-cluster datasets [P]. **[I]** Confirms that
deterministic-graph-plus-LLM is established research, and that *temporal* relationships deserve
first-class treatment — a dimension K8sGPT's single-latest-event collection entirely lacks.

### 5.4 ARGUS

**[P]** Senja, Razavi Zadegan, Leitner — arXiv [2608.23084](https://arxiv.org/abs/2608.23084),
submitted 2026-08-24, *"ARGUS: MCP-Grounded Root Cause Analysis for Kubernetes Incidents"*.

Uses **MCP servers as the data-access layer** across four sources — Kubernetes state, Prometheus
metrics, Loki logs, NATS messaging — explicitly for reusability across organisations rather than
bespoke integrations. Delivers structured diagnostic summaries into Slack. Reported: correct root
cause in all 10 tested scenarios, 0.91 aggregate MCP success ratio [P].

**Most valuable finding [P]:** a **"diagnostic/prescriptive asymmetry"** — practitioners trusted
ARGUS's identification of *what failed*, but were sceptical of its *recommended remediation
actions*. **[I] This is strong empirical support for this project's read-only, "recommended next
investigation steps" framing over automated remediation — and it suggests recommendation quality and
trust should be evaluated separately from diagnostic accuracy.**

### 5.5 Other benchmarks

- **ITBench** [D] — [IBM/ITBench](https://github.com/IBM/ITBench), Apache-2.0, 510 stars, created
  2025-02-05, last push 2026-10-13. Agents inspect alerts, events, traces, and topology to identify
  contributing-factor entities (deployments, pods, namespaces, network policies). Used as the
  evaluation target in §5.2. An `ITBench-AA` leaderboard is published on Artificial Analysis [C].
- **SREGym** [C] — *"A Live Benchmark for AI SRE Agents with High-Fidelity Failure Scenarios"*,
  arXiv [2605.07161](https://arxiv.org/abs/2605.07161). A repository lookup at `microsoft/sre-gym`
  returned 404; the correct repository was not located. Open question.
- **Multi-dataset microservice diagnosis benchmark** [C] — arXiv
  [2606.29193](https://arxiv.org/abs/2606.29193); reported 400 HipsterShop cases anchored to
  per-modality key evidence and 103 OpenTelemetry Demo cases anchored to typed causal chains,
  stress-tested across the 2025 CCF AIOps Challenge. Not independently verified.
- **TriFleetRCA** [C] — arXiv [2609.23766](https://arxiv.org/abs/2609.23766), on-premise LLM RCA for
  Kubernetes. Not examined beyond the title; relevant to local-model deployment questions.

---

## 6. Hard technical constraints discovered

These are properties of Kubernetes itself and will constrain any design, independent of tool choice.

### 6.1 Kubernetes Events expire after one hour by default — verified in source

**[S]** From
[`pkg/controlplane/apiserver/options/options.go`](https://github.com/kubernetes/kubernetes/blob/master/pkg/controlplane/apiserver/options/options.go)
in kubernetes/kubernetes:

```go
EventTTL: 1 * time.Hour,
...
fs.DurationVar(&s.EventTTL, "event-ttl", s.EventTTL, "Amount of time to retain events.")
```

**Implications [I]:**
- Any investigation starting more than ~1 hour after an incident may find **no events at all**. The
  most diagnostically valuable evidence for scheduling, image-pull, mount, and eviction failures is
  the first to disappear.
- **EKS, GKE, and AKS do not expose apiserver flags** [D], so on managed control planes the
  retention window is the provider's decision, not the operator's.
- `kubectl get events` can show an apparently days-old event because repeated occurrences update an
  existing event *series*, preserving `creationTimestamp` while refreshing the last-observed time
  [D]. **Event age is therefore not a reliable incident timestamp** — a correctness trap for any
  timeline-building logic.
- "Missing information" is thus not an edge case but the **normal state** of a late investigation.
  This supports design principle 4 strongly, and explains why Komodor's change-history product and
  Sloop (§4.3) exist.

### 6.2 Other constraints noted

- **[I]** Container logs are rotated by the kubelet and lost on pod deletion;
  `--previous` retrieves only the immediately prior container instance. A pod that has
  crash-looped many times retains little history.
- **[I]** Read-only enforcement belongs in RBAC and the client surface, not in command-string
  filtering (see §4.4). `kubectl debug`, `exec`, `attach`, and `cp` are mutating or
  side-effecting and must be excluded by capability, not by regex.
- **[D/I]** Secret *metadata* (names, keys, mount references) is usually sufficient for diagnosing
  `MissingSecretBinding`-class failures without reading values — consistent with
  kubernetes-mcp-server's `denied_resources` approach (§4.6) and with this project's safety rules.

---

## 7. Comparison table

Evidence grade in the final column reflects the **weakest** grade supporting that row.

| Candidate | Category | Access | Evidence gathering | Object correlation | AI role | Read-only | Grade |
|---|---|---|---|---|---|---|---|
| **K8sGPT** | C | OSS Apache-2.0 | Deterministic; `pod.Status` fields, fixed reason lists; **single latest event**; no logs in pod analyzer | **Minimal** — `OwnerReferences` walk → one parent string | **Explainer only** — LLM gets the extracted string, "simplify in ≤280 chars" | Yes (read-only client) | **[S]** |
| **HolmesGPT** | D | OSS Apache-2.0 | **LLM-driven**; ~40 toolsets (k8s, Prometheus, Loki, Datadog, Grafana, DBs, bash); server-side filtering, pagination, injection hardening | **Prompt-instructed** ownership-chain walking; no graph data structure | **Full agentic** — plans, selects tools, iterates, writes conclusion | Assumed, not enforced (code comment confirms) | **[S]** |
| **Troubleshoot.sh** | B | OSS Apache-2.0 | **Deterministic, strictly collect-then-analyze**; ~80 collectors → bundle; ~75 analyzers over the bundle; redaction as a pipeline stage | **Weak** — independent pass/warn/fail assertions | **~None**; local-Ollama helper present, surface untraced | Collectors include `exec`/`run_pod` | **[S]** |
| **kubectl-ai** | D | OSS Apache-2.0 | LLM-driven kubectl + **general bash** | None structural | Full agentic (NL → commands) | **Partial** — shell-AST read/write classifier, bypassable via bash tool | **[S]** |
| **kagent** | Platform | OSS Apache-2.0 | N/A — agent runtime (CRDs, controller, toolservers, MCP) | N/A | Hosts agents; no built-in k8s diagnostic logic | **No** — mutation-capable; prompt-level guardrails only | **[S]** |
| **kubernetes-mcp-server** | Tool layer | OSS Apache-2.0 | Read-only MCP tools over k8s/OpenShift; selectable toolsets | None | None (client's model reasons) | **Yes, declaratively** — `read_only=true` + `denied_resources` (Secrets) | **[S/D]** |
| **Popeye / kube-score / Polaris** | A | OSS | Deterministic rule checks over live resources or YAML | None | **None** | Yes | **[D]** |
| **Botkube** | — | OSS MIT | Event-driven notifications + recommendations | None | Minimal | Yes | **[D]** · stale since 2024-12 |
| **GKE Gemini Cloud Assist** | D | **Closed; Preview; Premium Support only** | Logs, configs, metrics via runbooks, signal analysis, tools; GCP-wide (nodes, IAM, LBs) | Cross-service, within one GCP project | Agentic; **ranked Observations + multi-hypothesis cards with source-data links** | Diagnostic | **[D]** · *"re-running … can produce results with small differences"* |
| **Komodor Klaudia** | D | Closed, commercial | Events, logs, resource state, **change history**, metrics + per-customer history of past investigations | **Claimed** correlation + dependency checks; multi-agent domain specialists | Agentic, autonomous until satisfied | Not stated | **[C]** · own comparison page 403'd |
| **Datadog Bits AI SRE** | D | Closed, commercial | LLM-driven queries over Datadog telemetry | Claimed, platform-internal | Agentic; **explicitly generate-and-test multiple hypotheses** | Remediation workflows exist | **[C]** |
| **Causely** | E | Closed, commercial | Telemetry distilled locally into entities/relations/signals; raw data stays on-prem | **Strongest** — topology graph × causal models → **probability-weighted causality DAG** + codebook + attribute dependency graph | **No LLM mentioned**; probabilistic causal inference | Observation-based | **[D]** |
| **Grafana Sift** | E | Closed, commercial | **Fixed named checks** with declared data dependencies (Loki/Prometheus/k8s metrics) | Asserts knowledge graph (separate) | **Statistical/ML**, not LLM; Assistant adds LLM layer | Diagnostic | **[D]** |
| **OpenShift Lightspeed** | D | Commercial, GA | **Base assistant cannot interrogate the cluster** — returns commands to copy/run; MCP integration adds read-only access | None in base assistant | Knowledge/guidance assistant | Yes (read-only by design) | **[D]** |
| **Cleric / Resolve.ai / Traversal / NeuBird** | D | Closed, commercial | Telemetry-platform integrations | Claimed | Agentic; Resolve.ai claims **parallel multi-hypothesis** sub-agents | **Cleric: read-only by default**, opt-in writes | **[C]** |
| **Cloud-OpsBench** | Benchmark | OSS MIT | **Frozen snapshots**: k8s states, logs, alerts, metrics, tool cache → deterministic replay | Ground-truth labels; typed fault taxonomy | Evaluates agents; grades **evidence acquisition** via process labels | N/A (offline) | **[D]** |
| **Auditable Graph-Guided RCA** | Research | Preprint | **Deterministic, read-only**, bounded | **Typed incident graph**, propagation-aware | LLM reasons over graph; **verdicts independently validated against evidence nodes** | **Read-only by design** | **[P]** · no production-readiness claim |
| **SynergyRCA (StateGraph)** | Research | Preprint | Deterministic graph construction + RAG | **StateGraph (spatial + temporal) + MetaGraph** | LLM predicts pertinent resource, queries graphs | Not stated | **[P]** |
| **ARGUS** | Research | Preprint | **MCP-grounded**: k8s state, Prometheus, Loki, NATS | Correlates across sources | LLM produces structured summaries into Slack | Not stated | **[P]** · diagnostic/prescriptive trust asymmetry |

---

## 8. Candidate gaps — each stated with its strongest counter-evidence

These are **hypotheses for the gap-analysis phase, not conclusions**. Every one is paired with what
already occupies the space, so none can be mistaken for established novelty.

| # | Candidate gap | Counter-evidence that must be answered |
|---|---|---|
| G1 | **Reproducible, deterministic investigation output** — same cluster state in, same findings out; diffable and regression-testable | GKE Gemini Cloud Assist openly states re-runs differ [D]; all agentic tools inherit LLM non-determinism [I]. **No surveyed tool claims reproducibility.** But Cloud-OpsBench achieves determinism by freezing evidence [D], so the idea is established even if no *tool* ships it. |
| G2 | **Machine-readable finding→evidence binding** rather than prose that mentions evidence | GKE already links each observation to its source data [D]; the auditable-graph paper validates verdicts against evidence nodes [P]. HolmesGPT returns `analysis: str` + tool-call transcript — an audit trail, but no per-finding binding [S]. **Partially occupied; the gap is narrower than it looks.** |
| G3 | **A typed Kubernetes correlation model** (owner chain, Service↔Endpoints↔Pod, PVC↔PV↔StorageClass, Pod↔Node, ConfigMap/Secret references, sibling replicas, event timeline) built deterministically before reasoning | K8sGPT correlates almost nothing [S]; HolmesGPT correlates by prompt [S]; Causely has a far richer graph but telemetry-driven and LLM-free [D]; StateGraph [P] and the typed incident graph [P] both do this in research. **Not novel as an idea; apparently absent from shipping OSS.** |
| G4 | **Deterministic evidence collection ahead of reasoning, for Kubernetes API evidence specifically** | Troubleshoot.sh does collect-then-analyze properly but without correlation or AI [S]; Cloud-OpsBench snapshots evidence for replay [D]. **The pattern is proven; the combination with correlated reasoning is where the opening may be.** |
| G5 | **First-class representation of missing and contradictory evidence** — not hedged prose but typed state ("events expired", "logs rotated", "not checked") | HolmesGPT's prompt demands exactly this behaviour in detail, including a four-way Affected/Healthy/Changed-for-another-reason/Not-checked classification [S]. **The behaviour is specified; what is missing is enforcement in a data model rather than instructions to a model.** This is the sharpest remaining distinction found. |
| G6 | **Evidence-acquisition quality as a measured property**, not just final-answer accuracy | Cloud-OpsBench's milestone process labels already grade this [D]; Datadog has published on SRE-agent evaluation platforms [C]. **Already addressed in benchmarking; not addressed by any tool's own output.** |
| G7 | **Structurally enforced read-only** via RBAC and capability restriction | kubernetes-mcp-server has `read_only` + Secret denylist [S/D] — the best OSS precedent; Cleric claims read-only-by-default [C]; the auditable-graph paper makes read-only a design constraint [P]; kubectl-ai's filter is bypassable [I]; kagent is mutation-capable by design [S]. **Partially occupied; HolmesGPT notably assumes rather than enforces it [S].** |
| G8 | **Portable, cluster-agnostic, self-hostable investigation** with no vendor telemetry platform required | Komodor, Datadog, Causely, Grafana, and GKE all require their own platform or cloud [D/C]; HolmesGPT is self-hostable but needs an LLM and optionally an observability stack [D]. **Plausible but commercially crowded.** |
| G9 | **Honest coverage boundaries** — stating which failure classes the system can and cannot reach | No surveyed tool publishes this. Red Hat's AI System Card [D] is the closest practice found. **Apparently open, and cheap to do well.** |

### What is explicitly *not* a gap

Stated plainly so it is not re-litigated later:

- **Observations-vs-hypotheses separation** — shipping in GKE Gemini Cloud Assist [D].
- **Multiple ranked hypotheses under uncertainty** — GKE [D], Datadog [C], Resolve.ai [C].
- **Linking findings to source data** — GKE [D].
- **Facts-vs-hypotheses discipline and uncertainty hedging** — specified in detail in HolmesGPT's
  prompt [S].
- **Deterministic collection before analysis** — Troubleshoot.sh, in production since 2019 [S].
- **Deterministic graph + LLM reasoning for Kubernetes RCA** — published research [P] ×3.
- **Read-only-by-default AI access to Kubernetes** — kubernetes-mcp-server [S/D], Cleric [C].
- **Reproducible Kubernetes failure scenarios for evaluation** — Cloud-OpsBench, 754 cases, MIT [D].

**[I] Overall assessment:** The project's *concept* is well-occupied. The plausible opening is
narrow and specific: **a reproducible, self-hostable, Kubernetes-API-evidence-first engine whose
evidence→finding→hypothesis structure is enforced by a typed data model and verified
programmatically, rather than requested of an LLM in a prompt.** That framing is defensible on the
evidence gathered. "Nobody does AI-assisted Kubernetes investigation well" is not.

---

## 9. Strongest candidates to study further

Ranked by how much they should influence the next phase:

1. **HolmesGPT [S]** — the OSS tool to beat. Read its full prompt suite and toolset catalogue before
   designing anything. Its prompt encodes hard-won evidence discipline that should be *adopted*, and
   its free-text result model is the clearest structural opening.
2. **"Auditable Graph-Guided RCA" [P]** — the closest published match to this project's intended
   architecture, with measured results and candid limitations. Must be read in full.
3. **Cloud-OpsBench [D]** — 754 replayable ground-truth cases with evidence-acquisition grading, MIT
   licensed. Likely removes most of the evaluation-harness work.
4. **Troubleshoot.sh [S]** — the production-proven collector/analyzer/redaction separation, and a
   portable evidence-bundle format worth imitating.
5. **GKE Gemini Cloud Assist [D]** — the output model to benchmark against conceptually; its stated
   limitations (non-reproducible, GCP-only, Preview, premium-gated) define the realistic opening.
6. **K8sGPT [S]** — the analyzer catalogue is a useful inventory of codified Kubernetes failure
   modes; its AI layer is a cautionary example.
7. **Causely [D]** — the serious non-LLM alternative. Required reading for the architectural
   comparison that `CLAUDE.md` mandates.
8. **kubernetes-mcp-server [S/D]** — the safety-model reference, and a possible evidence-access layer.

---

## 10. Open questions for the next phase

**Novelty and positioning**
1. Does the "Auditable Graph-Guided RCA" paper, read in full, already implement the intended
   architecture closely enough to make this project a re-implementation? Is there public code?
2. Given GKE Gemini Cloud Assist ships observations-plus-hypotheses with source links, is
   **reproducibility** (G1) a strong enough differentiator to anchor the project?
3. Is the real contribution "typed and verified rather than prompt-requested" (G5/G2)? Can that be
   stated as a testable property rather than a design preference?

**Architecture**
4. Deterministic correlation graph, probabilistic causal model (Causely-style), or agentic
   tool-calling — and on what criteria is the choice made? `CLAUDE.md` requires this comparison.
5. Should the evidence layer be a **frozen bundle** (Troubleshoot.sh / Cloud-OpsBench) rather than
   live queries? That would deliver reproducibility, offline analysis, and testability in one move.
   What does it cost in freshness?
6. Build evidence access directly, or consume kubernetes-mcp-server? What does owning the collector
   buy that justifies the maintenance?
7. How are hypotheses actually generated and ranked — LLM, deterministic rules over the graph, or a
   hybrid where rules propose and the LLM explains? What makes a ranking defensible?

**Evidence and correctness**
8. Given the verified 1-hour event TTL, what is the honest minimum evidence floor for a late
   investigation, and how is degraded confidence represented rather than hidden?
9. Since event age is unreliable (series updates preserve `creationTimestamp`), how is a trustworthy
   incident timeline constructed?
10. What is the complete read-only capability set, enforced by RBAC — and is `kubectl debug`/`exec`
    permanently excluded or deferred?
11. How is Secret *metadata* collected while provably never reading values?

**Evaluation**
12. Adopt Cloud-OpsBench and/or ITBench directly, or build scenarios? What does the project's own
    harness add beyond them?
13. Heeding the auditable-graph authors' warning that gains concentrate on **artificial
    fault-injection** cases — how is overfitting to synthetic faults avoided?
14. Following ARGUS's **diagnostic/prescriptive asymmetry** finding, should recommendation quality be
    evaluated separately from diagnostic accuracy, with a lower trust bar assumed for
    recommendations?
15. What is the baseline to beat — K8sGPT, HolmesGPT, a plain LLM with kubectl, or a human SRE?

**Unresolved research items**
16. Troubleshoot.sh's `ollama_helper.go`: what AI-assisted analysis is actually implemented there?
17. SREGym: correct repository was not located (`microsoft/sre-gym` → 404).
18. Komodor's technical comparison post returned HTTP 403 — retrieve by another route to assess its
    architecture claims fairly.
19. Datadog's SRE-agent evaluation-platform engineering posts — unread, likely valuable for
    acceptance criteria.

---

## 11. Sources

**Source-inspected repositories (code read directly)**
- [k8sgpt-ai/k8sgpt](https://github.com/k8sgpt-ai/k8sgpt) — `pkg/analyzer/pod.go`, `pkg/util/util.go`, `pkg/ai/prompts.go`, `pkg/analyzer/` listing
- [robusta-dev/holmesgpt](https://github.com/robusta-dev/holmesgpt) — `holmes/plugins/toolsets/kubernetes.yaml`, `holmes/plugins/prompts/generic_ask.jinja2`, `holmes/plugins/toolsets/investigator/model.py` + `investigator_instructions.jinja2`, `holmes/core/models.py`, `holmes/core/safeguards.py`
- [replicatedhq/troubleshoot](https://github.com/replicatedhq/troubleshoot) — `pkg/collect/`, `pkg/analyze/` listings, `pkg/analyze/ollama_helper.go`
- [GoogleCloudPlatform/kubectl-ai](https://github.com/GoogleCloudPlatform/kubectl-ai) — `pkg/tools/` listing, `pkg/tools/kubectl_filter.go`
- [kagent-dev/kagent](https://github.com/kagent-dev/kagent) — `helm/kagent/templates/` listing, `builtin-prompts-configmap.yaml`
- [containers/kubernetes-mcp-server](https://github.com/containers/kubernetes-mcp-server) — `README.md` configuration and toolsets
- [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) — `pkg/controlplane/apiserver/options/options.go` (`EventTTL`)

**Repository metadata only**
- [derailed/popeye](https://github.com/derailed/popeye) · [zegl/kube-score](https://github.com/zegl/kube-score) · [FairwindsOps/polaris](https://github.com/FairwindsOps/polaris) · [kubeshop/botkube](https://github.com/kubeshop/botkube) · [salesforce/sloop](https://github.com/salesforce/sloop) · [k8sgpt-ai/k8sgpt-operator](https://github.com/k8sgpt-ai/k8sgpt-operator) · [IBM/ITBench](https://github.com/IBM/ITBench) · [LLM4Ops/Cloud-OpsBench](https://github.com/LLM4Ops/Cloud-OpsBench)

**Vendor and project documentation**
- [Gemini Cloud Assist investigations](https://docs.cloud.google.com/cloud-assist/investigations) · [GKE troubleshooting with Gemini](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/introduction-gemini)
- [How Causely works](https://docs.causely.ai/getting-started/how-causely-works/) · [Causely causal reasoning system](https://www.causely.ai/causal-reasoning-system)
- [Grafana Sift analyses](https://grafana.com/docs/grafana-cloud/ai-tools/machine-learning/sift/analyses/) · [Sift announcement](https://grafana.com/blog/announcing-sift-automated-system-checks-for-faster-incident-response-times-in-grafana-cloud/) · [Noisy neighbors](https://grafana.com/docs/grafana-cloud/machine-learning/sift/analyses/noisy-neighbors/)
- [Robusta HolmesGPT docs](https://docs.robusta.dev/master/configuration/holmesgpt/index.html) · [CNCF: HolmesGPT](https://www.cncf.io/blog/2026/01/07/holmesgpt-agentic-troubleshooting-built-for-the-cloud-native-era/) · [CNCF Sandbox issue #392](https://github.com/cncf/sandbox/issues/392)
- [CNCF: K8sGPT](https://www.cncf.io/blog/2024/06/07/generative-ai-for-kubernetes-meet-k8sgpt-open-source-project/) · [CNCF Sandbox issue #38](https://github.com/cncf/sandbox/issues/38)
- [OpenShift Lightspeed](https://www.redhat.com/en/technologies/cloud-computing/openshift/lightspeed) · [AI System Card](https://access.redhat.com/ai/system-card/red-hat-openshift-lightspeed) · [Lightspeed + ACM multicluster](https://developers.redhat.com/articles/2026/08/31/ai-powered-multicluster-management-querying-fleet-health-openshift-lightspeed)
- [Solo.io: contributing kagent to CNCF](https://www.solo.io/blog/bringing-agentic-ai-to-kubernetes-contributing-kagent-to-cncf) · [CNCF Sandbox issue #360](https://github.com/cncf/sandbox/issues/360)
- [Datadog: building Bits AI SRE](https://www.datadoghq.com/blog/building-bits-ai-sre/) · [Bits Investigation](https://www.datadoghq.com/blog/bits-ai-sre/) · [Bits Kubernetes remediation](https://docs.datadoghq.com/containers/bits_ai_kubernetes_remediation) · [SRE-agent evaluation platform](https://www.datadoghq.com/blog/engineering/bits-ai-eval-platform/)
- [Komodor: Introducing KlaudiaAI](https://komodor.com/blog/introducing-klaudiaai-redefining-kubernetes-troubleshooting/) · [Klaudia product page](https://komodor.com/platform/klaudia-ai-powered-troubleshooting/) · [Multi-agent AI SRE](https://komodor.com/blog/multi-agent-ai-sre-has-landed-and-its-built-for-your-most-complex-stacks/)

**Research preprints (abstracts only)**
- [Auditable Graph-Guided Root Cause Analysis for Kubernetes Incidents](https://arxiv.org/abs/2606.08590) — Kuvshinova & Jin, 2026-06-07
- [Simplifying Root Cause Analysis in Kubernetes with StateGraph and LLM](https://arxiv.org/abs/2506.02490) — Xiang et al., 2025-06-03
- [ARGUS: MCP-Grounded Root Cause Analysis for Kubernetes Incidents](https://arxiv.org/abs/2608.23084) — Senja et al., 2026-08-24
- [SREGym: A Live Benchmark for AI SRE Agents](https://arxiv.org/abs/2605.07161) · [Multi-Dataset Benchmark for LLM Agents in Microservice Failure Diagnosis](https://arxiv.org/abs/2606.29193) · [TriFleetRCA](https://arxiv.org/abs/2609.23766)
- [ITBench-AA leaderboard](https://artificialanalysis.ai/evaluations/itbench-aa)

**Secondary / unverified**
- [Kubernetes event retention: why the default hour isn't enough](https://radarhq.io/blog/kubernetes-event-retention) · [Verify --event-ttl and reclaim etcd space](https://oneuptime.com/blog/post/2026-09-04-kubernetes-events-never-expire-event-ttl-etcd/view) (TTL default independently verified in source, §6.1)
- [awesome-ai-sre](https://github.com/agamm/awesome-ai-sre) · [Mezmo: 2026 AI SRE market map](https://www.mezmo.com/learn/the-2026-ai-sre-market-map-agents-harnesses-and-the-data-layer) · [Cleric vs Resolve.ai vs Traversal](https://wetheflywheel.com/en/comparisons/cleric-vs-resolve-ai-vs-traversal/) — vendor-capability claims in §4.15 derive from aggregators of this kind and are correspondingly weak

---

## 12. Phase summary

**What changed.** `docs/research/landscape-research.md` created. No code, no dependencies, no cluster
access, no integration with any other platform.

**What evidence supports the decisions.** Direct source inspection of seven repositories (K8sGPT's
pod analyzer, correlation utils and AI prompts; HolmesGPT's Kubernetes toolset, system prompt,
investigator model, result model and safeguards; Troubleshoot.sh's collector/analyzer split;
kubectl-ai's command filter; kagent's Helm templates and prompt library; kubernetes-mcp-server's
configuration; and the Kubernetes apiserver's `EventTTL` default). Primary documentation for Google,
Causely, Grafana, and Red Hat. Abstract-level reading of four research preprints and the
Cloud-OpsBench dataset specification.

**What remains uncertain.** Commercial platform internals are unverified and rest on vendor claims.
Research results are self-reported and largely unreplicated. Source inspection was targeted, so
statements that a capability is absent are bounded by the files read. One intended primary source
(Komodor) was inaccessible, and one benchmark repository (SREGym) was not located.

**What should happen next.** Review these findings, then — per `CLAUDE.md`'s workflow — proceed to gap
analysis and the architecture proposal. The gap analysis should start from §8, because each candidate
gap there is already paired with the prior art that threatens it, and from the open questions in §10,
of which questions 1–5 are the ones that determine whether the project has a defensible position.
