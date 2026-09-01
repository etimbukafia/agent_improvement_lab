"""Behavior tests for isolated enterprise evaluation."""

# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from enterprise_agent_improvement_lab.contracts.cases import (
    ActionExpectation,
    ApprovalExpectation,
    CaseProvenance,
    DatasetSplit,
    EnterpriseEvaluationCase,
    FixtureReference,
    StateExpectation,
)
from enterprise_agent_improvement_lab.contracts.evaluation_environment import (
    ExternalServiceCall,
    ExternalServiceStubDefinition,
)
from enterprise_agent_improvement_lab.contracts.failures import (
    EvaluationFailure,
    FailureCategory,
    Severity,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    ApprovalDecision,
    ApprovalDecisionEvent,
    ApprovalRequestEvent,
    DelegationEvent,
    ExecutionTrace,
    RetrievalEvent,
    StateMutationEvent,
    ToolCallEvent,
    ToolCallOutcome,
    TriggerInfo,
    WorkflowTransitionEvent,
)
from enterprise_agent_improvement_lab.enterprise_runner import EnterpriseEvaluationRunner
from enterprise_agent_improvement_lab.environment import LocalEvaluationEnvironment
from enterprise_agent_improvement_lab.evaluators import (
    ApprovalExpirationRespected,
    CorrectDelegationAgent,
    DelegationLoopPrevention,
    ExpectedFinalState,
    ForbiddenMutation,
    ForbiddenWorkflowTransition,
    NoActionBeforeApproval,
    RequiredBeforeAction,
    RequiredWorkflowStep,
    RetrievalSourceCorrectness,
    TenantBoundary,
    ToolAccess,
    ToolSideEffectCorrectness,
    ValidTransition,
    default_enterprise_evaluators,
)
from enterprise_agent_improvement_lab.evaluators.base import EvaluationContext
from enterprise_agent_improvement_lab.failure_mining import infer_failure_category

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _case(**updates: object) -> EnterpriseEvaluationCase:
    values: dict[str, object] = {
        "case_id": "case-1",
        "dataset_id": "enterprise",
        "dataset_version": "1.0",
        "split": DatasetSplit.DEVELOPMENT,
        "provenance": CaseProvenance(source="test"),
    }
    values.update(updates)
    return EnterpriseEvaluationCase(**values)


def _trace(*events: object) -> ExecutionTrace:
    return ExecutionTrace(
        execution_id="execution-1",
        agent_id="agent",
        agent_version="1",
        candidate_id="candidate-1",
        case_id="case-1",
        trigger=TriggerInfo(kind="test"),
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
        events=tuple(events),
    )


class _WritingRuntime:
    async def execute(
        self,
        case: EnterpriseEvaluationCase,
        candidate: object,
        environment: LocalEvaluationEnvironment,
    ) -> ExecutionTrace:
        environment.state["order"] = {"status": "closed"}
        return _trace(
            StateMutationEvent(
                event_id="write",
                sequence=1,
                timestamp=NOW,
                mutation_id="m1",
                resource="order",
                operation="update",
            )
        )


class _FailingRuntime:
    async def execute(
        self,
        case: EnterpriseEvaluationCase,
        candidate: object,
        environment: LocalEvaluationEnvironment,
    ) -> ExecutionTrace:
        environment.state["written"] = True
        raise RuntimeError("runtime failed")


class _Stub:
    definition = ExternalServiceStubDefinition(
        stub_id="payments", version="1", service_name="payments"
    )

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def calls(self) -> tuple[ExternalServiceCall, ...]:
        return (
            ExternalServiceCall(
                stub_id="payments", sequence=0, operation="charge", occurred_at=NOW
            ),
        )


class _FixtureLoader:
    async def load(
        self, fixture: FixtureReference, environment: LocalEvaluationEnvironment
    ) -> None:
        environment.state[fixture.fixture_id] = fixture.version


@pytest.mark.asyncio
async def test_environment_resets_state_and_keeps_before_and_after_snapshots() -> None:
    environment = LocalEvaluationEnvironment(frozen_at=NOW)
    runner = EnterpriseEvaluationRunner(_WritingRuntime())
    case = _case(initial_state={"order": {"status": "open"}})

    first = await runner.run_case(case, object(), environment)
    second = await runner.run_case(case, object(), environment)

    assert first.initial_state is not None
    assert first.final_state is not None
    assert first.initial_state.state["order"]["status"] == "open"
    assert first.final_state.state["order"]["status"] == "closed"
    assert second.initial_state is not None
    assert second.initial_state.state["order"]["status"] == "open"


@pytest.mark.asyncio
async def test_failed_execution_still_captures_final_state_for_teardown() -> None:
    environment = LocalEvaluationEnvironment(frozen_at=NOW)
    result = await EnterpriseEvaluationRunner(_FailingRuntime()).run_case(
        _case(), object(), environment
    )

    assert isinstance(result.error, RuntimeError)
    assert result.final_state is not None
    assert result.final_state.state == {"written": True}
    assert result.failures[0].category == FailureCategory.INTEGRATION


@pytest.mark.asyncio
async def test_environment_stubs_external_services_and_captures_their_calls() -> None:
    stub = _Stub()
    environment = LocalEvaluationEnvironment(external_service_stubs=(stub,), frozen_at=NOW)

    await EnterpriseEvaluationRunner(_WritingRuntime()).run_case(_case(), object(), environment)

    assert stub.started and stub.stopped
    assert environment.external_side_effects[0].operation == "charge"


@pytest.mark.asyncio
async def test_environment_loads_declared_fixture_before_agent_execution() -> None:
    environment = LocalEvaluationEnvironment(fixture_loader=_FixtureLoader(), frozen_at=NOW)
    case = _case(fixtures=(FixtureReference(fixture_id="orders", version="1"),))

    result = await EnterpriseEvaluationRunner(_WritingRuntime()).run_case(
        case, object(), environment
    )

    assert result.initial_state is not None
    assert result.initial_state.state["orders"] == "1"


@pytest.mark.asyncio
async def test_enterprise_runner_creates_state_failure_with_snapshot_evidence(candidate) -> None:
    environment = LocalEvaluationEnvironment(frozen_at=NOW)
    case = _case(
        expected_final_state=(StateExpectation(path="order.status", expected_value="open"),)
    )
    result = await EnterpriseEvaluationRunner(
        _WritingRuntime(), evaluators=(ExpectedFinalState(),)
    ).run_case(case, candidate, environment)

    failure = result.failures[0]
    assert failure.category == FailureCategory.STATE
    assert failure.evidence_refs == ("case-1:final",)


def test_enterprise_evaluators_detect_prohibited_actions_and_boundaries() -> None:
    forbidden = _case(
        prohibited_actions=(
            ActionExpectation(action="delete", action_type="state_mutation", target="orders"),
        )
    )
    mutation_trace = _trace(
        StateMutationEvent(
            event_id="delete",
            sequence=1,
            timestamp=NOW,
            mutation_id="m",
            resource="orders",
            operation="delete",
        )
    )
    assert not ForbiddenMutation().evaluate(EvaluationContext(forbidden, mutation_trace)).passed

    tenant_case = _case(tenant_context={"tenant_id": "one"})
    tenant_trace = _trace(
        StateMutationEvent(
            event_id="cross",
            sequence=1,
            timestamp=NOW,
            mutation_id="m",
            resource="orders",
            operation="update",
        )
    ).model_copy(update={"tenant_id": "two"})
    assert not TenantBoundary().evaluate(EvaluationContext(tenant_case, tenant_trace)).passed


def test_enterprise_evaluators_detect_approval_workflow_delegation_and_side_effect_errors() -> None:
    approval_case = _case(required_approvals=(ApprovalExpectation(approval_id="a1", action="pay"),))
    action = ToolCallEvent(
        event_id="pay",
        sequence=1,
        timestamp=NOW,
        call_id="c",
        name="pay",
        outcome=ToolCallOutcome.SUCCESS,
    )
    assert (
        not NoActionBeforeApproval()
        .evaluate(EvaluationContext(approval_case, _trace(action)))
        .passed
    )
    assert (
        not RequiredBeforeAction().evaluate(EvaluationContext(approval_case, _trace(action))).passed
    )

    workflow_case = _case(
        expected_actions=(
            ActionExpectation(
                action="approve",
                action_type="workflow_transition",
                target="orders",
                arguments={"from_state": "new", "to_state": "approved"},
            ),
        )
    )
    transition = WorkflowTransitionEvent(
        event_id="bad",
        sequence=1,
        timestamp=NOW,
        workflow_id="orders",
        from_state="new",
        to_state="rejected",
        transition="approve",
    )
    assert (
        not ValidTransition().evaluate(EvaluationContext(workflow_case, _trace(transition))).passed
    )

    loop = _trace(
        DelegationEvent(
            event_id="d1",
            sequence=1,
            timestamp=NOW,
            delegation_id="d1",
            source_agent_id="a",
            target_agent_id="b",
        ),
        DelegationEvent(
            event_id="d2",
            sequence=2,
            timestamp=NOW,
            delegation_id="d2",
            source_agent_id="b",
            target_agent_id="a",
        ),
    )
    assert not DelegationLoopPrevention().evaluate(EvaluationContext(_case(), loop)).passed

    side_effect_case = _case(
        expected_final_state=(StateExpectation(path="order.status", expected_value="closed"),)
    )
    context = EvaluationContext(
        side_effect_case,
        _trace(action),
        final_state=LocalEvaluationEnvironment(frozen_at=NOW).snapshot("empty"),
    )
    result = ToolSideEffectCorrectness().evaluate(context)
    assert not result.passed
    assert result.failure_category == FailureCategory.TOOL_SIDE_EFFECT


def test_enterprise_evaluator_ids_map_to_enterprise_failure_categories() -> None:
    assert infer_failure_category("authorization.tenant_boundary") == FailureCategory.AUTHORIZATION
    assert infer_failure_category("approval.no_action_before_approval") == FailureCategory.APPROVAL
    assert infer_failure_category("delegation.loop_prevention") == FailureCategory.DELEGATION
    assert (
        infer_failure_category("tool.side_effect_correctness") == FailureCategory.TOOL_SIDE_EFFECT
    )
    assert infer_failure_category("state.transaction_integrity") == FailureCategory.DATA_INTEGRITY
    assert infer_failure_category("tool.retry_safety") == FailureCategory.RELIABILITY
    assert infer_failure_category("business.sla") == FailureCategory.BUSINESS_OUTCOME


def test_enterprise_catalog_has_all_required_stable_evaluator_ids() -> None:
    evaluator_ids = {evaluator.evaluator_id for evaluator in default_enterprise_evaluators()}

    assert evaluator_ids == {
        "state.expected_final_state",
        "state.forbidden_mutation",
        "state.invariant_preserved",
        "state.transaction_integrity",
        "authorization.tool_access",
        "authorization.resource_scope",
        "authorization.tenant_boundary",
        "authorization.role_boundary",
        "approval.required_before_action",
        "approval.correct_reviewer",
        "approval.no_action_before_approval",
        "approval.expiration_respected",
        "workflow.valid_transition",
        "workflow.required_step",
        "workflow.forbidden_transition",
        "workflow.completion",
        "tool.idempotency",
        "tool.side_effect_correctness",
        "tool.retry_safety",
        "tool.timeout_behavior",
        "tool.compensation_behavior",
        "delegation.correct_agent",
        "delegation.minimum_privilege",
        "delegation.context_integrity",
        "delegation.loop_prevention",
        "delegation.result_validation",
        "retrieval.source_correctness",
        "retrieval.authorization",
        "retrieval.freshness",
        "retrieval.grounding",
        "business.resolution_rate",
        "business.containment",
        "business.sla",
        "business.manual_work_reduction",
        "business.error_cost",
        "business.value_metric",
    }


def test_enterprise_failure_records_are_immutable() -> None:
    failure = EvaluationFailure(
        failure_id="failure-1",
        evaluator_id="authorization.tool_access",
        category=FailureCategory.AUTHORIZATION,
        severity=Severity.CRITICAL,
        trace_id="trace-1",
        summary="The tool access check failed.",
        expected_behavior="Only approved tools can run.",
        observed_behavior="A prohibited tool ran.",
        created_at=NOW,
    )

    with pytest.raises(Exception):
        failure.category = FailureCategory.QUALITY


def test_enterprise_catalog_uses_typed_action_and_trace_evidence() -> None:
    tool_case = _case(
        prohibited_actions=(ActionExpectation(action="pay", action_type="tool_call"),)
    )
    tool = ToolCallEvent(
        event_id="pay",
        sequence=1,
        timestamp=NOW,
        call_id="pay",
        name="pay",
        outcome=ToolCallOutcome.SUCCESS,
    )
    assert not ToolAccess().evaluate(EvaluationContext(tool_case, _trace(tool))).passed

    request = ApprovalRequestEvent(
        event_id="request",
        sequence=1,
        timestamp=NOW,
        approval_id="a",
        action="pay",
        expires_at=NOW + timedelta(seconds=1),
    )
    late = ApprovalDecisionEvent(
        event_id="late",
        sequence=2,
        timestamp=NOW + timedelta(seconds=2),
        approval_id="a",
        decision=ApprovalDecision.APPROVED,
    )
    assert (
        not ApprovalExpirationRespected()
        .evaluate(EvaluationContext(_case(), _trace(request, late)))
        .passed
    )

    requirement = ActionExpectation(
        action="approve",
        action_type="workflow_transition",
        target="orders",
        arguments={"to_state": "approved"},
    )
    transition = WorkflowTransitionEvent(
        event_id="transition",
        sequence=1,
        timestamp=NOW,
        workflow_id="orders",
        to_state="approved",
        transition="approve",
    )
    assert (
        RequiredWorkflowStep()
        .evaluate(EvaluationContext(_case(required_actions=(requirement,)), _trace(transition)))
        .passed
    )
    assert (
        not ForbiddenWorkflowTransition()
        .evaluate(EvaluationContext(_case(prohibited_actions=(requirement,)), _trace(transition)))
        .passed
    )

    delegation = DelegationEvent(
        event_id="delegate",
        sequence=1,
        timestamp=NOW,
        delegation_id="d",
        target_agent_id="risk",
    )
    required_delegation = ActionExpectation(
        action="delegate", action_type="delegation", target="risk"
    )
    assert (
        CorrectDelegationAgent()
        .evaluate(
            EvaluationContext(_case(required_actions=(required_delegation,)), _trace(delegation))
        )
        .passed
    )
    retrieval = RetrievalEvent(
        event_id="retrieve",
        sequence=1,
        timestamp=NOW,
        retrieval_id="r",
        source="policy-db",
    )
    required_retrieval = ActionExpectation(
        action="retrieve", action_type="retrieval", target="policy-db"
    )
    assert (
        RetrievalSourceCorrectness()
        .evaluate(
            EvaluationContext(_case(required_actions=(required_retrieval,)), _trace(retrieval))
        )
        .passed
    )
