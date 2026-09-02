# Enterprise Agent Improvement Lab Architecture

## Purpose

Enterprise Agent Improvement Lab is a provider-neutral system for controlled evaluation and improvement of enterprise AI agents.

It answers two questions:

1. Is this agent safe, correct, reliable, efficient, and useful?
2. Is the proposed candidate better than the current version?

The Lab does not run production agents. It does not deploy agents. It does not own business problem discovery.

## Responsibility boundaries

### Enterprise Agent Improvement Lab

The Lab owns:

- evaluation datasets and cases;
- evaluation evidence;
- execution trace contracts;
- deterministic evaluators and explicit subjective judges;
- failure mining and review;
- root-cause evidence;
- bounded improvement planning;
- candidate construction rules;
- baseline comparison;
- promotion evidence;
- human-controlled promotion and rollback records;
- evidence-governance contracts;
- storage interfaces for Lab records.

### Enterprise Agent Harness

Enterprise Agent Harness owns:

- agent construction;
- runtime execution;
- model access;
- tool execution;
- permissions;
- policies;
- approvals;
- runtime state;
- runtime safety;
- runtime tracing;
- agent, tool, capability, and policy registries.

The Harness is an integration. It is not a dependency of the Lab core.

### Applications

Applications own:

- business problem discovery;
- business-specific orchestration;
- product workflows;
- decisions about which agents or capabilities to build.

## System shape

```text
Enterprise Agent Harness or another runtime
        │
        │ execution evidence
        ▼
Runtime integration adapter
        │
        ▼
Enterprise Agent Improvement Lab
        │
        ├── Evaluation
        ├── Failure mining
        ├── Root-cause analysis
        ├── Improvement planning
        ├── Candidate construction
        ├── Baseline comparison
        └── Promotion evidence
        │
        ▼
Enterprise candidate definition
        │
        ▼
Runtime integration adapter
        │
        ▼
Controlled evaluation environment
```

## Core domain concepts

The canonical enterprise contracts include:

- `ExecutionTrace`
- typed execution events
- `EnterpriseEvaluationCase`
- `CandidateArtifact`
- `EnterpriseAgentCandidate`
- `EnterpriseCandidateChange`
- `ImprovementScope`
- `EnvironmentSnapshot`
- enterprise evaluation reports
- `EvaluationFailure`
- `FailureCluster`
- `RootCauseHypothesis`
- `ImprovementPlan`
- enterprise comparison contracts
- risk-aware promotion contracts
- governance contracts
- system-level evaluation contracts

The old conversation- and prompt-shaped public contracts are migration history. They are not the canonical API.

## Execution trace model

The Lab uses one generic ordered event stream.

Typed event families represent:

- model calls;
- tool calls;
- messages;
- state reads;
- state mutations;
- retrieval;
- approval requests;
- approval decisions;
- delegation;
- workflow transitions;
- external events;
- human actions;
- errors.

Core behavior must not be hidden in untyped metadata.

## Runtime adapter boundary

The Lab accepts external execution through a small provider-neutral runtime boundary.

`EnterpriseRuntime` describes:

- runtime identity;
- execution of one enterprise evaluation case against one enterprise candidate;
- return of a Lab-compatible `ExecutionTrace`.

Harness-specific translation belongs under `integrations/`.

Framework-specific runners, including optional Pydantic Evals support, stay outside core domain contracts.

## Evaluation boundary

The Lab evaluates evidence. It does not enforce runtime policy.

The Lab can verify that:

- a required approval occurred;
- an unauthorized action did not occur;
- a state invariant was preserved;
- a workflow followed valid transitions;
- tool side effects matched expectations;
- delegation stayed within allowed boundaries;
- business and operational outcomes met defined requirements.

The runtime remains responsible for enforcing controls during execution.

## Evaluation environment

Write-capable agents require controlled evaluation environments.

The environment boundary supports:

- fixture loading;
- initial state;
- disposable state;
- controlled clocks;
- external service stubs;
- state snapshots;
- side-effect inspection;
- reset and teardown.

One evaluation case must not leak state into another case.

## Candidate and improvement model

Candidates are complete enterprise agent definitions, not prompt patches.

Candidate artifacts can represent prompts, tools, policies, routing, models, memory, retrieval, workflows, approvals, capabilities, and configuration.

Typed candidate changes explain what changed and why.

`ImprovementScope` limits which changes can occur and protects resources such as datasets, evaluators, promotion rules, policies, permissions, agents, tools, and capabilities.

Candidate lineage remains explicit and immutable.

## Improvement decision flow

```text
EvaluationFailure
    ↓
FailureCluster
    ↓
RootCauseHypothesis
    ↓
ImprovementPlan
    ↓
Bounded candidate builder
    ↓
EnterpriseAgentCandidate
```

Root-cause and planning decisions must reference evidence.

The Lab prefers existing approved tools and capabilities over unrestricted generation.

Arbitrary executable code generation is outside the v0.1 scope.

## Comparison and promotion

Baseline comparison covers more than aggregate quality scores.

It can include:

- security;
- authorization;
- state integrity;
- approvals;
- workflow completion;
- tool side effects;
- delegation;
- reliability;
- latency;
- token usage;
- cost;
- business outcomes.

Hard regressions remain distinct from soft regressions.

A high-risk regression must not be hidden by aggregate score improvement.

Promotion evidence is risk-aware. Human approval remains the final authority.

## Candidate lifecycle

The controlled lifecycle is:

```text
DRAFT
  ↓
OFFLINE_EVALUATED
  ↓
SHADOW
  ↓
CANARY
  ↓
APPROVED
  ↓
ACTIVE
  ↓
RETIRED
```

Shadow and canary records contain evaluation evidence. The Lab does not deploy candidates or route production traffic.

## Production evidence ingestion

The Lab can ingest production execution evidence through provider-neutral ingestion boundaries.

Sampling can select traces from explicit operational signals such as:

- permission denial;
- approval rejection;
- manual override;
- rollback;
- unexpected state mutation;
- SLA breach;
- policy violation;
- operator escalation;
- compensation events;
- business KPI degradation;
- data-integrity failure.

The Lab is not a real-time production monitoring platform.

## Evidence governance

Evaluation evidence must be explicit, auditable, and governed.

The governance model includes:

- evidence references;
- redaction policies;
- retention policies;
- sensitive-field classifications;
- tenant boundaries.

Prefer safe references and summaries over copies of sensitive raw payloads.

Secrets and raw credentials must not be persisted as evaluation evidence.

## Storage

Core services depend on storage ports rather than SQLite-specific behavior.

The main storage boundaries include datasets, traces, experiments, candidates, failures, evaluations, promotions, artifacts, and environment snapshots.

SQLite is the included persistence implementation for v0.1.

## Runner architecture

Evaluation services depend on provider-neutral runner contracts.

Supported patterns include:

- local execution;
- replay evaluation;
- shadow evaluation;
- optional Pydantic Evals integration.

Runner selection must not change domain evaluation semantics.

## Multi-agent evaluation

System-level contracts reuse the same trace, evaluator, comparison, and failure architecture.

The Lab can evaluate:

- one agent;
- agent-to-agent interaction;
- whole-system behavior.

System evaluation can detect delegation loops, context leakage, privilege escalation, inconsistent decisions, duplicate work, invalid delegation, and missing result validation.

## Core versus integrations

### Core

Core contains:

- domain contracts;
- evaluation logic;
- comparison logic;
- failure logic;
- improvement planning;
- candidate rules;
- promotion rules;
- governance contracts;
- storage protocols;
- provider-neutral interfaces.

### Integrations

Integrations contain:

- Enterprise Agent Harness adapter;
- optional framework adapters;
- external judge providers;
- production trace ingestion adapters;
- external persistence implementations when added later.

## Provider neutrality

Core domain contracts must not depend on:

- a model provider;
- an agent framework;
- Pydantic Evals;
- a database vendor;
- a cloud platform.

## Non-goals

The Lab does not own:

- arbitrary executable code generation;
- autonomous deployment;
- automatic production promotion;
- production agent execution;
- business opportunity discovery;
- real-time production monitoring;
- model training;
- reinforcement learning.
