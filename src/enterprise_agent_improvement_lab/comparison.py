"""Deterministic comparison of enterprise evaluation evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from hashlib import sha256

from enterprise_agent_improvement_lab.contracts.common import utc_now
from enterprise_agent_improvement_lab.contracts.evaluation import EnterpriseEvaluationReport
from enterprise_agent_improvement_lab.contracts.experiments import (
    BaselineComparison,
    ComparisonDimensionWeight,
    ComparisonMetric,
    ComparisonVerdict,
    ComponentChange,
    EnterpriseComparisonDimension,
    EnterpriseComparisonMetric,
    EnterpriseComparisonPolicy,
    EvaluatorFamilyAggregate,
)
from enterprise_agent_improvement_lab.contracts.failures import EvaluationScore, FailureCategory


class ComparisonError(ValueError):
    """Raised when a comparison cannot be reproduced safely."""


class EnterpriseComparisonRunner:
    """Compare typed enterprise reports or already-paired enterprise metrics."""

    def __init__(self, *, policy: EnterpriseComparisonPolicy | None = None) -> None:
        self.policy = policy or EnterpriseComparisonPolicy(policy_id="enterprise-default")

    def compare(
        self,
        baseline: EnterpriseEvaluationReport
        | Sequence[EnterpriseComparisonMetric | ComparisonMetric],
        candidate: EnterpriseEvaluationReport
        | Sequence[EnterpriseComparisonMetric | ComparisonMetric]
        | None = None,
        *,
        baseline_run_id: str = "baseline",
        candidate_run_id: str = "candidate",
        baseline_snapshot: object | None = None,
        candidate_snapshot: object | None = None,
        baseline_candidate: object | None = None,
        candidate_candidate: object | None = None,
        baseline_manifest: object | None = None,
        candidate_manifest: object | None = None,
        holdout_metrics: Sequence[ComparisonMetric] = (),
        target_metric_ids: Sequence[str] = (),
        target_failure_ids: Sequence[str] = (),
        created_at: datetime | None = None,
    ) -> BaselineComparison:
        """Compare two enterprise reports or two paired metric collections."""

        if isinstance(baseline, EnterpriseEvaluationReport):
            if not isinstance(candidate, EnterpriseEvaluationReport):
                raise ComparisonError("An enterprise report comparison needs two reports")
            return compare_enterprise_reports(
                baseline,
                candidate,
                policy=self.policy,
                baseline_snapshot=baseline_snapshot,
                candidate_snapshot=candidate_snapshot,
                baseline_candidate=baseline_candidate,
                candidate_candidate=candidate_candidate,
                baseline_manifest=baseline_manifest,
                candidate_manifest=candidate_manifest,
                holdout_metrics=holdout_metrics,
                target_metric_ids=target_metric_ids,
                target_failure_ids=target_failure_ids,
                created_at=created_at,
            )
        if candidate is None or isinstance(candidate, EnterpriseEvaluationReport):
            raise ComparisonError("An enterprise metric comparison needs two metric inputs")
        return compare_enterprise_metrics(
            baseline,
            candidate,
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            policy=self.policy,
            baseline_snapshot=baseline_snapshot,
            candidate_snapshot=candidate_snapshot,
            baseline_candidate=baseline_candidate,
            candidate_candidate=candidate_candidate,
            baseline_manifest=baseline_manifest,
            candidate_manifest=candidate_manifest,
            holdout_metrics=holdout_metrics,
            target_metric_ids=target_metric_ids,
            targeted_failure_ids=target_failure_ids,
            created_at=created_at,
        )


def compare_enterprise_metrics(
    baseline_metrics: Sequence[EnterpriseComparisonMetric | ComparisonMetric],
    candidate_metrics: Sequence[EnterpriseComparisonMetric | ComparisonMetric] | None = None,
    *,
    baseline_run_id: str = "baseline",
    candidate_run_id: str = "candidate",
    policy: EnterpriseComparisonPolicy | None = None,
    baseline_snapshot: object | None = None,
    candidate_snapshot: object | None = None,
    baseline_candidate: object | None = None,
    candidate_candidate: object | None = None,
    baseline_manifest: object | None = None,
    candidate_manifest: object | None = None,
    holdout_metrics: Sequence[ComparisonMetric] = (),
    target_metric_ids: Sequence[str] = (),
    pass_to_fail_transitions: Sequence[str] = (),
    targeted_failure_ids: Sequence[str] = (),
    target_cluster_id: str | None = None,
    created_at: datetime | None = None,
) -> BaselineComparison:
    """Compare enterprise dimensions with deterministic risk-aware gates."""

    if baseline_run_id == candidate_run_id:
        raise ComparisonError("Baseline and candidate run IDs must differ")
    resolved_policy = policy or EnterpriseComparisonPolicy(policy_id="enterprise-default")
    metrics = tuple(
        sorted(_pair_metrics(baseline_metrics, candidate_metrics), key=lambda x: x.metric_id)
    )
    incompatible = _snapshots_incompatible(
        baseline_snapshot,
        candidate_snapshot,
        require=resolved_policy.require_environment_compatibility,
    )
    metrics = _apply_policy(metrics, resolved_policy)
    regressions = tuple(
        metric.metric_id
        for metric in metrics
        if _metric_regressed(metric, resolved_policy.metric_tolerance)
    )
    hard_regressions = _unique(
        (
            *(
                metric.metric_id
                for metric in metrics
                if metric.metric_id in regressions and metric.hard
            ),
            *pass_to_fail_transitions,
            "environment_incompatible" if incompatible else "",
        )
    )
    enterprise_regressions = _unique(regressions)
    all_regressions = _unique(
        (
            *enterprise_regressions,
            *pass_to_fail_transitions,
            "environment_incompatible" if incompatible else "",
        )
    )
    target_ids = set(target_metric_ids)
    target_metrics = tuple(metric for metric in metrics if metric.metric_id in target_ids)
    target_improved = _target_improved(target_metrics or metrics, resolved_policy.metric_tolerance)
    if incompatible or hard_regressions:
        verdict = ComparisonVerdict.REJECTED
    elif all_regressions:
        verdict = ComparisonVerdict.REGRESSED
    elif not metrics:
        verdict = ComparisonVerdict.INCONCLUSIVE
    else:
        verdict = ComparisonVerdict.IMPROVED

    dimensions = {
        name: _dimension_ids(metrics, regressions, name) for name in EnterpriseComparisonDimension
    }
    numerical = tuple(
        metric.metric_id
        for metric in metrics
        if metric.metric_id in regressions
        and (
            metric.dimension
            in {
                EnterpriseComparisonDimension.COST,
                EnterpriseComparisonDimension.LATENCY,
                EnterpriseComparisonDimension.TOKEN_USAGE,
                EnterpriseComparisonDimension.RELIABILITY,
            }
            or metric.metric_name.casefold()
            in {"cost", "latency", "duration", "tokens", "token_usage", "reliability"}
        )
    )
    legacy_metrics = tuple((*(_legacy_metric(metric) for metric in metrics), *holdout_metrics))
    timestamp = created_at or utc_now()
    component_changes = compare_candidate_components(
        baseline_candidate,
        candidate_candidate,
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
    )
    baseline_provenance = _build_provenance(baseline_manifest, baseline_snapshot)
    candidate_provenance = _build_provenance(candidate_manifest, candidate_snapshot)
    return BaselineComparison(
        comparison_id=_comparison_id(baseline_run_id, candidate_run_id),
        experiment_id=f"experiment:{candidate_run_id}",
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        metrics=legacy_metrics,
        regressions=all_regressions,
        targeted_failure_ids=tuple(targeted_failure_ids),
        target_cluster_id=target_cluster_id,
        target_improved=target_improved,
        pass_to_fail_transitions=tuple(pass_to_fail_transitions),
        numerical_regressions=numerical,
        hard_regressions=hard_regressions,
        enterprise_metrics=metrics,
        enterprise_regressions=enterprise_regressions,
        business_regressions=dimensions[EnterpriseComparisonDimension.BUSINESS_OUTCOMES],
        security_regressions=dimensions[EnterpriseComparisonDimension.SECURITY],
        authorization_regressions=dimensions[EnterpriseComparisonDimension.AUTHORIZATION],
        approval_regressions=dimensions[EnterpriseComparisonDimension.APPROVALS],
        state_integrity_regressions=dimensions[EnterpriseComparisonDimension.STATE_INTEGRITY],
        workflow_completion_regressions=dimensions[
            EnterpriseComparisonDimension.WORKFLOW_COMPLETION
        ],
        cost_regressions=dimensions[EnterpriseComparisonDimension.COST],
        latency_regressions=dimensions[EnterpriseComparisonDimension.LATENCY],
        token_usage_regressions=dimensions[EnterpriseComparisonDimension.TOKEN_USAGE],
        tool_side_effect_regressions=dimensions[EnterpriseComparisonDimension.TOOL_SIDE_EFFECTS],
        delegation_regressions=dimensions[EnterpriseComparisonDimension.DELEGATION],
        reliability_regressions=dimensions[EnterpriseComparisonDimension.RELIABILITY],
        policy_regressions=dimensions[EnterpriseComparisonDimension.POLICY],
        tenant_boundary_regressions=dimensions[EnterpriseComparisonDimension.TENANT_BOUNDARY],
        risk_weighted_regression_score=sum(
            metric.weighted_loss for metric in metrics if metric.metric_id in regressions
        ),
        evaluator_family_aggregates=_family_aggregates(metrics, resolved_policy.metric_tolerance),
        environment_compatible=not incompatible,
        verdict=verdict,
        created_at=timestamp,
        notes=(
            "Baseline and candidate use incompatible environment snapshots."
            if incompatible
            else "Enterprise dimensions compared with risk-weighted gates."
        ),
        baseline_candidate_id=_optional_text(baseline_candidate, "candidate_id"),
        candidate_candidate_id=_optional_text(candidate_candidate, "candidate_id"),
        baseline_manifest_id=baseline_provenance[0],
        candidate_manifest_id=candidate_provenance[0],
        baseline_manifest_digest=baseline_provenance[1],
        candidate_manifest_digest=candidate_provenance[1],
        baseline_environment_snapshot_id=baseline_provenance[2],
        candidate_environment_snapshot_id=candidate_provenance[2],
        component_changes=component_changes,
        holdout_checked=bool(holdout_metrics),
        holdout_baseline_run_id=baseline_run_id if holdout_metrics else None,
        holdout_candidate_run_id=candidate_run_id if holdout_metrics else None,
    )


def compare_enterprise_reports(
    baseline: EnterpriseEvaluationReport,
    candidate: EnterpriseEvaluationReport,
    *,
    policy: EnterpriseComparisonPolicy | None = None,
    baseline_snapshot: object | None = None,
    candidate_snapshot: object | None = None,
    baseline_candidate: object | None = None,
    candidate_candidate: object | None = None,
    baseline_manifest: object | None = None,
    candidate_manifest: object | None = None,
    holdout_metrics: Sequence[ComparisonMetric] = (),
    target_metric_ids: Sequence[str] = (),
    target_failure_ids: Sequence[str] = (),
    target_cluster_id: str | None = None,
    created_at: datetime | None = None,
) -> BaselineComparison:
    """Derive comparable metrics from two typed evaluation reports."""

    if baseline.run_id == candidate.run_id:
        raise ComparisonError("Baseline and candidate report IDs must differ")
    if (baseline.dataset_id, baseline.dataset_version) != (
        candidate.dataset_id,
        candidate.dataset_version,
    ):
        raise ComparisonError("Baseline and candidate reports use different datasets")
    base_snapshot = (
        baseline_snapshot if baseline_snapshot is not None else baseline.environment_snapshot_id
    )
    candidate_snapshot_value = (
        candidate_snapshot if candidate_snapshot is not None else candidate.environment_snapshot_id
    )
    resolved_policy = policy or EnterpriseComparisonPolicy(policy_id="enterprise-default")
    base_scores = _scores_by_family(baseline)
    candidate_scores = _scores_by_family(candidate)
    if set(base_scores) != set(candidate_scores):
        raise ComparisonError(
            "Baseline and candidate reports must expose the same evaluator families"
        )
    metrics: list[EnterpriseComparisonMetric] = []
    for family in sorted(base_scores):
        base_values = base_scores[family]
        candidate_values = candidate_scores[family]
        dimension = _family_dimension(family, (*base_values, *candidate_values))
        metrics.append(
            EnterpriseComparisonMetric(
                metric_id=f"enterprise:{family}:mean_score",
                dimension=dimension,
                evaluator_family=family,
                metric_name="mean_score",
                baseline_value=sum(score.score for score in base_values) / len(base_values),
                candidate_value=sum(score.score for score in candidate_values)
                / len(candidate_values),
                higher_is_better=True,
                hard=dimension in resolved_policy.hard_dimensions,
                evidence_refs=_score_evidence((*base_values, *candidate_values)),
            )
        )
    target_ids = set(target_metric_ids)
    if target_failure_ids:
        target_set = set(target_failure_ids)
        target_score_ids = {
            target.removeprefix("failure:")
            for target in target_set
            if target.startswith("failure:")
        }
        target_evaluator_ids = {
            score.evaluator_id
            for score in (*baseline.scores, *candidate.scores)
            if score.score_id in target_score_ids
        }
        target_ids.update(
            f"enterprise:{failure.evaluator_id}:mean_score"
            for failure in (*baseline.failures, *candidate.failures)
            if _failure_matches_target(failure, target_failure_ids)
        )
        target_ids.update(
            f"enterprise:{evaluator_id}:mean_score" for evaluator_id in target_evaluator_ids
        )
    transitions = _pass_to_fail(baseline, candidate)
    resolved_holdout_metrics = tuple(holdout_metrics) or _holdout_metrics(baseline, candidate)
    return compare_enterprise_metrics(
        metrics,
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        policy=resolved_policy,
        baseline_snapshot=base_snapshot,
        candidate_snapshot=candidate_snapshot_value,
        baseline_candidate=baseline_candidate,
        candidate_candidate=candidate_candidate,
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        holdout_metrics=resolved_holdout_metrics,
        target_metric_ids=tuple(sorted(target_ids)),
        pass_to_fail_transitions=transitions,
        targeted_failure_ids=target_failure_ids,
        target_cluster_id=target_cluster_id,
        created_at=created_at,
    )


def compare_candidate_components(
    baseline: object | None,
    candidate: object | None,
    *,
    baseline_manifest: object | None = None,
    candidate_manifest: object | None = None,
) -> tuple[ComponentChange, ...]:
    """Compare exact prompt, skill, tool, policy, and runtime component intent."""

    baseline_source = _comparison_source(
        baseline_manifest if baseline_manifest is not None else baseline
    )
    candidate_source = _comparison_source(
        candidate_manifest if candidate_manifest is not None else candidate
    )
    baseline_values = _component_identity_map(baseline_source)
    candidate_values = _component_identity_map(candidate_source)
    keys = sorted(set(baseline_values) | set(candidate_values))
    changes: list[ComponentChange] = []
    for component_type, component_id in keys:
        baseline_ref = baseline_values.get((component_type, component_id))
        candidate_ref = candidate_values.get((component_type, component_id))
        # ``component_changes`` is evidence of differences.  Unchanged
        # components remain represented by the candidate/manifest itself and
        # should not make promotion evidence look like a change occurred.
        if baseline_ref == candidate_ref:
            continue
        changes.append(
            ComponentChange(
                component_type=component_type,
                component_id=component_id,
                baseline_ref=baseline_ref,
                candidate_ref=candidate_ref,
                relationship="agent",
            )
        )
    return tuple(changes)


def _component_identity_map(source: object | None) -> dict[tuple[str, str], str]:
    if source is None:
        return {}
    values: dict[tuple[str, str], str] = {}
    for kind, field in (
        ("agent", "agent"),
        ("prompt", "prompt_ref"),
        ("skill", "skill_refs"),
        ("tool", "tool_refs"),
        ("policy", "policy_refs"),
    ):
        raw = _source_component_value(source, field, kind)
        for value in _sequence_or_one(raw):
            identity = _component_identity(value, kind, source)
            if identity is None:
                continue
            component_id, rendered = identity
            values[(kind, component_id)] = rendered
    runtime = _source_component_value(source, "runtime_profile", "runtime_profile")
    if runtime is not None:
        identity = _component_identity(runtime, "runtime_profile", source)
        if identity is not None:
            values[("runtime_profile", identity[0])] = identity[1]
    provider = _source_component_value(source, "provider_profile", "provider")
    if provider is not None:
        identity = _component_identity(provider, "provider", source)
        if identity is not None:
            values[("provider", identity[0])] = identity[1]
    return values


def _source_component_value(source: object, field: str, kind: str) -> object:
    target = source
    nested_candidate = _value(source, "candidate")
    if nested_candidate is not None and nested_candidate is not source:
        target = nested_candidate
    if kind == "agent" and _value(source, "agent") is not None:
        target = _value(source, "agent")
    value = _value(target, field)
    if value is None and kind == "agent":
        value = target
    if value is None and field == "skill_refs":
        value = _value(target, "skills")
    if value is None and field == "tool_refs":
        value = (
            *_sequence_or_one(_value(target, "tools")),
            *_sequence_or_one(_value(target, "tool_bindings")),
        )
    if value is None and field == "policy_refs":
        value = _value(target, "policies")
    if value is None and field == "prompt_ref":
        prompt_kinds = {"system_prompt", "developer_prompt", "user_template"}
        artifacts = _sequence_or_one(_value(target, "artifacts"))
        value = next(
            (artifact for artifact in artifacts if str(_value(artifact, "kind")) in prompt_kinds),
            None,
        )
    return value


def _component_identity(value: object, kind: str, source: object) -> tuple[str, str] | None:
    component_id: object | None = None
    version: object | None = None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if ":" in raw:
            prefix, identity = raw.split(":", 1)
            if prefix in {kind, "provider", "runtime_profile"}:
                raw = identity
        component_id, separator, version = raw.rpartition("@")
        component_id = component_id if separator else raw
        if not separator:
            artifact_identity = _artifact_identity(source, kind, component_id)
            if artifact_identity is not None:
                return artifact_identity
        rendered = f"{kind}:{component_id}@{version}" if separator else f"{kind}:{component_id}"
        return component_id, rendered
    if isinstance(value, Mapping):
        mapping_identity = value.get("identity")
        if isinstance(mapping_identity, str) and mapping_identity.strip():
            return _component_identity(mapping_identity, kind, source)
        registry = value.get("registry_reference") or value.get("registry_ref")
        if isinstance(registry, str):
            return _component_identity(registry, kind, source)
        component_id = (
            value.get("component_id")
            or value.get(f"{kind}_id")
            or value.get("profile_id")
            or value.get("id")
            or value.get("artifact_id")
        )
        if kind == "agent":
            version = value.get("agent_version")
            # ``EnterpriseAgentCandidate.version`` identifies the immutable
            # Lab bundle, not necessarily the Harness runtime agent. Do not
            # report a runtime-agent change merely because the bundle was
            # rebuilt when no explicit ``agent_version`` was proposed. Flat
            # Harness agent records still use their ordinary ``version``.
            if version is None and "candidate_id" not in value:
                version = value.get("version")
        else:
            version = value.get("version")
    else:
        object_identity = getattr(value, "identity", None)
        if isinstance(object_identity, str) and object_identity.strip():
            return _component_identity(object_identity, kind, source)
        registry = getattr(value, "registry_reference", None)
        if isinstance(registry, str):
            return _component_identity(registry, kind, source)
        component_id = (
            getattr(value, "component_id", None)
            or getattr(value, f"{kind}_id", None)
            or getattr(value, "profile_id", None)
            or getattr(value, "id", None)
            or getattr(value, "artifact_id", None)
        )
        if component_id is None and kind == "agent":
            component_id = getattr(value, "agent_id", None)
        if component_id is None and kind == "agent":
            agent_ref = getattr(value, "agent_ref", None)
            if agent_ref is not None:
                return _component_identity(agent_ref, kind, source)
        if kind == "agent":
            version = getattr(value, "agent_version", None)
            # Lab candidates carry a separate candidate bundle version. It is
            # not a resolved Harness agent version unless explicitly set.
            if version is None and getattr(value, "candidate_id", None) is None:
                version = getattr(value, "version", None)
        else:
            version = getattr(value, "version", None)
    if component_id is None:
        return None
    component_id = str(component_id)
    if version is None:
        return component_id, f"{kind}:{component_id}"
    return component_id, f"{kind}:{component_id}@{version}"


def _comparison_source(source: object | None) -> object | None:
    """Unwrap common Harness build wrappers before comparing components."""

    if source is None:
        return None
    manifest = _value(source, "manifest")
    if manifest is not None and (
        _value(manifest, "prompt_ref") is not None or _value(manifest, "agent") is not None
    ):
        return manifest
    provenance = _value(source, "provenance")
    if provenance is not None and (
        _value(provenance, "prompt_ref") is not None or _value(provenance, "agent_ref") is not None
    ):
        return provenance
    candidate = _value(source, "candidate")
    if candidate is not None and candidate is not source:
        if _value(source, "registry_references") is not None:
            return source
        return candidate
    definition = _value(source, "definition")
    if definition is not None:
        candidate = _value(definition, "candidate")
        if candidate is not None:
            return candidate
        return definition
    return source


def _artifact_identity(
    source: object,
    kind: str,
    component_id: str,
) -> tuple[str, str] | None:
    """Resolve a stable component ID through candidate artifact references."""

    artifacts = _sequence_or_one(_value(source, "artifacts"))
    if not artifacts:
        nested_candidate = _value(source, "candidate")
        if nested_candidate is not None and nested_candidate is not source:
            artifacts = _sequence_or_one(_value(nested_candidate, "artifacts"))
    for artifact in artifacts:
        raw_kind = _value(artifact, "kind")
        kind_value = getattr(raw_kind, "value", raw_kind)
        if kind == "prompt" and kind_value not in {
            "system_prompt",
            "developer_prompt",
            "user_template",
        }:
            continue
        if kind == "skill" and kind_value != "skill_configuration":
            continue
        if kind == "tool" and kind_value not in {"tool_binding", "tool_configuration"}:
            continue
        if kind == "policy" and kind_value not in {"policy", "approval_policy"}:
            continue
        registry = _value(artifact, "registry_reference")
        if isinstance(registry, str) and "@" in registry:
            prefix, identity = registry.split(":", 1) if ":" in registry else (kind, registry)
            artifact_id, _, version = identity.rpartition("@")
            if prefix == kind and artifact_id == component_id and version:
                return component_id, f"{kind}:{component_id}@{version}"
        artifact_component_id = _value(artifact, "artifact_id")
        artifact_version = _value(artifact, "version")
        if artifact_component_id == component_id and artifact_version:
            return component_id, f"{kind}:{component_id}@{artifact_version}"
    registry_references = _sequence_or_one(_value(source, "registry_references"))
    if not registry_references:
        nested_candidate = _value(source, "candidate")
        if nested_candidate is not None and nested_candidate is not source:
            registry_references = _sequence_or_one(_value(nested_candidate, "registry_references"))
    for reference in registry_references:
        raw_kind = _value(reference, "component_kind") or _value(reference, "kind")
        raw_kind = getattr(raw_kind, "value", raw_kind)
        if raw_kind != kind:
            continue
        reference_id = _value(reference, "component_id") or _value(reference, "id")
        reference_version = _value(reference, "version")
        if reference_id == component_id and reference_version:
            return component_id, f"{kind}:{component_id}@{reference_version}"
    return None


def _sequence_or_one(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


def _value(source: object, name: str) -> object:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _optional_text(source: object | None, name: str) -> str | None:
    value = _value(source, name) if source is not None else None
    return str(value) if value is not None and str(value).strip() else None


def _build_provenance(
    manifest: object | None, snapshot: object | None
) -> tuple[str | None, str | None, str | None]:
    manifest_source = manifest
    if manifest_source is not None:
        nested_manifest = _value(manifest_source, "manifest")
        if nested_manifest is not None:
            manifest_source = nested_manifest
        provenance = _value(manifest_source, "provenance")
        if provenance is not None:
            manifest_source = provenance
    snapshot_identity = (
        snapshot.strip()
        if isinstance(snapshot, str) and snapshot.strip()
        else _optional_text(snapshot, "registry_snapshot_id")
        or _optional_text(snapshot, "environment_snapshot_id")
        or _optional_text(snapshot, "identity")
    )
    return (
        _optional_text(manifest_source, "manifest_id")
        or _optional_text(manifest_source, "resolved_manifest_id"),
        _optional_text(manifest_source, "manifest_digest")
        or _optional_text(manifest_source, "resolved_manifest_digest"),
        _optional_text(manifest_source, "registry_snapshot_id") or snapshot_identity,
    )


def _pair_metrics(
    baseline: Sequence[EnterpriseComparisonMetric | ComparisonMetric],
    candidate: Sequence[EnterpriseComparisonMetric | ComparisonMetric] | None,
) -> tuple[EnterpriseComparisonMetric, ...]:
    baseline_values = tuple(_coerce_metric(item) for item in baseline)
    if candidate is None:
        return baseline_values
    candidate_values = tuple(_coerce_metric(item) for item in candidate)
    base_by_id = {item.metric_id: item for item in baseline_values}
    current_by_id = {item.metric_id: item for item in candidate_values}
    if len(base_by_id) != len(baseline_values) or len(current_by_id) != len(candidate_values):
        raise ComparisonError("Enterprise metric IDs must be unique")
    if set(base_by_id) != set(current_by_id):
        raise ComparisonError("Baseline and candidate enterprise metrics must have the same IDs")
    result: list[EnterpriseComparisonMetric] = []
    for metric_id in sorted(base_by_id):
        base = base_by_id[metric_id]
        current = current_by_id[metric_id]
        if (base.dimension, base.evaluator_family, base.higher_is_better) != (
            current.dimension,
            current.evaluator_family,
            current.higher_is_better,
        ):
            raise ComparisonError(f"Enterprise metric {metric_id} changed its contract")
        result.append(
            base.model_copy(
                update={
                    "candidate_value": current.candidate_value,
                    "evidence_refs": tuple(
                        dict.fromkeys((*base.evidence_refs, *current.evidence_refs))
                    ),
                }
            )
        )
    return tuple(result)


def _coerce_metric(
    metric: EnterpriseComparisonMetric | ComparisonMetric,
) -> EnterpriseComparisonMetric:
    if isinstance(metric, EnterpriseComparisonMetric):
        return metric
    try:
        dimension = EnterpriseComparisonDimension(metric.dimension)
    except ValueError:
        dimension = EnterpriseComparisonDimension.BUSINESS_OUTCOMES
    return EnterpriseComparisonMetric(
        metric_id=metric.metric_id,
        dimension=dimension,
        evaluator_family=metric.slice_key or "legacy",
        metric_name=metric.metric_name,
        baseline_value=metric.baseline_value,
        candidate_value=metric.candidate_value,
        higher_is_better=metric.higher_is_better,
    )


def _apply_policy(
    metrics: Sequence[EnterpriseComparisonMetric],
    policy: EnterpriseComparisonPolicy,
) -> tuple[EnterpriseComparisonMetric, ...]:
    hard_dimensions = set(policy.hard_dimensions)
    weights: dict[EnterpriseComparisonDimension, ComparisonDimensionWeight] = {
        item.dimension: item for item in policy.dimension_weights
    }
    return tuple(
        metric.model_copy(
            update={
                "risk_weight": weights[metric.dimension].weight
                if metric.dimension in weights
                else metric.risk_weight,
                "hard": metric.hard
                or metric.dimension in hard_dimensions
                or (weights[metric.dimension].hard if metric.dimension in weights else False),
            }
        )
        for metric in metrics
    )


def _metric_regressed(metric: EnterpriseComparisonMetric, tolerance: float) -> bool:
    if metric.higher_is_better:
        return metric.candidate_value < metric.baseline_value - tolerance
    return metric.candidate_value > metric.baseline_value + tolerance


def _dimension_ids(
    metrics: Sequence[EnterpriseComparisonMetric],
    regressions: Sequence[str],
    dimension: EnterpriseComparisonDimension,
) -> tuple[str, ...]:
    regression_ids = set(regressions)
    return tuple(
        metric.metric_id
        for metric in metrics
        if metric.dimension == dimension and metric.metric_id in regression_ids
    )


def _target_improved(metrics: Sequence[EnterpriseComparisonMetric], tolerance: float) -> bool:
    return bool(metrics) and all(
        metric.candidate_value > metric.baseline_value + tolerance
        if metric.higher_is_better
        else metric.candidate_value < metric.baseline_value - tolerance
        for metric in metrics
    )


def _failure_matches_target(failure: object, target_failure_ids: Sequence[str]) -> bool:
    """Match report failure IDs and normalized score-backed failure IDs."""

    targets = set(target_failure_ids)
    failure_id = _optional_text(failure, "failure_id")
    score_id = _optional_text(failure, "score_id")
    return bool(
        (failure_id and failure_id in targets)
        or (score_id and score_id in targets)
        or (score_id and f"failure:{score_id}" in targets)
    )


def _holdout_metrics(
    baseline: EnterpriseEvaluationReport,
    candidate: EnterpriseEvaluationReport,
) -> tuple[ComparisonMetric, ...]:
    """Build safe holdout metrics when both reports contain the same holdout set."""

    def scores_by_family(
        report: EnterpriseEvaluationReport,
    ) -> tuple[set[str], dict[str, list[float]]]:
        score_by_id = {score.score_id: score for score in report.scores}
        case_ids: set[str] = set()
        grouped: dict[str, list[float]] = {}
        for result in report.case_results:
            split = getattr(result.split, "value", result.split)
            if split != "holdout":
                continue
            case_ids.add(result.case_id)
            for score_id in result.score_ids:
                score = score_by_id.get(score_id)
                if score is not None:
                    grouped.setdefault(score.evaluator_id, []).append(score.score)
        return case_ids, grouped

    baseline_cases, baseline_families = scores_by_family(baseline)
    candidate_cases, candidate_families = scores_by_family(candidate)
    if not baseline_cases or baseline_cases != candidate_cases:
        return ()
    metrics: list[ComparisonMetric] = []
    for family in sorted(set(baseline_families) & set(candidate_families)):
        base_values = baseline_families[family]
        candidate_values = candidate_families[family]
        if not base_values or not candidate_values:
            continue
        metrics.append(
            ComparisonMetric(
                metric_id=f"holdout.{family}",
                baseline_value=sum(base_values) / len(base_values),
                candidate_value=sum(candidate_values) / len(candidate_values),
                higher_is_better=True,
                dimension="holdout",
                slice_key="holdout",
                metric_name="mean_score",
            )
        )
    return tuple(metrics)


def _family_aggregates(
    metrics: Sequence[EnterpriseComparisonMetric], tolerance: float
) -> tuple[EvaluatorFamilyAggregate, ...]:
    grouped: dict[str, list[EnterpriseComparisonMetric]] = {}
    for metric in metrics:
        grouped.setdefault(metric.evaluator_family, []).append(metric)
    return tuple(
        EvaluatorFamilyAggregate(
            family=family,
            metric_ids=tuple(metric.metric_id for metric in values),
            baseline_score=sum(metric.baseline_value for metric in values) / len(values),
            candidate_score=sum(metric.candidate_value for metric in values) / len(values),
            regression_count=sum(_metric_regressed(metric, tolerance) for metric in values),
            risk_weighted_loss=sum(metric.weighted_loss for metric in values),
            regressed=any(_metric_regressed(metric, tolerance) for metric in values),
        )
        for family, values in sorted(grouped.items())
    )


def _scores_by_family(
    report: EnterpriseEvaluationReport,
) -> dict[str, tuple[EvaluationScore, ...]]:
    grouped: dict[str, list[EvaluationScore]] = {}
    for score in report.scores:
        grouped.setdefault(score.evaluator_id, []).append(score)
    return {
        family: tuple(sorted(values, key=lambda score: score.score_id))
        for family, values in grouped.items()
    }


def _family_dimension(
    family: str, scores: Sequence[EvaluationScore]
) -> EnterpriseComparisonDimension:
    normalized = family.casefold()
    categories = {score.failure_category for score in scores if score.failure_category is not None}
    if FailureCategory.AUTHORIZATION in categories or "authorization" in normalized:
        return (
            EnterpriseComparisonDimension.TENANT_BOUNDARY
            if "tenant" in normalized
            else EnterpriseComparisonDimension.AUTHORIZATION
        )
    if FailureCategory.POLICY in categories or "policy" in normalized:
        return EnterpriseComparisonDimension.POLICY
    if FailureCategory.APPROVAL in categories or "approval" in normalized:
        return EnterpriseComparisonDimension.APPROVALS
    if (
        FailureCategory.STATE in categories
        or FailureCategory.DATA_INTEGRITY in categories
        or "state" in normalized
    ):
        return EnterpriseComparisonDimension.STATE_INTEGRITY
    if FailureCategory.DELEGATION in categories or "delegation" in normalized:
        return EnterpriseComparisonDimension.DELEGATION
    if FailureCategory.TOOL_SIDE_EFFECT in categories or "side_effect" in normalized:
        return EnterpriseComparisonDimension.TOOL_SIDE_EFFECTS
    if "workflow" in normalized:
        return EnterpriseComparisonDimension.WORKFLOW_COMPLETION
    if FailureCategory.BUSINESS_OUTCOME in categories or normalized.startswith("business"):
        return EnterpriseComparisonDimension.BUSINESS_OUTCOMES
    if any(marker in normalized for marker in ("latency", "duration")):
        return EnterpriseComparisonDimension.LATENCY
    if any(marker in normalized for marker in ("token", "usage")):
        return EnterpriseComparisonDimension.TOKEN_USAGE
    if "cost" in normalized:
        return EnterpriseComparisonDimension.COST
    if any(marker in normalized for marker in ("reliability", "retry", "timeout")):
        return EnterpriseComparisonDimension.RELIABILITY
    if any(marker in normalized for marker in ("safety", "security", "privacy")):
        return EnterpriseComparisonDimension.SECURITY
    return EnterpriseComparisonDimension.BUSINESS_OUTCOMES


def _score_evidence(scores: Sequence[EvaluationScore]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ref for score in scores for ref in score.evidence_refs))


def _pass_to_fail(
    baseline: EnterpriseEvaluationReport, candidate: EnterpriseEvaluationReport
) -> tuple[str, ...]:
    candidate_results = {
        (result.case_id, result.repeat_index): result for result in candidate.case_results
    }
    return tuple(
        f"pass_to_fail:{result.case_id}:repeat-{result.repeat_index}"
        for result in sorted(
            baseline.case_results, key=lambda item: (item.case_id, item.repeat_index)
        )
        if result.passed
        and (
            candidate_results.get((result.case_id, result.repeat_index)) is None
            or not candidate_results[(result.case_id, result.repeat_index)].passed
        )
    )


def _snapshots_incompatible(
    baseline: object | None,
    candidate: object | None,
    *,
    require: bool,
) -> bool:
    if not require:
        return False
    if (baseline is None) != (candidate is None):
        return True
    if baseline is None or candidate is None:
        return False
    checker = getattr(baseline, "is_compatible_with", None)
    if callable(checker):
        return not bool(checker(candidate))
    return baseline != candidate


def _legacy_metric(metric: EnterpriseComparisonMetric) -> ComparisonMetric:
    return ComparisonMetric(
        metric_id=metric.metric_id,
        baseline_value=metric.baseline_value,
        candidate_value=metric.candidate_value,
        higher_is_better=metric.higher_is_better,
        dimension=metric.dimension.value,
        slice_key=metric.evaluator_family,
        metric_name=metric.metric_name,
    )


def _comparison_id(baseline_run_id: str, candidate_run_id: str) -> str:
    value = f"{baseline_run_id}:{candidate_run_id}".encode("utf-8")
    return f"comparison-{sha256(value).hexdigest()[:16]}"


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


__all__ = [
    "ComparisonError",
    "EnterpriseComparisonRunner",
    "compare_candidate_components",
    "compare_enterprise_metrics",
    "compare_enterprise_reports",
]
