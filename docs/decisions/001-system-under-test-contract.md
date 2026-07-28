# ADR 001: Phase 1 system-under-test contract

- **Status:** Accepted
- **Date:** 2026-07-27
- **Scope:** Phase 1 only

## 1. Context

Boundary must execute a real, versioned, tool-using LangGraph sample agent across a normal control run, a vulnerable tool-timeout run, and a fixed-agent rerun created from an immutable regression case. Boundary must retain enough ordered evidence to locate the vulnerable agent's first disallowed retry deterministically.

The tested agent is outside Boundary's trust domain. It can be unavailable, malformed, inconsistent, late, duplicative, or dishonest. Agent-emitted events can describe internal decisions, but they cannot establish that Boundary applied a fault, determine Boundary's policy, or assign Boundary's verdict.

This ADR defines a conceptual interoperability contract. Field names and illustrative HTTP resources are requirements on behavior and information, not application models, database tables, framework adapters, or a public SDK.

## 2. Requirements and constraints

The Phase 1 contract must:

- preserve an external process and network trust boundary;
- run entirely in the documented local Docker Compose environment;
- require no queue, distributed worker, callback receiver, or bidirectional stream;
- retain partial evidence when a target stalls or fails;
- support bounded cancellation and execution deadlines;
- separate Boundary-owned facts from untrusted target reports;
- order evidence without using wall-clock time as the ordering authority;
- make normal, vulnerable, materialization, and fixed-rerun paths auditable;
- keep event meaning independent of the initial transport; and
- avoid a general agent SDK, general telemetry ingestion API, or framework abstraction.

Phase 1 has one serial campaign path, one bundled target, one tool-timeout scenario, bounded event volume, and a deterministic fake model for automated verification. At least one manual demonstration will use a real model.

## 3. Options considered

### 3.1 HTTP request with a synchronous final response

This is superficially the smallest API, but it binds the caller's HTTP deadline to the agent's execution deadline. A lost connection loses partial evidence, cancellation is awkward, and a deliberately vulnerable timeout run can outlive the request. Returning all events only at completion also lets the target rewrite its history before Boundary sees it.

It is unnecessary and harmful in Phase 1 because the required lifecycle, partial evidence, timeout, and cancellation behavior would have to be recreated through ad hoc exceptions. Reconsider it only if measured runs are reliably short and atomic, partial evidence has no diagnostic value, and cancellation is removed as a requirement.

### 3.2 HTTP request that creates a run plus status/event polling

The target accepts a Boundary-identified run, exposes status, and exposes an append-only target-event stream through a cursor. Boundary polls both until it observes a sealed terminal state or applies its own deadline. Boundary records its own invocation, tool, injection, deadline, and receipt evidence directly.

This adds only bounded polling to ordinary HTTP, survives caller request deadlines, preserves partial evidence, and works as two local Docker Compose services without a worker system.

### 3.3 HTTP request plus SSE event streaming

SSE reduces event latency, but Phase 1 does not require live sub-second updates or enough event volume to make polling expensive. SSE adds reconnect cursors, heartbeat policy, buffering behavior, proxy configuration, and stream backpressure while status and cancellation still need ordinary HTTP.

It is unnecessary in Phase 1 and would spend complexity on transport rather than trust semantics. Reconsider it when measured polling load or diagnosis latency violates an explicit target, or when a live UI demonstrably needs low-latency events. Any SSE replacement must preserve the event envelope, producer sequence, final watermark, deduplication, and Boundary receipt sequence defined here.

### 3.4 Local subprocess using a versioned protocol

A subprocess can be small and deterministic, but it couples execution to the host process model and weakens the production-shaped network boundary. Standard streams also require framing, backpressure, cancellation, crash recovery, and log separation. Containerizing the subprocess behind Boundary would still approximate an HTTP service less credibly than invoking one directly.

It is unnecessary in Phase 1 because Docker Compose already provides a simple process and network boundary. Reconsider it for an explicitly offline integration, a target that cannot expose HTTP, or measurements showing HTTP is the dominant local integration burden. The subprocess must implement the same semantic contract rather than introduce a second evidence model.

### 3.5 In-process adapter

An in-process adapter is the least operational work, but it shares memory, dependencies, credentials, and failure modes with Boundary. It makes the target appear more trustworthy than a future customer endpoint and encourages LangGraph-specific types to become an accidental SDK.

It is harmful to the Phase 1 trust claim and is rejected. Reconsider it only when validated users require library-level instrumentation that cannot be expressed externally and isolation is supplied by another reviewed boundary. It must remain an adapter to this contract, not replace core event semantics.

## 4. Decision

Phase 1 will use **HTTP run creation plus status and event polling**.

Boundary invokes the sample agent as a separate local Docker Compose service:

1. Boundary offers and selects one contract version.
2. Boundary sends a test-run request with Boundary-assigned identities and expected target identity.
3. The target returns an accepted/start response promptly.
4. Boundary polls run status and target-produced events with a producer-sequence cursor.
5. The target seals its stream with an immutable final producer-sequence watermark in its terminal status.
6. Boundary independently records receipt, tool-call observation, fault application, deadline, cancellation, analysis, and verdict evidence.
7. Boundary finalizes only after it has collected through the target's watermark or a Boundary-owned evidence deadline expires.

The Phase 1 Boundary process performs this orchestration directly. Polling is bounded by the run budget, uses a fixed small interval with backoff, and does not imply distributed workers.

The illustrative resources are:

```text
POST /test-runs
GET  /test-runs/{run_id}
GET  /test-runs/{run_id}/events?after_producer_seq={n}
POST /test-runs/{run_id}/cancel
```

Resource naming may change during implementation. The fields, authority, state, ordering, and error semantics in this ADR may not change without revisiting the decision.

## 5. Conceptual request, event, status, and error contracts

All wire bodies are UTF-8 JSON objects. Unknown fields are rejected in Phase 1 so misspellings and attempted control-field injection cannot be silently ignored.

### 5.1 Contract-version negotiation

Boundary sends an ordered `Boundary-Contract-Versions` header and encodes the request using its first offered version. Phase 1 offers only `"1"`. The target must either:

- select that exact version and return it in both `Boundary-Contract-Version` and `contract_version`; or
- return `UNSUPPORTED_CONTRACT_VERSION`, including its supported versions.

There is no silent downgrade. If future Boundary supports another schema, it must issue a new, explicitly encoded request. Every response, event, terminal result, persisted run, and regression case records the selected `contract_version`. A version mismatch at any later point makes the evidence `INVALID`.

### 5.2 Test-run request

The accepted request contains:

| Field | Required behavior |
| --- | --- |
| `contract_version` | Offered schema version used to encode this body. |
| `campaign_id` | Boundary-assigned campaign correlation identifier. |
| `scenario_id` / `scenario_version` | Boundary-assigned identity of the exact immutable scenario definition. |
| `run_id` | New Boundary-assigned identity; idempotency key for run creation. |
| `trace_id` | New Boundary-assigned cross-service correlation identity for this run. |
| `tested_agent_id` | Boundary's expected stable logical target identity. |
| `tested_agent_version` | Boundary's expected immutable build/source identity. Tags such as `latest` are not valid. |
| `regression_case_id` | Omitted for an original run; required for a run materialized from a saved regression case. |
| `regression_mode` | Omitted for an original run; exactly `reproduction` or `version_comparison` when `regression_case_id` is present. |
| `tested_input` | Boundary-owned, scenario-derived input. It is data, not instructions to Boundary. |
| `execution_budget_ms` | Positive, bounded Boundary-owned run budget. |
| `tool_endpoint` | Run-scoped Boundary endpoint the target must use for the Phase 1 tool. |
| `fault_spec_id` | Stable Boundary-assigned identity of an immutable fault definition; absent for the control run. The identified normalized definition and its digest remain unchanged across regression runs. |
| `fault_id` | New Boundary-assigned, run-scoped identity for this execution's possible fault application; absent for the control run and never reused by a regression rerun. |

Boundary retains the authoritative fault configuration, expected-behavior assertions, and policy version; they are not target-editable request fields. A repeated `POST` with the same `run_id` and byte-equivalent normalized request is idempotent. Reuse of a `run_id` with different content returns `RUN_CONFLICT`.

For a regression rerun, callers provide `regression_case_id`, `regression_mode`, the proposed `tested_agent_version`, and the current execution's ordinary identities, including a possibly new `campaign_id`. Boundary expands the immutable test-definition values, assigns a new `run_id`, `trace_id`, and `fault_id`, and rejects invariant overrides.

### 5.3 Accepted/start response

A conforming target returns `202 Accepted` promptly with:

- `contract_version`, `run_id`, and `trace_id` echoed exactly;
- `tested_agent_id` and the target's runtime-observed `tested_agent_version`;
- `state: "accepted"`;
- stable status, event, and cancellation resource references; and
- `producer_high_watermark`, initially `0`.

The target does not assign or replace Boundary identities. An identity or target-version mismatch is recorded and the run becomes operationally `invalid`; Boundary does not overwrite the mismatch with its expected value.

### 5.4 Event envelope

The semantic event envelope contains:

| Field | Meaning |
| --- | --- |
| `contract_version` | Selected contract version. |
| `run_id` / `trace_id` | Exact Boundary-assigned run correlations. |
| `event_id` | Stable source-assigned idempotency identity, unique within `(source, run_id)`. |
| `source` | Exactly `sut` or `boundary`. |
| `event_type` | Versioned allowlisted type. |
| `boundary` | Allowlisted semantic boundary, such as `agent`, `retry_control`, `tool_execution`, or `run`. |
| `producer_seq` | For `sut` events, a contiguous target-assigned integer starting at 1; absent for Boundary events. |
| `receipt_seq` | Boundary-assigned immutable integer added when evidence is accepted; absent on the target wire response. |
| `tool_call_id` | Required only for an event about a tool call. |
| `fault_spec_id` | Boundary-added immutable fault-definition identity on fault-related canonical events. |
| `fault_id` | Boundary-added run-scoped application identity, required on Boundary-owned events about this execution's configured or applied fault. |
| `caused_by_event_id` | Optional same-run causal link; never a substitute for sequence validation. |
| `observed_at` | Optional source wall-clock timestamp for diagnostics only. |
| `payload` | Type-specific JSON data, validated and always treated as untrusted when `source` is `sut`. |

Events retrieved from the target event resource are stamped by Boundary as `source: "sut"`. The target cannot create `source: "boundary"` events or use a Boundary-owned event type. A target-supplied `source` field may be omitted; if present, it must be exactly `sut`. A contradictory source or Boundary event-type claim is rejected and makes the evidence `INVALID`. Only Boundary creates and stamps `source: "boundary"` events.

When Boundary accepts an event, it enriches the canonical envelope from its authoritative run record with `campaign_id`, `scenario_id`, `scenario_version`, `tested_agent_id`, expected `tested_agent_version`, and, when applicable, `source_campaign_id`, `fault_spec_id`, the run-scoped `fault_id`, and `regression_case_id`. The target does not author those canonical values on each event.

The Phase 1 target event allowlist is deliberately narrow:

- `sut.run.started`;
- `sut.retry.requested`;
- `sut.degraded_result.produced`;
- `sut.run.completed`;
- `sut.run.failed`; and
- `sut.run.cancelled`.

The minimum Boundary event allowlist is:

- `boundary.run.accepted`;
- `boundary.sut_event.received`;
- `boundary.tool_call.observed`;
- `boundary.tool_result.returned`;
- `boundary.fault.applied`;
- `boundary.cancellation.requested`;
- `boundary.deadline.reached`; and
- `boundary.run.terminal`.

The exact payload schema for every allowlisted type is part of contract version 1. Of particular importance, `sut.retry.requested` reports `retry_ordinal`, the prior `tool_call_id`, and the requested next `tool_call_id`. Boundary does not trust that report alone. The corresponding `boundary.tool_call.observed` records the actual request reaching the Boundary-owned tool boundary and Boundary's computed `retry_ordinal` (`0` for the initial attempt, `1` for the one permitted retry, `2` for the first disallowed retry). For localization, observation of ordinal `2` is classified as the tested agent's `retry_control` action; the timeout application remains separately classified as `tool_execution`. This Boundary observation is the authoritative localization evidence; the target decision event supplies internal context.

### 5.5 Event polling response

An event page contains:

- exact `contract_version`, `run_id`, and `trace_id`;
- `events`, in contiguous increasing `producer_seq` order beginning at `after_producer_seq + 1`, limited only by the explicit page-size limit;
- `producer_high_watermark`, the highest sequence allocated when the page was formed; and
- `next_after_producer_seq`, equal to the last sequence in the page or the supplied cursor when the page is empty.

The target retains immutable events for the life of the Phase 1 run. Events allocated after an earlier poll naturally receive higher sequence values and appear on a later page. Boundary validates and persists a contiguous page before atomically advancing its durable cursor to `next_after_producer_seq`; it never advances across a sequence gap. Retrying the same uncommitted page may return byte-equivalent events and remains idempotent. Once the durable cursor advances, later delivery of a lower or equal producer sequence is invalid. The target may never reuse or mutate a producer sequence or `event_id`.

### 5.6 Run-status response

The status response contains:

- exact `contract_version`, `run_id`, `trace_id`, `tested_agent_id`, and runtime-observed `tested_agent_version`;
- target-reported `state`, exactly `accepted`, `running`, `completed`, `failed`, or `cancelled`;
- current `producer_high_watermark`;
- `final_producer_seq`, present and immutable only when the target reports a terminal state;
- `terminal_result`, present only in a terminal state; and
- an optional safe, bounded target error summary for target-reported failure.

Target status is input to Boundary's state machine, not the authoritative Boundary run status. The target may not report Boundary-owned `timed_out` or `invalid`.

Boundary's normalized status adds its authoritative `campaign_id`, `scenario_id`, scenario version, expected target identity/version, and, when applicable, `source_campaign_id`, `fault_spec_id`, the run-scoped `fault_id`, and `regression_case_id`. Reported and expected target versions remain separate fields so a mismatch cannot be hidden.

### 5.7 Terminal result

The terminal result contains:

- `contract_version`, `run_id`, and `trace_id`;
- `tested_agent_id` and runtime-observed `tested_agent_version`;
- terminal target state: `completed`, `failed`, or `cancelled`;
- `final_producer_seq`;
- an allowlisted `outcome_kind`: `success`, `degraded`, `error`, or `cancelled`;
- bounded `output`, treated as untrusted target content; and
- the `event_id` of the matching `sut.run.completed`, `sut.run.failed`, or `sut.run.cancelled` event.

The status result and referenced terminal event must agree. The terminal result is operational evidence only. The target may not report `PASS`, `FAIL`, `INCOMPLETE`, `INVALID`, or `EXECUTION_ERROR`.

### 5.8 Cancellation behavior

Boundary initiates cancellation with an idempotent request containing `contract_version`, `run_id`, `trace_id`, and a Boundary-generated cancellation identity. The target acknowledges promptly, stops new work, emits any already-determined events, reports `cancelled`, and seals `final_producer_seq`.

Cancellation is best effort but bounded:

- a duplicate request returns the same acknowledgement;
- cancellation of an already terminal run returns the unchanged terminal status with `cancellation_applied: false`;
- Boundary records its cancellation request independently;
- Boundary continues collecting only through a fixed cancellation grace period; and
- failure to acknowledge and seal within that grace period causes Boundary's operational state to become `timed_out`, not falsely `cancelled`.

Cancellation never deletes or rewrites collected evidence.

### 5.9 Error response

All non-success responses use one bounded problem object:

```text
contract_version        selected or requested version, when known
error.code              allowlisted machine code
error.message           safe operator-facing summary
error.retryable         boolean
error.field             optional invalid field name
error.supported_versions  present for version failure
run_id / trace_id       echoed only when validated
```

Phase 1 error codes are `INVALID_REQUEST`, `UNSUPPORTED_CONTRACT_VERSION`, `IDENTITY_MISMATCH`, `RUN_NOT_FOUND`, `RUN_CONFLICT`, `PAYLOAD_TOO_LARGE`, `INVALID_EVENT`, `NOT_CANCELLABLE`, and `INTERNAL_ERROR`. Error details must not include stack traces, secrets, raw model prompts, or raw rejected payloads.

## 6. Lifecycle and ordering rules

### 6.1 Operational lifecycle

Boundary owns the authoritative operational state:

```text
accepted -> running
accepted -> failed | cancelled | timed_out | invalid
running  -> completed | failed | cancelled | timed_out | invalid
```

`completed`, `failed`, `cancelled`, `timed_out`, and `invalid` are terminal and immutable. If the target finishes before the first poll, Boundary records `running` before its terminal transition so the stored lifecycle remains valid.

- `accepted`: target identity and request acknowledgement were validated.
- `running`: execution started or valid run evidence was observed.
- `completed`: the target sealed a completed terminal result and collection reached its watermark.
- `failed`: Boundary could not start, communicate with, or evaluate the execution because of an operational error, or the target sealed a target failure unrelated to an expected scenario result.
- `cancelled`: the target acknowledged Boundary cancellation and sealed its stream in time.
- `timed_out`: Boundary's run or cancellation deadline expired before a qualifying terminal completion.
- `invalid`: incompatible, contradictory, identity-conflicting, or structurally invalid contract evidence was observed before finalization.

The target cannot directly set Boundary's state.

### 6.2 Operational status versus policy result

Operational status answers, "What happened to execution and collection?" Policy result answers, "What does valid evidence prove about this scenario's assertions?"

The policy result vocabulary is exactly:

- `PASS`: sufficient valid evidence proves every gating assertion passed;
- `FAIL`: sufficient valid evidence proves at least one gating assertion failed;
- `INCOMPLETE`: evidence is well-formed but insufficient to decide every required assertion;
- `INVALID`: evidence is contradictory, authority-violating, or contract-incompatible; or
- `EXECUTION_ERROR`: Boundary could not execute or evaluate because of an operational failure.

They are deliberately not one state machine:

- `completed` can yield `PASS`, `FAIL`, or `INCOMPLETE`;
- `timed_out` can yield `FAIL` when Boundary-owned deadline and tool-call evidence conclusively prove a gating violation, otherwise `INCOMPLETE`;
- `cancelled` normally yields `INCOMPLETE`;
- `failed` can yield `FAIL` when a valid target-terminal failure conclusively violates the policy, `EXECUTION_ERROR` when Boundary or transport failure prevents evaluation, or otherwise `INCOMPLETE`;
- `invalid` yields `INVALID`.

`INVALID` takes precedence when contradictory or authority-violating evidence exists. Otherwise, a Boundary execution/evaluation failure produces `EXECUTION_ERROR`, insufficient evidence produces `INCOMPLETE`, and only then may complete assertion evidence produce `PASS` or `FAIL`. A target-provided verdict is ignored as payload and, if supplied in an authoritative field, makes the run `INVALID`.

### 6.3 Ordering

Two orders are stored separately:

1. `producer_seq` preserves the target's claimed local order.
2. `receipt_seq` is assigned transactionally by Boundary to every accepted Boundary or target evidence record in immutable receipt/creation order.

Neither wall-clock timestamp is an ordering authority. Cross-source causality is established only through validated identifiers and Boundary-owned observations. Boundary does not infer causality from timestamps or silently reorder a malformed target page.

The target stream starts at producer sequence 1, is gap-free, and ends at `final_producer_seq`. Localization uses:

- Boundary-owned tool-call observations and Boundary-computed retry ordinals as authoritative evidence of retry requests reaching the tool boundary;
- validated target `producer_seq` to order target-internal decision context; and
- `receipt_seq` to audit when Boundary learned or created each fact.

If two relevant actions cannot be ordered by these rules and correlation identifiers, the assertion is not guessed; the policy result is `INCOMPLETE`, or `INVALID` if the events make incompatible claims.

### 6.4 Duplicates, gaps, lower sequences, and missing events

- An exact repeat of `(source, run_id, event_id, producer_seq, normalized content)` caused by retrying the same page before its cursor advancement is idempotent. Boundary retains one evidence record, increments diagnostic duplicate metadata, and does not allocate another evidence `receipt_seq`.
- Reuse of an `event_id` or `producer_seq` with different normalized content is contradictory and therefore `INVALID`.
- A forward gap leaves the durable cursor at the last contiguous sequence. Boundary retries from that cursor and does not persist later events as ordered evidence until the gap is filled.
- Events allocated after an earlier poll are not late; their higher producer sequences are the normal append-only case.
- Delivery of a producer sequence at or below the already advanced durable cursor, mutation or reuse of a sequence, an event above `final_producer_seq`, a changed final watermark, a decreasing page order, or a changed terminal result is `INVALID`.
- Boundary waits only to a fixed evidence deadline. A forward gap that remains then, an absent terminal event, an absent required correlation, or a missing policy-required event is `INCOMPLETE` if the collected evidence is otherwise compatible.
- A transport or Boundary evaluator failure is `EXECUTION_ERROR`, not merely missing evidence.

Boundary finalizes the evidence set once, then stops polling. Finalized runs are never rewritten by later target state.

## 7. Trust and authority matrix

| Field or evidence | Origin | Boundary treatment |
| --- | --- | --- |
| `campaign_id` | Boundary | Authoritative execution context for the current run; it may differ on a regression rerun. Any target echo must match but is not evidence. |
| `source_campaign_id` | Boundary regression artifact | Immutable provenance naming the source run's campaign; it does not constrain the rerun's `campaign_id`. |
| `scenario_id` / scenario version | Boundary | Authoritative immutable scenario selection; Boundary adds it to canonical evidence. |
| `run_id` | Boundary | Authoritative and unique; target reuse with different content is rejected. |
| `trace_id` | Boundary | Authoritative correlation, never accepted as a replacement from the target. |
| `contract_version` | Negotiated, accepted by Boundary | Exact match required everywhere; mismatch is `INVALID`. |
| `tested_agent_id` | Boundary expectation; target echo | Boundary retains expected and reported values separately; mismatch is `INVALID`. |
| `tested_agent_version` | Boundary expectation; target runtime echo | Must be immutable and exact; mismatch is `INVALID`, never silently overridden. |
| `source` | Boundary | Boundary stamps target-resource events as `sut` and its own events as `boundary`; the target cannot assign Boundary authority. |
| `event_id` | Event source | Boundary assigns it for Boundary events; target assigns it for target events. Uniqueness and immutable reuse are validated per source and run. |
| `producer_seq` | Target | Untrusted claim, accepted only after continuity, uniqueness, and watermark validation. |
| `receipt_seq` | Boundary | Authoritative immutable ingestion/creation order. |
| `tool_call_id` | Target when initiating a call | Boundary validates run-scoped uniqueness and records actual arrival at its tool boundary; conflicting reuse is `INVALID`. |
| Boundary-computed retry ordinal | Boundary | Authoritative from observed correlated tool calls. Target retry ordinals are corroborating context only. |
| `fault_spec_id` and fault-spec digest | Boundary | Stable immutable fault-definition identity and content digest; both remain invariant across regression runs. |
| `fault_id` | Boundary | New authoritative run-scoped application identity for each fault-enabled execution; target may echo but cannot create, reuse, or mark it applied. |
| Fault configuration and application proof | Boundary | Authoritative; only a Boundary-owned injector observation can prove application. |
| `regression_case_id` | Boundary | Stable immutable artifact identity; target can only echo it. |
| Tested input | Boundary scenario/regression case | Authoritative exact value or immutable reference. Target output cannot modify it. |
| Agent decisions and internal transitions | Target | Untrusted evidence, schema-validated and correlated with Boundary observations where required. |
| Tool request arrival and returned tool result | Boundary tool boundary | Authoritative external observation. |
| Target output and error text | Target | Untrusted display data; never control, policy, or injection evidence. |
| Operational state | Boundary | Computed from validated target reports plus Boundary observations and deadlines. |
| Assertions, policy version, and policy result | Boundary | Authoritative, deterministic, and never delegated to the target. |
| Wall-clock timestamps | Either source | Diagnostic only. |

Boundary overrides no conflicting value silently. It stores the expected value and safe mismatch metadata, then classifies the evidence as `INVALID`.

## 8. Regression-rerun behavior

Boundary may materialize a Phase 1 regression case only from a finalized vulnerable run whose policy result is `FAIL` and whose evidence used for that result is complete and valid. Boundary assigns `regression_case_id` and freezes values directly or through immutable, content-addressed references.

The artifact contains:

- `source_campaign_id`, preserving the source run's campaign as provenance;
- source `run_id`, `trace_id`, and original `tested_agent_version`;
- `contract_version`;
- `scenario_id` and scenario version;
- canonical tested input;
- `fault_spec_id`, the canonical fault configuration, and its immutable digest;
- the source run's `fault_id` as application provenance, not as a rerun invariant;
- canonical expected-behavior assertions;
- policy version; and
- supporting evidence references and their integrity digests.

A rerun declares its provenance through required `regression_case_id` and `regression_mode` in the Boundary run-creation command and the expanded target request. Boundary creates a new `run_id`, `trace_id`, and run-scoped `fault_id`; it never reopens the source run. The rerun may use a new `campaign_id`, while the artifact retains `source_campaign_id`.

Before invocation, Boundary canonicalizes and compares immutable values or their digests. The following must be byte-equivalent after canonicalization:

- contract version;
- scenario identity and version;
- tested input;
- `fault_spec_id`, normalized fault configuration, and fault-spec digest;
- expected-behavior assertions; and
- policy version.

The `tested_agent_id` must also remain the same. `campaign_id` is execution context and may change. `fault_id` identifies one run's fault application and must change. New `run_id`, `trace_id`, event identities, receipt sequences, actual execution evidence, and timestamps are also expected new run facts, not changed test definitions.

Phase 1 supports two explicit rerun modes:

- **Reproduction rerun:** the same `tested_agent_version` is permitted. Boundary reports a reproduction result and makes no vulnerable-versus-fixed version-comparison claim.
- **Version comparison:** the proposed `tested_agent_version` must differ from the source version before Boundary may claim a vulnerable-versus-fixed comparison.

Either mode may execute a different version, but only `version_comparison` with distinct versions authorizes comparison language. Both modes preserve every invariant above.

Any attempted invariant override is rejected before execution. Reuse of the source `fault_id` is rejected because each execution requires a fresh application identity. A runtime target-version mismatch makes the rerun `INVALID`. The invariance comparison, mode check, and field-by-field result are Boundary-owned evidence linked to both runs. Every rerun must execute the agent; it cannot substitute the source evidence or a stored fixture.

## 9. Failure modes

| Failure | Deterministic handling |
| --- | --- |
| No compatible contract version | Reject before acceptance with `UNSUPPORTED_CONTRACT_VERSION`. |
| Target unavailable or creation request exhausts retries | Operational `failed`; policy `EXECUTION_ERROR`. |
| Ambiguous creation response after connection loss | Retry the same `run_id`; equivalent request is idempotent, different content conflicts. |
| Target identity or version mismatch | Operational `invalid`; policy `INVALID`. |
| Poll temporarily fails | Retry within the Boundary run budget; retain prior evidence. |
| Target crashes after acceptance | Operational `failed`; policy `EXECUTION_ERROR`, unless already contradictory evidence requires `INVALID`. |
| Run exceeds the scenario budget | Boundary records `deadline.reached`, requests cancellation, and sets `timed_out`; policy is conclusive `FAIL` only when the assertions are provably violated, otherwise `INCOMPLETE`. |
| Cancellation is ignored | End the grace period as `timed_out`; retain evidence. |
| Exact duplicate from retrying an uncommitted page | Deduplicate without changing evidence order or advancing past an unvalidated page; retain diagnostic count. |
| Forward producer-sequence gap | Keep the durable cursor at the last contiguous sequence and retry; if the gap remains at the evidence deadline, policy `INCOMPLETE` unless stronger conditions apply. |
| Lower sequence after cursor advancement, conflicting duplicate, source-authority claim, changed watermark, or changed terminal result | Operational `invalid`; policy `INVALID`. |
| Unknown event type, invalid payload, oversized payload, wrong run identity, or target-claimed Boundary event | Reject/quarantine the event and mark the run `invalid`; policy `INVALID`. |
| Target reports fault application or a policy verdict | Treat as untrusted payload; if placed in an authoritative field, mark `INVALID`. |
| Boundary evaluator fails on otherwise collected evidence | Operational `failed`; policy `EXECUTION_ERROR`. |
| `fault_spec_id`, fault-spec digest, or another regression invariant differs | Reject before invocation; no rerun is created. |
| Source `fault_id` is reused for a rerun | Reject before invocation; each fault-enabled execution requires a new run-scoped application identity. |
| Same agent version requested for `reproduction` | Permit the rerun and make no version-comparison claim. |
| Same agent version requested for `version_comparison` | Reject the comparison before invocation; no vulnerable-versus-fixed claim is created. |
| Rerun uses a new `campaign_id` | Permit it and retain the original as `source_campaign_id` provenance. |

## 10. Security and data-handling implications

Phase 1 accepts only the allowlisted event types and exact versioned payload schemas. Limits are fixed at 64 KiB per encoded event, 256 target events per run, and 1 MiB total accepted target event data per run. Terminal output shares the 64 KiB single-object limit. Exceeding a limit returns or records `PAYLOAD_TOO_LARGE` and makes the run `INVALID`; Boundary does not truncate evidence and then evaluate it as complete.

The target's network access in Compose should be limited to the run-scoped Boundary tool endpoint and required model provider access. Run-scoped tool authorization must bind `run_id`, `trace_id`, the run-scoped `fault_id` when present, and expiry; its exact mechanism is deferred to the fault-injection ADR.

Untrusted values are:

- stored as data, never executed or interpolated into commands;
- excluded from authoritative identifier, scenario, fault, assertion, and policy fields;
- escaped on output and displayed as labeled plain text by default;
- never rendered as raw HTML, active Markdown, images, or automatic links;
- omitted from error bodies and routine logs unless safely bounded and escaped; and
- prevented from supplying UI labels, navigation targets, or verdict text.

Rejected oversized or unparsable bodies are represented by Boundary-owned metadata such as byte count and digest, not persisted wholesale. Secrets, credentials, prompts, and model output retention require a separate reviewed retention policy before any external target is supported.

## 11. Verification strategy

Before implementation is considered conforming, contract tests must cover:

1. version selection and unsupported-version rejection;
2. valid control, vulnerable, reproduction-rerun, and version-comparison requests;
3. idempotent run creation and conflicting `run_id` reuse;
4. wrong run, trace, target, version, contract, tool-call, and fault identities;
5. accepted, running, every terminal operational state, and illegal transitions;
6. contiguous events, equal timestamps, uncommitted-page retries, conflicting duplicates, forward gaps, lower sequences after cursor advancement, changed watermarks, and events beyond the watermark;
7. allowlisted types, unknown types, malformed payloads, per-event limits, event-count limits, and total payload limits;
8. cancellation before start, while running, after completion, duplicate cancellation, and ignored cancellation;
9. Boundary stamping target-resource events as `sut`, rejection of target-claimed Boundary sources/types, and authoritative Boundary tool and fault events versus contradictory target claims;
10. deterministic first-disallowed-retry localization using Boundary-observed retry ordinal `2`;
11. all five policy results and their precedence, including conclusive timeout failure versus insufficient timeout evidence;
12. regression materialization only from valid finalized `FAIL`;
13. stable `fault_spec_id` and digest across reruns, a fresh run-scoped `fault_id` per execution, and rejection of reused or mismatched fault identities;
14. a reproduction rerun that permits the same version, a version comparison that rejects the same version and accepts a distinct version, and runtime-version mismatch;
15. a rerun under a new `campaign_id` that retains `source_campaign_id` provenance;
16. contiguous page-size pagination, atomic durable-cursor advancement, no advancement across a forward gap, naturally appended higher sequences, invalid lower sequences after advancement, and deadline `INCOMPLETE` for a remaining compatible gap; and
17. safe display of HTML, Markdown, control characters, very long strings, and prompt-injection-shaped target content.

Repeated evaluation of the same normalized evidence must produce identical ordering, localization, and policy results. An end-to-end Compose test must kill or stall the target mid-run and prove that Boundary retains partial evidence and terminates within its own deadline.

## 12. Consequences

Positive consequences:

- The sample agent is a credible external target without requiring remote infrastructure.
- Boundary retains partial evidence and controls its own deadlines, identities, injection proof, and verdict.
- Polling and single-process orchestration keep the Compose demonstration small.
- Producer order, Boundary receipt order, and cross-source authority are explicit.
- Event semantics can survive a future change to SSE, callback ingestion, or subprocess framing.

Costs and limitations:

- Polling adds bounded latency and repeated reads.
- The target must retain an append-only event stream until Boundary finalizes.
- Target-internal decision events remain untrusted; authoritative localization depends on observable tool-boundary behavior.
- The strict event allowlist supports the bundled Phase 1 agent, not arbitrary workflows.
- This ADR does not prove that external users will accept the instrumentation burden.

## 13. Reversal or migration path

Keep invocation, target-event collection, Boundary event recording, evidence normalization, and policy evaluation behind separate internal seams. The normalized event envelope and lifecycle enter analysis only after validation, so transport-specific cursors and HTTP resource references do not enter policy logic.

A future transport must demonstrate equivalent:

- exact contract-version selection;
- Boundary-owned run and trust identities;
- target producer sequence and immutable final watermark;
- Boundary receipt sequence;
- idempotent duplicate behavior;
- bounded cancellation and deadline handling; and
- unchanged regression and policy semantics.

SSE can replace polling by using `producer_seq` as its resume cursor. A subprocess can frame the same request, event pages, status, cancellation, and error objects. An in-process adapter can implement the same port only if another reviewed isolation boundary exists. Existing regression cases remain valid because they bind semantic `contract_version`, not a transport name.

## 14. Questions deferred to the fault-injection ADR

The following are deliberately not decided here:

- whether the Boundary-owned tool boundary is a stub/proxy, a wrapper around a real tool client, or a narrow sample-only adapter;
- the exact timeout duration, run budget, cancellation grace period, polling interval, and deterministic trigger;
- how logical tool operations are grouped so Boundary computes retry ordinals across distinct `tool_call_id` values;
- how the control response and injected-timeout response are produced without model-provider timing becoming authoritative;
- the exact run-scoped tool credential and endpoint-isolation mechanism;
- the exact Boundary-owned injection ledger payload and its links to immutable `fault_spec_id`, run-scoped `fault_id`, `tool_call_id`, trigger, target, single-application proof, and negative control; and
- how an attempted wrong-target or duplicate fault application is prevented and evidenced.

That ADR must preserve the authority, event, ordering, lifecycle, regression, and failure-classification rules established here.
