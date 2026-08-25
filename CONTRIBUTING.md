# Contributing

Keep changes small, typed, and covered by tests. The Lab is a self-improvement
stack, so changes to evaluator definitions, contracts, datasets, and promotion
rules need especially clear evidence and versioning.

## Before opening a change

Install the pinned development dependencies and run the complete local gate:

```text
python -m pip install -e ".[dev]"
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy src
python -m pytest
```

If a change affects the package boundary, also run:

```text
python -m compileall -q src tests
```

## Change rules

- Preserve `extra="forbid"` and schema-version validation for public
  contracts unless an ADR explains the change.
- Keep deterministic numerical and structural checks separate from model-judge
  logic.
- Do not add an agent framework, provider SDK, example, or optional integration
  to core imports.
- Do not make promotion or deployment implicit; record the decision boundary.
- Add or update an ADR when a change affects a repository-wide boundary.
- Never commit credentials, raw sensitive prompts, or sensitive tool results.
