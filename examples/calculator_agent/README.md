# Calculator agent example

This example runs a complete Lab improvement cycle without a model key.

The baseline returns arithmetic results directly. It does not call the
calculator tool. A confirmed human failure creates a bounded prompt candidate.
The candidate calls the calculator tool. The Lab compares both candidates on
development and holdout cases, then records an explicit approval.

Run it from the repository root:

```text
python -m examples.calculator_agent.run_cycle
```

Saved reports are written to `examples/calculator_agent/reports/` by default.
They include the baseline report, candidate report, normalized failures,
failure clusters, candidate artifacts, comparison, and promotion decision.

The runtime uses only safe numeric arithmetic. It does not use `eval`, a model,
or a network service.
