# Enterprise Agent Improvement Lab Migration Map

This map classifies the current project by what should happen during the enterprise transition.

| Current module | Current responsibility | Target responsibility | Class | Destination | Risk | Later phase |
| --- | --- | --- | --- | --- | --- | --- |
| `contracts/cases.py` | Dataset and case contracts | Enterprise cases with state, approvals, actions, invariants, business outcomes | GENERALIZE | `contracts/cases.py` | High | 6 |
| `contracts/traces.py` | Turn and tool-call traces | Generic enterprise execution traces with typed events | REDESIGN | `contracts/traces.py` | High | 1 |
| `contracts/candidates.py` | Prompt/config candidate contracts | Enterprise artifacts, candidates, change types, scope | REDESIGN | `contracts/candidates.py` | High | 2-3 |
| `contracts/evaluation.py` | Case and aggregate evaluation reports | Enterprise evaluation results across more dimensions | GENERALIZE | `contracts/evaluation.py` | Medium | 8, 13 |
| `contracts/experiments.py` | Run, comparison, promotion contracts | Add environment snapshots and risk-aware promotion | GENERALIZE | `contracts/experiments.py` | High | 5, 13, 14 |
| `contracts/failures.py` | Scores, failures, annotations, sampling | Expand failure taxonomy and enterprise sampling reasons | GENERALIZE | `contracts/failures.py` | Medium | 9, 16 |
| `contracts/sessions.py` | Session-level evaluation | Keep where useful for conversational/session agents | KEEP | `contracts/sessions.py` | Low | Later |
| `contracts/calibration.py` | Judge calibration contracts | Preserve provider-neutral judge calibration | KEEP | same | Low | Later |
| `runtime.py` | Small runtime protocol | Generic enterprise runtime adapter protocol | GENERALIZE | `runtime.py` | High | 1, 4 |
| `runner.py` | Pydantic Evals-backed evaluation runner | Runner abstraction with Pydantic as one backend | GENERALIZE | `runners/` | High | 20 |
| `candidates.py` | Constrained prompt/config candidate generation | Specialized bounded enterprise candidate builders | REDESIGN | `candidates/` or `builders/` | High | 12 |
| `comparison.py` | Baseline/candidate comparison | Enterprise-aware regressions and business/security dimensions | GENERALIZE | `comparison.py` | High | 13 |
| `promotion.py` | Promotion eligibility and decision services | Risk-aware enterprise promotion evidence | GENERALIZE | `promotion.py` | High | 14 |
| `failure_mining.py` | Failure normalization and clustering | Add root-cause inputs and enterprise components | GENERALIZE | `failure_mining.py` | Medium | 9-10 |
| `review.py` | Human review workflows | Preserve and expand for root causes and promotion evidence | GENERALIZE | `review.py` | Medium | 10, 14 |
| `sampling.py` | Session sampling rules | Enterprise operational sampling signals | GENERALIZE | `sampling.py` | Medium | 16 |
| `calibration.py` | Judge calibration services | Preserve | KEEP | same | Low | Later |
| `coverage.py` | Coverage summaries | Extend only when enterprise dimensions need it | GENERALIZE | same | Low | Later |
| `evaluators/base.py` | Evaluator protocol and trace helpers | Generic evaluator protocol over enterprise trace evidence | GENERALIZE | `evaluators/base.py` | High | 8 |
| `evaluators/tools.py` | Tool selection, arguments, trajectory | Preserve and add write/idempotency/side-effect checks | GENERALIZE | `evaluators/tools.py` | Medium | 8 |
| `evaluators/operational.py` | Latency, token, cost, loop, error rate | Preserve | KEEP | same | Low | 8 |
| `evaluators/safety.py` | Safety checks | Expand for authorization, policy, approval boundaries | GENERALIZE | `evaluators/safety.py` plus new families | High | 8 |
| `evaluators/sessions.py` | Cross-turn/session checks | Keep for session agents only | KEEP | same | Low | Later |
| `storage/sqlite.py` | SQLite persistence | Keep implementation, add new stores/contracts as needed | GENERALIZE | `storage/sqlite.py` | Medium | 19 |
| `storage/__init__.py` | Storage exports | Add storage protocols and enterprise records | GENERALIZE | `storage/` | Medium | 19 |
| `dashboard.py` | Read-only query views | Expand to root causes, candidate lineage, risk, promotion evidence | GENERALIZE | `dashboard.py` | Low | 21 |
| `cli.py` | CLI workflows | Expand enterprise commands after core stabilizes | GENERALIZE | `cli.py` | Low | 21 |
| `serialization.py` | Stable serialization | Preserve and extend for new contracts | KEEP | same | Low | As needed |
| `datasets.py` | Dataset validation and helpers | Preserve and extend for enterprise cases | GENERALIZE | same | Medium | 6 |
| `examples/calculator_agent` | Deterministic reference example | Preserve as regression example, add enterprise vertical slice later | KEEP | `examples/` | Low | v0.1 slice |
| `pyproject.toml` | Package and dependency metadata | Keep package name until enterprise contracts are real | KEEP | same | Low | 22 |
| package name | `agent_improvement_lab` | Rename only after architecture transition | REPLACE LATER | `enterprise_agent_improvement_lab` | Medium | 22 |

## Main migration rule

Do not replace stable evaluation lifecycle code because the current models are narrow.

Change the domain models first. Then adapt services around them.

## Compatibility strategy

Do not build a large backward-compatibility layer.

Use temporary conversion helpers where they protect the current calculator example during migration.

The main compatibility target is behavioral proof, not permanent legacy APIs.

## Highest-risk migrations

1. Trace redesign.
2. Candidate and artifact redesign.
3. Enterprise case contracts.
4. Harness integration boundary.
5. Comparison and promotion semantics.
6. Runner abstraction.

These need explicit review before implementation proceeds.
