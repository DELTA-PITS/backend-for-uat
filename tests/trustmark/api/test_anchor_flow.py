"""
Register / verify / confirmation flow against a REAL (in-memory SQLite) session, with the
blockchain connector replaced by a double. Covers what the mock-session tests in
test_documents.py cannot: chain_id + status persistence, pending -> confirmed transitions,
legacy Anvil rows under a Sepolia configuration, and the additive schema upgrade.

Run with:
    python -m pytest tests/trustmark/api/test_anchor_flow.py
"""
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trustmark.api.v1 import documents
from trustmark.infra.auth.keycloak import Principal
from trustmark.infra.blockchain_connector import (
    AnchorPending,
    AnchorReceipt,
    AnchorSubmission,
    AnchorTransactionNotFound,
    ReceiptTimeout,
    UnexpectedPayload,
)
from trustmark.infra.db import Base
from trustmark.infra.schema import migrate
from trustmark.registry.models import RegistryRecord

MODULE = "trustmark.api.v1.documents"
SEPOLIA = 11155111
WALLET = "0x" + "a" * 40


class Upload:
    def __init__(self, content: bytes, filename="doc.pdf"):
        self._content, self.filename, self.content_type = content, filename, "application/pdf"

    async def read(self):
        return self._content


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def sepolia_env(monkeypatch):
    monkeypatch.setenv("BLOCKCHAIN_RPC_URL", "http://rpc.invalid")
    monkeypatch.setenv("BLOCKCHAIN_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("BLOCKCHAIN_CHAIN_ID", str(SEPOLIA))
    monkeypatch.setenv("BLOCKCHAIN_NETWORK_NAME", "Ethereum Sepolia")
    monkeypatch.setenv("BLOCKCHAIN_EXPLORER_URL", "https://sepolia.etherscan.io")


def receipt(success=True, block=500, confirmations=3):
    return AnchorReceipt(success=success, block_number=block, block_hash="0x" + "7" * 64, gas_used=22_000,
                         effective_gas_price=2 * 10**9, confirmations=confirmations)


def chain(chain_id=SEPOLIA, wait=None, **attrs):
    c = MagicMock()
    c.chain_id, c.confirmations = chain_id, 1
    c.submit_anchor.return_value = AnchorSubmission(tx_hash="ab" * 32, wallet=WALLET, nonce=1, chain_id=chain_id)
    c.wait_for_anchor_receipt.return_value = receipt() if wait is None else wait
    for key, value in attrs.items():
        setattr(c, key, value)
    return c


def principal(sub="issuer-1"):
    return Principal(sub=sub, username="u", email="u@example.test", roles={"publisher"}, claims={})


def do_register(db, connector, content=b"%PDF-1.4 a"):
    with patch(f"{MODULE}._get_blockchain_service", return_value=connector):
        return asyncio.run(documents.register(file=Upload(content), principal=principal(), db=db))


def do_verify(db, connector, content=b"%PDF-1.4 a"):
    with patch(f"{MODULE}._get_blockchain_service", return_value=connector):
        return asyncio.run(documents.verify_full(file=Upload(content), db=db))


def onchain(content=b"%PDF-1.4 a", **overrides):
    return {"value": hashlib.sha256(content).hexdigest(), "timestamp": "2026-10-04 00:00:00+00:00",
            "from": WALLET, "block_number": 500, **overrides}


class TestRegisterPersistence:
    def test_confirmed_registration_stores_chain_and_receipt_metadata(self, db):
        result = do_register(db, chain())
        row = db.query(RegistryRecord).one()
        assert (row.chain_id, row.anchor_status, row.anchor_type) == (SEPOLIA, "confirmed", "calldata-v1")
        assert row.block_number == 500 and row.gas_used == 22_000 and row.anchor_wallet == WALLET
        assert row.submitted_at is not None and row.confirmed_at is not None
        assert result["anchor"]["explorer_tx_url"] == f"https://sepolia.etherscan.io/tx/0x{'ab' * 32}"
        assert result["anchor"]["network_name"] == "Ethereum Sepolia"

    def test_receipt_timeout_keeps_tx_hash_and_stays_pending(self, db):
        c = chain()
        c.wait_for_anchor_receipt.side_effect = ReceiptTimeout()
        result = do_register(db, c)
        row = db.query(RegistryRecord).one()
        assert row.transaction_hash == "ab" * 32  # broadcast hash is never lost
        assert row.anchor_status == "pending" and row.confirmed_at is None
        assert result["anchor_status"] == "pending"

    def test_failed_receipt_marks_failed(self, db):
        do_register(db, chain(wait=receipt(success=False)))
        assert db.query(RegistryRecord).one().anchor_status == "failed"

    def test_insufficient_confirmations_stay_pending(self, db):
        do_register(db, chain(wait=receipt(confirmations=1), confirmations=3))
        assert db.query(RegistryRecord).one().anchor_status == "pending"

    def test_broadcast_failure_stores_nothing(self, db):
        c = chain()
        c.submit_anchor.side_effect = HTTPException(502, detail="Blockchain broadcast failed")
        with pytest.raises(HTTPException):
            do_register(db, c)
        assert db.query(RegistryRecord).count() == 0

    def test_pending_duplicate_is_refreshed_not_rebroadcast(self, db):
        c = chain()
        c.wait_for_anchor_receipt.side_effect = ReceiptTimeout()
        do_register(db, c)
        c2 = chain()
        c2.get_anchor_receipt.return_value = receipt()
        result = do_register(db, c2)
        assert result["already_existed"] is True and result["anchor_status"] == "confirmed"
        c2.submit_anchor.assert_not_called()
        assert db.query(RegistryRecord).one().confirmed_at is not None

    def test_failed_anchor_is_reanchored_on_reregistration(self, db):
        do_register(db, chain(wait=receipt(success=False)))
        c2 = chain()
        c2.submit_anchor.return_value = AnchorSubmission(tx_hash="ef" * 32, wallet=WALLET, nonce=2, chain_id=SEPOLIA)
        result = do_register(db, c2)
        row = db.query(RegistryRecord).one()
        assert result["already_existed"] is False
        assert row.transaction_hash == "ef" * 32 and row.anchor_status == "confirmed"


class TestVerifyOutcomes:
    def registered(self, db):
        do_register(db, chain())

    def verifier(self, **read):
        c = chain()
        for key, value in read.items():
            setattr(c.read_transaction_value, key, value)
        return c

    def test_valid_transaction(self, db):
        self.registered(db)
        c = self.verifier(return_value=onchain())
        result = do_verify(db, c)
        assert result["valid"] is True and result["verification_status"] == "confirmed"
        assert result["anchor"]["network_name"] == "Ethereum Sepolia"

    def test_wrong_hash(self, db):
        self.registered(db)
        result = do_verify(db, self.verifier(return_value=onchain(value="0" * 64)))
        assert result["valid"] is False and result["verification_status"] == "hash_mismatch"

    def test_wrong_sender_wallet(self, db):
        self.registered(db)
        result = do_verify(db, self.verifier(return_value=onchain(**{"from": "0x" + "b" * 40})))
        assert result["verification_status"] == "wallet_mismatch" and result["valid"] is False

    def test_malformed_calldata_is_invalid_not_a_500(self, db):
        self.registered(db)
        result = do_verify(db, self.verifier(side_effect=UnexpectedPayload()))
        assert result["valid"] is False and result["verification_status"] == "invalid_anchor_payload"

    def test_missing_transaction(self, db):
        self.registered(db)
        result = do_verify(db, self.verifier(side_effect=AnchorTransactionNotFound()))
        assert result["valid"] is False and result["verification_status"] == "transaction_not_found"

    def test_unmined_transaction_reports_pending(self, db):
        self.registered(db)
        result = do_verify(db, self.verifier(side_effect=AnchorPending()))
        assert result["valid"] is False and result["verification_status"] == "pending"

    def test_pending_record_is_never_reported_valid(self, db):
        c = chain()
        c.wait_for_anchor_receipt.side_effect = ReceiptTimeout()
        do_register(db, c)
        v = chain()
        v.get_anchor_receipt.return_value = None
        result = do_verify(db, v)
        assert result["valid"] is False and result["verification_status"] == "pending"
        v.read_transaction_value.assert_not_called()

    def test_pending_record_confirms_during_verify(self, db):
        c = chain()
        c.wait_for_anchor_receipt.side_effect = ReceiptTimeout()
        do_register(db, c)
        v = chain()
        v.get_anchor_receipt.return_value = receipt()
        v.read_transaction_value.return_value = onchain()
        assert do_verify(db, v)["valid"] is True
        assert db.query(RegistryRecord).one().anchor_status == "confirmed"

    def test_failed_anchor_is_not_valid(self, db):
        do_register(db, chain(wait=receipt(success=False)))
        result = do_verify(db, chain())
        assert result["valid"] is False and result["verification_status"] == "anchor_failed"

    def test_record_from_another_chain_is_not_checked_against_this_one(self, db):
        do_register(db, chain(chain_id=SEPOLIA))
        other = chain(chain_id=31337)
        result = do_verify(db, other)
        assert result["valid"] is False and result["verification_status"] == "chain_not_available"
        other.read_transaction_value.assert_not_called()


class TestLegacyAnvilRecords:
    def legacy_row(self, db, content=b"%PDF-1.4 a"):
        row = RegistryRecord(content_hash=hashlib.sha256(content).hexdigest(), transaction_hash="12" * 32,
                             issuer_id="old", original_filename="old.pdf", content_type="application/pdf")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def test_server_defaults_describe_a_legacy_local_anchor(self, db):
        row = self.legacy_row(db)
        assert (row.chain_id, row.anchor_status, row.anchor_type) == (31337, "legacy_local", "calldata-v1")

    def test_legacy_row_under_sepolia_config_is_unverifiable_not_an_error(self, db):
        self.legacy_row(db)
        c = chain(chain_id=SEPOLIA)
        result = do_verify(db, c)
        assert result["valid"] is False and result["verification_status"] == "chain_not_available"
        assert result["anchor"]["chain_id"] == 31337
        assert result["anchor"]["network_name"] == "Local development chain (Anvil)"
        assert result["anchor"]["explorer_tx_url"] is None
        c.read_transaction_value.assert_not_called()

    def test_legacy_row_still_verifies_when_backend_is_on_anvil(self, db, monkeypatch):
        monkeypatch.setenv("BLOCKCHAIN_CHAIN_ID", "31337")
        monkeypatch.delenv("BLOCKCHAIN_NETWORK_NAME")
        monkeypatch.delenv("BLOCKCHAIN_EXPLORER_URL")
        self.legacy_row(db)
        c = chain(chain_id=31337)
        c.read_transaction_value.return_value = onchain(**{"from": None})
        result = do_verify(db, c)
        assert result["valid"] is True and result["anchor"]["network_name"] == "Local development chain (Anvil)"

    def test_legacy_transaction_hash_gets_0x_prefix_in_anchor_metadata(self, db):
        self.legacy_row(db)
        result = do_verify(db, chain(chain_id=SEPOLIA))
        assert result["anchor"]["transaction_hash"] == "0x" + "12" * 32


class TestSchemaUpgrade:
    def test_adds_columns_to_pre_existing_table_and_keeps_rows(self):
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE registry_records (id VARCHAR(36) PRIMARY KEY, content_hash VARCHAR(64) NOT NULL, "
                "transaction_hash VARCHAR(66) NOT NULL, issuer_id VARCHAR(128) NOT NULL, "
                "original_filename VARCHAR(255), content_type VARCHAR(128), created_at DATETIME NOT NULL)"))
            conn.execute(text(
                "INSERT INTO registry_records VALUES ('r1','"+"a"*64+"','"+"b"*64+"','u','f.pdf','application/pdf','2026-09-01 10:00:00')"))

        added = migrate(engine)
        assert "registry_records.chain_id" in added and "registry_records.anchor_status" in added

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT chain_id, anchor_status, anchor_type, submitted_at, confirmed_at FROM registry_records")).one()
        assert (row.chain_id, row.anchor_status, row.anchor_type) == (31337, "legacy_local", "calldata-v1")
        assert str(row.submitted_at).startswith("2026-09-01")  # backfilled from created_at
        assert row.confirmed_at is None
        assert migrate(engine) == []  # idempotent

    def test_fresh_database_gets_all_columns(self):
        engine = create_engine("sqlite://")
        migrate(engine)
        names = {c["name"] for c in inspect(engine).get_columns("registry_records")}
        assert {"chain_id", "anchor_type", "anchor_status", "anchor_wallet", "block_number", "block_hash",
                "gas_used", "effective_gas_price", "submitted_at", "confirmed_at"} <= names
