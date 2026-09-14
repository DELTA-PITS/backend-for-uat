# PITS Post-Fix QA Master — Regression & Retest Report

**Date**: 2026-09-14
**Executed by**: Claude Code (independent QA, same continuation as `pits-qa-master-export-2026-09-13.md` and `pass-5-integration-reproducibility.md`)
**Baseline this file compares against**: `pass-5-integration-reproducibility.md` (before-fix, unchanged, do not re-read for numbers — this file supersedes it for anything post-fix)

**Bottom line**: all 3 fixable application-code bugs were fixed, and — on explicit user confirmation — the one non-code finding (missing Keycloak `sub` claim) was also fixed, in both production and this repo's local realm export. After all fixes, **the full 53-test Pass 5 integration suite is 53/53 PASS, 0 FAIL, 0 SKIP.** All 94 backend unit tests still pass. Frontend was not touched in this fix pass (see §3).

---

## 1. Code Changes / Fix Summary

| # | Finding | File / area | Commit | Date |
|---|---|---|---|---|
| F1 | IDOR — `GET /records` returned every publisher's records | `src/trustmark/api/v1/documents.py::list_records()` — added `.filter_by(issuer_id=principal.sub)` | `c0598d1` | 2026-09-14 19:50:22 +0700 |
| F2 | Malformed bearer token → unhandled `500` instead of `401` | `src/trustmark/infra/auth/keycloak.py::KeycloakVerifier.verify()` — wrapped `jwt.get_unverified_header()` in try/except for `JWTError` | `c0598d1` | 2026-09-14 19:50:22 +0700 |
| F3 | Filename > 255 chars → unhandled `500` (DB column-length violation) | `src/trustmark/api/v1/documents.py::register()` — added `_validate_filename()` (400 for names > 255 chars) | `c0598d1` | 2026-09-14 19:50:22 +0700 |
| F4 | `BC-INT-08` (pending-transaction handling) was SKIPPED, not tested | `tests/integration_pass5/test_blockchain_integration.py` — test now genuinely forces a pending tx via a temporary block-time Anvil restart + direct DB insert, bypassing `register()` | `5e449e6` | 2026-09-14 21:51:39 +0700 |
| F5 | Missing Keycloak `sub` claim → empty `issuer_id` on every record (Finding 2 in the master export / pass-5 report) | **Not application code.** Realm/client protocol-mapper configuration gap. Fixed in two places (§1.1) | — | 2026-09-14 (this session, after F1–F4) |

Associated unit-test updates (same commit `c0598d1`, so their FAIL/PASS status wasn't hidden by test drift):
- `tests/trustmark/api/test_documents.py::TestListRecords` — renamed `test_records_from_two_issuers_visible_to_one_principal` → `test_records_filtered_to_requesting_principal`; now asserts the fix (`filter_by(issuer_id=...)` called, only requester's records returned).
- `tests/trustmark/infra/auth/test_keycloak.py::TestVerify` — renamed `test_verify_malformed_token_is_unhandled` → `test_verify_malformed_token_returns_401`; now asserts `HTTPException(401)`.

Both renames match the docstring each test carried since it was written ("once fixed, update this test to assert X instead").

### 1.1 F5 detail — the two-part infrastructure fix

Finding 2's root cause (established in `pass-5-integration-reproducibility.md` §3) is that neither Keycloak realm/client definition — production or this repo's own `docker/realms/realm-export.json` used to build every local/disposable stack — carries a protocol mapper that emits the `sub` claim. Fixing only one side would have left the other still broken (production tokens fixed but every future local test run still reproducing Finding 2, or vice versa), so both were fixed identically:

**Production** (`keycloak.pangkalandata.id`, realm `nextjs-kc`, client `nextjs-web`, uuid `a3098ba0-40bd-4d21-a5d4-04af8de6a2c6`):
- Backed up the client's full config first: `_docs/qa/pass5/keycloak-prod-backup/nextjs-web-client-before-2026-09-14.json`.
- Added protocol mapper `sub-claim` (`oidc-usermodel-property-mapper`, `user.attribute=id`, `claim.name=sub`) via the admin REST API.
- Verified before/after with a real `uat-tester` login:
  - Before: `sub: None`
  - After: `sub: e5100f48-caeb-4cb9-a094-d3b534c7adaa` (uat-tester's real Keycloak user id)

**This repo** (`docker/realms/realm-export.json`, used by `docker compose up` to build every disposable local test stack):
- Added the identical protocol mapper to the `nextjs-web` client definition in the JSON file (16-line diff, `git diff docker/realms/realm-export.json`).
- Rebuilt the local stack from a clean `docker compose down -v` → `up -d`, recreated `publisher-a`/`publisher-b`/`user-c`, confirmed `sub` now present on a fresh token.

**Caveat, stated for the paper**: existing records registered *before* this fix (in production, and in any prior local test run) permanently have `issuer_id=""` — this is not retroactively repairable, since the empty string was already committed to those rows. Only records registered *after* the fix carry a real publisher identity.

---

## 2. Regression Test Results (test-level evidence, not just numbers)

Evidence level for every result below: `INTEGRATION-REAL-LOCAL` (real Postgres + real Keycloak + real Anvil, freshly rebuilt disposable stack, no mocks) unless stated otherwise. Backend unit results are `UNIT-MOCKED`.

### F1 — Cross-Publisher IDOR

**Before Fix**
Status: FAIL
Publisher A's `GET /records` included Publisher B's record (and vice versa) — all fields exposed (`record_id, content_hash, filename, transaction_hash, created_at, issuer_id`).

**Code Fix**
File: `src/trustmark/api/v1/documents.py::list_records()`
Commit: `c0598d1`
Change: added `.filter_by(issuer_id=principal.sub)` before `.order_by(...)`.

**Regression Test**
Test ID: `tests/integration_pass5/test_isolation.py::TestIsolation::test_iso_01_and_02_cross_publisher_visibility`
Expected: Publisher A's response contains only A's own records; Publisher B's contains only B's own.
Actual (post F1+F5 fix, real 2-account run): `ISO-01: publisher-a sees 52 records total, includes own record_id=True, includes B's record_id=False` / `ISO-02: publisher-b sees 8 records total, includes own record_id=True, includes A's record_id=False`
Result: **PASS**

**Important nuance (documented, not glossed over)**: immediately after the F1 code fix alone (Finding 2/F5 still unfixed), this test **still FAILED** — because every publisher's `principal.sub` was `""`, the new filter became `WHERE issuer_id = ''` for everyone, which still matched every record ever written with an empty issuer_id (i.e., everyone's). The fix was correct but had no visible effect until F5 (the Keycloak config) was also fixed. Verified intermediate state (F1 fixed, F5 not yet):
```
ISO-01: publisher-a sees 39 records total, includes own record_id=True, includes B's record_id=True
```
This is preserved here as evidence that F1 and F5 are independent-but-coupled fixes, not that F1 alone was sufficient.

**After Fix Status**: **RESOLVED** (both F1 and F5 required)

**Evidence**: pytest output above; full suite log `/tmp/pass5_allfixed.log` (local, not committed — see §10 for what raw artifacts are in the repo).

---

### F5 — Missing `issuer_id` / Keycloak `sub`

**Before Fix**
Status: FAIL (4 tests: `DB-INT-07`, `KC-INT-02`, `KC-INT-03`, `KC-INT-04`)
Every publisher's JWT had no `sub` claim; every registered record's `issuer_id` was `""`.

**Fix**: see §1.1 — Keycloak protocol mapper, production + local realm export.

**Regression Tests**

| Test ID | Expected | Actual (post-fix) | Result |
|---|---|---|---|
| `test_kc_int_02_token_has_nonempty_sub` | Token `sub` non-empty | `sub='ebdb9595-e141-4d7c-badd-0a408c04ec44'` | **PASS** |
| `test_kc_int_03_principal_sub_equals_jwt_sub` | Registered `issuer_id` == token `sub` | Confirmed equal | **PASS** |
| `test_db_int_07_issuer_id_persists_as_authenticated_sub` | DB row `issuer_id` == JWT `sub` | `DB-INT-07: token sub='ebdb9595-e141-4d7c-badd-0a408c04ec44', stored issuer_id='ebdb9595-e141-4d7c-badd-0a408c04ec44'` | **PASS** |
| `test_kc_int_04_registration_stores_issuer_id_equal_to_sub` (Publisher B) | Same, second account | Confirmed equal for `publisher-b` too | **PASS** |

**After Fix Status**: **RESOLVED**

---

### F2 — Malformed Bearer Token → 500

**Before Fix**
Status: FAIL
`Authorization: Bearer this-is-not-a-jwt-at-all` → unhandled `500 Internal Server Error` (real HTTP round trip against the live backend).

**Code Fix**
File: `src/trustmark/infra/auth/keycloak.py::KeycloakVerifier.verify()`
Commit: `c0598d1`
Change: wrapped `jwt.get_unverified_header(token)` in try/except for `JWTError`, raising `HTTPException(401, "Malformed token")`.

**Regression Test**
Test ID: `test_kc_int_08_malformed_bearer_returns_401_not_500`
Expected: controlled `401`.
Actual: `KC-INT-08: status=401, body={"detail":"Malformed token"}`
Result: **PASS**

**After Fix Status**: **RESOLVED**

---

### F3 — Filename > 255 Chars → 500

**Before Fix**
Status: FAIL (newly discovered in Pass 5)
A 504-character filename crashed the DB insert with an unhandled `500` (`original_filename` is `VARCHAR(255)`, no application-level length check).

**Code Fix**
File: `src/trustmark/api/v1/documents.py::register()`
Commit: `c0598d1`
Change: added `_validate_filename()`, called before `_read_upload()`, rejecting names > 255 chars with a clean `400`.

**Regression Test**
Test ID: `test_file_09_very_long_filename`
Expected: clean `4xx`, not a crash.
Actual: `FILE-09: status=400, filename_len=504` / `FILE-09: rejected: {"detail":"Filename exceeds the maximum length of 255 characters"}`
Result: **PASS**

**After Fix Status**: **RESOLVED**

---

### F4 — BC-INT-08 Was Skipped, Not Tested

**Before Fix**
Status: SKIP ("Anvil auto-mines instantly, pending-tx state not reproducible")

**Test Fix** (not application code — this finding was that a test was left unexecuted, not that the app was broken)
File: `tests/integration_pass5/test_blockchain_integration.py::test_bc_int_08_pending_transaction_handling`
Commit: `5e449e6`
Change: test now temporarily restarts Anvil with `--block-time 6`, fires a transaction directly via `BlockchainConnector.create_transaction()` without waiting for a receipt, inserts a matching DB row directly (bypassing `register()`, which would refuse to persist a pending tx), and hits the real `GET /verify/{hash}` endpoint while genuinely unmined.

**Regression Test**
Expected: real `400 "Transaction is still pending"` while unmined, `200 valid:true` once mined.
Actual: confirmed both, in sequence, against the live local backend (see pass-5 report §10.5 for the full transcript).
Result: **PASS**

**After Fix Status**: **RESOLVED** (was never "broken" — was untested; now tested and confirmed correct)

---

## 3. Full Test Rerun Summary (by layer)

| Layer | Count | Result (post-fix) | Changed since baseline? |
|---|---|---|---|
| Backend unit (`tests/trustmark`) | 94 | **94 PASS** | Test IDs for 2 tests renamed (F1/F2 regression guards), count unchanged |
| Backend Pass 5 integration (`tests/integration_pass5`) | 53 | **53 PASS, 0 FAIL, 0 SKIP** | Baseline was 45/7/1 → 44/8/1 → 47/5/1 → 48/5/0 → **53/0/0** (this file) |
| Backend API E2E (live historical, `tests/api`) | 27 | **NOT RERUN** | Unchanged from 2026-09-09 baseline (19 pass/3 fail/2 skip/3 xfail) — these hit production over HTTP and were not re-executed in this fix pass; production's Keycloak config *was* changed (§1.1), so a rerun would likely change AUTH-7's outcome, but this was not verified live in this pass |
| Frontend unit (Vitest) | 30 | **NOT RERUN** | No frontend code was touched; last known: 30/30 (2026-09-13) |
| Frontend E2E (Playwright, historical) | 18 | **NOT RERUN** | No frontend code was touched; last known: ~14 pass / 4 fail (2026-09-09, with documented ambiguity on TC-16) |

**Why frontend and the live API E2E suite were not rerun**: this fix pass targeted the 3 code bugs and 1 config bug found in Pass 5's backend-only local integration testing. No frontend file was modified, so a frontend rerun would not exercise any changed code. The live API E2E suite (`tests/api`) hits *production* over HTTP — production's Keycloak config was changed (§1.1), which could plausibly change `AUTH-7`'s specific IDOR-inference result now that `sub` is non-empty, but production's *application code* (the actual FastAPI backend) has **not** been redeployed with the F1/F2/F3 code fixes yet — see §9 for this important gap.

---

## 4. Finding Status After Fix (summary table)

| Finding | Status | Evidence |
|---|---|---|
| IDOR `/records` (F1) | **RESOLVED** | §2 F1, `test_iso_01_and_02_cross_publisher_visibility` PASS |
| `issuer_id` empty / Keycloak `sub` (F5) | **RESOLVED** | §2 F5, `KC-INT-02/03/04`, `DB-INT-07` all PASS |
| Malformed bearer → 500 (F2) | **RESOLVED** | §2 F2, `KC-INT-08` PASS |
| Filename too long → 500 (F3) | **RESOLVED** | §2 F3, `FILE-09` PASS |
| PDF/file content validation (magic bytes) | **NOT FIXED** | Still accepts non-PDF bytes as long as extension/MIME are claimed correctly — `FILE-02`/`FILE-03`/`FILE-04` in Pass 5 documented this as a gap, not addressed in this pass. Out of scope by user's own prioritization this session. |
| `TEST_MODE` auth-bypass mechanism | **NOT FIXED** | Still present in `src/trustmark/main.py` — a deployment-hardening decision (should this code path exist in a production image at all), not addressed this pass |
| BC-INT-08 (pending-tx) left as SKIP | **RESOLVED** (test coverage gap, not an app bug) | §2 F4 |
| DUP-4-style race condition | **N/A — not a confirmed bug** | See pass-5 report §8; not reproduced in 5-concurrent-request trials, nothing to fix |

---

## 5. Integration Retest — Full Pass 5 Suite

**Baseline (before any fix)**: 45 PASS / 7 FAIL / 1 SKIP (later corrected to 44/8/1 — a test bookkeeping fix, see pass-5 report §9).

**Final, post-all-fixes, fresh-stack rerun**:

```
53 tests collected
53 passed, 0 failed, 0 skipped
```

Progression across this session (each step independently verified against a freshly rebuilt disposable stack):

| Step | PASS | FAIL | SKIP | What changed |
|---|---|---|---|---|
| Initial report | 45 | 7 | 1 | — |
| Count correction | 44 | 8 | 1 | Fixed a missing assertion in `FILE-09`'s own test (test bug, not app bug) |
| F1+F2+F3 code fixes | 47 | 5 | 1 | IDOR filter, malformed-token 401, filename validation |
| F4 (BC-INT-08 de-skipped) | 48 | 5 | 0 | Test now genuinely forces and verifies a pending-tx state |
| **F5 (Keycloak `sub`, prod + local)** | **53** | **0** | **0** | Remaining 5 FAILs were all rooted in the missing `sub` claim |

No test in the 53-test suite is currently failing, skipped, or unverified.

---

## 6. Provenance Retest

Golden path (`PROV-01`) rerun end-to-end post-fix:

```
PROV-01 evidence: record_id=ea3a0bf9-efde-467c-9f4c-d3dee9a29c05
PROV-01 evidence: content_hash=6b851cd719423d1d651f924b959ff09c74ccc6249c6e2ddb834c6f0f177cacc9
PROV-01 evidence: transaction_hash=90bd9847dc26831fafbacaf262df1203ee13a334b9b4f4de62819a47d496e721
PROV-01 evidence: issuer_id='ebdb9595-e141-4d7c-badd-0a408c04ec44'
PROV-01 evidence: token sub='ebdb9595-e141-4d7c-badd-0a408c04ec44'
PROV-01 evidence: onchain_value=6b851cd719423d1d651f924b959ff09c74ccc6249c6e2ddb834c6f0f177cacc9
PROV-01 evidence: public verify body={'valid': True, 'record_id': 'ea3a0bf9-efde-467c-9f4c-d3dee9a29c05', ...}
PROV-01 PASSED: full provenance chain confirmed end-to-end on local stack.
```

Key difference from the before-fix run: `issuer_id` now equals the token's real `sub` (`ebdb9595-e141-4d7c-badd-0a408c04ec44`), rather than the pre-fix empty string. Every other link in the chain (upload SHA-256 == registry `content_hash` == on-chain value; public unauthenticated verification succeeds) was already correct pre-fix and remains correct post-fix.

**Tamper test (`PROV-02`)**:
```
PROV-02 evidence: original_hash=028cdd..., modified_hash=fd2440..., result={'valid': False, ...}
```
PASS, unchanged from before-fix behaviour (this was never broken).

**Unregistered document (`PROV-03`)**:
```
PROV-03 evidence: {'valid': False, 'content_hash': '57c0008c...'}
```
PASS, unchanged.

**Golden path status**: **PASS**, before and after fix — the fix improved identity attribution within the chain, it did not change whether the chain itself worked.

---

## 7. Multi-Publisher Retest

Two real, independently-authenticated Keycloak accounts (`publisher-a`, `publisher-b`), rerun post-fix:

```
ISO-01: publisher-a sees 52 records total, includes own record_id=True, includes B's record_id=False
ISO-02: publisher-b sees 8 records total, includes own record_id=True, includes A's record_id=False
```

Publisher A no longer sees Publisher B's records, and vice versa. `ISO-04` (multiple records per publisher) confirmed the same holds at scale:

```
ISO-04: after multiple records per publisher, GET /records for A returns 55 total rows spanning 1 distinct issuer_id value(s): {'ebdb9595-e141-4d7c-badd-0a408c04ec44'}
```

All 5 isolation tests (`ISO-01` through `ISO-05`) now **PASS**.

**Note on record counts** (52, 8, 55): these are cumulative counts across every registration made by each account throughout this entire QA session (Pass 5 + this fix pass), since the same disposable Postgres volume accumulated records across multiple test runs before being torn down. The relevant number is not the total count but that publisher-a's 52 records are *all* publisher-a's own (0 leaked from B), and publisher-b's 8 are *all* its own (0 leaked from A).

---

## 8. Performance Retest

**Not performed in this fix pass.** The 3-run Locust benchmark from Pass 5 (`_docs/qa/pass5/locust-runs/run{1,2,3}/`) remains the only Locust data — those numbers are **not** superseded or invalidated by this fix pass, and are not re-presented here per the instruction not to mix them with a new run. If a post-fix Locust run is wanted, it should be a clearly-labeled 4th run, not blended with runs 1–3 (which pre-date the `issuer_id` fix and would show different failure characteristics — `verify_with_path_parameter`'s `issuer_id` assertion, which failed on every request in runs 1–3 due to Finding 2, would now pass).

---

## 9. Remaining Open Findings / Known Limitations

- **Production backend application code has not been redeployed.** SSH'd into the production server (`209.58.160.63`) to check: `/home/hamka/pits/backend-for-uat` is pinned at git commit `c6a5638` ("Allow HTTP for local Keycloak realm"), which predates **all** of: the 2026-09-09 API E2E/unit-hardening work (`4155258`), the Pass 5 suite (`4097005`), and all 3 code fixes in this file (`c0598d1`, `5e449e6`). The production checkout also has local, uncommitted modifications (`docker/docker-compose.yml` changed; several `.bak-*` files and a `docker/themes/` directory not in git) — deployment there is evidently done by direct file patching on the server, not `git pull`. **This means F1 (IDOR), F2 (malformed-token 500), and F3 (filename crash) are fixed in this repository and verified locally, but are very likely still live/unfixed on the actual production backend.** Only F5 (the Keycloak identity-provider config) was fixed in production itself, since that's a Keycloak-side change independent of the backend's own deployed code. Deploying the 3 code fixes to production was treated as a separate, higher-risk decision (touches the live backend container serving real traffic) and was not done as part of this fix pass — flagging this explicitly rather than letting the paper imply production is patched.
- **Live API E2E suite (`tests/api`, hits production) not rerun** — see §3. Given the note above, rerunning it now would very likely still show the IDOR (`AUTH-7`) and malformed-token (`AUTH-2`) findings live in production, precisely because the code fix hasn't been deployed there yet.
- **Frontend (unit + E2E) not touched or rerun** — no frontend code was in scope for this fix pass.
- **PDF magic-byte/content validation** — still absent; not addressed.
- **`TEST_MODE` auth-bypass mechanism** — still present in `main.py`; a deployment-hardening decision not addressed this pass.
- **DUP-4-style race condition** — never confirmed as a real bug (5-concurrent-request trials in Pass 5 always resolved to exactly 1 authoritative row); no fix needed unless a future, more aggressive concurrency test finds otherwise.
- **Anvil non-persistence** (BC-INT-10, transaction history lost on container restart) — an accepted environment/prototype limitation, not something to "fix" for a local dev chain.
- **Locust performance retest** — not performed, see §8.
- **Existing production records permanently carry `issuer_id=""`** — the Keycloak fix (F5) only affects records registered after 2026-09-14; historical records cannot be retroactively attributed.

---

## 10. Raw Evidence Index

| Artifact | Path |
|---|---|
| Before-fix Pass 5 report (baseline, unchanged content + this addendum note) | `_docs/qa/pass-5-integration-reproducibility.md` |
| This post-fix master report | `_docs/qa/pits-post-fix-qa-master.md` |
| Production Keycloak client backup (before F5) | `_docs/qa/pass5/keycloak-prod-backup/nextjs-web-client-before-2026-09-14.json` |
| Code fix commits | `c0598d1` (F1/F2/F3), `5e449e6` (F4) — `backend-for-uat` repo, branch `main` |
| Local realm-export fix (F5, local side) | `docker/realms/realm-export.json` (git diff: +16 lines, added `sub-claim` protocol mapper) |
| Updated unit tests | `tests/trustmark/api/test_documents.py`, `tests/trustmark/infra/auth/test_keycloak.py` (both in commit `c0598d1`) |
| Updated integration test (F4) | `tests/integration_pass5/test_blockchain_integration.py` (commit `5e449e6`) |
| Full backend unit test run (94/94) | Not saved as a file this pass — reproducible via `pytest tests/trustmark -v` with the env vars listed in the Pass 5 report §2.1 |
| Full Pass 5 integration rerun (53/53) | Not saved as a file this pass — reproducible via the exact commands in the Pass 5 report §2.5, against a stack built from the now-updated `realm-export.json` |
| Locust raw artifacts (Pass 5, pre-fix, not superseded) | `_docs/qa/pass5/locust-runs/run{1,2,3}/` |
| QA evidence HTML package (deployed, pre-dates this fix pass) | `frontend-for-uat/_docs/qa/results/pass5-evidence-package/`, live at `https://pits-ui.pangkalandata.id/qa-evidence-pass5/` — **not yet updated with post-fix numbers**, still reflects the 44/8/1-then-corrected state |

**Note on "pytest XML/log" artifacts**: this pass ran pytest with plain terminal output (`-v`, `-s`), not `--junitxml` or persisted log files — the evidence above is transcribed verbatim from those runs into this document and the commit messages, but no separate `.xml`/`.log` file exists in the repo for this specific fix-verification pass (unlike Pass 5's Locust runs, which do have raw CSV/HTML artifacts). If machine-readable pytest output is needed for the paper's own archival purposes, it can be regenerated by rerunning the documented commands with `--junitxml=<path>` added.
