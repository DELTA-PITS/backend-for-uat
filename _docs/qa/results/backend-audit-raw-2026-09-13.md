# PITS Backend Forensic Audit — Raw Facts
Repo: `/Users/laksaersa/GitHub/_personal/_PITS/backend-for-uat`
Audit date: 2026-09-13
HEAD commit at audit time: `7261708` "Bind Postgres, Keycloak, Anvil, and app ports to 127.0.0.1 only"
`git status -sb` at audit time:
```
## main...origin/main
 M .gitignore
 M tests/trustmark/infra/test_blockchain_connector.py
?? _docs/
?? tests/api/
?? tests/trustmark/api/
?? tests/trustmark/infra/auth/
?? tests/trustmark/infra/test_commons.py
?? tests/trustmark/test_main_test_mode.py
```
i.e. at audit time, `_docs/qa/*`, `tests/api/*`, `tests/trustmark/api/*`, `tests/trustmark/infra/auth/*`, `tests/trustmark/infra/test_commons.py`, `tests/trustmark/test_main_test_mode.py` are all **untracked** (not committed to git) in this working copy, and `tests/trustmark/infra/test_blockchain_connector.py` has an **uncommitted modification** (diff captured verbatim in section D).

`git log --oneline -30` (full history, only 6 commits exist):
```
7261708 Bind Postgres, Keycloak, Anvil, and app ports to 127.0.0.1 only
c6a5638 Allow HTTP for local Keycloak realm
95464d2 Remove GitHub Actions workflows from UAT repository
f3525d4 Exclude GitHub Actions workflows from UAT release
4b94c81 Exclude GitHub Actions workflow from UAT release
a70d53b Initial stakeholder UAT release
```
`git log --follow --oneline` for `src/trustmark/main.py`, `src/trustmark/api/v1/documents.py`, `src/trustmark/infra/auth/keycloak.py`, `src/trustmark/infra/blockchain_connector.py`: all show only `a70d53b Initial stakeholder UAT release` — i.e. these source files have not been modified since the initial commit, per git history in this repo copy. (This repo is a squashed/exported "UAT" copy — `INFORMATION NEEDED: whether a fuller history exists in an upstream/private repo this was exported from.`)

---

## A. Backend Architecture Facts

### A.1 Framework & runtime
- Framework: **FastAPI 0.135.3** (`uv.lock`; `pyproject.toml:14` pins `fastapi>=0.135.1`)
- ASGI server: **uvicorn 0.44.0** (`pyproject.toml:25` pins `uvicorn>=0.41.0`)
- ORM: **SQLAlchemy 2.0.49** (`pyproject.toml:23` pins `sqlalchemy>=2.0.48`); `pyproject.toml:21` also separately lists `sqlalchemy-orm>=1.2.10` (a different, effectively-obsolete PyPI package predating SQLAlchemy's own ORM being built-in — see Finding D.9)
- JWT lib: **python-jose[cryptography] 3.5.0** (`pyproject.toml:27`)
- Blockchain lib: **web3.py 7.16.0** (`pyproject.toml:30`), with **eth-account 0.13.7** (transitive, pinned in `uv.lock`)
- Config: **Dynaconf 3.2.13** (`pyproject.toml:13` pins `dynaconf>=3.2.12`)
- DB driver: **psycopg2-binary 2.9.11** (`pyproject.toml:19`)
- Test framework: **pytest 9.0.3** (`pyproject.toml:20` pins `pytest>=9.0.2`)
- Load test: **locust 2.43.4** (`pyproject.toml:17` pins `locust>=2.43.3`)
- Python: `requires-python = ">=3.12.8"` (`pyproject.toml:11`); Docker image pins exactly `python:3.12.8-bookworm` (`docker/Dockerfile:1`). Local `uv run` resolved interpreter: Python 3.12.14 (uv-managed, per `uv run pytest` banner). Host system `python3` is 3.9.6 (irrelevant — `uv run` uses its own managed 3.12.14, not host python).
- Package name/version: `trustmark` `0.1.0-dev` (`pyproject.toml:2-3`)
- No `[tool.pytest.ini_options]` block found in `pyproject.toml` via grep — pytest picks up `pyproject.toml` as its config root (`configfile: pyproject.toml` in pytest banner) but no custom options are set there (`INFORMATION NEEDED: exact pytest config section — grep for "[tool.pytest" returned nothing, meaning either no options are configured, or they live under a different exact key not matched by the grep pattern used`).

### A.2 Endpoint list
All routers registered in `src/trustmark/main.py:75-91`.

| Method | Path | File:line (handler) | Auth requirement | Request shape | Response shape (fields: type) |
|---|---|---|---|---|---|
| GET | `/health` | `src/trustmark/main.py:70-72` | none | none | `{"status": str}` |
| GET | `{API_PREFIX}/health` | `src/trustmark/api/v1/metrics.py:12-20` (router mounted `src/trustmark/main.py:87-91`) | none | none | Pydantic `HealthResponse`: `{"status": str}` |
| POST | `{API_PREFIX}/register` | `src/trustmark/api/v1/documents.py:48-76` | **bearer + `publisher` role** (`Depends(require_roles("publisher"))`, `documents.py:51`; router also globally wrapped with `Depends(get_current_principal)` at `main.py:79`, so principal is resolved twice — see Finding D.10) | `multipart/form-data`: `file: UploadFile` (required) | `{"stored": bool, "already_existed": bool, "record_id": str, "content_hash": str, "transaction_hash": str, "issuer_id": str, "filename": str\|None, "content_type": str\|None, "created_at": str}` (built by `_record_payload`, `documents.py:36-45`) |
| GET | `{API_PREFIX}/records` | `src/trustmark/api/v1/documents.py:79-85` | **bearer + `publisher` role** (`Depends(require_roles("publisher"))`, `documents.py:81`) | none | `{"records": [ {same per-record shape as register response} ]}` |
| POST | `{API_PREFIX}/verify` | `src/trustmark/api/v1/documents.py:88-106` | **none** (public; router_verify is mounted at `main.py:82-86` with no `Depends(get_current_principal)`) | `multipart/form-data`: `file: UploadFile` (required) | If not found: `{"valid": false, "content_hash": str}`. If found & mismatched: `{"valid": false, "content_hash": str, "record_id": str}`. If found & matched: `{"valid": true, ...record fields, "blockchain_timestamp": str}` |
| GET | `{API_PREFIX}/verify/{file_hash}` | `src/trustmark/api/v1/documents.py:109-128` | **none** (public) | path param `file_hash: str` | same 3-shape pattern as POST /verify |

`API_PREFIX` = `settings.API_PREFIX` = `/api/v1` (`conf/settings.toml:6`, `api_prefix = "/api/v1"`).

Note: `main.py:70-72` defines a **second, duplicate** `/health` route directly on `app` (no prefix) alongside the prefixed `{API_PREFIX}/health` from `metrics.router` — two different health endpoints exist at two different paths with two different response models (plain dict vs Pydantic `HealthResponse`) — see Finding D.11.

Files present but **empty (0 bytes)**, confirmed via `wc -l`: `src/trustmark/api/v1/exports.py`, `src/trustmark/api/v1/inventor.py`, `src/trustmark/api/v1/status.py`, `src/trustmark/api/v1/verification.py`, `src/trustmark/infra/cache.py`, `src/trustmark/infra/reports.py`, `src/trustmark/infra/hash_generator.py`, `src/trustmark/services/__init__.py`, `src/trustmark/services/etherum_connector.py` [sic, misspelled], `src/trustmark/services/github_connector.py`, `src/trustmark/services/graphdb_connector.py`, `src/trustmark/services/x_connector.py` — none of these are imported/wired anywhere in `main.py`, so none of these paths/services are live. `src/trustmark/models/documents.py` (17 lines) defines Pydantic models `Provenance`/`Document` but these are **not imported or used by any router** — dead code (see Finding D.1).

### A.3 Database
DBMS: configurable via `DATABASE_URL` env var (`src/trustmark/infra/db.py:7`); falls back to SQLite at `settings.DB_PATH` if unset (`db.py:8-12`, `db_path` = `conf/settings.toml:5`). Production docker-compose wires Postgres (`docker/docker-compose.yml:76`: `DATABASE_URL: postgresql+psycopg2://...@postgres:5432/...`).

Single table, single model, `src/trustmark/registry/models.py`:

| Column | Type | Constraints | File:line |
|---|---|---|---|
| `id` | `String(36)` | PK, default `uuid.uuid4()` | `models.py:12` |
| `content_hash` | `String(64)` | unique, indexed, not null | `models.py:13` |
| `transaction_hash` | `String(66)` | unique, not null | `models.py:14` |
| `issuer_id` | `String(128)` | not null (but can be empty string `""` — see Finding D.2) | `models.py:15` |
| `original_filename` | `String(255)` | nullable | `models.py:16` |
| `content_type` | `String(128)` | nullable | `models.py:17` |
| `created_at` | `DateTime(timezone=True)` | not null, server default `func.now()` | `models.py:18` |

Table name: `registry_records` (`models.py:10`). No FKs (single table, no relations). Schema is created via `Base.metadata.create_all(bind=engine)` in the FastAPI `lifespan` handler (`main.py:36`) — i.e. no Alembic/migration tooling found anywhere in the repo (`INFORMATION NEEDED: confirm no alembic dir exists` — none found in `find` listing above).

`RegistryRecord.id` uses a `String(36)` primary key with an application-generated UUID4 default (not a DB-native UUID type, not autoincrement) — every insert generates its own id client-side (`models.py:12`).

### A.4 Auth (Keycloak)
- File: `src/trustmark/infra/auth/keycloak.py` (213 lines).
- Realm/client names as referenced **in code**: none hardcoded — `KEYCLOAK_ISSUER`, `KEYCLOAK_ISSUER_URL`, `KEYCLOAK_AUDIENCE` are all read from Dynaconf settings, which resolve from env vars via `@format {env[...]}` in `conf/settings.toml:12-14`. The realm/client names only appear in `docker/.env.example` (data, not code): `KEYCLOAK_ISSUER=http://localhost:8080/realms/nextjs-kc` (realm `nextjs-kc`), `KEYCLOAK_AUDIENCE=account` (audience/client `account`).
- `KEYCLOAK_ROLES_CLIENT_ID` — referenced via `getattr(settings, "KEYCLOAK_ROLES_CLIENT_ID", None)` at `keycloak.py:192` — is **not defined anywhere** in `conf/settings.toml` or `docker/.env.example`. This means `client_id` passed into `_extract_roles()` is always `None` in every configuration file present in this repo, so the client-roles branch (`_extract_roles`, `keycloak.py:167-171`) is **dead in practice** unless an operator sets `KEYCLOAK_ROLES_CLIENT_ID` out-of-band (not documented anywhere in this repo) — see Finding D.12. Only realm roles (`claims["realm_access"]["roles"]`) are actually extracted in any configuration present in the repo.
- JWT claims the code actually reads, with call sites:
  - `claims.get("sub", "")` → `Principal.sub` — `keycloak.py:196`
  - `claims.get("preferred_username")` → `Principal.username` — `keycloak.py:197`
  - `claims.get("email")` → `Principal.email` — `keycloak.py:198`
  - `(claims.get("realm_access") or {}).get("roles") or []` — `keycloak.py:164` (inside `_extract_roles`)
  - `(claims.get("resource_access") or {}).get(client_id, {}).get("roles", [])` — `keycloak.py:169` (inside `_extract_roles`, gated on `client_id` being truthy)
  - Full raw `claims` dict is also stored on `Principal.claims` — `keycloak.py:200`
- `Principal` construction: `keycloak.py:195-201`, inside `get_current_principal()` (`keycloak.py:184-201`), a `@dataclass(frozen=True)` defined at `keycloak.py:21-27` with fields `sub: str, username: Optional[str], email: Optional[str], roles: Set[str], claims: dict[str, Any]`.
- Role extraction (`_extract_roles`, `keycloak.py:161-173`): realm roles unioned with client roles (if `client_id` given) into one flat `Set[str]` — no distinction preserved between realm-level and client-level roles once in `Principal.roles`.
- `get_current_principal` — `keycloak.py:184-201`. Rejects if no creds or scheme isn't `bearer` (case-insensitive check, `.scheme.lower() != "bearer"`) → 401 "Missing bearer token" (`keycloak.py:187-188`). Otherwise calls `_verifier.verify(creds.credentials)` (module-level singleton `_verifier`, constructed at `keycloak.py:177-181`).
- `require_roles(*required)` — `keycloak.py:204-212`. Returns a FastAPI dependency closure; checks `required_set.issubset(principal.roles)`; raises 403 "Forbidden" if not satisfied (`keycloak.py:208-209`).
- `KeycloakVerifier.verify()` (`keycloak.py:113-158`): calls `jwt.get_unverified_header(token)` at line 114, **outside** any try/except (the nearest `try:` starts at line 130) — see Finding D.3. JWKS lookup by `kid` (`keycloak.py:115-128`), one cache-refresh retry on unknown kid (`keycloak.py:121-125`) before giving up with 401 "Unknown signing key" (`keycloak.py:127-128`). Actual decode at `keycloak.py:130-143`: `algorithms=["RS256", "PS256"]`, `issuer=self.expected_issuer`, `audience=self.audience`, with `verify_signature/verify_exp/verify_iss/verify_aud` all `True`. Exception handling: `ExpiredSignatureError` → 401 "Token expired" (`keycloak.py:145-147`); the `JWTClaimsError` branch is present but **commented out** (`keycloak.py:148-151`, dead/disabled code — iss/aud mismatches fall through to the generic `except JWTError` below instead, which still returns 401, so behavior is equivalent but the specific error message differentiation the comment implies was intended is not implemented); `JWTError` → 401 "Invalid token {e}" (`keycloak.py:152-155`); bare `except Exception` → 401 "Invalid token {e}" (`keycloak.py:156-158`, catches anything else including non-jose exceptions and still returns 401, not 500 — this is a broader catch than the raw `jwt.get_unverified_header` call at line 114 gets).
- `KeycloakVerifier.jwks()` caching: TTL-based (`jwks_ttl_seconds`, default 3600s, `keycloak.py:36`), in-process only (single dict + timestamp, `keycloak.py:42-43,104-111`) — no cross-process/shared cache; every uvicorn worker (if run with >1 worker) would maintain its own independent JWKS cache. `_fetch_jwks()` (`keycloak.py:45-102`) does two sequential HTTP GETs (OIDC discovery, then JWKS endpoint), each with 5s timeout, converting `requests.RequestException` → 503, `ValueError` (bad JSON) → 500, and validates `jwks_uri` shape (`keycloak.py:66-75`) and JWKS response shape (`keycloak.py:95-100`) before returning — **except** `oidc["jwks_uri"]` at line 66 is a **plain dict index**, not `.get()`, and is outside the enclosing try/except that wraps the *request* (the try at line 46 only covers the `requests.get`/`.json()` calls above it, not this indexing operation) — an unhandled `KeyError` results if Keycloak's discovery document omits `jwks_uri` — see Finding D.4.

### A.5 Hashing
- File: `src/trustmark/infra/hash_engine.py` (17 lines). Note: `src/trustmark/infra/hash_generator.py` also exists but is **completely empty (0 bytes)** — dead/unused file, not the real hash module (see Finding D.13; task instructions anticipated needing to search for the "actual" hash module — `hash_engine.py` is it, `hash_generator.py` is a red herring/vestigial file).
- Algorithm: **SHA-256** via stdlib `hashlib.sha256` (`hash_engine.py:1,13,17`).
- Two functions:
  - `generate_hash(content: str) -> str` (`hash_engine.py:11-13`): calls `canonicalize_text()` first, then hashes.
  - `generate_hash_from_bytes(content: bytes) -> str` (`hash_engine.py:16-17`): hashes raw bytes directly, **no canonicalization**.
- `canonicalize_text(content: str) -> bytes` (`hash_engine.py:4-8`): normalizes `\r\n`/`\r` → `\n`, strips trailing whitespace per-line (`.rstrip()`), joins with `\n`, then `.strip()` the whole result, then UTF-8 encodes.
- **What the live `/register` and `/verify` endpoints actually hash**: `documents.py:8` imports only `generate_hash_from_bytes`; both `register()` (`documents.py:55`) and `verify_full()` (`documents.py:91`) call `generate_hash_from_bytes(content)` on the **raw uploaded bytes**, with **no canonicalization applied at all** in the live HTTP path — the text-canonicalizing `generate_hash()` function exists and is tested but is **not used by any FastAPI endpoint**; it is only used by the locust load-test files (`tests/locust/locustfiles/verify.py:10,19`, reading a local file as text) — see Finding D.5. This means the "canonicalization" behavior (CRLF/whitespace normalization) documented and unit-tested for `generate_hash()` provides **no actual integrity-check robustness benefit** to real API clients hitting `/register` or `/verify`, since those paths hash raw bytes exactly as uploaded (any byte-for-byte difference, including a single trailing newline or a CRLF/LF line-ending change introduced by a client's upload pipeline, produces a different hash and a false `valid: false`).

### A.6 Blockchain
- File: `src/trustmark/infra/blockchain_connector.py` (85 lines).
- Library: **web3.py 7.16.0** (`uv.lock`), `eth_account`/`eth_account.signers.local.LocalAccount`, `hexbytes.HexBytes`.
- Class `BlockchainConnector` (alias `Blockchain_connector` for backward compat, `blockchain_connector.py:85`), constructed per-request in `documents.py:15-20` (`_get_blockchain_service()`) — a **new** `Web3`/account/connection is built on every single register/verify call, not a shared/pooled connection (see Finding D.14 — potential performance concern, not evaluated under load in this audit).
- Tx construction (write path), `create_transaction()`, `blockchain_connector.py:35-54`:
  - Payload = `VALUE_PREFIX + value.encode("utf-8")` where `VALUE_PREFIX = b"VALUE:"` (`blockchain_connector.py:8`), hex-encoded via `Web3.to_hex()` (`blockchain_connector.py:38`) and placed in the tx `data` field.
  - `value` passed in is the SHA-256 content hash hex string (called from `documents.py:62`: `blockchain_service.create_transaction(content_hash)`).
  - Transaction sent as a **self-transfer**: `"from": acct.address, "to": acct.address, "value": 0` (`blockchain_connector.py:41-43`) — no smart contract is deployed or called; the hash is stored purely as calldata on a zero-value self-transaction. **No smart contract exists anywhere in this repo** (confirmed: no `.sol` files, no ABI/bytecode artifacts found in the file listing).
  - EIP-1559 fields hardcoded: `gas: 50000`, `maxFeePerGas: 2 gwei`, `maxPriorityFeePerGas: 1 gwei` (`blockchain_connector.py:47-49`) — no dynamic fee estimation, no retry/replace-by-fee logic for stuck transactions.
  - `chainId: w3.eth.chain_id` (`blockchain_connector.py:46`) — read live from the connected node at tx-build time, not pinned to a specific expected chain ID in code or config anywhere in the repo (`INFORMATION NEEDED: the deployed production chain ID value` — not present in any committed config; only `docker-compose.yml` shows a local `anvil` service, Anvil's default dev chain ID is 31337 but this is not asserted/verified anywhere in code).
  - Signs locally via `acct.sign_transaction(tx)` then broadcasts via `w3.eth.send_raw_transaction(signed.raw_transaction)` (`blockchain_connector.py:52-53`).
- Tx read-back (verify path), `read_transaction_value()`, `blockchain_connector.py:56-76`:
  - `w3.eth.get_transaction(HexBytes(tx_hash))` (`blockchain_connector.py:58`), reads `input` field, validates it starts with `VALUE_PREFIX` (`blockchain_connector.py:60-61`) else 500 "Unexpected payload format".
  - Requires `blockNumber` present (else 400 "Transaction is still pending", `blockchain_connector.py:63-65`) and the corresponding block's `timestamp` resolvable (else another 400 "Transaction is still pending", `blockchain_connector.py:67-70` — same message reused for a semantically different case, a missing-timestamp block, not just an unmined tx — see Finding D.6).
  - Returns `{"value": <decoded hash string>, "timestamp": datetime (UTC)}` (`blockchain_connector.py:72-76`).
  - Caller (`documents.py:98-100`, `120-122`) compares `onchain_value["value"] != existing.content_hash` — a straight string equality check against the DB-stored hash, i.e. **on-chain data is only used as a cross-check against Postgres, not as the sole source of truth** — a DB row with no matching/mismatching on-chain tx returns `valid: false` with `record_id` present (a middle state, "registered but evidence inconsistent", distinguishable from "never registered").
  - `wait_for_receipt()` (`blockchain_connector.py:78-81`): blocks on `w3.eth.wait_for_transaction_receipt`, called synchronously inside the `async def register()` handler (`documents.py:63`) with no timeout parameter passed — an unresponsive/slow chain would hang the request indefinitely (`wait_for_transaction_receipt`'s own library default timeout, if any, is `INFORMATION NEEDED: web3.py 7.16.0's default timeout for wait_for_transaction_receipt — not overridden anywhere in this codebase`), and because this is a **sync call inside an `async def` FastAPI handler with no `run_in_executor`/threadpool offload**, it blocks the event loop for the whole wait — see Finding D.15.
- Chain type: **local/private chain only, by every config file present in this repo.** `docker/docker-compose.yml:44-56` runs `ghcr.io/foundry-rs/foundry:latest`'s `anvil` (Foundry's local dev-chain simulator) bound to `127.0.0.1:8545` (per the latest commit `7261708`, localhost-only). `docker/.env.example:21-22`: `BLOCKCHAIN_RPC_URL=http://anvil:8545`, `BLOCKCHAIN_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80` — this exact private key is **Anvil/Foundry's well-known, publicly-documented default test account #1 private key** (used by every local Anvil instance unless overridden — not a leaked real secret, but also means `.env.example`'s "example" key is trivially usable to sign real transactions if this exact value were ever pointed at a real/public chain by mistake). No public-chain RPC URL (Infura/Alchemy/public mainnet or testnet endpoint) appears anywhere in any config file in this repo — **this deployment, as configured in this repository, runs entirely against a local, ephemeral Anvil instance**, not a public blockchain.

### A.7 Infra
- `docker/Dockerfile`: base image `python:3.12.8-bookworm` (`Dockerfile:1`). Single stage (no multi-stage build). Steps: apt update/upgrade/dist-upgrade + install `git curl` (`Dockerfile:9-14`); creates non-root user `akmi` (`Dockerfile:16`); sets `PYTHONPATH=/home/akmi/pits/src`, `BASE_DIR=/home/akmi/pits` (`Dockerfile:18-19`); installs `uv` from `ghcr.io/astral-sh/uv:latest` (`Dockerfile:25` — **unpinned `:latest` tag**, reproducibility risk — see Finding D.16); creates venv, copies `src`, `conf`, `pyproject.toml`, `README.md`, `uv.lock` (`Dockerfile:31-40`, comment at line 36 literally says `#Temporary, will be removed later` for the `conf` copy); `uv sync --frozen --no-cache` then `chown` (`Dockerfile:45`); switches to non-root `USER akmi` (`Dockerfile:46`); creates `logs`, `resources/data/files-storage/{registered,verified}`, `resources/data/db` dirs (`Dockerfile:47` — note: `files-storage/registered` and `files-storage/verified` subdirectories are created but **nothing in the current source code ever writes to them** — uploaded file bytes are hashed in-memory and never persisted to disk anywhere in `documents.py`; these directories are vestigial/unused — see Finding D.17); `EXPOSE 41012`; `CMD ["python", "-m", "trustmark.main"]` (`Dockerfile:50`).
- `docker/docker-compose.yml` services (names only): `postgres` (image `postgres:16-alpine`), `keycloak` (image `quay.io/keycloak/keycloak:26.6.2`), `anvil` (image `ghcr.io/foundry-rs/foundry:latest` — also unpinned `:latest`), `trustmark-app` (built from local `docker/Dockerfile`). All four services' host port bindings are explicitly `127.0.0.1:<port>:<container-port>` as of commit `7261708` (postgres 5432, keycloak 8080, anvil 8545, app `EXPOSE_PORT`/41012) — i.e. none are bound to `0.0.0.0`/all interfaces at the docker-compose level in current HEAD.
- Env var **names** referenced by the app (values never reproduced here beyond the well-known Anvil test key already public knowledge, noted above):
  - From `conf/settings.toml` (`@format {env[...]}` substitutions): `BASE_DIR`, `BLOCKCHAIN_RPC_URL`, `BLOCKCHAIN_PRIVATE_KEY`, `KEYCLOAK_ISSUER`, `KEYCLOAK_ISSUER_URL`, `KEYCLOAK_AUDIENCE`, `MAX_UPLOAD_BYTES`.
  - From `commons.py`/`db.py`/`main.py` directly via `os.environ`/`os.getenv`: `BASE_DIR` (`commons.py:8`), `DATABASE_URL` (`db.py:7`), `EXPOSE_PORT` (`main.py:64`, via `get_env_int`), `CORS_ALLOW_ORIGINS` (`main.py:51`), `TEST_MODE` (`main.py:110`).
  - From `keycloak.py` (indirectly, not a literal `os.environ` call but a settings key with no `@format` wrapper, so **not required at import time** the way the others are): `KEYCLOAK_ROLES_CLIENT_ID` (`keycloak.py:192`, via `getattr(settings, ...)`, not present in any settings file — see A.4 above).
  - From `docker/.env.example` only (compose-level, not all consumed by app code directly — some feed Keycloak/Postgres containers, not the FastAPI app): `EXPOSE_PORT`, `BUILD_DATE`, `CORS_ALLOW_ORIGINS`, `MAX_UPLOAD_BYTES`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `KEYCLOAK_USER`, `KEYCLOAK_PASS`, `KEYCLOAK_ISSUER`, `KEYCLOAK_ISSUER_URL`, `KEYCLOAK_AUDIENCE`, `BLOCKCHAIN_RPC_URL`, `BLOCKCHAIN_PRIVATE_KEY`, `TEST_MODE`.
  - **Required-at-import-time set** (the `@format {env[...]}` keys in `conf/settings.toml` that are read eagerly at module import, not lazily on first `.get()` call, per empirical test-collection failure in section C): `KEYCLOAK_ISSUER`, `KEYCLOAK_ISSUER_URL`, `KEYCLOAK_AUDIENCE` — because `keycloak.py:176-181` evaluates `settings.get("KEYCLOAK_ISSUER_URL", settings.KEYCLOAK_ISSUER)` and constructs the module-level singleton `_verifier` at **import time**, not inside a function. `BLOCKCHAIN_RPC_URL`/`BLOCKCHAIN_PRIVATE_KEY`/`MAX_UPLOAD_BYTES` are read **lazily inside function bodies** (`documents.py:16-17,24`) via `settings.get(...)`, so they do NOT block import (confirmed: `test_documents.py` collected and ran fine with those three left unset in the module-import path, since `documents.py` doesn't evaluate them at import time — it only imports `settings` itself, which succeeds; per-test values are set via `monkeypatch.setenv` inside individual test functions, e.g. `test_documents.py:114-119`). This asymmetry (`KEYCLOAK_*` = eager/import-time; `BLOCKCHAIN_*`/`MAX_UPLOAD_BYTES` = lazy/call-time) is exactly why `tests/trustmark/api/test_documents.py` and `tests/trustmark/infra/auth/test_keycloak.py` failed to *collect* at baseline while blockchain-related dummy values weren't strictly needed for collection (only for individual test bodies that explicitly `monkeypatch.setenv` them).

---

## B. Backend Test Inventory (94 tests total, this-audit-run collection)

All 94 tests below were collected via `uv run pytest tests/trustmark -v --collect-only` with dummy env vars set (see C.2). No test in `tests/trustmark` is parametrized beyond the 4 explicit `TestFetchJwks::test_jwks_uri_invalid_shape_returns_500[...]` cases (4 params) and 3 `test_jwks_response_wrong_shape_returns_500[...]` cases (3 params) — both already counted individually below. `tests/api/test_register_verify_e2e.py` (27 tests per the prior results doc) was **NOT** re-executed this audit (see instructions constraint; also see section F) and is listed separately at the bottom for completeness/traceability, marked NOT RUN.

Layer legend: Unit = pure function / fully mocked, no I/O. Integration = none present in `tests/trustmark` (would need a real DB/HTTP/chain). API E2E = `tests/api/*` only (live server). Performance = `tests/locust/*` (not pytest, not executed as part of `tests/trustmark`).

### B.1 `tests/trustmark/infra/test_hash_engine.py` (7 tests)

| Test ID | Category | Layer | Purpose | Mocked | Real | Expected | Actual (this run) | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `test_hash_engine.py::TestHashEngine::test_generate_hash_canonicalization` | Unit | Unit | CRLF vs LF produce identical hash | none | `generate_hash` | equal hashes | equal hashes | PASS | |
| `...::test_hash_with_file_content` | Unit | Unit | Hash of "Hello World" matches known SHA-256 | tmp_path file | `generate_hash` | `a591a6d4...` | matched | PASS | |
| `...::test_generate_hash_from_bytes` | Unit | Unit | Raw byte hash of `b"test data"` | none | `generate_hash_from_bytes` | `916f0027...` | matched | PASS | |
| `...::test_generate_hash_empty_string` | Unit | Unit | Empty string hashes to SHA-256("") | none | `generate_hash` | `e3b0c442...` | matched | PASS | |
| `...::test_generate_hash_from_bytes_empty` | Unit | Unit | Empty bytes hashes to SHA-256(b"") | none | `generate_hash_from_bytes` | `e3b0c442...` | matched | PASS | |
| `...::test_generate_hash_invalid_input_raises` | Unit | Unit | `generate_hash(None)` raises `AttributeError` | none | `generate_hash` | `AttributeError` | raised | PASS | |
| `...::test_generate_hash_from_bytes_invalid_input_raises` | Unit | Unit | `generate_hash_from_bytes("not bytes")` raises `TypeError` | none | `generate_hash_from_bytes` | `TypeError` | raised | PASS | |

### B.2 `tests/trustmark/infra/test_blockchain_connector.py` (14 tests) — **file has uncommitted local modification, see D.7**

| Test ID | Category | Layer | Purpose | Mocked | Real | Expected | Actual (this run) | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `TestBlockchainConnector::test_connect_success` | Unit | Unit | `_connect()` sets `w3` when node reports connected | `Web3` | connector logic | `connector.w3 == mock` | matched | PASS | |
| `...::test_connect_failure` | Unit | Unit | `_connect()` raises 500 when node reports disconnected | `Web3` | connector logic | `HTTPException(500)` | raised | PASS | |
| `...::test_get_account_invalid_key` | Unit | Unit | `_get_account()` wraps bad key in `HTTPException(500)` | `Account` | connector logic | `HTTPException(500)` | raised | PASS | |
| `...::test_create_transaction_success` | Unit | Unit | Happy-path tx construction/sign/send returns tx hash | `Web3`, `Account` | connector logic | `tx_hash == "1234"` | matched | PASS | |
| `...::test_create_transaction_connection_failure` | Unit | Unit | `create_transaction` propagates connection failure as 500 | `Web3` | connector logic | `HTTPException(500)` | raised | PASS | |
| `...::test_read_transaction_value_success` | Unit | Unit | Mined tx w/ resolvable block timestamp returns `{value, timestamp}` dict | `Web3` | connector logic | `result["value"]=="test value"`, `timestamp.year==2025` | matched | PASS | Docstring documents this fixture was previously stale/broken — see D.7 |
| `...::test_read_transaction_value_pending_no_block_number_returns_400` | Unit | Unit | `blockNumber=None` → 400 "pending" | `Web3` | connector logic | `HTTPException(400)`, "pending" in detail | raised, matched | PASS | Added 2026-09-09 per docstring |
| `...::test_read_transaction_value_block_missing_timestamp_returns_400` | Unit | Unit | Mined block but `timestamp=None` → 400 "pending" (reused message) | `Web3` | connector logic | `HTTPException(400)`, "pending" in detail | raised, matched | PASS | Added 2026-09-09 per docstring; see D.6 for message-reuse note |
| `...::test_read_transaction_value_invalid_format` | Unit | Unit | `input` not prefixed with `VALUE_PREFIX` → 500 | `Web3` | connector logic | `HTTPException(500)` | raised | PASS | |
| `...::test_read_transaction_value_no_input` | Unit | Unit | Missing `input` key entirely → 500 | `Web3` | connector logic | `HTTPException(500)` | raised | PASS | |
| `...::test_get_web3_connection_failure` | Unit | Unit | `_get_web3()` raises 500 when disconnected | `Web3` | connector logic | `HTTPException(500)` | raised | PASS | |
| `...::test_wait_for_receipt_success` | Unit | Unit | Happy-path receipt wait returns block number | `Web3` | connector logic | `receipt == 99955` | matched | PASS | |
| `...::test_wait_for_receipt_connection_failure` | Unit | Unit | `wait_for_receipt` on disconnected node → 500 | `Web3` | connector logic | `HTTPException(500)` | raised | PASS | Added 2026-09-09 per docstring |

### B.3 `tests/trustmark/infra/test_commons.py` (4 tests)

| Test ID | Category | Layer | Purpose | Mocked | Real | Expected | Actual | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `TestGetEnvInt::test_valid_int_string_is_parsed` | Unit | Unit | `get_env_int` parses a valid int env var | monkeypatch env | `get_env_int` | `8080` | matched | PASS | |
| `...::test_missing_var_returns_default` | Unit | Unit | Missing var → default | monkeypatch env | `get_env_int` | `1234` | matched | PASS | |
| `...::test_non_numeric_value_falls_back_to_default_with_warning` | Unit | Unit | Non-numeric string → default + warning log | monkeypatch env | `get_env_int` | `1234` | matched | PASS | |
| `...::test_empty_string_falls_back_to_default` | Unit | Unit | Empty string → default | monkeypatch env | `get_env_int` | `1234` | matched | PASS | |

### B.4 `tests/trustmark/test_main_test_mode.py` (4 tests)

| Test ID | Category | Layer | Purpose | Mocked | Real | Expected | Actual | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `TestTestModeAuthBypass::test_test_mode_true_installs_a_hardcoded_auth_bypass` | Unit/Security | Unit (runs `main.py` as `__main__` via `runpy`) | Confirms `TEST_MODE=true` installs `dependency_overrides[get_current_principal]` | `uvicorn.run` | actual `trustmark.main` module exec | override installed | installed | PASS | Confirms TEST_MODE bypass still wired exactly as documented |
| `...::test_override_principal_is_hardcoded_publisher_with_no_real_identity` | Unit/Security | Unit | Confirms override principal is `sub="test-user"`, has `publisher` role, no token needed | `uvicorn.run` | real override coroutine | `sub=="test-user"`, `"publisher" in roles` | matched | PASS | |
| `...::test_test_mode_false_leaves_real_auth_in_place` | Unit/Security | Unit | `TEST_MODE=false` → no override installed | `uvicorn.run` | real module exec | no override | no override | PASS | |
| `...::test_test_mode_unset_defaults_to_false_leaving_real_auth_in_place` | Unit/Security | Unit | `TEST_MODE` unset → same as false | `uvicorn.run` | real module exec | no override | no override | PASS | |

### B.5 `tests/trustmark/api/test_documents.py` (27 tests)

| Test ID | Category | Layer | Purpose | Mocked | Real | Expected | Actual | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `TestConfigAndUploadHelpers::test_max_upload_bytes_reads_configured_value` | Unit | Unit | `_max_upload_bytes()` reads env-configured value | monkeypatch env | fn logic | `1234` | matched | PASS | |
| `...::test_max_upload_bytes_default_is_unreachable_if_env_var_unset` | Unit/Finding | Unit | Unsetting `MAX_UPLOAD_BYTES` entirely raises, doesn't silently default to 20MB | monkeypatch env | fn logic, Dynaconf | exception w/ "MAX_UPLOAD_BYTES" in message | raised as expected | PASS | Confirms candidate-6th-bug docstring claim (D.8) |
| `...::test_get_blockchain_service_missing_rpc_url_returns_500` | Unit | Unit | Empty `BLOCKCHAIN_RPC_URL` → 500 | monkeypatch env | fn logic | `HTTPException(500)` | raised | PASS | |
| `...::test_get_blockchain_service_missing_private_key_returns_500` | Unit | Unit | Empty `BLOCKCHAIN_PRIVATE_KEY` → 500 | monkeypatch env | fn logic | `HTTPException(500)` | raised | PASS | |
| `...::test_get_blockchain_service_constructs_connector_when_configured` | Unit | Unit | Both configured → `BlockchainConnector` constructed w/ right kwargs | `BlockchainConnector` ctor patched | fn logic | ctor called once w/ rpc_url/private_key | matched | PASS | |
| `...::test_read_upload_non_empty_within_limit_returns_bytes` | Unit | Unit | Normal upload returns bytes | `_max_upload_bytes` patched | `_read_upload` | bytes returned unchanged | matched | PASS | |
| `...::test_read_upload_empty_file_returns_400` | Unit | Unit | Empty upload → 400 "empty" | patched limit | `_read_upload` | `HTTPException(400)` | raised | PASS | |
| `...::test_read_upload_exactly_at_limit_is_allowed` | Unit | Unit | Boundary `>` not `>=`: file == limit passes | patched limit | `_read_upload` | 100 bytes accepted | matched | PASS | |
| `...::test_read_upload_one_byte_over_limit_returns_413` | Unit | Unit | 1 byte over limit → 413 | patched limit | `_read_upload` | `HTTPException(413)` | raised | PASS | |
| `TestRegister::test_new_unique_content_creates_record_and_one_blockchain_tx` | Unit | Unit | New content → stored, one tx, DB add+commit | DB session, blockchain service | `register()` handler | `stored=True, already_existed=False`, correct hash | matched | PASS | |
| `...::test_duplicate_content_returns_existing_record_no_new_tx` | Unit | Unit | Existing hash → no new tx, no DB add | DB session, blockchain service | `register()` handler | `already_existed=True`, no chain/db calls | matched | PASS | |
| `...::test_principal_sub_empty_writes_empty_issuer_id` | Unit/Finding | Unit | `Principal.sub=""` written straight into `issuer_id` with no validation | DB session, blockchain service | `register()` handler | `created_records[0].issuer_id == ""` | matched | PASS | Reproduces Finding-2-shaped issue at write site (D.2) |
| `...::test_blockchain_failure_mid_transaction_propagates` | Unit/Finding | Unit | No try/except around blockchain call — failure propagates raw | DB session, blockchain service (raises) | `register()` handler | `HTTPException(500)` propagates, `db.add` never called | matched | PASS | |
| `...::test_db_commit_failure_propagates_unhandled` | Unit/Finding | Unit | No try/except around `db.commit()` — `RuntimeError` (stand-in for `IntegrityError`) propagates raw | DB session (commit raises), blockchain service | `register()` handler | `RuntimeError` propagates | matched | PASS | |
| `TestListRecords::test_records_from_two_issuers_visible_to_one_principal` | Unit/Finding | Unit | **Reproduces the `/records` IDOR** deterministically: one principal sees records from 2 different issuers | DB session (mocked `.all()`) | `list_records()` handler | both issuer-A and issuer-B visible to issuer-A's principal | both visible | PASS | Confirms Finding 1 (IDOR) still present in current code — see D.2 verdict below |
| `...::test_empty_table_returns_empty_list` | Unit | Unit | Empty table → empty list | DB session | `list_records()` | `{"records": []}` | matched | PASS | |
| `...::test_ordered_by_created_at_descending` | Unit | Unit | Query calls `order_by` once (can't verify actual SQL order w/ mock) | DB session | `list_records()` | `order_by` called once | matched | PASS | Self-documented limitation: doesn't prove real DB sort correctness |
| `TestVerify::test_registered_and_onchain_match_returns_valid_true` | Unit | Unit | Matching DB+chain → `valid=true` w/ timestamp | DB session, blockchain service | `verify_full()` | `valid=True`, `record_id` present, `blockchain_timestamp` present | matched | PASS | |
| `...::test_never_registered_returns_valid_false_no_record_id` | Unit | Unit | No DB record → `valid=false`, no `record_id` key | DB session (empty) | `verify_full()` | `valid=False`, no `record_id` | matched | PASS | |
| `...::test_onchain_mismatch_returns_valid_false_with_record_id` | Unit | Unit | DB record exists but on-chain value differs → `valid=false` WITH `record_id` | DB session, blockchain service (mismatched value) | `verify_full()` | `valid=False`, `record_id` present | matched | PASS | Distinguishes "never registered" vs "registered but inconsistent" |
| `...::test_blockchain_node_down_propagates_unhandled` | Unit/Finding | Unit | No try/except around `read_transaction_value` — node-down failure propagates raw | DB session, blockchain service (raises 500) | `verify_full()` | `HTTPException(500)` propagates | matched | PASS | |
| `...::test_verify_by_hash_registered_matches_verify_full_behaviour` | Unit | Unit | GET-by-hash mirrors POST /verify behavior when registered | DB session, blockchain service | `verify_by_hash()` | `valid=True` | matched | PASS | |
| `...::test_verify_by_hash_not_registered_returns_valid_false` | Unit | Unit | GET-by-hash, unregistered hash → `valid=false` | DB session (empty) | `verify_by_hash()` | `valid=False` | matched | PASS | |
| `...::test_verify_by_hash_wrong_length_returns_400` | Unit | Unit | Too-short hash → 400 | DB session | `verify_by_hash()` | `HTTPException(400)` | raised | PASS | |
| `...::test_verify_by_hash_non_hex_characters_returns_400` | Unit | Unit | Non-hex chars → 400 | DB session | `verify_by_hash()` | `HTTPException(400)` | raised | PASS | |
| `...::test_verify_by_hash_injection_payload_returns_400_before_db_hit` | Unit/Security | Unit | SQLi-shaped payload rejected by format check BEFORE reaching `db.query` | DB session (asserted not called) | `verify_by_hash()` | `HTTPException(400)`, `db.query` never called | matched | PASS | Confirms format validation runs before ORM layer |
| `...::test_verify_by_hash_mixed_case_and_whitespace_normalised` | Unit | Unit | Uppercase + whitespace hash normalized to lowercase/stripped before lookup | DB session | `verify_by_hash()` | `filter_by(content_hash=<lowercased>)` | matched | PASS | |

### B.6 `tests/trustmark/infra/auth/test_keycloak.py` (39 tests)

| Test ID | Category | Layer | Purpose | Mocked | Real | Expected | Actual | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `TestFetchJwks::test_oidc_discovery_unreachable_returns_503` | Unit | Unit | OIDC discovery network failure → 503 | `requests.get` | `_fetch_jwks` | `HTTPException(503)` | raised | PASS | |
| `...::test_oidc_response_invalid_json_returns_500` | Unit | Unit | Bad JSON from OIDC discovery → 500 | `requests.get` | `_fetch_jwks` | `HTTPException(500)` | raised | PASS | |
| `...::test_oidc_config_missing_jwks_uri_key_is_unhandled` | Unit/Finding | Unit | Missing `jwks_uri` key → unhandled `KeyError`, not a clean 500 | `requests.get` | `_fetch_jwks` | `KeyError` (documents current buggy behavior) | `KeyError` raised | PASS | "Candidate 5th bug" per test docstring — confirms D.4 |
| `...::test_jwks_uri_invalid_shape_returns_500[None]` | Unit | Unit | `jwks_uri=None` → 500 | `requests.get` | `_fetch_jwks` | `HTTPException(500)` | raised | PASS | |
| `...::test_jwks_uri_invalid_shape_returns_500[]` (empty string) | Unit | Unit | `jwks_uri=""` → 500 | `requests.get` | `_fetch_jwks` | `HTTPException(500)` | raised | PASS | |
| `...::test_jwks_uri_invalid_shape_returns_500[123]` | Unit | Unit | `jwks_uri=123` (int) → 500 | `requests.get` | `_fetch_jwks` | `HTTPException(500)` | raised | PASS | |
| `...::test_jwks_uri_invalid_shape_returns_500[bad_jwks_uri3]` (`[]` list) | Unit | Unit | `jwks_uri=[]` → 500 | `requests.get` | `_fetch_jwks` | `HTTPException(500)` | raised | PASS | |
| `...::test_jwks_endpoint_unreachable_returns_503` | Unit | Unit | JWKS endpoint network failure (after OIDC succeeds) → 503 | `requests.get` (2-call side_effect) | `_fetch_jwks` | `HTTPException(503)` | raised | PASS | |
| `...::test_jwks_response_invalid_json_returns_500` | Unit | Unit | Bad JSON from JWKS endpoint → 500 | `requests.get` | `_fetch_jwks` | `HTTPException(500)` | raised | PASS | |
| `...::test_jwks_response_wrong_shape_returns_500[bad_jwks_body0]` (`{"keys":"not-a-list"}`) | Unit | Unit | Malformed JWKS body shape → 500 | `requests.get` | `_fetch_jwks` | `HTTPException(500)` | raised | PASS | |
| `...::test_jwks_response_wrong_shape_returns_500[bad_jwks_body1]` (`{"no_keys_field":True}`) | Unit | Unit | Malformed JWKS body shape → 500 | `requests.get` | `_fetch_jwks` | `HTTPException(500)` | raised | PASS | |
| `...::test_jwks_response_wrong_shape_returns_500[bad_jwks_body2]` (list not dict) | Unit | Unit | Malformed JWKS body shape → 500 | `requests.get` | `_fetch_jwks` | `HTTPException(500)` | raised | PASS | |
| `...::test_well_formed_jwks_returned_unchanged` | Unit | Unit | Valid JWKS returned as-is | `requests.get` | `_fetch_jwks` | equals `VALID_JWKS` | matched | PASS | |
| `TestJwksCaching::test_second_call_within_ttl_does_not_refetch` | Unit | Unit | Cache hit within TTL doesn't refetch | `_fetch_jwks` | `jwks()` caching logic | `_fetch_jwks` called once | matched | PASS | |
| `...::test_call_after_ttl_expired_refetches` | Unit | Unit | Cache miss after TTL forces refetch | `_fetch_jwks` | `jwks()` caching logic | `_fetch_jwks` called twice | matched | PASS | |
| `TestVerify::test_well_formed_token_returns_claims` | Unit | Unit | Happy path returns decoded claims | `jwks()`, `jwt.decode` | `verify()` | claims returned unchanged | matched | PASS | |
| `...::test_token_missing_kid_header_returns_401` | Unit | Unit | No `kid` header → 401 "kid" | none (real JWT via `jose`) | `verify()` | `HTTPException(401)` | raised | PASS | |
| `...::test_unknown_kid_found_after_one_refresh_succeeds` | Unit | Unit | Key rotation: unknown kid triggers one refresh, then succeeds | `jwks()`, `jwt.decode` | `verify()` | claims returned | matched | PASS | |
| `...::test_unknown_kid_still_missing_after_refresh_returns_401` | Unit | Unit | Kid still missing after refresh → 401 "unknown signing key" | `jwks()` | `verify()` | `HTTPException(401)` | raised | PASS | |
| `...::test_expired_signature_returns_401` | Unit | Unit | `ExpiredSignatureError` → 401 "expired" | `jwks()`, `jwt.decode` (raises) | `verify()` | `HTTPException(401)` | raised | PASS | |
| `...::test_bad_signature_returns_401` | Unit | Unit | `JWTError` (bad sig) → 401 | `jwks()`, `jwt.decode` (raises) | `verify()` | `HTTPException(401)` | raised | PASS | |
| `...::test_issuer_or_audience_mismatch_returns_401` | Unit | Unit | iss/aud mismatch (surfaces as `JWTError` in this jose version) → 401 | `jwks()`, `jwt.decode` (raises) | `verify()` | `HTTPException(401)` | raised | PASS | Confirms commented-out `JWTClaimsError` branch (keycloak.py:148-151) is irrelevant — generic `JWTError` path already returns 401 |
| `...::test_unexpected_exception_mid_decode_still_returns_401_not_500` | Unit | Unit | Arbitrary `RuntimeError` mid-decode still caught by broad `except Exception` → 401 not 500 | `jwks()`, `jwt.decode` (raises `RuntimeError`) | `verify()` | `HTTPException(401)` | raised | PASS | |
| `...::test_verify_malformed_token_is_unhandled` | Unit/Finding | Unit | Non-JWT-shaped string → `jwt.get_unverified_header` raises `JWTError` **uncaught**, `verify()` doesn't return an `HTTPException` at all | none (real non-JWT string) | `verify()` | `JWTError` propagates uncaught (documents CURRENT buggy behavior — test's own docstring says it should be updated to expect 401 once fixed) | `JWTError` raised, uncaught | PASS | Confirms Finding 3 / AUTH-2 root cause still present in code — see D.3 |
| `TestExtractRoles::test_realm_roles_included` | Unit | Unit | Realm roles extracted into set | none | `_extract_roles` | `{"publisher","offline_access"}` | matched | PASS | |
| `...::test_missing_realm_access_no_crash` | Unit | Unit | No `realm_access` key → empty set, no crash | none | `_extract_roles` | `set()` | matched | PASS | |
| `...::test_realm_access_present_but_null` | Unit | Unit | `realm_access: None` → empty set, no crash | none | `_extract_roles` | `set()` | matched | PASS | |
| `...::test_client_roles_included_when_client_id_given` | Unit | Unit | Client roles extracted when `client_id` given | none | `_extract_roles` | `{"viewer"}` | matched | PASS | |
| `...::test_resource_access_missing_the_given_client_id_no_crash` | Unit | Unit | `client_id` not present in `resource_access` → empty set, no crash | none | `_extract_roles` | `set()` | matched | PASS | |
| `...::test_client_id_none_skips_client_roles_entirely` | Unit | Unit | `client_id=None` → client roles branch fully skipped even if data present | none | `_extract_roles` | `set()` | matched | PASS | Confirms A.4's "client roles branch is dead unless `KEYCLOAK_ROLES_CLIENT_ID` set" finding |
| `...::test_realm_and_client_roles_unioned_without_duplicates` | Unit | Unit | Realm + client roles unioned, deduped | none | `_extract_roles` | `{"publisher","viewer"}` | matched | PASS | |
| `TestGetCurrentPrincipal::test_no_credentials_returns_401` | Unit | Unit | No creds → 401 "missing bearer token" | none | `get_current_principal` | `HTTPException(401)` | raised | PASS | |
| `...::test_wrong_scheme_returns_401` | Unit | Unit | Non-Bearer scheme (`Basic`) → 401 | none | `get_current_principal` | `HTTPException(401)` | raised | PASS | |
| `...::test_valid_token_populates_principal` | Unit | Unit | Full claims → `Principal` populated correctly | `_verifier.verify` | `get_current_principal` | sub/username/email/roles all correct | matched | PASS | |
| `...::test_missing_sub_claim_reproduces_finding_2` | Unit/Finding | Unit | No `sub` claim in token → `Principal.sub == ""` silently | `_verifier.verify` (returns claims w/o `sub`) | `get_current_principal` | `principal.sub == ""` | matched | PASS | Reproduces Finding 2 (empty `sub`/`issuer_id`) at the exact claims-extraction site — see D.2 |
| `...::test_missing_email_and_username_fall_back_to_none` | Unit | Unit | No email/username claims → both `None` | `_verifier.verify` | `get_current_principal` | `email is None`, `username is None` | matched | PASS | |
| `TestRequireRoles::test_principal_has_required_role_passes_through` | Unit | Unit | Principal with required role passes | none | `require_roles` | same principal returned | matched | PASS | |
| `...::test_principal_missing_required_role_returns_403` | Unit | Unit | Principal without required role → 403 | none | `require_roles` | `HTTPException(403)` | raised | PASS | |
| `...::test_principal_has_extra_roles_beyond_required_still_passes` | Unit | Unit | Extra roles beyond required don't block | none | `require_roles` | same principal returned | matched | PASS | |

### B.7 `tests/api/test_register_verify_e2e.py` (27 tests, per prior results doc — **NOT RE-EXECUTED THIS AUDIT**)

Per the task's explicit safety instruction (no internet/production-credential access assumed for this audit run), this file was read in full but not executed. All 27 rows below are carried forward from `_docs/qa/results/api-test-results-2026-09-09.md` for traceability, marked `NOT RUN` this audit — treat these specific PASS/FAIL values as **unverified this session**, last known from 2026-09-09:

| Test ID (scenario) | File | Category | Layer | Purpose | Result (2026-09-09, not reverified) | Status this audit |
|---|---|---|---|---|---|---|
| REG-1 register new document | test_register_verify_e2e.py | API | API E2E | New doc registers, `issuer_id` should equal token `sub` | FAIL (issuer_id empty) | NOT RUN |
| REG-2 empty file | " | API | API E2E | 0-byte file → 400 | PASS | NOT RUN |
| REG-3 file exceeds limit | " | API | API E2E | Oversized file → 413 | PASS* (via nginx, not app) | NOT RUN |
| REG-4 file exactly at limit | " | API | API E2E | Exactly-20MB file → 200 | XFAIL (unreachable behind nginx ~1MiB limit) | NOT RUN |
| REG-4b file at effective nginx limit | " | API | API E2E | ~1MiB file → 200 | PASS | NOT RUN |
| REG-5 missing file field | " | API | API E2E | No `file` field → 422 | PASS | NOT RUN |
| REG-6 non-PDF content accepted | " | API | API E2E | No content-type/magic-byte validation, non-PDF accepted | PASS (documents known gap) | NOT RUN |
| DUP-1 duplicate same publisher | " | API | API E2E | Re-register same content → `already_existed=true`, same ids | PASS | NOT RUN |
| DUP-2 duplicate different publisher | " | API | API E2E | Needs 2nd account | XFAIL (no 2nd account) | NOT RUN |
| DUP-3 same filename different content | " | API | API E2E | Distinct records for distinct content | PASS | NOT RUN |
| DUP-4 concurrent register race | " | API | API E2E | 2 parallel identical registers — race check | PASS (no exploitable race observed) | NOT RUN |
| AUTH-1 register without token | " | API | API E2E | No token → 401 | PASS | NOT RUN |
| AUTH-2 malformed token | " | API | API E2E | Non-JWT string → should be 401 | **FAIL (500 observed)** | NOT RUN |
| AUTH-3 expired token | " | API | API E2E | Real-TTL-expired token → 401 | PASS | NOT RUN |
| AUTH-4 valid token missing publisher role | " | API | API E2E | Needs non-publisher account | XFAIL (no such account) | NOT RUN |
| AUTH-5 issuer/audience mismatch (proxied via unknown kid) | " | API | API E2E | → 401 | PASS* (proxy test, not true iss/aud mismatch) | NOT RUN |
| AUTH-6 /records without token | " | API | API E2E | → 401 | PASS | NOT RUN |
| AUTH-7 /records IDOR check | " | API | API E2E | Publisher A sees other issuers' records | **FAIL — CONFIRMED IDOR** | NOT RUN |
| AUTH-8 /verify without token | " | API | API E2E | Public by design → 200 | PASS | NOT RUN |
| VER-1 verify registered document | " | API | API E2E | `valid=true` | PASS | NOT RUN |
| VER-2 verify unregistered document | " | API | API E2E | `valid=false`, no `record_id` | PASS | NOT RUN |
| VER-3 verify tampered document | " | API | API E2E | 1-byte change → `valid=false` | PASS | NOT RUN |
| VER-4 verify-by-hash not registered | " | API | API E2E | `valid=false` | PASS | NOT RUN |
| VER-5 verify-by-hash bad format | " | API | API E2E | → 400 | PASS | NOT RUN |
| VER-6 verify-by-hash injection payload | " | API | API E2E | → 400/404, never reaches DB unsafely | PASS | NOT RUN |
| VER-7 on-chain mismatch | " | API | API E2E | Needs server/Anvil access | SKIPPED (out of scope) | NOT RUN |
| VER-8 blockchain node down | " | API | API E2E | Needs server access | SKIPPED (out of scope) | NOT RUN |

### B.8 `tests/locust/locustfiles/{health,register,verify}.py` — Performance, NOT pytest tests, NOT executed this audit

Not run this audit (no live server target was stood up — `locust.conf:2` points at `http://127.0.0.1:41012`, no such server was running during this audit session). Static analysis only, see A/D sections.

---

## C. Execution Log

### C.1 Attempt 1 — baseline, no env vars set
Command: `cd /Users/laksaersa/GitHub/_personal/_PITS/backend-for-uat && uv run pytest tests/trustmark -v 2>&1`

Result: **2 collection errors, 0 tests run** (interrupted before any test executed).

Verbatim key failure (both `test_documents.py` and `test_keycloak.py` fail identically at import time):
```
tests/trustmark/api/test_documents.py:29: in <module>
    from trustmark.api.v1 import documents
src/trustmark/api/v1/documents.py:4: in <module>
    from trustmark.infra.auth.keycloak import Principal, require_roles
src/trustmark/infra/auth/keycloak.py:176: in <module>
    _issuer_url = str(settings.get("KEYCLOAK_ISSUER_URL", settings.KEYCLOAK_ISSUER))
...
E   dynaconf.utils.parse_conf.DynaconfFormatError: Dynaconf can't interpolate variable because 'KEYCLOAK_ISSUER'
```
```
tests/trustmark/infra/auth/test_keycloak.py:31: in <module>
    from trustmark.infra.auth.keycloak import (
src/trustmark/infra/auth/keycloak.py:176: in <module>
    _issuer_url = str(settings.get("KEYCLOAK_ISSUER_URL", settings.KEYCLOAK_ISSUER))
...
E   dynaconf.utils.parse_conf.DynaconfFormatError: Dynaconf can't interpolate variable because 'KEYCLOAK_ISSUER'
```
Final line: `Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!` / `1 warning, 2 errors in 1.25s`

This **exactly confirms** the task's stated last-known issue: `KEYCLOAK_ISSUER` (not even `KEYCLOAK_ISSUER_URL` — note the actual code at `keycloak.py:176` evaluates `settings.KEYCLOAK_ISSUER` as the fallback default *argument* to `.get()`, and Python evaluates that argument eagerly regardless of whether `KEYCLOAK_ISSUER_URL` itself is set, so `KEYCLOAK_ISSUER` fails to interpolate first) blocks collection of both files that import `trustmark.infra.auth.keycloak` (directly or transitively via `trustmark.api.v1.documents`).

### C.2 Attempt 2 — dummy env vars set, full run
Env vars exported before invocation: `KEYCLOAK_ISSUER=http://dummy.invalid/realms/test`, `KEYCLOAK_ISSUER_URL=http://dummy.invalid/realms/test`, `KEYCLOAK_AUDIENCE=account`, `BLOCKCHAIN_RPC_URL=http://dummy.invalid:8545`, `BLOCKCHAIN_PRIVATE_KEY=0x`, `MAX_UPLOAD_BYTES=20971520`, `DATABASE_URL=sqlite:////tmp/dummy_audit.db`.

Command: `uv run pytest tests/trustmark -v 2>&1`

Result: **94 passed, 1 warning, 0 failed, 0 errors, in 0.88s**

Full pass list is reproduced test-by-test in section B (every row above shows Status=PASS). Verbatim summary line:
```
======================== 94 passed, 1 warning in 0.88s =========================
```
The 1 warning is unrelated to app code — a `DeprecationWarning` from the `websockets` package (a `locust` transitive dependency), not from any file under audit:
```
.venv/lib/python3.12/site-packages/websockets/legacy/__init__.py:6: DeprecationWarning: websockets.legacy is deprecated
```

**No test failed for reasons other than the env-var import gate.** Every one of the 94 tests passed cleanly once the import-time env vars were supplied — there were zero additional logic failures uncovered by fixing only the collection blocker.

### C.3 Attempt 3 — `--collect-only`, same dummy env vars
Command: `uv run pytest tests/trustmark -v --collect-only 2>&1`

Result: `========================= 94 tests collected in 0.68s ==========================` — confirms the 94-test count from C.2 is the complete, correct static count (no hidden skipped/deselected tests, no parametrization beyond the 7 param-ids already itemized in section B.6).

### C.4 `uv sync` (step 1)
```
Resolved 101 packages in 24ms
Checked 99 packages in 15ms
```
No errors; environment was already synced.

---

## D. Findings Observed From Source (this audit)

**D.1 — `src/trustmark/models/documents.py` (`Provenance`/`Document` Pydantic models) is dead code.** NEW, not previously documented in either prior doc. Not imported by any router, any test, or `main.py`. `INFORMATION NEEDED: whether this was intended for a future endpoint (e.g. structured metadata submission) that was never wired up.`

**D.2 — `GET /api/v1/records` IDOR (no `issuer_id` filter): CONFIRMED at current HEAD, still present.** File:line: `src/trustmark/api/v1/documents.py:84`: `db.query(RegistryRecord).order_by(RegistryRecord.created_at.desc()).all()` — no `.filter_by(issuer_id=principal.sub)` or equivalent anywhere in `list_records()` (`documents.py:79-85`). This is the exact same code shape as both prior docs describe (`api-test-scenarios.md` AUTH-7, `api-test-results-2026-09-09.md` Temuan #4). **CONFIRMS prior finding, no code change since.** Additionally reproduced deterministically this audit via the unit test `tests/trustmark/api/test_documents.py::TestListRecords::test_records_from_two_issuers_visible_to_one_principal`, which PASSED (i.e., the bug-reproducing assertion held) in this run (see B.5).

Related — `Principal.sub` extraction: `claims.get("sub", "")` at `src/trustmark/infra/auth/keycloak.py:196` — **CONFIRMED, unchanged.** This is the exact line the prior results doc's Temuan #1 cites (verbatim same file:line number, 196). No validation exists anywhere between this line and the DB write (`documents.py:68`: `issuer_id=principal.sub`) to reject an empty `sub`. Unit test `test_missing_sub_claim_reproduces_finding_2` and `test_principal_sub_empty_writes_empty_issuer_id` both PASS this run, confirming the code path is unchanged and still produces `issuer_id=""` end-to-end when `sub` is absent from the token's claims. Whether the **live production Keycloak token** for `uat-tester` still lacks a `sub` claim is **not verifiable from source code alone** — that is a live Keycloak realm/client-scope configuration fact, external to this repo. `INFORMATION NEEDED: current production Keycloak client-scope configuration for client nextjs-web — whether a `sub` mapper has been added since 2026-09-09.` (Not re-verified this audit per the task's execution-safety constraint on live E2E tests.)

**D.3 — Malformed bearer token → unhandled exception: CONFIRMED, still present.** `src/trustmark/infra/auth/keycloak.py:114`: `headers = jwt.get_unverified_header(token)` sits **before** the nearest enclosing `try:` at line 130 — a non-JWT-shaped string raises `JWTError` (or similar) with **no** try/except in scope inside `verify()`, propagating out of `get_current_principal()` uncaught. In a running FastAPI app with no global exception handler for unhandled exceptions, this becomes a 500, not the 401 every other malformed-token case in the same function correctly returns (`keycloak.py:145-158`). Same file:line and same mechanism as prior doc's Temuan #2 / AUTH-2 finding (`api-test-results-2026-09-09.md` cites `keycloak.py:113-117`, consistent with this audit's read at line 114). Confirmed via unit test `test_verify_malformed_token_is_unhandled` (`tests/trustmark/infra/auth/test_keycloak.py:276-293`), which PASSES this run precisely because it asserts the *current, still-buggy* behavior (`pytest.raises(JWTError)`, not `HTTPException`) — the test's own docstring states it should be changed to expect a clean 401 once/if this is fixed. **CONFIRMS prior finding, no code change since.**

**D.4 — `oidc["jwks_uri"]` plain dict index outside try/except: NEW as a named finding in this audit's terms, though flagged as a "candidate 5th bug" already inside the test file's own docstring (`test_keycloak.py:89-97`), which itself appears to be a from an earlier audit pass not captured in the two prior QA markdown docs supplied for this audit.** File:line: `src/trustmark/infra/auth/keycloak.py:66`: `jwks_uri = oidc["jwks_uri"]` — the enclosing `try:` at line 46 only wraps the `requests.get(...)` call and `.raise_for_status()`/`.json()` above it (lines 47-51), not this subsequent indexing operation. If Keycloak's `/.well-known/openid-configuration` response is ever missing the `jwks_uri` key, this raises an unhandled `KeyError` rather than the intended 500 `HTTPException` the surrounding code pattern clearly intends (see the very next lines, 72-75, which DO wrap a similar invalid-shape case in a clean 500). Confirmed via unit test `test_oidc_config_missing_jwks_uri_key_is_unhandled` (`test_keycloak.py:89-103`), which asserts `pytest.raises(KeyError)` and **PASSES** this run — i.e. current code still raises the raw `KeyError`, not a handled 500. This finding does **not** appear in either `_docs/qa/api-test-scenarios.md` or `_docs/qa/results/api-test-results-2026-09-09.md` — both those docs only ever discuss the register/verify/records/auth endpoints, never `_fetch_jwks`'s internal error handling. **NEW finding relative to the two prior QA docs supplied**, though the test file itself labels it "candidate 5th bug" implying some other, unsupplied prior document may already know about it.

**D.5 — Live `/register`/`/verify` endpoints hash raw bytes, not canonicalized text: NEW, not previously documented.** `src/trustmark/api/v1/documents.py:8` imports only `generate_hash_from_bytes`; both call sites (`documents.py:55`, `documents.py:91`) hash raw uploaded bytes with zero canonicalization. The `canonicalize_text`/`generate_hash` functions in `hash_engine.py:4-13` (CRLF normalization, trailing-whitespace stripping) are fully unit-tested (`test_hash_engine.py`) and exist in the codebase, but are **not reachable from any live HTTP endpoint** — only from the locust load-test scripts, which read local files as text (`tests/locust/locustfiles/verify.py:10,19`: `from src.trustmark.infra.hash_engine import generate_hash` / `hash = generate_hash(file.read())`). This is a **functional inconsistency between what is unit-tested/documented as the hashing behavior and what a real API client actually experiences** — worth flagging for the paper if any claim describes "canonicalized" or "normalized" hashing as an integrity-detection feature of the deployed API surface.

**D.6 — `blockchain_connector.py:70` reuses the "pending" 400 message for a semantically distinct case.** NEW, not previously documented. `read_transaction_value()` (`blockchain_connector.py:56-76`) raises `HTTPException(400, detail="Transaction is still pending")` in **two different conditions**: (a) `blockNumber is None` (line 65 — tx genuinely not yet mined, i.e., actually pending) and (b) `blockNumber` present but the resolved block's `timestamp` is `None` (line 70 — a mined block with an unexpectedly missing timestamp, which is NOT "still pending" in any normal sense; the connector's own test docstring at `test_blockchain_connector.py:137-139` calls this "edge case on some dev/test chains"). A client cannot distinguish these two failure modes from the response alone. Confirmed by reading both branches directly (`blockchain_connector.py:63-65` vs `67-70`) and both corresponding unit tests (`test_read_transaction_value_pending_no_block_number_returns_400`, `test_read_transaction_value_block_missing_timestamp_returns_400`), both PASS this run.

**D.7 — `tests/trustmark/infra/test_blockchain_connector.py` carries an uncommitted local diff that fixes a previously-stale test fixture.** Per `git status -sb` (section header) this file is `M` (modified, not committed). The diff (captured in full via `git diff`) shows the working copy's version of `test_read_transaction_value_success` is the **corrected** version (asserting `result["value"]`/`result["timestamp"]` against the current `{value, timestamp}`-dict-returning contract of `read_transaction_value()`), plus 3 net-new test functions (`test_read_transaction_value_pending_no_block_number_returns_400`, `test_read_transaction_value_block_missing_timestamp_returns_400`, `test_wait_for_receipt_connection_failure`) that did not exist in the last-committed version. The file's own top-of-test docstring (`test_blockchain_connector.py:90-105`, reproduced verbatim here as it is directly relevant to the audit's own instructions about verifying paper claims) states: *"This is the exact fixture staleness the accompanying paper's §5.1 describes fixing ('16 out of 17 ... one outdated test fixture was then corrected ... final test run passed 17 out of 17') - but this repository still had the old, broken version prior to this fix, so that corrected state was not actually persisted here."* This is a **first-person, self-referential claim written into the test file itself** about a "paper's §5.1" and about work done in an unspecified prior session — it is being reported here as literal file content/data found in the repository, **not treated as an instruction to this audit** (per standard handling of any text encountered in analyzed files). Factually: (1) the corrected fixture and the 3 new tests exist only in the **uncommitted working tree**, not in git history; (2) if this file were reset to its last-committed (`a70d53b`) state, `test_read_transaction_value_success` would assert `value == "test value"` against a function that (per current `blockchain_connector.py:56-76` source, itself unchanged since `a70d53b` per git log) returns a `dict`, not a bare string — i.e. it would fail, reproducing a 16-of-17-pass result for this file alone (8 committed tests total → 7 pass + this 1 fail, not literally "16 of 17", since this file only has 8 committed tests, not 17; the "17" figure in the docstring evidently refers to a different, larger test count than what is committed to git in `tests/trustmark/infra/test_blockchain_connector.py` alone — `INFORMATION NEEDED: what exact 17-test set and what exact paper section the docstring's "§5.1" and "16 out of 17" figures refer to; this audit found no paper document in the repository itself to cross-check against`). This audit did **not** attempt to git-stash/reset the file to reproduce the described "16/17" state, since doing so would alter the working tree beyond the scope of a read-only forensic audit; the claim is reported as-is, unverified beyond the diff itself.

**D.8 — `MAX_UPLOAD_BYTES` default-argument is unreachable if the env var is fully unset (not just empty): CONFIRMED present, NEW as a named/tested finding (candidate "6th bug" per the test's own docstring, same caveat as D.4 about an unsupplied prior document possibly already knowing of it).** `conf/settings.toml:15`: `max_upload_bytes = "@format {env[MAX_UPLOAD_BYTES]}"` is a **required** substitution. `documents.py:24`: `int(settings.get("MAX_UPLOAD_BYTES", 20 * 1024 * 1024))` — the `20*1024*1024` default only fires if the **settings key itself** is absent, not if the env-var substitution *inside* an already-present key fails. If `MAX_UPLOAD_BYTES` is unset entirely (distinct from set-to-empty-string) in a given deployment, this raises an unhandled `DynaconfFormatError` rather than falling back to 20MB as the code's own default argument implies. Confirmed via `test_max_upload_bytes_default_is_unreachable_if_env_var_unset` (`test_documents.py:96-112`), PASSES this run (asserts the exception IS raised, i.e. confirms the bug).

**D.9 — `pyproject.toml:21` lists `sqlalchemy-orm>=1.2.10` alongside `sqlalchemy>=2.0.48`.** NEW, not previously documented. `sqlalchemy-orm` on PyPI is a legacy/compat package unrelated to SQLAlchemy 2.x's own built-in ORM (which the codebase actually uses via `sqlalchemy.orm.Session`/`DeclarativeBase`, both imported directly from the `sqlalchemy` package itself in `src/trustmark/infra/db.py:4`, not from any `sqlalchemy_orm` namespace). This dependency line appears to be a vestigial or mistaken addition; it resolves and installs without conflict (`uv sync` succeeded) but is not imported anywhere in `src/` per grep. `INFORMATION NEEDED: whether removing this dependency line changes anything observable — not tested this audit as it's out of scope (pure dependency hygiene, not a functional finding).`

**D.10 — `register`/`records` endpoints resolve `get_current_principal` twice per request.** NEW, not previously documented. `main.py:75-80` wraps the entire `documents.router_upload` (both `/register` and `/records`) with `dependencies=[Depends(get_current_principal)]` at the **router-inclusion** level. Separately, both handlers individually declare `principal: Principal = Depends(require_roles("publisher"))` (`documents.py:51`, `documents.py:81`), and `require_roles`'s inner `_dep` (`keycloak.py:207`) itself declares `Depends(get_current_principal)`. FastAPI's dependency-caching (`use_cache=True` by default) means `get_current_principal` is only actually **executed** once per request despite being declared twice (both `Depends(...)` calls reference the exact same callable object, so FastAPI's per-request dependency cache dedupes them) — this is not a functional bug, but the router-level `dependencies=[Depends(get_current_principal)]` at `main.py:79` is redundant/dead-weight given `require_roles` already enforces authentication as a superset. `INFORMATION NEEDED: confirm empirically via request-level trace whether Keycloak/JWKS network calls happen once or twice per request — not verified this audit; reasoned from FastAPI's documented dependency-caching behavior, not observed directly.`

**D.11 — Duplicate `/health` endpoints with different response shapes.** NEW, not previously documented. `main.py:70-72` defines `GET /health` (no prefix) returning a plain `dict[str, str]`. `metrics.py:12-20` defines `GET {API_PREFIX}/health` (i.e. `GET /api/v1/health`) returning a Pydantic `HealthResponse` model with an OpenAPI `summary`/`description`. Both exist simultaneously and both are mounted (`main.py:70-72` directly on `app`; `metrics.router` included at `main.py:87-91`). `tests/locust/locustfiles/health.py:17` load-tests `{settings.API_PREFIX}/health` specifically (the prefixed, Pydantic-typed one), not the bare `/health`. Functionally harmless (both return equivalent `{"status": "ok"}` JSON) but is dead/duplicate surface area.

**D.12 — `KEYCLOAK_ROLES_CLIENT_ID` is read via `getattr` with no config-file default anywhere in the repo, meaning client-role extraction is effectively always disabled in every configuration shipped in this repo.** NEW, not previously documented. See A.4 for full detail; file:line `keycloak.py:192`.

**D.13 — `src/trustmark/infra/hash_generator.py` is an empty (0-byte) file, distinct from the real, populated `hash_engine.py`.** NEW. Likely a leftover/renamed-away module. Not imported anywhere (confirmed via grep across `src/` and `tests/` — no `hash_generator` import found).

**D.14 — A new `Web3`/account/connection object is constructed per-request (`_get_blockchain_service()`, `documents.py:15-20`, called fresh on every `register`/`verify_full`/`verify_by_hash` call), with no connection pooling/reuse across requests.** NEW, not previously documented; **performance concern, not evaluated under actual load this audit** (locust suite not executed — see B.8/F).

**D.15 — `wait_for_receipt()` (`blockchain_connector.py:78-81`) is a blocking, synchronous `web3.py` call invoked directly inside an `async def register()` handler (`documents.py:63`, no `run_in_executor`/thread offload), with no explicit timeout override.** NEW, not previously documented. Under FastAPI/uvicorn's default single-event-loop model, a slow/unresponsive chain node would block the entire event loop — all other concurrent requests to the same worker process — for the duration of the wait, not just the calling request. `INFORMATION NEEDED: web3.py 7.16.0's default `wait_for_transaction_receipt` timeout value — not investigated further this audit (would require reading web3.py's own source, out of this repo's scope).`

**D.16 — `docker/Dockerfile:25` installs `uv` from `ghcr.io/astral-sh/uv:latest` (unpinned tag); `docker-compose.yml:45` runs `ghcr.io/foundry-rs/foundry:latest` (also unpinned) for the `anvil` service.** NEW, not previously documented. Both are reproducibility/supply-chain hygiene concerns (a build today and a build next month could pull different `uv`/`foundry` versions), distinct from the already-pinned `python:3.12.8-bookworm`, `postgres:16-alpine`, and `quay.io/keycloak/keycloak:26.6.2` images in the same files.

**D.17 — `Dockerfile:47` creates `resources/data/files-storage/registered` and `resources/data/files-storage/verified` directories that nothing in current source code writes to.** NEW, not previously documented. Confirmed via grep: no `open(`, `write(`, `files-storage`, or `files_storage_path` (the matching `conf/settings.toml:7` setting) usage found anywhere in `src/trustmark/api/v1/documents.py` — uploaded bytes are read into memory (`_read_upload`, `documents.py:27-33`), hashed, and never persisted to disk; only the hash and blockchain tx hash are stored (in Postgres). `files_storage_path` (`conf/settings.toml:7`) is defined in settings but **never referenced anywhere in `src/`** (confirmed via grep for `files_storage_path`/`FILES_STORAGE_PATH` — zero matches outside `conf/settings.toml` itself). Vestigial config/infra for a file-persistence feature that does not exist in the current codebase.

**D.18 — `verify.py` locust file: `is not str`/`is not bool` type-check bug — CONFIRMED, exact lines identified.** Per task's explicit ask to check this. `tests/locust/locustfiles/verify.py:39`: `elif response_body["record_id"] is not str:` and `verify.py:44`: `elif response_body["created_at"] is not str:` — both compare an actual **value** (e.g. a UUID string) against the **class object** `str` using identity (`is not`), which is essentially always `True` (a string value is never the same object as the `str` type itself) — meaning these two `elif` branches **always evaluate true and always call `response.failure(...)`**, regardless of whether `record_id`/`created_at` are actually valid strings. This makes the corresponding locust checks permanently broken/always-failing (the reverse of a silent false-negative — this is a false-positive-failure bug: correct server responses get marked as load-test failures). By contrast, `tests/locust/locustfiles/register.py:31,36,41` correctly wrap the same style of check with `type(...)`: `elif type(response_body["already_existed"]) is not bool:`, `elif type(response_body["record_id"]) is not str:`, `elif type(response_body["created_at"]) is not str:` — these are the technically-correct (if unidiomatic vs. `isinstance()`) form. **This is a real, reproducible bug in `verify.py`, confirmed by direct source inspection** (`grep -n "is not str\|is not bool\|type(" tests/locust/locustfiles/verify.py tests/locust/locustfiles/register.py`, output captured above). Separately, `verify.py:34` (`elif response_body["issuer_id"] != "":`) hardcodes an **expectation that `issuer_id` is always empty string** — this locust file's own author appears to have already baked in the "sub claim is always missing" bug (D.2/Finding 1/2) as expected/normal behavior for its load-test assertions, rather than flagging it — i.e. this performance test would not currently surface a regression if the `issuer_id`-empty bug were ever fixed (it would instead start *failing* the load test, since `issuer_id` would then be a real value, not `""`).

**D.19 — `TEST_MODE` auth bypass mechanism: CONFIRMED still present and still wired into `python -m trustmark.main`.** File:line `main.py:109-113`:
```python
if __name__ == "__main__":
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        app.dependency_overrides[get_current_principal] = override_get_current_principal
    main()
```
`docker/Dockerfile:50`: `CMD ["python", "-m", "trustmark.main"]` — `python -m <module>` executes the module with `__name__ == "__main__"`, the same mechanism as running the file directly, so this guard is **live in the actual production container entrypoint**, not inert. Current `docker/.env`, `docker/.env.example`, `docker/.env.save` (per this audit's read of `.env.example`, section A.7/config values) all pin `TEST_MODE=False` (`docker/.env.example:24`) — i.e. the bypass is **not currently active** by default configuration in this repo, but the mechanism to fully disable authentication for every request (installing a hardcoded `sub="test-user"`, `roles={"publisher"}` principal with zero token verification, `main.py:99-106`) is a single environment-variable flip away, with **no separate build target, no code path stripped for production images, no additional gate**. Confirmed via 4 passing unit tests in `tests/trustmark/test_main_test_mode.py` (see B.4), which exercise the real module code via `runpy.run_module("trustmark.main", run_name="__main__")` (not a simulation — actually executes `main.py`'s top-level `if __name__ == "__main__":` block, with only `uvicorn.run` patched out to avoid starting a real server). **This exactly matches and CONFIRMS the finding already described in the test file's own header docstring** (`test_main_test_mode.py:1-28`), which itself describes this as found "during the 2026-09-09 source audit" — a finding not present in either of the two prior QA markdown docs supplied for this task (`api-test-scenarios.md`, `api-test-results-2026-09-09.md` — neither mentions `TEST_MODE` at all). **NEW relative to the two prior docs explicitly supplied for this audit's comparison, though the test file's own docstring implies a separate, unsupplied "2026-09-09 source audit" already knew of it.**

**D.20 — Summary verdict table vs. prior docs' explicit check-list (from the task instructions):**

| Item task asked to re-verify | Verdict this audit |
|---|---|
| `GET /api/v1/records` filter by `issuer_id`? | **Still NO filter — IDOR confirmed present**, `documents.py:84` |
| `Principal.sub` = `claims.get("sub","")`? | **Still present unchanged**, `keycloak.py:196` |
| `jwt.get_unverified_header` outside try/except? | **Still present unchanged**, `keycloak.py:114` (try starts at 130) |
| `MAX_UPLOAD_BYTES` vs magic-byte validation | **Still absent** — no content-type/magic-byte validation found anywhere in `documents.py`; `_read_upload` (`documents.py:27-33`) only checks byte-length, nothing else. `REG-6`-style non-PDF acceptance is a direct code-level consequence, consistent with prior doc. |
| `TEST_MODE` bypass wired into `python -m trustmark.main`? | **Still present and still wired**, `main.py:109-113`, `Dockerfile:50` |
| `verify.py` `is not str` bug | **Confirmed present**, `verify.py:39,44` (see D.18 for exact text) |

---

## E. Source File Inventory

| Path | Approx line count | Purpose | Test coverage (Test IDs) | Associated findings |
|---|---|---|---|---|
| `src/trustmark/main.py` | 114 | FastAPI app construction, router mounting, CORS, TEST_MODE bypass, uvicorn entrypoint | `tests/trustmark/test_main_test_mode.py` (4 tests, B.4) | D.10, D.11, D.19 |
| `src/trustmark/api/v1/documents.py` | 129 | `/register`, `/records`, `/verify`, `/verify/{file_hash}` handlers | `tests/trustmark/api/test_documents.py` (27 tests, B.5) | D.2, D.5, D.8, D.10 |
| `src/trustmark/api/v1/metrics.py` | 20 | `{API_PREFIX}/health` route | NONE (no dedicated test file found for this module) | D.11 |
| `src/trustmark/api/v1/exports.py` | 0 (empty) | unknown/unused | NONE | none |
| `src/trustmark/api/v1/inventor.py` | 0 (empty) | unknown/unused | NONE | none |
| `src/trustmark/api/v1/status.py` | 0 (empty) | unknown/unused | NONE | none |
| `src/trustmark/api/v1/verification.py` | 0 (empty) | unknown/unused | NONE | none |
| `src/trustmark/infra/auth/keycloak.py` | 213 | JWT verification (`KeycloakVerifier`), `Principal`, `get_current_principal`, `require_roles`, `_extract_roles` | `tests/trustmark/infra/auth/test_keycloak.py` (39 tests, B.6) | D.2, D.3, D.4, D.12 |
| `src/trustmark/infra/blockchain_connector.py` | 85 | `BlockchainConnector`: tx create/read/wait against Web3-compatible RPC | `tests/trustmark/infra/test_blockchain_connector.py` (14 tests, B.2) | D.6, D.7, D.14, D.15 |
| `src/trustmark/infra/commons.py` | 55 | Dynaconf `settings` singleton, `get_env_int`, `project_details` | `tests/trustmark/infra/test_commons.py` (4 tests, B.3) | none new |
| `src/trustmark/infra/hash_engine.py` | 17 | SHA-256 hashing (`generate_hash`, `generate_hash_from_bytes`, `canonicalize_text`) | `tests/trustmark/infra/test_hash_engine.py` (7 tests, B.1) | D.5 |
| `src/trustmark/infra/hash_generator.py` | 0 (empty) | unused/vestigial | NONE | D.13 |
| `src/trustmark/infra/db.py` | 35 | SQLAlchemy engine/session setup, `Base`, `get_db` | NONE (no dedicated test file; exercised indirectly only via mocked `Session` in `test_documents.py`, never against a real DB/SQLite file in `tests/trustmark`) | none new (see F for DB-integration-test gap) |
| `src/trustmark/infra/cache.py` | 0 (empty) | unknown/unused | NONE | none |
| `src/trustmark/infra/reports.py` | 0 (empty) | unknown/unused | NONE | none |
| `src/trustmark/registry/models.py` | 18 | `RegistryRecord` SQLAlchemy model | Indirectly via `test_documents.py`'s `make_record()` helper (constructs real `RegistryRecord` instances, but never persists/queries them against a real DB in `tests/trustmark`) | none new |
| `src/trustmark/models/documents.py` | 17 | `Provenance`/`Document` Pydantic models | NONE | D.1 (dead code) |
| `src/trustmark/services/__init__.py` | 0 (empty) | unused/unwired | NONE | none |
| `src/trustmark/services/etherum_connector.py` | 0 (empty) | unused/unwired [sic — "etherum" misspelling in filename] | NONE | none |
| `src/trustmark/services/github_connector.py` | 0 (empty) | unused/unwired | NONE | none |
| `src/trustmark/services/graphdb_connector.py` | 0 (empty) | unused/unwired | NONE | none |
| `src/trustmark/services/x_connector.py` | 0 (empty) | unused/unwired | NONE | none |
| `tests/api/conftest.py` | 118 | E2E fixtures/helpers for live-server tests against `https://pits.pangkalandata.id` | N/A (fixture file itself) | none new |
| `tests/api/test_register_verify_e2e.py` | 467 | 27 live-server E2E test functions (B.7) | N/A (is the test file) | see B.7 |
| `tests/locust/locustfiles/health.py` | 27 | Load test, `{API_PREFIX}/health` | N/A (not pytest) | none new |
| `tests/locust/locustfiles/register.py` | 74 | Load test, `/register` | N/A (not pytest) | none new (correctly uses `type(...) is not ...`) |
| `tests/locust/locustfiles/verify.py` | 118 | Load test, `/verify` + `/verify/{hash}` | N/A (not pytest) | D.18 |
| `tests/locust/locust.conf` | 9 | Locust CLI config (target `http://127.0.0.1:41012`, 10 users, 60s run) | N/A | none new |
| `conf/settings.toml` | 15 | Dynaconf settings, `@format {env[...]}` substitutions | N/A (config file) | ties to D.8, A.7 |
| `docker/Dockerfile` | 52 | App container build | N/A | D.16, D.17 |
| `docker/docker-compose.yml` | 89 | 4-service local/UAT stack (postgres, keycloak, anvil, trustmark-app) | N/A | D.16 |
| `docker/.env.example` | 24 | Example env values for compose stack | N/A | A.6 (Anvil default key), A.7 |
| `pyproject.toml` | 42 | Project metadata, dependencies, `start` script entrypoint | N/A | D.9 |

---

## F. Unknowns

- INFORMATION NEEDED: whether a fuller git history exists in an upstream/private repo this `backend-for-uat` copy was exported/squashed from — this repo's own `git log` shows only 6 commits total, all dated around the same UAT-release window, with no history predating "Initial stakeholder UAT release" (`a70d53b`).
- INFORMATION NEEDED: exact pytest config section in `pyproject.toml` — `grep -n -A10 "\[tool.pytest" pyproject.toml` returned no output, meaning either no `[tool.pytest.ini_options]` block exists (pytest is simply using defaults / rootdir auto-detection via presence of `pyproject.toml`), or it exists under a key spelling this grep pattern didn't match. Not further investigated as it doesn't affect the test-run facts already captured directly via pytest's own banner output.
- INFORMATION NEEDED: the actual deployed production chain ID and whether `pits.pangkalandata.id`'s current backend connects to the same local Anvil pattern shown in this repo's `docker-compose.yml`, or something else — this repo's config files only describe a local Anvil setup; the prior QA results doc's production testing (`https://pits.pangkalandata.id`) implies a real deployed environment whose exact infra (same docker-compose? different?) is not fully re-derivable from this repo alone.
- INFORMATION NEEDED: current production Keycloak client-scope configuration for client `nextjs-web` — specifically whether a `sub` protocol mapper has been added since the 2026-09-09 results doc's finding that the production access token for `uat-tester` had no `sub` claim at all. Not re-verified this audit (no live E2E execution performed, per task's execution-safety constraint).
- INFORMATION NEEDED: web3.py 7.16.0's exact default timeout behavior for `w3.eth.wait_for_transaction_receipt` when no timeout kwarg is passed (relevant to D.15) — not investigated by reading web3.py's own source, which is outside this repository.
- INFORMATION NEEDED: what exact "17-test set" and what exact paper "§5.1" the uncommitted `test_blockchain_connector.py` docstring (D.7) refers to — no paper document exists inside this repository to cross-check the "16 out of 17...final test run passed 17 out of 17" claim against. This audit did not have access to the referenced academic paper's text.
- INFORMATION NEEDED: whether `tests/api/test_register_verify_e2e.py`'s 2026-09-09 results (27 tests: 19 pass / 3 fail / 2 skip / 3 xfail, per `_docs/qa/results/api-test-results-2026-09-09.md`) still hold today (2026-09-13) — **NOT RE-EXECUTED THIS AUDIT** per the task's explicit instruction not to run live-production E2E tests without working internet+credentials confirmed available; this session did not attempt to reach `https://pits.pangkalandata.id` or `https://keycloak.pangkalandata.id`.
- INFORMATION NEEDED: whether `docker/.env` and `docker/.env.save` (both present in the repo, read partially — `.env.example` was read in full per task instructions, `.env`/`.env.save` were located by `find` but not opened in full during this audit to avoid unnecessarily reproducing what could be live/production-adjacent secret material beyond the task's explicit ask for `.env.example`) contain values that differ meaningfully from `.env.example` — `INFORMATION NEEDED: confirm whether .env / .env.save contain the same Anvil-default test key and same-shape localhost URLs as .env.example, or materially different (e.g. real production) values` — this was deliberately not fully read/reported to avoid transcribing potentially-live credential material into this document beyond what the task explicitly asked for (`.env.example`).
- Whether the empty stub files (`exports.py`, `inventor.py`, `status.py`, `verification.py`, `cache.py`, `reports.py`, all four `services/*.py`) represent planned-but-unbuilt features, deliberately scaffolded placeholders, or leftover artifacts from a larger private codebase this UAT export was trimmed from — INFORMATION NEEDED, no changelog/README/docs found in-repo explaining their intended purpose.
