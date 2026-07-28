# ADR 002: Phase 1 fault-injection model

- **Status:** Accepted
- **Date:** 2026-07-27
- **Scope:** Phase 1 tool-timeout scenario only

## 1. Context

ADR 001 establishes an external system-under-test boundary, Boundary-owned run and trace identities, a Boundary-owned tool endpoint, Boundary-computed retry ordinals, and a trust rule: the tested agent may describe its experience, but it cannot prove that Boundary applied a fault.

Phase 1 must use one immutable timeout definition to produce two materially different live executions:

- the vulnerable agent's initial tool call and one permitted retry each have a Boundary-proven realized timeout effect, after which deterministic recovery requests a third call; and
- the fixed agent has the identical two Boundary-proven realized timeout effects, requests no third call, and deterministically produces the expected explicit degraded result within budget.

The third request in the vulnerable run is retry ordinal `2` and is the first unsafe divergence at `retry_control`. The injected timeouts occur at `tool_execution` and are intended test conditions. Later budget exhaustion or terminal failure is a downstream symptom.

This ADR decides the smallest mechanism and semantics needed to make those claims trustworthy. It defines a conceptual contract and evidence model, not application code, database schemas, service scaffolding, or a general fault language.

## 2. Requirements and constraints

The Phase 1 mechanism must:

- be owned and observed by Boundary rather than by the tested agent;
- deterministically affect the initial attempt and one permitted retry;
- independently prove both activation start and realized timeout effect for every affected attempt at the intended tool boundary;
- observe a vulnerable third request as authoritative retry ordinal `2`;
- allow the model to select only the initial tool and arguments, after which ordinary deterministic application control owns timeout recovery and retry count;
- make the vulnerable version deterministically issue ordinals `0`, `1`, and `2`;
- make the fixed version deterministically issue only ordinals `0` and `1`, then produce the explicit degraded result;
- prevent model output from changing the retry ceiling, timeout policy, fault specification, or degraded terminal behavior;
- leave control runs on the same tool path unaffected;
- use the same immutable `fault_spec_id`, normalized definition, and digest across vulnerable and fixed runs;
- assign each injected run a fresh, run-scoped `fault_id`;
- allow one run-scoped fault to activate on multiple matching calls;
- activate at most once for each unique `tool_call_id`;
- reject duplicate, wrong-target, expired, and cross-run calls deterministically;
- finish within bounded deadlines in automated and real-model demonstrations;
- preserve ADR 001's authority, ordering, lifecycle, regression, and policy semantics; and
- avoid a general fault DSL, external dependency, infrastructure-chaos layer, or enterprise identity system.

Phase 1 has exactly one expected tool and one logical tool operation per run. Calls are serial in the supported scenario. General distributed-operation correlation, arbitrary triggers, latency profiles, and multiple simultaneous faults are out of scope.

## 3. Options considered

### 3.1 Agent-side fault hooks

An agent-side hook can raise a timeout with little code and no extra network hop. It is rejected because the system under test would both apply and report the adverse condition. Boundary could not independently distinguish a configured injection from agent behavior, an incidental exception, or fabricated evidence. Hooks also risk coupling the scenario to LangGraph or a particular tool client.

This option is smaller only by discarding ADR 001's trust model.

### 3.2 Boundary-owned tool proxy or stub

The agent calls a Boundary-owned HTTP tool endpoint. For Phase 1, that endpoint is a deterministic stub: it returns a fixed successful result when no fault applies and withholds that result past the configured client timeout when a fault applies. Boundary registers request arrival, computes retry order, records activation start, and separately records whether the timeout effect was realized before the target can report its experience.

This adds one narrow endpoint already anticipated by ADR 001. It supplies the required trust boundary, deterministic control behavior, direct correlation, and authoritative evidence without another deployable dependency.

### 3.3 Separate mock dependency service

A separate mock service could implement the same success and delay behavior. It is rejected for Phase 1 because Boundary would still need a trusted proxy, signed callbacks, or another evidence channel to prove what the mock did. Otherwise the mock becomes an additional authority and service lifecycle for no scenario benefit. It may become useful when a future scenario must reproduce a real dependency protocol that a Boundary stub cannot credibly represent.

### 3.4 Infrastructure-level network fault injection

Container network shaping, firewall rules, or a chaos proxy could create realistic packet loss or delay. It is rejected because matching a specific run and tool call, proving each activation, enforcing at-most-once application, and maintaining a clean negative control would require privileged configuration and platform-specific orchestration. Network timing also makes automated tests less deterministic.

This option is appropriate only if a later requirement specifically concerns transport behavior that cannot be represented at the HTTP tool boundary.

## 4. Decision

Phase 1 will use a **Boundary-owned deterministic HTTP tool stub** on the existing Boundary service.

The bundled agent receives a run-scoped tool URL and opaque authorization capability. Every logical attempt calls that URL with a new `tool_call_id`. After authorization and identity validation, Boundary atomically registers the unique call, assigns its retry ordinal from observed arrival order, matches the immutable fault specification, and decides whether to activate.

The model may select the initial tool and its arguments. Once that selection is made, a versioned, ordinary deterministic LangGraph/application control path owns timeout recovery. It reuses the selected logical operation, applies the fixed tool-client timeout, and controls all subsequent retry and terminal decisions. Model output cannot raise or lower the retry ceiling, alter timing or fault configuration, select the degraded result, or decide whether another attempt occurs.

For fault-enabled runs:

- the vulnerable deterministic controller issues ordinal `0`, ordinal `1`, and then the disallowed ordinal `2`;
- ordinals `0` and `1` each begin an activation and must independently realize the configured timeout effect;
- ordinal `2` is accepted and observed as a third tool request but does not activate because it is outside `affected_attempts`;
- the observation of ordinal `2`, not any subsequent result, is authoritative evidence of the first unsafe divergence at `retry_control`; and
- later calls or terminal effects are downstream symptoms.

For fixed runs, the deterministic controller issues only ordinals `0` and `1`; both begin activation and realize the identical configured timeout effect. The controller then emits the versioned explicit degraded result without consulting the model and without a third request.

Automated fake-model runs and the configured real-model demonstration differ only in initial tool-and-argument selection. After that selection they execute the same deterministic recovery path for the selected agent version.

One `fault_id` identifies the run-scoped configured fault instance. It does **not** identify a single activation. That fault may produce up to two activation events, one for each matching `tool_call_id`. “At most once” is enforced per `tool_call_id`, not per `fault_id` or per run.

For each matching call, Boundary distinguishes:

- `fault_activation_started`: the activation decision was durably committed and Boundary began withholding the successful response; and
- `fault_effect_realized`: Boundary-owned monotonic evidence proves that no response was sent before the configured client-timeout boundary.

The first event never implies the second. In particular, a client disconnect or cancellation before the client-timeout boundary proves only that activation began.

The stub does not forward to a real dependency in Phase 1. Its no-fault result is fixed by the versioned scenario, so normal behavior and injection behavior do not depend on an external service.

## 5. Fault-specification semantics

### 5.1 Immutable definition

The Phase 1 fault specification is a closed, versioned object with these conceptual fields:

```json
{
  "schema_version": 1,
  "fault_kind": "tool_timeout",
  "target_tool": "boundary.phase1.lookup",
  "trigger_rule": "retry_ordinal_in",
  "affected_attempts": [0, 1],
  "tool_client_timeout_ms": 500,
  "injected_hold_ms": 1000,
  "maximum_activations": 2,
  "scenario_id": "phase1.tool-timeout",
  "scenario_version": 1,
  "compatible_contract_versions": ["1"]
}
```

The identifiers above are conceptual stable values; implementation may choose equivalent reviewed names without changing their meaning.

- `target_tool` is an exact allowlisted tool identity, not a pattern.
- `trigger_rule` has only the literal Phase 1 meaning “activate when Boundary's computed retry ordinal is in `affected_attempts`.”
- `affected_attempts` is exactly `[0, 1]`.
- `tool_client_timeout_ms` is the deadline the bundled agent's tool client applies to each request.
- `injected_hold_ms` is measured by Boundary from accepted request arrival using a monotonic clock. Boundary sends no successful response before that interval expires.
- `maximum_activations` is exactly `2`, a defense-in-depth ceiling across the run.
- scenario and contract compatibility are exact allowlists, not ranges.

There is no expression language, probability, time window, payload predicate, wildcard target, user-supplied script, or composition of faults.

### 5.2 Identity, normalization, and digest

`fault_spec_id` is a stable Boundary-assigned identity that resolves to exactly one immutable normalized definition. Reusing that ID with different normalized content is invalid.

Normalization uses the RFC 8785 JSON Canonicalization Scheme after exact schema validation. Domain rules additionally require:

- durations and versions use the declared integer or string forms;
- arrays retain their declared order; and
- unknown or omitted required fields are rejected rather than defaulted during digest calculation.

The fault-definition digest is lowercase hexadecimal SHA-256 over those normalized UTF-8 bytes. Boundary stores or immutably references the normalized bytes and digest. Vulnerable and fixed regression runs must have the same `fault_spec_id`, normalized bytes, and digest. Each receives a different `fault_id`.

### 5.3 Configuration validation

Boundary rejects the run before target invocation unless:

- the schema version and fault kind are exactly supported;
- the target tool equals the scenario's one expected tool;
- `affected_attempts` is exactly `[0, 1]`, contains unique non-negative integers, and is consistent with the trigger rule;
- `maximum_activations` equals `2` and is not greater than the number of affected attempts;
- `tool_client_timeout_ms` and `injected_hold_ms` equal the reviewed scenario values;
- `injected_hold_ms` is at least the client timeout plus the required proof margin;
- the scenario identity/version and contract version are compatible;
- the resolved normalized bytes reproduce the stored digest; and
- the `fault_spec_id` has not previously resolved to different content.

These strict checks intentionally prevent Phase 1 configuration from becoming a general fault framework.

## 6. Tool-call grouping and retry calculation

All valid requests for the one expected tool under a single `run_id` belong to that run's one logical tool operation. No caller-supplied operation identifier is needed.

Boundary computes retry ordinals as follows:

1. Validate the capability and its run, trace, tool, fault, and expiry bindings.
2. Validate the request's exact `run_id`, `trace_id`, expected tool identity, configured `fault_id` presence or absence, and unique non-empty `tool_call_id`.
3. Under a run-scoped atomic registration, reject any reused `tool_call_id`.
4. Assign the next ordinal from the count of previously accepted distinct tool calls for that run and tool:

```text
0 = initial attempt
1 = permitted retry
2 = first disallowed retry
```

5. Persist the arrival and ordinal before evaluating or applying a fault.

The registration transaction or equivalent critical section is the ordering authority for tool arrivals. Wall-clock timestamps are diagnostic only. Every accepted `tool_call_id` is unique across the run, including calls whose fault does not activate.

The supported agent issues calls serially. Concurrent calls are outside the Phase 1 scenario: Boundary still serializes their registration for audit, but marks the run incompatible with the scenario rather than inventing parallel retry semantics.

A target-reported retry ordinal or retry-decision event is corroborating context only. It cannot change Boundary's ordinal. A mismatch is contradictory evidence handled under ADR 001; the Boundary-observed arrival remains authoritative for localization.

## 7. Injection lifecycle

For each request, Boundary performs this lifecycle in order:

1. **Authorize.** Validate the opaque capability without logging it.
2. **Validate identity.** Compare run, trace, tool, and fault identities with the authoritative run record.
3. **Register arrival.** Atomically reserve the unique `tool_call_id`, assign a Boundary event ID and `receipt_seq`, and compute the retry ordinal.
4. **Match.** Compare the target and computed ordinal with the immutable fault definition.
5. **Decide.** Check that the call has no prior activation and the run's activation count is below `maximum_activations`.
6. **Activate or respond.**
   - On activation, durably commit the activation decision. As the first action of the hold, atomically close the successful-response gate, begin withholding the successful result, and durably record `fault_activation_started`.
   - With no configured fault or no match, return the deterministic success response and record it.
7. **Prove or fail to prove the effect.**
   - Compute `client_timeout_boundary = accepted_request_monotonic_time + tool_client_timeout_ms`.
   - If Boundary's monotonic hold reaches or crosses that boundary while the successful-response gate remains closed and no response has been sent, durably record `fault_effect_realized`.
   - An equally authoritative Boundary-owned response-state observation may establish the same fact only if it proves no response was sent before that monotonic boundary.
   - A client disconnect or cancellation before the boundary is recorded, but it does not create `fault_effect_realized`. Boundary may continue the response-state hold independently to the boundary; if cancellation destroys that state or otherwise prevents proof, the effect remains unproven.
8. **Finish bounded work.** If work remains at `injected_hold_ms`, record hold completion and end the request with a bounded injected-timeout response when the connection permits. Never return the normal success payload for a call whose activation began.

For a matching call, the activation decision must commit before `fault_activation_started`; committing `fault_activation_started` establishes that the response gate is closed and the hold has begun. Its monotonic activation-start value must be earlier than the client-timeout boundary. If either commit fails, Boundary does not claim a proven activation. If activation begins at or after the boundary, Boundary cannot attribute the timeout effect to the configured hold and does not record `fault_effect_realized`.

A repeated `tool_call_id` is not another attempt. It is rejected before ordinal allocation or matching, records a duplicate-rejection event linked to the original arrival and any original activation, and can never receive a second activation.

An accepted ordinal `2` is recorded before response behavior. It does not match `[0, 1]`, so Boundary records a no-activation decision with reason `attempt_not_selected`. That single observation is sufficient to locate the vulnerable agent's first unsafe divergence even if the run later exhausts its budget.

## 8. Boundary-owned evidence ledger

The authoritative ledger is append-only and uses ADR 001's Boundary event IDs and `receipt_seq`. At minimum it records:

| Boundary-owned record | Required evidence |
| --- | --- |
| Tool request arrival | Validated identities, target tool, `tool_call_id`, request digest, Boundary event ID, and assigned `receipt_seq`. |
| Retry calculation | The run-scoped grouping rule, computed `retry_ordinal`, and the arrival event used. |
| Fault-spec match | `fault_spec_id`, definition digest, target comparison, ordinal comparison, and match/no-match reason. |
| Activation decision | `fault_id`, `tool_call_id`, prior-activation check, activation count before decision, maximum, and decision reason. |
| `fault_activation_started` | A unique Boundary activation event ID, `fault_id`, `tool_call_id`, retry ordinal, accepted-request monotonic origin, activation-start relationship, client-timeout boundary, and hold deadline. It proves the durable decision and start of withholding, not a realized timeout. |
| `fault_effect_realized` | A unique Boundary effect event ID linked to `fault_activation_started`, the client-timeout boundary, the Boundary monotonic observation that reached or crossed it, and authoritative response-gate state proving that no response was sent before the boundary. |
| Client termination observation | Disconnect or cancellation, its monotonic relationship to the client-timeout boundary, and whether Boundary retained enough response-state authority to evaluate effect realization. |
| Timeout completion | Injected hold completion and any bounded timeout response, linked to activation and effect events. A completion after the boundary is not a substitute for `fault_effect_realized` unless its Boundary-owned state proves the required no-response interval. |
| Tool response | For a non-activated call, deterministic response identity/digest and completion; linked to arrival and decision. |
| Duplicate rejection | Reused `tool_call_id`, original arrival event ID, original activation event ID if any, and deterministic rejection code. |
| Wrong target or identity | Safe mismatch category and digest where useful; no raw credential or unbounded rejected body. |
| Run deadline | Boundary deadline event, monotonic budget relationship, cancellation request, and grace-period result. |

Every call-related record carries or immutably inherits:

```text
run_id
trace_id
fault_spec_id       when configured
fault_id            when configured
tool_call_id
retry_ordinal       after accepted arrival
Boundary event_id
receipt_seq
```

Records also link to preceding Boundary event IDs: match to arrival, decision to match, `fault_activation_started` or response to decision, `fault_effect_realized` to activation start, and timeout completion or client termination to the applicable activation/effect record. Unique activation and effect event IDs distinguish each affected call under the same `fault_id`.

Control records explicitly carry `fault_configured: false`; they do not invent null fault identities. Rejected calls that never pass identity validation receive a rejection event but no retry ordinal and do not join the logical operation.

The evidence needed to prove that an intended timeout was applied and realized is the Boundary-owned chain:

```text
accepted arrival
→ computed ordinal
→ immutable-spec match
→ activation decision
→ fault_activation_started
→ fault_effect_realized
```

The tested agent's timeout exception, retry event, terminal output, disconnect, or cancellation may corroborate the chain but cannot replace either Boundary-owned record. A disconnect or cancellation before the client-timeout boundary proves only `fault_activation_started`. Absence of a trustworthy `fault_activation_started` means Boundary may not claim activation; absence of `fault_effect_realized` means Boundary may not claim that the intended timeout occurred.

### 8.1 Timeout outcome and policy semantics

An injected attempt has complete required injection evidence only when its accepted arrival, match, `fault_activation_started`, and `fault_effect_realized` records form one valid correlation chain. Both ordinals `0` and `1` require complete chains.

The vulnerable scenario may return `FAIL` for the disallowed ordinal `2` only when the two required preceding timeout effects are proven. The fixed scenario may return `PASS` only when both effects are proven, no ordinal `2` exists, and the explicit deterministic degraded result is produced within budget.

If activation begins but effect realization cannot be proven, Boundary does not claim the intended timeout occurred and required injection evidence is `INCOMPLETE`. Contradictory evidence, such as a response-send record before the boundary alongside a claimed realized effect, is `INVALID`. Failure of Boundary's ledger, monotonic clock, response gate, or evaluator is `EXECUTION_ERROR`. These states take precedence over a target timeout report and prevent `PASS` or `FAIL` from being inferred from incomplete fault proof.

## 9. Control-run behavior

A control run uses the same Boundary-owned endpoint, deterministic tool implementation, authorization checks, request schema, grouping rule, and evidence ledger. Its run request and capability bind no `fault_spec_id` and no `fault_id`.

For ordinal `0`, Boundary records:

- accepted tool request arrival;
- computed retry ordinal `0`;
- `fault_configured: false`;
- a no-activation decision with reason `no_fault_configured`; and
- the fixed successful tool response and response digest.

The response is returned well within `tool_client_timeout_ms`. The scenario expects the agent to complete successfully without a retry. Any supplied fault identity on a control capability, or omission of a required fault identity on an injected capability, is an identity mismatch and is rejected; Boundary does not silently convert between control and injected behavior.

This negative control proves that the normal tool path is healthy. An injected-run timeout claim additionally requires Boundary's immutable fault to be configured, `fault_activation_started` to exist, and `fault_effect_realized` to prove the no-response interval.

## 10. Authorization and isolation

Phase 1 uses one high-entropy, opaque bearer capability generated by Boundary for each run. Boundary stores only a cryptographic hash of the capability and an authoritative capability record binding:

- `run_id`;
- `trace_id`;
- expiry;
- exact expected tool identity;
- `fault_id` for an injected run, or an explicit no-fault binding for a control; and
- active run state.

The capability is sent only in the test-run request over the private Compose network and then in an authorization header to the tool endpoint. It is never placed in URLs, events, error bodies, traces, routine logs, or persisted request payloads. Safe capability-record identity may be logged, but not the secret.

The tool URL contains the expected `run_id` as routing context, but the URL is not authorization. Boundary validates the presented capability hash and compares every bound identity with both the URL/request and authoritative run record.

Deterministic rejection rules are:

- missing or unknown capability: unauthorized;
- expired, retired, or post-terminal capability: unauthorized;
- wrong run, trace, tool, or fault binding: forbidden identity mismatch;
- fault identity present for a control or absent/wrong for an injected run: forbidden identity mismatch;
- reused `tool_call_id` under an otherwise valid capability: duplicate-call conflict; and
- a capability presented to another run's endpoint: forbidden cross-run mismatch.

The capability is intentionally multi-use for distinct calls in its one run; the initial call and retry must use the same binding. “Reused authorization” means presentation outside its bound run/tool/fault context, after expiry or retirement, or replay with a reused `tool_call_id`—not legitimate use for another unique call in the same active run.

Docker Compose exposes the Boundary tool listener only on the internal application network, not on a host-published port. The bundled agent can reach Boundary and its required model provider, but the tool endpoint is not reachable from unrelated Compose services by default. Phase 1 runs one campaign serially, and every run receives a fresh capability. Network placement reduces exposure; the capability binding supplies the actual cross-run isolation.

This is local, run-scoped capability authorization, not user authentication, RBAC, enterprise multi-tenancy, or a public credential format.

## 11. Timing and determinism

The reviewed Phase 1 timing values are:

| Timing | Value | Authority and purpose |
| --- | ---: | --- |
| Tool-client timeout | 500 ms | Enforced independently by the bundled agent client for every tool attempt. |
| Injected hold | 1,000 ms | Boundary never returns normal success for an activation; this is twice the client timeout and provides a 500 ms proof margin. |
| Run deadline | 30,000 ms | Boundary-owned monotonic budget covering the complete target execution. |
| Cancellation grace | 2,000 ms | Bounded collection after Boundary requests cancellation. |
| Target polling interval | 100 ms | ADR 001 event/status collection; it does not gate tool arrival or activation recording. |

Boundary computes deadlines from a monotonic clock. Wall-clock timestamps are retained only for diagnostics. The activation-start record is committed before waiting, and the client-timeout boundary precedes the injected hold deadline. If the server observes client disconnect or request cancellation, it records that outcome; it may release connection-specific work, but it must retain an independent Boundary-owned response-state guard through the client-timeout boundary to prove realization. If the connection remains open, the 1,000 ms hold deadline ends the wait and Boundary returns a deterministic timeout response, never the success payload.

More precisely, Boundary records `fault_activation_started` only after the durable decision commit and when it closes the response gate. It records `fault_effect_realized` only after its monotonic clock reaches or crosses `accepted_request_monotonic_time + 500 ms` and its authoritative response state proves no response was sent earlier. Wall-clock timestamps, target timeout reports, and early disconnects cannot satisfy this relationship. Boundary may release connection-specific work after an early disconnect only if an independent Boundary-owned response-state guard continues through the client-timeout boundary; otherwise realization is unproven.

The non-fault stub performs no external I/O and returns its fixed result without intentional delay. A control test asserts ordering and the absence of virtual deadline advancement rather than requiring completion within a fragile sub-millisecond wall-clock threshold.

Automated tests use an injectable monotonic clock and controllable waiter at the decision/hold seam. They advance virtual time and assert that activation start precedes the client-timeout boundary, that effect realization occurs only at or after it with the response gate closed, and that event order is stable without sleeping for exact wall-clock durations. A small number of HTTP integration tests use generous upper bounds and assert inequalities—activation start precedes effect realization, no response send precedes the client-timeout boundary, and the run ends before the run deadline plus cancellation grace—rather than exact elapsed milliseconds.

The fake model and configured real model may choose the initial tool and arguments, but both then enter the same versioned deterministic recovery controller. Model calls are not made between timeout recovery attempts and cannot affect retry count or degraded termination. The real-model demonstration uses the same timing values and remains bounded by the 30-second run deadline plus the 2-second cancellation grace and finite orchestration overhead. A model-provider timeout during initial selection must not exceed the remaining run budget. Provider latency may cause `EXECUTION_ERROR` or `INCOMPLETE`; it may not be relabeled as a successful fault application or scenario verdict.

## 12. Failure modes

| Failure | Deterministic handling |
| --- | --- |
| Unknown, malformed, or incompatible fault definition | Reject before target invocation; no run execution begins. |
| `fault_spec_id` resolves to different content or digest | Reject as immutable-spec conflict. |
| Regression fault ID/digest/configuration differs | Reject before rerun; do not claim an invariant comparison. |
| Source run's `fault_id` is reused | Reject; assign a fresh run-scoped `fault_id` for every injected execution. |
| Missing, expired, retired, or cross-run capability | Reject before tool-call registration; record safe Boundary rejection evidence. |
| Wrong target, run, trace, or fault identity | Reject before ordinal allocation or activation; record identity-mismatch evidence. |
| Reused `tool_call_id` | Reject as duplicate, preserve the original ordinal, and never activate again. |
| Matching call after two activations | Record no activation with `maximum_activations_reached`; never exceed the ceiling. Under the valid Phase 1 spec this is defense in depth because only ordinals `0` and `1` match. |
| Ordinal `2` arrives | Record the authoritative arrival and no-match reason `attempt_not_selected`; localize it as the first unsafe divergence at `retry_control`. |
| `fault_activation_started` commit fails | Do not begin the hold or claim activation; classify the Boundary ledger failure as `EXECUTION_ERROR`. |
| Activation begins at or after the client-timeout boundary | Record activation start if valid, but not effect realization; the configured injection did not prove the intended causal timing, so required injection evidence is `INCOMPLETE` unless a Boundary timing failure requires `EXECUTION_ERROR`. |
| Client disconnect or cancellation occurs before the client-timeout boundary | Record only the termination observation and activation start. Continue an independent response-state guard to the boundary when possible; otherwise do not claim realization and return `INCOMPLETE` unless stronger precedence applies. |
| Response is sent before the client-timeout boundary after activation starts | Do not record effect realization. Treat a simultaneous or later claim of realization as contradictory `INVALID` evidence; otherwise the intended effect is unproven and required injection evidence is `INCOMPLETE`. |
| Monotonic clock, response gate, or effect-ledger commit fails | Do not claim effect realization; classify the Boundary failure as `EXECUTION_ERROR`. |
| Agent reports a timeout without a complete Boundary activation/effect chain | Treat it as untrusted context; required injection evidence is `INCOMPLETE`, unless contradictory evidence requires `INVALID` or a Boundary failure requires `EXECUTION_ERROR`. |
| Model does not select the expected initial tool or supplies scenario-invalid arguments | Do not enter or fabricate the recovery path; classify according to the scenario's execution/evidence rules, never as a proven injected `PASS` or `FAIL`. |
| Model output attempts to change retry, timeout, fault, or degraded-result control | Ignore it as untrusted content. If it enters an authoritative control field, mark the run `INVALID`; deterministic application control remains authoritative. |
| Vulnerable controller does not issue exactly `0`, `1`, `2`, or fixed controller issues ordinal `2` | Treat the observed behavior as a tested-agent/version or scenario-conformance failure; do not attribute it to model discretion. |
| Run deadline occurs after ordinal `2` | Record it as a downstream symptom; it does not replace or move the first unsafe divergence. |
| Run deadline occurs before sufficient evidence | Follow ADR 001: `FAIL` only if a violation is already conclusive, otherwise `INCOMPLETE`. |
| Control tool fails or times out | Do not claim an injection; classify as an operational/control failure and prevent a misleading injected-run comparison. |
| Concurrent tool calls arrive | Serialize registration for audit and mark the run scenario-incompatible; do not infer generalized retry semantics. |

## 13. Security implications

The selected boundary prevents the untrusted agent from authoring activation evidence or choosing its retry ordinal. Exact target and identity binding limits confused-deputy and cross-run use. Per-run capability rotation and retirement limit replay, while internal-only Compose exposure limits accidental access.

The endpoint accepts a small allowlisted JSON body with fixed size limits and rejects unknown fields. Tool arguments are treated as untrusted data and do not select a fault, target, delay, response template, URL, command, or file. Request and response digests support comparison without persisting unnecessary raw content.

Capabilities and model-provider credentials must never enter the evidence ledger. Errors expose safe codes and validated correlation identities only. Rejected credentials are not retained, even in hashed diagnostic form beyond the stored capability hash needed for validation.

This mechanism does not provide hostile multi-tenant isolation, internet-facing authentication, denial-of-service protection, secret distribution, or protection from a compromised Boundary process. Those are outside the local single-user Phase 1 threat model.

## 14. Verification strategy

Before implementation is considered conforming, tests must prove:

1. the canonical example normalizes identically and produces the same digest on repeated evaluation;
2. unknown fields, wrong targets, incompatible scenario/contracts, invalid attempts, invalid timing, invalid activation limits, and digest conflicts are rejected before invocation;
3. vulnerable and fixed runs use identical `fault_spec_id`, normalized definition, and digest but distinct `run_id`, `trace_id`, `fault_id`, and capability;
4. after either fake-model or configured real-model initial tool selection, the same deterministic recovery controller owns retries and terminal behavior;
5. the vulnerable controller deterministically issues distinct calls observed as ordinals `0`, `1`, and `2`, while the fixed controller issues only `0` and `1` and then the explicit degraded result;
6. model output cannot change retry count, tool-client timeout, fault specification, or degraded terminal behavior;
7. ordinals `0` and `1` each receive exactly one independently linked `fault_activation_started` under the same `fault_id`;
8. each activation decision is durably committed before `fault_activation_started`, and each `fault_activation_started` establishes that withholding began before its client-timeout boundary;
9. each `fault_effect_realized` occurs only when Boundary monotonic time reaches or crosses the client-timeout boundary and authoritative response state proves no earlier response was sent;
10. an early client disconnect or cancellation without continued Boundary response-state proof produces activation evidence but no realized-effect evidence;
11. missing realized-effect evidence makes required injection evidence `INCOMPLETE`, while contradictory response/effect evidence is `INVALID` and Boundary proof-system failure is `EXECUTION_ERROR`;
12. ordinal `2` receives no activation and is the authoritative first unsafe divergence at `retry_control`;
13. duplicate delivery of either activated `tool_call_id` is rejected and cannot create another ordinal, activation start, or realized effect;
14. wrong-target, missing, expired, retired, cross-run, wrong-trace, and wrong-fault authorization all fail before ordinal allocation;
15. an uncorroborated target timeout report cannot prove activation or effect realization;
16. the control uses the identical endpoint, returns the fixed success response, and records `no_fault_configured` with zero activations or effects;
17. the vulnerable live execution has two complete activation/effect chains, produces ordinal `2`, localizes the `retry_control` first unsafe divergence, and returns scenario-policy `FAIL`;
18. the fixed live execution has two complete activation/effect chains, no ordinal `2`, an explicit deterministic degraded result within budget, and scenario-policy `PASS`;
19. neither vulnerable `FAIL` nor fixed `PASS` is emitted when either required effect is unproven;
20. budget exhaustion or terminal failure after ordinal `2` remains a downstream symptom;
21. virtual-clock tests require no real sleeps and repeated evidence normalization yields identical order and results;
22. HTTP integration tests terminate within generous bounds derived from the client timeout, injected hold, run deadline, and cancellation grace; and
23. the configured real-model demonstration remains bounded, enters the same deterministic post-selection recovery path, and never treats provider latency as injection proof.

The end-to-end regression verification must start the fixed run from the immutable regression case. It may not reconstruct or edit the fault configuration manually.

## 15. Consequences

Positive consequences:

- Boundary separately proves the start of each activation and whether its intended timeout effect was actually realized.
- The same narrow tool path provides both positive injection and negative-control evidence.
- Two activations under one run-scoped fault are explicit, while per-call at-most-once behavior remains enforceable.
- Retry ordinal `2` is observable without trusting the agent's retry report.
- Retry behavior after initial tool selection is deterministic and identical between fake-model and real-model execution for a given agent version.
- The stub removes external dependency timing from Phase 1 and keeps Compose small.
- Fixed and vulnerable comparisons bind the immutable fault definition while rotating execution identities.

Costs and limitations:

- The Phase 1 timeout is production-shaped at the HTTP tool boundary but does not reproduce packet-level network behavior or a real dependency protocol.
- The agent must call Boundary's endpoint and carry a run-scoped capability.
- The one-operation grouping rule cannot represent multiple tools, interleaved operations, or parallel attempts.
- Boundary must retain an authoritative response-state guard through the client-timeout boundary even when the client disconnects early, or report the required injection evidence as incomplete.
- Real HTTP disconnect observation varies by server/runtime and never proves realization before the client-timeout boundary.
- The model is deliberately excluded from timeout recovery after initial tool selection, so Phase 1 evaluates deterministic application retry control rather than model-directed recovery.
- The chosen timing values may require adjustment only through a new fault-spec version and reviewed regression invariants, not ad hoc per-run changes.

## 16. Reversal or migration path

Keep fault matching, activation accounting, evidence creation, capability validation, and tool behavior behind narrow internal seams. A later proxy may forward non-fault calls to a real dependency while retaining the same Boundary-owned registration and activation ledger. A separate mock service may replace the stub only if Boundary still owns or cryptographically verifies the complete application evidence chain.

Infrastructure injection may be added as a new injector kind only after it can prove exact run, tool call, trigger, at-most-once activation, and negative-control semantics. Agent-side hooks may provide corroborating context but may never become authoritative application proof under the current trust model.

Multiple tools or logical operations require a new grouping decision and fault-spec version. Existing Phase 1 regression cases remain interpretable because they bind the exact normalized definition, digest, scenario, and contract version. Migration must not reinterpret `[0, 1]`, change the meaning of `fault_id`, or recalculate historical ordinals.

## 17. Questions deferred to ADR 003

ADR 003 may decide the next layer while preserving this ADR's semantics:

- the exact assertion identifiers and deterministic analysis representation that cite ordinal `2`, the two complete activation/effect chains, degraded output, and deadlines;
- the physical persistence and transaction boundaries for Boundary events, activation accounting, finalized evidence, analyses, and policy results;
- the exact storage representation of the policy implications fixed here, without changing their `INCOMPLETE`, `INVALID`, and `EXECUTION_ERROR` precedence;
- the exact regression-case integrity envelope and field-by-field invariance evidence presented to analysis and the UI;
- the minimal run-details presentation of injection boundary, first unsafe divergence, downstream symptoms, and supporting Boundary event IDs; and
- safe retention limits for tool arguments, response bodies, request digests, and diagnostic timing data.

ADR 003 must not broaden the fault specification, delegate activation proof to the tested agent, change the Phase 1 grouping rule, or treat wall-clock timestamps as ordering authority.
