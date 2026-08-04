# Physical interruption verification

- Date: 2026-08-04
- Base commit: `f5ffafa4a0afe75fdacab0327425b97bd9eaff97`
- Worktree: uncommitted Task 10 changes
- Passing command:

  ```console
  python3 scripts/verification/verify_physical_interruption.py --output docs/verification/generated/physical-interruption-attempt-2.json
  ```

## Result

PASS on attempt 2.

| Field | Value |
| --- | --- |
| Campaign | `e4d9a86a-484d-4655-98a6-169b5beb27d5` |
| Injected run | `0dcdba7b-160c-4bf7-ab22-37c64082330f` |
| Pre-kill status | `running` |
| Pre-kill activation/effect counts | `1 / 0` |
| Physical action | `docker compose kill --signal KILL boundary` |
| Campaign terminal | `failed` / `RUNTIME_LOST_UNPROVEN` |
| Run terminal | `failed` / `EXECUTION_ERROR` |
| Evidence set | `f9772880-b4e6-4eb1-b485-dd6134c1492b` |
| Retained receipts | `10` |
| Post-restart activation/effect counts | `1 / 0` |
| Reconciliation evidence | present |

Attempt 1 is retained at `docs/verification/generated/physical-interruption-attempt-1.json`: campaign `df8988b4-e326-4e0e-9d32-2b7cd225002d`, injected run `79394ba3-a17d-4eaa-bb72-79ce2f8dfffc`. It conservatively ended `RUNTIME_LOST_UNPROVEN` / `EXECUTION_ERROR` with 11 retained receipts, but the kill landed after the only activation already had a matching effect (`1 / 1`), so the narrower unproven-effect gate correctly failed.

## What this proves

After durable accepted work and a durable `fault_activation_started`, physically losing the Boundary process does not relabel ambiguous work successful and does not manufacture `fault_effect_realized`. Restart reconciliation preserves partial evidence and publishes a conservative terminal error through the public frontend API.

## Remaining uncertainty

This is one focused Boundary-container kill, not a chaos framework. Deterministic Task 5/8 failpoints still prove individual transaction seams and crash positions. Sample-agent or PostgreSQL process loss was not physically exercised here.
