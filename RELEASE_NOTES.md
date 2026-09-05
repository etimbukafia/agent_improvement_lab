# Enterprise Agent Improvement Lab v0.1.0

## Summary

Enterprise Agent Improvement Lab v0.1.0 provides a provider-neutral system for evaluation and controlled improvement of enterprise AI agents.

The release moves the project beyond prompt-focused agent evaluation. It adds typed enterprise execution evidence, bounded improvement planning, enterprise candidate changes, risk-aware comparison, governed evidence, and human-controlled promotion.

## Main capabilities

### Enterprise execution evidence

- Generic `ExecutionTrace` model.
- Typed events for model calls, tools, messages, state, retrieval, approvals, delegation, workflows, human actions, external events, and errors.
- Explicit event order and identity.
- Safe trace summaries.

### Enterprise evaluation cases

- Typed enterprise triggers and inputs.
- Initial and expected state.
- Required, optional, and prohibited actions.
- Approval requirements.
- State invariants.
- Authorization and tenant context.
- Business outcomes and budgets.

### Controlled evaluation environments

- Fixture loading.
- Disposable state.
- State snapshots.
- Reset and teardown.
- Controlled clocks.
- External service stubs.
- Side-effect inspection for write-capable agents.

### Enterprise evaluator catalog

Evaluation support includes enterprise checks for:

- state integrity;
- authorization;
- approvals;
- workflows;
- tool behavior;
- delegation;
- retrieval;
- safety;
- reliability;
- operational budgets;
- business outcomes.

### Failure analysis

- Expanded enterprise failure taxonomy.
- Failure clustering.
- Evidence-backed `RootCauseHypothesis` contracts.
- Supporting and conflicting evidence.
- Explicit confidence and reviewer state.

### Improvement planning

- Typed `ImprovementPlan` decisions.
- Explicit `ImprovementScope` boundaries.
- Support for prompt, skill, tool, policy, permission, routing, model, retrieval, memory, workflow, threshold, and approval-rule changes.
- Human review when evidence is insufficient.

### Bounded candidate construction

Specialized candidate builders support controlled changes to enterprise agent definitions.

Candidate generation preserves:

- immutable artifacts;
- lineage;
- source failures;
- evidence references;
- scope boundaries;
- protected resources.

Arbitrary executable code generation is outside this release.

### Enterprise comparison and promotion

Candidate comparison can include:

- quality;
- security;
- authorization;
- state integrity;
- approvals;
- workflow completion;
- tool side effects;
- delegation;
- reliability;
- latency;
- token use;
- cost;
- business outcomes.

Risk-aware promotion profiles keep high-risk regressions separate from aggregate improvement.

The Lab computes promotion eligibility. Human approval remains the final authority.

### Multi-agent evaluation

System-level evaluation can represent and review:

- individual agents;
- agent-to-agent interaction;
- whole-system behavior.

Checks include delegation loops, context leakage, privilege escalation, duplicate work, inconsistent decisions, invalid delegation, and missing result validation.

### Production evidence ingestion

The Lab can ingest production execution evidence and select traces for later evaluation.

Sampling can use structured signals such as permission denial, approval rejection, manual override, rollback, state mutation, SLA breach, policy violation, compensation, KPI degradation, and data-integrity failure.

The Lab is not a real-time monitoring platform.

### Shadow and canary evidence

The candidate lifecycle supports:

`DRAFT → OFFLINE_EVALUATED → SHADOW → CANARY → APPROVED → ACTIVE → RETIRED`

The Lab records evaluation evidence for these stages. It does not deploy candidates or route production traffic.

### Evidence governance

Governance contracts support:

- evidence references;
- redaction policies;
- retention policies;
- sensitive-field classification;
- tenant boundaries.

Secrets and raw credentials must not be persisted as evaluation evidence.

### Storage and runners

- Provider-neutral storage ports.
- SQLite implementation for v0.1.0.
- Provider-neutral evaluation runner architecture.
- Local, replay, shadow, and optional Pydantic Evals integration patterns.

### Enterprise Agent Harness integration

[Enterprise Agent Harness](https://github.com/etimbukafia/enterprise-agent-harness) remains outside the Lab core.

The integration boundary can translate enterprise agent definitions, registry identity, runtime evidence, approvals, delegation, tools, policies, and execution traces into Lab contracts.

## Public API transition

The canonical Python package is:

`enterprise_agent_improvement_lab`

The distribution name is:

`enterprise-agent-improvement-lab`

Legacy conversation-shaped and prompt-shaped public contracts were removed after consumer migration.

See `docs/API_MIGRATION.md` for the migration map.

## Safety and product boundaries

v0.1.0 does not provide:

- autonomous production deployment;
- automatic promotion without human approval;
- arbitrary executable code generation;
- real-time production monitoring;
- generalized multi-agent optimization;
- model fine-tuning;
- reinforcement learning.

## Development validation

The repository quality workflow checks supported Python versions with Ruff, mypy, pytest, compile checks, and the deterministic calculator reference cycle.
