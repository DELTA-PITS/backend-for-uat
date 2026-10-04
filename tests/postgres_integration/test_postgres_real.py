"""
Real-PostgreSQL integration tests (Phase 2): the explicit migration and the advisory nonce lock,
neither of which can be proven on SQLite. Skipped unless PITS_PG_ADMIN_URL points at a server where
the role may CREATE/DROP databases. Every test works in its own throw-away database, so nothing
here can touch a production database.

Run (inside the backend image, on the docker network that reaches postgres):
    PITS_PG_ADMIN_URL=postgresql+psycopg2://user:pass@postgres:5432/postgres \
      python -m pytest tests/postgres_integration -v
"""
from __future__ import annotations

import multiprocessing
import os
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ADMIN_URL = os.environ.get("PITS_PG_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="PITS_PG_ADMIN_URL not set")

# trustmark.* modules read DATABASE_URL at import time; never import them against a real DB name.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BASE_DIR", os.getcwd())

from trustmark.infra import schema  # noqa: E402
from trustmark.infra.blockchain_connector import BlockchainConnector  # noqa: E402
from trustmark.infra.nonce_lock import signer_lock, signer_lock_key  # noqa: E402

LEGACY_DDL = (
    "CREATE TABLE registry_records (id VARCHAR(36) PRIMARY KEY, content_hash VARCHAR(64) NOT NULL UNIQUE, "
    "transaction_hash VARCHAR(66) NOT NULL UNIQUE, issuer_id VARCHAR(128) NOT NULL, original_filename VARCHAR(255), "
    "content_type VARCHAR(128), created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
)


@pytest.fixture()
def scratch_db():
    name = f"pits_pgtest_{uuid.uuid4().hex[:10]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name)
    engine = create_engine(url)
    try:
        yield SimpleNamespace(name=name, url=url, engine=engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def make_legacy(engine):
    with engine.begin() as conn:
        conn.execute(text(LEGACY_DDL))
        conn.execute(text("INSERT INTO registry_records (id, content_hash, transaction_hash, issuer_id) "
                          "VALUES ('r1', repeat('a',64), repeat('b',64), 'u')"))


def _migrate_in_process(url_str, out):
    engine = create_engine(url_str)
    try:
        out.put(("ok", schema.migrate(engine)))
    except Exception as exc:  # pragma: no cover
        out.put(("error", repr(exc)))


class TestMigrationOnPostgres:
    def test_dialect_is_postgresql(self, scratch_db):
        assert scratch_db.engine.dialect.name == "postgresql"

    def test_check_fails_then_migrate_then_check_passes(self, scratch_db):
        make_legacy(scratch_db.engine)
        with pytest.raises(schema.SchemaOutOfDate, match="chain_id"):
            schema.check_schema(scratch_db.engine)
        added = schema.migrate(scratch_db.engine)
        assert "registry_records.chain_id" in added and "registry_records.confirmed_at" in added
        schema.check_schema(scratch_db.engine)

    def test_legacy_rows_become_legacy_local_chain_31337_and_are_kept(self, scratch_db):
        make_legacy(scratch_db.engine)
        schema.migrate(scratch_db.engine)
        with scratch_db.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT chain_id, anchor_status, anchor_type, submitted_at = created_at AS backfilled, "
                "confirmed_at FROM registry_records WHERE id='r1'")).one()
            total = conn.execute(text("SELECT count(*) FROM registry_records")).scalar()
        assert (row.chain_id, row.anchor_status, row.anchor_type) == (31337, "legacy_local", "calldata-v1")
        assert row.backfilled is True and row.confirmed_at is None and total == 1

    def test_second_run_is_a_no_op(self, scratch_db):
        make_legacy(scratch_db.engine)
        schema.migrate(scratch_db.engine)
        assert schema.migrate(scratch_db.engine) == []

    def test_fresh_empty_database_gets_full_schema(self, scratch_db):
        schema.migrate(scratch_db.engine)
        schema.check_schema(scratch_db.engine)

    def test_column_types_in_postgres_match_the_sql_migration(self, scratch_db):
        make_legacy(scratch_db.engine)
        schema.migrate(scratch_db.engine)
        with scratch_db.engine.connect() as conn:
            types = dict(conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='registry_records'")).all())
        assert types["chain_id"] == "integer"
        assert types["block_number"] == "bigint"
        assert types["submitted_at"] == "timestamp with time zone"
        assert types["anchor_status"] == "character varying"

    def test_three_concurrent_migrators_all_succeed(self, scratch_db):
        make_legacy(scratch_db.engine)
        url_str = scratch_db.url.render_as_string(hide_password=False)
        out = multiprocessing.Queue()
        procs = [multiprocessing.Process(target=_migrate_in_process, args=(url_str, out)) for _ in range(3)]
        [p.start() for p in procs]
        [p.join(60) for p in procs]
        results = [out.get(timeout=5) for _ in procs]
        assert [r[0] for r in results] == ["ok", "ok", "ok"], results
        schema.check_schema(scratch_db.engine)
        with scratch_db.engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM registry_records")).scalar() == 1

    def test_failed_migration_leaves_nothing_half_applied(self, scratch_db, monkeypatch):
        make_legacy(scratch_db.engine)
        good = schema.migration_statements()
        monkeypatch.setattr(schema, "migration_statements", lambda: good[:3] + ["ALTER TABLE no_such_table ADD COLUMN x INT"])
        with pytest.raises(Exception):
            schema.migrate(scratch_db.engine)
        monkeypatch.undo()
        missing = schema.missing_schema(scratch_db.engine)
        assert "registry_records.chain_id" in missing  # the first statements were rolled back too


class TestAdvisoryLockOnPostgres:
    ADDRESS = "0x" + "ab" * 20

    def test_lock_is_a_real_pg_advisory_lock_visible_in_pg_locks(self, scratch_db):
        key = signer_lock_key(11155111, self.ADDRESS)
        with signer_lock(11155111, self.ADDRESS, engine=scratch_db.engine):
            with scratch_db.engine.connect() as other:
                held = other.execute(text(
                    "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted "
                    "AND ((classid::bigint << 32) | objid::bigint) = :key"), {"key": key}).scalar()
            assert held == 1
        with scratch_db.engine.connect() as other:
            assert other.execute(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory'")).scalar() == 0

    def test_second_holder_for_same_signer_waits_for_release(self, scratch_db):
        events = []

        def holder():
            with signer_lock(1, self.ADDRESS, engine=scratch_db.engine):
                events.append(("a-in", time.monotonic()))
                time.sleep(1.0)
                events.append(("a-out", time.monotonic()))

        def waiter():
            time.sleep(0.2)
            with signer_lock(1, self.ADDRESS, engine=scratch_db.engine):
                events.append(("b-in", time.monotonic()))

        threads = [threading.Thread(target=holder), threading.Thread(target=waiter)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        order = [name for name, _ in sorted(events, key=lambda e: e[1])]
        assert order == ["a-in", "a-out", "b-in"]

    def test_different_signers_do_not_block_each_other(self, scratch_db):
        started = time.monotonic()
        done = []

        def run(addr):
            with signer_lock(1, addr, engine=scratch_db.engine):
                time.sleep(0.6)
            done.append(addr)

        threads = [threading.Thread(target=run, args=("0x" + c * 40,)) for c in "12"]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert time.monotonic() - started < 1.1 and len(done) == 2

    def test_concurrent_anchor_submissions_get_distinct_nonces_through_postgres_lock(self, scratch_db):
        from eth_account import Account
        from hexbytes import HexBytes

        account = Account.create()

        class Eth:
            chain_id = 11155111
            block_number = 100

            def __init__(self):
                self.next_nonce = 0
                self.nonces = []

            max_priority_fee = 10**9

            def get_block(self, ident):
                return {"baseFeePerGas": 10**9}

            def estimate_gas(self, tx):
                return 22_000

            def get_balance(self, address):
                return 10**18

            def get_transaction_count(self, address, block="latest"):
                value = self.next_nonce
                time.sleep(0.05)  # widen the read -> broadcast race window
                return value

            def send_raw_transaction(self, raw):
                self.nonces.append(Account.recover_transaction(raw) and self._nonce_of(raw))
                self.next_nonce += 1
                return HexBytes("0x" + "cd" * 32)

            @staticmethod
            def _nonce_of(raw):
                from eth_account.typed_transactions import TypedTransaction

                return TypedTransaction.from_bytes(HexBytes(raw)).as_dict()["nonce"]

        eth = Eth()
        connector = BlockchainConnector("http://unused", account.key.hex(), chain_id=11155111,
                                        signer_lock=lambda c, a: signer_lock(c, a, engine=scratch_db.engine))
        connector.w3 = SimpleNamespace(eth=eth)
        errors = []

        def run():
            try:
                connector.submit_anchor("ab" * 32)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors
        assert sorted(eth.nonces) == list(range(8))

    def test_without_the_lock_the_same_race_does_produce_duplicate_nonces(self, scratch_db):
        """Control experiment: proves the harness really opens the race, so the passing test above is meaningful."""
        from eth_account import Account
        from hexbytes import HexBytes

        account = Account.create()

        class Eth:
            chain_id = 11155111
            block_number = 100
            max_priority_fee = 10**9
            next_nonce = 0
            sent = []

            def get_block(self, ident):
                return {"baseFeePerGas": 10**9}

            def estimate_gas(self, tx):
                return 22_000

            def get_balance(self, address):
                return 10**18

            def get_transaction_count(self, address, block="latest"):
                value = self.next_nonce
                time.sleep(0.05)
                return value

            def send_raw_transaction(self, raw):
                from eth_account.typed_transactions import TypedTransaction

                self.sent.append(TypedTransaction.from_bytes(HexBytes(raw)).as_dict()["nonce"])
                self.next_nonce += 1
                return HexBytes("0x" + "cd" * 32)

        eth = Eth()
        eth.sent = []
        connector = BlockchainConnector("http://unused", account.key.hex(), chain_id=11155111, signer_lock=None)
        connector.w3 = SimpleNamespace(eth=eth)
        threads = [threading.Thread(target=lambda: connector.submit_anchor("ab" * 32)) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(set(eth.sent)) < 8


class TestNonceStoreOnPostgres:
    ADDRESS = "0x" + "cd" * 20

    def test_roundtrip_ttl_and_timestamptz_on_postgres(self, scratch_db):
        from trustmark.infra.nonce_lock import DbNonceStore

        schema.migrate(scratch_db.engine)
        store = DbNonceStore(scratch_db.engine)
        assert store.get_floor(11155111, self.ADDRESS, 120) is None
        store.put_next(11155111, self.ADDRESS, 41)
        assert store.get_floor(11155111, self.ADDRESS, 120) == 41
        store.put_next(11155111, self.ADDRESS, 42)
        assert store.get_floor(11155111, self.ADDRESS, 120) == 42
        with scratch_db.engine.begin() as conn:
            conn.execute(text("UPDATE anchor_signer_nonces SET updated_at = now() - interval '10 minutes'"))
        assert store.get_floor(11155111, self.ADDRESS, 120) is None

    def test_stale_rpc_plus_postgres_lock_and_store_give_distinct_nonces(self, scratch_db):
        from eth_account import Account
        from eth_account.typed_transactions import TypedTransaction
        from hexbytes import HexBytes
        from trustmark.infra.nonce_lock import DbNonceStore

        schema.migrate(scratch_db.engine)
        account = Account.create()

        class Eth:
            chain_id = 11155111
            block_number = 1
            max_priority_fee = 10**9
            sent = []

            def get_block(self, ident):
                return {"baseFeePerGas": 10**9}

            def estimate_gas(self, tx):
                return 22_000

            def get_balance(self, address):
                return 10**18

            def get_transaction_count(self, address, block="latest"):
                return 100  # stale: a lagging RPC node that never saw our earlier broadcasts

            def send_raw_transaction(self, raw):
                self.sent.append(TypedTransaction.from_bytes(HexBytes(raw)).as_dict()["nonce"])
                return HexBytes("0x" + "cd" * 32)

        eth = Eth()
        eth.sent = []
        connector = BlockchainConnector(
            "http://unused", account.key.hex(), chain_id=11155111,
            signer_lock=lambda c, a: signer_lock(c, a, engine=scratch_db.engine),
            nonce_store=DbNonceStore(scratch_db.engine))
        connector.w3 = SimpleNamespace(eth=eth)
        threads = [threading.Thread(target=lambda: connector.submit_anchor("ab" * 32)) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(eth.sent) == list(range(100, 108))
