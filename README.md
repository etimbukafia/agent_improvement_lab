# Enterprise Agent Improvement Lab

Enterprise Agent Improvement Lab is a small self-improvement stack for AI agents. It turns
evaluation cases and observed agent behavior into reviewable failures, bounded
candidate changes, reproducible comparisons, and human-controlled promotion
decisions.

The Lab is deliberately a library first. An agent runtime connects through a
small adapter boundary, while the Lab owns the contracts, deterministic
evaluation evidence, experiment records, and improvement workflow.

## What it does

- validates versioned evaluation datasets and immutable prompt artifacts;
- stores typed traces, session summaries, scores, failures, and experiment
  manifests;
- runs deterministic checks alongside explicitly marked subjective judges;
- groups failures into actionable improvement work;
- compares a candidate with a baseline and records promotion or rollback
  decisions;
- exposes CLI workflows and read-only dashboard query services;
- includes a deterministic calculator-agent improvement cycle.

The core package does not orchestrate an agent, import an agent framework, or
deploy a model. Those concerns belong behind adapters or in the application
using the Lab.

## Development

The repository targets Python 3.11–3.14. The local development interpreter is
recorded in `.python-version`.

```text
python -m pip install -e ".[dev]"
python -m ruff format --check src tests examples
python -m ruff check src tests examples
python -m mypy src examples
python -m pytest
```

The same commands run in the repository quality workflow. `python -m compileall
-q src tests examples` is a useful additional smoke check.

Run the calculator cycle without a model key:

```text
python -m examples.calculator_agent.run_cycle
```

## Repository map

```text
docs/                         product brief, non-goals, and architecture decisions
examples/                     coverage artifact and calculator-agent example
src/enterprise_agent_improvement_lab/    installable core package
```

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
