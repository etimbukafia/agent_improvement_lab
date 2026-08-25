"""Deterministic multi-turn session evaluators."""

from __future__ import annotations

from collections import Counter

from agent_improvement_lab.contracts.failures import FailureCategory
from agent_improvement_lab.evaluators.base import (
    EvaluationContext,
    EvaluationOutcome,
    LabEvaluator,
    metadata_strings,
    normalize_text,
    numeric_claims,
    ordered_turns,
    outcome,
    style_signature,
)


class SessionContextRetention(LabEvaluator):
    """Check that required context terms appear in later turns."""

    evaluator_id = "session.context_retention"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        terms = metadata_strings(context.case, "required_context_terms")
        if not terms:
            return outcome(1.0, True, "No required context terms were declared.")
        later_text = " ".join(
            turn.output_text or "" for turn in ordered_turns(context.trace)[1:]
        ).casefold()
        retained = sum(term.casefold() in later_text for term in terms)
        return outcome(
            retained / len(terms),
            retained == len(terms),
            f"Retained {retained} of {len(terms)} required context terms.",
            category=FailureCategory.CONTEXT,
        )


class RepeatedQuestionRate(LabEvaluator):
    """Penalize repeated user questions within one session."""

    evaluator_id = "session.repeated_question_rate"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        questions = [normalize_text(turn.input_text) for turn in ordered_turns(context.trace)]
        repeated = len(questions) - len(set(questions))
        denominator = max(len(questions) - 1, 1)
        rate = repeated / denominator
        return outcome(
            1.0 - rate,
            repeated == 0,
            f"Found {repeated} repeated questions across {len(questions)} turns.",
            category=FailureCategory.CONTEXT,
        )


class CrossTurnNumericalConsistency(LabEvaluator):
    """Check that repeated labelled numeric claims keep the same value."""

    evaluator_id = "session.cross_turn_numerical_consistency"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        claims: dict[str, list[float]] = {}
        for turn in ordered_turns(context.trace):
            for label, values in numeric_claims(turn.output_text or "").items():
                claims.setdefault(label, []).extend(values)
        repeated_claims = {label: values for label, values in claims.items() if len(values) > 1}
        if not repeated_claims:
            return outcome(1.0, True, "No repeated labelled numeric claims were found.")
        consistent = sum(len(set(values)) == 1 for values in repeated_claims.values())
        return outcome(
            consistent / len(repeated_claims),
            consistent == len(repeated_claims),
            f"Found {consistent} consistent of {len(repeated_claims)} repeated numeric claims.",
            category=FailureCategory.QUALITY,
        )


class SessionContradictionRate(LabEvaluator):
    """Check declared phrase pairs that must not both appear in a session."""

    evaluator_id = "session.contradiction_rate"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        pairs = context.case.metadata.get("contradiction_pairs", ())
        if not isinstance(pairs, (list, tuple)) or not pairs:
            return outcome(1.0, True, "No contradiction phrase pairs were declared.")
        text = " ".join(turn.output_text or "" for turn in ordered_turns(context.trace)).casefold()
        contradictions = 0
        checked = 0
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            checked += 1
            if str(pair[0]).casefold() in text and str(pair[1]).casefold() in text:
                contradictions += 1
        if not checked:
            return outcome(1.0, True, "No valid contradiction phrase pairs were declared.")
        return outcome(
            1.0 - contradictions / checked,
            contradictions == 0,
            f"Found {contradictions} contradictions across {checked} declared phrase pairs.",
            category=FailureCategory.QUALITY,
        )


class ClarificationQuality(LabEvaluator):
    """Check whether the agent asks for clarification when the case requires it."""

    evaluator_id = "session.clarification_quality"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        required = context.case.metadata.get("clarification_required")
        if not isinstance(required, bool):
            return outcome(1.0, True, "Clarification need was not declared.")
        asked = any("?" in (turn.output_text or "") for turn in ordered_turns(context.trace))
        passed = asked == required
        return outcome(
            1.0 if passed else 0.0,
            passed,
            f"Clarification required={required}; observed question={asked}.",
            category=FailureCategory.QUALITY,
        )


class UnnecessaryClarificationRate(LabEvaluator):
    """Penalize questions when a case does not require clarification."""

    evaluator_id = "session.unnecessary_clarification_rate"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        required = context.case.metadata.get("clarification_required")
        if required is True:
            return outcome(1.0, True, "Clarification is required for this case.")
        turns = ordered_turns(context.trace)
        question_count = sum("?" in (turn.output_text or "") for turn in turns)
        rate = question_count / max(len(turns), 1)
        return outcome(
            1.0 - rate,
            question_count == 0,
            f"Observed {question_count} unnecessary clarification questions.",
            category=FailureCategory.EFFICIENCY,
        )


class SessionStyleConsistency(LabEvaluator):
    """Check that response format and length band stay stable across turns."""

    evaluator_id = "session.style_consistency"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        signatures = [
            style_signature(turn.output_text or "") for turn in ordered_turns(context.trace)
        ]
        if len(signatures) <= 1:
            return outcome(1.0, True, "One turn does not provide a style variation.")
        counts = Counter(signatures)
        consistent = max(counts.values())
        return outcome(
            consistent / len(signatures),
            consistent == len(signatures),
            f"The dominant style covered {consistent} of {len(signatures)} turns.",
            category=FailureCategory.QUALITY,
        )


def default_session_evaluators() -> tuple[LabEvaluator, ...]:
    """Return the default session evaluator catalog."""

    return (
        SessionContextRetention(),
        RepeatedQuestionRate(),
        CrossTurnNumericalConsistency(),
        SessionContradictionRate(),
        ClarificationQuality(),
        UnnecessaryClarificationRate(),
        SessionStyleConsistency(),
    )
