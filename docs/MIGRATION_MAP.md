# Enterprise Agent Improvement Lab Migration Map

This map records the completed transition from the original narrow Lab contracts to the enterprise architecture.

| Area | Original responsibility | Enterprise responsibility | Final status |
| --- | --- | --- | --- |
| `contracts/cases.py` | Conversational and tool-focused cases | Enterprise cases with state, approvals, actions, invariants, and business outcomes | COMPLETE |
| `contracts/traces.py` | Turn and tool-call traces | Generic enterprise execution traces with typed events | COMPLETE |
| `contracts/candidates.py` | Prompt/config candidate contracts | Enterprise artifacts, candidates, change types, and improvement scope | COMPLETE |
| `contracts/evaluation.py` | Case and aggregate evaluation reports | Enterprise evaluation results across operational and business dimensions | COMPLETE |
| `contracts/experiments.py` | Run and comparison contracts | Environment snapshots, enterprise comparison, and risk-aware promotion evidence | COMPLETE |
| `contracts/failures.py` | Scores, failures, annotations, and sampling | Enterprise failure taxonomy and production sampling reasons | COMPLETE |
| `contracts/sessions.py` | Session-level evaluation | Session support where conversational evaluation needs it | KEPT |
| `contracts/calibration.py` | Judge calibration contracts | Provider-neutral judge calibration | KEPT |
| `runtime.py` | Small runtime protocol | Provider-neutral `EnterpriseRuntime` boundary | COMPLETE |
| old runner module | Pydantic Evals-specific execution | Provider-neutral enterprise runner architecture | REMOVED |
| old candidate generator module | Prompt/config candidate generation | Specialized bounded enterprise candidate builders | REMOVED |
| `comparison.py` | Baseline/candidate comparison | Enterprise-aware comparison with hard and risk-weighted regressions | COMPLETE |
| `promotion.py` | Promotion eligibility and decisions | Risk-aware promotion evidence with human authority | COMPLETE |
| `failure_mining.py` | Failure normalization and clustering | Enterprise failure mining and root-cause inputs | COMPLETE |
| `review.py` | Human review workflows | Root-cause, candidate, comparison, and promotion review | COMPLETE |
| `sampling.py` | Session sampling rules | Enterprise operational and production evidence sampling | COMPLETE |
| `evaluators/` | Tool, safety, session, and operational checks | Enterprise evaluator families for state, authorization, approval, workflow, tools, delegation, retrieval, and business outcomes | COMPLETE |
| `storage/` | SQLite persistence | Storage ports with SQLite as the v0.1 implementation | COMPLETE |
| `dashboard.py` | Read-only query views | Enterprise review and evidence query views | COMPLETE |
| `cli.py` | Basic CLI workflows | Enterprise evaluation and review workflows | COMPLETE |
| `serialization.py` | Stable serialization | Stable enterprise contract serialization | KEPT |
| `datasets.py` | Dataset validation and helpers | Enterprise dataset validation and helpers | COMPLETE |
| `examples/calculator_agent` | Deterministic reference example | Deterministic regression example | KEPT |
| `integrations/enterprise_agent_harness/` | No original equivalent | Enterprise Agent Harness boundary adapter | ADDED |
| governance contracts | No original equivalent | Evidence references, redaction, retention, and tenant boundaries | ADDED |
| multi-agent contracts | No original equivalent | System candidate and system evaluation support | ADDED |
| package name | `agent_improvement_lab` | `enterprise_agent_improvement_lab` | COMPLETE |
| distribution name | original project distribution | `enterprise-agent-improvement-lab` | COMPLETE |

## Migration rule

The transition changed domain models before replacing stable lifecycle behavior.

The project did not keep a permanent compatibility architecture.

Old public domain classes were removed after required consumers migrated.

Bounded wire-format aliases can remain only where stored data still needs them.

## Canonical public direction

New integrations should use the enterprise contracts directly.

Important canonical concepts include:

- `ExecutionTrace`;
- `EnterpriseEvaluationCase`;
- `EnterpriseAgentCandidate`;
- `EnterpriseCandidateChange`;
- `ImprovementScope`;
- `EnvironmentSnapshot`;
- `RootCauseHypothesis`;
- `ImprovementPlan`;
- provider-neutral evaluation runners;
- enterprise comparison and promotion contracts.

See `API_MIGRATION.md` for removed API names and their replacements.

## Highest-risk migrations completed

1. Execution trace redesign.
2. Candidate and artifact redesign.
3. Enterprise case contracts.
4. Enterprise Agent Harness integration boundary.
5. Comparison and promotion semantics.
6. Runner abstraction.
7. Evidence governance and persistence boundaries.
8. System-level evaluation.

These areas now use the enterprise architecture as the implementation surface.
