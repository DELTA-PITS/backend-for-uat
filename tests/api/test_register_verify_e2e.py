"""
Automated implementation of the scenarios designed in
_docs/qa/api-test-scenarios.md (REG-1..6, DUP-1..4, AUTH-1..8, VER-1..8).

Executed live against https://pits.pangkalandata.id — no mocking, no access
to the FastAPI app object. See tests/api/conftest.py for fixtures/helpers.

Known limitations (documented instead of silently skipped, per scenario doc
section 6 / task instructions):
  - DUP-2 and the cross-account half of AUTH-7 need a SECOND publisher
    account. `test-publisher` exists in Keycloak but its password is
    unknown, and creating a new Keycloak user was blocked by this session's
    tool-permission policy. Those scenarios are marked xfail(strict=False)
    with a clear reason instead of being silently skipped, and AUTH-7 is
    additionally checked with the single-account evidence described in the
    scenario doc (any issuer_id != uat-tester's sub appearing in /records is
    already proof of the IDOR).
  - AUTH-4 (token valid, `publisher` role absent) needs an account without
    that role. Same blocker as above - no second/alternate account available
    this session.
  - AUTH-5 (issuer/audience mismatch) can't be produced with a *validly
    signed* token from another issuer without a second Keycloak realm and
    its private key. We instead send a token with an unknown `kid`, which
    exercises the same failure path (verification fails before iss/aud is
    even checked) and must still return 401.
  - VER-7 (on-chain data manipulated after DB write) and VER-8 (blockchain
    node down) require direct server/infra access (editing Anvil chain state
    or cutting RPC connectivity), which is out of scope for HTTP-only
    testing. Documented as not executed, not skipped silently.
"""
from __future__ import annotations

import concurrent.futures
import time

import pytest
import requests

from conftest import (
    BASE_URL,
    MAX_UPLOAD_BYTES,
    fetch_token,
    list_records,
    register,
    unique_pdf_bytes,
    verify,
    verify_by_hash,
)


# ---------------------------------------------------------------------------
# 1. Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_reg1_register_new_document(self, publisher_token):
        content = unique_pdf_bytes("REG-1")
        resp = register(publisher_token, content)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stored"] is True
        assert body["already_existed"] is False
        assert body["transaction_hash"]

        import hashlib

        assert body["content_hash"] == hashlib.sha256(content).hexdigest()

        # FINDING: production Keycloak token for uat-tester (client nextjs-web)
        # carries NO "sub" claim at all (confirmed by decoding the JWT payload
        # directly - see results doc). Principal.sub falls back to "" and every
        # record ever registered - by any publisher, including historical docs
        # from before this test session - has issuer_id == "". This assertion
        # is intentionally left strict so the gap keeps failing loudly instead
        # of being silently tolerated.
        assert body["issuer_id"], (
            "issuer_id is empty - Keycloak access token has no 'sub' claim, so "
            "every registered document loses its publisher attribution"
        )

    def test_reg2_empty_file(self, publisher_token):
        resp = register(publisher_token, b"")
        assert resp.status_code == 400, resp.text
        assert "empty" in resp.json()["detail"].lower()

    def test_reg3_file_exceeds_limit(self, publisher_token):
        """FINDING: production nginx enforces its own default
        `client_max_body_size 1m` (~1 MiB) in front of the app, which is far
        below the app's configured MAX_UPLOAD_BYTES=20MB. A file sized to
        exceed the *app's* 20MB limit still gets 413 - but from nginx, as a
        raw HTML page, not the app's JSON `{"detail": "..."}`. Confirmed by
        binary search: the real cutoff is ~1,048,403 bytes of multipart body
        (see results doc)."""
        content = b"0" * (2 * 1024 * 1024)  # 2MB: clearly over both possible limits
        resp = register(publisher_token, content)
        assert resp.status_code == 413, resp.text
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            assert "exceed" in resp.json()["detail"].lower()
        else:
            # nginx's own 413 page intercepted the request before FastAPI ran
            assert "413" in resp.text

    @pytest.mark.xfail(
        strict=False,
        reason="app's MAX_UPLOAD_BYTES=20MB boundary is unreachable in production: "
        "nginx's default client_max_body_size (~1MiB) rejects the request before "
        "it reaches FastAPI. See test_reg4b_file_at_effective_nginx_limit for the "
        "boundary that IS reachable.",
    )
    def test_reg4_file_exactly_at_limit(self, publisher_token):
        # unique marker at the front so it doesn't collide with reg3/other runs
        marker = unique_pdf_bytes("REG-4")
        content = marker + b"0" * (MAX_UPLOAD_BYTES - len(marker))
        assert len(content) == MAX_UPLOAD_BYTES
        resp = register(publisher_token, content)
        assert resp.status_code == 200, resp.text
        assert resp.json()["already_existed"] is False

    def test_reg4b_file_at_effective_nginx_limit(self, publisher_token):
        """The boundary that's actually reachable in production: just under
        nginx's ~1MiB client_max_body_size, the app itself still accepts and
        registers the file normally (app-level logic for REG-4 is fine - the
        production infra config is the mismatch, not the app code)."""
        marker = unique_pdf_bytes("REG-4b")
        target_content_bytes = 1_040_000  # safely under the measured ~1,048,403 cutoff
        content = marker + b"0" * (target_content_bytes - len(marker))
        resp = register(publisher_token, content)
        assert resp.status_code == 200, resp.text
        assert resp.json()["already_existed"] is False

    def test_reg5_missing_file_field(self, publisher_token):
        resp = requests.post(
            f"{BASE_URL}/register",
            headers={"Authorization": f"Bearer {publisher_token}"},
            timeout=30,
        )
        assert resp.status_code == 422, resp.text

    def test_reg6_non_pdf_content_type_is_accepted(self, publisher_token):
        """Documents a known gap: no content-type/magic-byte validation."""
        content = b"just a plain text file, not a pdf " + unique_pdf_bytes("REG-6").hex().encode()
        resp = register(
            publisher_token, content, filename="not-a-pdf.pdf", content_type="text/plain"
        )
        assert resp.status_code == 200, (
            f"Expected non-PDF content to be accepted (known gap, no content "
            f"validation in documents.py::register). Got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["stored"] is True


# ---------------------------------------------------------------------------
# 2. Duplicate detection
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    def test_dup1_duplicate_same_publisher(self, publisher_token):
        content = unique_pdf_bytes("DUP-1")
        first = register(publisher_token, content)
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["already_existed"] is False

        second = register(publisher_token, content)
        assert second.status_code == 200, second.text
        second_body = second.json()

        assert second_body["already_existed"] is True
        assert second_body["record_id"] == first_body["record_id"]
        assert second_body["content_hash"] == first_body["content_hash"]
        assert second_body["transaction_hash"] == first_body["transaction_hash"]

    @pytest.mark.xfail(
        strict=False,
        reason="needs a second publisher account; test-publisher password unknown "
        "and creating a new Keycloak user was blocked in this session",
    )
    def test_dup2_duplicate_different_publisher(self, publisher_token):
        second_token = fetch_token("test-publisher", "UNKNOWN")["access_token"]
        content = unique_pdf_bytes("DUP-2")
        first = register(publisher_token, content)
        assert first.status_code == 200
        second = register(second_token, content)
        assert second.status_code == 200
        body = second.json()
        assert body["already_existed"] is True
        assert body["issuer_id"] == first.json()["issuer_id"]

    def test_dup3_same_filename_different_content(self, publisher_token):
        content_a = unique_pdf_bytes("DUP-3-A")
        content_b = unique_pdf_bytes("DUP-3-B")

        resp_a = register(publisher_token, content_a, filename="same-name.pdf")
        resp_b = register(publisher_token, content_b, filename="same-name.pdf")

        assert resp_a.status_code == 200, resp_a.text
        assert resp_b.status_code == 200, resp_b.text
        body_a, body_b = resp_a.json(), resp_b.json()

        assert body_a["already_existed"] is False
        assert body_b["already_existed"] is False
        assert body_a["content_hash"] != body_b["content_hash"]
        assert body_a["record_id"] != body_b["record_id"]

    def test_dup4_concurrent_register_race(self, publisher_token):
        """Adversarial: fire two identical registrations at once and see if the
        check-then-commit gap in register() lets both create blockchain
        transactions for the same content_hash, or if the DB unique
        constraint on content_hash turns the loser into an unhandled 500."""
        content = unique_pdf_bytes("DUP-4-race")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(register, publisher_token, content) for _ in range(2)]
            results = [f.result() for f in futures]

        statuses = sorted(r.status_code for r in results)
        bodies = []
        for r in results:
            try:
                bodies.append(r.json())
            except ValueError:
                bodies.append({"_raw": r.text})

        print("DUP-4 race statuses:", statuses)
        print("DUP-4 race bodies:", bodies)

        ok_bodies = [b for r, b in zip(results, bodies) if r.status_code == 200]
        tx_hashes = {b.get("transaction_hash") for b in ok_bodies if "transaction_hash" in b}

        if statuses == [200, 200]:
            assert len(tx_hashes) == 1, (
                f"RACE CONDITION CONFIRMED: two concurrent registers of the same "
                f"content both returned 200 with DIFFERENT transaction_hash values "
                f"({tx_hashes}) - two blockchain transactions were created for one "
                f"content_hash. bodies={bodies}"
            )
        else:
            # One side likely hit the DB unique constraint on content_hash as an
            # unhandled IntegrityError -> 500, instead of being caught and turned
            # into a graceful already_existed=True response.
            assert 500 not in statuses, (
                f"Concurrent register produced an unhandled error instead of a "
                f"graceful duplicate response: statuses={statuses} bodies={bodies}"
            )


# ---------------------------------------------------------------------------
# 3. Authentication & authorization
# ---------------------------------------------------------------------------


class TestAuth:
    def test_auth1_register_without_token(self):
        resp = register(None, unique_pdf_bytes("AUTH-1"))
        assert resp.status_code == 401, resp.text
        assert "missing bearer token" in resp.json()["detail"].lower()

    def test_auth2_register_malformed_token(self):
        resp = requests.post(
            f"{BASE_URL}/register",
            headers={"Authorization": "Bearer garbage-string"},
            files={"file": ("doc.pdf", unique_pdf_bytes("AUTH-2"), "application/pdf")},
            timeout=30,
        )
        assert resp.status_code == 401, (
            f"Malformed token must not cause a 500. Got {resp.status_code}: {resp.text}"
        )

    def test_auth3_register_expired_token(self, stale_token_capture):
        elapsed = time.monotonic() - stale_token_capture["issued_at"]
        remaining = stale_token_capture["expires_in"] - elapsed
        if remaining > 0:
            time.sleep(remaining + 5)

        resp = register(stale_token_capture["access_token"], unique_pdf_bytes("AUTH-3"))
        assert resp.status_code == 401, resp.text
        assert "expired" in resp.json()["detail"].lower()

    @pytest.mark.xfail(
        strict=False,
        reason="needs an account without the publisher role; none available "
        "this session (only uat-tester, which has publisher)",
    )
    def test_auth4_valid_token_missing_publisher_role(self):
        raise NotImplementedError("no non-publisher test account available")

    def test_auth5_token_unknown_signing_key(self, publisher_token):
        """Proxy for issuer/audience mismatch: a token whose `kid` isn't in the
        server's JWKS fails verification before iss/aud are even checked, so
        this exercises the same 401 failure path AUTH-5 describes."""
        # Tamper the header segment of a real token so its kid no longer matches
        import base64
        import json

        header_b64, payload_b64, sig_b64 = publisher_token.split(".")

        def _b64url_decode(s: str) -> bytes:
            padding = "=" * (-len(s) % 4)
            return base64.urlsafe_b64decode(s + padding)

        def _b64url_encode(b: bytes) -> str:
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

        header = json.loads(_b64url_decode(header_b64))
        header["kid"] = "qa-nonexistent-kid"
        tampered_header_b64 = _b64url_encode(json.dumps(header).encode())
        tampered_token = f"{tampered_header_b64}.{payload_b64}.{sig_b64}"

        resp = requests.post(
            f"{BASE_URL}/register",
            headers={"Authorization": f"Bearer {tampered_token}"},
            files={"file": ("doc.pdf", unique_pdf_bytes("AUTH-5"), "application/pdf")},
            timeout=30,
        )
        assert resp.status_code == 401, resp.text

    def test_auth6_records_without_token(self):
        resp = list_records(None)
        assert resp.status_code == 401, resp.text

    def test_auth7_records_idor_check(self, publisher_token):
        """Adversarial/IDOR: list_records() in documents.py queries
        RegistryRecord with NO issuer_id filter. With only one test account we
        can't do a full two-account A-sees-B comparison, but if uat-tester's
        token can see ANY record whose issuer_id isn't uat-tester's own sub,
        that alone proves the endpoint leaks other publishers' records."""
        resp = list_records(publisher_token)
        assert resp.status_code == 200, resp.text
        records = resp.json()["records"]

        import base64
        import json

        payload = publisher_token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload + padding))
        my_sub = claims.get("sub")

        if not my_sub:
            # BUG (separate from IDOR): uat-tester's access token has no "sub"
            # claim at all (verified directly - see result doc). Backend does
            # principal.sub = claims.get("sub", "") in keycloak.py, so every
            # record this account registers gets issuer_id="". That alone
            # collapses the "is this MY record" comparison this test wanted to
            # do. Fall back to counting distinct issuer_id values instead:
            # if >1 distinct value (even "" vs a real one) shows up, some
            # record belongs to an identity other than this token's.
            distinct_issuers = {r["issuer_id"] for r in records}
            print(f"AUTH-7: token has NO 'sub' claim (claims={sorted(claims.keys())}). "
                  f"Falling back to distinct issuer_id count. "
                  f"total records={len(records)}, distinct issuer_ids={distinct_issuers}")
            pytest.fail(
                "Token has no 'sub' claim, so per-publisher ownership can't be "
                "checked from claims alone. This does NOT clear list_records() "
                "of the IDOR: source code (documents.py::list_records) shows "
                "`db.query(RegistryRecord).order_by(...).all()` with ZERO "
                "issuer_id/WHERE filter - it returns every record in the table "
                "to any authenticated publisher regardless of identity. That is "
                "confirmed by code inspection independent of this token's "
                "missing 'sub' claim. Separately, the missing 'sub' claim is "
                "itself a bug: it is why every registered record's issuer_id "
                "is empty (see REG-1 result)."
            )

        other_issuers = {r["issuer_id"] for r in records if r["issuer_id"] != my_sub}

        print(f"AUTH-7: my sub={my_sub}, total records={len(records)}, "
              f"distinct other issuer_ids visible={other_issuers}")

        if other_issuers:
            pytest.fail(
                "IDOR CONFIRMED: GET /api/v1/records for uat-tester returned "
                f"{len(other_issuers)} other publisher(s)' issuer_id(s) "
                f"({other_issuers}) out of {len(records)} total records. "
                "list_records() has no issuer_id filter - every authenticated "
                "publisher can see every publisher's registry history."
            )
        else:
            pytest.skip(
                "No other-publisher records existed in the DB at test time to "
                "prove the leak empirically (code review still shows the query "
                "has no issuer_id filter - see documents.py::list_records). "
                "Re-run after a second publisher account registers a document, "
                "or treat the code-level finding as sufficient evidence."
            )

    def test_auth8_verify_without_token_is_public(self):
        resp = verify(unique_pdf_bytes("AUTH-8"))
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 4. Verification
# ---------------------------------------------------------------------------


class TestVerification:
    def test_ver1_verify_registered_document(self, publisher_token):
        content = unique_pdf_bytes("VER-1")
        reg = register(publisher_token, content)
        assert reg.status_code == 200, reg.text

        resp = verify(content)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["valid"] is True
        assert body["content_hash"] == reg.json()["content_hash"]
        assert body["blockchain_timestamp"]

    def test_ver2_verify_unregistered_document(self):
        resp = verify(unique_pdf_bytes("VER-2"))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["valid"] is False
        assert body["content_hash"]
        assert "record_id" not in body

    def test_ver3_verify_tampered_document(self, publisher_token):
        content = bytearray(unique_pdf_bytes("VER-3"))
        reg = register(publisher_token, bytes(content))
        assert reg.status_code == 200, reg.text

        content[-1] ^= 0xFF  # flip one byte after registration
        resp = verify(bytes(content))
        assert resp.status_code == 200, resp.text
        assert resp.json()["valid"] is False

    def test_ver4_verify_by_hash_not_registered(self):
        fake_hash = "ab" * 32  # 64 hex chars, not registered
        resp = verify_by_hash(fake_hash)
        assert resp.status_code == 200, resp.text
        assert resp.json()["valid"] is False

    def test_ver5_verify_by_hash_bad_format(self):
        for bad_hash in ["abc123", "z" * 64, "12345"]:
            resp = verify_by_hash(bad_hash)
            assert resp.status_code == 400, f"{bad_hash}: {resp.status_code} {resp.text}"
            assert "64-character" in resp.json()["detail"]

    def test_ver6_verify_by_hash_injection_payload(self):
        for payload in ["' OR '1'='1", "../../etc/passwd", "1' ; DROP TABLE registry_records;--"]:
            resp = requests.get(f"{BASE_URL}/verify/{payload}", timeout=30)
            assert resp.status_code in (400, 404), (
                f"payload={payload!r} got unexpected status {resp.status_code}: {resp.text}"
            )
            if resp.status_code == 400:
                assert "64-character" in resp.json()["detail"]

    @pytest.mark.skip(
        reason="VER-7 needs direct server/Anvil access to desync on-chain data "
        "from the DB record without going through the public API - out of scope "
        "for HTTP-only testing this session (no SSH/server-side actions taken)."
    )
    def test_ver7_onchain_mismatch(self):
        raise NotImplementedError

    @pytest.mark.skip(
        reason="VER-8 needs the ability to take the Anvil/blockchain RPC down, "
        "which requires server access and would affect the shared production "
        "environment - out of scope for HTTP-only testing this session."
    )
    def test_ver8_blockchain_node_down(self):
        raise NotImplementedError
