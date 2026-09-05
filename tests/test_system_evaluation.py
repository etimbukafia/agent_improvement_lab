from __future__ import annotations

from datetime import datetime, timezone

from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateArtifact,
    CandidateArtifactKind,
    EnterpriseAgentCandidate,
)
from enterprise_agent_improvement_lab.contracts.cases import CaseProvenance, DatasetSplit, RiskLevel
from enterprise_agent_improvement_lab.contracts.system import (
    SystemCandidate,
    SystemEvaluationCase,
    SystemInteractionConstraint,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    ApprovalDecision,
    ApprovalDecisionEvent,
    DelegationEvent,
    ExecutionTrace,
    MessageEvent,
    ToolCallEvent,
    ToolCallOutcome,
)
from enterprise_agent_improvement_lab.system import evaluate_system_execution

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _candidate(agent_id: str, tool: str = "orders.read") -> EnterpriseAgentCandidate:
    artifact = CandidateArtifact(
        artifact_id=f"definition-{agent_id}",
        name=agent_id,
        version="1.0.0",
        kind=CandidateArtifactKind.AGENT_DEFINITION,
        content=f'{{"agent_id":"{agent_id}"}}',
        created_at=NOW,
    )
    return EnterpriseAgentCandidate(
        candidate_id=f"candidate-{agent_id}",
        agent_id=agent_id,
        version="1.0.0",
        artifacts=(artifact.to_reference(),),
        tool_refs=(f"tool:{tool}@1.0.0",),
    )


def _case(**updates: object) -> SystemEvaluationCase:
    values: dict[str, object] = {
        "case_id": "system-case-1",
        "dataset_id": "system",
        "dataset_version": "1.0.0",
        "split": DatasetSplit.DEVELOPMENT,
        "risk": RiskLevel.HIGH,
        "agent_ids": ("supervisor", "worker"),
        "provenance": CaseProvenance(source="test"),
    }
    values.update(updates)
    return SystemEvaluationCase(**values)


def _trace(agent_id: str, *events: object, tenant_id: str | None = "tenant-1") -> ExecutionTrace:
    return ExecutionTrace(
        execution_id=f"execution-{agent_id}",
        agent_id=agent_id,
        agent_version="1.0.0",
        candidate_id=f"candidate-{agent_id}",
        case_id="system-case-1",
        tenant_id=tenant_id,
        started_at=NOW,
        ended_at=NOW,
        events=tuple(events)
        or (
            MessageEvent(
                event_id=f"message-{agent_id}",
                sequence=0,
                timestamp=NOW,
                message_id=f"message-{agent_id}",
                role="assistant",
            ),
        ),
    )


def test_system_report_preserves_multiple_agents_and_delegation_chain() -> None:
    case = _case(
        interaction_constraints=(
            SystemInteractionConstraint(
                constraint_id="delegation-1",
                source_agent_id="supervisor",
                target_agent_id="worker",
                allowed_target_agent_ids=("worker",),
            ),
        )
    )
    delegation = DelegationEvent(
        event_id="delegate-1",
        sequence=0,
        timestamp=NOW,
        delegation_id="delegation-1",
        source_agent_id="supervisor",
        target_agent_id="worker",
        child_execution_id="execution-worker",
        result_validated=True,
    )
    report = evaluate_system_execution(
        case,
        (_trace("supervisor", delegation), _trace("worker")),
        SystemCandidate(
            system_candidate_id="system-1",
            version="1.0.0",
            agent_candidates=(_candidate("supervisor"), _candidate("worker")),
        ),
        run_id="run-1",
    )

    assert report.overall_passed
    assert report.delegation_edges[0].source_agent_id == "supervisor"
    assert report.delegation_edges[0].target_agent_id == "worker"
    assert len(report.individual_results) == 2


def test_system_evaluation_detects_delegation_loop_and_privilege_escalation() -> None:
    case = _case()
    loop = (
        _trace(
            "supervisor",
            DelegationEvent(
                event_id="delegate-1",
                sequence=0,
                timestamp=NOW,
                delegation_id="d1",
                target_agent_id="worker",
            ),
        ),
        _trace(
            "worker",
            DelegationEvent(
                event_id="delegate-2",
                sequence=0,
                timestamp=NOW,
                delegation_id="d2",
                source_agent_id="worker",
                target_agent_id="supervisor",
            ),
            ToolCallEvent(
                event_id="tool-1",
                sequence=1,
                timestamp=NOW,
                call_id="call-1",
                name="orders.read",
                outcome=ToolCallOutcome.SUCCESS,
                authorization_granted=False,
            ),
        ),
    )

    report = evaluate_system_execution(case, loop)
    failed = {check.check_type for check in report.system_checks if not check.passed}

    assert "delegation_loop" in failed
    assert "privilege_escalation" in failed
    assert any(failure.category.value == "authorization" for failure in report.failures)


def test_system_evaluation_detects_context_leakage_decision_conflict_and_duplicate_work() -> None:
    case = _case(
        interaction_constraints=(
            SystemInteractionConstraint(
                constraint_id="consistency-1",
                require_consistent_decisions=True,
                forbidden_context_fields=("customer_ssn",),
            ),
        )
    )
    first = _trace(
        "supervisor",
        ApprovalDecisionEvent(
            event_id="approval-1",
            sequence=0,
            timestamp=NOW,
            approval_id="payment-1",
            decision=ApprovalDecision.APPROVED,
        ),
        ToolCallEvent(
            event_id="work-1",
            sequence=1,
            timestamp=NOW,
            call_id="work-1",
            name="orders.read",
            outcome=ToolCallOutcome.SUCCESS,
            idempotency_key_digest="same-work",
        ),
    )
    second = _trace(
        "worker",
        ApprovalDecisionEvent(
            event_id="approval-2",
            sequence=0,
            timestamp=NOW,
            approval_id="payment-1",
            decision=ApprovalDecision.REJECTED,
        ),
        ToolCallEvent(
            event_id="work-2",
            sequence=1,
            timestamp=NOW,
            call_id="work-2",
            name="orders.read",
            outcome=ToolCallOutcome.SUCCESS,
            idempotency_key_digest="same-work",
            metadata={"customer_ssn": "field-reference-only"},
        ),
        tenant_id="tenant-2",
    )

    report = evaluate_system_execution(case, (first, second))
    failed = {check.check_type for check in report.system_checks if not check.passed}

    assert {"context_leakage", "decision_consistency", "duplicated_work"} <= failed


def test_single_agent_system_case_remains_supported() -> None:
    case = _case(agent_ids=("solo",))
    report = evaluate_system_execution(case, (_trace("solo"),))

    assert report.overall_passed
    assert report.system_candidate_id.startswith("system:")
