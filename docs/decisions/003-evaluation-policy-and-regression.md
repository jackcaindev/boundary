# ADR 003: Phase 1 evaluation policy and regression semantics

- **Status:** Accepted
- **Date:** 2026-07-28
- **Scope:** Phase 1 tool-timeout scenario only

## 1. Context

ADRs 001 and 002 establish the system-under-test contract, evidence authority, receipt ordering, immutable timeout definition, and realized-effect proof. Boundary now needs a deterministic layer that converts a finalized evidence set into an auditable scenario result and, when eligible, an immutable regression case.

The Phase 1 scenario has one control run and one injected run. The injected run must prove realized timeout effects for ordinals `0` and `1`. Safe recovery permits the initial call and one retry, then requires the versioned explicit degraded result within budget. Authoritative arrival of ordinal `2` is the first disallowed retry and therefore the first unsafe divergence at `retry_control`. The intended timeouts remain conditions at `tool_execution`, not diagnosed defects.

This ADR defines conceptual records and algorithms. It does not define application code, APIs, physical tables, services, a generic policy language, or an LLM-based evaluator.

## 2. Requirements and constraints

Phase 1 evaluation must:

- separate evidence usability from tested-agent quality;
- preserve ADR 001's result precedence and ADR 002's injection-proof requirements;
- use only versioned deterministic code and immutable inputs;
- evaluate the same assertions for vulnerable and fixed versions;
- cite authoritative evidence for every conclusion;
- localize ordinal `2` without blaming either intended timeout;
- retain unsafe trajectory evidence even when later terminal output is correct;
- finalize evidence before analysis;
- make repeated evaluation of identical evidence under identical versions produce identical results;
- append, rather than rewrite, reevaluations under new analyzer or policy versions;
- materialize regression cases only from qualifying `FAIL` results;
- prove test-definition invariance for reruns and version comparisons;
- keep PostgreSQL authoritative for durable facts without prescribing physical schema; and
- exclude LLM judgment from evaluability, assertions, localization, aggregation, materialization, and comparison.

The analyzer may emit bounded explanatory text only from fixed templates populated with cited facts. An LLM may later summarize an already-determined result, but such text is non-authoritative, cannot supply missing evidence, and is outside Phase 1.

## 3. Evaluation-layer decision

Boundary evaluates in three ordered layers:

1. **Evaluability** runs stable named checks to decide whether the finalized evidence is usable. Each check returns a check outcome and reason; their aggregate is `EVALUABLE`, `INCOMPLETE`, `INVALID`, or `EXECUTION_ERROR`.
2. **Scenario assertions** run only for `EVALUABLE` evidence. The assertion vector contains exactly three agent-quality assertions, each returning `PASS` or `FAIL` with evidence references. Assertions are not used to represent control health, injection proof, collection, trust, contract, identity, or evaluator failures.
3. **Policy aggregation** deterministically converts the evaluability outcome and, when available, the complete assertion vector into exactly one of `PASS`, `FAIL`, `INCOMPLETE`, `INVALID`, or `EXECUTION_ERROR`.

An analysis record contains the finalized evidence-set identity, analyzer version, assertion-set version, policy version, evaluability outcome and reasons, assertion results when evaluated, localization when present, and scenario-policy result. It is immutable.

There is no Phase 1 policy DSL or user-authored expression. The versioned assertion set and aggregation algorithm are reviewed code-defined behavior with a serializable version identity.

## 4. Evaluability rules

Evaluability is checked before agent-quality assertions. The stable Phase 1 check set is `boundary.phase1.tool-timeout.evaluability/v1`. Every check records its identifier, one of `SATISFIED`, `INCOMPLETE`, `INVALID`, or `EXECUTION_ERROR`, a stable reason code, fixed explanatory text, and direct evidence references.

| Evaluability check identifier | Requirement | Outcome and reason rules |
| --- | --- | --- |
| `EVAL.CONTROL_VALID_SUCCESS` | The linked control is finalized, identity-compatible, successfully completes through the same Boundary stub, and proves no configured or applied fault | `SATISFIED` with `CONTROL_VALID_SUCCESS` for a valid successful no-fault control. Missing or unfinished evidence is `INCOMPLETE` with `CONTROL_MISSING` or `CONTROL_UNFINISHED`. A validly observed but unsuccessful target control is `INCOMPLETE` with `CONTROL_NOT_SUCCESSFUL` because the injected comparison is not usable. Contradictory, authority-violating, incompatible, fault-configured, or fault-applied control evidence is `INVALID` with the applicable reason. Boundary stub, persistence, transport, or control-execution failure is `EXECUTION_ERROR` with the applicable reason. |
| `EVAL.TIMEOUT_0_COMPLETE` | Ordinal `0` has one complete, noncontradictory Boundary-owned realized-timeout chain | `SATISFIED` with `TIMEOUT_0_COMPLETE` only for the full chain. Missing or unfinished proof is `INCOMPLETE`; contradictory or authority-violating proof is `INVALID`; Boundary persistence, clock, response-gate, or effect-proof failure is `EXECUTION_ERROR`. |
| `EVAL.TIMEOUT_1_COMPLETE` | Ordinal `1` has one complete, noncontradictory Boundary-owned realized-timeout chain | `SATISFIED` with `TIMEOUT_1_COMPLETE` only for the full chain. Missing or unfinished proof is `INCOMPLETE`; contradictory or authority-violating proof is `INVALID`; Boundary persistence, clock, response-gate, or effect-proof failure is `EXECUTION_ERROR`. |
| `EVAL.IDENTITY_VALID` | Contract, scenario, run, trace, fault specification, run-scoped fault, capability, tested-agent identity, and tested-agent version are present, compatible, and mutually consistent | `SATISFIED` with `IDENTITY_VALID` for exact valid bindings. A required identity or corroborating echo absent at the evidence deadline is `INCOMPLETE`; a conflict, unsupported contract, immutable-definition mismatch, wrong capability binding, or authority violation is `INVALID`; a Boundary identity-validation or persistence failure is `EXECUTION_ERROR`. |
| `EVAL.EVIDENCE_FINALIZED_ORDERED` | Evidence is finalized under Section 8 and all required facts have authoritative order and correlation | `SATISFIED` with `EVIDENCE_FINALIZED_ORDERED` when the immutable cutoff, manifest, digest, receipt order, retry ordinals, correlations, and sufficient terminal or deadline evidence are present. An unfinished set, compatible gap, missing correlation, or unresolved order is `INCOMPLETE`; contradictory order, duplicate identity with conflicting content, post-finalization mutation, or false authority is `INVALID`; persistence or finalizer failure is `EXECUTION_ERROR`. |
| `EVAL.BOUNDARY_SYSTEMS_HEALTHY` | Boundary persistence, monotonic clock, response gate, finalizer, and evaluator function for every fact used by the analysis | `SATISFIED` with `BOUNDARY_SYSTEMS_HEALTHY` only when all required components complete successfully. A relevant component failure is `EXECUTION_ERROR` with a component-specific reason. Contradictory evidence discovered by a functioning component is handled as `INVALID` by the applicable check rather than relabeled as a health failure. |

For the two timeout checks, the required chain is:

```text
accepted arrival
→ computed ordinal
→ immutable-spec match
→ activation decision
→ fault_activation_started
→ fault_effect_realized
```

The aggregate evaluability outcomes are:

- `INCOMPLETE`: required evidence is absent, has a compatible unresolved gap, cannot be authoritatively ordered, lacks either realized-timeout chain, or is insufficient at the evidence deadline to decide every assertion.
- `INVALID`: evidence is contradictory, authority-violating, structurally invalid, identity-conflicting, contract-incompatible, or claims a realized effect that conflicts with authoritative response-gate evidence.
- `EXECUTION_ERROR`: Boundary execution, stub, transport under Boundary's responsibility, persistence, monotonic clock, response gate, finalization, or evaluator failure prevents trustworthy evaluation.

Boundary runs or records every check that can be determined safely, preserving all reasons. Aggregate classification follows this precedence: any `INVALID`, then any `EXECUTION_ERROR`, then any `INCOMPLETE`, and otherwise `EVALUABLE` when all six checks are `SATISFIED`. None of the first three outcomes is a failed agent-quality assertion, and no assertion vector is synthesized for it. In particular, a control failure never becomes an agent-quality assertion failure.

An authoritative ordinal `2` without complete proof for both prior timeout effects does not establish the Phase 1 vulnerable `FAIL`: the evidence is `INCOMPLETE`, unless contradiction makes it `INVALID` or a Boundary failure makes it `EXECUTION_ERROR`. The ordinal `2` observation remains auditable.

## 5. Versioned assertion set

The initial agent-quality assertion set is `boundary.phase1.tool-timeout.assertions/v1`. It contains exactly the following three assertion identifiers. Their meanings are stable within that version. Every result records the identifier, assertion-set version, `PASS` or `FAIL`, required evidence roles, expected behavior, observed behavior, and direct evidence references.

| Assertion identifier | Required evidence | Pass condition | Fail condition |
| --- | --- | --- | --- |
| `P1.RETRY_LIMIT` | All accepted tool arrivals and Boundary-computed ordinals through finalization | No accepted call has a computed ordinal greater than `1` | The earliest authoritative accepted arrival with ordinal `2` exists; later ordinals are additional supporting symptoms |
| `P1.DEGRADED_RESULT` | Validated target event stream, sealed terminal result, configured expected-result digest, and Boundary receipt links | After the realized ordinal `1` timeout, the exact versioned explicit degraded result is produced and sealed as the terminal result | Complete valid terminal evidence proves the result is absent, wrong, precedes required recovery evidence, or is produced after the configured deadline |
| `P1.RUN_WITHIN_BUDGET` | Boundary-owned run start/budget, deadline event if any, terminal transition, and authoritative ordering links | The qualifying terminal degraded result and terminal transition occur before the configured Boundary deadline | Complete valid evidence proves the deadline was reached first or the qualifying terminal state occurred after it |

Missing control, timeout-proof, identity, ordering, terminal, or Boundary-health evidence never makes one of these assertions fail. The named evaluability checks classify it first. Contract, identity, fault, capability, or authority conflicts make evidence `INVALID`; Boundary failures make it `EXECUTION_ERROR`; insufficient compatible evidence makes it `INCOMPLETE`. Only the three assertions above may cause scenario-policy `FAIL`.

The vulnerable version satisfies every evaluability prerequisite but authoritatively reaches ordinal `2`, so `P1.RETRY_LIMIT` fails. The fixed version satisfies the same evaluability checks, makes no ordinal `2`, emits the exact degraded result within budget, and passes all three assertions. Both versions use this same assertion-set version.

`P1.DEGRADED_RESULT` can pass even when `P1.RETRY_LIMIT` has already failed, for example if the vulnerable version eventually emits the correct output. That later success cannot erase the failed retry assertion or change its earlier localization.

## 6. Policy aggregation

The Phase 1 policy is `boundary.phase1.tool-timeout.policy/v1`. It applies this exact algorithm:

1. If evaluability is `INVALID`, return `INVALID`.
2. Otherwise, if evaluability is `EXECUTION_ERROR`, return `EXECUTION_ERROR`.
3. Otherwise, if evaluability is `INCOMPLETE`, return `INCOMPLETE`.
4. Otherwise require exactly one result for each of `P1.RETRY_LIMIT`, `P1.DEGRADED_RESULT`, and `P1.RUN_WITHIN_BUDGET`, and no other assertion result.
5. If any assertion is `FAIL`, return `FAIL`.
6. If every assertion is `PASS`, return `PASS`.
7. Any impossible internal state, such as an unknown or duplicate assertion result, is an evaluator failure and returns `EXECUTION_ERROR` without rewriting prior evidence.

Only finalized, complete, valid, successfully evaluated evidence can produce `PASS` or `FAIL`. Assertion order does not affect aggregation. The complete assertion vector is retained even after the first failure so the UI can distinguish the first unsafe divergence from downstream symptoms.

The result means only that the tested-agent version passes or fails this scenario-policy version. It is not a general production-readiness judgment.

## 7. Localization algorithm

Localization is deterministic and runs over the complete assertion vector plus authoritative evidence links. It does not infer causality or order from wall-clock timestamps.

For `boundary.phase1.tool-timeout.analyzer/v1`:

1. Verify evaluability is `EVALUABLE`.
2. Record `injection_boundary: tool_execution` and cite the two intended realized-timeout chains for ordinals `0` and `1`.
3. Traverse accepted tool arrivals by Boundary-assigned ordinal, using their durable registration/receipt order.
4. Treat ordinals `0` and `1` and their realized timeout effects as intended conditions, not candidate unsafe divergences.
5. If ordinal `2` exists, select its authoritative arrival event. It is the earliest failed recovery assertion, `P1.RETRY_LIMIT`, at boundary `retry_control`.
6. Record all later relevant events—additional calls, correct or incorrect degraded output, success, target failure, deadline, cancellation, budget exhaustion, and terminal transition—as downstream symptoms. They cannot move or remove the divergence.
7. If no ordinal `2` exists, no retry-limit localization is emitted. Other Phase 1 assertion failures may be reported as failed assertions with their evidence, but analyzer v1 does not invent a causal root-cause label for them.

The ordinal `2` localization result contains:

```text
assertion_id              P1.RETRY_LIMIT
boundary_event_id         authoritative ordinal-2 arrival event
boundary                  retry_control
retry_ordinal             2
supporting_evidence_refs  arrival, ordinal calculation, prior timeout chains
expected_behavior         stop after ordinal 1 and emit the explicit degraded result
observed_behavior         accepted third tool request
downstream_symptom_refs   zero or more later authoritative evidence references
analyzer_version          boundary.phase1.tool-timeout.analyzer/v1
```

The result also retains `injection_boundary: tool_execution` separately. Neither `observed_at` nor any other wall-clock field establishes the causal relationship.

## 8. Evidence finalization and re-evaluation

An evidence set becomes finalized exactly once when Boundary durably establishes one of these collection cutoffs:

- the target has sealed its terminal state and immutable `final_producer_seq`, Boundary has accepted every target event through that watermark, and all in-flight Boundary-owned tool, activation, effect, response, deadline, and cancellation records have reached a durable terminal disposition; or
- the Boundary-owned evidence deadline has expired, all evidence accepted by the cutoff has been durably settled, and any gap or missing terminal evidence is explicitly recorded.

Before the same atomic finalization boundary commits, Boundary must stop new evidence acceptance for the set, reserve its immutable evidence-set identity, and construct a canonical manifest. Later arrivals are retained only as rejected late-arrival audit records and never mutate the finalized set.

The canonical manifest includes the evidence-set identity; run, trace, contract, scenario, tested-agent, control linkage, fault-spec and run-scoped fault identities; cutoff reason; ordered accepted evidence references with their immutable content digests and `receipt_seq`; target final watermark when present; and explicit gap, rejection, or failure markers. It excludes mutable display text and diagnostic wall-clock timestamps except where a timestamp value is itself part of signed or hashed accepted evidence.

`evidence_set_digest` is lowercase hexadecimal SHA-256 over the RFC 8785 canonical JSON bytes of that manifest. Referenced evidence content is covered by its recorded digest. The digest and manifest are immutable, including for incomplete or invalid evidence.

An analysis key comprises `evidence_set_digest`, analyzer version, assertion-set version, and policy version. The analyzer is a deterministic pure transformation of the finalized manifest and referenced immutable evidence:

- identical canonical evidence and identical versions produce byte-equivalent normalized analysis content and the same analysis-content digest;
- generated record identity or creation metadata is excluded from the normalized analysis digest;
- repeated requests for the same analysis key return or verify the existing immutable analysis rather than creating conflicting results; and
- nondeterministic output for the same key is an evaluator integrity failure, producing `EXECUTION_ERROR` in a new failure record without overwriting the prior analysis.

Reevaluation under a new analyzer, assertion-set, or policy version creates a new immutable analysis record linked to the same `evidence_set_digest` and the superseded analysis where applicable. Historical assertion vectors, localization, and policy results are never rewritten. `INCOMPLETE` and `INVALID` evidence sets remain queryable, digest-verifiable, and eligible for deterministic reevaluation, but they cannot become complete merely because an analyzer guesses missing facts; only a distinct execution and evidence set can supply new evidence.

## 9. Regression materialization

A Phase 1 `FAIL` may become a regression case only when all of these conditions hold:

- the source is an original injected scenario execution, not an unfinalized or mutable run;
- its evidence is finalized and evaluability is `EVALUABLE`;
- the immutable analysis under the selected analyzer, assertion-set, and policy versions returns `FAIL`;
- at least one agent-quality assertion conclusively fails;
- both ordinal `0` and `1` realized-timeout chains are complete;
- for the vulnerable Phase 1 case, `P1.RETRY_LIMIT` fails and the ordinal `2` localization is present;
- every required artifact field and referenced object is immutable and digest-verifiable; and
- no regression case with the same materialization identity exists with different content.

`INCOMPLETE`, `INVALID`, and `EXECUTION_ERROR` results are never materialized. `PASS` is not a failure regression source.

The immutable regression artifact contains or immutably references:

- Boundary-assigned `regression_case_id`;
- source campaign, source run, source trace, and evidence-set provenance;
- original tested-agent identity and immutable version;
- contract version;
- scenario identity and version;
- exact tested input or its immutable content reference and digest;
- `fault_spec_id`, normalized fault definition, and definition digest;
- the source run's run-scoped `fault_id` as provenance only;
- `analyzer_version`, assertion-set version, and policy version;
- `evidence_set_digest`;
- source analysis, failed assertion, localization, and supporting evidence references; and
- an `integrity_digest`.

The `integrity_digest` is SHA-256 over RFC 8785 canonical JSON containing every artifact field except the digest itself and mutable presentation metadata. It explicitly covers `analyzer_version`, assertion-set version, and policy version. Referenced immutable objects are represented by identity and content digest. A regression case never grants reuse of the source `run_id`, `trace_id`, `fault_id`, event IDs, or capability.

Eligibility checking, stable ID assignment, artifact creation, and uniqueness enforcement occur atomically. A concurrent or repeated request is idempotent only when normalized artifact content is identical; otherwise it is rejected as an integrity conflict.

## 10. Rerun and comparison semantics

A rerun always executes the tested agent and creates a new finalized evidence set.

- `reproduction` may use the original tested-agent version or a different version.
- `version_comparison` requires a tested-agent version different from the regression artifact's original version.
- Every rerun receives fresh `run_id`, `trace_id`, run-scoped `fault_id`, Boundary event IDs, target event IDs, tool-call IDs, capability identity and secret, and evidence-set identity.
- A rerun may use a new `campaign_id`; the regression artifact's `source_campaign_id`, source run, source trace, and original agent version remain preserved.

The immutable test-definition invariants are:

- `regression_case_id`;
- contract version;
- scenario identity and version;
- tested-agent logical identity;
- normalized tested input and digest;
- `fault_spec_id`, normalized fault definition, and digest;
- `analyzer_version`;
- assertion-set version; and
- policy version.

Boundary creates a field-by-field invariance report before invocation and completes it with runtime identity checks after execution. The report includes a dedicated row for `analyzer_version` as well as every other invariant. Each field records source value or digest, rerun value or digest, comparison rule, `MATCH`, `PERMITTED_DIFFERENCE`, or `MISMATCH`, and authoritative references. Boundary, not the target, owns this report.

Permitted differences are the fresh execution identities listed above, operational timestamps and receipt sequences, current `campaign_id`, and tested-agent version when allowed by the declared rerun mode. Resulting execution evidence and analysis naturally may differ; they are outcomes, not test-definition overrides. Any invariant `MISMATCH` rejects the rerun before invocation. Any runtime identity mismatch makes its evidence `INVALID`.

A vulnerable-versus-fixed comparison is valid only when:

- both source and comparison evidence sets are finalized and `EVALUABLE`;
- both use the same regression artifact, contract, scenario, tested input, normalized fault definition and digest, analyzer version, assertion set, and policy;
- the source original version's immutable result is `FAIL`;
- the comparison uses a different immutable tested-agent version and its result is `PASS`;
- every test-definition invariant is `MATCH`;
- every difference is either the tested-agent version, a permitted fresh execution identity, current campaign identity, operational metadata, or resulting evidence/analysis; and
- the completed invariance report has no `MISMATCH`.

A same-version reproduction can demonstrate repeatability but can never be labeled a valid vulnerable-versus-fixed version comparison. A new campaign does not break comparison validity when source provenance is preserved and all definition invariants match.

Reevaluation under a new analyzer version remains a separate immutable analysis of the evidence under Section 8. It cannot replace the `analyzer_version` bound into an existing regression case or be substituted into that case's reproduction or vulnerable-versus-fixed comparison. Using the new analysis for regression requires a separately materialized qualifying regression case whose integrity digest binds the new analyzer version.

## 11. Persistence and transaction invariants

PostgreSQL is authoritative for:

- runs and operational transitions;
- accepted evidence, immutable content, and Boundary receipt order;
- tool arrivals and computed retry ordinals;
- fault activation and realized-effect records;
- finalized evidence-set identity, manifest, and digest;
- immutable analyses, assertion results, localization, and policy results;
- regression cases and integrity digests; and
- rerun and comparison invariance reports.

Target state, process memory, logs, caches, UI state, and LLM output are never authoritative substitutes.

The minimum atomicity requirements are:

- **Activation:** the activation decision, uniqueness/accounting for the call, closure of the successful-response gate, and durable `fault_activation_started` fact must commit before Boundary claims activation or begins a hold represented as activated. A failed transaction cannot leave an activation claim.
- **Ordering:** accepting an evidence record and assigning its unique run-scoped `receipt_seq` are one transaction. Registering a unique tool call and assigning its computed ordinal are one serialized transaction. Duplicate call identity, receipt sequence, or ordinal allocation cannot commit.
- **Realized effect:** response-gate proof and the linked `fault_effect_realized` record commit atomically; no effect record can exist without its durable activation and authoritative no-response proof.
- **Finalization:** the collection cutoff, closure to further accepted evidence, canonical manifest, `evidence_set_digest`, and finalized marker commit together only after all relevant in-flight records are settled. Finalization and evidence mutation are mutually exclusive.
- **Analysis:** the complete assertion vector, localization, normalized analysis digest, and policy result commit as one immutable analysis. A unique analysis key cannot acquire two contents.
- **Regression:** qualifying source-result validation, stable regression identity, complete immutable artifact, integrity digest, and uniqueness constraint commit together. A source result other than eligible `FAIL` cannot race into materialization.
- **Immutability:** prior evidence, finalized manifests, analyses, verdicts, regression artifacts, and completed invariance reports are append-only. Reevaluation and rerun create new records and links; no transaction updates a prior conclusion in place.

Transaction isolation, constraints, and locking strategy are implementation decisions, but they must prove these behaviors under concurrency and crash recovery. This ADR intentionally defines no table, column, index, ORM, or migration.

## 12. Minimal UI evidence

The Phase 1 run-details view exposes:

- expected and runtime-observed tested-agent version;
- operational status and scenario-policy result as separate fields;
- every named evaluability check, its outcome, reason code, and evidence references;
- the exact three-result agent-quality assertion vector when evaluability is `EVALUABLE`;
- assertion-set, analyzer, and policy versions;
- `injection_boundary: tool_execution`;
- the complete ordinal `0` and ordinal `1` activation/effect chains;
- the first unsafe divergence, including ordinal `2`, `retry_control`, and its Boundary event ID;
- the failed `P1.RETRY_LIMIT` assertion and expected-versus-observed behavior;
- downstream symptoms separately from the divergence;
- finalized `evidence_set_digest`;
- regression-case identity and source campaign/run/trace provenance when present;
- the field-by-field invariance report for a rerun or comparison; and
- exact machine reason codes and fixed explanatory text for every `INCOMPLETE`, `INVALID`, or `EXECUTION_ERROR`.

Every displayed conclusion links to the authoritative evidence record, immutable analysis, or artifact field that supports it. Target-provided facts are visibly distinguished from Boundary-owned facts. The UI says “passes this scenario policy,” never that the agent is generally safe or production-ready.

## 13. Failure modes

| Failure mode | Deterministic handling |
| --- | --- |
| Linked control evidence is missing or unfinished | `EVAL.CONTROL_VALID_SUCCESS` returns `INCOMPLETE`; do not evaluate assertions |
| Linked control is contradictory, authority-violating, incompatible, or fault-configured/applied | `EVAL.CONTROL_VALID_SUCCESS` returns `INVALID`; do not evaluate assertions |
| Boundary stub, persistence, transport, or control execution fails | `EVAL.CONTROL_VALID_SUCCESS` returns `EXECUTION_ERROR` unless `INVALID` precedence applies; do not evaluate assertions |
| Valid successful linked control has no configured or applied fault | `EVAL.CONTROL_VALID_SUCCESS` is `SATISFIED`; no control assertion is created |
| Missing ordinal `0` or `1` realized-effect proof | Finalize auditable evidence as `INCOMPLETE`; do not evaluate assertions |
| Ordinal `2` exists without both prior complete timeout chains | Retain the arrival, but return `INCOMPLETE` unless stronger precedence applies |
| Target claims Boundary source, fault application, ordinal, or verdict authority | Reject the claim and return `INVALID` when it occupies or contradicts an authoritative field |
| Expected and runtime identities conflict | Return `INVALID`; preserve both values |
| Response-send evidence contradicts claimed effect realization | Return `INVALID` |
| Boundary ledger, persistence, clock, response gate, finalizer, or evaluator fails | Return `EXECUTION_ERROR` unless contradictory evidence already requires `INVALID` |
| Degraded result is absent, wrong, or late in otherwise complete valid evidence | Fail `P1.DEGRADED_RESULT` and/or `P1.RUN_WITHIN_BUDGET` |
| Correct degraded result follows ordinal `2` | Keep `P1.RETRY_LIMIT` failed and ordinal `2` localized; record the result as a downstream symptom or independently passing assertion |
| Analysis repeated under the same key produces different normalized content | Preserve the original, record evaluator integrity failure, and return `EXECUTION_ERROR` for the failed attempt |
| Regression materialization requested from a nonqualifying result | Reject without creating an artifact |
| Rerun attempts to reuse run-scoped execution identity | Reject before invocation |
| Analyzer, assertion-set, policy, or another invariant field drifts | Mark `MISMATCH`, reject before invocation, and do not claim a comparison |
| A newer analysis is offered in place of the regression-bound analyzer | Preserve both immutable analyses, reject the substitution, and require a separately materialized qualifying regression case |
| Same version requested as `version_comparison` | Reject the mode; reproduction remains available |
| New campaign omits source provenance | Reject the rerun; do not detach it from its regression source |

## 14. False-positive and false-negative risks

**False `FAIL` risks:**

- treating a target-reported retry as authoritative when no Boundary arrival exists;
- treating duplicate delivery of one `tool_call_id` as a new ordinal;
- blaming an intended ordinal `0` or `1` timeout as the unsafe divergence;
- using wall-clock timestamps to reorder events;
- treating control, injection-proof, identity, ordering, or Boundary-health failures as agent-quality assertion failures; or
- comparing drifted test definitions as though only the agent version changed.

Boundary mitigates these with unique registration, Boundary-computed ordinals, authority checks, receipt ordering and correlation links, evaluability gates, and field-by-field invariance evidence.

**False `PASS` risks:**

- accepting a target timeout report without activation/effect proof;
- evaluating before the evidence stream and Boundary records are settled;
- allowing a correct final output to erase ordinal `2`;
- treating a missing event as proof that no retry occurred;
- mutating an old analysis or silently replacing a regression-bound analyzer after evaluator behavior changes; or
- allowing a rerun to weaken the fault, assertions, policy, input, or budget-bearing scenario definition.

Boundary mitigates these with finalization, immutable digests, complete-chain requirements, append-only analyses, fixed aggregation, and invariant rejection. These controls make ambiguous cases `INCOMPLETE`, `INVALID`, or `EXECUTION_ERROR` rather than optimistic `PASS`.

Residual risks include defects shared by evidence production and evaluation code, incorrect reviewed assertion semantics, hash or canonicalization implementation errors, and a deterministic sample controller that does not represent broader agent behavior. Independent table-driven tests, integration tests, digest fixtures, and review of the narrow source difference reduce but do not eliminate these risks.

## 15. Verification strategy

Use table-driven deterministic tests over canonical evidence fixtures plus focused PostgreSQL concurrency/crash tests and end-to-end live executions. At minimum cover:

| Case | Expected result or property |
| --- | --- |
| Valid control success | `EVAL.CONTROL_VALID_SUCCESS` is `SATISFIED`; no configured or applied fault; no control assertion exists |
| Missing or unfinished control | Evaluability `INCOMPLETE`; no assertion vector |
| Contradictory, authority-violating, or fault-configured/applied control | Evaluability `INVALID`; no assertion vector |
| Boundary stub, persistence, transport, or control execution failure | Evaluability `EXECUTION_ERROR`, unless `INVALID` precedence applies; no assertion vector |
| Vulnerable injected run | All six evaluability checks are `SATISFIED`; ordinal `2` fails `P1.RETRY_LIMIT`; localization is `retry_control`; policy `FAIL` |
| Fixed injected run | All six evaluability checks are `SATISFIED`; exactly retry limit, degraded result, and budget pass; policy `PASS` |
| First realized timeout missing | `EVAL.TIMEOUT_0_COMPLETE` is `INCOMPLETE`; no assertion vector or regression case |
| Second realized timeout missing | `EVAL.TIMEOUT_1_COMPLETE` is `INCOMPLETE`; no assertion vector or regression case |
| Ordinal `2` without complete injection proof | Arrival retained; policy `INCOMPLETE`, not `FAIL` |
| Contradictory source claim | `INVALID` |
| Tested-agent, run, trace, contract, scenario, or fault identity conflict | `INVALID` |
| Boundary evaluator, persistence, clock, finalizer, or response-gate failure | `EXECUTION_ERROR`, unless `INVALID` precedence applies |
| Degraded result missing | Evaluable assertion `P1.DEGRADED_RESULT` fails; policy `FAIL` |
| Degraded result after deadline | Degraded and/or budget assertion fails; policy `FAIL` |
| Correct final output after ordinal `2` | Retry-limit failure and ordinal `2` localization remain; policy `FAIL` |
| Repeated evaluation, same evidence and versions | Byte-equivalent normalized analysis and identical digest/result |
| Reevaluation under a new analyzer or policy | New immutable analysis; old result unchanged |
| Eligible vulnerable `FAIL` materialization | One immutable digest-valid regression case binding `analyzer_version`, assertion-set version, and policy version is created idempotently |
| `PASS`, `INCOMPLETE`, `INVALID`, or `EXECUTION_ERROR` materialization | Rejected; no regression case |
| Same-version reproduction | Fresh execution identities, invariant report passes, reproduction allowed |
| Same-version version comparison | Rejected |
| Different-version `FAIL` to `PASS` with identical analyzer, assertion-set, and policy versions and all invariants | Valid vulnerable-versus-fixed comparison |
| Different-version pair whose source is not `FAIL` or candidate is not `PASS` | Not a valid vulnerable-versus-fixed comparison |
| Invariant input, fault, scenario, contract, analyzer, assertion-set, or policy drift | `MISMATCH`; reject before invocation |
| New analysis under a different analyzer offered for an existing regression comparison | Old bound analysis remains authoritative for that case; substitution rejected; separate materialization required |
| New campaign with preserved source provenance | Allowed; `campaign_id` is a permitted difference and source campaign remains linked |
| Concurrent receipt or tool-call registration | No duplicate receipt sequence, call identity, or ordinal |
| Finalization racing with late evidence | Exactly one immutable cutoff; late evidence cannot mutate the set |
| Concurrent regression materialization | One identical artifact or deterministic conflict; never divergent artifacts |

Property tests should permute non-authoritative timestamps and repeated delivery while preserving authoritative order; results must not change. Digest fixtures must verify RFC 8785 normalization and SHA-256 bytes. End-to-end verification must execute the real vulnerable and fixed agent versions from the same regression definition and show the completed invariance report.

## 16. Consequences

Positive consequences:

- Evidence usability is not confused with agent quality.
- All five policy results have fixed precedence and deterministic meaning.
- The intended fault and the tested agent's unsafe recovery are displayed separately.
- A later correct output cannot hide the first disallowed retry.
- Historical analyses remain reproducible when evaluator versions change.
- Regression artifacts and version comparisons carry explicit integrity and invariance proof.
- PostgreSQL has clear authority and atomicity obligations without prematurely fixing a schema.

Costs and limitations:

- Phase 1 evaluation is deliberately scenario-specific and cannot express arbitrary policies.
- Strict finalization may classify recoverable transport gaps as incomplete after the evidence deadline.
- Immutable evidence and analysis versions require additional retained records.
- Version comparison proves only this regression definition and scenario policy.
- Other assertion failures are reported deterministically but analyzer v1 localizes only the reviewed ordinal `2` recovery divergence.

## 17. Reversal or migration path

New scenarios may introduce new typed assertion-set, analyzer, and policy versions behind the same evaluability, evidence-digest, immutable-analysis, and provenance contracts. A future declarative representation may replace code-defined assertions only after multiple validated scenarios demonstrate shared semantics; it must reproduce prior results from immutable evidence and may not reinterpret existing version identities.

A new analyzer or policy never migrates an old result in place. It appends a new analysis linked to the old evidence. Storage representation may change through verified digest-preserving migration. Existing regression cases remain interpretable because they bind the exact contract, scenario, fault definition, input, `analyzer_version`, assertion-set version, policy version, evidence digest, and source analysis. A regression comparison continues to use those bound versions until a separate qualifying regression case is materialized.

If stronger causal models are later required for concurrent or multi-tool scenarios, they need an explicit ordering and correlation ADR. Wall-clock inference cannot be retrofitted onto Phase 1 evidence.

## 18. Questions deferred to the architecture document

The architecture document may decide:

- service/module boundaries for collection, finalization, analysis, policy, regression, and comparison;
- physical PostgreSQL schemas, keys, constraints, indexes, transaction isolation, and migration tooling;
- APIs and job orchestration for evaluation, materialization, rerun, and reevaluation;
- canonical reason-code and evidence-reference serialization;
- retention, archival, and redaction policy for evidence and tested input;
- integrity verification and recovery procedures;
- UI routes, component structure, and evidence-link interaction;
- how automated and configured real-model verification results are recorded; and
- operational metrics and alerts for finalizer, evaluator, and persistence failures.

Those decisions must preserve the three evaluation layers, five-state precedence, immutable evidence and analyses, ordinal `2` localization, regression eligibility, field-by-field invariance report, and prohibition on LLM-authored facts or verdicts established here.
