# PASS 5 — Integration & Reproducibility

**Date**: 2026-09-14
**Executed by**: Claude Code (independent QA pass, session continuation of `pits-qa-master-export-2026-09-13.md`)
**Scope**: closes the integration/reproducibility gap identified in the master export — real PostgreSQL, real Keycloak, real Anvil, multi-publisher isolation, full provenance journey, and a reproducible Locust benchmark, all against a **local, disposable, full-stack replica**. Production (`pits.pangkalandata.id` / `keycloak.pangkalandata.id`) was **not touched** at any point in this pass.

No new unit tests were added to inflate the existing 94 (backend) / 30 (frontend) unit counts. Everything in this document is either `INTEGRATION-REAL-LOCAL` (real Postgres/Keycloak/Anvil, no mocks) or `PERFORMANCE-LOCAL` (Locust against the same local stack).

---

## 1. Executive Summary

- Stood up the actual `docker/docker-compose.yml` stack from `backend-for-uat` (Postgres 16, Keycloak 26.6.2, Anvil/Foundry, the FastAPI backend) end-to-end on a local machine, fully disposable, torn down after this pass.
- **New root-cause finding**: the "empty `issuer_id`" bug (previously attributed to production Keycloak config) reproduces identically on a **completely fresh, stock, local Keycloak 26.6.2 realm** imported from this repo's own `docker/realms/realm-export.json`. Controlled experiment (add a `sub` protocol mapper → claim appears; remove it → claim disappears again) proves the exact mechanism: the realm export's `clientScopes` array is empty, so the client is never granted a scope carrying an `oidc-usermodel-property-mapper` for `sub`. This is a **Keycloak realm/client provisioning gap in the exported realm JSON itself**, not a code bug in the FastAPI backend and not a one-off production drift. It affects any deployment (production or local) that imports this exact `realm-export.json`.
- **Multi-publisher IDOR reconfirmed with live two-account runtime evidence** (not just source inspection or a single-account proxy, which is all that was possible in the 2026-09-09 pass): Publisher A's `GET /records` genuinely returns Publisher B's records, with all fields (`content_hash`, `filename`, `transaction_hash`, `created_at`, `issuer_id`) exposed.
- **Full provenance golden journey (PROV-01) confirmed end-to-end** on real infrastructure: SHA-256 computed at upload == registry `content_hash` == value read back from a real, mined Anvil transaction; a public, unauthenticated verifier confirms the same document by upload and by hash. Tamper test (PROV-02) and never-registered test (PROV-03) both behave correctly.
- **One new finding**: a filename over 255 characters causes an unhandled `500 Internal Server Error` (the `original_filename` column is `String(255)`, no length validation before the DB write).
- **Anvil is a non-persistent local dev chain**: a container restart loses all transaction history (confirmed by direct experiment). This is an environment/prototype limitation, explicitly not a claim about any real blockchain network's durability.
- **Malformed bearer token → 500, not 401, reconfirmed live** (previously a unit-mocked-only finding; now confirmed against the real `KeycloakVerifier.verify()` path with a real HTTP round trip).
- Locust load-test code had **two real, previously undiagnosed bugs** (`is not str` identity comparisons that always evaluate true, and a hardcoded "must equal empty string" expectation baked around the issuer_id bug) plus a **missing Authorization header** in `register.py` that made 100% of historical `/register` load-test traffic fail outright unless the target was running with `TEST_MODE=True` (the auth-bypass mechanism documented in the master export). All three are fixed here, and 3 fresh 60-second runs were captured against the local stack. These numbers are **not** compared 1:1 against the historical 8,880/8,245/8,165 figures — the historical runs' target configuration (TEST_MODE on/off, auth wiring) cannot be determined from the numbers alone, and this pass explicitly does not claim they are the same environment.
- **Test counts, exact**: 53 new integration tests written for this pass. **44 PASS, 8 FAIL (all documenting real, reproduced findings — not test defects), 1 SKIP** (Anvil pending-tx state not reproducible in this compose config). *(Corrected 2026-09-14 after a second, independent rerun requested for verification: the first published version of this report said 45/7/1, which undercounted by one FAIL — `FILE-09` was narratively documented as a finding but its test had no assertion, so pytest reported it as PASS regardless of the server's actual response; the assertion was added and the whole suite rerun from a fresh stack to confirm. See §9 for the full reconciliation.)*

---

## 2. Environment

### 2.1 Stack

Brought up via the repository's own `docker/docker-compose.yml`, unmodified, using the repository's own `docker/.env` (all values in that file are local dummy/dev secrets — `trustmark`/`trustmark`, `admin`/`admin`, Anvil's well-known default test private key — identical across `.env`, `.env.example`, `.env.save`; none are production credentials):

```bash
cd backend-for-uat/docker
docker compose up -d --build
```

| Service | Image | Version (confirmed live) | Port (127.0.0.1 only) |
|---|---|---|---|
| postgres | `postgres:16-alpine` | PostgreSQL 16.15 | 5432 |
| keycloak | `quay.io/keycloak/keycloak:26.6.2` | Keycloak 26.6.2 (JVM 21.0.11) | 8080 |
| anvil | `ghcr.io/foundry-rs/foundry:latest` | anvil 1.8.1 (commit `982849d`) | 8545 |
| trustmark-app | built from `docker/Dockerfile` | Python 3.12.8, backend commit `4155258` | 41012 |

Host: macOS (Darwin 25.5.0, arm64), Docker Desktop 29.7.2, Docker Compose v5.5.0.

Env vars used (names only, values are the repo's own local dummy defaults from `docker/.env` — nothing here is a secret):
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `KEYCLOAK_USER`, `KEYCLOAK_PASS`, `KEYCLOAK_ISSUER=http://localhost:8080/realms/nextjs-kc`, `KEYCLOAK_ISSUER_URL=http://keycloak:8080/realms/nextjs-kc`, `KEYCLOAK_AUDIENCE=account`, `BLOCKCHAIN_RPC_URL=http://anvil:8545`, `BLOCKCHAIN_PRIVATE_KEY` (Anvil's public, well-known default test account #0 key — not a secret, printed by every Anvil instance on startup), `MAX_UPLOAD_BYTES=20971520`, `TEST_MODE=False` (real auth enforced for all tests in this pass, except where a test explicitly documents `TEST_MODE`'s own bypass mechanism from source).

### 2.2 Realm / client configuration

Realm `nextjs-kc` imported as-is from `docker/realms/realm-export.json` (the same file used to provision every environment, including production). Client `nextjs-web`, confidential, `directAccessGrantsEnabled=true` (password grant), secret `pits-local-client-secret` (a placeholder value, present verbatim in the repo's own realm export — not a live production secret). Zero custom protocol mappers, zero custom client scopes (`clientScopes: []` in the export).

### 2.3 Test accounts (created for this pass, disposable)

Created via `_docs/qa/pass5/setup_keycloak_users.sh` (Keycloak Admin REST API):

| Username | Realm role | Purpose |
|---|---|---|
| `publisher-a` | `publisher` | Publisher A |
| `publisher-b` | `publisher` | Publisher B |
| `user-c` | *(none)* | Authenticated, no `publisher` role — negative-path tests |

Passwords are throwaway values local to this disposable container (`PassA-2026!`, `PassB-2026!`, `PassC-2026!` — not used anywhere else, not production credentials).

### 2.4 Test DB / chain

- Postgres: fresh `trustmark` database, schema created by the app's own `Base.metadata.create_all()` on startup (no seed data beyond what the tests themselves register).
- Anvil: default in-memory dev chain, auto-mining (no `--block-time` flag configured in `docker-compose.yml`), chain id from Anvil's default.

### 2.5 Initialization order

1. `docker compose up -d --build` (waits on Postgres healthcheck → Keycloak → Anvil healthcheck → app).
2. `bash _docs/qa/pass5/setup_keycloak_users.sh` (waits for Keycloak's own readiness, then creates the 3 users above).
3. `uv sync` locally to get a Python 3.12 venv with the project's own dependencies (psycopg2, requests, web3, pytest, locust) for the test/benchmark scripts, which run **outside** the containers, over the network, exactly like a real client would.

### 2.6 New files added for this pass

- `tests/integration_pass5/` — 6 test modules + `conftest.py`, 53 tests total (all `INTEGRATION-REAL-LOCAL`).
- `_docs/qa/pass5/setup_keycloak_users.sh` — Keycloak user provisioning script.
- `_docs/qa/pass5/locust-runs/run{1,2,3}/` — raw Locust CSV + HTML + log artifacts.
- Fixes to `tests/locust/locustfiles/register.py` and `verify.py` (see §9).

---

## 3. Root-Cause Finding: Missing `sub` Claim (Finding 2, re-diagnosed)

**Previous status** (2026-09-09 pass): observed only on the production `uat-tester` token; attributed tentatively to production-specific client/realm config, unconfirmed.

**This pass**: reproduced identically on a from-scratch local Keycloak import of the exact same `realm-export.json`.

**Evidence** (`publisher-a` token, freshly issued, local Keycloak):

```json
{
  "exp": 1789319530, "iat": 1789319230, "iss": "http://127.0.0.1:8080/realms/nextjs-kc",
  "aud": "account", "azp": "nextjs-web", "acr": "1",
  "realm_access": {"roles": ["offline_access","publisher","default-roles-nextjs-kc","uma_authorization"]},
  "scope": "profile email", "preferred_username": "publisher-a", "email": "publisher-a@pass5.local"
  // no "sub" key at all
}
```

**Controlled experiment (root cause proof)**:
1. Baseline: `sub` absent — confirmed above.
2. Added, via Keycloak Admin REST API, a single protocol mapper directly on client `nextjs-web`: `oidc-usermodel-property-mapper`, `user.attribute=id`, `claim.name=sub`, `access.token.claim=true`.
3. Fetched a new token → `sub` = `d9c9d42c-a481-44fa-b91b-9ceb374a4dea` (publisher-a's real Keycloak user id).
4. **Removed the mapper again immediately** (to restore the baseline before running the formal KC-INT test suite) → `sub` reverts to absent.

**Root cause**: `sub` is populated by an explicit protocol mapper in Keycloak, not automatically. The realm export used by this repo (`docker/realms/realm-export.json`) has `clientScopes: []` and the client has zero protocol mappers of its own — there is no mapper anywhere in the scope chain that emits `sub`. This is a **Keycloak realm/client provisioning gap in the exported realm JSON**, reproducible on any environment (local or production) that imports it as-is. It is not a FastAPI/backend code defect — `trustmark/infra/auth/keycloak.py`'s `claims.get("sub", "")` behaves exactly as intended given the claims it receives.

This explains, with a concrete mechanism, why every registered document (in this pass and in the 2026-09-09 production pass) gets `issuer_id=""`.

---

## 4. Test Matrix

Evidence level for every test in this section: **INTEGRATION-REAL-LOCAL** unless noted. Status legend: PASS / FAIL / SKIP / BLOCKED / N/A.

### 4.1 PostgreSQL integration (`tests/integration_pass5/test_db_integration.py`)

| ID | Objective | Status | Finding |
|---|---|---|---|
| DB-INT-01 | New record persists in real Postgres | PASS | — |
| DB-INT-02 | Record retrievable from a fresh connection after the request ends | PASS | — |
| DB-INT-03 | `content_hash` UNIQUE constraint genuinely rejects a duplicate | PASS | — |
| DB-INT-04 | `transaction_hash` UNIQUE constraint genuinely rejects a duplicate | PASS | — |
| DB-INT-05 | 5 concurrent identical registrations → exactly one authoritative row | PASS | Real Postgres UNIQUE constraint + the app's own duplicate-check-then-insert path resolved the race to exactly 1 DB row across 5 concurrent requests in this run. (Note: DUP-4 in the 2026-09-09 live pass raised the theoretical possibility of two blockchain transactions for one hash if two requests both pass the pre-check before either commits; this run's timing did not trigger that window — see §14 remaining gaps.) |
| DB-INT-06 | Failed transaction does not leave a partial row | PASS | — |
| DB-INT-07 | `issuer_id` persists exactly as the authenticated principal's JWT `sub` | **FAIL** | Reconfirms Finding 2 live: stored `issuer_id=''`, token `sub=None` (see §3). Not a new bug — direct DB-level confirmation of the already-diagnosed root cause. |
| DB-INT-08 | `created_at` generated and returned | PASS | — |
| DB-INT-09 | DB session cleanup after 20 rapid request/response cycles (no leak) | PASS | — |
| DB-INT-10 | Postgres unavailable during `/register` | PASS (documented) | `register()` returns `500 Internal Server Error` (no clean 503) when Postgres is stopped. Recorded as expected-current-behaviour, not asserted as a hard failure — see §10 (resilience). Postgres restarted and confirmed healthy before subsequent tests ran. |

### 4.2 Keycloak integration (`tests/integration_pass5/test_keycloak_integration.py`)

| ID | Objective | Status | Finding |
|---|---|---|---|
| KC-INT-01 | Publisher A login produces a valid, well-formed token | PASS | — |
| KC-INT-02 | Token has a non-empty `sub` | **FAIL** | Root cause in §3. |
| KC-INT-03 | `Principal.sub` (via persisted `issuer_id`) equals the JWT `sub` | **FAIL** | Same root cause; `issuer_id=''`, `sub=None`. |
| KC-INT-04 | Registration stores `issuer_id` equal to `sub` (Publisher B) | **FAIL** | Same root cause, confirmed for a second account. |
| KC-INT-05 | `publisher` role accepted | PASS | — |
| KC-INT-06 | No-role user (`user-c`) rejected from `/register` | PASS (403) | — |
| KC-INT-07 | No-role user rejected from `/records` | PASS (403) | — |
| KC-INT-08 | Malformed bearer → controlled 401, not 500 | **FAIL** | Reconfirms Finding 3 with a real HTTP round trip against a real Keycloak-backed backend (previously unit-mocked only). `jwt.get_unverified_header()` in `keycloak.py::KeycloakVerifier.verify()` runs before any try/except in scope; a non-JWT-shaped bearer string raises unhandled, producing a raw `500 Internal Server Error`. |
| KC-INT-09 | Expired token rejected (waited out the real 300s Keycloak default lifespan, no forged signature) | PASS (401, "Token expired") | — |
| KC-INT-10 | Wrong/unverifiable issuer path → 401 (kid-tamper proxy — see limitation below) | PASS | — |
| KC-INT-11 | Wrong/unverifiable audience path → 401 (kid-tamper proxy) | PASS | — |
| KC-INT-12 | Missing token rejected | PASS (401) | — |
| KC-INT-13 | Full JWKS/OIDC-discovery flow works against real Keycloak | PASS | — |

**Limitation on KC-INT-10/11**: as in the 2026-09-09 pass, a *validly signed* token from a genuinely different issuer/audience needs a second Keycloak realm and its private key. Both tests use the same kid-tampering proxy as the historical `AUTH-5` test (forces the same "unverifiable token → 401" path before `iss`/`aud` are even evaluated). This is documented, not silently substituted.

### 4.3 Multi-publisher isolation (`tests/integration_pass5/test_isolation.py`)

| ID | Objective | Status | Finding |
|---|---|---|---|
| ISO-01 + ISO-02 (one pytest test, `test_iso_01_and_02_cross_publisher_visibility`, two assertions) | Publisher A's `GET /records` shows only A's records; Publisher B's shows only B's | **FAIL** (counts as **1** in the pytest-level tally in §9, not 2 — see note below) | **IDOR CONFIRMED with live two-account evidence.** A's response included B's record (`publisher-b-secret.pdf`) AND B's response included A's record. |
| ISO-03 | Exact fields of the leaked cross-publisher record | PASS (documents the leak) | All 6 fields exposed: `record_id, content_hash, filename, transaction_hash, created_at, issuer_id`. Full leaked record captured in test output. |
| ISO-04 | Isolation failure persists across multiple records per publisher | **FAIL** | A's listing spanned 3 distinct `issuer_id` values after both publishers registered multiple documents (confirms it's a whole-table leak, not a one-off). |
| ISO-05 | No separate single-record endpoint exists to probe a second IDOR vector | PASS (N/A confirmed) | `GET /records/{id}` → 404; not a route in this API. |

This is the single most important confirmation in this pass: the 2026-09-09 finding was necessarily a **single-account proxy** (only one production credential was available). This pass had two independently authenticated, real Keycloak accounts and reproduced the exact same leak directly.

### 4.4 Anvil / blockchain integration (`tests/integration_pass5/test_blockchain_integration.py`)

**Scope note**: Anvil-only, local Ethereum-compatible dev chain. Nothing below is evidence about any public Ethereum network.

| ID | Objective | Status | Finding |
|---|---|---|---|
| BC-INT-01 | Connect to real Anvil | PASS | — |
| BC-INT-02/03 | Register content → real tx created and mined | PASS | Mined in block 1, `status=1`. |
| BC-INT-04/05 | On-chain value == uploaded SHA-256 | PASS | Exact match confirmed. |
| BC-INT-06 | DB `transaction_hash` resolves to a real Anvil transaction | PASS | — |
| BC-INT-07 | Unknown transaction hash / unregistered content_hash | PASS | `verify_by_hash` → `valid:false` (DB-level, never reaches Anvil); direct `web3.eth.get_transaction` on a never-broadcast hash → `TransactionNotFound`, as expected. |
| BC-INT-08 | Pending-transaction handling | **SKIP** | Anvil in this compose config auto-mines every transaction instantly (no `--block-time`); a genuinely pending tx is not reproducible without changing Anvil's mining mode, out of scope for this pass. |
| BC-INT-09 | Anvil unavailable during `/register` | PASS (documented) | `500 "Blockchain connection failed"` — the connector's own `HTTPException(500)` from `_connect()`, propagated with no wrapping. Anvil restarted and confirmed healthy afterward. |
| BC-INT-10 | Anvil container restart — data survival | PASS (documents a real limitation) | Transaction registered before the restart was **lost** (`TransactionNotFound`) after `docker compose restart anvil`. Anvil's default in-memory chain has no state-persistence flag configured. Documented explicitly as an environment/prototype limitation, not a claim about any real chain's durability. |

### 4.5 Full provenance golden journey (`tests/integration_pass5/test_provenance_journey.py`)

| ID | Objective | Status | Finding |
|---|---|---|---|
| PROV-01 | Full journey: real Keycloak login → upload → SHA-256 → real Postgres row → real Anvil tx (mined) → on-chain value read back → public unauthenticated verify (by upload AND by hash), all matching | **PASS** (with one documented sub-step exception) | Confirmed: `uploaded_sha256 == content_hash == onchain_value`; tx mined (`status=1`); public `POST /verify` → `valid:true`; public `GET /verify/{hash}` → `valid:true`. The one sub-step that does **not** hold is "issuer_id equals Publisher A's sub" — because of Finding 2 (§3); this is printed and documented in the test, not silently passed. |
| PROV-02 | Tamper test: flip 1 byte post-registration → public verify must return `valid:false` | PASS | Confirmed; original/modified SHA-256 differ, verify correctly reports invalid. |
| PROV-03 | Verify a document that was never registered | PASS | `valid:false`, no `record_id` in the response. |

This is the highest-value evidence in this pass for the paper: the entire chain of custody (upload → hash → DB → blockchain → public re-verification) is empirically confirmed to work correctly end-to-end on real infrastructure, with the one already-known caveat (publisher identity attribution) called out explicitly rather than hidden.

### 4.6 File validation (`tests/integration_pass5/test_file_validation.py`)

**Scope**: backend HTTP layer only. This local stack runs the FastAPI backend directly on `:41012` with no frontend dev server and no nginx in front of it. Frontend (Next.js client-side Dropzone checks) and nginx (`client_max_body_size`) layers are **not** re-verified in this pass — see the 2026-09-09/13 passes and the frontend repo's own QA docs for those.

| ID | Objective | Status | Finding |
|---|---|---|---|
| FILE-01 | Valid PDF | PASS | — |
| FILE-02 | `.txt` content renamed `.pdf` | PASS (documents known gap) | Accepted (200) — reconfirms no magic-byte/content validation, source-level finding already known, now confirmed live. |
| FILE-03 | Real PDF bytes, spoofed `image/png` MIME | PASS (documents known gap) | Accepted (200) — content-type header is trusted, not verified. |
| FILE-04 | Corrupt/non-conformant `%PDF` header | PASS (documents known gap) | Accepted (200) — no structural PDF validation exists at all. |
| FILE-05 | Empty file | PASS | Correctly rejected, 400. |
| FILE-06 | Exactly at 20 MB limit | PASS | Accepted, 200. |
| FILE-07 | 1 byte over 20 MB limit | PASS | Correctly rejected, 413. |
| FILE-08 | Unicode filename | PASS | Accepted, filename preserved. |
| FILE-09 | Very long filename (504 chars) | **FAIL — NEW FINDING** (fixed to genuinely assert 2026-09-14; originally had no assertion and always reported PASS regardless of the server's response — see §9 reconciliation) | `500 Internal Server Error`. `registry_records.original_filename` is `VARCHAR(255)`; nothing in `documents.py::register()` validates filename length before the DB write, so Postgres itself raises (`StringDataRightTruncation` or equivalent), which propagates unhandled. |
| FILE-10 | Path-traversal-style filename (`../../etc/passwd.pdf`) | PASS | Accepted, 200, stored as a literal string. No filesystem effect — confirms the separately-known finding that uploaded files are never written to disk at all (hashed in memory only), so path-traversal via filename has no filesystem consequence here, only a display/DB-content concern. |
| FILE-11 | Same bytes, different filename | PASS | Second request correctly resolves to `already_existed:true`, same `record_id`; filename from the *first* registration is what's returned (content identity, not filename, drives dedup — as designed). |
| FILE-12 | One-byte-different PDF | PASS | Distinct hashes, distinct records, as expected. |

### 4.7 Resilience (`tests/integration_pass5/test_resilience.py`, plus DB-INT-10/BC-INT-09 above)

| ID | Objective | Status | Finding |
|---|---|---|---|
| RES-01 | Postgres unavailable | see DB-INT-10 | 500, no partial state observed after restore. |
| RES-02 | Keycloak unavailable | PASS (documents behaviour) | New login attempts correctly fail (connection refused) while Keycloak is down. An **already-issued** token continued to be accepted by the backend while Keycloak was stopped — `KeycloakVerifier` caches JWKS for up to 3600s (`jwks_ttl_seconds`), so a backend that has already fetched a signing key can keep validating tokens signed with it even with Keycloak fully offline. This is expected caching behaviour, not a bug, but worth noting for the paper: Keycloak availability and backend auth availability are not perfectly coupled. |
| RES-03 | Anvil unavailable during `/register` | see BC-INT-09 | 500. |
| RES-04 | Anvil unavailable during `/verify` (for an already-registered doc) | PASS (documents behaviour) | `verify_full()` propagates the connector's `500` unhandled, same shape as register-side. |
| RES-05 | Transaction timeout/pending | not reproducible | See BC-INT-08 — Anvil auto-mines instantly in this config. |
| RES-06 | Backend container restart preserves already-committed data | PASS | Record registered before `docker compose restart trustmark-app` was still present and correct after the container came back healthy. |
| RES-07 | Frontend/backend network failure | NOT RUN | No frontend dev server was started as part of this pass (backend-only local stack, see §4.6 scope note) — out of scope for this pass, not fabricated. |

### 4.8 Locust reproducibility — see §9 below.

---

## 5. Findings — Classified

### 5.1 New findings (first observed in this pass)

| # | Finding | Evidence level |
|---|---|---|
| N1 | **Root cause of Finding 2 identified**: empty `clientScopes` in `realm-export.json` means no protocol mapper anywhere emits the `sub` claim — reproducible on a completely fresh Keycloak install, not a production-only drift. Confirmed by add/remove mapper experiment. | INTEGRATION-REAL-LOCAL |
| N2 | Filename > 255 chars → unhandled `500` (Postgres column-length violation surfaces raw, no validation in `documents.py`). | INTEGRATION-REAL-LOCAL |
| N3 | Keycloak's default access-token lifespan for this realm is 300s (no `accessTokenLifespan` override in the realm export) — worth knowing for anyone re-running long integration/load tests against a fresh import of this realm. | INTEGRATION-REAL-LOCAL |
| N4 | Backend's JWKS caching (up to 3600s) means an already-issued, already-verified token keeps working even with Keycloak fully stopped — auth availability is not tightly coupled to Keycloak's own uptime once a key has been cached. | INTEGRATION-REAL-LOCAL |
| N5 | `tests/locust/locustfiles/register.py` sent **no Authorization header at all** — 100% of its historical load-test traffic against any backend with `TEST_MODE=False` would have failed with 401 before any of the file's own assertions ran. See §9. | SOURCE-OBSERVED / confirmed INTEGRATION-REAL-LOCAL after fix |
| N6 | `tests/locust/locustfiles/verify.py`'s `verify_not_stored` task used a non-hex, wrong-length literal (`"ThisIsNotAStoredHash"`), which the backend's own format validation rejects with `400` before ever reaching the "not registered" branch the task intended to exercise — another guaranteed, permanent 100% failure unrelated to actual server behaviour. See §9. | SOURCE-OBSERVED / confirmed INTEGRATION-REAL-LOCAL after fix |

### 5.2 Reconfirmed findings (previously known, now confirmed with stronger/live evidence)

| # | Finding | Prior evidence level | This pass's evidence level |
|---|---|---|---|
| R1 | Finding 1 (IDOR, `GET /records` has no `issuer_id` filter) | UNIT-MOCKED (`test_documents.py`) + SOURCE-OBSERVED + single-account proxy (2026-09-09 `AUTH-7`) | **INTEGRATION-REAL-LOCAL, two independent real accounts** |
| R2 | Finding 2 (empty `issuer_id` / missing `sub` claim) | E2E-LIVE-HISTORICAL (production only) + UNIT-MOCKED | **INTEGRATION-REAL-LOCAL, root cause now identified (see N1/§3)** |
| R3 | Finding 3 (malformed bearer → 500, not 401) | UNIT-MOCKED only | **INTEGRATION-REAL-LOCAL, real HTTP round trip** |
| R4 | No content/magic-byte validation of uploaded files | E2E-LIVE-HISTORICAL (production `REG-6`) | **INTEGRATION-REAL-LOCAL** |
| R5 | Uploaded files are never written to disk, hashed in memory only | SOURCE-OBSERVED (master export) | **INTEGRATION-REAL-LOCAL confirmation via FILE-10 (path-traversal filename has zero filesystem effect)** |
| R6 | `TEST_MODE` auth-bypass mechanism is live in the production container entrypoint (`python -m trustmark.main`) | UNIT-MOCKED (`test_main_test_mode.py`) + SOURCE-OBSERVED | Not re-executed against a live container in this pass (would require restarting the local stack with `TEST_MODE=True`, deliberately not done — see §10 "do not fix/toggle before baseline" discipline applied symmetrically to not *enabling* a bypass either). Still SOURCE-OBSERVED + UNIT-MOCKED only. |

### 5.3 Historical findings NOT re-touched in this pass

- DUP-4-style race condition (two concurrent registrations both creating separate blockchain transactions before either commits) — DB-INT-05 in this pass ran 5 concurrent requests and got exactly 1 authoritative row, but this does not prove the race window can never be hit; timing-dependent races are inherently non-exhaustive from a handful of runs. Not claimed as "fixed" or "disproven," just not reproduced this time.
- Locust historical totals (8,880 / 8,245 / 8,165) are **not reconciled or explained** by this pass — see §9's explicit non-comparison.

### 5.4 Test-code findings (bugs in the test/benchmark code itself, not the application)

| # | File | Bug | Fix applied |
|---|---|---|---|
| T1 | `tests/locust/locustfiles/verify.py` | `response_body["record_id"] is not str` and the `created_at` equivalent compare a **value** to the **type object** `str` using identity (`is`) — this is `True` for every string value that has ever existed, so both branches fired as failures unconditionally whenever reached. | Changed to `not isinstance(..., str)`. |
| T2 | `tests/locust/locustfiles/verify.py` | `response_body["issuer_id"] != ""` encoded the *known bug* (Finding 2) as the *expected/correct* value — meaning a hypothetical fix to Finding 2 would have made this locustfile's tests fail, backwards from intent. | Changed to assert issuer_id is **non-empty** (the intended-correct behaviour); this now fails while Finding 2 is unfixed, which is accurate given `--DO NOT change expected result merely to match current behaviour--`. |
| T3 | `tests/locust/locustfiles/register.py` | No `Authorization` header sent at all — guaranteed 401 on any backend enforcing real auth (`TEST_MODE=False`, the current default everywhere per `docker/.env`/`.env.example`/`.env.save`). | Added `on_start()` fetching a real Keycloak token for `publisher-a`, attached as a Bearer header on both tasks. |
| T4 | `tests/locust/locustfiles/verify.py` | `verify_not_stored` task's hardcoded hash literal (`"ThisIsNotAStoredHash"`) is not a 64-char hex string, so the backend's own format validation rejects it with 400 before the "not registered" branch is ever reached — a guaranteed, permanent failure unrelated to server load or correctness. | Replaced with a well-formed, never-registered 64-hex-char hash (`"ff"*32`). |
| T5 | `tests/integration_pass5/test_file_validation.py` | `test_file_09_very_long_filename` had no assertion at all — it printed the response status/body and always reported PASS regardless of what the server did, even though the narrative in §4.6 documented it as a live FAIL/finding (unhandled 500). This is exactly the kind of self-inconsistency that produced the 45/7/1 vs. corrected 44/8/1 count discrepancy caught during the 2026-09-14 verification rerun (see §9). | Added `assert resp.status_code != 500` with a message explaining the expected clean 4xx/truncation vs. the actual crash. |

All four are documented here with the exact original code (captured before editing) so the change is auditable; none of them touch application code, only the test/benchmark scripts.

---

## 6. Locust Reproducibility (Section 9)

### 6.1 Fixes applied

See §5.4 (T1-T4). All changes are in `tests/locust/locustfiles/register.py` and `verify.py`, diffed against git history for full traceability.

### 6.2 Benchmark configuration

- Target: the same local stack as the rest of this pass (`http://127.0.0.1:41012`), **not** production.
- Locust 2.43.4 (installed via `uv sync` into this repo's own `.venv`).
- Config: 10 users, spawn rate 1/s, run time 60s, **headless**, 3 independent runs back-to-back (≈3s gap between runs).
- Raw artifacts (CSV, HTML report, log) for every run: `_docs/qa/pass5/locust-runs/run{1,2,3}/`.

### 6.3 Explicit non-comparison

**Historical paper figures**: 8,880 / 8,245 / 8,165 (conflicting totals, source/methodology not fully known from this pass's vantage point — see master export for what is known).

**This pass's new benchmark**: run against a `TEST_MODE=False`, fully-Keycloak-authenticated local stack, with the register.py auth bug fixed. These are **not** claimed to be the same environment, same `TEST_MODE` setting, same hardware, or same test-code version as whatever produced the historical numbers — there is no way to confirm that from the numbers alone. Present the two side by side in the paper only as "historical (methodology uncertain)" vs. "new, reproducible, fully documented" — never as a direct before/after comparison.

### 6.4 Results (raw, 3 independent 60s runs, 10 users, spawn-rate 1/s)

| Run | Total requests | Total failures | Failure % | Requests/s | Failures/s |
|---|---|---|---|---|---|
| 1 | 2,743 | 285 | 10.4% | 46.32 | 4.81 |
| 2 | 2,735 | 286 | 10.5% | 46.32 | 4.84 |
| 3 | 2,735 | 269 | 9.8% | 46.31 | 4.56 |

**All failures across all 3 runs are exactly one thing**: `GET /api/v1/verify/{known-hash}` failing the (correct, fixed) assertion that `issuer_id` is non-empty — i.e., every single failure is Finding 2 (§3) being exercised under load, not a server error, not a crash, not a timeout. `/register`, `/verify` (POST), `/api/v1/health`, and the "verify a never-registered hash" path all had **0 failures** across all 3 runs.

### 6.5 Per-endpoint latency (Run 1, representative — see `run{1,2,3}_stats.csv` for all three)

| Endpoint | Requests | Median (ms) | Mean (ms) | Min | Max | p90 | p95 | p99 |
|---|---|---|---|---|---|---|---|---|
| `GET /api/v1/health` | 831 | 24 | 25.2 | 1.7 | 112.7 | 42 | 47 | 56 |
| `POST /api/v1/register` | 1,097 | 20 | 21.1 | 2.8 | 108.5 | 35 | 41 | 51 |
| `POST /api/v1/verify` | 267 | 24 | 25.8 | 9.3 | 110.6 | 39 | 43 | 53 |
| `GET /api/v1/verify/{known-hash}` | 285 | 35 | 34.7 | 8.3 | 78.8 | 48 | 52 | 59 |
| `GET /api/v1/verify/{unregistered-hash}` | 263 | 24 | 23.9 | 3.0 | 61.4 | 38 | 42 | 51 |
| **Aggregate** | 2,743 | 23 | 24.5 | 1.7 | 112.7 | 41 | 46 | 55 |

(Runs 2 and 3 are consistent with Run 1 within a few ms across every percentile — see raw CSVs for exact per-run numbers; not reproduced in full here to avoid a wall of near-duplicate tables.)

### 6.6 Aggregate across 3 runs (mean of run-level aggregates, informational only)

- Mean aggregate throughput: **46.32 req/s** (essentially identical across all 3 runs — the target throughput given `constant_throughput(5)` per simulated user × ~10 users across 5 task types is inherently self-limiting, not a server capacity ceiling).
- Mean failure rate: **10.2%**, entirely attributable to Finding 2 as described in §6.4.
- No difference in latency distribution between runs — the stack behaves consistently across independent trials, which is itself useful reproducibility evidence for the paper (i.e., the local replica is not exhibiting run-to-run instability).

### 6.7 What this benchmark does and does not prove

- **Does prove**: the backend, under a modest, fully-authenticated, real-Keycloak-verified, real-Postgres, real-Anvil load (46 req/s sustained for 3×60s), has zero unexpected server-side failures — every single failure recorded is the already-diagnosed Finding 2, not a crash, timeout, or new defect.
- **Does not prove**: nothing here establishes a maximum throughput or breaking point — `constant_throughput(5)` per user caps demand well below any ceiling this stack might have. A capacity/stress test would need a different Locust load shape (e.g., unbounded wait time or a step-load pattern), which was out of scope for this pass.

---

## 7. Raw Artifact Inventory

| Artifact | Path |
|---|---|
| Keycloak user provisioning script | `_docs/qa/pass5/setup_keycloak_users.sh` |
| Integration test suite (53 tests) | `tests/integration_pass5/*.py` |
| Locust fix — register.py | `tests/locust/locustfiles/register.py` (diff vs. git history) |
| Locust fix — verify.py | `tests/locust/locustfiles/verify.py` (diff vs. git history) |
| Locust run 1 raw CSV/HTML/log | `_docs/qa/pass5/locust-runs/run1/` |
| Locust run 2 raw CSV/HTML/log | `_docs/qa/pass5/locust-runs/run2/` |
| Locust run 3 raw CSV/HTML/log | `_docs/qa/pass5/locust-runs/run3/` |
| This report | `_docs/qa/pass5/pass-5-integration-reproducibility.md` |

---

## 8. Remaining Gaps

- **DUP-4-style true race condition**: not forced to occur in this pass's 5-concurrent-request trial (exactly 1 authoritative row resulted). A tighter, larger-N concurrency test (e.g., 50+ simultaneous identical requests with a barrier to synchronize dispatch) would be needed to more aggressively probe the check-then-insert window in `documents.py::register()`.
- **KC-INT-10/11 (true issuer/audience mismatch)**: still only a kid-tampering proxy, same limitation as the 2026-09-09 pass — needs a second local Keycloak realm with its own signing key to test a *validly signed, wrong-issuer* token.
- **BC-INT-08 (pending transaction)**: not reproducible without reconfiguring Anvil's mining mode (`--block-time`), out of scope for this pass.
- **RES-07 (frontend/backend network failure)**: no frontend dev server was part of this pass's local stack; not run.
- **Frontend/nginx layers of file validation** (FILE section): explicitly out of scope for this backend-only local stack — see 2026-09-09/13 passes for what's known there.
- **`TEST_MODE=True` live re-verification**: the auth-bypass finding (R6) remains UNIT-MOCKED + SOURCE-OBSERVED only; deliberately not toggled live in this pass to avoid conflating "finding evidence" with "exercising a security bypass," even in a disposable local environment.
- **Locust historical-number reconciliation**: explicitly not attempted (see §6.3) — the historical benchmark's exact target configuration cannot be reconstructed from the numbers alone.

---

## 9. Exact Final Test Counts

**Reconciliation note (2026-09-14)**: the first published version of this report said 45 PASS / 7 FAIL / 1 SKIP. On request, the entire suite was rebuilt from a fresh disposable stack and rerun independently to verify that number. Two discrepancies were found and corrected, both in this report/test-suite's own bookkeeping — **neither changes any application-level finding**:

1. `ISO-01` and `ISO-02` are documented in §4.3 as two named requirement IDs, but they are implemented as **one** pytest test function (`test_iso_01_and_02_cross_publisher_visibility`, two assertions). Counted at the pytest execution level (the correct level for "did this actually run and pass/fail"), that's 1 FAIL, not 2.
2. `FILE-09` (very long filename) was narratively documented as "FAIL — NEW FINDING" because the server genuinely returns an unhandled `500`, but the test function itself had **no assertion** — it only printed the status code, so pytest reported PASS unconditionally regardless of what the server actually did. An assertion was added (`assert resp.status_code != 500`) and the fix is included in the commit history for this pass. The server-side bug this test exercises is unchanged; only the test's ability to detect it was fixed.

**Final, independently-reran, ground-truth counts**:

- **New integration tests this pass**: 53
  - **PASS: 44**
  - **FAIL (all documenting real, reproduced findings, none a test defect): 8** — DB-INT-07, FILE-09, KC-INT-02, KC-INT-03, KC-INT-04, KC-INT-08, `test_iso_01_and_02_cross_publisher_visibility` (covers ISO-01+ISO-02), ISO-04
  - SKIP: 1 — BC-INT-08
- Every number above comes from an actual, complete `pytest` run against a freshly rebuilt local stack (Postgres/Keycloak/Anvil/backend all recreated from scratch, test accounts recreated), not inferred or carried over from the first run.
- **Locust runs**: 3 independent 60-second runs, 2,743 / 2,735 / 2,735 total requests respectively (8,213 combined), 285 / 286 / 269 failures respectively (840 combined, 10.2% aggregate) — all attributable to Finding 2, zero unexpected server errors. (Not rerun during this reconciliation pass — no code change affects Locust's target behaviour.)
- **No unit tests were added** — the 94 backend / 30 frontend unit counts from the master export are unchanged by this pass.

**No application code was modified in this reconciliation pass** — only the `FILE-09` test's assertion. All 8 FAILs above remain open findings pending a separate, explicitly-scoped fix pass.
