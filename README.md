Given an instrumented agent run, Boundary can inject a controlled production failure, capture ordered evidence, identify the tested agent’s first unsafe divergence (the first failing boundary), materialize an immutable reproducible regression case, and determine whether the agent passes an explicit scenario policy.

## Canonical fault-definition kernel

Prerequisites: Python 3.12 or newer and `uv`.

From the repository root:

```console
cd backend
uv sync --locked
uv run --locked pytest
```

The published Phase 1 fixture is
`backend/tests/fixtures/fault-spec-v1.json`. Its RFC 8785 canonical UTF-8
bytes have this lowercase SHA-256 digest:

```text
13c5a1d3a7ebe65a9fc2a4c834a216c32839239e77ee8d4e7f6aad711452e1ba
```
