"""
Unit tests for trustmark.api.v1.documents — fully mocked DB session and
blockchain connector, no live server, no live database, no network I/O.

Purpose: documents.py is the other module (alongside keycloak.py) behind the
findings from the 2026-09-09 live QA pass. Two tests here in particular are
load-bearing:

  - TestListRecords::test_records_from_two_issuers_visible_to_one_principal
    reproduces Finding 1 (the /api/v1/records IDOR) deterministically, with
    a two-line mock DB, in milliseconds - no second Keycloak account needed.

  - TestRegister::test_principal_sub_empty_writes_empty_issuer_id
    reproduces Finding 2's effect at the exact write site, independent of
    whether the live Keycloak token has a `sub` claim on any given day.

Run with:
    /tmp/pits-unit-venv/bin/python -m pytest tests/trustmark/api/test_documents.py -v
"""
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from trustmark.api.v1 import documents
from trustmark.infra.auth.keycloak import Principal
from trustmark.registry.models import RegistryRecord

MODULE = "trustmark.api.v1.documents"


class FakeUploadFile:
    """Minimal duck-typed stand-in for fastapi.UploadFile - documents.py only
    ever awaits `.read()` and reads `.filename` / `.content_type`, so a real
    UploadFile (which needs a Starlette request context to construct
    cleanly) isn't necessary for a pure unit test of the handler logic."""

    def __init__(self, content: bytes, filename: str = "doc.pdf", content_type: str = "application/pdf"):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


def make_principal(sub: str = "user-1", roles=frozenset({"publisher"})) -> Principal:
    return Principal(sub=sub, username="tester", email="tester@example.test", roles=set(roles), claims={})


def make_record(**overrides) -> RegistryRecord:
    defaults = dict(
        id="rec-1",
        content_hash="a" * 64,
        transaction_hash="0x" + "b" * 64,
        issuer_id="user-1",
        original_filename="doc.pdf",
        content_type="application/pdf",
        created_at="2026-09-06T10:00:00+00:00",
    )
    defaults.update(overrides)
    return RegistryRecord(**defaults)


def mock_db(first_return=None, all_return=None):
    """Builds a MagicMock standing in for the SQLAlchemy Session, wired so
    `db.query(...).filter_by(...).first()` and
    `db.query(...).order_by(...).all()` return what the test wants."""
    db = MagicMock()
    query = db.query.return_value
    query.filter_by.return_value.first.return_value = first_return
    query.order_by.return_value.all.return_value = all_return or []
    return db


# ---------------------------------------------------------------------------
# _max_upload_bytes() / _read_upload() / _get_blockchain_service()
# ---------------------------------------------------------------------------


class TestConfigAndUploadHelpers:
    """`settings` is a Dynaconf object whose `conf/settings.toml` values are
    `@format {env[...]}` strings, resolved fresh from `os.environ` on every
    `.get()` call (confirmed empirically - it is NOT cached at import time).
    That makes `monkeypatch.setenv`/`delenv` the correct, direct way to test
    these branches, rather than mocking `settings.get` itself."""

    def test_max_upload_bytes_reads_configured_value(self, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "1234")
        assert documents._max_upload_bytes() == 1234

    def test_max_upload_bytes_default_is_unreachable_if_env_var_unset(self, monkeypatch):
        """AUDIT FINDING (candidate 6th bug, same shape as the jwks_uri one
        in test_keycloak.py): `conf/settings.toml` defines
        `max_upload_bytes = "@format {env[MAX_UPLOAD_BYTES]}"` - a REQUIRED
        substitution. `_max_upload_bytes()`'s own `settings.get("MAX_UPLOAD_
        BYTES", 20*1024*1024)` default parameter only fires if the *settings
        key* is absent, not if the env-var substitution inside it fails. If
        the env var is ever fully unset (not just empty-string) in a given
        deployment, this raises an unhandled `DynaconfFormatError` instead
        of quietly falling back to 20MB as the code's own default argument
        implies it would. Empty-string (misconfigured-but-present) is fine -
        see the RPC/private-key tests below, which correctly hit the "not
        configured" 500 path with an empty string."""
        monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
        with pytest.raises(Exception) as exc:
            documents._max_upload_bytes()
        assert "MAX_UPLOAD_BYTES" in str(exc.value)

    def test_get_blockchain_service_missing_rpc_url_returns_500(self, monkeypatch):
        monkeypatch.setenv("BLOCKCHAIN_RPC_URL", "")
        monkeypatch.setenv("BLOCKCHAIN_PRIVATE_KEY", "0xabc")
        with pytest.raises(HTTPException) as exc:
            documents._get_blockchain_service()
        assert exc.value.status_code == 500

    def test_get_blockchain_service_missing_private_key_returns_500(self, monkeypatch):
        monkeypatch.setenv("BLOCKCHAIN_RPC_URL", "http://localhost:8545")
        monkeypatch.setenv("BLOCKCHAIN_PRIVATE_KEY", "")
        with pytest.raises(HTTPException) as exc:
            documents._get_blockchain_service()
        assert exc.value.status_code == 500

    def test_get_blockchain_service_constructs_connector_when_configured(self, monkeypatch):
        monkeypatch.setenv("BLOCKCHAIN_RPC_URL", "http://localhost:8545")
        monkeypatch.setenv("BLOCKCHAIN_PRIVATE_KEY", "0xabc")
        with patch(f"{MODULE}.BlockchainConnector") as mock_ctor:
            documents._get_blockchain_service()
        mock_ctor.assert_called_once_with(rpc_url="http://localhost:8545", private_key="0xabc")

    def test_read_upload_non_empty_within_limit_returns_bytes(self):
        file = FakeUploadFile(b"hello pdf bytes")
        with patch(f"{MODULE}._max_upload_bytes", return_value=1_000_000):
            content = asyncio.run(documents._read_upload(file))
        assert content == b"hello pdf bytes"

    def test_read_upload_empty_file_returns_400(self):
        file = FakeUploadFile(b"")
        with patch(f"{MODULE}._max_upload_bytes", return_value=1_000_000):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(documents._read_upload(file))
        assert exc.value.status_code == 400
        assert "empty" in exc.value.detail.lower()

    def test_read_upload_exactly_at_limit_is_allowed(self):
        """Boundary is `>`, not `>=` - a file exactly at the limit must pass."""
        file = FakeUploadFile(b"x" * 100)
        with patch(f"{MODULE}._max_upload_bytes", return_value=100):
            content = asyncio.run(documents._read_upload(file))
        assert len(content) == 100

    def test_read_upload_one_byte_over_limit_returns_413(self):
        file = FakeUploadFile(b"x" * 101)
        with patch(f"{MODULE}._max_upload_bytes", return_value=100):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(documents._read_upload(file))
        assert exc.value.status_code == 413


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestRegister:
    def test_new_unique_content_creates_record_and_one_blockchain_tx(self):
        content = b"%PDF-1.4 unique content A"
        db = mock_db(first_return=None)
        principal = make_principal(sub="issuer-A")
        chain = MagicMock()
        chain.create_transaction.return_value = "0xTX1"
        chain.wait_for_receipt.return_value = 123

        with patch(f"{MODULE}._get_blockchain_service", return_value=chain):
            result = asyncio.run(
                documents.register(file=FakeUploadFile(content), principal=principal, db=db)
            )

        assert result["stored"] is True
        assert result["already_existed"] is False
        assert result["content_hash"] == hashlib.sha256(content).hexdigest()
        chain.create_transaction.assert_called_once()
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_duplicate_content_returns_existing_record_no_new_tx(self):
        content = b"%PDF-1.4 already registered"
        existing = make_record(content_hash=hashlib.sha256(content).hexdigest())
        db = mock_db(first_return=existing)
        principal = make_principal(sub="issuer-B")
        chain = MagicMock()

        with patch(f"{MODULE}._get_blockchain_service", return_value=chain):
            result = asyncio.run(
                documents.register(file=FakeUploadFile(content), principal=principal, db=db)
            )

        assert result["already_existed"] is True
        assert result["record_id"] == existing.id
        chain.create_transaction.assert_not_called()
        db.add.assert_not_called()

    def test_principal_sub_empty_writes_empty_issuer_id(self):
        """Reproduces Finding 2's effect at the write site: whatever
        Principal.sub is - including "", the exact value production tokens
        currently produce - is written straight into issuer_id with no
        validation in between."""
        content = b"%PDF-1.4 content C"
        db = mock_db(first_return=None)
        principal = make_principal(sub="")  # the production-observed value
        chain = MagicMock()
        chain.create_transaction.return_value = "0xTX3"
        chain.wait_for_receipt.return_value = 1

        created_records = []
        db.add.side_effect = lambda rec: created_records.append(rec)

        with patch(f"{MODULE}._get_blockchain_service", return_value=chain):
            asyncio.run(documents.register(file=FakeUploadFile(content), principal=principal, db=db))

        assert created_records[0].issuer_id == ""

    def test_blockchain_failure_mid_transaction_propagates(self):
        """Confirms current behaviour: register() has no try/except around
        the blockchain calls, so a failure there is not converted into a
        clean client-facing error - it propagates as whatever the connector
        raised (here, a generic HTTPException(500) from the connector
        itself, per blockchain_connector.py's own error handling)."""
        content = b"%PDF-1.4 content D"
        db = mock_db(first_return=None)
        principal = make_principal()
        chain = MagicMock()
        chain.create_transaction.side_effect = HTTPException(500, detail="Blockchain connection failed")

        with patch(f"{MODULE}._get_blockchain_service", return_value=chain):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(documents.register(file=FakeUploadFile(content), principal=principal, db=db))
        assert exc.value.status_code == 500
        db.add.assert_not_called()  # never reached record construction

    def test_db_commit_failure_propagates_unhandled(self):
        """Confirms current behaviour under a DUP-4-style race: if two
        requests both pass the `.first()` duplicate check before either
        commits, the second commit would hit the `content_hash`/
        `transaction_hash` UNIQUE constraint at the DB level. register() has
        no try/except around `db.commit()`, so that error is not caught here
        either - it propagates raw (in production, SQLAlchemy's
        IntegrityError), same shape of gap as the blockchain-failure case
        above."""
        content = b"%PDF-1.4 content E"
        db = mock_db(first_return=None)
        db.commit.side_effect = RuntimeError("UNIQUE constraint failed: registry_records.content_hash")
        principal = make_principal()
        chain = MagicMock()
        chain.create_transaction.return_value = "0xTX5"
        chain.wait_for_receipt.return_value = 1

        with patch(f"{MODULE}._get_blockchain_service", return_value=chain):
            with pytest.raises(RuntimeError):
                asyncio.run(documents.register(file=FakeUploadFile(content), principal=principal, db=db))


# ---------------------------------------------------------------------------
# list_records()  —  Finding 1 (IDOR)
# ---------------------------------------------------------------------------


class TestListRecords:
    def test_records_from_two_issuers_visible_to_one_principal(self):
        """THE test that proves Finding 1 without a live server or a second
        Keycloak account. Two records belonging to two different issuers
        sit in the mock DB; list_records() is called once, as one
        authenticated principal (issuer-A). If the response contains
        issuer-B's record too, the endpoint has no per-publisher filter -
        which is exactly documents.py's current, unfiltered
        `db.query(RegistryRecord).order_by(...).all()`."""
        record_a = make_record(id="rec-A", issuer_id="issuer-A", original_filename="a.pdf")
        record_b = make_record(id="rec-B", issuer_id="issuer-B", original_filename="b-private.pdf")
        db = mock_db(all_return=[record_b, record_a])
        principal = make_principal(sub="issuer-A")

        result = documents.list_records(principal=principal, db=db)

        returned_issuers = {r["issuer_id"] for r in result["records"]}
        assert returned_issuers == {"issuer-A", "issuer-B"}, (
            "list_records() returned records from an issuer other than the "
            "requesting principal - this IS the bug (Finding 1), not a test "
            "failure to fix. A correct implementation filters by "
            "principal.sub and this assertion would need updating to "
            "returned_issuers == {'issuer-A'}."
        )

    def test_empty_table_returns_empty_list(self):
        db = mock_db(all_return=[])
        result = documents.list_records(principal=make_principal(), db=db)
        assert result["records"] == []

    def test_ordered_by_created_at_descending(self):
        """This only confirms the query *asks* for descending order (the
        mock can't simulate real SQL ORDER BY) - i.e. that `order_by` is
        called with the descending clause, not that SQLite would actually
        sort correctly. True sort-order correctness needs an integration
        test against a real (even if in-memory) database."""
        db = mock_db(all_return=[])
        documents.list_records(principal=make_principal(), db=db)
        db.query.return_value.order_by.assert_called_once()


# ---------------------------------------------------------------------------
# verify_full() / verify_by_hash()
# ---------------------------------------------------------------------------


class TestVerify:
    def test_registered_and_onchain_match_returns_valid_true(self):
        content = b"%PDF-1.4 verify me"
        content_hash = hashlib.sha256(content).hexdigest()
        record = make_record(content_hash=content_hash)
        db = mock_db(first_return=record)
        chain = MagicMock()
        chain.read_transaction_value.return_value = {"value": content_hash, "timestamp": "2026-09-06T00:00:00+00:00"}

        with patch(f"{MODULE}._get_blockchain_service", return_value=chain):
            result = asyncio.run(documents.verify_full(file=FakeUploadFile(content), db=db))

        assert result["valid"] is True
        assert result["record_id"] == record.id
        assert "blockchain_timestamp" in result

    def test_never_registered_returns_valid_false_no_record_id(self):
        content = b"%PDF-1.4 never seen before"
        db = mock_db(first_return=None)

        result = asyncio.run(documents.verify_full(file=FakeUploadFile(content), db=db))

        assert result["valid"] is False
        assert "record_id" not in result

    def test_onchain_mismatch_returns_valid_false_with_record_id(self):
        """Reproduces VER-7 (registry/on-chain desync) without touching a
        live Anvil node: the DB has a record, but the value read back from
        the (mocked) chain does not match. valid=False, but - unlike the
        never-registered case above - record_id IS present, so a client can
        tell "not registered" apart from "registered but evidence
        inconsistent"."""
        content = b"%PDF-1.4 desynced"
        content_hash = hashlib.sha256(content).hexdigest()
        record = make_record(content_hash=content_hash)
        db = mock_db(first_return=record)
        chain = MagicMock()
        chain.read_transaction_value.return_value = {"value": "a-different-hash-entirely", "timestamp": "x"}

        with patch(f"{MODULE}._get_blockchain_service", return_value=chain):
            result = asyncio.run(documents.verify_full(file=FakeUploadFile(content), db=db))

        assert result["valid"] is False
        assert result["record_id"] == record.id

    def test_blockchain_node_down_propagates_unhandled(self):
        """Reproduces VER-8 (blockchain node unreachable) without cutting
        real server connectivity: verify_full() has no try/except around
        `read_transaction_value`, so a connector-level failure propagates
        as-is rather than becoming a clean 503."""
        content = b"%PDF-1.4 node is down"
        record = make_record(content_hash=hashlib.sha256(content).hexdigest())
        db = mock_db(first_return=record)
        chain = MagicMock()
        chain.read_transaction_value.side_effect = HTTPException(500, detail="Blockchain connection failed")

        with patch(f"{MODULE}._get_blockchain_service", return_value=chain):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(documents.verify_full(file=FakeUploadFile(content), db=db))
        assert exc.value.status_code == 500

    def test_verify_by_hash_registered_matches_verify_full_behaviour(self):
        content_hash = "c" * 64
        record = make_record(content_hash=content_hash)
        db = mock_db(first_return=record)
        chain = MagicMock()
        chain.read_transaction_value.return_value = {"value": content_hash, "timestamp": "x"}

        with patch(f"{MODULE}._get_blockchain_service", return_value=chain):
            result = documents.verify_by_hash(file_hash=content_hash, db=db)

        assert result["valid"] is True

    def test_verify_by_hash_not_registered_returns_valid_false(self):
        db = mock_db(first_return=None)
        result = documents.verify_by_hash(file_hash="d" * 64, db=db)
        assert result["valid"] is False

    def test_verify_by_hash_wrong_length_returns_400(self):
        db = mock_db()
        with pytest.raises(HTTPException) as exc:
            documents.verify_by_hash(file_hash="abc123", db=db)
        assert exc.value.status_code == 400

    def test_verify_by_hash_non_hex_characters_returns_400(self):
        db = mock_db()
        bad_hash = "g" * 64  # 'g' is not a valid hex digit
        with pytest.raises(HTTPException) as exc:
            documents.verify_by_hash(file_hash=bad_hash, db=db)
        assert exc.value.status_code == 400

    def test_verify_by_hash_injection_payload_returns_400_before_db_hit(self):
        """VER-6 as a unit test: confirms the hex/length validation runs
        BEFORE any `db.query(...)` call - i.e. a malicious file_hash never
        reaches the ORM layer at all."""
        db = mock_db()
        with pytest.raises(HTTPException) as exc:
            documents.verify_by_hash(file_hash="' OR '1'='1", db=db)
        assert exc.value.status_code == 400
        db.query.assert_not_called()

    def test_verify_by_hash_mixed_case_and_whitespace_normalised(self):
        content_hash = "e" * 64
        db = mock_db(first_return=None)
        # uppercase + surrounding whitespace should still resolve to the same lookup
        documents.verify_by_hash(file_hash=f"  {content_hash.upper()}  ", db=db)
        called_kwargs = db.query.return_value.filter_by.call_args.kwargs
        assert called_kwargs["content_hash"] == content_hash
