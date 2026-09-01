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
- execution trace contracts used for evaluation;
- deterministic evaluators and optional judges;
- failure mining and review;
- root-cause evidence;
- bounded improvement planning;
- candidate comparison;
- promotion evidence;
- human-controlled promotion and rollback records.

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

## Current architecture

The current project has strong foundations:

- a small `AgentRuntime` adapter boundary;
- versioned datasets;
- immutable candidate artifacts;
- typed traces and summaries;
- deterministic evaluators;
- optional Pydantic Evals execution;
- failure mining and annotations;
- baseline comparison;
- holdout evaluation;
- promotion gates;
- explicit human promotion and rollback decisions;
- SQLite persistence;
- CLI and dashboard query services.

The main constraints are model assumptions, not the evaluation lifecycle.

## Current limits

### Conversational trace assumption

`AgentTrace` is built around turns, text input, text output, and tool calls.

Enterprise agents also need to represent:

- events;
- state reads and writes;
- approvals;
- delegations;
- workflow transitions;
- background work;
- human actions;
- external events;
- non-text outcomes.

### Prompt-centered candidate assumption

The current candidate model mainly changes prompts and configuration.

Enterprise improvements must also support:

- tool additions and removals;
- permission changes;
- policy changes;
- routing changes;
- model changes;
- retrieval changes;
- memory changes;
- workflow changes;
- capability changes;
- approval-rule changes.

### Interaction-centered case assumption

Enterprise cases must support initial state, actions, approvals, invariants, final state, authorization context, and business outcomes.

## Target architecture

```text
Enterprise Agent Harness
        │
        │ execution evidence
        ▼
Harness integration adapter
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
Candidate definition
        │
        ▼
Enterprise Agent Harness
        │
        ▼
Evaluation environment
```

## Core domain concepts

The target core needs these concepts:

- `ExecutionTrace`
- typed execution events
- `EnterpriseEvaluationCase`
- `CandidateArtifact`
- `EnterpriseAgentCandidate`
- `EnterpriseCandidateChange`
- `ImprovementScope`
- `EnvironmentSnapshot`
- `EvaluationReport`
- `EvaluationFailure`
- `FailureCluster`
- `RootCauseHypothesis`
- `ImprovementPlan`
- `BaselineComparison`
- `PromotionPolicy`
- `PromotionDecision`

## Execution trace model

Use one generic ordered event stream.

Suggested event types:

- model call;
- tool call;
- message;
- state read;
- state mutation;
- retrieval;
- approval request;
- approval decision;
- delegation;
- workflow transition;
- external event;
- human action;
- error.

Keep typed events. Do not replace domain contracts with untyped metadata dictionaries.

## Runtime adapter boundary

The Lab must continue to accept external runtimes through a small protocol.

The core protocol should describe:

- runtime identity;
- execution of one evaluation case against one candidate;
- return of a Lab-compatible execution trace.

Harness-specific translation belongs under `integrations/`.

## Evaluation boundary

The Lab evaluates evidence. It must not enforce runtime policy itself.

The Lab can verify that:

- a required approval occurred;
- an unauthorized action did not occur;
- a state invariant was preserved;
- a workflow followed valid transitions.

The Harness remains responsible for enforcing those controls during execution.

## Candidate lifecycle

```text
DRAFT
  ↓
EVALUATED
  ↓
REVIEW
  ↓
APPROVED or REJECTED
  ↓
RETIRED
```

Later phases can add shadow and canary states.

A candidate must have immutable lineage from:

- parent candidate;
- source failures;
- source annotations;
- improvement scope;
- generator or builder;
- environment snapshot.

## Promotion lifecycle

Promotion remains evidence-based and human-controlled.

The Lab computes eligibility from promotion gates.

A human records the final decision.

The Lab must never convert eligibility into automatic production deployment.

## Evidence model

Evaluation evidence must be explicit and auditable.

Prefer references and safe summaries over raw sensitive payload copies.

The evidence model must support:

- trace references;
- state snapshots;
- evaluator evidence;
- artifact checksums;
- environment snapshots;
- candidate lineage;
- promotion-gate evidence.

## Core versus integrations

### Core

Core contains:

- contracts;
- evaluation logic;
- comparison logic;
- failure logic;
- candidate rules;
- promotion rules;
- storage protocols;
- provider-neutral interfaces.

### Integrations

Integrations contain:

- Enterprise Agent Harness adapter;
- Pydantic Evals runner adapter;
- external judge providers;
- production trace ingestion adapters;
- storage implementations that need external systems.

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
- model training;
- reinforcement learning.
