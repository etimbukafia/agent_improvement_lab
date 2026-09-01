# Pydantic Evals adapter

The old `enterprise_agent_improvement_lab.runner.PydanticEvalsRunner` module was removed.
The optional adapter now lives at
`enterprise_agent_improvement_lab.runners.PydanticEvalsRunner`.

Import it explicitly when the `evals` extra is installed:

```python
from enterprise_agent_improvement_lab.runners import PydanticEvalsRunner

runner = PydanticEvalsRunner(runtime)
result = runner.run_sync(dataset, candidate, manifest, repeat=2)
```

The adapter imports Pydantic Evals lazily. It translates typed
`EnterpriseEvaluationCase` values, runs the provider dataset, and returns
Lab-native `EnterpriseEvaluationRunResult` evidence.

The Lab core remains independent of Pydantic Evals. Core contracts, storage,
comparison, promotion, and evaluation semantics use typed Lab contracts.
