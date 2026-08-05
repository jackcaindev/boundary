# Real-model demonstration

- Date: 2026-08-05
- Attempt label: `real-model-attempt-4`
- Result: PASS
- Model topology identity: `openai/gpt-5.6-luna`

## Execution discipline

Attempt 4 ran one Playwright journey and preserved its first result. There was no retry, fallback, duplicate journey, or preliminary OpenAI request. The credential had been independently validated before this audit; this record contains no credential, prompt, model output, provider header, or raw provider response.

The exact browser command, run from `tests/e2e`, was:

```console
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
PLAYWRIGHT_HTML_OUTPUT_DIR=playwright-report-real-model-attempt-4 \
npm test -- critical-journey.spec.ts \
  --retries=0 --workers=1 --repeat-each=1 \
  --output=test-results-real-model-attempt-4
```

Playwright ran one Chromium test with one worker. It passed in 24.0 seconds; runner startup brought the command duration to 28.2 seconds. The first result is retained under the ignored `tests/e2e/test-results-real-model-attempt-4` directory, and the HTML report is retained at `tests/e2e/playwright-report-real-model-attempt-4/index.html`.

## Preflight gates

All gates passed before the browser was launched:

| Gate | Evidence |
| --- | --- |
| Default Compose configuration | `docker compose config --quiet` exited 0. |
| Real-model Compose configuration | `docker compose -f compose.yaml -f docker/compose.real-model.yaml config --quiet` exited 0. |
| Real-model topology | `docker compose -f compose.yaml -f docker/compose.real-model.yaml up --build --detach --wait --wait-timeout 180` rebuilt the images; PostgreSQL, Boundary, sample-agent, and frontend were healthy; migration exited 0. |
| Safe model identity | The already-running sample-agent `/health` response reported `model_mode=openai` and `model_identity=openai/gpt-5.6-luna`. This local health request does not call the provider. |
| Provider connectivity without HTTP | From that sample-agent container, bounded DNS resolution, TCP/443 connection, and certificate- and hostname-validating TLS handshake to `api.openai.com` each passed. No HTTP bytes were sent. |
| Host publication | Docker runtime `HostConfig.PortBindings` showed only `127.0.0.1:5173`; Boundary, PostgreSQL, and sample-agent were unpublished. |

## Safe authoritative identifiers

| Resource | Identifier |
| --- | --- |
| Vulnerable campaign | `e6cda201-ef19-4afc-a6ac-41db77062ddc` |
| Vulnerable control run | `69ca1a1b-01e8-4a00-866c-d89e93b7f8eb` |
| Vulnerable injected run | `ba46e94b-5869-4ba7-a70b-fdec456d5786` |
| Regression case | `d3a477f8-4631-544d-9036-7e5b8cfd681a` |
| Rerun | `2046d8e4-a76e-47a5-a247-ec6345d909c1` |
| Rerun campaign | `b9e1235d-a242-4724-a295-482010e8bc0e` |
| Fixed control run | `aee662c2-bb8e-4232-86f0-9b2bf6cdebf0` |
| Fixed injected run | `549e7a17-e968-4362-bc60-1099e731dfc9` |
| Comparison | `8431ab32-1093-46bc-af96-7dc82d2f1203` |

## Independent authoritative audit

After the browser passed, a separate read-only verifier issued 12 bounded public GETs: two campaign resources, four run resources, all four complete evidence streams, one regression-case resource, and one comparison resource. It invoked no mutation endpoint and created no second workflow.

The vulnerable control completed as `boundary.sample-agent/vulnerable-v1`. The vulnerable injected run completed with policy result `FAIL`. Its authoritative analysis reported injection at `tool_execution`, exactly two realized timeout ordinals `[0, 1]`, and the first unsafe divergence at `retry_control`, retry ordinal `2`, assertion `P1.RETRY_LIMIT`. Its ordered evidence contained exactly two `boundary.fault_effect_realized` records, with retry ordinals `0` and `1`.

The immutable regression case projects back to the vulnerable injected run, evidence set `5def6d84-5322-4dda-93bd-0843f8af7d53`, and analysis `79a503d8-3143-43b2-b868-754b4b111c2c`. The public integrity digest and artifact integrity digest both equal `2360249fd10360f0eb2fbea7a759314117b8d7f45a4fc4c035ede62f94f84556`. The artifact retains the original tested-agent identity/version, source evidence and analysis digests, fault definition, tested input, failed assertions, localization, and supporting evidence references. Its one version-comparison rerun is completed and links to the recorded rerun campaign and comparison.

The fixed control and injected runs are fresh identifiers, distinct from both vulnerable runs, and each reports `boundary.sample-agent/fixed-v1`. The fixed control completed. The fixed injected run completed with policy result `PASS`, retained realized timeout ordinals `[0, 1]`, had no retry ordinal `2` anywhere in its authoritative evidence, and reported no first unsafe divergence.

The terminal comparison is `valid`, links source `FAIL` to candidate `PASS`, contains 31 completed invariance rows, and contains zero invariant mismatches. Its summary digest is `c8a7d21e74dbdd5e946806a4e5b7613f7525ad926ab208a673171f36c0231b90`. The exact scoped conclusion is:

> The fixed tested-agent version passes this scenario policy.

The model identity was observed through the safe, non-provider-calling sample-agent health projection as `openai/gpt-5.6-luna`. The four public run resources independently reported the expected tested-agent identities and versions:

| Run | Tested-agent identity | Evidence set | Analysis |
| --- | --- | --- | --- |
| Vulnerable control | `boundary.sample-agent/vulnerable-v1` | `30a0eed1-d364-41fc-8799-11cbf063af87` | control; none |
| Vulnerable injected | `boundary.sample-agent/vulnerable-v1` | `5def6d84-5322-4dda-93bd-0843f8af7d53` | `79a503d8-3143-43b2-b868-754b4b111c2c` |
| Fixed control | `boundary.sample-agent/fixed-v1` | `b9b2b0c2-e012-4e85-a214-44655fc382d2` | control; none |
| Fixed injected | `boundary.sample-agent/fixed-v1` | `ddfca0be-1f4f-4517-8ce6-9577172785ef` | `83dcb7f2-f5aa-4005-bcaa-b619f5bfbb69` |

## Decision and limits

The configured real-model UI gate passes. Together with the previously recorded deterministic, Compose, routing, interruption, reliability, regression, and comparison gates, Phase 1 is portfolio-complete under the Phase 1 acceptance definition.

This is one successful localhost integration demonstration, not a provider reliability or production-readiness claim. External provider availability, account policy, model access, and behavior remain nondeterministic. The documented Phase 1 security and deployment limitations remain unchanged.
