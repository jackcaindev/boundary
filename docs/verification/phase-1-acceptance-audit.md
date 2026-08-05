# Phase 1 acceptance audit

- Date: 2026-08-05
- Base commit: `d3cb2bcccf7e50359c7319ba8d2109e5e9d652f5`
- Worktree: uncommitted provider-failure diagnostic hardening and final-verification documentation and metadata changes; no commit or push performed

## Executed checks

| Check | Exact command | Result |
| --- | --- | --- |
| Backend unit/contract | `cd backend && UV_CACHE_DIR=/tmp/uv-cache uv run --locked pytest tests/unit tests/contract` | 121 passed |
| Sample-agent local | `cd sample-agent && UV_CACHE_DIR=/tmp/uv-cache uv run --locked pytest` | 31 passed |
| Post-diagnostic sample-agent local | `cd sample-agent && UV_CACHE_DIR=/tmp/uv-cache uv run --locked pytest` | 52 passed |
| Frontend | `cd frontend && npm test && npm run typecheck && npm run build` | 30 passed; typecheck/build passed |
| E2E typecheck | `cd tests/e2e && npm run typecheck` | passed |
| Compose configs | `docker compose config --quiet` and `docker compose -f compose.yaml -f docker/compose.real-model.yaml config --quiet` | passed |
| Real-model attempt 4 | `cd tests/e2e && PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' PLAYWRIGHT_HTML_OUTPUT_DIR=playwright-report-real-model-attempt-4 npm test -- critical-journey.spec.ts --retries=0 --workers=1 --repeat-each=1 --output=test-results-real-model-attempt-4` | 1/1 passed; labeled artifacts and independent authoritative audit preserved |
| Clean start/browser | `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' ./scripts/verification/verify_clean_compose.sh --reset` | passed |
| Physical interruption | `python3 scripts/verification/verify_physical_interruption.py --output docs/verification/generated/physical-interruption-attempt-2.json` | passed |
| Fake reliability | `python3 scripts/verification/public_workflow.py --attempts 10 --output docs/verification/generated/fake-reliability.json` | 10/10 passed |
| Task 5 HTTP/proof | `docker compose --profile test run --build --rm integration-tests /app/backend/.venv/bin/pytest tests/integration/test_task5_compose.py tests/integration/test_task5_timeout_proof.py` | 25 passed |
| Task 6 | `docker compose --profile test run --build --rm integration-tests /app/backend/.venv/bin/pytest tests/integration/test_task6_finalization_analysis.py` | 5 passed |
| Task 7 | `docker compose --profile test run --build --rm integration-tests /app/backend/.venv/bin/pytest tests/integration/test_task7_regression_comparison.py` | 5 passed |
| Task 8 final | `docker compose --profile test run --build --rm integration-tests /app/backend/.venv/bin/pytest tests/integration/test_task8_api_executor.py tests/integration/test_task8_reconciliation.py` | 22 passed |
| Full Compose backend | `docker compose --profile test run --build --rm integration-tests` | 252 passed in 75.52 s |
| Full Compose sample-agent | `docker compose --profile test run --build --rm sample-agent-tests` | 31 passed |
| Post-diagnostic Compose sample-agent | `docker compose --profile test run --build --rm sample-agent-tests` | 52 passed |
| Script syntax | `sh -n scripts/verification/verify_clean_compose.sh` and `python3 -m py_compile scripts/verification/public_workflow.py scripts/verification/verify_physical_interruption.py` | passed |
| Whitespace | `git diff --check` | passed |

The Python projects own no separate formatter, linter, or static type-checker command.

## Passed acceptance criteria

- Reproducible empty-volume migration and health verification.
- Frontend-only localhost publication; private Boundary, PostgreSQL, sample-agent, and internal tool route.
- Complete browser-level vulnerable-to-fixed UI journey over public routes, bounded polling, useful safe diagnostics, ID capture, and route refresh.
- Vulnerable `FAIL`, immutable regression case, fresh fixed control/injected execution, fixed `PASS`, valid invariance comparison, and exact scoped conclusion.
- Physical Boundary process loss retains partial evidence, reports conservative ambiguity, and never promotes an unproven effect.
- Ten independent fake demonstrations pass 10/10 with every attempt recorded and no retries.
- Explicit fake/default and real/OpenAI adapter separation; provider controls only initial reviewed tool arguments; strict untrusted validation; 10-second maximum provider request; no raw provider content retention.
- Local ignored secret path and explicit Compose override place provider credential/egress only on sample-agent.
- One configured real-model browser demonstration passed with `openai/gpt-5.6-luna`; a separate public-resource audit proved vulnerable `FAIL`, exactly two realized timeout effects, ordinal-2 divergence, immutable regression materialization, fresh fixed execution, fixed `PASS`, no fixed ordinal 2, no invariant mismatch, and the exact scoped conclusion. See [real-model-demonstration.md](real-model-demonstration.md).
- Existing deterministic failpoint suites remain intact and a regression now covers cancellation after finalized evidence but before analysis.
- Full backend, sample-agent, frontend, browser type, config, and Compose suites pass.

## Failed attempts retained and diagnosed

- Initial clean/browser development failures are enumerated in `clean-start-browser.md`.
- Physical attempt 1 missed the unproven window and correctly failed its narrow gate; attempt 2 passed.
- The first focused Task 5 command produced 25 setup errors because migration `0007` refused to downgrade a database containing prior Task 8 verification state. An explicit scoped volume reset was performed; the clean rerun passed 25/25.
- Task 8 initially failed 2/21 (control campaigns ended failed under the fixed 500 ms boundary). A transparent rerun failed 1/21 on the finalized-before-analysis cancellation race. The race was fixed, a deterministic failpoint regression was added, the focused pair passed 2/2, the full focused suite passed 22/22, and the final full Compose suite passed 252/252.
- Initial host Python commands could not use the sandboxed default uv cache; the same locked commands passed with `UV_CACHE_DIR=/tmp/uv-cache`.
- Real-model attempt 3 ended in `LocalProtocolError` because the credential file was malformed. Attempt 4 was authorized only after independent validation of the corrected ignored, untracked, mode-600 credential. Attempt 4 ran once and passed; it was not retried.

## Real-model acceptance criterion

The previously missing configured real-model UI demonstration is now verified by [real-model-demonstration.md](real-model-demonstration.md). Both Compose configurations, all service health checks, the exact model identity, bounded DNS/TCP/TLS connectivity without HTTP, and frontend-only host publication passed before the single browser journey. The journey passed once, and all captured authoritative resources were then retrieved and checked independently through read-only public routes.

## Remaining uncertainty and limitations

This remains a localhost, single-process, single-worker, unauthenticated portfolio system. It has no TLS, RBAC, tenancy, distributed queue, multi-replica coordination, production secret manager, rate limiting, or production observability. Physical interruption covered Boundary only. Browser installation through the network was not proven; installed Chrome was used. One successful provider-backed run proves the configured integration path once; it does not establish continuing provider availability, account policy, model access, spend characteristics, deterministic model behavior, or production reliability.

## Acceptance decision

All required fake-model, browser, routing, interruption, reliability, repository, immutable-regression, comparison, Compose, PostgreSQL-authority, and configured real-model demonstration gates are evidenced and pass. Phase 1 is genuinely portfolio-complete under the Phase 1 acceptance definition.
