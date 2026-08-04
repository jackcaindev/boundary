# Ten-run fake-model reliability

- Date: 2026-08-04
- Base commit: `f5ffafa4a0afe75fdacab0327425b97bd9eaff97`
- Worktree: uncommitted Task 10 changes
- Command:

  ```console
  python3 scripts/verification/public_workflow.py --attempts 10 --output docs/verification/generated/fake-reliability.json
  ```

## Result

PASS: 10 successes, 0 failures, no retries. Wall-clock interval was 64.442 seconds; summed attempt durations were 64.422 seconds. Every attempt used fresh campaign and idempotency identities, executed the actual fake sample agent and Boundary executor, produced vulnerable injected `FAIL`, materialized immutable regression provenance, executed fresh fixed control/injected runs, produced fixed injected `PASS`, completed a valid invariance comparison, and returned exactly `The fixed tested-agent version passes this scenario policy.`

| # | Campaign | Vulnerable control | Vulnerable injected | Regression | Rerun | Rerun campaign | Fixed control | Fixed injected | Comparison | Seconds |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| 1 | `6410af56-b022-4ddc-a24a-e218c437fbe2` | `8d6efa83-5dcd-4efe-9c9f-ac11166c4b74` | `757e37f4-4907-4c56-9c98-232dce9f6477` | `862fd449-f041-5f1e-999c-08d6457fdf94` | `e8b91893-04a5-4c9f-bfb5-62f325a4073b` | `1c7fbbfb-641d-442d-9364-455d520183b8` | `a6daf449-edcc-41d8-937a-be940811c74e` | `8e0f176c-fbf0-4390-a0bd-e2ea0c87517c` | `63cda64a-e94d-4136-9894-0b1dd8e4863e` | 6.307 |
| 2 | `52f2fc3f-49ef-4fd3-bd40-da70b48d41ef` | `ea73fee3-c811-4884-915b-5fb5ebc97bc9` | `f47d6ad3-95ee-485a-96e7-b30d9daea344` | `502ea494-a947-582f-8375-8c1559e2a575` | `e1b32265-c6a7-4bd6-b7c8-ac242491521b` | `6159f60c-56db-4cde-b803-bc3412649117` | `0c6f0955-5525-4a53-9466-cf830ab54d42` | `e9c5a5be-2f8a-4b1f-9a7c-c72943bc6c67` | `078f9fbb-d08b-4cb0-8daf-249f3774e666` | 7.157 |
| 3 | `733d5328-659b-47b0-9fb0-7cfc559cabf3` | `5d105ed6-7558-454f-9739-30399f1dc3f6` | `f8213ccb-4bb6-4da6-ae9c-bcbdc69bbe99` | `ca26af81-1d68-5276-8528-74ff066013d7` | `462215c8-94c0-4d31-b7eb-b9b581123aef` | `b27f810a-f5e3-46ab-9b55-4a50e943ed50` | `86589efc-28a6-40f3-aaee-2365da7a6780` | `27ac0f7b-2d0e-41db-9d5d-82e170213fe9` | `9441c7ce-aebc-4c19-851e-812186ac0286` | 6.593 |
| 4 | `01820802-9c2e-4732-905f-8e5e48db30a2` | `8d7e9ff1-b44e-4e99-b34c-f98db13a5210` | `af43ced6-fc75-4585-81f2-158f20736a39` | `ac67e8ad-ea8a-56b0-9b3e-c4670ad0cf1c` | `d8a9dab6-89cf-4639-a528-74b22386acfd` | `52425153-0943-47bc-9b37-812f5bcf5335` | `1055a42e-0906-46d6-a287-9eb435933ba5` | `4ff9dec4-277f-44b4-ba60-a73453b7ad3b` | `110c7359-bab4-4022-8970-2d5cd8274d89` | 6.269 |
| 5 | `fb88874f-fc14-41ec-822c-8360c4f13fb7` | `87bf4d5b-7f94-47f0-bc5c-9f1fb845ce7f` | `4e18928e-57f5-49c3-84cc-caf8ceef583d` | `36555675-bca0-5b0c-83cd-1814f1b51889` | `58cb5402-f7de-4e7a-8c01-7ea5cd624b86` | `774307f0-acf0-4da3-aef7-70646f776b23` | `bb48fcb9-4cf1-40fa-a259-107d07a4350a` | `8b48de44-e515-4f97-af84-098ca63c88de` | `aaa10fc3-9ccc-4c71-abec-07b6e3b1518a` | 6.556 |
| 6 | `56240e5d-3036-4d27-bc88-ca97ba5c73a3` | `284f0ec5-778b-437f-9971-c636f897d760` | `515facfa-8c38-41f1-9eee-b71de9161dd9` | `8cde7b89-0d0d-5bfe-ad5a-a2c0282b75f2` | `fd1778c3-3c02-4596-b656-ca8cbee1825f` | `03403a6b-4dda-4f4a-8b85-7de0733fe9c7` | `71ff6ce9-3bfb-4297-8380-8c1c568b632a` | `ec46e582-71fa-4dd8-bf3a-db8ffa1c07f2` | `94234cd7-1aae-4b5a-94e4-43fc7b915f14` | 6.250 |
| 7 | `2d6e9c1b-a48a-4dbe-9c4c-b9062637b95d` | `5bfd2e9b-ff6f-4feb-9e7d-0d944887aa47` | `33ab19c2-3f18-4eca-aa20-f84f535fc542` | `7757dc4d-01a1-5275-b057-2c3a75f3fa0c` | `c60739ba-a127-4854-bc5f-7dea3e9a4e99` | `a6a99467-1b16-41fa-8c04-08529150b9e6` | `16c6e23f-6c80-4d93-8509-8f028da3e312` | `72db13b8-d329-44a0-ae6b-8b10e40e1484` | `7519b2c1-417b-423f-adf6-5daf3f61fc5e` | 6.242 |
| 8 | `f828ea72-3f03-411c-b15f-92e22d861e77` | `897dd9eb-b71e-4eb8-baa4-034e727f9e18` | `2551384e-6fcf-4a32-b32e-3a36ccb49c0d` | `769a90b0-8043-5cf8-a652-d88f2d863a23` | `8da07a34-3e0a-47d3-a366-674a0a59a4f5` | `4cfa3359-0227-492f-8d0e-894a750c0da9` | `558d5681-4008-40c6-9595-66ee018a8553` | `55622ada-4036-480e-a443-1f785deb73ef` | `a9d827c4-a4b9-408a-a28a-255b8c04788d` | 6.035 |
| 9 | `74f68019-7514-485c-a905-87aae9821a03` | `f2090cb7-baba-4a37-bab8-f365875c0ef3` | `afa57c36-8f01-447f-b652-efed67dd24fe` | `db035375-53ee-535c-884e-ee021fee8056` | `71dd66f7-12f7-46d4-b518-fa9d8bdaa032` | `f7a0198b-c3a2-45af-b4e1-926d86705c4e` | `882312c5-e89c-49cf-ba94-fcb6f756cd9d` | `ac1d7d62-2eed-4bcd-8131-dd87ce276c57` | `006efed7-32b0-4335-96c6-3a36aeb2d674` | 5.885 |
| 10 | `e95571dd-de05-48f4-b0ad-8e43ea99e9c4` | `14dda67e-c324-458e-bb30-eff090fc0238` | `05738a01-7ff6-4eeb-a9be-a8d991b9d22a` | `6c18ebe7-5738-52e7-973a-002110c5baf7` | `80f48c17-d0c4-4500-9c79-7b7e9c15cf23` | `701f1e9a-d550-490a-847d-b6b94bcb6bad` | `c9537161-2752-44c9-86a2-5fac79863bef` | `50f88309-884f-47b4-b3a6-68c832e546d8` | `d157ddf9-c87f-4cf6-a210-e3b08b44b238` | 7.128 |

Failure reason was `null` for every attempt. No secret, capability, database URL, provider output, or raw model content was recorded.

## What this proves

The deterministic fake-model execution path and complete Boundary campaign/regression/comparison lifecycle exceeded the required 9/10 gate through actual HTTP execution, not fixtures or stored results.

## Remaining uncertainty

This is a local, serial, single-process sample on one machine. It does not establish production availability, concurrency behavior, provider reliability, or real-model success. One separate browser-development campaign experienced a control timeout and is disclosed in the clean-start record.
