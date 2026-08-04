# Clean-start and browser verification

- Date: 2026-08-04
- Base commit: `f5ffafa4a0afe75fdacab0327425b97bd9eaff97`
- Worktree: uncommitted Task 10 changes; no unrelated changes were present at start
- Passing command:

  ```console
  PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' ./scripts/verification/verify_clean_compose.sh --reset
  ```

## Result

PASS. The explicit project-scoped reset created a new PostgreSQL volume; migration completed at `0007_executor_public_api`; PostgreSQL, Boundary, sample-agent, and frontend reported healthy; only `127.0.0.1:5173` was published; public `/api` returned Boundary's error contract; `/internal` and `/internal/*` returned 404; E2E typecheck passed; and the one-worker/no-retry Playwright journey passed in 19.8 seconds (26.0 seconds including runner startup).

| Record | ID |
| --- | --- |
| Vulnerable campaign | `9810810b-e24d-40e6-91a6-d8b56c80aa54` |
| Vulnerable control | `b03c6f72-81d5-4c7c-99bb-bd6c3adfdd4a` |
| Vulnerable injected | `39fd12ef-1bae-45ea-a408-8a8fdfacdcd4` |
| Regression case | `2c0c2427-1b0f-56b9-86e3-2aaf579ad5a8` |
| Rerun campaign | `85d962d9-a502-4a7b-9184-2587aed9c482` |
| Fixed control | `dc1e118a-1a87-422f-8bd4-fa1919744272` |
| Fixed injected | `2a86e5c9-a757-45b2-9bd6-f3d51fa0252c` |
| Comparison | `b003ef9d-bedd-4610-bd6c-7d857e26b2a0` |

The browser observed vulnerable control completion, vulnerable injected `FAIL`, ordered receipts, three separate diagnostic panels and their authoritative evidence links, immutable regression provenance, fresh `fixed-v1` control/injected execution, fixed injected `PASS`, completed invariant rows without mismatch, and exactly `The fixed tested-agent version passes this scenario policy.` Direct refresh of both run and comparison routes passed.

## Retained failed attempts

Failures were not silently replaced:

- Playwright launch attempt: executable override was initially placed at the wrong config level; no campaign was started.
- Locator-development attempts retained under ignored `tests/e2e/test-results-attempt-*`: campaign `d09c5640-f485-4695-a510-9118d1ef3ef4` (duplicate version text), `baa79263-ce68-4e87-9620-4a91ea0ae2e9` (duplicate `FAIL` text), `95ea468b-e858-4631-bbe8-43288eea322b` and `20cce279-8075-425f-b6ba-e2ac13e05022` (invalid evidence-ID uniqueness assumptions).
- One genuine campaign failure was retained: campaign `2bc40b54-0625-43d2-afe3-ef1bfa3e6068`, control `f4ae711b-8829-483c-80af-db71d58c696f`, `CAMPAIGN_EXECUTION_ERROR` / `TOOL_CALL_FAILED`. Boundary did not fabricate the injected run. The formal independent ten-run gate later passed 10/10.
- Two clean-verifier attempts stopped after healthy migration because the verifier expected an optional error-envelope field and then did not recognize Docker Compose v5's unpublished `:0` form. Both assertions were corrected; neither failure reached or altered the browser workflow.
- A Playwright browser download emitted no progress and was cancelled; the passing audit used the installed Chrome executable explicitly.

## What this proves

The smallest public topology—browser, localhost frontend/Nginx proxy, Boundary API/executor, separate sample agent, and PostgreSQL—works from an empty volume without browser access to private networks, the internal tool route, or PostgreSQL. The browser follows server-provided application links and never computes a verdict.

## Remaining uncertainty

The audit used macOS Chrome rather than a newly downloaded Playwright browser. Image pulls, npm/Playwright downloads, Docker startup time, and localhost scheduling remain environment-dependent. The observed one-off control timeout is recorded rather than hidden; the formal ten-run record is the reliability gate.
