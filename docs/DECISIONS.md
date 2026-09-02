# Enterprise Agent Improvement Lab Decisions

## Decision 1 — Keep the Lab library-first

### Context
The current project is an installable library with explicit contracts and services.

### Decision
Keep the Lab library-first.

### Alternatives considered
- Turn it into an application service.
- Merge it into the runtime.

### Consequences
The core stays reusable across products and runtimes.

### Risks
Integration code must remain well-defined so the library does not become too abstract.

---

## Decision 2 — Keep runtime execution outside the Lab

### Context
The `EnterpriseRuntime` protocol separates execution from evaluation.

### Decision
The Lab evaluates runtime evidence. It does not own agent execution.

### Alternatives considered
- Let the Lab orchestrate agents directly.

### Consequences
Runtime safety and evaluation stay separate.

### Risks
Adapters must preserve enough runtime evidence for valid evaluation.

The old conversation-shaped runtime and runner APIs are migration history, not supported extension points.

---

## Decision 3 — Use a generic enterprise execution trace

### Context
The original trace model centered on conversational turns.

### Decision
Use a generic ordered enterprise execution trace.

### Alternatives considered
- Keep conversational turns as the root model.
- Store enterprise events only in metadata.

### Consequences
The Lab can represent background, workflow, approval, delegation, and write-capable agents.

### Risks
An over-general trace could become vague.

Typed events prevent this.

---

## Decision 4 — Preserve typed event models

### Context
Enterprise execution includes different semantic event types.

### Decision
Use typed contracts for model calls, tools, state mutations, approvals, delegations, retrieval, and workflow transitions.

### Alternatives considered
- One untyped event dictionary.

### Consequences
Evaluators can depend on explicit evidence.

### Risks
More contracts must be maintained.

---

## Decision 5 — Use enterprise candidate artifacts

### Context
The original candidate model centered on prompts and configuration.

### Decision
Keep prompts as valid artifacts, but use a general enterprise candidate artifact model.

### Alternatives considered
- Encode all non-prompt changes as configuration.

### Consequences
Tools, policies, routing, models, workflows, approvals, memory, retrieval, and capabilities can be explicit candidate components.

### Risks
Artifact boundaries must stay simple.

---

## Decision 6 — Use typed enterprise candidate changes

### Context
A JSON path diff does not explain the meaning of an enterprise improvement.

### Decision
Represent changes with explicit types such as tool addition, policy change, routing change, and workflow change.

### Alternatives considered
- Use generic artifact diffs only.

### Consequences
The system can reason about allowed change classes and their risk.

### Risks
The taxonomy can grow too large.

Keep the set based on real change types.

---

## Decision 7 — Keep improvement bounded by scope

### Context
The Lab protects datasets, evaluators, promotion rules, and protected resources.

### Decision
Use `ImprovementScope` to bound candidate changes.

### Alternatives considered
- Let a generator make unrestricted changes.

### Consequences
Candidate generation remains controlled and reviewable.

### Risks
Scopes that are too strict can block useful improvements.

---

## Decision 8 — Keep Enterprise Agent Harness as an integration

### Context
Enterprise Agent Harness is the expected main runtime.

### Decision
Keep it behind a dedicated integration adapter.

The Lab core must not import the Harness.

### Alternatives considered
- Add the Harness as a required package dependency.
- Merge both projects.

### Consequences
The Lab stays runtime-neutral.

### Risks
The adapter must handle version drift between projects.

---

## Decision 9 — Capture reproducible environment and registry snapshots

### Context
A candidate cannot be reproduced from model and prompt information alone.

### Decision
Record agent, tool, capability, policy, runtime, provider, model, fixture, and environment identity with each run.

### Alternatives considered
- Store names and versions only in metadata.

### Consequences
Historical comparisons can identify the exact ecosystem used.

### Risks
Snapshot identity rules must stay stable.

---

## Decision 10 — Remove legacy domain APIs after consumer migration

### Context
Enterprise contracts were introduced beside the original narrow contracts during migration.

### Decision
Remove old domain classes and modules after internal consumers migrate.

Retain only bounded wire-format aliases needed to read stored data.

### Consequences
The public surface is explicit: `ExecutionTrace`, `EnterpriseAgentCandidate`, `EnterpriseEvaluationCase`, provider-neutral evaluation runners, and typed enterprise comparison APIs.

### Risks
Downstream imports of removed APIs fail at import time.

Migration documentation and tests make that break explicit.

---

## Decision 11 — Support stateful evaluation environments

### Context
Enterprise agents can perform writes and external side effects.

### Decision
Use an explicit evaluation-environment boundary with setup, fixtures, state capture, reset, and teardown.

### Alternatives considered
- Run enterprise tests directly against live services.

### Consequences
Write-capable agents can be tested safely.

### Risks
Test environments can diverge from production behavior.

---

## Decision 12 — Prefer deterministic evaluators

### Context
Many enterprise requirements can be calculated from traces and state.

### Decision
Use deterministic evaluators whenever the requirement has an objective check.

### Alternatives considered
- Use LLM judges for most evaluation.

### Consequences
Results remain reproducible and easier to audit.

### Risks
Some quality outcomes cannot be reduced to deterministic checks.

---

## Decision 13 — Use subjective judges only when needed

### Context
Some semantic quality checks need judgment.

### Decision
Keep judges explicit, calibrated, and separate from deterministic checks.

### Alternatives considered
- Remove judges.
- Treat judge output as authoritative.

### Consequences
The Lab can measure subjective quality without hiding uncertainty.

### Risks
Judge drift and provider variance remain possible.

---

## Decision 14 — Preserve explicit human promotion authority

### Context
The system separates promotion eligibility from human decision.

### Decision
Keep that separation.

### Alternatives considered
- Automatically promote eligible candidates.

### Consequences
The Lab provides evidence. Humans retain production authority.

### Risks
Human review can slow promotion.

---

## Decision 15 — Keep framework runners outside the Lab core

### Context
Framework-specific runners can be useful, but they should not define the Lab domain.

### Decision
Keep framework runners in application or integration packages.

The Lab exposes provider-neutral runtime and evaluation boundaries.

### Alternatives considered
- Remove framework integrations.
- Keep one framework as the permanent architecture boundary.

### Consequences
Applications can use Pydantic Evals, replay, shadow, or other runner implementations without changing core contracts.

### Risks
Each adapter must preserve event identity, ordering, and safe evidence.

---

## Decision 16 — Rename only after the architecture transition

### Context
A package rename should represent a real product change.

### Decision
Rename only after enterprise contracts and workflows exist.

### Alternatives considered
- Rename before implementation starts.

### Consequences
The final package name reflects the completed architecture transition.

### Risks
Migration documentation must clearly distinguish removed and canonical APIs.
