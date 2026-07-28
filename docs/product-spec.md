# Boundary Product Specification

## Purpose

Boundary is an active reliability-testing and release-gating product for AI agents. It is not a passive tracing product. Its core claim is:

> Given an instrumented AI agent endpoint, Boundary can inject a controlled production failure, capture the resulting execution evidence, identify the first failing system boundary, generate a reproducible regression scenario, and determine whether the agent meets an explicit release policy.

The repository README expresses the same claim using “instrumented agent run” and “regression case.” This specification treats “endpoint” and “scenario” as clarifications, not changes to that intent.

This document defines the product outcome Phase 1 must prove. A proposed Phase 1 feature is in scope only if it is necessary to demonstrate or trust that complete outcome.

## Primary User

The primary user is an AI engineer preparing a tool-using agent or workflow for production. The engineer can instrument an agent, understand its tool calls and retry behavior, and interpret a concrete failure report. They need a repeatable way to test reliability before release, not another place to browse traces.

## Current User Workflow

Today the engineer typically:

1. Runs mostly happy-path evaluations.
2. Encounters a failure in an evaluation or production-like environment.
3. Attempts to reproduce it manually.
4. Reads traces without a consistent diagnostic rule.
5. Guesses whether the primary fault is in the model, tool, orchestration, state, retry policy, or another boundary.
6. Manually creates a regression case if time permits.
7. Makes a release decision without a repeatable, evidence-backed reliability gate.

This workflow is slow, depends on individual judgment, and often confuses downstream symptoms with the first unsafe divergence.

## Core Problem

Existing evaluation and tracing workflows do not make controlled failure testing a first-class, repeatable release activity. Engineers lack one workflow that:

- introduces a known failure at a specific system boundary;
- records trustworthy, ordered evidence of what happened;
- compares observed behavior with explicit expected safe behavior;
- locates the earliest observed divergence;
- preserves that case for regression;
- and converts assertion results into a release decision.

As a result, teams can ship agents that perform well on happy paths but behave unsafely or unpredictably when tools fail.

## Product Thesis

If Boundary turns a realistic production failure into a controlled, evidence-backed, reproducible test campaign, an AI engineer can diagnose the first failing boundary and make a repeatable release decision faster and with greater confidence than through manual trace inspection.

Phase 1 tests this thesis with one failure mode: a timeout injected into a tool call made by a bundled sample agent.

## Why Tracing Tools Alone Are Insufficient

Tracing is necessary evidence infrastructure, but traces usually describe executions that already happened. A trace viewer alone does not:

- deliberately create a production-shaped failure;
- prove that a fault was injected at the intended boundary;
- define the safe behavior expected after that fault;
- distinguish the primary divergence from later retry or terminal-state symptoms;
- turn the same failure into a rerunnable regression scenario;
- or evaluate an explicit release policy.

Boundary may use OpenTelemetry-compatible concepts, but its product workflow begins with an active test campaign and ends with a policy result. Trace display supports that workflow; it is not the product outcome.

## Primary Differentiator

Boundary links five capabilities into one auditable loop:

1. **Controlled fault injection** at a named system boundary.
2. **Ordered execution evidence** that includes proof of the injected fault.
3. **Deterministic comparison** against explicit safe-behavior assertions.
4. **First-failing-boundary localization** based on the earliest observed divergence, with downstream symptoms labeled separately.
5. **Reproducible regression and release gating** using the same scenario and policy before and after a fix.

The differentiator is the complete loop, not any single trace, diagnosis label, or dashboard.

## Phase 1 User Journey

1. The engineer opens Boundary and creates a test campaign using the bundled tool-timeout scenario and vulnerable sample agent.
2. Boundary invokes the real sample agent and injects a timeout at the intended tool boundary.
3. Boundary captures ordered execution events, including evidence that the injection occurred.
4. Boundary compares the observed run with the scenario’s expected safe behavior.
5. Boundary identifies the earliest observed divergence as the first failing boundary and distinguishes later symptoms.
6. The run-details view shows the injected fault, event sequence, expected-versus-observed assertions, localization, and failed release result.
7. The engineer applies the provided narrow fix variant to the sample agent.
8. The engineer reruns the same saved regression scenario under the same release policy.
9. Boundary shows changed real execution evidence, satisfied assertions, and a passing release result.

## Phase 1 Vertical Slice

Phase 1 is one production-shaped path:

```text
Create one test campaign
→ run one bundled vulnerable tool-using sample agent
→ inject one controlled tool timeout
→ capture ordered execution evidence
→ compare explicit expected and observed behavior
→ identify the first failing boundary
→ show primary failure and downstream symptoms
→ evaluate one explicit release policy as failed
→ run the narrowly fixed sample-agent variant
→ rerun the same regression scenario
→ evaluate the same release policy as passed
```

The vertical slice must execute the sample agent both times. Stored or manually constructed “before” and “after” traces do not satisfy the claim.

Phase 1 may expose only the controls and views needed for this path. It does not need a general campaign builder, fault catalog, policy editor, or trace explorer.

## Phase 1 Acceptance Criteria

Phase 1 is accepted only when all of the following are demonstrated end to end:

### Campaign and execution

- An engineer can create and run a campaign against the bundled vulnerable sample agent.
- The campaign records the exact version of the system-under-test contract, scenario definition, sample-agent variant, and release assertions used for each run.
- The vulnerable and fixed runs execute real agent logic; results are not selected fixtures or hand-authored traces.
- A completed run is immutable enough to audit: reruns create distinct run records rather than rewriting prior evidence.

### Controlled fault

- Boundary injects one tool timeout at the declared tool boundary under a deterministic trigger.
- The evidence identifies the intended injection point and records that the fault was actually applied.
- A control run or equivalent verification demonstrates that the timeout came from Boundary’s injection mechanism rather than an incidental tool failure.
- The injected timeout and relevant timing values are bounded so the demonstration finishes predictably.

### Evidence

- Boundary captures a run-scoped, ordered sequence of events sufficient to reconstruct the agent’s relevant decisions, tool attempt, injected timeout, retry or recovery behavior, and terminal outcome.
- Events carry stable run identity, event identity, boundary identity, event type, and ordering information.
- Invalid, duplicated, late, or untrusted system-under-test input cannot silently alter authoritative Boundary-owned injection or policy evidence.
- The UI displays evidence from the actual recorded runs.

### Deterministic diagnosis

- Expected safe behavior is represented as explicit, machine-evaluable assertions.
- Boundary compares expected and observed behavior without requiring an LLM-generated conclusion.
- The first failing boundary is the boundary associated with the earliest assertion-relevant divergence in the ordered evidence.
- The report separates that primary divergence from downstream symptoms such as repeated calls, exhausted retries, an incorrect terminal state, or a missed budget.
- The localization result cites the specific evidence and failed assertion that produced it.

### Regression and release policy

- The initial vulnerable run produces the expected failed assertions and a failed release result.
- The scenario can be rerun without manually recreating its inputs, injection settings, expected behavior, or release assertions.
- A narrow, inspectable change in the bundled sample-agent variant addresses the diagnosed behavior.
- The fixed run produces materially different execution evidence and satisfies the same release assertions.
- The same explicit policy produces a passing release result for the fixed run.
- The release result is a deterministic aggregation of assertion outcomes; it is not a free-form model judgment.

### Operability and scope

- A new developer can run the vertical slice locally from documented steps using the chosen local process and container setup.
- Automated tests cover the deterministic injection, ordering, localization, and policy logic at appropriate levels.
- The end-to-end demonstration can be repeated reliably in the supported local environment.
- No Phase 1 non-goal is required to complete the demonstration.

## Explicit Phase 1 Non-Goals

Phase 1 will not include:

- Kubernetes or production cloud infrastructure;
- multiple cloud providers or a finalized AWS topology;
- full authentication, RBAC, billing, multi-tenancy, or organization administration;
- arbitrary agent-framework integrations;
- production trace import or general incident replay;
- automatic source-code modification;
- a general observability or trace-analysis platform;
- a broad fault catalog or arbitrary fault authoring;
- parallel or distributed campaign execution;
- CI or GitHub release integration;
- hosted remote workers or customer-VPC execution;
- a plugin marketplace or visual workflow builder;
- team and enterprise workflows;
- security or prompt-injection testing;
- generic AI-generated root-cause conclusions;
- saved-policy management beyond the one versioned Phase 1 policy;
- Redis without a measured requirement;
- multi-agent architecture without independently justified responsibilities;
- LangGraph unless explicit workflow state provides a concrete advantage for this slice.

## Phase 2 Opportunities

Phase 2 should be selected from evidence gathered in Phase 1 rather than treated as committed scope. Candidate opportunities include:

- a small set of additional high-value fault scenarios;
- campaign orchestration and parallel scenario execution;
- CI and GitHub checks that consume deterministic release results;
- saved and reusable release policies;
- historical reliability comparisons;
- production trace import and incident-to-regression replay;
- one framework-specific adapter based on user demand;
- stronger security and prompt-injection scenarios.

The first Phase 2 investment should address the largest validated limitation in the Phase 1 workflow.

## Longer-Term Platform Direction

Boundary may evolve into a platform for active agent reliability engineering:

- author and execute failure campaigns;
- replay production incidents as regression scenarios;
- evaluate versioned release policies across agent versions;
- compare reliability over time;
- run tests in hosted, private, or customer-VPC workers;
- integrate release evidence into engineering workflows;
- and support multiple agent frameworks through narrow, versioned adapters.

This direction is intentionally non-binding. Each capability must own a demonstrated user need, trust requirement, scale constraint, or isolation requirement before it becomes architecture.

## Product Risks

- **Active testing may not be urgent enough.** Engineers may prefer better trace inspection or conventional evaluations to deliberate fault injection.
- **The first-failing-boundary concept may confuse users.** Real systems can have ambiguous causality, concurrent events, or multiple contributing defects.
- **Evidence may not be trustworthy enough.** A system under test could omit, reorder, duplicate, or fabricate events unless Boundary owns critical evidence and validates the contract.
- **One bundled demo may feel artificial.** The before-and-after result may prove implementation correctness without proving external product demand.
- **The fixed variant may overfit the scenario.** A passing policy could demonstrate one narrow response rather than general reliability.
- **Determinism may be overstated.** Timing, model behavior, and process scheduling can make timeout scenarios flaky.
- **Release “pass” may imply too much.** The UI and language must make clear that a pass applies only to the executed scenario and policy version.
- **Scope may drift toward observability.** A rich trace UI could consume the milestone without proving injection, regression, and gating.
- **Untrusted content may influence operators or analysis.** Tool and agent output must never be treated as authoritative Boundary control data.

## Open Product Questions

- What minimum external contract would a real agent team accept to instrument and invoke its endpoint?
- Is “first failing boundary” the clearest user-facing term, and what evidence makes users trust it?
- Which expected safe response to a tool timeout is most representative: bounded retry, fallback, explicit failure, or a combination?
- What should the Phase 1 policy assert so that the vulnerable failure and fixed pass are meaningful rather than contrived?
- How should Boundary communicate the limited scope of a release result?
- Must the first external integration be endpoint-based, library-based, or sidecar/proxy-based?
- What evidence must Boundary own independently of the system under test?
- How much scenario editing, if any, is necessary for Phase 1 users to understand reproducibility?
- What user research or design-partner signal would justify Phase 2 investment?

## Measurable Success Criteria

### Product-proof metrics

- The complete vulnerable-to-fixed demonstration succeeds in at least 9 of 10 consecutive clean local executions.
- The injected fault is evidenced at the intended boundary in every successful demonstration run.
- The vulnerable variant is localized to the intended first failing boundary and fails the policy in every successful demonstration run.
- The fixed variant passes the same scenario and policy in every successful demonstration run.
- A rerun requires no manual reconstruction of scenario inputs, injection configuration, expected behavior, or assertions.
- Every localization and release result links to the exact assertion and recorded evidence used to compute it.

### Usability and value signals

- A target AI engineer can explain, from the run-details view, what fault was injected, where safe behavior first diverged, which later events were symptoms, and why the policy passed or failed.
- A target AI engineer can complete the bundled campaign and regression rerun from documented setup without source-level knowledge of Boundary.
- At least three target-user interviews or design-partner reviews evaluate whether this workflow is more valuable than manual trace inspection; findings are recorded before Phase 2 scope is selected.

These criteria prove the Phase 1 product mechanism and gather an initial demand signal. They do not establish broad production readiness or market fit.

## Concise Demo Narrative

An engineer runs Boundary’s bundled campaign against a vulnerable sample agent. Boundary deliberately times out the agent’s tool call and records proof of the injection plus the ordered events that follow. The agent handles the timeout unsafely; Boundary points to the earliest divergence from the scenario’s expected safe behavior, labels later retry and terminal-state problems as symptoms, and fails the explicit release policy. The engineer selects the narrowly fixed sample-agent variant and reruns the same saved scenario. A new real execution shows the corrected behavior, the same assertions pass, and Boundary returns a passing release result. The audience can inspect the evidence behind both decisions.
