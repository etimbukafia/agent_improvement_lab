# Enterprise Enterprise Agent Improvement Lab v0.1 Scope

## Goal

Prove one complete enterprise improvement cycle before adding wider production features.

## Required v0.1 capabilities

The first enterprise release must include:

1. general enterprise execution traces;
2. enterprise candidate and artifact model;
3. typed candidate changes;
4. Enterprise Agent Harness adapter;
5. environment and registry snapshots;
6. enterprise evaluation cases;
7. sandbox and fixture lifecycle;
8. core enterprise evaluator families;
9. expanded failure taxonomy;
10. root-cause contracts;
11. improvement planner;
12. bounded candidate builders;
13. enterprise-aware comparison and promotion;
14. one full Harness → Lab → candidate → Harness → comparison round trip.

## Required evaluator families

The first release must cover at least:

- state correctness;
- authorization;
- approval boundaries;
- workflow validity;
- tool use;
- operational cost and latency;
- basic business outcome checks.

## Reference vertical slice

Use a delivery-resolution agent.

The baseline agent can:

- identify a customer;
- retrieve an order;
- search policy;
- escalate.

The baseline agent cannot retrieve live shipment tracking.

## Expected improvement cycle

```text
1. Evaluate baseline.
2. Detect delivery failures.
3. Cluster failures.
4. Identify missing tracking capability.
5. Create root-cause hypothesis.
6. Choose TOOL_ADDITION.
7. Attach approved get_tracking tool.
8. Instantiate candidate through Enterprise Agent Harness.
9. Re-run evaluation.
10. Compare baseline and candidate.
11. Detect remaining regression or split-shipment weakness.
12. Produce promotion evidence.
13. Require a human promotion decision.
```

## What this vertical slice must prove

- stateful evaluation;
- tool additions;
- candidate lineage;
- registry snapshots;
- Harness integration;
- deterministic evaluation;
- failure mining;
- root-cause evidence;
- bounded improvement;
- regression detection;
- promotion evidence.

## Definition of done

The v0.1 architecture is proven when the system can answer:

- What failed?
- Why did it likely fail?
- What part of the agent should change?
- What changed in the candidate?
- Was the candidate better?
- What regressed?
- Is the candidate eligible for the next lifecycle stage?
- What evidence supports that conclusion?

The final promotion decision must remain human-controlled.
