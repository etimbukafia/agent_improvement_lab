# Agent Improvement Lab

## Repository implementation plan

**Status:** Phase 0 complete; Phase 1 complete; Phase 2 complete; Phase 3 complete; Phase 4 complete; Phase 5 complete; Phase 6 complete  
**Repository:** `evals/agent-improvement-lab` (independent Git repository)  
**Role:** Agent evaluation and controlled-improvement toolkit  
**Last updated:** 2026-08-24

## 1. Purpose

Agent Improvement Lab is a library and CLI for evaluating AI agents and managing controlled prompt or configuration improvements. It executes versioned evaluation datasets through a runtime adapter, records failures and human annotations, compares a candidate with a baseline, and records promotion decisions.

The Lab answers:

> What failed, why did it fail, what should change, did the candidate improve, did anything regress, and is the candidate safe to promote?

## 2. Design boundaries

- Agent runtimes integrate through public protocols and versioned trace contracts.
- Core packages do not depend on a particular agent framework or application domain.
- Examples and optional integrations do not become dependencies of the core package.
- Evaluation criteria and promotion rules cannot be changed by candidate generation.

## 3. Scope

### Included

- Versioned datasets and cases
- Pydantic Evals execution
- Output, tool-call, trajectory, session, safety, latency, and cost evaluators
- Trace and session summaries
- Failure normalization and clustering
- Lightweight SME annotation workflow
- Regression and golden-dataset lifecycle
- Versioned prompt/configuration candidates
- Baseline-versus-candidate experiments
- Holdout evaluation
- Hard promotion gates
- Human approval and rollback records
- Feedback sampling from completed sessions
- Framework adapters
- CLI and reusable dashboard data services

### Excluded

- Agent orchestration
- Domain-specific analytics
- Production monitoring infrastructure
- Fine-tuning
- Autonomous deployment
- Model-versus-model comparison as a product feature
- Enterprise RBAC
- Hosted observability platform

## 4. Architecture

```text
Runtime adapter
      |
      v
Pydantic Evals task
      |
      +-- dataset/cases
      +-- deterministic evaluators
      +-- calibrated model judges
      +-- trace/session evaluators
      |
      v
Experiment report
      |
      +-- failure normalization
      +-- failure clustering
      +-- SME review
      +-- regression/golden promotion
      |
      v
Candidate generator
      |
      v
Baseline/candidate replay
      |
      v
Promotion policy + human decision
```

## 5. Proposed repository structure

```text
agent-improvement-lab/
|-- pyproject.toml
|-- README.md
|-- PROJECT_PLAN.md
|-- src/agent_improvement_lab/
|   |-- contracts/
|   |   |-- cases.py
|   |   |-- evaluation.py
|   |   |-- candidates.py
|   |   |-- traces.py
|   |   |-- sessions.py
|   |   |-- failures.py
|   |   `-- experiments.py
|   |-- runtime.py
|   |-- runner.py
|   |-- datasets.py
|   |-- coverage.py
|   |-- evaluators/
|   |   |-- base.py
|   |   |-- tools.py
|   |   |-- trajectories.py
|   |   |-- sessions.py
|   |   |-- safety.py
|   |   `-- operational.py
|   |-- experiments.py
|   |-- comparison.py
|   |-- failure_mining.py
|   |-- review.py
|   |-- candidates.py
|   |-- promotion.py
|   |-- sampling.py
|   |-- storage/
|   |   `-- sqlite.py
|   `-- cli.py
|-- examples/
|   `-- calculator_agent/
|-- tests/
`-- artifacts/
```

## 6. Generic contracts

Define strict, versioned Pydantic contracts for:

- `EvaluationCaseRef`
- `DatasetVersion`
- `AgentCandidate`
- `PromptArtifact`
- `AgentTrace`
- `ObservedTurn`
- `ObservedToolCall`
- `TraceSummary`
- `SessionSummary`
- `SessionEvaluationResult`
- `EvaluationScore`
- `EvaluationFailure`
- `FailureCluster`
- `HumanAnnotation`
- `ExperimentRun`
- `BaselineComparison`
- `PromotionPolicy`
- `PromotionDecision`

Core runtime protocol:

```python
class AgentRuntime(Protocol):
    async def execute(
        self,
        case: EvaluationCaseRef,
        candidate: AgentCandidate,
    ) -> AgentTrace: ...
```

Tool argument expectations support exact values, types, allowed values, regular-expression patterns, numeric ranges, required fields, and protected arguments.

## 7. Generic evaluator catalog

### Tool and trajectory

- `ToolSelectionAccuracy`
- `ToolArgumentAccuracy`
- `ToolArgumentConstraintMatch`
- `TrajectoryMatch`
- `RedundantToolCallRate`
- `ToolErrorRecovery`

### Session

- `SessionContextRetention`
- `RepeatedQuestionRate`
- `CrossTurnNumericalConsistency`
- `SessionContradictionRate`
- `ClarificationQuality`
- `UnnecessaryClarificationRate`
- `SessionStyleConsistency`

### Safety and integrity

- `InstructionOverrideResistance`
- `ProtectedArgumentIntegrity`
- `AuthorizationBoundaryPreserved`
- `RequiredVerificationExecuted`

### Operational

- `LatencyBudget`
- `TokenBudget`
- `CostBudget`
- `LoopBoundCompliance`
- `ErrorRate`

Deterministic evaluators are preferred. Model judges are limited to subjective qualities, must return explanations and confidence, and must be calibrated against human labels.

## 8. Evaluation coverage matrix

Maintain a versioned coverage artifact connecting requirements to code and cases.

| Requirement | Evaluator | Evidence | Gate |
| --- | --- | --- | --- |
| Correct tool | `ToolSelectionAccuracy` | Tool trace | Soft |
| Correct arguments | `ToolArgumentAccuracy` | Typed call arguments | Soft |
| Protected arguments | `ProtectedArgumentIntegrity` | Runtime context and calls | Hard |
| Correct order | `TrajectoryMatch` | Ordered events | Soft |
| No redundant calls | `RedundantToolCallRate` | Call signatures/count | Soft |
| Context retained | `SessionContextRetention` | Cross-turn state | Soft |
| No repeated questions | `RepeatedQuestionRate` | Clarification intents | Soft |
| Numerical consistency | `CrossTurnNumericalConsistency` | Structured claims | Hard |
| Style consistency | `SessionStyleConsistency` | Complete session | Soft |
| Injection resistance | `InstructionOverrideResistance` | Safety events/behavior | Hard |
| Explanation present | All evaluators | Score and reason | Required |

## 9. Storage

Use SQLite for local metadata:

- Dataset registry
- Candidate and prompt registry
- Experiment manifests
- Case results and evaluator scores
- Trace/session summaries
- Failure clusters
- Sampling events
- Annotations and annotation history
- Promotion and rollback decisions

Store immutable prompt files, dataset files, and complete report artifacts in the repository or an artifact directory. Persist checksums in SQLite.

Raw prompts and sensitive tool results are not required by generic trace/session summaries.

## 10. Phased implementation

## Lab Phase 0 — Framing and repository foundation

### Objective

Establish package boundaries and development standards.

### Tasks

- [x] Write the Lab product brief and README outline.
- [x] Record ADRs for Pydantic Evals, SQLite, adapter protocols, and human-controlled promotion.
- [x] Define explicit non-goals.
- [x] Create the package structure and `pyproject.toml`.
- [x] Pin Python and dependency versions.
- [x] Configure formatting, linting, typing, and pytest.
- [x] Add a license and contribution notes.
- [x] Add a dependency-boundary test preventing the core package from importing examples or optional integrations.

### Deliverables

- Installable skeleton
- ADRs
- Automated quality checks

### Exit criteria

- The empty package installs and tests successfully.
- Core modules have no dependency on examples or a specific agent framework.

## Lab Phase 1 — Contracts, datasets, and experiment storage

### Objective

Create the stable data model used by every later Lab feature.

### Tasks

- [x] Implement the contracts in Section 6 with schema versions.
- [x] Implement dataset split, risk, tag, and provenance metadata.
- [x] Add YAML and JSON dataset loading and validation.
- [x] Reject duplicate case IDs and invalid version references.
- [x] Implement immutable prompt artifacts and checksums.
- [x] Implement run manifests containing dataset, candidate, prompt, toolset, runtime, provider, seed, and timestamp metadata.
- [x] Implement SQLite migrations and repositories.
- [x] Implement stable JSON report serialization.
- [x] Add serialization round-trip and migration tests.
- [x] Add safe `TraceSummary` and `SessionSummary` schemas.
- [x] Verify summaries do not require raw prompts or sensitive tool output.

### Deliverables

- Versioned contract package
- Dataset loader
- SQLite experiment store
- Artifact serializer

### Exit criteria

- Datasets, candidates, experiments, traces, sessions, and decisions round-trip losslessly.
- Invalid references fail with actionable errors.

## Lab Phase 2 — Pydantic Evals execution and evaluator library

### Objective

Run generic agents through Pydantic Evals and score outputs and behavior.

### Tasks

- [x] Wrap Pydantic Evals execution behind a small Lab API.
- [x] Implement `AgentRuntime` and adapter lifecycle hooks.
- [x] Implement the tool and trajectory evaluator catalog.
- [x] Implement the deterministic session evaluator catalog.
- [x] Implement the safety/integrity evaluator catalog.
- [x] Implement operational-budget evaluators.
- [x] Support evaluation from the Lab's explicit trace contracts.
- [x] Require every score to contain an evaluator ID and explanation.
- [x] Support repeated runs for stochastic cases.
- [x] Aggregate by split, risk, tag, workflow, and failure category.
- [x] Implement the coverage-matrix artifact and validator.
- [x] Create intentionally correct and incorrect fixtures for evaluator self-tests.
- [x] Add a deterministic fake runtime.

### Deliverables

- Pydantic Evals runner
- Generic evaluator library
- Aggregate report model
- Evaluator self-test suite

### Exit criteria

- The same runtime can be evaluated without knowledge of its agent framework.
- Tool arguments and complete sessions are evaluated, not only final output.

## Lab Phase 3 — Failure mining, sampling, and SME review

### Objective

Turn evaluations and observed sessions into reviewable improvement data.

### Tasks

- [x] Define the generic failure taxonomy: planning, tool selection, arguments, trajectory, grounding, context, safety, quality, and efficiency.
- [x] Normalize failed evaluator results into `EvaluationFailure` records.
- [x] Cluster failures deterministically by evaluator, runtime component, tags, and intent.
- [x] Add optional LLM-assisted cluster titles without allowing the model to alter scores.
- [x] Implement completed-session sampling rules.
- [x] Sample on thumbs-down, deterministic verification failure, low judge confidence, critic rejection, tool error, repeated clarification, excessive latency/tokens, and unrecognized intent.
- [x] Persist the sampling reason.
- [x] Implement SME lifecycle: `unreviewed -> confirmed/rejected -> regression_candidate -> golden`.
- [x] Record reviewer, timestamp, severity, expected behavior, notes, and label confidence.
- [x] Preserve append-only annotation history.
- [x] Convert confirmed annotations into new versioned cases.
- [x] Add tests for sampling, state transitions, conflicting labels, and case generation.

### Deliverables

- Failure inbox
- Failure clustering
- Sampling service
- SME annotation workflow
- Regression-case generator

### Exit criteria

- A completed external-agent session can become a reviewed, versioned regression case.
- No LLM-generated label becomes golden without human confirmation.

## Lab Phase 4 — Candidate generation and comparison

### Objective

Propose constrained improvements and compare them reproducibly.

### Tasks

- [x] Define candidate scopes for prompt and bounded configuration artifacts.
- [x] Implement candidate creation from selected confirmed failures.
- [x] Supply the generator with current artifacts, failures, human expectations, and constraints.
- [x] Require a rationale and machine-readable change summary.
- [x] Render human-readable prompt/configuration diffs.
- [x] Prevent generated candidates from modifying datasets, labels, evaluator code, or promotion rules.
- [x] Run baseline and candidate with identical dataset and runtime manifests.
- [x] Compare aggregate and sliced metrics.
- [x] Detect pass-to-fail transitions and numerical regressions.
- [x] Run holdout only after development/regression gates pass.
- [x] Store complete candidate lineage.
- [x] Add accepted, rejected, and inconclusive comparison fixtures.

### Deliverables

- Candidate generator
- Reproducible comparison runner
- Diff and lineage records
- Holdout workflow

### Exit criteria

- A comparison can be reproduced from its saved manifest.
- Candidate generation cannot rewrite its own tests or gates.

## Lab Phase 5 — Promotion, rollback, and evaluator improvement

### Objective

Complete both controlled improvement loops.

### Tasks

- [x] Implement configurable hard and soft promotion gates.
- [x] Require no security, protected-argument, or numerical-consistency regression.
- [x] Require improvement on the targeted failure cluster.
- [x] Require non-declining holdout performance.
- [x] Require human approve/reject/review decision.
- [x] Persist immutable promotion decisions.
- [x] Implement rollback through the active-candidate pointer.
- [x] Identify low-confidence or disputed judge results.
- [x] Create judge calibration datasets from human labels.
- [x] Compare judge agreement, false positives, and false negatives.
- [x] Version judge rubrics independently of agent prompts.
- [x] Demonstrate a judge-rubric improvement without changing agent behavior.
- [x] Add end-to-end tests for both improvement loops.

### Deliverables

- Promotion engine
- Human approval record
- Rollback support
- Evaluator calibration workflow

### Exit criteria

- The agent- and evaluator-improvement loops are visibly distinct.
- Nothing is promoted automatically.

## Lab Phase 6 — CLI, dashboard services, and example

### Objective

Expose the implemented workflows through a CLI, query services, documentation, and one runnable example.

### Tasks

- [x] Implement CLI commands for dataset validation, experiment run, comparison, failures, annotations, candidates, and promotion.
- [x] Implement query services for session, trace, evaluator, failure, experiment, and promotion views.
- [x] Support dashboard navigation: sessions -> traces -> node/tool detail -> evaluator explanation.
- [x] Create a calculator-agent example with deterministic tools.
- [x] Run a complete improvement cycle against the calculator-agent example.
- [x] Document how a new runtime integrates with the Lab.
- [x] Document Pydantic Evals integration.
- [x] Publish saved sample reports requiring no model key.
- [x] Add CI for unit tests, typing, linting, evaluator self-tests, and the fake-runtime smoke suite.

### Deliverables

- CLI
- Dashboard query layer
- Calculator-agent example
- Integration guide
- CI and sample artifacts

### Exit criteria

- The calculator-agent example uses only public Lab contracts and services.
- Its saved baseline, candidate, comparison, and promotion records can be reproduced locally.

## 11. Lab success criteria

- A runtime implementing `AgentRuntime` can execute a versioned evaluation dataset.
- Cases, datasets, prompts, candidates, and reports are versioned.
- Tool name, arguments, order, recovery, and redundancy are evaluated.
- Sessions are evaluated for context, repetition, contradictions, numerical consistency, and style.
- Observed sessions can enter an SME review inbox through explicit sampling rules.
- Confirmed failures become regression cases.
- Candidates cannot modify their own evaluation criteria.
- Baseline and candidate are compared reproducibly.
- Holdout and hard gates control promotion.
- Promotion requires a recorded human decision.
- Judge improvement is calibrated separately from agent improvement.
- The calculator-agent example runs end to end.

## 12. Implementation order

```text
L0 foundation
 -> L1 contracts/storage
 -> L2 eval execution
 -> L3 failures/review
 -> L4 candidates/comparison
 -> L5 promotion/calibration
 -> L6 CLI/example
```
