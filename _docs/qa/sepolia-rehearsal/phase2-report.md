# Phase 2 report - Ethereum Sepolia rehearsal (2026-10-04)

Branch `feat/sepolia-public-chain`. No cutover: production `trustmark-app` + Anvil + database `tdb` were not restarted,
recreated, migrated or modified. No secret is stored in this file.

## Topology
| | Production (unchanged) | Rehearsal |
|---|---|---|
| Container | `trustmark-trustmark-app-1` | `trustmark-app-sepolia` (own Compose project `trustmark-sepolia`) |
| Database | `tdb` (57 records, 7 columns, no new table) | `trustmark_sepolia` (pinned by `REQUIRE_DATABASE_NAME`) |
| Chain | Anvil 31337 | Ethereum Sepolia 11155111 via keyless public RPC |
| Access | existing routes | `127.0.0.1:41013`; public API path `https://pits-ui.pangkalandata.id/sepolia-api/` |

Source for the rehearsal lives in `/home/hamka/pits/backend-sepolia` (copy of commit `185a03d` + later fixes), separate from the
divergent production checkout. Backup: `/home/hamka/pits/backup-20261004-sepolia/`. nginx backup:
`pits-ui.conf.bak-20261004-sepolia-rehearsal`.

## Wallet
Public address `0x3EB7D8332a8405a81Dbae7Bc2dA2FAfAe3603EB5` (generated on the server, key never displayed, not the Anvil key),
funded 0.05 Sepolia ETH from a faucet (tx `0x7843bb0a6ec6d4d7d9bdb01aa86217380556dce3cdda302d08be3d9b2451e565`).

## Canonical transaction
- File: `pits-sepolia-canonical.pdf`, SHA-256 `613171c873064a79d01805ba70f1cb5f4460800e46e83b8728d6bbf32b6adde9`
- Record `1b752465-a85e-4ae1-ba2e-812d0a83b16f`
- Tx `0xeb912fd2728dc79330b592684ca687374a882273a43c0a21ccb95719b052ed19`, block 11839743, gas used 23800, effective gas price 977322016 wei, status success
- https://sepolia.etherscan.io/tx/0xeb912fd2728dc79330b592684ca687374a882273a43c0a21ccb95719b052ed19
- Independent check on Etherscan (page opened in a browser): From = To = wallet, value 0, input data
  `0x56414c55453a3631...` decodes to `VALUE:613171c8...adde9`, equal to the SHA-256 of the PDF.
- PITS verify (by upload and by hash): `valid:true`, `verification_status: confirmed`, anchor `chain_id 11155111`, `Ethereum Sepolia`, explorer URL set.
  Tampered file and unknown hash: `valid:false`. Re-registering the same file: `already_existed:true`, no new transaction.

## Findings fixed during Phase 2
1. Schema CLI saw empty metadata (models not imported) - check passed / migrate failed on fresh DB. Fixed + regression test.
2. web3/urllib3 DEBUG logs wrote the full RPC URL (API key) to the log file. Loggers now WARNING.
3. **Stale pending nonce on public RPC**: 4 simultaneous registers -> 2 failed ("replacement transaction underpriced", nonce reused)
   in two consecutive live runs, although the PostgreSQL advisory lock worked. Fix: persisted per-signer nonce floor with 120 s TTL
   (`anchor_signer_nonces`, migration 002). Re-run: 6 simultaneous registers -> 6x HTTP 200, nonces 6-11, 0 broadcast failures.

## Tests
- Unit: 187 passed (`tests/trustmark`).
- Real PostgreSQL (`tests/postgres_integration`, run inside the rehearsal image): 15 passed - explicit migration, idempotency, 3 concurrent
  migrators, rollback of a failing migration, legacy rows -> `legacy_local`/31337, advisory lock visible in `pg_locks`, second holder waits,
  distinct signers do not block, nonce race (with a control run without the lock that does collide), nonce floor on Postgres.
- Live pending flow (throw-away container + scratch DB, receipt timeout 1 s): register -> `pending` with tx hash saved; verify -> `valid:false`,
  `pending`; later verify -> `valid:true`, `confirmed`, DB row `confirmed` with block number. Scratch DB and container removed.
- Live Sepolia smoke: auth via real Keycloak (`TEST_MODE=False`), 401 without/with malformed token, 503 on unfunded wallet with nothing stored,
  dedup, 6 concurrent anchors, verify success/failure cases.
- Pass 5 (53 tests) NOT RUN: `tests/integration_pass5/conftest.py` is hard-wired to `127.0.0.1:41012` (the production backend), `localhost:8080`
  and DB `trustmark`, and BC-INT-08/resilience tests stop the Anvil container. Running it on the shared server would hit production.
  Classification: Anvil-specific = test_blockchain_integration (8), test_resilience (3), part of test_provenance_journey (3);
  application/API generic = test_db_integration (10), test_file_validation (12), test_isolation (4), test_keycloak_integration (13).
  It needs its own isolated stack (different ports/project) before it can be run against Sepolia.

## Production regression
UI, API health and Keycloak realm 200; production containers unchanged (same IDs/StartedAt, 0 restarts); Anvil healthy; `tdb` unchanged.
Pre-existing, unrelated: 3 of 57 production records (created 2026-09-06 18:34-18:47, before the Anvil container was recreated at 19:00)
point at transactions that no longer exist on Anvil, so the old production verify returns HTTP 500 for them (54/57 verify 200).
Under the new code these report `verification_status: transaction_not_found`.

## Addendum 2026-10-04 (evening) - server hang, recovery, legacy re-anchoring, Anvil persistence
- A frontend image build run on the 4 GB host coincided with a full hang (SSH/HTTPS/console unresponsive for ~3.5 h); recovered by a portal
  reboot. `docker` and `nginx` are disabled at boot on that host, so every service had to be started by hand after the reboot.
  Anvil is in-memory, so the reboot reset the chain to block 0: the 57 records in `tdb` can no longer be verified through the production
  backend (data in Postgres is intact).
- **Legacy re-anchoring**: `tools/reanchor_legacy.py` copies the 57 records from `tdb` (read-only) into `trustmark_sepolia` and anchors each
  SHA-256 on Sepolia. Original id/filename/issuer/`created_at` are preserved; `anchor_type = calldata-v1-reanchored` marks them because the
  on-chain timestamp is the re-anchoring time. Production `tdb` is not modified. Verified through `/sepolia-api/` only.
- **Anvil persistence** (UAT only): production compose now starts Anvil with `--state /data/anvil-state.json --state-interval 10` and a bind
  mount `/home/hamka/pits/anvil-data` (backup of the previous compose: `backup-20261004-sepolia/docker-compose.yml.server.before-anvil-persist`).
  Tested: graceful restart and SIGKILL both keep the chain height. Up to 10 s of blocks can be lost on a crash; it is still a local chain.
