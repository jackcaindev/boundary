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
networks. The critical Task 3 test uses real HTTP between the Boundary
test container and the separate sample-agent container.

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
