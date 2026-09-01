import pytest

from enterprise_agent_improvement_lab.contracts.cases import (
    ActionExpectation,
    ApprovalExpectation,
    BusinessOutcomeExpectation,
    EnterpriseEvaluationCase,
    EvaluationBudget,
    InvariantExpectation,
    StateExpectation,
)
from enterprise_agent_improvement_lab.contracts.traces import TriggerInfo
from enterprise_agent_improvement_lab.serialization import model_from_json, model_to_json


def _case(**updates: object) -> EnterpriseEvaluationCase:
    values: dict[str, object] = {
        "case_id": "enterprise-case-1",
        "dataset_id": "enterprise-workflows",
        "dataset_version": "1.0.0",
        "split": "development",
        "risk": "high",
        "provenance": {"source": "test", "source_ref": "workflow-fixture-1"},
    }
    values.update(updates)
    return EnterpriseEvaluationCase(**values)


def test_current_conversational_case_maps_to_typed_enterprise_case(case) -> None:
    restored = model_from_json(EnterpriseEvaluationCase, model_to_json(case))

    assert restored.case_id == case.case_id
    assert restored.input_text == "What is two plus two?"
    assert restored.expected_outputs[0].expected_value == "4"
    assert restored.expected_outputs[0].path == "answer"
    assert restored == case


def test_text_input_remains_explicit_without_requiring_other_enterprise_fields(case) -> None:
    text_case = case.model_copy(update={"input": "hello", "input_text": "hello"})
    restored = model_from_json(EnterpriseEvaluationCase, model_to_json(text_case))

    assert restored.input == "hello"
    assert restored.input_text == "hello"
    assert restored == text_case


def test_event_driven_case_does_not_require_conversational_text() -> None:
    case = _case(
        trigger=TriggerInfo(
            kind="external_event",
            source="shipping-service",
            name="shipment.created",
            event_id="event-1",
        ),
        fixtures={"orders": "fixture-2.0.0"},
        input=None,
    )

    assert case.trigger is not None
    assert case.trigger.kind == "external_event"
    assert case.input is None
    assert case.input_text is None
    assert case.fixtures[0].identity == "orders@fixture-2.0.0"


def test_mapping_shorthands_have_deterministic_order() -> None:
    first = _case(
        fixtures={"orders": "1.0.0", "customers": "2.0.0"},
        expected_final_state={"z": 1, "a": 2},
        business_outcomes={"z_outcome": True, "a_outcome": False},
    )
    second = _case(
        fixtures={"customers": "2.0.0", "orders": "1.0.0"},
        expected_final_state={"a": 2, "z": 1},
        business_outcomes={"a_outcome": False, "z_outcome": True},
    )

    assert model_to_json(first) == model_to_json(second)


def test_required_optional_and_prohibited_actions_are_explicit() -> None:
    case = _case(
        expected_actions=(
            ActionExpectation(action="retrieve_order", action_type="tool_call", order=0),
        ),
        required_actions=(
            ActionExpectation(
                action_id="action-1",
                action="record_resolution",
                action_type="state_mutation",
                target="orders/order-1",
                arguments={"status": "resolved"},
            ),
        ),
        optional_actions=(ActionExpectation(action="notify_customer", action_type="message"),),
        prohibited_actions=(ActionExpectation(action="delete_order", action_type="tool_call"),),
    )

    assert [action.action for action in case.expected_actions] == ["retrieve_order"]
    assert case.required_actions[0].arguments == {"status": "resolved"}
    assert case.optional_actions[0].action_type == "message"
    assert case.prohibited_actions[0].action == "delete_order"


def test_approval_requirements_are_typed_and_explicit() -> None:
    case = _case(
        required_approvals=(
            ApprovalExpectation(
                approval_id="approval-1",
                policy_id="refund-policy@1.0.0",
                action="issue_refund",
                approver_role="manager",
            ),
        )
    )

    approval = case.required_approvals[0]
    assert approval.identity == "approval-1"
    assert approval.decision == "approved"
    assert approval.policy_id == "refund-policy@1.0.0"


def test_expected_final_state_is_typed_by_state_path() -> None:
    case = _case(
        expected_final_state={
            "order.status": "resolved",
            "refund.issued": False,
        }
    )

    values = {
        expectation.path: expectation.expected_value for expectation in case.expected_final_state
    }
    assert values == {"order.status": "resolved", "refund.issued": False}
    assert all(
        isinstance(expectation, StateExpectation) for expectation in case.expected_final_state
    )


def test_state_invariants_are_typed_and_can_cover_multiple_paths() -> None:
    case = _case(
        state_invariants=(
            InvariantExpectation(
                invariant_id="account-never-negative",
                paths=("account.balance", "account.available_credit"),
                operator="greater_than_or_equal",
                expected_value=0,
                evidence_refs=("state-evidence-1",),
            ),
        )
    )

    invariant = case.state_invariants[0]
    assert invariant.state_paths == ("account.balance", "account.available_credit")
    assert invariant.expected_value == 0
    assert invariant.evidence_refs == ("state-evidence-1",)


def test_tenant_authorization_security_and_policy_context_remain_explicit() -> None:
    case = _case(
        tenant_context={"tenant_id": "tenant-1", "profile": "gold"},
        authorization_context={"principal_id": "user-1", "roles": ["support"]},
        security_context={"classification": "restricted", "require_mfa": True},
        policy_references=("orders-policy@1.0.0", "refund-policy@1.0.0"),
        business_outcomes=(
            BusinessOutcomeExpectation(outcome_id="case_resolved", expected_value=True),
        ),
        budgets=EvaluationBudget(max_duration_ms=5000, max_tool_calls=4),
    )

    assert case.tenant_context == {"tenant_id": "tenant-1", "profile": "gold"}
    assert case.authorization_context is not None
    assert case.authorization_context.roles == ("support",)
    assert case.security_context == {"classification": "restricted", "require_mfa": True}
    assert case.policy_references == ("orders-policy@1.0.0", "refund-policy@1.0.0")
    assert case.business_outcomes[0].outcome == "case_resolved"
    assert case.budgets is not None
    assert case.budgets.max_tool_calls == 4


def test_conflicting_required_and_prohibited_actions_fail_validation() -> None:
    with pytest.raises(ValueError, match="required_actions"):
        _case(
            required_actions=(ActionExpectation(action="issue_refund"),),
            prohibited_actions=(ActionExpectation(action="issue_refund"),),
        )
