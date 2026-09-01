# Enterprise Agent Improvement Lab Decisions

## Decision 1 — Keep the Lab library-first

### Context
The current project is a small installable library with explicit contracts and services.

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
The current `AgentRuntime` protocol already separates execution from evaluation.

### Decision
The Lab evaluates runtime evidence. It does not own agent execution.

### Alternatives considered
- Let the Lab orchestrate agents directly.

### Consequences
Runtime safety and evaluation stay separate.

### Risks
Adapters must preserve enough runtime evidence for valid evaluation.

---

## Decision 3 — Introduce a generic enterprise execution trace

### Context
The current trace model is centered on conversational turns.

### Decision
Introduce a generic ordered enterprise execution trace.

### Alternatives considered
- Keep `ObservedTurn` as the root model.
- Store all enterprise events in metadata.

### Consequences
The Lab can represent background, workflow, approval, and write-capable agents.

### Risks
An over-general trace could become vague.

Use typed events to prevent this.

---

## Decision 4 — Preserve typed event models

### Context
Enterprise execution includes different semantic event types.

### Decision
Use typed contracts for model calls, tools, state mutations, approvals, delegations, and workflow transitions.

### Alternatives considered
- One untyped event dictionary.

### Consequences
Evaluators can depend on explicit evidence.

### Risks
More contracts must be maintained.

---

## Decision 5 — Generalize prompt artifacts into enterprise candidate artifacts

### Context
The current candidate model is centered on prompts and configuration.

### Decision
Keep prompts as valid artifacts, but introduce a general candidate artifact model.

### Alternatives considered
- Keep prompt artifacts and encode everything else as configuration.

### Consequences
Tools, policies, routing, models, workflows, and capabilities can become explicit candidate components.

### Risks
Artifact boundaries must stay simple.

---

## Decision 6 — Introduce typed enterprise candidate changes

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

Keep the initial set small and based on real changes.

---

## Decision 7 — Keep improvement bounded by scope

### Context
The current project already protects datasets, evaluators, promotion rules, and protected artifacts.

### Decision
Expand this model into `ImprovementScope`.

### Alternatives considered
- Let an LLM generate unrestricted candidate changes.

### Consequences
Candidate generation remains controlled and reviewable.

### Risks
Scopes that are too strict can block useful improvements.

---

## Decision 8 — Make Enterprise Agent Harness an integration, not a core dependency

### Context
Enterprise Agent Harness is the expected main runtime.

### Decision
Create a dedicated integration adapter.

The Lab core must not import the Harness.

### Alternatives considered
- Add the Harness as a required package dependency.
- Merge both projects.

### Consequences
The Lab stays runtime-neutral.

### Risks
The adapter must handle version drift between projects.

---

## Decision 9 — Extend reproducibility with environment and registry snapshots

### Context
A candidate cannot be reproduced from model and prompt information alone.

### Decision
Record agent, tool, capability, policy, runtime, provider, model, and fixture identity with each run.

### Alternatives considered
- Store names and versions only in metadata.

### Consequences
Historical comparisons can identify the exact ecosystem used.

### Risks
Snapshot identity rules must be stable.

---

## Decision 10 — Support stateful evaluation environments

### Context
Enterprise agents can perform writes and external side effects.

### Decision
Add an explicit evaluation-environment boundary with setup, fixtures, state capture, and teardown.

### Alternatives considered
- Run enterprise tests directly against live services.

### Consequences
Write-capable agents can be tested safely.

### Risks
Test environments can diverge from production behavior.

---

## Decision 11 — Prefer deterministic evaluators

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

## Decision 12 — Use subjective judges only when needed

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

## Decision 13 — Preserve explicit human promotion authority

### Context
The current system separates promotion eligibility from human decision.

### Decision
Keep that separation.

### Alternatives considered
- Automatically promote eligible candidates.

### Consequences
The Lab provides evidence. Humans retain production authority.

### Risks
Human review can slow promotion.

---

## Decision 14 — Make Pydantic Evals one runner backend

### Context
The current runner is coupled to Pydantic Evals.

### Decision
Preserve that integration, but move toward a generic runner protocol.

### Alternatives considered
- Remove Pydantic Evals.
- Keep it as the permanent architecture boundary.

### Consequences
The Lab can later support replay, shadow, or distributed runners.

### Risks
Do not add the abstraction before it is needed by the migration.

---

## Decision 15 — Delay the package rename

### Context
The current package is still structurally the Agent Improvement Lab.

### Decision
Rename only after enterprise contracts and workflows exist.

### Alternatives considered
- Rename before implementation starts.

### Consequences
The name change reflects a real architecture change.

### Risks
Temporary documentation must distinguish current and target names.
