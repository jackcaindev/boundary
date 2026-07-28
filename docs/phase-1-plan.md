# Boundary Phase 1 Plan

## Planning Intent

Phase 1 is a one-week, production-shaped vertical slice that must prove Boundary’s complete active reliability loop with one bundled sample agent and one controlled tool-timeout fault. This plan deliberately does not settle detailed architecture, schemas, APIs, or application structure; those require review before implementation.

## Repository Facts

### State before the original planning task

The original inspection on 2026-07-27 found a Git repository on `main` with no commits. `README.md` was the only file outside `.git/` and was untracked; no `docs/` directory existed.

### Current state

As inspected before this review on 2026-07-27:

- The repository root is `/Users/jackcain/Dev/boundary`.
- It is a Git repository on branch `main`, tracking `origin/main`.
- The working tree was clean at the start of this review.
- `README.md`, `docs/product-spec.md`, and `docs/phase-1-plan.md` are tracked.
- There is no repository-local `AGENTS.md`.
- There are no dependency or package manifests.
- There are no application source directories or application files.
- There are no configuration or environment example files.
- There are no tests or test configuration.
- There are no Dockerfiles or Docker Compose files.
- There are no database migrations or schemas.
- There are no CI workflows.

These facts describe the current repository inventory, not future requirements.

## Assumptions

The following are inputs or recommendations, not repository facts:

- The primary user is an AI engineer preparing a tool-using agent or workflow for production.
- Phase 1 will use a minimal tool-using LangGraph sample agent with distinct, inspectable vulnerable and fixed version identities.
- The single Phase 1 fault is a deterministic tool timeout.
- Phase 1 should use a Python backend, FastAPI, Pydantic v2, PostgreSQL, React, Vite, TypeScript, and Docker Compose unless the pre-implementation design finds a concrete conflict.
- PostgreSQL is intended to be the authoritative store, but its exact Phase 1 persistence boundary is undecided.
- OpenTelemetry-compatible event and trace concepts are preferred, but no exporter or trace backend is yet required.
- AWS is an eventual target, not a Phase 1 deployment requirement.
- The system under test and all content it returns are untrusted.
- Boundary should make diagnosis and release decisions deterministically from versioned evidence and assertions.
- Boundary’s Phase 1 campaign execution, assertion evaluation, localization, and policy result should use plain deterministic service code.
- Automated tests should use a deterministic fake model; at least one configured end-to-end manual portfolio demonstration must use a real model call. Mocked model behavior validates contracts and workflow behavior, not model quality.
- Redis is unnecessary unless implementation produces a measured coordination or delivery requirement that PostgreSQL and the initial process model cannot satisfy.
- LangGraph is not justified in Boundary’s control plane. Its use in the bundled system under test does not justify control-plane adoption.
- Multiple agents or distributed workers are unnecessary for the bundled Phase 1 execution.
- A single local engineer and a single campaign at a time are sufficient for the first demonstration.
- “One week” means five focused implementation days with scope protected; staffing and exact calendar availability are not yet known.

## Highest-Risk Product Assumptions

1. **Active fault injection is a priority problem.** Engineers may value better evaluations or trace interpretation more than controlled failure campaigns.
2. **First-unsafe-divergence localization is valuable and understandable.** Users must distinguish an intended injection from the tested agent’s first unsafe behavior and later symptoms.
3. **An external contract can provide enough evidence.** Instrumentation burden may outweigh the perceived value, or agent-emitted evidence may be too untrustworthy.
4. **The before-and-after regression demonstration proves meaningful value.** A bundled sample can look staged unless its unsafe and corrected behavior resemble real agent failures.
5. **An explicit release policy improves decisions.** Engineers must prefer a narrowly scoped, repeatable gate to discretionary interpretation of traces.
6. **One scenario is sufficient to prove the thesis.** It can prove the mechanism, but not breadth; product messaging must not overstate the result.
7. **The provisional timeout policy is representative.** If “at most one bounded retry, then an explicit degraded result within budget” is contrived, correct localization and policy results will still be unpersuasive.

These assumptions should be tested through the working slice and target-user review, not buried under broader feature work.

## Highest-Risk Technical Assumptions

1. **Tool-timeout injection can be deterministic.** Boundary must control the trigger and bound timing without depending on an unreliable external service.
2. **Event ordering can be authoritative enough for diagnosis.** Wall-clock timestamps alone may be insufficient; ordering semantics must survive retries, late events, and duplicates.
3. **Boundary can prove the intended fault occurred.** Agent-reported timeout data cannot be the sole authority for Boundary-owned injection.
4. **Events can distinguish injection, divergence, and symptoms.** The evidence model must prove the tool timeout was injected, show the first disallowed retry decision, and classify later budget or terminal failure separately.
5. **The tested-agent contract can be versioned and validated.** Malformed, missing, duplicated, or fabricated input must not silently corrupt campaign results.
6. **The same regression scenario is meaningfully rerunnable.** The fixed run must hold inputs, injection, expected behavior, and policy constant while varying only the sample-agent behavior.
7. **The release result can be fully deterministic.** Assertion evaluation and aggregation must not depend on an LLM explanation.
8. **Local execution can be repeatable.** Process scheduling and any model dependency must not make the one-week demo flaky.
9. **PostgreSQL and a simple process model are sufficient.** This remains likely for Phase 1 but must be confirmed against run-state and event-write requirements.

## Phase 1 Localization and Policy Semantics

Phase 1 uses three distinct evidence concepts:

- **`injection_boundary`:** tool execution, where Boundary deliberately applies the timeout.
- **`first_unsafe_divergence`:** the retry/control decision that requests the first retry beyond the single permitted bounded retry.
- **`downstream_symptoms`:** later consequences, including budget exhaustion or an incorrect terminal state.

The first failing boundary is only the boundary containing `first_unsafe_divergence`; the injected timeout is not diagnosed as a system-under-test defect.

The expected safe behavior is at most one bounded retry followed by an explicit degraded terminal result within the run budget. Policy evaluation must return exactly one of:

- **`PASS`:** complete, valid evidence proves all assertions for this scenario policy.
- **`FAIL`:** complete, valid evidence proves at least one gating assertion failed.
- **`INCOMPLETE`:** required evidence is missing or the run did not reach enough valid evidence for evaluation.
- **`INVALID`:** evidence is contradictory, untrusted in an authoritative field, or incompatible with the contract, scenario, or policy version.
- **`EXECUTION_ERROR`:** Boundary could not execute or evaluate the run because of an operational failure.

Only `PASS` and `FAIL` are assertion verdicts. The other states must never be coerced into either, and `PASS` means only that the tested-agent version passes this scenario policy.

## Regression-Case Semantics

After the vulnerable injected run returns `FAIL`, Boundary materializes an immutable regression-case artifact with a stable `regression_case_id`. The artifact contains or immutably references:

- source run identity;
- contract version;
- scenario version;
- tested input;
- fault configuration;
- expected-behavior assertions;
- policy version;
- original tested-agent version;
- supporting evidence references.

The fixed-agent run starts from this saved artifact. Boundary must verify that the contract and scenario versions, tested input, fault configuration, assertions, and policy remain unchanged while the tested-agent version changes. This section defines required behavior and evidence, not storage or API design.

## Delivery Gates and One-Week Cut Line

### Core mechanism checkpoint

This checkpoint proves the headless mechanism:

```text
normal control execution
→ vulnerable injected execution
→ Boundary-owned injection proof
→ ordered evidence
→ first unsafe divergence
→ deterministic scoped policy FAIL
→ immutable regression case with stable regression_case_id
→ narrow agent-version fix
→ fixed run started from the saved regression case
→ unchanged regression inputs, fault, assertions, and policy verified
→ deterministic scoped policy PASS
```

It may temporarily use documented ephemeral persistence or direct local process commands. It is an engineering checkpoint, not the completed Phase 1 portfolio milestone.

### Phase 1 portfolio complete

Phase 1 portfolio completion requires:

- PostgreSQL as the authoritative run, event, regression-case, analysis, and policy-result store;
- Docker Compose as the documented clean-start local workflow;
- the minimal inspectable UI;
- deterministic automated tests using a fake model;
- at least one successful configured manual demonstration using a real model call;
- repeated vulnerable `FAIL` and fixed `PASS` verification;
- immutable regression-case generation and rerun from the saved artifact.

If the real-model demonstration, PostgreSQL workflow, or Docker Compose workflow is missing, Phase 1 is incomplete; the omission cannot be converted into milestone debt.

### Must ship

The must-ship path is:

```text
normal control execution
→ vulnerable injected execution
→ Boundary-owned injection proof
→ ordered evidence
→ first unsafe divergence
→ deterministic scoped policy FAIL
→ immutable regression case with stable regression_case_id
→ narrow agent-version fix
→ fixed run started from the saved regression case
→ unchanged regression inputs, fault, assertions, and policy verified
→ deterministic scoped policy PASS
→ minimal inspectable UI
```

It includes the versioned contract and scenario, distinct inspectable agent versions, all five policy-result states, PostgreSQL authority, the Docker Compose clean-start path, deterministic automated execution with a fake model, and at least one configured successful real-model demonstration. The headless mechanism checkpoint must pass before substantial frontend work, but all portfolio-completion requirements remain must ship.

### Should ship

- Initial target-user timing and comprehension observations.
- Additional developer ergonomics beyond the documented clean-start workflow.

### Stretch

- Frontend polish beyond the minimal inspectable run-details view.
- Automated collection or presentation of workflow-value metrics.
- OpenTelemetry export.
- Polished real-model onboarding beyond the minimum configuration needed for the required demonstration.
- Additional UI comparison views between vulnerable and fixed runs.

Stretch work begins only after the must-ship headless loop and minimal inspectable UI pass end to end.

## Workflow-Value Validation

There is no measured baseline yet. Phase 1 should treat these as hypotheses requiring target-user comparison with manual trace inspection:

- evidence-backed localization within five minutes of run completion;
- rerunning an existing regression within two minutes excluding execution, and producing the initial regression within 15 minutes;
- instrumenting a comparable local agent to the minimum contract in under two hours;
- at least 80% of reviewers correctly distinguishing injection, divergence, symptoms, and scoped verdict after one walkthrough;
- faster localization and regression rerun than manual trace inspection without reducing correct-localization rate.

The milestone should record timings, completion and correctness rates, instrumentation effort, and qualitative feedback. These targets are not acceptance claims until measured with target users.

## Phase 1 Implementation Sequence

The sequence keeps a runnable path throughout the week and makes the headless proof work before substantial frontend polish. Each task should end with executable evidence or a reviewed contract, not an isolated horizontal layer. “Day” assignments are targets and may overlap; stretch scope should be removed before correctness or trust guarantees are weakened.

### Day 1 — Task 1: Repository and development skeleton

- **Observable goal:** A new developer can run the minimal headless service path and test commands from documented local instructions.
- **Required inputs:** Reviewed technology choices, supported local tool versions, service boundaries, and basic developer commands.
- **Expected output:** Minimal repository structure, health path, test harness, and local process setup necessary to begin the headless vertical slice; PostgreSQL connectivity and Docker Compose should be added here when they do not block that path.
- **Main risk:** Spending the day on platform polish or speculative abstractions instead of enabling the first execution.
- **Verification required:** Clean-start setup, backend health check, one passing smoke test, and database or documented temporary persistence verification if implemented.
- **Must not include yet:** A frontend shell before the headless path needs it, Kubernetes, cloud infrastructure, authentication, Redis, distributed workers, generic plugin systems, full design systems, or unused service placeholders.

### Day 1 — Task 2: Versioned system-under-test contract

- **Observable goal:** Boundary can invoke one conforming test target and reject invalid or incompatible inputs and events.
- **Required inputs:** Reviewed decisions for invocation, event transport and ordering, trust boundaries, boundary identities, and contract versioning.
- **Expected output:** A minimal versioned contract and contract tests covering invocation, run correlation, event envelope, completion, and errors.
- **Main risk:** Letting the bundled sample’s implementation details become an accidental universal agent API.
- **Verification required:** Contract conformance tests for valid input plus malformed, unsupported-version, missing-event, duplicate-event, and incorrect-run-identity cases.
- **Must not include yet:** Arbitrary framework adapters, a public SDK suite, generalized trace ingestion, framework discovery, or compatibility promises beyond Phase 1.

### Day 2 — Task 3: Bundled vulnerable sample agent

- **Observable goal:** A minimal tool-using LangGraph agent performs the workflow and exhibits a stable, inspectable disallowed retry decision after a tool timeout.
- **Required inputs:** The versioned contract, one deterministic tool behavior, and a reviewed definition of expected safe timeout handling.
- **Expected output:** Distinct vulnerable and fixed tested-agent versions with an inspectable narrow source difference; automated tests use a deterministic fake model, while the required configured manual portfolio demonstration uses a real model call.
- **Main risk:** Creating a toy failure whose diagnosis is predetermined by fixtures rather than observed behavior.
- **Verification required:** A normal control execution succeeds; an injected execution requests the first disallowed retry reliably; the two versions have distinct identities; source review proves the narrow change; deterministic tests validate contracts and workflow behavior but make no model-quality claim.
- **Must not include yet:** LangGraph in Boundary’s control plane, multiple agents, real-model dependence in automated tests, unrelated tools, multiple faults, automatic code modification, or framework-general abstractions.

### Day 2 — Task 4: Tool-timeout fault injection

- **Observable goal:** Boundary causes a timeout at the declared tool boundary using a deterministic trigger and records independent proof that it did so.
- **Required inputs:** Reviewed injection location, timeout semantics, trigger conditions, identifiers, and authoritative evidence ownership.
- **Expected output:** A single bounded timeout injector integrated into the real sample execution path, plus an injection evidence record.
- **Main risk:** Mistaking a natural delay, client timeout, or agent-reported error for proof of Boundary’s intended injection.
- **Verification required:** Positive test proving the fault is applied once at the declared boundary; negative control proving no timeout without injection; repeatability test across multiple runs; tests for wrong target and duplicate trigger.
- **Must not include yet:** A fault DSL, broad fault catalog, network chaos system, arbitrary latency profiles, hosted injectors, or a marketplace.

### Day 3 — Task 5: Ordered event collection

- **Observable goal:** Each run produces an immutable, run-scoped ordered event sequence sufficient to distinguish injection, the first unsafe retry decision, and later symptoms.
- **Required inputs:** Reviewed event contract, ordering rule, authority boundaries, expected lifecycle, and minimum evidence fields.
- **Expected output:** Persistent events for invocation, agent/workflow transitions, tool attempt, Boundary-owned injected timeout, each retry/control decision, and terminal outcome.
- **Main risk:** Relying on arrival time or system-under-test claims in a way that makes the earliest divergence ambiguous.
- **Verification required:** Tests for deterministic ordering, concurrent or equal timestamps, duplicates, late events, missing required events, invalid run identities, and payload size or content constraints.
- **Must not include yet:** A general telemetry lake, arbitrary OpenTelemetry backend, log aggregation, production trace import, full-text search, or long-term retention infrastructure.

### Day 3 — Task 6: Deterministic failure localization

- **Observable goal:** Boundary identifies tool execution as the injection boundary, the first disallowed retry/control decision as the first unsafe divergence, and later budget or terminal failures as symptoms.
- **Required inputs:** Reviewed expected-behavior representation, boundary taxonomy, ordering rule, and localization algorithm.
- **Expected output:** A deterministic analysis result with separate `injection_boundary`, `first_unsafe_divergence`, and `downstream_symptoms` fields, plus failed assertion and supporting event references.
- **Main risk:** Encoding a sample-specific answer or presenting correlation as stronger causality than the evidence supports.
- **Verification required:** Table-driven tests for a permitted retry, first disallowed retry, budget and terminal symptoms, missing or contradictory evidence, ambiguous order, and multiple failed assertions; no test may label the injected timeout itself as the tested-agent defect; repeated analysis of identical evidence must produce identical output.
- **Must not include yet:** LLM root-cause generation, generic anomaly detection, probabilistic diagnosis, broad boundary ontologies, or remediation advice.

### Day 3 — Task 7: Release-policy evaluation

- **Observable goal:** The vulnerable run returns `FAIL` for this explicit scenario policy solely because named, versioned assertions fail, while non-verdict evidence states remain distinct.
- **Required inputs:** Reviewed assertion representation, policy aggregation rule, incomplete-run behavior, and result vocabulary.
- **Expected output:** One versioned Phase 1 policy, per-assertion results, evidence links, and exactly one aggregate state: `PASS`, `FAIL`, `INCOMPLETE`, `INVALID`, or `EXECUTION_ERROR`.
- **Main risk:** A simplistic pass/fail label that obscures missing evidence or implies general production readiness.
- **Verification required:** Tests for all five states, including missing, contradictory, untrusted, incompatible, and operational-error cases; stable reevaluation; no free-form model result can change the outcome; UI copy says “passes this scenario policy.”
- **Must not include yet:** A general policy language, drag-and-drop editor, policy marketplace, organization policy management, waivers, approvals, or CI integration.

### Day 4 — Task 8: Regression-case materialization and headless rerun

- **Observable goal:** A vulnerable `FAIL` materializes an immutable regression case, and a fixed-agent run started from that artifact returns `PASS`.
- **Required inputs:** Integrated headless campaign path, failed source run, versioned sample agents, regression-case materialization rules, deterministic analysis, and policy evaluation.
- **Expected output:** A stable `regression_case_id`; an artifact containing or immutably referencing source run identity, contract version, scenario version, tested input, fault configuration, expected-behavior assertions, policy version, original tested-agent version, and supporting evidence; plus a fixed run started from that artifact.
- **Main risk:** Treating an existing scenario as the generated regression case, allowing artifact inputs to drift, or substituting stored fixtures for live behavior.
- **Verification required:** The vulnerable run returns `FAIL` before materialization; the artifact is immutable and provenance-complete; the fixed run names its `regression_case_id`; Boundary verifies that contract, scenario, tested input, fault configuration, assertions, and policy are unchanged while only the tested-agent version changes; the vulnerable version returns `FAIL` and fixed version returns `PASS` repeatedly.
- **Must not include yet:** Automatic patch generation, arbitrary user repositories, historical analytics, statistical comparisons, or multi-scenario campaigns.

### Day 4 — Task 9: Minimal campaign and run-details UI

- **Observable goal:** An engineer can start the bundled campaign and understand the resulting evidence, localization, and policy result without reading database records.
- **Required inputs:** Passing headless before-and-after loop and reviewed information hierarchy for campaign, run, evidence, comparison, and verdict.
- **Expected output:** Minimal campaign start and regression-rerun controls plus a run-details view showing tested-agent version, `regression_case_id`, source-run provenance, injection boundary and proof, ordered events, first unsafe divergence, downstream symptoms, expected-versus-observed assertions, invariance verification, and one of the five policy-result states.
- **Main risk:** Expanding into a general observability dashboard or hiding deterministic evidence behind decorative summaries.
- **Verification required:** UI tests cover loading, running, `PASS`, `FAIL`, `INCOMPLETE`, `INVALID`, and `EXECUTION_ERROR`; manual inspection confirms every displayed claim maps to recorded evidence.
- **Must not include yet:** General dashboards, trace search, analytics, custom scenario builders, policy editors, collaboration, authentication, complex visualizations, or design-system work unrelated to the demo.

### Day 5 — Task 10: Containerization and final verification

- **Observable goal:** A new developer can run the complete demonstration from a clean checkout with a small documented command sequence.
- **Required inputs:** Stable integrated slice, finalized runtime dependencies, database setup, and exact verification commands.
- **Expected output:** PostgreSQL-backed services and a documented Docker Compose clean-start workflow that repeat the complete portfolio demonstration.
- **Main risk:** Masking nondeterminism with retries or spending the final day on production deployment concerns.
- **Verification required:** Clean Docker Compose build and startup; PostgreSQL initialization and authoritative persistence of runs, events, regression cases, analyses, and policy results; automated tests with the fake model; at least 10 consecutive end-to-end demo executions with at least 9 successes; at least one successful configured end-to-end manual portfolio demonstration using a real model call; immutable regression-case generation and rerun; inspection of version identity and source diff; dependency and scope review.
- **Must not include yet:** Production AWS resources, Kubernetes, autoscaling, remote workers, multi-region concerns, Redis, enterprise security, or CI/CD beyond what is strictly necessary to verify locally.

### Day 5 — Milestone review

At the end of Day 5, review the acceptance criteria in `docs/product-spec.md`, record failed or flaky criteria, and demonstrate the slice to target AI engineers if available. A missing trust property, nondeterministic verdict, immutable regression rerun, PostgreSQL workflow, Docker Compose workflow, or successful configured real-model demonstration means Phase 1 is incomplete; it must not be relabeled as future polish or milestone debt.

## Decisions Required Before Implementation

The next planning task should evaluate and recommend these decisions without expanding into a generic platform design.

### 1. How Boundary invokes the tested agent

- **Why it matters:** Invocation determines isolation, reproducibility, fault reach, versioning, and how closely the bundled slice resembles a later external integration.
- **Options to evaluate:** HTTP endpoint, local subprocess with a versioned protocol, or in-process adapter.
- **Review focus:** Prefer the smallest option that preserves a credible external trust boundary and can later be replaced.

### 2. How ordered events are received

- **Why it matters:** Localization depends on a trustworthy sequence across agent, tool, injector, and terminal events.
- **Options to evaluate:** Events returned with the final response, streamed to Boundary, or sent to a run-scoped ingestion endpoint; Boundary-assigned sequence versus producer-local sequence plus reconciliation.
- **Review focus:** Define authoritative order, late-event behavior, duplicate handling, and what happens when evidence is incomplete.

### 3. Where the timeout fault is injected

- **Why it matters:** The injection location determines whether Boundary can target and prove the named boundary rather than merely simulate an error response.
- **Options to evaluate:** Boundary-owned tool stub/proxy, invocation wrapper around a real tool client, or instrumentation hook in the sample adapter.
- **Review focus:** Isolation from system-under-test claims, determinism, realism, and future replaceability.

### 4. How Boundary proves the fault occurred

- **Why it matters:** A diagnosis is not trustworthy if the same untrusted target both experiences and attests to the injected fault.
- **Options to evaluate:** Boundary-owned injection ledger/event, cryptographically or structurally correlated request and injection identifiers, and negative control evidence.
- **Review focus:** Minimum independent evidence needed to establish target, timing/order, trigger, and single application.

### 5. How expected behavior is represented

- **Why it matters:** Expected behavior must be explicit, versioned, rerunnable, and understandable without becoming a general specification language.
- **Options to evaluate:** A narrow typed scenario document, code-defined Phase 1 assertions, or a small declarative assertion set.
- **Review focus:** Encode the provisional rule—at most one bounded retry followed by an explicit degraded terminal result within budget—while preserving evidence references and version identity.

### 6. How first-unsafe-divergence localization is encoded

- **Why it matters:** The three-concept model is settled, but the deterministic algorithm and evidence requirements are not.
- **Options to evaluate:** Assertion evaluation over ordered retry-decision events, expected state-machine transition comparison, or a hybrid with explicit precedence rules.
- **Review focus:** Always return separate injection, divergence, and symptom evidence; define ordering, missing evidence, concurrent events, boundary taxonomy, and ambiguity without diagnosing the injected timeout as a defect.

### 7. How release assertions and results are represented

- **Why it matters:** The policy must be explicit and stable across both runs, and the settled five-state vocabulary needs deterministic transition and aggregation rules.
- **Options to evaluate:** A fixed typed policy object, a minimal declarative list of assertions, or code-defined checks with a serialized version.
- **Review focus:** Define exact mappings to `PASS`, `FAIL`, `INCOMPLETE`, `INVALID`, and `EXECUTION_ERROR`, versioning, evidence links, and “passes this scenario policy” UI language.

### 8. How the sample-agent vulnerability and fix are versioned

- **Why it matters:** The vulnerable behavior is settled as requesting more retries than permitted, but the version identity and narrow source-change mechanism must be auditable.
- **Options to evaluate:** Separate versioned source directories, immutable build/version identifiers from commits, or another simple representation that makes the exact source difference inspectable.
- **Review focus:** Only the tested-agent version changes between runs; the fixed version performs at most one bounded retry then returns an explicit degraded result within budget; Boundary never modifies source automatically.

### 9. What must be Boundary-owned versus system-under-test-provided

- **Why it matters:** The system under test and its returned content are untrusted, while policy evidence must remain auditable.
- **Options to evaluate:** Boundary ownership of run identity, injection record, receipt order, scenario, assertions, and verdict; limited target ownership of internal transition events.
- **Review focus:** Validation, payload limits, sanitization, authoritative fields, conflict handling, and display safety.

### 10. How a failed run becomes an immutable regression case

- **Why it matters:** Generating a reproducible regression case is part of the product claim; rerunning a preexisting scenario alone does not prove it.
- **Options to evaluate:** Materializing a Boundary-owned immutable artifact that embeds required values or immutably references separately versioned records, with a stable Boundary-assigned `regression_case_id`.
- **Review focus:** Define when materialization is permitted, identifier stability, provenance and evidence requirements, immutability, how a rerun declares its source regression case, and how Boundary proves invariant fields did not change while the tested-agent version did. Do not design the database schema or API in this ADR.

## Reversible Decisions

The following choices should be made for Phase 1 convenience behind narrow boundaries and should not be treated as platform commitments:

- Exact React component hierarchy, routing layout, and styling approach.
- Exact Python module layout beyond clear separation of execution, evidence, analysis, and policy concerns.
- Initial persistence library or ORM, provided PostgreSQL remains authoritative and domain behavior is not hidden in ORM hooks.
- Migration tooling.
- Whether the bundled sample runs in-process, as a subprocess, or as a local container after the external contract is preserved.
- Exact event transport after event semantics and versioning are fixed.
- Trace export backend; Phase 1 may require none.
- Possible future LangGraph use in Boundary’s control plane, only if a later independently demonstrated workflow-state requirement justifies it.
- Worker and queue infrastructure.
- Redis or another coordination service.
- Cloud deployment topology and AWS service selection.
- Framework-specific adapters and SDK packaging.
- Frontend state-management and component libraries.
- Long-term storage, analytics, and retention design.

Replaceability does not mean postponing every choice. It means avoiding public compatibility promises and abstractions before the Phase 1 evidence identifies stable seams.

## Deferred Features and Dependencies

| Deferred item | Why it is deferred in Phase 1 | Evidence that could justify it later |
| --- | --- | --- |
| Redis | A single local campaign path has no demonstrated queue, cache, lock, or delivery requirement that PostgreSQL and the process model cannot meet. | Measured contention, delivery semantics, latency, or coordination needs with a clear owner and failure model. |
| LangGraph in Boundary’s control plane | The control plane must use plain deterministic service code; the bundled LangGraph system under test does not create a control-plane requirement. | A later independently demonstrated need for campaign branching, resumability, checkpointing, or explicit workflow state that is materially simpler and safer with it. |
| Multiple agents or distributed workers | Phase 1 has one bounded sample execution and no independently justified roles or isolation domains. | Different tools, permissions, context, fault capabilities, isolation, scaling, or independently evaluated responsibilities. |
| Additional fault scenarios | Breadth does not strengthen the proof until the complete timeout loop works and is trusted. | User research identifies another high-frequency, high-cost failure and the first injector design proves extensible. |
| Parallel campaign execution | Concurrency is unnecessary for the single-scenario milestone. | Real campaign duration or CI throughput measurements show serial execution is unacceptable. |
| CI and GitHub integration | The local policy result must first be trustworthy. | Design partners repeatedly complete the local loop and request enforcement in pull requests or release pipelines. |
| Production trace import and incident replay | Phase 1 creates a controlled scenario rather than ingesting arbitrary production evidence. | Users provide incidents they cannot economically reproduce and a safe, versioned import contract is defined. |
| Framework-specific adapters | There is no validated first external framework integration. | Target-user demand concentrates on a framework and the minimal contract proves insufficient without an adapter. |
| General OpenTelemetry backend | Compatible concepts do not require operating a telemetry platform. | Export, interoperability, or retention requirements cannot be met by the authoritative Phase 1 event store. |
| Security and prompt-injection testing | These introduce different threat models and assertions from timeout reliability. | User demand and a reviewed security-testing model justify a separate slice. |
| Hosted and customer-VPC workers | Remote execution adds isolation, secrets, networking, tenancy, and operational burden. | External targets cannot be tested acceptably from a local runner and customers validate deployment constraints. |
| Authentication, RBAC, multi-tenancy, organizations, and billing | Phase 1 is a local single-user proof. | Team adoption or hosted use creates concrete identity, authorization, isolation, and commercial requirements. |
| Kubernetes and production AWS infrastructure | There is no production workload or topology to size. | A hosted product, service-level objective, and measured workload justify deployment design. |
| Generic policy language and policy editor | One explicit policy is sufficient to test deterministic gating. | Multiple validated scenarios require user-authored combinations that cannot be served by typed policy versions. |
| General observability UI, analytics, and trace search | These risk displacing the active campaign workflow. | Users need cross-run investigation after repeatedly using injection, localization, and gating. |
| Automatic source-code modification | The milestone requires an inspectable narrow fix, not automated remediation. | Users trust diagnoses and explicitly request a separately evaluated remediation workflow with safety controls. |
| AI-generated root-cause analysis | Deterministic evidence is sufficient for Phase 1 and must remain authoritative. | Deterministic results are established and users need optional summaries that cite, never override, the evidence. |
| Plugin marketplace and visual workflow builder | Neither contributes to the one-scenario proof. | A validated ecosystem or nontechnical authoring need emerges after stable extension contracts exist. |

No deferred item is a roadmap commitment.

## Phase 1 Scope Recommendation

The authoritative cut line is the two delivery gates plus the Must ship / Should ship / Stretch division above. Temporary persistence or direct-process simplifications are permitted only for the core mechanism checkpoint. Phase 1 portfolio completion requires PostgreSQL, Docker Compose, the minimal UI, fake-model automated tests, a successful configured real-model demonstration, repeated `FAIL`/`PASS` verification, and immutable regression-case generation and rerun. If any is missing, report Phase 1 as incomplete.

## Exit Criteria and Handoff

Phase 1 is ready for review when every acceptance criterion in `docs/product-spec.md` has a linked verification result or an explicitly recorded failure. The handoff should include:

- exact clean-start and demo commands;
- automated test results;
- run identifiers for a vulnerable `FAIL` and fixed `PASS`;
- the stable `regression_case_id` linking them;
- the version identities of scenario, contract, tested agents, and policy;
- evidence that the regression case contains or immutably references every required input and provenance field;
- verification that regression inputs, fault configuration, assertions, and policy did not change while the tested-agent version did;
- evidence references supporting injection, localization, symptoms, and both verdicts;
- repeated-run reliability results;
- and target-user feedback or a scheduled plan to collect it.

The next Codex task should produce the reviewed system-under-test contract and fault-injection ADRs, focused on the ten decisions above. It should settle invocation, ordered evidence, Boundary-owned injection proof, trust boundaries, version identity, localization inputs, five-state policy semantics, and regression-case materialization and invariance evidence while documenting alternatives and verification strategy. Broader architecture, data models, API schemas, and skeleton creation should follow only after those ADR decisions are accepted.
