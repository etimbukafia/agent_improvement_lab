# Runtime integration guide

Applications integrate with the Lab through the provider-neutral
`EnterpriseRuntime` protocol. The application or Harness adapter owns agent
execution; the Lab owns evaluation of the returned evidence.

```python
from enterprise_agent_improvement_lab import (
    EnterpriseAgentCandidate,
    EnterpriseEvaluationCase,
    ExecutionTrace,
)


class MyRuntime:
    name = "my-agent-runtime"
    version = "1.0.0"

    async def execute(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        environment,
    ) -> ExecutionTrace:
        # Run the application agent here and return typed execution events.
        ...
```

Every trace must preserve the case, candidate, agent, execution, and event
identities. Events must use aware UTC timestamps and unique sequence numbers.
Use `MessageEvent` for conversational evidence, `ToolCallEvent` for tools,
`StateReadEvent` and `StateMutationEvent` for state evidence, and the other
typed event contracts for approvals, delegations, workflows, external events,
human actions, retrieval, model calls, and errors. Store sensitive payloads in
the runtime only; trace summaries contain safe references and summaries.

The runtime should execute against the exact `EnvironmentSnapshot` referenced
by its `RunManifest`. The Harness integration collects Harness-specific
registry and runtime identity and translates it at the boundary; the Lab core
does not import Harness code.

## Enterprise Agent Harness artifact boundary

The optional adapter targets the current [Enterprise Agent Harness](https://github.com/etimbukafia/enterprise-agent-harness)
public contracts. It translates a Lab candidate into an `AgentConfig` with
one exact `prompt_ref`, separate `skill_refs`, explicit executable `tool_refs`,
and `policy_refs`. Lab `SYSTEM_PROMPT`, `DEVELOPER_PROMPT`, and `USER_TEMPLATE`
artifacts can materialize an immutable Harness `PromptDefinition`. A
`SKILL_CONFIGURATION` artifact can materialize an immutable `SkillDefinition`.

The four Lab candidate fields use the provider-neutral
`CandidateComponentReference` contract. Each reference is exact before it
reaches the adapter. The adapter does not select a registry version for a bare
component ID.

Skill tool dependencies describe discovery only. They do not grant execution
authority. The candidate must list every executable tool explicitly.

After a factory build, the adapter records the Harness `ResolvedAgentManifest`
ID, digest, registry snapshot ID, and exact component references. The manifest
describes what Harness built; the Lab candidate describes what the Lab proposed.

The trace adapter keeps safe component provenance. It drops uncontracted
`skill_selected` and `skill_selection` metadata. These values cannot become Lab
evidence without an explicit Harness selection event.

## Run an adapter

Pass a runtime object or zero-argument runtime factory as `module:attribute`.

```text
enterprise-agent-improvement-lab dataset validate examples/calculator_agent/dataset.json
enterprise-agent-improvement-lab experiment run \
  --dataset examples/calculator_agent/dataset.json \
  --candidate examples/calculator_agent/baseline_candidate.json \
  --manifest examples/calculator_agent/baseline_manifest.json \
  --runtime examples.calculator_agent.runtime:CalculatorRuntime \
  --database artifacts/calculator.sqlite3 \
  --report artifacts/calculator-baseline-report.json
```

The command stores the run, environment reference, traces, summaries, scores,
and sessions in SQLite. The report file is stable JSON.

## Improvement workflow

1. Validate a versioned enterprise dataset.
2. Run the baseline through an `EnterpriseRuntime`.
3. Normalize failed scores into failures.
4. Review failures with `annotations` commands.
5. Build a bounded `EnterpriseAgentCandidate` with typed artifacts and changes.
6. Compare baseline and candidate runs with compatible environment snapshots.
7. Evaluate promotion gates.
8. Record an explicit human decision.

The Lab never approves or deploys a candidate by itself.

## Dashboard query layer

`DashboardQueryService` is a read-only service for a UI or API. It exposes
session, execution trace, evaluator, failure, experiment, and promotion views.
