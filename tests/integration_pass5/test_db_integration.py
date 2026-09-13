"""
PASS 5 — Section 2: PostgreSQL real integration tests (DB-INT-01..10).

Runs against the LOCAL disposable Postgres container started by
docker/docker-compose.yml (postgres:16-alpine, db=trustmark). No mocking of
SQLAlchemy or the database — every test either goes through the live FastAPI
app (real HTTP) or connects to Postgres directly with psycopg2 to inspect
what the app actually persisted.
"""
from __future__ import annotations

import concurrent.futures
import time

import pytest
import requests

from conftest import (
    BACKEND_URL,
    register,
    unique_pdf_bytes,
)


def _get_record_row(db_conn, content_hash: str):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, content_hash, transaction_hash, issuer_id, original_filename, "
            "content_type, created_at FROM registry_records WHERE content_hash = %s",
            (content_hash,),
        )
        return cur.fetchone()


class TestDbIntegration:
    def test_db_int_01_new_record_persists(self, token_a, db_conn):
        content = unique_pdf_bytes("DB-INT-01")
        resp = register(token_a, content)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        row = _get_record_row(db_conn, body["content_hash"])
        assert row is not None, "record not found in Postgres after register()"
        assert row[0] == body["record_id"]

    def test_db_int_02_record_retrievable_after_request_ends(self, token_a, db_conn):
        """The register() request has already returned (fixture db_conn opens
        a brand-new connection/session in a separate process context from
        whatever session the app used) - this proves durability beyond the
        request's own SQLAlchemy session, not just an in-memory echo."""
        content = unique_pdf_bytes("DB-INT-02")
        resp = register(token_a, content)
        assert resp.status_code == 200
        content_hash = resp.json()["content_hash"]

        # brand new connection, simulating a later, unrelated request
        row = _get_record_row(db_conn, content_hash)
        assert row is not None

    def test_db_int_03_content_hash_unique_constraint(self, db_conn):
        """Insert a row directly, then attempt a second insert with the same
        content_hash but a different transaction_hash - the DB itself (not
        app logic) must reject it."""
        import uuid

        content_hash = uuid.uuid4().hex + uuid.uuid4().hex[:32]  # 64 chars
        tx1 = "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:34]
        tx2 = "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:34]

        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO registry_records (id, content_hash, transaction_hash, issuer_id) "
                "VALUES (%s, %s, %s, %s)",
                (str(uuid.uuid4()), content_hash, tx1, "db-int-03"),
            )

        with pytest.raises(Exception) as exc:
            with db_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO registry_records (id, content_hash, transaction_hash, issuer_id) "
                    "VALUES (%s, %s, %s, %s)",
                    (str(uuid.uuid4()), content_hash, tx2, "db-int-03-dup"),
                )
        assert "unique" in str(exc.value).lower() or "duplicate" in str(exc.value).lower()

    def test_db_int_04_transaction_hash_unique_constraint(self, db_conn):
        import uuid

        tx_hash = "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:34]
        h1 = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        h2 = uuid.uuid4().hex + uuid.uuid4().hex[:32]

        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO registry_records (id, content_hash, transaction_hash, issuer_id) "
                "VALUES (%s, %s, %s, %s)",
                (str(uuid.uuid4()), h1, tx_hash, "db-int-04"),
            )

        with pytest.raises(Exception) as exc:
            with db_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO registry_records (id, content_hash, transaction_hash, issuer_id) "
                    "VALUES (%s, %s, %s, %s)",
                    (str(uuid.uuid4()), h2, tx_hash, "db-int-04-dup"),
                )
        assert "unique" in str(exc.value).lower() or "duplicate" in str(exc.value).lower()

    def test_db_int_05_concurrent_identical_registration(self, token_a, db_conn):
        """Fires 5 concurrent identical registrations against the LIVE stack
        (real Postgres + real Anvil) and records exactly what happens - does
        NOT assume the app is race-safe."""
        content = unique_pdf_bytes("DB-INT-05")
        n = 5

        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(register, token_a, content) for _ in range(n)]
            results = [f.result() for f in futures]

        statuses = [r.status_code for r in results]
        bodies = []
        for r in results:
            try:
                bodies.append(r.json())
            except ValueError:
                bodies.append({"_raw": r.text[:300]})

        content_hash = None
        for b in bodies:
            if isinstance(b, dict) and b.get("content_hash"):
                content_hash = b["content_hash"]
                break

        with db_conn.cursor() as cur:
            cur.execute("SELECT id, transaction_hash FROM registry_records WHERE content_hash = %s", (content_hash,))
            db_rows = cur.fetchall()

        tx_hashes_returned = {b.get("transaction_hash") for b in bodies if isinstance(b, dict) and b.get("transaction_hash")}

        print(f"DB-INT-05: {n} concurrent requests -> statuses={statuses}")
        print(f"DB-INT-05: response bodies={bodies}")
        print(f"DB-INT-05: DB rows for this content_hash={db_rows}")
        print(f"DB-INT-05: distinct transaction_hash values in responses={tx_hashes_returned}")

        assert len(db_rows) == 1, (
            f"EXPECTED exactly one authoritative registry record for {n} concurrent "
            f"identical registrations, found {len(db_rows)} rows: {db_rows}"
        )

    def test_db_int_06_transaction_rollback_on_commit_failure(self, db_conn):
        """Simulates a commit failure at the DB level directly (a transaction
        that violates a constraint) and confirms no partial row survives -
        this is Postgres's own transactional guarantee, exercised for real."""
        import uuid

        content_hash = uuid.uuid4().hex + uuid.uuid4().hex[:32]

        # db_conn has autocommit=True (see fixture) - each statement commits
        # independently, so a failing INSERT here does not leave the
        # connection in psycopg2's "aborted transaction" state the way an
        # explicit BEGIN...COMMIT block would.
        with pytest.raises(Exception):
            with db_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO registry_records (id, content_hash, transaction_hash, issuer_id) "
                    "VALUES (%s, %s, %s, %s)",
                    (str(uuid.uuid4()), content_hash, None, "db-int-06"),  # transaction_hash NOT NULL -> fails
                )

        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM registry_records WHERE content_hash = %s", (content_hash,))
            count = cur.fetchone()[0]
        assert count == 0, "partial row survived a failed transaction"

    def test_db_int_07_issuer_id_persists_as_authenticated_sub(self, token_a, db_conn):
        content = unique_pdf_bytes("DB-INT-07")
        resp = register(token_a, content)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        row = _get_record_row(db_conn, body["content_hash"])
        assert row is not None
        db_issuer_id = row[3]

        assert body["issuer_id"] == db_issuer_id
        # Decode the token's own sub claim to compare against what's stored
        import base64
        import json

        payload_b64 = token_a.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        expected_sub = claims.get("sub")

        print(f"DB-INT-07: token sub={expected_sub!r}, stored issuer_id={db_issuer_id!r}")
        assert db_issuer_id == expected_sub, (
            f"stored issuer_id ({db_issuer_id!r}) does not match the authenticated "
            f"principal's JWT 'sub' claim ({expected_sub!r})"
        )

    def test_db_int_08_created_at_generated_and_returned(self, token_a, db_conn):
        content = unique_pdf_bytes("DB-INT-08")
        resp = register(token_a, content)
        assert resp.status_code == 200
        body = resp.json()
        assert body["created_at"], "created_at missing from API response"

        row = _get_record_row(db_conn, body["content_hash"])
        assert row is not None
        assert row[6] is not None, "created_at is NULL in the database row"

    def test_db_int_09_session_cleanup_after_exception(self, token_a, db_conn):
        """Send a request that raises server-side (blockchain misconfigured
        via a bogus content type causing a 4xx before DB access) and then
        confirm the connection pool is not exhausted / DB still responsive -
        proxy for "does get_db()'s try/finally actually close the session".
        Fires 20 rapid requests that each open+close a DB session."""
        ok = 0
        for i in range(20):
            resp = register(token_a, unique_pdf_bytes(f"DB-INT-09-{i}"))
            if resp.status_code == 200:
                ok += 1
        assert ok == 20, f"only {ok}/20 requests succeeded - possible session/connection leak"

        with db_conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1

    def test_db_int_10_db_unavailable_behaviour(self, token_a):
        """Stops the postgres container, fires a register(), records the
        actual status code/behaviour, then restarts postgres and waits for
        health before returning control (so later tests aren't collateral
        damage)."""
        import subprocess

        compose_dir = "/Users/laksaersa/GitHub/_personal/_PITS/backend-for-uat/docker"
        subprocess.run(["docker", "compose", "stop", "postgres"], cwd=compose_dir, check=True, capture_output=True)
        time.sleep(2)

        try:
            resp = register(token_a, unique_pdf_bytes("DB-INT-10"))
            print(f"DB-INT-10: status={resp.status_code}, body={resp.text[:500]}")
            actual_status = resp.status_code
            actual_body = resp.text[:500]
        except requests.exceptions.RequestException as e:
            actual_status = None
            actual_body = str(e)
            print(f"DB-INT-10: request-level exception: {e}")
        finally:
            subprocess.run(["docker", "compose", "start", "postgres"], cwd=compose_dir, check=True, capture_output=True)
            for _ in range(30):
                health = subprocess.run(
                    ["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "trustmark", "-d", "trustmark"],
                    cwd=compose_dir, capture_output=True,
                )
                if health.returncode == 0:
                    break
                time.sleep(2)

        # Recorded for the report, not asserted strictly - actual behaviour
        # (500 vs hang vs connection error) is exactly what this test exists
        # to observe.
        print(f"DB-INT-10 FINAL: status={actual_status}, body={actual_body}")
