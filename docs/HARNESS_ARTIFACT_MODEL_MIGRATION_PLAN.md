# Harness Artifact Model Migration Plan

Status: proposed implementation plan

## 1. Goal

Migrate Enterprise Agent Improvement Lab to the current Enterprise Agent Harness artifact model.

Harness repository:

https://github.com/etimbukafia/enterprise-agent-harness

The Lab must understand these Harness component types:

```text
Agent
Prompt
Skill
Tool
Policy
```

The Lab must continue to own evaluation, root-cause analysis, bounded candidate improvement, comparison, and promotion evidence.

The Harness must continue to own governed runtime construction, permissions, policies, approvals, tool execution, state, runtime authority, and exact resolved build provenance.

The migration is forward-only.

Do not keep the old Harness Capability model as an active Lab concept.

---

## 2. Boundary after migration

The target relationship is:

```text
Operational or evaluation evidence
        |
        v
Enterprise Agent Improvement Lab
  -> failure analysis
  -> root-cause hypothesis
  -> bounded change plan
  -> candidate artifacts
        |
        v
Enterprise Agent Harness adapter
  -> PromptDefinition
  -> SkillDefinition
  -> ToolDefinition or exact tool reference
  -> PolicyDefinition or exact policy reference
  -> AgentConfig
        |
        v
Enterprise Agent Harness
  -> validation
  -> governed build
  -> ResolvedAgentManifest
  -> governed execution
  -> execution trace
        |
        v
Enterprise Agent Improvement Lab
  -> evaluate
  -> compare
  -> promotion evidence
  -> human decision
```

The Lab must not become a second runtime.

The Lab must not duplicate Harness authority logic.

The Lab must not add CX operational gap diagnosis such as `SKILL_GAP`, `PROMPT_GAP`, `TOOL_GAP`, or `AGENT_GAP` to its core contracts.

---

# Phase 0 - Baseline and migration inventory

## Goal

Confirm every Lab contract and integration path that still depends on the old Harness Capability model.

## Tasks

- [ ] Inspect the current Lab architecture, contracts, storage, serialization, examples, and tests.
- [ ] Inspect `src/enterprise_agent_improvement_lab/integrations/enterprise_agent_harness/`.
- [ ] Inspect current candidate and improvement contracts.
- [ ] Inspect environment snapshot contracts.
- [ ] Inspect trace translation and production-ingestion paths.
- [ ] Inspect candidate builders and change-planning logic.
- [ ] Inspect current SQLite persistence and JSON serialization.
- [ ] Inspect docs and examples for active Capability terminology.
- [ ] Inspect the current Harness public API at https://github.com/etimbukafia/enterprise-agent-harness.
- [ ] Record the exact current Harness contracts used by the adapter.
- [ ] Record all Lab fields, enums, aliases, tests, and fixtures that must migrate.

## Required migration inventory

At minimum, inspect active uses of:

```text
CAPABILITY_CONFIGURATION
CAPABILITY_ADDITION
CAPABILITY_REMOVAL
capabilities
affected_capability
HarnessComponentKind.CAPABILITY
capability_registry_version
registry.capabilities
AgentConfig.capabilities
AgentConfig.allowed_tools
AgentConfig.policies
```

## Exit criteria

The full migration surface is known before implementation begins.

---

# Phase 1 - Replace Capability with Skill in Lab core contracts

## Goal

Use one consistent artifact vocabulary across active Lab contracts.

## Candidate artifact migration

Replace active candidate artifact kinds such as:

```text
CAPABILITY_CONFIGURATION
```

with:

```text
SKILL_CONFIGURATION
```

## Change-kind migration

Replace:

```text
CAPABILITY_ADDITION
CAPABILITY_REMOVAL
```

with:

```text
SKILL_ADDITION
SKILL_REMOVAL
```

Keep all other valid change kinds unchanged.

## Candidate model migration

Replace active candidate fields such as:

```text
capabilities
```

with:

```text
skills
```

The values must remain Lab-side exact references or stable IDs.

Do not import Harness model types into Lab core contracts.

## Root-cause migration

Replace:

```text
affected_capability
```

with:

```text
affected_skill
```

Do not add a second parallel field.

## Improvement decision migration

Replace active planner decisions that use Capability terminology with Skill terminology.

Example target:

```text
SKILL_ADDITION
SKILL_REMOVAL
```

## Forward-only rule

Do not preserve active aliases such as:

```text
CAPABILITY_ADDITION = SKILL_ADDITION
```

unless a real released-data compatibility need is proved.

The default target is one architecture and one vocabulary.

## Tasks

- [ ] Update `CandidateArtifactKind`.
- [ ] Update `ChangeKind`.
- [ ] Update `EnterpriseAgentCandidate`.
- [ ] Update `RootCauseHypothesis`.
- [ ] Update `ImprovementDecision`.
- [ ] Update validators and serialization.
- [ ] Update storage mappings.
- [ ] Update CLI and report output.
- [ ] Update tests and fixtures.

## Exit criteria

The Lab core has one reusable competence term: Skill.

---

# Phase 2 - Add first-class Prompt and Skill component references to candidate provenance

## Goal

Make Lab candidates identify the exact prompt and skill components that a Harness build should use.

## Candidate target

A candidate should be able to express exact component intent equivalent to:

```text
agent_id
agent_version
prompt
skills
tools
policies
runtime_profile
model_configuration
memory_configuration
retrieval_configuration
routing_configuration
approval_configuration
workflow_configuration
```

The exact Lab field shape can remain provider-neutral and Harness-neutral.

Do not use Harness `ComponentReference` directly in core contracts.

## Prompt candidate semantics

The Lab already supports prompt-oriented artifacts.

Keep useful Lab evaluation artifacts such as:

```text
SYSTEM_PROMPT
DEVELOPER_PROMPT
USER_TEMPLATE
```

where they still serve evaluation and bounded candidate generation.

Do not treat these as direct Harness runtime artifacts.

The Harness runtime uses one exact `PromptDefinition` reference per agent.

The adapter must convert the selected bounded prompt candidate into one exact Harness prompt artifact.

## Skill candidate semantics

A candidate must be able to identify exact skills independently of tools.

This distinction must remain explicit:

```text
Skill dependency != executable authority
```

A candidate that adds a skill must not automatically gain executable access to every tool referenced by that skill.

Agent tool authority must be changed separately when intended.

## Tasks

- [ ] Add or rename candidate prompt reference fields only where needed.
- [ ] Add or rename candidate skill reference fields.
- [ ] Preserve exact version identity.
- [ ] Preserve candidate lineage and artifact checksums.
- [ ] Preserve immutable candidate behavior.
- [ ] Add validation that skill and tool lists are separate.
- [ ] Add tests for prompt-only, skill-only, and tool-only candidate changes.

## Exit criteria

A Lab candidate can express exact prompt, skill, tool, and policy intent without collapsing those components together.

---

# Phase 3 - Migrate Harness integration component contracts

## Goal

Update the Lab-side Harness boundary to the current Harness component model.

## HarnessComponentKind target

Replace:

```text
AGENT
TOOL
CAPABILITY
POLICY
APPROVAL_POLICY
RUNTIME_PROFILE
PROVIDER
```

with:

```text
AGENT
PROMPT
SKILL
TOOL
POLICY
APPROVAL_POLICY
RUNTIME_PROFILE
PROVIDER
```

Do not keep active `CAPABILITY` support.

## HarnessRegistryReference

Keep this Lab-side boundary type if it remains useful.

It must support exact identity for:

```text
prompt
skill
tool
policy
agent
```

Preserve version and source-artifact lineage.

## Tasks

- [ ] Add `PROMPT`.
- [ ] Replace `CAPABILITY` with `SKILL`.
- [ ] Update reference coercion.
- [ ] Update identity validation.
- [ ] Update artifact-to-registry-reference mapping.
- [ ] Update tests.

## Exit criteria

The Lab adapter understands the current Harness component vocabulary.

---

# Phase 4 - Rewrite AgentConfig translation

## Goal

Build valid current Harness `AgentConfig` objects through the adapter.

## Remove old AgentConfig fields

The adapter must stop building:

```text
capabilities
allowed_tools
policies
```

## Build current AgentConfig fields

The adapter must build:

```text
prompt_ref
skill_refs
tool_refs
policy_refs
```

Use exact current Harness `ComponentReference` and `ComponentType` values inside the adapter only.

The Lab core must remain free of Harness imports.

## Prompt rule

The adapter must no longer state that Harness has no prompt field.

The adapter must resolve or build one exact prompt artifact and use its exact reference in `AgentConfig.prompt_ref`.

Do not copy prompt text into arbitrary metadata.

## Skill rule

The adapter must translate Lab skill references into exact Harness `skill_refs`.

A skill reference does not grant tool authority.

## Tool rule

The adapter must translate only intended executable tool access into exact Harness `tool_refs`.

Tool dependency and tool authority must remain separate.

## Policy rule

Translate policy references into exact Harness `policy_refs`.

Do not bypass policy or approval checks through candidate metadata.

## Tasks

- [ ] Update allowed AgentConfig field filtering.
- [ ] Update component-reference construction.
- [ ] Update prompt resolution.
- [ ] Update skill resolution.
- [ ] Update tool resolution.
- [ ] Update policy resolution.
- [ ] Preserve runtime profile, provider profile, runtime limits, state strategy, memory strategy, template, risk, and approval requirements.
- [ ] Validate through the real Harness `AgentConfig` contract.
- [ ] Add integration tests against the current Harness.

## Exit criteria

A Lab candidate can become a valid current Harness `AgentConfig` without obsolete fields.

---

# Phase 5 - Add PromptDefinition and SkillDefinition candidate materialization

## Goal

Allow bounded Lab changes to become exact Harness artifacts before build.

## Prompt materialization

When the candidate changes prompt behavior, materialize one exact Harness `PromptDefinition`.

The prompt must preserve:

```text
prompt_id
version
purpose
instructions
owner
lifecycle
metadata
```

Use only the exact current Harness fields after inspection.

Prompt version must change when prompt behavior changes.

Prompt provenance must link back to the Lab candidate artifact or change record.

## Skill materialization

When the candidate adds or changes a skill, materialize one exact Harness `SkillDefinition`.

Preserve:

```text
skill_id
version
name
description
supported intents or operations
required tool refs
optional tool refs
owner
risk
lifecycle
metadata
```

Use only fields present in the current Harness contract.

## Authority rule

Materializing a SkillDefinition must not grant agent execution authority.

If the candidate intends to execute a new tool, the candidate must also change the agent's exact `tool_refs`.

## Tasks

- [ ] Add bounded prompt-to-Harness materialization.
- [ ] Add bounded skill-to-Harness materialization.
- [ ] Preserve exact versions.
- [ ] Preserve content hashes and Lab lineage.
- [ ] Register candidate prompt and skill artifacts only in the candidate evaluation registry scope.
- [ ] Add behavior tests.

## Exit criteria

Prompt and skill changes become exact governed Harness artifacts without hidden runtime mutation.

---

# Phase 6 - Update environment snapshots

## Goal

Capture the exact Harness environment required for reproducible evaluation.

## Remove old snapshot semantics

Stop reading:

```text
registry.capabilities
capability_registry_version
```

## Add current snapshot semantics

Capture equivalent information for:

```text
agent registry
prompt registry
skill registry
tool registry
policy registry or policy state
resolved manifest
registry snapshot identity
```

## EnvironmentSnapshot changes

Migrate active fields from Capability to Skill.

Add prompt registry provenance if the snapshot contract needs it.

Possible target fields:

```text
agent_registry_version
prompt_registry_version
skill_registry_version
tool_registry_version
policy_registry_version
agent_definition_hash
prompt_hashes
skill_hashes
tool_hashes
policy_hashes
```

Use the smallest contract that preserves reproducibility.

Do not duplicate complete Harness records when exact references, hashes, and snapshot identity are enough.

## Tasks

- [ ] Replace capability registry capture with skill registry capture.
- [ ] Add prompt registry capture.
- [ ] Preserve deterministic component hashing.
- [ ] Preserve provider, model, runtime, fixture, tenant, feature-flag, and external-stub evidence.
- [ ] Update snapshot persistence.
- [ ] Update comparison logic that depends on environment identity.
- [ ] Update tests.

## Exit criteria

Evaluation snapshots can distinguish prompt, skill, tool, and policy changes exactly.

---

# Phase 7 - Use ResolvedAgentManifest as authoritative build provenance

## Goal

Use Harness-owned resolved build evidence instead of reconstructing the final graph from Lab metadata.

## Required provenance

After the Harness builds a candidate, the Lab should preserve references equivalent to:

```text
candidate_id
manifest_id
manifest_digest
registry_snapshot_id
agent_ref
prompt_ref
skill_refs
tool_refs
policy_refs
runtime profile
provider profile
```

Use the exact current Harness manifest contract.

## Rule

The Harness manifest is authoritative for what was actually built.

The Lab candidate remains authoritative for what the Lab proposed.

These are related but not interchangeable.

The Lab must be able to detect disagreement between proposed candidate intent and resolved Harness build provenance.

## Tasks

- [ ] Read manifest provenance after build.
- [ ] Store safe exact references in Lab candidate execution evidence.
- [ ] Preserve manifest digest.
- [ ] Preserve registry snapshot identity.
- [ ] Compare requested versus resolved component identity.
- [ ] Fail candidate preparation or evaluation when required component identity does not match.
- [ ] Add tamper/provenance tests.

## Exit criteria

Every evaluated Harness candidate has exact, tamper-evident build provenance.

---

# Phase 8 - Update Harness trace translation

## Goal

Preserve truthful prompt and skill provenance in Lab execution traces.

## Prompt evidence

Translate safe exact prompt references from Harness trace or manifest evidence.

Do not copy full prompt instructions into execution traces by default.

## Skill evidence

Translate exact skills available to the execution.

Do not infer skill selection from tool execution.

Only represent `skill.selected` or equivalent when Harness emits a real explicit selection signal.

## Tool evidence

Continue to translate tool calls, results, permission failures, policy decisions, approvals, retries, and errors.

Keep tool authority separate from skill dependency information.

## Safe trace metadata

Review the adapter safe metadata allowlist.

Add prompt and skill reference fields only when they are safe exact provenance.

Do not add hidden reasoning or raw prompt text.

## Tasks

- [ ] Add prompt provenance translation.
- [ ] Add skill availability provenance translation.
- [ ] Preserve exact tool and policy evidence.
- [ ] Update safe metadata keys.
- [ ] Update production trace ingestion where needed.
- [ ] Add tests proving no false skill-selection claim.

## Exit criteria

Lab traces preserve the new Harness provenance without inventing runtime facts.

---

# Phase 9 - Update bounded change builders

## Goal

Make candidate builders understand prompt, skill, and tool changes as separate operations.

## Required change semantics

### PROMPT_CHANGE

Changes behavior instructions.

It must produce a new exact prompt candidate artifact and Harness prompt version.

It must not change runtime authority.

### SKILL_ADDITION

Adds one exact reusable skill to the candidate agent.

It must not automatically grant every tool referenced by that skill.

### SKILL_REMOVAL

Removes one exact skill reference from the candidate agent.

Tool authority must only change if the change plan explicitly includes a tool change.

### TOOL_ADDITION

Can mean one of these bounded operations:

```text
attach an approved existing tool to the agent
register a candidate tool artifact
add or update a skill dependency
```

Do not collapse these into one hidden operation.

The change record must show which component relationship changed.

### TOOL_REMOVAL

Remove explicit agent authority only when intended.

Removing a tool from one skill dependency does not necessarily mean removing it from agent authority or another skill.

## Tasks

- [ ] Update candidate builders from Capability to Skill terminology.
- [ ] Add prompt materialization paths.
- [ ] Add skill addition/removal paths.
- [ ] Make tool-authority changes explicit.
- [ ] Make skill-dependency changes explicit.
- [ ] Preserve protected-component and improvement-scope enforcement.
- [ ] Preserve deterministic builder behavior.
- [ ] Add behavior tests for each supported change class.

## Exit criteria

Candidate builders produce bounded, reviewable component changes with no implicit authority expansion.

---

# Phase 10 - Update root-cause and improvement planning

## Goal

Use the new component vocabulary without changing Lab responsibility.

## Root-cause scope

The Lab may conclude that an evaluated candidate failed because of:

```text
prompt behavior
skill configuration or availability
tool behavior or availability
policy constraint
permission constraint
approval behavior
routing
model behavior
retrieval
memory
workflow
```

This is evaluation root cause.

It is not CX operational opportunity diagnosis.

## Important boundary

Do not add these Autopilot concepts to Lab core contracts:

```text
AGENT_GAP
PROMPT_GAP
SKILL_GAP
TOOL_GAP
POLICY_CONSTRAINT as operational gap taxonomy
APPROVAL_FRICTION as operational opportunity taxonomy
```

The Lab may receive an upstream proposed component change.

The Lab still decides whether that candidate performs better and remains safe.

## Tasks

- [ ] Replace capability-specific root-cause fields with skill terminology.
- [ ] Update planner decision mapping.
- [ ] Preserve deterministic planning bounds.
- [ ] Preserve human-review escalation.
- [ ] Preserve prior-experiment evidence.
- [ ] Update tests.

## Exit criteria

The Lab can analyze skill-related evaluated failures without becoming an operational opportunity engine.

---

# Phase 11 - Update comparison and promotion evidence

## Goal

Compare baseline and candidate at exact component boundaries.

## Required comparison evidence

Comparison should be able to explain differences such as:

```text
prompt A@1.0.0 -> prompt A@1.1.0
skill refund_resolution@1.0.0 -> @1.1.0
tool get_tracking absent -> @1.0.0
policy unchanged
runtime unchanged
```

Do not rely only on generic candidate IDs.

## Promotion evidence

Promotion records should preserve:

```text
baseline candidate
candidate candidate
baseline manifest digest
candidate manifest digest
component changes
evaluation evidence
regression evidence
risk evidence
human decision
```

The Harness must not make the Lab promotion decision.

The Lab must not deploy production changes automatically.

## Tasks

- [ ] Add component-diff evidence where useful.
- [ ] Preserve exact manifest identity in comparison.
- [ ] Preserve environment snapshot identity.
- [ ] Update promotion records.
- [ ] Update human review output.
- [ ] Add tests for prompt-only, skill-only, and tool-only comparisons.

## Exit criteria

Promotion evidence states exactly what changed and what was evaluated.

---

# Phase 12 - Update storage and serialization

## Goal

Persist the new forward-only contract cleanly.

## Migration scope

Update active persisted forms for:

```text
capabilities -> skills
capability configuration -> skill configuration
capability addition/removal -> skill addition/removal
capability registry version -> skill registry version
prompt registry provenance
manifest provenance
```

## Forward-only rule

Prefer one current schema baseline.

Do not keep permanent dual schemas for old Capability and new Skill records.

If repository-generated fixtures or example artifacts use the old schema, regenerate them.

If migration tests are needed for a released persisted format, keep them focused and explicit.

## Tasks

- [ ] Update Pydantic serialization.
- [ ] Update SQLite schema/mapping.
- [ ] Update stored JSON reports.
- [ ] Update fixture loaders.
- [ ] Update generated example artifacts.
- [ ] Update serialization tests.
- [ ] Remove unused compatibility code.

## Exit criteria

Stored Lab state uses one current artifact model.

---

# Phase 13 - Update reference examples

## Goal

Demonstrate the Skill versus Tool boundary through the reference evaluation cycle.

## Existing reference slice

Keep the delivery-resolution missing-tool example, but express it in current terms.

Target concept:

```text
Baseline agent
  -> delivery_resolution@1.0.0
  -> direct executable tools do not include get_tracking

Evaluation failure
  -> missing operation evidence

Root cause
  -> required tool unavailable for the evaluated behavior

Change
  -> TOOL_ADDITION

Candidate
  -> exact get_tracking tool reference added to agent authority
  -> skill dependency updated only if needed

Harness
  -> builds exact candidate

Lab
  -> reevaluates
  -> compares
  -> produces promotion evidence
  -> stops for human decision
```

## Add a separate skill example

Add a small scenario that proves Skill is not Tool.

Example:

```text
Payment tools already exist.
No duplicate-charge skill exists.

Candidate change:
SKILL_ADDITION

No new tool implementation is required.
```

The example should show that skill addition and tool addition are different candidate operations.

## Tasks

- [ ] Update calculator or enterprise examples where Capability terms remain active.
- [ ] Update baseline candidate JSON.
- [ ] Update candidate reports.
- [ ] Add a skill-addition example or fixture.
- [ ] Ensure generated artifacts use the current schema.

## Exit criteria

The examples clearly demonstrate prompt, skill, and tool changes as separate evaluation targets.

---

# Phase 14 - Documentation cleanup

## Goal

Make active Lab documentation match the new architecture.

## Update

Review and update at least:

```text
README.md
RELEASE_NOTES.md
docs/ARCHITECTURE.md
docs/DECISIONS.md
docs/INTEGRATION_GUIDE.md
docs/MIGRATION_MAP.md
docs/API_MIGRATION.md
docs/NON_GOALS.md
docs/V0_1_SCOPE.md
```

## Documentation rules

Use the full Harness URL when referencing the runtime:

https://github.com/etimbukafia/enterprise-agent-harness

Do not mechanically replace every English use of `capability`.

Replace it only where it names the old Harness/Lab artifact concept.

Preserve historical records where they describe past architecture.

Add a new architecture decision if an old decision is superseded.

Do not silently rewrite historical decisions.

## Exit criteria

Current documentation describes the actual Prompt/Skill/Tool artifact model and Lab/Harness boundary.

---

# Phase 15 - Tests and quality gate

## Goal

Prove the migration through public behavior and integration boundaries.

## Required tests

Add or update behavior tests for:

- Lab candidate with exact prompt, skill, tool, and policy intent;
- Capability terminology removed from active candidate contracts;
- current Harness `AgentConfig` creation;
- exact `prompt_ref`, `skill_refs`, `tool_refs`, and `policy_refs` translation;
- prompt candidate materialization;
- skill candidate materialization;
- skill dependency not granting tool authority;
- tool addition not silently changing unrelated skills;
- skill addition not silently granting tools;
- current Harness registry snapshot capture;
- prompt and skill environment provenance;
- resolved manifest provenance;
- manifest digest preservation;
- trace translation of prompt and skill availability;
- no inferred skill-selection evidence;
- prompt-only comparison;
- skill-only comparison;
- tool-only comparison;
- promotion evidence with exact component changes;
- storage and serialization round trips;
- updated reference example cycle.

## Do not over-test

Do not test:

- private helper implementation;
- source-file inventories;
- import text;
- arbitrary constants;
- internal dictionary layout;
- duplicated branches already protected through stronger integration tests.

## Quality commands

Run the repository's configured quality gate.

At minimum:

```text
python -m ruff format --check src tests examples
python -m ruff check src tests examples
python -m mypy src
python -m compileall -q src tests examples
python -m pytest -q
git diff --check
```

Adjust only if the repository configuration requires a different exact command.

Do not finish with known failures.

## Exit criteria

The full suite passes against the current Harness integration.

---

# Final target model

The Lab should finish with this active model:

```text
EnterpriseAgentCandidate
  -> Agent identity/version
  -> Prompt candidate/reference
  -> Skill candidate/references
  -> Tool candidate/references
  -> Policy candidate/references
  -> runtime/model/memory/retrieval/routing/approval/workflow configuration
  -> immutable lineage
  -> typed changes

EnterpriseAgentHarnessAdapter
  -> materializes candidate PromptDefinition when needed
  -> materializes candidate SkillDefinition when needed
  -> resolves exact ToolDefinition references
  -> resolves exact PolicyDefinition references
  -> builds current AgentConfig
  -> invokes AgentFactory
  -> captures ResolvedAgentManifest
  -> translates execution trace

Enterprise Agent Improvement Lab
  -> evaluates
  -> diagnoses evaluated failure causes
  -> builds bounded candidate changes
  -> compares baseline and candidate
  -> produces promotion evidence
  -> stops for human decision
```

---

# Architecture invariants

The migration must preserve these rules.

1. The Lab does not own production runtime authority.
2. The Harness remains behind an integration adapter.
3. The Lab core remains provider-neutral and Harness-neutral.
4. Evaluation does not directly modify the production agent.
5. Candidate changes remain immutable and reviewable.
6. Prompt changes cannot grant runtime authority.
7. Skill changes cannot grant runtime authority.
8. Tool authority changes must be explicit.
9. Policy and approval checks remain Harness-owned at execution time.
10. Deterministic evaluation runs before optional LLM judgment where applicable.
11. Score is not evidence.
12. Expected evidence and observed evidence remain separate.
13. Root-cause hypotheses remain evidence-backed.
14. Improvement scope and protected components remain enforced.
15. Promotion evidence does not equal deployment authority.
16. Human authority remains final for promotion and production release.
17. Harness manifest provenance is authoritative for what was built.
18. Lab candidate provenance is authoritative for what was proposed.
19. Trace evidence must not claim skill selection without a real signal.
20. Operational CX gap diagnosis remains outside Lab core.

---

# Non-goals

Do not add:

- CX Autopilot opportunity discovery;
- `PROMPT_GAP`;
- `SKILL_GAP`;
- `TOOL_GAP`;
- `AGENT_GAP`;
- autonomous production deployment;
- autonomous self-modification;
- a second agent runtime;
- another policy engine;
- another approval engine;
- prompt optimization loops without bounded review;
- arbitrary generated code execution;
- business-specific skill taxonomies in Lab core.

---

# Recommended implementation passes

## Pass 1 - Core contract migration

Complete:

- Phase 0
- Phase 1
- Phase 2
- Phase 3

Exit with stable Prompt/Skill/Tool candidate semantics.

## Pass 2 - Harness integration migration

Complete:

- Phase 4
- Phase 5
- Phase 6
- Phase 7
- Phase 8

Exit with current Harness AgentConfig, registries, manifest provenance, and trace translation.

## Pass 3 - Improvement pipeline migration

Complete:

- Phase 9
- Phase 10
- Phase 11
- Phase 12

Exit with bounded change builders, root-cause planning, comparison, promotion evidence, and persistence on the new model.

## Pass 4 - Reference slice and cleanup

Complete:

- Phase 13
- Phase 14
- Phase 15

Exit with updated examples, docs, fixtures, and a clean quality gate.

---

# Final acceptance criteria

The migration is complete when all statements are true:

- Active Lab contracts use Skill, not Capability, for reusable competence.
- `CandidateArtifactKind` has a Skill configuration concept.
- `ChangeKind` supports `SKILL_ADDITION` and `SKILL_REMOVAL`.
- `RootCauseHypothesis` uses `affected_skill` instead of `affected_capability`.
- `EnterpriseAgentCandidate` tracks skills separately from tools.
- Prompt candidate artifacts can materialize one exact Harness `PromptDefinition`.
- Skill candidate changes can materialize one exact Harness `SkillDefinition`.
- `HarnessComponentKind` includes `PROMPT` and `SKILL` and no active `CAPABILITY`.
- The Harness adapter uses current `prompt_ref`, `skill_refs`, `tool_refs`, and `policy_refs`.
- The adapter no longer uses `capabilities`, `allowed_tools`, or old `policies` AgentConfig fields.
- The Lab captures prompt and skill registry provenance.
- The Lab captures Harness resolved manifest identity and digest.
- The Lab verifies proposed versus resolved component identity.
- Trace translation preserves prompt and skill provenance without false selection claims.
- Skill dependencies do not grant tool execution authority.
- Tool authority changes remain explicit candidate changes.
- Candidate comparison can show prompt-only, skill-only, and tool-only differences.
- Promotion evidence records exact component changes and resolved build provenance.
- Active storage and serialization use the new schema.
- Current examples use the new artifact model.
- Current documentation matches the implementation.
- No obsolete active Capability compatibility layer remains.
- The full formatting, lint, typing, compilation, and test quality gate passes.
