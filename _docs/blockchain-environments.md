# Blockchain environments — Anvil, Sepolia rehearsal, production

Status: **Phase 1 (code ready, nothing deployed)** — 2026-10-04, branch `feat/sepolia-public-chain`.

| Environment | Chain | Chain ID | Where | Status |
|---|---|---|---|---|
| Local / CI | Anvil (Foundry) | `31337` | `docker/docker-compose.yml` → `anvil` | works, unchanged |
| Public rehearsal | Ethereum Sepolia | `11155111` | remote RPC provider, backend `trustmark-app-sepolia` | **template only — not yet run anywhere** |
| Production (current) | Anvil | `31337` | server `trustmark-anvil-1` | **still Anvil until an official cutover** |

Do not describe Sepolia as live until Phase 2 has actually been run and checked on the server.

## Anchor protocol (unchanged)

A plain EVM transaction, wallet → same wallet, `value = 0`, calldata `b"VALUE:" + sha256_hex`
(`anchor_type = calldata-v1`). No smart contract.

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `BLOCKCHAIN_RPC_URL` | yes | JSON-RPC endpoint (`http://anvil:8545` or an HTTPS Sepolia provider) |
| `BLOCKCHAIN_PRIVATE_KEY` | yes | Signer key. Server `.env` only. Never the Anvil key on a public chain |
| `BLOCKCHAIN_CHAIN_ID` | **yes (new)** | Must equal what the RPC reports, otherwise startup/first use fails |
| `BLOCKCHAIN_NETWORK_NAME` | no | Label returned to clients (`"Ethereum Sepolia"`) |
| `BLOCKCHAIN_EXPLORER_URL` | no | Base URL for tx links (`https://sepolia.etherscan.io`); empty for Anvil |
| `BLOCKCHAIN_RECEIPT_TIMEOUT_SECONDS` | no | Max wait for a receipt per request, default 45 |
| `BLOCKCHAIN_CONFIRMATIONS` | no | Confirmations before `confirmed`, default 1 |

> **Upgrade note:** `BLOCKCHAIN_CHAIN_ID` is mandatory. Deploying this code onto the existing Anvil
> production without adding `BLOCKCHAIN_CHAIN_ID=31337` to its env will stop the app at startup
> (by design — an unset chain ID cannot be validated).

Startup (`trustmark.main`) validates the configuration and compares the RPC's `eth_chainId` with
`BLOCKCHAIN_CHAIN_ID`. A mismatch aborts startup; an unreachable RPC only logs a warning and is
re-checked on first use. Secrets are masked in `repr()` of `ChainConfig` / `BlockchainConnector`.

## Database and schema migration

`registry_records` gained (additive only): `chain_id`, `anchor_type`, `anchor_status`
(`pending | confirmed | failed | legacy_local`), `anchor_wallet`, `block_number`, `block_hash`,
`gas_used`, `effective_gas_price`, `submitted_at`, `confirmed_at`.
Existing rows get `chain_id=31337`, `anchor_status=legacy_local`, `submitted_at=created_at`. Legacy Anvil
records are kept as-is (never purged/archived): they show as "Anvil / chain 31337 / not publicly verifiable",
new ones as "Ethereum Sepolia / chain 11155111 / publicly verifiable".

**The schema is changed explicitly, never silently by startup.**

| Command | Effect |
|---|---|
| `python -m trustmark.infra.schema check` | read-only; exit 1 and list what is missing |
| `python -m trustmark.infra.schema migrate` | additive + idempotent; runs `src/trustmark/migrations/*.sql` in ONE transaction under an advisory lock (PostgreSQL), then re-checks |

Application startup only runs `check_schema` and refuses to start (with the exact command to run) if the
schema is outdated. `SCHEMA_AUTO_APPLY=true` makes startup run `migrate` itself; it is set in `.env.example` for
fresh local/CI databases only and defaults to false in Compose.

Deployment order: backup (`pg_dump`) → `migrate` once → `check` → start/recreate the app.

Properties: two apps starting together never mutate anything (check is read-only); two overlapping `migrate`
runs are serialised by the advisory lock and each statement is `IF NOT EXISTS`; a half-applied migration cannot
persist on PostgreSQL (transactional DDL); `check` needs only catalog read access, `migrate` needs
CREATE/ALTER on the database (use the owner role for the migrate step if the app role is restricted); the SQL file is
the single source for PostgreSQL and `tests/trustmark/test_schema_migration.py` fails if its columns, types,
nullability or defaults drift from the models (SQLite dev/test derives the same change from the models).

## Behaviour changes visible to API clients

- `register` / records / verify responses add `anchor_status` and an `anchor` object
  (`chain_id`, `network_name`, `transaction_hash` (0x-prefixed), `explorer_url`, `explorer_tx_url`,
  `block_number`, `submitted_at`, `confirmed_at`). Existing flat fields are unchanged.
- `register` returns after at most the receipt timeout. If the receipt is not there yet the record is
  stored as `pending` with its transaction hash (HTTP 200, `anchor_status: "pending"`); re-registering the
  same document, or verifying it, re-checks the receipt. A `failed` anchor is re-broadcast on re-register.
- `verify` returns `valid: false` plus `verification_status` for anything that is not a confirmed, matching
  anchor: `pending`, `anchor_failed`, `hash_mismatch`, `wallet_mismatch`, `invalid_anchor_payload`,
  `transaction_not_found`, `chain_not_available` (record belongs to another chain than the backend's).
  A confirmed match returns `valid: true`, `verification_status: "confirmed"`.
- The frontend does not use any of the new fields yet.

## Fees, nonce, receipts

- Gas: `eth_estimateGas` + 20 % buffer. Fees: EIP-1559 `maxFeePerGas = 2 × base fee + priority fee`
  (priority from `eth_maxPriorityFeePerGas`, fallback: median of `eth_feeHistory`); legacy `gasPrice` on chains
  without a base fee. A balance pre-check returns 503 instead of an opaque node error.
- Nonce: `eth_getTransactionCount(addr, "pending")` read, sign and broadcast happen under a PostgreSQL
  transaction-scoped advisory lock keyed by (chain, signer) (process-local lock on SQLite), so concurrent
  registrations cannot reuse a nonce, also across several backend containers on one database.
- Receipt wait: bounded (`BLOCKCHAIN_RECEIPT_TIMEOUT_SECONDS`); blocking chain calls run in a worker thread so
  they no longer stall the event loop.

## Rehearsal database isolation (recommendation for Phase 2)

Use a **separate database** `trustmark_sepolia` on the same Postgres server (template:
`docker/docker-compose.sepolia.yml`; database name is literal, `REQUIRE_DATABASE_NAME=trustmark_sepolia` makes the app refuse to start against any other database, no fallback to `trustmark`). `content_hash` is globally UNIQUE: sharing the table would silently
block anchoring any document that already exists on Anvil and mix test chains with production records.

## TEST_MODE status

`trustmark/main.py` still installs a hard-coded authentication bypass (`sub="test-user"`, role `publisher`)
when the process runs as `__main__` with `TEST_MODE=true`. The Dockerfile starts the app with
`python -m trustmark.main`, so this code path **is live in every container**. It is off only because
`docker/.env*` pin `TEST_MODE=False`; the Sepolia template forces `"False"`. Before exposing a Sepolia
backend publicly, confirm `TEST_MODE` is `False` in its container (`docker compose exec ... printenv TEST_MODE`)
and ideally remove the bypass from the production image. Not changed in Phase 1.

## Known limits (Phase 1)

- Not exercised against a live Postgres (advisory-lock path) or live Sepolia; covered by SQLite + in-process EVM tests.
- A load-balanced RPC provider can return a lagging `pending` nonce; the lock does not protect against that.
- A pending record is only re-checked when it is registered again or verified; there is no background confirmer.
- The `docker-compose.yml` on the production server has drifted from git (see ops handoff); do not copy this file over it.
