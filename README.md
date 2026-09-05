# Enterprise Agent Improvement Lab

Enterprise Agent Improvement Lab evaluates enterprise AI agents, diagnoses failures, creates bounded improvement plans, compares candidate versions, and produces evidence for human-controlled promotion.

The Lab is library-first and provider-neutral. Agent runtimes connect through small adapter boundaries. The Lab owns evaluation contracts, evidence, failure analysis, controlled improvement logic, comparison, and promotion records.

The Lab does not deploy agents or enforce production runtime policy. Those responsibilities stay with the runtime, such as [Enterprise Agent Harness](https://github.com/etimbukafia/enterprise-agent-harness).

## What it does

- validates versioned enterprise evaluation datasets and typed expectations;
- records typed execution traces for messages, tools, state, approvals, delegation, workflows, retrieval, and errors;
- runs deterministic evaluators and explicit subjective judges where required;
- mines failures and groups them into reviewable failure clusters;
- creates evidence-backed root-cause hypotheses;
- plans bounded improvements within explicit improvement scopes;
- builds typed candidate changes for prompts, skills, tools, policies, routing, models, workflows, approvals, and related configuration;
- compares candidates with baselines across quality, safety, authorization, state integrity, workflow, reliability, cost, and business outcomes;
- applies risk-aware promotion gates while keeping the final promotion decision human-controlled;
- supports offline, replay, shadow, and controlled canary evaluation evidence;
- governs evidence with redaction, retention, and tenant-boundary contracts;
- persists evaluation records through storage ports with SQLite as the included implementation;
- exposes CLI review workflows and read-only dashboard query services.

## Core flow

```text
EnterpriseEvaluationCase
        ↓
EnterpriseRuntime
        ↓
ExecutionTrace
        ↓
Evaluation
        ↓
EvaluationFailure
        ↓
FailureCluster
        ↓
RootCauseHypothesis
        ↓
ImprovementPlan
        ↓
Bounded candidate change
        ↓
Baseline comparison
        ↓
Risk-aware promotion evidence
        ↓
Human decision
```

## Runtime boundary

The Lab core does not import an agent framework or own production execution.

Applications can implement `EnterpriseRuntime` directly or use an integration adapter. [Enterprise Agent Harness](https://github.com/etimbukafia/enterprise-agent-harness) remains outside the Lab core and can provide governed runtime, prompt, skill, registry, approval, policy, tool, and execution evidence through that boundary.

See `docs/INTEGRATION_GUIDE.md` for the runtime integration model.

## Evaluation design

The Lab prefers deterministic evaluation when a requirement can be calculated from traces, state, or explicit expectations.

Subjective judges stay explicit and separate from deterministic evidence. A score does not replace the evidence that supports it.

## Controlled improvement

Candidate improvement is bounded by `ImprovementScope`.

The Lab can recommend and construct typed changes, but it does not generate unrestricted executable code. Protected datasets, evaluators, promotion rules, policies, permissions, and other protected resources remain outside the allowed change scope.

Promotion eligibility is computed from evidence. Human approval remains the final authority.

## Development

The repository targets Python 3.11–3.14. The local development interpreter is recorded in `.python-version`.

```text
python -m pip install -e ".[dev]"
python -m ruff format --check src tests examples
python -m ruff check src tests examples
python -m mypy src examples
python -m pytest
python -m compileall -q src tests examples
```

The same quality checks run in GitHub Actions.

Run the deterministic calculator reference cycle without a model key:

```text
python -m examples.calculator_agent.run_cycle
```

## Repository map

```text
docs/                                   architecture, decisions, migration, and integration guides
examples/                               deterministic reference examples
src/enterprise_agent_improvement_lab/   installable core package
src/enterprise_agent_improvement_lab/integrations/  external runtime integrations
src/enterprise_agent_improvement_lab/evaluators/    evaluator catalog
tests/                                  behavior and integration tests
```

## Key documentation

- `docs/ARCHITECTURE.md` — product and system boundaries.
- `docs/DECISIONS.md` — architecture decisions.
- `docs/INTEGRATION_GUIDE.md` — runtime integration guide.
- `docs/API_MIGRATION.md` — canonical enterprise API migration.
- `docs/PYDANTIC_EVALS.md` — optional Pydantic Evals integration.
- `docs/NON_GOALS.md` — explicit product limits.

## Version

Current package version: `0.1.0`.

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
