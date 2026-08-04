# Boundary

Given an instrumented agent run, Boundary injects one controlled production-shaped failure, preserves ordered evidence, identifies the tested agent's first unsafe divergence, materializes an immutable regression case, and determines whether a fixed tested-agent version passes the same scenario policy.

Phase 1 is a local single-user portfolio demonstration. It is not a production deployment.

## Prerequisites

- Docker Desktop or another Docker Engine with Compose v2
- Python 3.12 or newer and `uv`
- Node.js 24 and npm
- Chromium installed by Playwright, or a local Chromium/Chrome executable

All Python and JavaScript dependencies are locked. `MODEL_MODE=fake` is the safe default in both [`.env.example`](.env.example) and Compose. Do not put provider keys in `.env`, command history, logs, or tracked files.

## Clean fake-model demonstration

The following command is intentionally destructive: it deletes only this repository's Compose containers, networks, and named PostgreSQL volume before rebuilding and verifying the complete workflow.

```console
./scripts/verification/verify_clean_compose.sh --reset
```

If Playwright-managed Chromium is unavailable but Chrome is already installed, provide its executable explicitly, for example on macOS:

```console
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  ./scripts/verification/verify_clean_compose.sh --reset
```

The verifier proves that a new volume migrates to Alembic head `0007_executor_public_api`; PostgreSQL, Boundary, the sample agent, and frontend become healthy; only `127.0.0.1:5173` is host-published; public `/api` reaches Boundary; `/internal` and `/internal/*` return 404; and the browser journey passes.

For a non-destructive normal start:

```console
docker compose up --build --detach
docker compose ps
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). In the UI:

1. Start the bundled vulnerable campaign.
2. Inspect the successful control and injected `FAIL` run.
3. Inspect ordered evidence, the injection boundary, first unsafe divergence, and downstream symptoms.
4. Open the immutable regression case and start the `fixed-v1` comparison.
5. Inspect the fresh fixed control and fixed injected `PASS` run.
6. Open the completed invariance report and confirm exactly: `The fixed tested-agent version passes this scenario policy.`

All browser navigation and polling uses public frontend routes. The browser test does not access PostgreSQL, the data network, or the private tool route and does not recompute verdicts.

## Task 10 verification commands

With the fake stack running:

```console
cd tests/e2e
npm ci
npx playwright install chromium
npm run typecheck
npm test
```

Use the same `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` override shown above to use an existing browser instead of downloading one.

Run ten independent public fake-model demonstrations, recording every attempt without retries:

```console
python3 scripts/verification/public_workflow.py \
  --attempts 10 \
  --output docs/verification/generated/fake-reliability.json
```

The command succeeds only for exactly ten attempts with at least nine complete successes. Each attempt uses fresh campaign and idempotency identities and records safe campaign, run, regression, rerun, and comparison IDs plus duration and failure reason.

Physically interrupt Boundary during an unproven injected activation and verify conservative restart reconciliation:

```console
python3 scripts/verification/verify_physical_interruption.py \
  --output docs/verification/generated/physical-interruption.json
```

This test uses the public frontend API to observe work and evidence, sends `SIGKILL` to the Boundary container, restarts it, and verifies retained partial evidence, `RUNTIME_LOST_UNPROVEN`, `EXECUTION_ERROR`, and no fabricated `fault_effect_realized`. Deterministic failpoint tests continue to own the finer crash-position matrix.

## Local and Compose test suites

Local locked checks:

```console
cd backend
UV_CACHE_DIR=/tmp/uv-cache uv run --locked pytest tests/unit tests/contract
cd ../sample-agent
UV_CACHE_DIR=/tmp/uv-cache uv run --locked pytest
cd ../frontend
npm ci
npm test
npm run typecheck
npm run build
cd ../tests/e2e
npm ci
npm run typecheck
```

The Python projects do not currently configure a separate formatter, linter, or static type checker.

Migration-backed suites downgrade and rebuild the schema. Run them from an explicitly empty local volume; migration `0007` refuses to discard existing Task 8 lifecycle state.

```console
docker compose down --volumes --remove-orphans

docker compose --profile test run --build --rm integration-tests \
  /app/backend/.venv/bin/pytest \
  tests/integration/test_task5_compose.py \
  tests/integration/test_task5_timeout_proof.py

docker compose --profile test run --build --rm integration-tests \
  /app/backend/.venv/bin/pytest \
  tests/integration/test_task6_finalization_analysis.py

docker compose --profile test run --build --rm integration-tests \
  /app/backend/.venv/bin/pytest \
  tests/integration/test_task7_regression_comparison.py

docker compose --profile test run --build --rm integration-tests \
  /app/backend/.venv/bin/pytest \
  tests/integration/test_task8_api_executor.py \
  tests/integration/test_task8_reconciliation.py

docker compose --profile test run --build --rm integration-tests
docker compose --profile test run --build --rm sample-agent-tests
docker compose config --quiet
docker compose -f compose.yaml -f docker/compose.real-model.yaml config --quiet
git diff --check
```

## Explicit real-model demonstration

Automated tests and reliability runs remain fake-only. The real adapter lets OpenAI select only the initial reviewed `boundary.phase1.lookup` tool and its arguments. Boundary still owns retries, fault realization, evidence, assertions, localization, and verdicts. Provider output is validated as untrusted input, and provider failure becomes a bounded safe target failure.

Boundary and the sample-agent do not persist raw provider prompts or responses in PostgreSQL, evidence, application logs, or verification records. The Responses API request uses `store=false` to disable response application state. Provider-side abuse-monitoring retention remains governed by the OpenAI organization/project data-control policy and may apply unless approved data controls such as Zero Data Retention (ZDR) or Modified Abuse Monitoring are enabled. The bundled real-model demonstration must use only reviewed, non-secret demo input.

Create the ignored local secret file `.secrets/openai_api_key` without printing the value, restrict its permissions, and then start the explicit override:

```console
mkdir -p .secrets
chmod 700 .secrets
# Write the credential to .secrets/openai_api_key using a non-echoing editor or secret manager.
chmod 600 .secrets/openai_api_key

docker compose -f compose.yaml -f docker/compose.real-model.yaml \
  up --build --detach
```

Only the sample-agent receives the Compose secret and provider-egress network. Boundary, PostgreSQL, and the frontend receive neither. Confirm the safe adapter identity without displaying a credential:

```console
docker compose -f compose.yaml -f docker/compose.real-model.yaml exec -T sample-agent \
  /app/sample-agent/.venv/bin/python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health').read().decode())"
```

Then run the complete UI workflow at [http://127.0.0.1:5173](http://127.0.0.1:5173), or run the Playwright UI journey with `npm test` from `tests/e2e`. Preserve the emitted IDs and inspect the vulnerable `vulnerable-v1` versus fixed `fixed-v1` controller versions. Create `docs/verification/real-model-demonstration.md` only after the full real run succeeds. Never commit the secret or raw provider content.

## Shutdown and reset

Normal shutdown preserves local PostgreSQL data:

```console
docker compose down
```

Explicit local reset deletes only the Boundary Compose volume:

```console
docker compose down --volumes --remove-orphans
```

## Security and deployment limitations

Phase 1 has no authentication, RBAC, multi-tenancy, external secret manager, TLS termination, rate limiting, distributed queue, multi-replica coordination, or production observability stack. It intentionally runs one Boundary process and one ASGI worker. PostgreSQL, Boundary, the sample agent, and the internal tool route are private Compose services; only the localhost frontend is published. Capabilities are short-lived bearer credentials but this local portfolio topology is not an internet-facing security boundary.

The real-model path depends on external provider availability, account policy, model access, and spend. `MODEL_REQUEST_TIMEOUT_MS` is bounded to 10 seconds, but the complete run remains subject to the 30-second Boundary run budget. Phase 1 cannot be called portfolio-complete until a real-model UI demonstration has actually succeeded and been audited.
