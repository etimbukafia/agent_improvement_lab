# Phase 0 Architecture Review

## Summary

The current Agent Improvement Lab has a strong evaluation lifecycle.

The enterprise transition should preserve that lifecycle and redesign the narrow domain contracts around it.

## Strong abstractions to preserve

The following parts are strong enough to keep or generalize:

- library-first package design;
- runtime adapter boundary;
- versioned datasets;
- immutable artifacts;
- candidate lineage;
- bounded candidate scopes;
- deterministic evaluator model;
- explicit subjective-judge support;
- failure normalization;
- failure clustering;
- human annotations;
- baseline-versus-candidate comparison;
- development and holdout split handling;
- hard regression checks;
- promotion gates;
- human promotion decisions;
- rollback records;
- judge calibration;
- read-only dashboard/query services;
- SQLite persistence;
- CLI workflow structure.

## Current abstractions that block enterprise use

### `AgentTrace`

The trace root assumes conversational turns.

This blocks clean representation of:

- event-driven executions;
- state mutations;
- approvals;
- delegations;
- long-running workflows;
- human actions;
- external events;
- non-text outcomes.

This is the first contract that should change.

### `AgentCandidate`

The current candidate structure mainly references prompt and configuration artifacts.

This blocks explicit representation of tool, policy, routing, model, approval, workflow, and capability changes.

### `PromptArtifact`

This is a good immutable artifact implementation, but it is too narrow as the general enterprise artifact abstraction.

Keep its checksum and immutability ideas.

Generalize the artifact model.

### `EvaluationCaseRef`

The current case model already has useful generic `input`, `expected`, risk, provenance, and tool expectations.

It needs explicit enterprise state and action semantics.

### `PydanticEvalsRunner`

The current runner works, but `ComparisonRunner` accepts a concrete `PydanticEvalsRunner`.

This is a real implementation coupling.

It does not need to be fixed in Phase 1, but the target architecture should move to a generic runner protocol.

## Highest-risk architecture changes

1. Generic execution trace design.
2. Enterprise candidate and artifact design.
3. Enterprise case semantics.
4. Harness-to-Lab adapter contracts.
5. Environment snapshot identity.
6. Enterprise regression semantics.
7. Risk-aware promotion semantics.

These are expensive to reverse after other phases depend on them.

## What should not change yet

Do not change these before their dependent enterprise contracts exist:

- package name;
- CLI command name;
- SQLite implementation shape;
- dashboard layout;
- Pydantic Evals integration;
- calculator example;
- judge calibration system;
- existing comparison and promotion workflows beyond required contract adaptation.

## Decisions that are expensive to reverse

### Event model

If execution evidence becomes an untyped event dictionary, later evaluators will depend on unstable metadata.

Use typed events from the start.

### Candidate meaning

The candidate must represent a governed agent definition, not only prompt text.

### Harness ownership

The Lab must not absorb runtime enforcement responsibilities.

### Improvement scope

The improvement planner and builders must never gain authority to modify evaluators, datasets, promotion rules, or protected governance controls.

### Promotion authority

Human promotion authority must remain separate from computed eligibility.

## Hidden couplings found

### Runner coupling

`ComparisonRunner` depends directly on `PydanticEvalsRunner`.

This means Pydantic Evals currently leaks above the runner implementation boundary.

Plan to replace this dependency with a runner protocol in Phase 20 or earlier if another runner is needed.

### Candidate artifact coupling

Candidate construction assumes prompt/configuration artifacts and replaces artifact IDs directly.

The current service is safe for its scope, but it cannot become the enterprise builder without redesign.

### Conversational trace helpers

Evaluator helpers order turns first and then tool calls.

This makes turn structure part of evaluator assumptions.

Enterprise evaluators should consume generic execution events or typed event selectors instead.

### Workflow metadata coupling

The runner reads workflow identity from case metadata.

Enterprise workflow identity should become explicit once enterprise case contracts are introduced.

## Conversational assumptions

Conversational assumptions exist in:

- `ObservedTurn`;
- `input_text` and `output_text`;
- turn-first tool ordering;
- session evaluators;
- style and repeated-question helpers;
- some sampling reasons.

These are not all defects.

They should remain available for conversational agents, but they must stop defining the root enterprise model.

## Pydantic Evals leakage

Pydantic Evals does not appear in core domain contracts.

That is good.

The main leakage is service-level coupling:

- `runner.py` is Pydantic-specific;
- `ComparisonRunner` accepts that concrete runner.

This can wait until the domain migration is stable.

## Candidate-generation safety today

The current candidate-generation boundary is strong for prompt/configuration changes.

It already:

- requires explicit scopes;
- protects configured paths;
- prevents no-op changes;
- validates generated artifact identity;
- records checksums;
- records lineage;
- protects datasets, evaluators, and promotion configuration paths by default.

Do not remove these controls.

Generalize them into enterprise change scopes and specialized builders.

## Compatibility strategy

Do not preserve every current class forever.

Use short-lived conversion helpers where needed.

The calculator example should remain a behavior regression test during the migration.

Do not build adapters that preserve obsolete domain concepts after the enterprise replacements are stable.

## Phase 1 readiness

Phase 1 is ready to begin.

The first implementation target should be the generic enterprise execution trace.

Phase 1 should not redesign candidates or cases at the same time.

The trace contract must settle first because evaluators, runtime adapters, state evidence, approvals, and later comparison logic all depend on it.

## Phase 1 blockers

No architecture blocker was found.

Before implementation, Phase 1 should define one explicit decision:

> whether the new enterprise trace replaces `AgentTrace` immediately or exists beside it with a temporary conversion path.

Preferred approach:

Introduce the new enterprise trace beside the existing conversational trace, add deterministic conversion, migrate consumers, then remove the old root model when no longer needed.
