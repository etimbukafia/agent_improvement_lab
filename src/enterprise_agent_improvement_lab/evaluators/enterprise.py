"""Deterministic evaluators for typed enterprise cases and traces."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from enterprise_agent_improvement_lab.contracts.cases import (
    ActionExpectation,
    AuthorizationContext,
    EnterpriseEvaluationCase,
)
from enterprise_agent_improvement_lab.contracts.failures import FailureCategory
from enterprise_agent_improvement_lab.contracts.traces import (
    ApprovalDecision,
    ApprovalDecisionEvent,
    ApprovalRequestEvent,
    DelegationEvent,
    ExecutionEventRecord,
    ExecutionEventStatus,
    ExecutionTrace,
    RetrievalEvent,
    StateMutationEvent,
    ToolCallEvent,
    ToolCallOutcome,
    WorkflowTransitionEvent,
)
from enterprise_agent_improvement_lab.evaluators.base import (
    EvaluationContext,
    EvaluationOutcome,
    LabEvaluator,
    outcome,
)


def _enterprise(
    context: EvaluationContext,
) -> tuple[EnterpriseEvaluationCase, ExecutionTrace] | None:
    if isinstance(context.case, EnterpriseEvaluationCase) and isinstance(
        context.trace, ExecutionTrace
    ):
        return context.case, context.trace
    return None


def _path_value(state: dict[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = state
    for part in path.removeprefix("$").strip(".").split("."):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _expiry(event: ApprovalRequestEvent) -> datetime:
    """Return a validated request expiry after the caller checked it exists."""

    assert event.expires_at is not None
    return event.expires_at


def _matches(expected: Any, observed: Any, operator: str) -> bool:
    if operator == "equals":
        return bool(observed == expected)
    if operator == "not_equals":
        return bool(observed != expected)
    if operator == "exists":
        return observed is not None
    return False


class ExpectedFinalState(LabEvaluator):
    """Check explicit final-state expectations against captured state."""

    evaluator_id = "state.expected_final_state"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None or not data[0].expected_final_state:
            return outcome(1.0, True, "No final-state expectation was declared.")
        case, _ = data
        final = context.final_state
        if final is None:
            return outcome(
                0.0,
                False,
                "Expected final state, but no final snapshot was captured.",
                category=FailureCategory.STATE,
            )
        failed = []
        for item in case.expected_final_state:
            found, observed = _path_value(final.state, item.path)
            if not found or not _matches(item.expected_value, observed, item.operator):
                failed.append(item.path)
        return outcome(
            1.0 if not failed else 0.0,
            not failed,
            "Final state matches all declared paths."
            if not failed
            else f"Final state differs at: {failed}.",
            category=FailureCategory.STATE,
            evidence_refs=(final.snapshot_id,),
        )


class ForbiddenMutation(LabEvaluator):
    """Reject state mutations that match prohibited state action expectations."""

    evaluator_id = "state.forbidden_mutation"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        case, trace = data
        forbidden = [
            item for item in case.prohibited_actions if item.action_type == "state_mutation"
        ]
        if not forbidden:
            return outcome(1.0, True, "No prohibited state mutation was declared.")
        events = [event for event in trace.events if isinstance(event, StateMutationEvent)]
        violations = [
            event for event in events if any(_mutation_matches(item, event) for item in forbidden)
        ]
        return outcome(
            1.0 if not violations else 0.0,
            not violations,
            "No prohibited state mutation occurred."
            if not violations
            else (
                f"Prohibited state mutations occurred: {[event.resource for event in violations]}."
            ),
            category=FailureCategory.STATE,
            evidence_refs=(event.event_id for event in violations),
        )


class InvariantPreserved(LabEvaluator):
    """Check path invariants against the final captured state."""

    evaluator_id = "state.invariant_preserved"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None or not data[0].state_invariants:
            return outcome(1.0, True, "No state invariant was declared.")
        final = context.final_state
        if final is None:
            return outcome(
                0.0,
                False,
                "State invariants require a final snapshot.",
                category=FailureCategory.STATE,
            )
        failed: list[str] = []
        for invariant in data[0].state_invariants:
            for path in invariant.paths:
                found, observed = _path_value(final.state, path)
                if not found or not _matches(
                    invariant.expected_value, observed, invariant.operator
                ):
                    failed.append(invariant.invariant_id)
                    break
            if any(_path_value(final.state, path)[0] for path in invariant.prohibited_paths):
                failed.append(invariant.invariant_id)
        failed = sorted(set(failed))
        return outcome(
            1.0 if not failed else 0.0,
            not failed,
            "All state invariants were preserved."
            if not failed
            else f"State invariants failed: {failed}.",
            category=FailureCategory.STATE,
            evidence_refs=(final.snapshot_id,),
        )


class RequiredBeforeAction(LabEvaluator):
    """Check that each required approval has an approved decision."""

    evaluator_id = "approval.required_before_action"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None or not data[0].required_approvals:
            return outcome(1.0, True, "No approval was required.")
        case, trace = data
        decisions = [event for event in trace.events if isinstance(event, ApprovalDecisionEvent)]
        missing = [
            requirement.identity
            for requirement in case.required_approvals
            if not any(
                event.approval_id == requirement.approval_id
                and event.decision.value == requirement.decision
                for event in decisions
            )
        ]
        return outcome(
            1.0 if not missing else 0.0,
            not missing,
            "All required approvals were decided as required."
            if not missing
            else f"Missing required approvals: {missing}.",
            category=FailureCategory.APPROVAL,
            evidence_refs=(event.event_id for event in decisions),
        )


class NoActionBeforeApproval(LabEvaluator):
    """Ensure an approval decision precedes each approval-gated action."""

    evaluator_id = "approval.no_action_before_approval"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        case, trace = data
        requirements = [item for item in case.required_approvals if item.action is not None]
        violations: list[str] = []
        for requirement in requirements:
            approved_at = [
                event.sequence
                for event in trace.events
                if isinstance(event, ApprovalDecisionEvent)
                and event.approval_id == requirement.approval_id
                and event.decision == ApprovalDecision.APPROVED
            ]
            actions = [
                event
                for event in trace.events
                if isinstance(event, ToolCallEvent) and event.name == requirement.action
            ]
            if actions and (
                not approved_at or min(event.sequence for event in actions) < min(approved_at)
            ):
                violations.append(requirement.action or "unknown")
        return outcome(
            1.0 if not violations else 0.0,
            not violations,
            "No action occurred before approval."
            if not violations
            else f"Actions occurred before approval: {violations}.",
            category=FailureCategory.APPROVAL,
        )


class ApprovalExpirationRespected(LabEvaluator):
    """Reject an approval decision that occurs after its request expires."""

    evaluator_id = "approval.expiration_respected"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        _, trace = data
        requests = {
            event.approval_id: event
            for event in trace.events
            if isinstance(event, ApprovalRequestEvent) and event.expires_at is not None
        }
        expired = [
            event
            for event in trace.events
            if isinstance(event, ApprovalDecisionEvent)
            and event.approval_id in requests
            and event.timestamp > _expiry(requests[event.approval_id])
        ]
        return outcome(
            1.0 if not expired else 0.0,
            not expired,
            "No approval decision occurred after expiry."
            if not expired
            else f"Expired approval decisions: {[event.event_id for event in expired]}.",
            category=FailureCategory.APPROVAL,
            evidence_refs=(event.event_id for event in expired),
        )


class TenantBoundary(LabEvaluator):
    """Check event tenant evidence against the typed case tenant context."""

    evaluator_id = "authorization.tenant_boundary"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None or not isinstance(data[0].tenant_context, dict):
            return outcome(1.0, True, "No tenant boundary was declared.")
        expected = data[0].tenant_context.get("tenant_id")
        if not isinstance(expected, str):
            return outcome(1.0, True, "No tenant ID was declared.")
        trace = data[1]
        if trace.tenant_id is None:
            return outcome(
                0.0,
                False,
                "The case declares a tenant, but the trace has no tenant evidence.",
                category=FailureCategory.AUTHORIZATION,
            )
        violations = [] if trace.tenant_id == expected else [trace.execution_id]
        return outcome(
            1.0 if not violations else 0.0,
            not violations,
            "All observed tenant IDs match the case tenant."
            if not violations
            else f"Tenant boundary violations: {violations}.",
            category=FailureCategory.AUTHORIZATION,
            evidence_refs=violations,
        )


class ToolAccess(LabEvaluator):
    """Reject prohibited tool calls declared as typed action expectations."""

    evaluator_id = "authorization.tool_access"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        case, trace = data
        authorization = case.authorization_context
        if not isinstance(authorization, AuthorizationContext):
            authorization = None
        allowed_tools = set(authorization.allowed_tools) if authorization is not None else set()
        prohibited = [
            item
            for item in case.prohibited_actions
            if item.action_type in {"tool_call", "authorization_tool"}
        ]
        calls = [event for event in trace.events if isinstance(event, ToolCallEvent)]
        violations = [
            event
            for event in calls
            if any(item.action == event.name for item in prohibited)
            or (bool(allowed_tools) and event.name not in allowed_tools)
        ]
        return outcome(
            1.0 if not violations else 0.0,
            not violations,
            "No prohibited tool was called."
            if not violations
            else f"Prohibited tools were called: {[event.name for event in violations]}.",
            category=FailureCategory.AUTHORIZATION,
            evidence_refs=(event.event_id for event in violations),
        )


class ValidTransition(LabEvaluator):
    """Validate declared workflow transitions from typed action expectations."""

    evaluator_id = "workflow.valid_transition"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        allowed = [
            item for item in data[0].expected_actions if item.action_type == "workflow_transition"
        ]
        if not allowed:
            return outcome(1.0, True, "No workflow transition was declared.")
        transitions = [
            event for event in data[1].events if isinstance(event, WorkflowTransitionEvent)
        ]
        invalid = [
            event
            for event in transitions
            if not any(_transition_matches(item, event) for item in allowed)
        ]
        return outcome(
            1.0 if not invalid else 0.0,
            not invalid,
            "All workflow transitions are declared."
            if not invalid
            else f"Invalid workflow transitions: {[event.event_id for event in invalid]}.",
            category=FailureCategory.STATE,
            evidence_refs=(event.event_id for event in invalid),
        )


class RequiredWorkflowStep(LabEvaluator):
    """Check that every required typed workflow transition occurred."""

    evaluator_id = "workflow.required_step"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        case, trace = data
        required = [
            item for item in case.required_actions if item.action_type == "workflow_transition"
        ]
        transitions = [
            event for event in trace.events if isinstance(event, WorkflowTransitionEvent)
        ]
        missing = [
            item.identity
            for item in required
            if not any(_transition_matches(item, event) for event in transitions)
        ]
        return outcome(
            1.0 if not missing else 0.0,
            not missing,
            "All required workflow steps occurred."
            if not missing
            else f"Missing workflow steps: {missing}.",
            category=FailureCategory.STATE,
        )


class ForbiddenWorkflowTransition(LabEvaluator):
    """Reject workflow transitions declared as prohibited actions."""

    evaluator_id = "workflow.forbidden_transition"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        case, trace = data
        prohibited = [
            item for item in case.prohibited_actions if item.action_type == "workflow_transition"
        ]
        violations = [
            event
            for event in trace.events
            if isinstance(event, WorkflowTransitionEvent)
            and any(_transition_matches(item, event) for item in prohibited)
        ]
        return outcome(
            1.0 if not violations else 0.0,
            not violations,
            "No prohibited workflow transition occurred."
            if not violations
            else f"Prohibited workflow transitions: {[event.event_id for event in violations]}.",
            category=FailureCategory.STATE,
            evidence_refs=(event.event_id for event in violations),
        )


class DelegationLoopPrevention(LabEvaluator):
    """Reject a delegation cycle visible in one execution trace."""

    evaluator_id = "delegation.loop_prevention"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        _, trace = data
        edges = [
            (event.source_agent_id or trace.agent_id, event.target_agent_id, event.event_id)
            for event in trace.events
            if isinstance(event, DelegationEvent)
        ]
        graph: dict[str, set[str]] = {}
        for source, target, _ in edges:
            graph.setdefault(source, set()).add(target)
        loops = [event_id for source, target, event_id in edges if _has_path(graph, target, source)]
        return outcome(
            1.0 if not loops else 0.0,
            not loops,
            "No delegation loop occurred." if not loops else f"Delegation loops occurred: {loops}.",
            category=FailureCategory.DELEGATION,
            evidence_refs=loops,
        )


class CorrectDelegationAgent(LabEvaluator):
    """Check delegation targets against required typed action expectations."""

    evaluator_id = "delegation.correct_agent"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        case, trace = data
        required = [item for item in case.required_actions if item.action_type == "delegation"]
        delegations = [event for event in trace.events if isinstance(event, DelegationEvent)]
        missing = [
            item.identity
            for item in required
            if not any(item.target == event.target_agent_id for event in delegations)
        ]
        return outcome(
            1.0 if not missing else 0.0,
            not missing,
            "All required delegation targets were used."
            if not missing
            else f"Missing delegation targets: {missing}.",
            category=FailureCategory.DELEGATION,
        )


class RetrievalSourceCorrectness(LabEvaluator):
    """Check retrieval sources against typed expected retrieval actions."""

    evaluator_id = "retrieval.source_correctness"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        case, trace = data
        expected = [item for item in case.required_actions if item.action_type == "retrieval"]
        retrievals = [event for event in trace.events if isinstance(event, RetrievalEvent)]
        missing = [
            item.identity
            for item in expected
            if not any(item.target == event.source for event in retrievals)
        ]
        return outcome(
            1.0 if not missing else 0.0,
            not missing,
            "All required retrieval sources were used."
            if not missing
            else f"Missing retrieval sources: {missing}.",
            category=FailureCategory.GROUNDING,
        )


class ToolSideEffectCorrectness(LabEvaluator):
    """Check inspected writes against expected final state evidence."""

    evaluator_id = "tool.side_effect_correctness"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        result = ExpectedFinalState().evaluate(context)
        if result.passed:
            return outcome(
                1.0,
                True,
                "Observed side effects produce the expected final state.",
                evidence_refs=result.evidence_refs,
            )
        return outcome(
            0.0,
            False,
            result.explanation,
            category=FailureCategory.TOOL_SIDE_EFFECT,
            evidence_refs=result.evidence_refs,
        )


class TransactionIntegrity(LabEvaluator):
    """Require transaction evidence for multi-write state changes."""

    evaluator_id = "state.transaction_integrity"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        requirements = [
            item for item in data[0].required_actions if item.action_type == "state_transaction"
        ]
        if not requirements:
            return outcome(1.0, True, "No state transaction was declared.")
        expected_ids = {item.target for item in requirements if item.target is not None}
        mutations = [
            event
            for event in data[1].events
            if isinstance(event, StateMutationEvent)
            and (not expected_ids or event.transaction_id in expected_ids)
        ]
        missing = [event.event_id for event in mutations if event.transaction_id is None]
        failed = [
            event.event_id for event in mutations if event.status == ExecutionEventStatus.ERROR
        ]
        valid = not missing and not failed
        return outcome(
            1.0 if valid else 0.0,
            valid,
            "All state mutations have successful transaction evidence."
            if valid
            else f"Transaction evidence is incomplete: missing={missing}, failed={failed}.",
            category=FailureCategory.DATA_INTEGRITY,
            evidence_refs=(*missing, *failed),
        )


class ResourceScope(LabEvaluator):
    """Reject tool calls outside typed resource action constraints."""

    evaluator_id = "authorization.resource_scope"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        authorization = data[0].authorization_context
        allowed_resources = (
            set(authorization.allowed_resources)
            if isinstance(authorization, AuthorizationContext)
            else set()
        )
        prohibited = [
            item for item in data[0].prohibited_actions if item.action_type == "resource_access"
        ]
        violations = [
            event
            for event in data[1].events
            if isinstance(event, ToolCallEvent)
            and (
                any(
                    item.action == event.name
                    and item.target is not None
                    and item.target == event.resource_id
                    for item in prohibited
                )
                or (bool(allowed_resources) and event.resource_id not in allowed_resources)
            )
        ]
        return _event_outcome(
            violations,
            FailureCategory.AUTHORIZATION,
            "No resource-scope violation occurred.",
            "Resource-scope violations",
        )


class RoleBoundary(LabEvaluator):
    """Check declared principal roles against tool action role constraints."""

    evaluator_id = "authorization.role_boundary"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        expected = [
            item for item in data[0].required_actions if item.action_type == "role_boundary"
        ]
        if not expected:
            return outcome(1.0, True, "No role boundary was declared.")
        actual_roles = set(data[1].principal_roles)
        missing = [
            item.identity
            for item in expected
            if item.target is not None and item.target not in actual_roles
        ]
        return outcome(
            1.0 if not missing else 0.0,
            not missing,
            "The principal role meets all declared boundaries."
            if not missing
            else f"Principal role does not meet boundaries: {missing}.",
            category=FailureCategory.AUTHORIZATION,
        )


class CorrectReviewer(LabEvaluator):
    """Check an approval decision against its declared reviewer role."""

    evaluator_id = "approval.correct_reviewer"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None or not data[0].required_approvals:
            return outcome(1.0, True, "No reviewer role was required.")
        decisions = [event for event in data[1].events if isinstance(event, ApprovalDecisionEvent)]
        invalid = [
            requirement.identity
            for requirement in data[0].required_approvals
            if requirement.approver_role is not None
            and not any(
                event.approval_id == requirement.approval_id
                and event.reviewer_role == requirement.approver_role
                for event in decisions
            )
        ]
        return outcome(
            1.0 if not invalid else 0.0,
            not invalid,
            "All approvals have the declared reviewer role."
            if not invalid
            else f"Incorrect approval reviewers: {invalid}.",
            category=FailureCategory.APPROVAL,
        )


class WorkflowCompletion(LabEvaluator):
    """Check a declared completion transition in the trace."""

    evaluator_id = "workflow.completion"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        completion = [
            item for item in data[0].required_actions if item.action_type == "workflow_completion"
        ]
        transitions = [
            event for event in data[1].events if isinstance(event, WorkflowTransitionEvent)
        ]
        missing = [
            item.identity
            for item in completion
            if not any(
                item.target == event.workflow_id
                and item.arguments.get("to_state") == event.to_state
                for event in transitions
            )
        ]
        return outcome(
            1.0 if not missing else 0.0,
            not missing,
            "All declared workflows completed."
            if not missing
            else f"Incomplete workflows: {missing}.",
            category=FailureCategory.STATE,
        )


class ToolIdempotency(LabEvaluator):
    """Require an idempotency key for declared write-capable tools."""

    evaluator_id = "tool.idempotency"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        return _tool_rule(context, "tool_idempotency", _idempotency_violation, "idempotency")


class ToolRetrySafety(LabEvaluator):
    """Check retry counts and idempotency evidence for declared tools."""

    evaluator_id = "tool.retry_safety"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        return _tool_rule(context, "tool_retry", _retry_violation, "retry safety")


class ToolTimeoutBehavior(LabEvaluator):
    """Check timeout evidence against typed tool timeout constraints."""

    evaluator_id = "tool.timeout_behavior"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        return _tool_rule(context, "tool_timeout", _timeout_violation, "timeout behavior")


class ToolCompensationBehavior(LabEvaluator):
    """Require declared compensation after a failed compensated tool call."""

    evaluator_id = "tool.compensation_behavior"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        rules = [
            item for item in data[0].required_actions if item.action_type == "tool_compensation"
        ]
        calls = [event for event in data[1].events if isinstance(event, ToolCallEvent)]
        missing: list[str] = []
        for rule in rules:
            failed = [
                call
                for call in calls
                if call.name == rule.action and call.outcome == ToolCallOutcome.ERROR
            ]
            compensation = rule.arguments.get("compensation_tool")
            if failed and (
                not isinstance(compensation, str)
                or not any(call.name == compensation for call in calls)
            ):
                missing.append(rule.identity)
        return outcome(
            1.0 if not missing else 0.0,
            not missing,
            "All failed tools received declared compensation."
            if not missing
            else f"Missing tool compensation: {missing}.",
            category=FailureCategory.TOOL_SIDE_EFFECT,
        )


class DelegationMinimumPrivilege(LabEvaluator):
    """Check delegated tools and permissions against typed ceilings."""

    evaluator_id = "delegation.minimum_privilege"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        return _delegation_rule(context, "delegated_tool_ids", "granted_permissions")


class DelegationContextIntegrity(LabEvaluator):
    """Check a declared delegation context checksum."""

    evaluator_id = "delegation.context_integrity"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        rules = [item for item in data[0].required_actions if item.action_type == "delegation"]
        bad = [
            item.identity
            for item in rules
            if "context_checksum" in item.arguments
            and not any(
                event.target_agent_id == item.target
                and event.context_checksum == item.arguments["context_checksum"]
                for event in data[1].events
                if isinstance(event, DelegationEvent)
            )
        ]
        return outcome(
            1.0 if not bad else 0.0,
            not bad,
            "All delegation contexts have the declared checksum."
            if not bad
            else f"Invalid delegation contexts: {bad}.",
            category=FailureCategory.DELEGATION,
        )


class DelegationResultValidation(LabEvaluator):
    """Require result validation for declared delegations."""

    evaluator_id = "delegation.result_validation"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        rules = [item for item in data[0].required_actions if item.action_type == "delegation"]
        bad = [
            item.identity
            for item in rules
            if item.arguments.get("result_validation_required") is True
            and not any(
                event.target_agent_id == item.target and event.result_validated is True
                for event in data[1].events
                if isinstance(event, DelegationEvent)
            )
        ]
        return outcome(
            1.0 if not bad else 0.0,
            not bad,
            "All delegated results were validated."
            if not bad
            else f"Unvalidated delegated results: {bad}.",
            category=FailureCategory.DELEGATION,
        )


class RetrievalAuthorization(LabEvaluator):
    """Require authorization evidence for declared retrieval sources."""

    evaluator_id = "retrieval.authorization"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        return _retrieval_rule(context, "authorized", "authorized retrieval")


class RetrievalFreshness(LabEvaluator):
    """Check retrieval age against typed maximum-age constraints."""

    evaluator_id = "retrieval.freshness"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        rules = [item for item in data[0].required_actions if item.action_type == "retrieval"]
        now = data[1].ended_at or datetime.now(timezone.utc)
        bad: list[str] = []
        for rule in rules:
            age_limit = rule.arguments.get("max_age_seconds")
            if not isinstance(age_limit, (int, float)):
                continue
            matching = [
                event
                for event in data[1].events
                if isinstance(event, RetrievalEvent) and event.source == rule.target
            ]
            if not matching or any(
                event.retrieved_at is None or (now - event.retrieved_at).total_seconds() > age_limit
                for event in matching
            ):
                bad.append(rule.identity)
        return outcome(
            1.0 if not bad else 0.0,
            not bad,
            "All retrieval results meet freshness limits."
            if not bad
            else f"Stale retrieval results: {bad}.",
            category=FailureCategory.GROUNDING,
        )


class RetrievalGrounding(LabEvaluator):
    """Require declared document evidence from each retrieval source."""

    evaluator_id = "retrieval.grounding"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        if data is None:
            return outcome(1.0, True, "No enterprise trace was supplied.")
        rules = [item for item in data[0].required_actions if item.action_type == "retrieval"]
        bad: list[str] = []
        for rule in rules:
            required_refs = rule.arguments.get("document_refs")
            if not isinstance(required_refs, (tuple, list)):
                continue
            actual = {
                reference
                for event in data[1].events
                if isinstance(event, RetrievalEvent) and event.source == rule.target
                for reference in event.document_refs
            }
            if not set(required_refs).issubset(actual):
                bad.append(rule.identity)
        return outcome(
            1.0 if not bad else 0.0,
            not bad,
            "All retrieval evidence supports the declared grounding."
            if not bad
            else f"Missing grounding evidence: {bad}.",
            category=FailureCategory.GROUNDING,
        )


class BusinessOutcomeMetric(LabEvaluator):
    """Check typed business outcomes against final state evidence."""

    evaluator_id = "business.value_metric"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        data = _enterprise(context)
        metric_id = getattr(self, "metric_id", None)
        outcomes = (
            tuple(item for item in data[0].business_outcomes if item.outcome_id == metric_id)
            if data is not None and metric_id is not None
            else data[0].business_outcomes
            if data is not None
            else ()
        )
        if data is None or not outcomes:
            return outcome(1.0, True, "No business outcome was declared.")
        if context.final_state is None:
            return outcome(
                0.0,
                False,
                "Business outcomes require a final state snapshot.",
                category=FailureCategory.BUSINESS_OUTCOME,
            )
        bad = []
        for item in outcomes:
            if item.state_path is None:
                bad.append(item.outcome_id)
                continue
            found, observed = _path_value(context.final_state.state, item.state_path)
            if not found or not _matches(item.expected_value, observed, item.operator):
                bad.append(item.outcome_id)
        return outcome(
            1.0 if not bad else 0.0,
            not bad,
            "All business outcomes meet their declared values."
            if not bad
            else f"Business outcomes failed: {bad}.",
            category=FailureCategory.BUSINESS_OUTCOME,
            evidence_refs=(context.final_state.snapshot_id,),
        )


class ResolutionRate(BusinessOutcomeMetric):
    evaluator_id = "business.resolution_rate"
    metric_id = "resolution_rate"


class Containment(BusinessOutcomeMetric):
    evaluator_id = "business.containment"
    metric_id = "containment"


class ServiceLevelAgreement(BusinessOutcomeMetric):
    evaluator_id = "business.sla"
    metric_id = "sla"


class ManualWorkReduction(BusinessOutcomeMetric):
    evaluator_id = "business.manual_work_reduction"
    metric_id = "manual_work_reduction"


class ErrorCost(BusinessOutcomeMetric):
    evaluator_id = "business.error_cost"
    metric_id = "error_cost"


def _event_outcome(
    events: Sequence[ExecutionEventRecord],
    category: FailureCategory,
    passed_text: str,
    failed_name: str,
) -> EvaluationOutcome:
    return outcome(
        1.0 if not events else 0.0,
        not events,
        passed_text if not events else f"{failed_name}: {[event.event_id for event in events]}.",
        category=category,
        evidence_refs=(event.event_id for event in events),
    )


def _tool_rule(
    context: EvaluationContext,
    action_type: str,
    violation: Any,
    name: str,
) -> EvaluationOutcome:
    data = _enterprise(context)
    if data is None:
        return outcome(1.0, True, "No enterprise trace was supplied.")
    rules = [item for item in data[0].required_actions if item.action_type == action_type]
    violations = [
        event
        for event in data[1].events
        if isinstance(event, ToolCallEvent)
        and any(rule.action == event.name and violation(rule, event) for rule in rules)
    ]
    return _event_outcome(
        violations,
        FailureCategory.TOOL_EXECUTION,
        f"All declared tools meet {name} requirements.",
        f"Tool {name} violations",
    )


def _idempotency_violation(rule: ActionExpectation, event: ToolCallEvent) -> bool:
    return rule.arguments.get("required") is not False and event.idempotency_key_digest is None


def _retry_violation(rule: ActionExpectation, event: ToolCallEvent) -> bool:
    maximum = rule.arguments.get("max_retry_count")
    if isinstance(maximum, int) and event.retry_count > maximum:
        return True
    return event.retry_count > 0 and event.idempotency_key_digest is None


def _timeout_violation(rule: ActionExpectation, event: ToolCallEvent) -> bool:
    maximum = rule.arguments.get("max_timeout_seconds")
    if isinstance(maximum, (int, float)) and (
        event.timeout_seconds is None or event.timeout_seconds > maximum
    ):
        return True
    return rule.arguments.get("must_timeout") is True and event.error_type != "tool_timeout"


def _delegation_rule(
    context: EvaluationContext,
    tools_key: str,
    permissions_key: str,
) -> EvaluationOutcome:
    data = _enterprise(context)
    if data is None:
        return outcome(1.0, True, "No enterprise trace was supplied.")
    rules = [item for item in data[0].required_actions if item.action_type == "delegation"]
    bad: list[str] = []
    for rule in rules:
        if tools_key not in rule.arguments and permissions_key not in rule.arguments:
            continue
        allowed_tools = set(rule.arguments.get(tools_key, ()))
        allowed_permissions = set(rule.arguments.get(permissions_key, ()))
        matching = [
            event
            for event in data[1].events
            if isinstance(event, DelegationEvent) and event.target_agent_id == rule.target
        ]
        if matching and any(
            not set(event.authorized_tool_ids).issubset(allowed_tools)
            or not set(event.granted_permissions).issubset(allowed_permissions)
            for event in matching
        ):
            bad.append(rule.identity)
    return outcome(
        1.0 if not bad else 0.0,
        not bad,
        "All delegations remain inside declared privilege ceilings."
        if not bad
        else f"Delegation privilege violations: {bad}.",
        category=FailureCategory.DELEGATION,
    )


def _retrieval_rule(context: EvaluationContext, field: str, name: str) -> EvaluationOutcome:
    data = _enterprise(context)
    if data is None:
        return outcome(1.0, True, "No enterprise trace was supplied.")
    rules = [item for item in data[0].required_actions if item.action_type == "retrieval"]
    bad = [
        item.identity
        for item in rules
        if item.arguments.get(field) is True
        and not any(
            event.source == item.target and event.authorized is True
            for event in data[1].events
            if isinstance(event, RetrievalEvent)
        )
    ]
    return outcome(
        1.0 if not bad else 0.0,
        not bad,
        f"All retrievals have {name} evidence."
        if not bad
        else f"Retrieval {name} failures: {bad}.",
        category=FailureCategory.AUTHORIZATION,
    )


def _mutation_matches(expectation: ActionExpectation, event: StateMutationEvent) -> bool:
    return expectation.action == event.operation and (
        expectation.target is None or expectation.target == event.resource
    )


def _transition_matches(expectation: ActionExpectation, event: WorkflowTransitionEvent) -> bool:
    arguments = expectation.arguments
    return (
        (expectation.target is None or expectation.target == event.workflow_id)
        and (not expectation.action or expectation.action == (event.transition or ""))
        and ("from_state" not in arguments or arguments["from_state"] == event.from_state)
        and ("to_state" not in arguments or arguments["to_state"] == event.to_state)
    )


def _has_path(graph: dict[str, set[str]], start: str, target: str) -> bool:
    pending = [start]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current not in seen:
            seen.add(current)
            pending.extend(graph.get(current, ()))
    return False


def default_enterprise_evaluators() -> tuple[LabEvaluator, ...]:
    """Return the deterministic enterprise catalog with typed evidence support."""

    return (
        ExpectedFinalState(),
        ForbiddenMutation(),
        InvariantPreserved(),
        TransactionIntegrity(),
        ToolAccess(),
        ResourceScope(),
        TenantBoundary(),
        RoleBoundary(),
        RequiredBeforeAction(),
        CorrectReviewer(),
        NoActionBeforeApproval(),
        ApprovalExpirationRespected(),
        ValidTransition(),
        RequiredWorkflowStep(),
        ForbiddenWorkflowTransition(),
        WorkflowCompletion(),
        ToolIdempotency(),
        ToolSideEffectCorrectness(),
        ToolRetrySafety(),
        ToolTimeoutBehavior(),
        ToolCompensationBehavior(),
        CorrectDelegationAgent(),
        DelegationMinimumPrivilege(),
        DelegationContextIntegrity(),
        DelegationLoopPrevention(),
        DelegationResultValidation(),
        RetrievalSourceCorrectness(),
        RetrievalAuthorization(),
        RetrievalFreshness(),
        RetrievalGrounding(),
        ResolutionRate(),
        Containment(),
        ServiceLevelAgreement(),
        ManualWorkReduction(),
        ErrorCost(),
        BusinessOutcomeMetric(),
    )
