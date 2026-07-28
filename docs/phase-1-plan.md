# Boundary Phase 1 Plan

## Planning Intent

Phase 1 is a one-week, production-shaped vertical slice that must prove Boundary’s complete active reliability loop with one bundled sample agent and one controlled tool-timeout fault. This plan deliberately does not settle detailed architecture, schemas, APIs, or application structure; those require review before implementation.

## Repository Facts

As inspected on 2026-07-27:

- The repository root is `/Users/jackcain/Dev/boundary`.
- It is a Git repository on branch `main`.
- The branch has no commits.
- `README.md` is the only file outside `.git/` and is currently untracked.
- The README contains one product statement:

  > Given an instrumented agent run, Boundary can inject a controlled production failure, identify the first failing boundary, generate a reproducible regression case, and determine whether the agent meets a release policy.

- Before this planning task, there was no `docs/` directory.
- There is no repository-local `AGENTS.md`.
- There are no dependency or package manifests.
- There are no application source directories or application files.
- There are no configuration or environment example files.
- There are no tests or test configuration.
- There are no Dockerfiles or Docker Compose files.
- There are no database migrations or schemas.
- There are no CI workflows.

The absence statements above come from the current repository inventory. They do not imply that these assets are unnecessary; only that they do not exist yet.

## Assumptions

The following are inputs or recommendations, not repository facts:

- The primary user is an AI engineer preparing a tool-using agent or workflow for production.
- Phase 1 will use a bundled vulnerable sample agent and a narrowly fixed variant.
- The single Phase 1 fault is a deterministic tool timeout.
- Phase 1 should use a Python backend, FastAPI, Pydantic v2, PostgreSQL, React, Vite, TypeScript, and Docker Compose unless the pre-implementation design finds a concrete conflict.
- PostgreSQL is intended to be the authoritative store, but its exact Phase 1 persistence boundary is undecided.
- OpenTelemetry-compatible event and trace concepts are preferred, but no exporter or trace backend is yet required.
- AWS is an eventual target, not a Phase 1 deployment requirement.
- The system under test and all content it returns are untrusted.
- Boundary should make diagnosis and release decisions deterministically from versioned evidence and assertions.
- Redis is unnecessary unless implementation produces a measured coordination or delivery requirement that PostgreSQL and the initial process model cannot satisfy.
- LangGraph is unnecessary unless explicit workflow state in campaign execution demonstrates value beyond simpler application control flow.
- Multiple agents or distributed workers are unnecessary for the bundled Phase 1 execution.
- A single local engineer and a single campaign at a time are sufficient for the first demonstration.
- “One week” means five focused implementation days with scope protected; staffing and exact calendar availability are not yet known.

## Highest-Risk Product Assumptions

1. **Active fault injection is a priority problem.** Engineers may value better evaluations or trace interpretation more than controlled failure campaigns.
2. **First-failing-boundary localization is valuable and understandable.** Users must see it as a useful diagnostic rule rather than an overconfident causal claim.
3. **An external contract can provide enough evidence.** Instrumentation burden may outweigh the perceived value, or agent-emitted evidence may be too untrustworthy.
4. **The before-and-after regression demonstration proves meaningful value.** A bundled sample can look staged unless its unsafe and corrected behavior resemble real agent failures.
5. **An explicit release policy improves decisions.** Engineers must prefer a narrowly scoped, repeatable gate to discretionary interpretation of traces.
6. **One scenario is sufficient to prove the thesis.** It can prove the mechanism, but not breadth; product messaging must not overstate the result.
7. **The chosen safe timeout behavior is representative.** If the assertion is contrived, a correct localization and pass/fail result will still be unpersuasive.

These assumptions should be tested through the working slice and target-user review, not buried under broader feature work.

## Highest-Risk Technical Assumptions

1. **Tool-timeout injection can be deterministic.** Boundary must control the trigger and bound timing without depending on an unreliable external service.
2. **Event ordering can be authoritative enough for diagnosis.** Wall-clock timestamps alone may be insufficient; ordering semantics must survive retries, late events, and duplicates.
3. **Boundary can prove the intended fault occurred.** Agent-reported timeout data cannot be the sole authority for Boundary-owned injection.
4. **Events can distinguish primary failure from symptoms.** The evidence model must show that an early unsafe response precedes later retry exhaustion or terminal failure.
5. **The tested-agent contract can be versioned and validated.** Malformed, missing, duplicated, or fabricated input must not silently corrupt campaign results.
6. **The same regression scenario is meaningfully rerunnable.** The fixed run must hold inputs, injection, expected behavior, and policy constant while varying only the sample-agent behavior.
7. **The release result can be fully deterministic.** Assertion evaluation and aggregation must not depend on an LLM explanation.
8. **Local execution can be repeatable.** Process scheduling and any model dependency must not make the one-week demo flaky.
9. **PostgreSQL and a simple process model are sufficient.** This remains likely for Phase 1 but must be confirmed against run-state and event-write requirements.

## Phase 1 Implementation Sequence

The sequence keeps a runnable path throughout the week. Each task should end with executable evidence or a reviewed contract, not an isolated horizontal layer. “Day” assignments are targets and may overlap; scope should be reduced before correctness or trust guarantees are weakened.

### Day 1 — Task 1: Repository and development skeleton

- **Observable goal:** A new developer can start the minimal backend, frontend, authoritative database, and test commands from documented local instructions.
- **Required inputs:** Reviewed technology choices, supported local tool versions, service boundaries, and basic developer commands.
- **Expected output:** Minimal repository structure, health paths, database connectivity, frontend shell, test harnesses, and local orchestration necessary for the vertical slice.
- **Main risk:** Spending the day on platform polish or speculative abstractions instead of enabling the first execution.
- **Verification required:** Clean-start setup, backend and frontend health checks, database connectivity, and one passing smoke test in each implemented test layer.
- **Must not include yet:** Kubernetes, cloud infrastructure, authentication, Redis, distributed workers, generic plugin systems, full design systems, or unused service placeholders.

### Day 1 — Task 2: Versioned system-under-test contract

- **Observable goal:** Boundary can invoke one conforming test target and reject invalid or incompatible inputs and events.
- **Required inputs:** Reviewed decisions for invocation, event transport and ordering, trust boundaries, boundary identities, and contract versioning.
- **Expected output:** A minimal versioned contract and contract tests covering invocation, run correlation, event envelope, completion, and errors.
- **Main risk:** Letting the bundled sample’s implementation details become an accidental universal agent API.
- **Verification required:** Contract conformance tests for valid input plus malformed, unsupported-version, missing-event, duplicate-event, and incorrect-run-identity cases.
- **Must not include yet:** Arbitrary framework adapters, a public SDK suite, generalized trace ingestion, framework discovery, or compatibility promises beyond Phase 1.

### Day 2 — Task 3: Bundled vulnerable sample agent

- **Observable goal:** The real sample agent performs a tool-using workflow and exhibits a stable, inspectable unsafe response to a tool timeout.
- **Required inputs:** The versioned contract, one deterministic tool behavior, and a reviewed definition of expected safe timeout handling.
- **Expected output:** One bundled vulnerable variant and one narrowly fixed variant or patch path, with the fixed path held back from the initial campaign run.
- **Main risk:** Creating a toy failure whose diagnosis is predetermined by fixtures rather than observed behavior.
- **Verification required:** A control execution succeeds without injection; an injected execution reliably exposes the intended vulnerability; tests prove the two variants differ only in the narrow handling behavior.
- **Must not include yet:** Multiple agents, external model dependence unless strictly necessary, unrelated tools, multiple faults, automatic code modification, or framework-general abstractions.

### Day 2 — Task 4: Tool-timeout fault injection

- **Observable goal:** Boundary causes a timeout at the declared tool boundary using a deterministic trigger and records independent proof that it did so.
- **Required inputs:** Reviewed injection location, timeout semantics, trigger conditions, identifiers, and authoritative evidence ownership.
- **Expected output:** A single bounded timeout injector integrated into the real sample execution path, plus an injection evidence record.
- **Main risk:** Mistaking a natural delay, client timeout, or agent-reported error for proof of Boundary’s intended injection.
- **Verification required:** Positive test proving the fault is applied once at the declared boundary; negative control proving no timeout without injection; repeatability test across multiple runs; tests for wrong target and duplicate trigger.
- **Must not include yet:** A fault DSL, broad fault catalog, network chaos system, arbitrary latency profiles, hosted injectors, or a marketplace.

### Day 3 — Task 5: Ordered event collection

- **Observable goal:** Each run produces an immutable, run-scoped ordered event sequence sufficient to reconstruct the relevant execution.
- **Required inputs:** Reviewed event contract, ordering rule, authority boundaries, expected lifecycle, and minimum evidence fields.
- **Expected output:** Persistent events for invocation, agent/workflow transitions, tool attempt, injected timeout, retry or recovery, and terminal outcome.
- **Main risk:** Relying on arrival time or system-under-test claims in a way that makes the earliest divergence ambiguous.
- **Verification required:** Tests for deterministic ordering, concurrent or equal timestamps, duplicates, late events, missing required events, invalid run identities, and payload size or content constraints.
- **Must not include yet:** A general telemetry lake, arbitrary OpenTelemetry backend, log aggregation, production trace import, full-text search, or long-term retention infrastructure.

### Day 3 — Task 6: Deterministic failure localization

- **Observable goal:** Boundary compares expected with observed behavior, points to the earliest evidenced divergence, and labels later failures as symptoms.
- **Required inputs:** Reviewed expected-behavior representation, boundary taxonomy, ordering rule, and localization algorithm.
- **Expected output:** A deterministic analysis result containing the first failing boundary, failed assertion, supporting event references, and downstream symptom references.
- **Main risk:** Encoding a sample-specific answer or presenting correlation as stronger causality than the evidence supports.
- **Verification required:** Table-driven tests for expected pass, primary divergence, retry symptom, missing evidence, ambiguous order, and multiple failed assertions; repeated analysis of identical evidence must produce identical output.
- **Must not include yet:** LLM root-cause generation, generic anomaly detection, probabilistic diagnosis, broad boundary ontologies, or remediation advice.

### Day 3 — Task 7: Release-policy evaluation

- **Observable goal:** The vulnerable run fails an explicit policy solely because named, versioned assertions fail.
- **Required inputs:** Reviewed assertion representation, policy aggregation rule, incomplete-run behavior, and result vocabulary.
- **Expected output:** One versioned Phase 1 policy, per-assertion results, deterministic aggregate status, and evidence links.
- **Main risk:** A simplistic pass/fail label that obscures missing evidence or implies general production readiness.
- **Verification required:** Tests for pass, fail, incomplete or invalid evidence, policy-version mismatch, and stable reevaluation; no free-form model result can change the verdict.
- **Must not include yet:** A general policy language, drag-and-drop editor, policy marketplace, organization policy management, waivers, approvals, or CI integration.

### Day 4 — Task 8: Minimal campaign and run-details UI

- **Observable goal:** An engineer can start the bundled campaign and understand the resulting evidence, localization, and policy result without reading database records.
- **Required inputs:** Working end-to-end backend path and reviewed information hierarchy for campaign, run, evidence, comparison, and verdict.
- **Expected output:** Minimal campaign creation/start control and run-details view showing status, injection proof, ordered events, expected-versus-observed assertions, first failing boundary, symptoms, and release result.
- **Main risk:** Expanding into a general observability dashboard or hiding deterministic evidence behind decorative summaries.
- **Verification required:** UI tests for loading, running, failed, passed, invalid, and incomplete states; manual inspection confirms every displayed claim maps to recorded evidence.
- **Must not include yet:** General dashboards, trace search, analytics, custom scenario builders, policy editors, collaboration, authentication, complex visualizations, or design-system work unrelated to the demo.

### Day 4 — Task 9: Before-and-after regression demonstration

- **Observable goal:** The same saved scenario and policy fail the vulnerable variant and pass the narrowly fixed variant using two real executions.
- **Required inputs:** Integrated campaign path, versioned sample variants, reproducible scenario, deterministic analysis, and UI.
- **Expected output:** Two distinct auditable runs linked to one regression scenario, with materially different event evidence and opposite justified policy results.
- **Main risk:** Accidentally changing inputs or assertions between runs, or substituting stored fixtures for live behavior.
- **Verification required:** Automated end-to-end test and manual demo compare scenario version, inputs, injection settings, and policy version; only the declared sample variant changes; vulnerable fails and fixed passes repeatedly.
- **Must not include yet:** Automatic patch generation, arbitrary user repositories, historical analytics, statistical comparisons, or multi-scenario campaigns.

### Day 5 — Task 10: Containerization and final verification

- **Observable goal:** A new developer can run the complete demonstration from a clean checkout with a small documented command sequence.
- **Required inputs:** Stable integrated slice, finalized runtime dependencies, database setup, and exact verification commands.
- **Expected output:** Minimal Docker assets and local documentation needed to run the supported services and repeat the demo.
- **Main risk:** Masking nondeterminism with retries or spending the final day on production deployment concerns.
- **Verification required:** Clean build and startup; migrations or initialization; automated tests; at least 10 consecutive end-to-end demo executions with at least 9 successes; manual inspection of one vulnerable and one fixed run; dependency and scope review.
- **Must not include yet:** Production AWS resources, Kubernetes, autoscaling, remote workers, multi-region concerns, Redis, enterprise security, or CI/CD beyond what is strictly necessary to verify locally.

### Day 5 — Milestone review

At the end of Day 5, review the acceptance criteria in `docs/product-spec.md`, record failed or flaky criteria, and demonstrate the slice to target AI engineers if available. A missing trust property or nondeterministic verdict is a milestone failure; it should not be relabeled as future polish.

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
- **Review focus:** Express only the timeout safety expectations needed for Phase 1 while preserving evidence references and version identity.

### 6. How first-failing-boundary localization works

- **Why it matters:** This is the central product concept and must be deterministic, auditable, and careful about causal claims.
- **Options to evaluate:** Earliest failed assertion mapped to an event boundary, expected state-machine transition comparison, or a hybrid with explicit precedence rules.
- **Review focus:** Ordering, missing evidence, concurrent events, boundary taxonomy, ambiguity, and separation of symptoms.

### 7. How release assertions and results are represented

- **Why it matters:** The policy must be explicit and stable across both runs, and “pass” must not imply broader production readiness.
- **Options to evaluate:** A fixed typed policy object, a minimal declarative list of assertions, or code-defined checks with a serialized version.
- **Review focus:** Aggregation, incomplete/error states, versioning, evidence links, and UI language.

### 8. What sample-agent behavior constitutes the vulnerability and fix

- **Why it matters:** The demo is persuasive only if the unsafe behavior, first divergence, downstream symptom, and fix are realistic and visible.
- **Options to evaluate:** Unbounded or excessive retry, retry without idempotency protection, incorrect fallback, or false-success terminal state after timeout.
- **Review focus:** Choose one primary unsafe divergence with at least one distinct downstream symptom; avoid combining multiple independent bugs.

### 9. What must be Boundary-owned versus system-under-test-provided

- **Why it matters:** The system under test and its returned content are untrusted, while policy evidence must remain auditable.
- **Options to evaluate:** Boundary ownership of run identity, injection record, receipt order, scenario, assertions, and verdict; limited target ownership of internal transition events.
- **Review focus:** Validation, payload limits, sanitization, authoritative fields, conflict handling, and display safety.

## Reversible Decisions

The following choices should be made for Phase 1 convenience behind narrow boundaries and should not be treated as platform commitments:

- Exact React component hierarchy, routing layout, and styling approach.
- Exact Python module layout beyond clear separation of execution, evidence, analysis, and policy concerns.
- Initial persistence library or ORM, provided PostgreSQL remains authoritative and domain behavior is not hidden in ORM hooks.
- Migration tooling.
- Whether the bundled sample runs in-process, as a subprocess, or as a local container after the external contract is preserved.
- Exact event transport after event semantics and versioning are fixed.
- Trace export backend; Phase 1 may require none.
- Use of LangGraph for later campaign workflows.
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
| LangGraph | The initial workflow is small and deterministic; adding orchestration before state complexity is known may obscure core logic. | Campaign branching, resumability, checkpointing, or explicit workflow-state requirements that are materially simpler and safer with it. |
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

Commit to exactly:

- one bundled tool-using sample agent with vulnerable and narrowly fixed behavior;
- one versioned invocation and evidence contract;
- one deterministically injected tool timeout;
- one ordered, authoritative run-evidence path;
- one narrow expected-behavior scenario;
- one deterministic first-failing-boundary algorithm;
- one explicit release policy;
- one campaign start path and one run-details view;
- two real executions proving failed-before and passed-after behavior;
- one supported local Docker Compose workflow and automated verification.

If the week is at risk, reduce UI polish and internal generality first. Do not remove independent injection proof, evidence ordering, deterministic localization, fixed-policy comparison, or the real before-and-after rerun; those are the product claim.

## Exit Criteria and Handoff

Phase 1 is ready for review when every acceptance criterion in `docs/product-spec.md` has a linked verification result or an explicitly recorded failure. The handoff should include:

- exact clean-start and demo commands;
- automated test results;
- run identifiers for a vulnerable failure and fixed pass;
- the version identities of scenario, contract, sample variants, and policy;
- evidence references supporting injection, localization, symptoms, and both verdicts;
- repeated-run reliability results;
- and target-user feedback or a scheduled plan to collect it.

The next Codex task should be a reviewed pre-implementation design focused only on the nine decisions above. Its output should recommend the smallest coherent contracts and component boundaries for this Phase 1 slice, document alternatives and trust assumptions, and define verification strategy. Detailed architecture, data model, API schemas, and skeleton creation should follow only after those decisions are accepted.
