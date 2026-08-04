# Phase 1 acceptance audit

- Date: 2026-08-04
- Base commit: `f5ffafa4a0afe75fdacab0327425b97bd9eaff97`
- Worktree: uncommitted Task 10 implementation; no commit or push performed

## Executed checks

| Check | Exact command | Result |
| --- | --- | --- |
| Backend unit/contract | `cd backend && UV_CACHE_DIR=/tmp/uv-cache uv run --locked pytest tests/unit tests/contract` | 121 passed |
| Sample-agent local | `cd sample-agent && UV_CACHE_DIR=/tmp/uv-cache uv run --locked pytest` | 31 passed |
| Frontend | `cd frontend && npm test && npm run typecheck && npm run build` | 30 passed; typecheck/build passed |
| E2E typecheck | `cd tests/e2e && npm run typecheck` | passed |
| Compose configs | `docker compose config --quiet` and `docker compose -f compose.yaml -f docker/compose.real-model.yaml config --quiet` | passed |
| Clean start/browser | `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' ./scripts/verification/verify_clean_compose.sh --reset` | passed |
| Physical interruption | `python3 scripts/verification/verify_physical_interruption.py --output docs/verification/generated/physical-interruption-attempt-2.json` | passed |
| Fake reliability | `python3 scripts/verification/public_workflow.py --attempts 10 --output docs/verification/generated/fake-reliability.json` | 10/10 passed |
| Task 5 HTTP/proof | `docker compose --profile test run --build --rm integration-tests /app/backend/.venv/bin/pytest tests/integration/test_task5_compose.py tests/integration/test_task5_timeout_proof.py` | 25 passed |
| Task 6 | `docker compose --profile test run --build --rm integration-tests /app/backend/.venv/bin/pytest tests/integration/test_task6_finalization_analysis.py` | 5 passed |
| Task 7 | `docker compose --profile test run --build --rm integration-tests /app/backend/.venv/bin/pytest tests/integration/test_task7_regression_comparison.py` | 5 passed |
| Task 8 final | `docker compose --profile test run --build --rm integration-tests /app/backend/.venv/bin/pytest tests/integration/test_task8_api_executor.py tests/integration/test_task8_reconciliation.py` | 22 passed |
| Full Compose backend | `docker compose --profile test run --build --rm integration-tests` | 252 passed in 75.52 s |
| Full Compose sample-agent | `docker compose --profile test run --build --rm sample-agent-tests` | 31 passed |
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
- Existing deterministic failpoint suites remain intact and a regression now covers cancellation after finalized evidence but before analysis.
- Full backend, sample-agent, frontend, browser type, config, and Compose suites pass.

## Failed attempts retained and diagnosed

- Initial clean/browser development failures are enumerated in `clean-start-browser.md`.
- Physical attempt 1 missed the unproven window and correctly failed its narrow gate; attempt 2 passed.
- The first focused Task 5 command produced 25 setup errors because migration `0007` refused to downgrade a database containing prior Task 8 verification state. An explicit scoped volume reset was performed; the clean rerun passed 25/25.
- Task 8 initially failed 2/21 (control campaigns ended failed under the fixed 500 ms boundary). A transparent rerun failed 1/21 on the finalized-before-analysis cancellation race. The race was fixed, a deterministic failpoint regression was added, the focused pair passed 2/2, the full focused suite passed 22/22, and the final full Compose suite passed 252/252.
- Initial host Python commands could not use the sandboxed default uv cache; the same locked commands passed with `UV_CACHE_DIR=/tmp/uv-cache`.

## Unverified acceptance criterion

No usable `OPENAI_API_KEY`, `.env`, or `.env.local` was configured when inspected without reading secret values. Therefore no real-model UI demonstration was executed and no real-model verification record exists. The exact setup and execution commands are in README. This gate must not be marked passed or fabricated.

## Remaining uncertainty and limitations

This remains a localhost, single-process, single-worker, unauthenticated portfolio system. It has no TLS, RBAC, tenancy, distributed queue, multi-replica coordination, production secret manager, rate limiting, or production observability. Physical interruption covered Boundary only. Browser installation through the network was not proven; installed Chrome was used. Real provider availability, model access, account policy, spend, and behavior remain unverified.

## Acceptance decision

All fake-model, browser, routing, interruption, and repository verification gates pass. Phase 1 is not yet genuinely portfolio-complete because the explicitly required real-model demonstration remains unverified.
