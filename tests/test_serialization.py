from enterprise_agent_improvement_lab.contracts.experiments import ExperimentRun
from enterprise_agent_improvement_lab.serialization import (
    model_from_json,
    model_to_json,
    stable_json_dumps,
)


def test_model_round_trip_is_lossless(dataset, experiment):
    assert model_from_json(type(dataset), model_to_json(dataset)) == dataset
    assert model_from_json(ExperimentRun, model_to_json(experiment)) == experiment


def test_json_key_order_and_whitespace_are_stable():
    assert stable_json_dumps({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert stable_json_dumps({"a": 1, "b": 2}) == '{"a":1,"b":2}'
