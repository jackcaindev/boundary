Given an instrumented agent run, Boundary can inject a controlled production failure, capture ordered evidence, identify the tested agent’s first unsafe divergence (the first failing boundary), materialize an immutable reproducible regression case, and determine whether the agent passes an explicit scenario policy.

## Verification

Prerequisites: Python 3.12 or newer and `uv`.

### Unit tests

From the repository root:

```console
cd backend
uv run --locked pytest tests/unit tests/contract
cd ../sample-agent
uv run --locked pytest
```

### Full PostgreSQL verification

From the repository root:

```console
docker compose --profile test run --build --rm integration-tests
```

This starts PostgreSQL, waits for the one-shot migration service to
complete, starts the unexposed sample-agent service, and executes the
locked Boundary suite across the private Compose application and data
networks. The Task 5–7 execution tests use real HTTP from the Boundary test
container to the separate sample-agent container and back to Boundary's
unpublished private tool route. They include the vulnerable `FAIL`, immutable
regression-case materialization, fresh `fixed-v1` control and injected runs,
and the sealed scenario-scoped `PASS` comparison.

The sample-agent image can be verified independently:

```console
docker compose --profile test run --build --rm sample-agent-tests
```

After verification, stop the Compose services without deleting their
data:

```console
docker compose down
```

## Canonical fault-definition kernel

The published Phase 1 fixture is
`backend/tests/fixtures/fault-spec-v1.json`. Its RFC 8785 canonical UTF-8
bytes have this lowercase SHA-256 digest:

```text
13c5a1d3a7ebe65a9fc2a4c834a216c32839239e77ee8d4e7f6aad711452e1ba
```

## Task 8 headless API

Boundary runs one PostgreSQL-backed serial executor inside the single FastAPI
process. Public mutations commit before returning and require an
`Idempotency-Key`; accepted execution continues asynchronously.

The Phase 1 workflow starts with:

```console
curl -i -X POST \
  -H 'Idempotency-Key: bundled-demo-1' \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://boundary:8000/api/v1/campaigns/bundled-tool-timeout
```

Poll the returned campaign URL, inspect its run and ordered evidence links,
then submit `{"mode":"version_comparison","tested_agent_version":"fixed-v1"}`
to the returned regression case's `/reruns` endpoint with a new idempotency
key. Liveness is `/health/live`; readiness is `/health/ready` and requires the
`0007_executor_public_api` migration, PostgreSQL, completed startup
reconciliation, valid immutable timing configuration, and a running executor.

The supported Boundary settings are `DATABASE_URL`, `SUT_BASE_URL`,
`BOUNDARY_INTERNAL_BASE_URL`, `RUN_DEADLINE_MS`, `CANCELLATION_GRACE_MS`,
`TARGET_POLL_INTERVAL_MS`, `TOOL_CLIENT_TIMEOUT_MS`, `INJECTED_HOLD_MS`,
`MAX_EVENT_BYTES`, `MAX_TARGET_EVENTS`, `MAX_TARGET_EVENT_BYTES`, and
`LOG_LEVEL`. Phase 1 timing and evidence-limit values must exactly match the
reviewed scenario; changing them makes Boundary unready. Run exactly one
Boundary process and one ASGI worker.

Migration `0007_executor_public_api` may be downgraded only when no Task 8
executor-managed campaign, cancellation/reconciliation evidence cutoff, or
ambiguous nonterminal execution checkpoint exists. The downgrade removes
Task 8-only non-campaign idempotency mappings before restoring Task 7
constraints and fails explicitly rather than discarding durable lifecycle or
immutable evidence state.
