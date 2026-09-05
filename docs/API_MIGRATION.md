# Public API migration

The enterprise contract migration is complete for the Lab core in v0.1. The
old conversation- and prompt-shaped modules were retained while consumers
migrated, then removed once the full test suite used the enterprise contracts.

| Removed API | Enterprise replacement | Status |
| --- | --- | --- |
| `AgentTrace` | `ExecutionTrace` with typed execution events | Removed |
| `ObservedTurn` | `MessageEvent`, plus typed event contracts | Removed |
| `ObservedToolCall` | `ToolCallEvent` | Removed |
| `TraceSummary` | `ExecutionTraceSummary` | Removed |
| `AgentCandidate` | `EnterpriseAgentCandidate` | Removed |
| `PromptArtifact` | `CandidateArtifact` | Removed |
| `PromptArtifactKind` | `CandidateArtifactKind` | Removed |
| `CandidateScope` | `ImprovementScope` | Removed |
| `CandidateChange` | `EnterpriseCandidateChange` and `ChangeKind` | Removed |
| `EvaluationCaseRef` | `EnterpriseEvaluationCase` | Removed |
| `ToolCallExpectation` | typed `ActionExpectation` | Removed |
| `CaseEvaluationResult` / `LabEvaluationReport` | enterprise evaluation result/report contracts | Removed |
| `enterprise_agent_improvement_lab.runner.PydanticEvalsRunner` | `enterprise_agent_improvement_lab.runners.PydanticEvalsRunner` | Old module removed; optional adapter retained |
| `ComparisonRunner` | `EnterpriseComparisonRunner` or comparison functions | Removed |

The canonical names above are the only supported public contracts. Importing
the removed modules or names is expected to fail. This is verified by the API
migration tests.

Some model fields still accept old serialized field names through Pydantic
validation aliases, and old case data can be normalized into typed action
expectations during deserialization. These are bounded wire-format migration
paths, not legacy classes or runtime APIs; new producers must emit canonical
enterprise fields. They can be removed in a future schema-version migration
after stored data has been reserialized.

The canonical package name is `enterprise_agent_improvement_lab`.

## Harness artifact model

Active candidate APIs use `skills`, `SKILL_CONFIGURATION`,
`SKILL_ADDITION`, and `SKILL_REMOVAL`. Root-cause hypotheses use
`affected_skill`. The Harness adapter uses exact `prompt_ref`, `skill_refs`,
`tool_refs`, and `policy_refs` values. It does not build obsolete
`capabilities`, `allowed_tools`, or AgentConfig `policies` fields.

The adapter may materialize Lab prompt and skill artifacts into the current
[Enterprise Agent Harness](https://github.com/etimbukafia/enterprise-agent-harness)
`PromptDefinition` and `SkillDefinition` contracts. A skill dependency is not
tool authority.
