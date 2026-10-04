"""
Public-chain readiness of BlockchainConnector (Phase 1 of the Sepolia migration):
chain-ID validation, dynamic fee/gas, pending-nonce + signer lock, bounded receipt
waiting, and tolerant reads. Uses a fake web3 object and a throwaway generated key -
no network, no real key material.

Run with:
    python -m pytest tests/trustmark/infra/test_blockchain_anchor.py
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from fastapi import HTTPException
from hexbytes import HexBytes
from web3.exceptions import TimeExhausted, TransactionNotFound

from trustmark.infra import blockchain_connector as bc
from trustmark.infra.blockchain_connector import (
    BASE_FEE_MULTIPLIER,
    GAS_ESTIMATE_BUFFER_PERCENT,
    VALUE_PREFIX,
    BlockchainConnector,
    ChainConfig,
    ChainConfigError,
    ChainMismatch,
)
from trustmark.infra.nonce_lock import signer_lock, signer_lock_key

ACCOUNT = Account.create()
GWEI = 10**9
HASH = "ab" * 32


class FakeEth:
    def __init__(self, chain_id=11155111):
        self.chain_id = chain_id
        self.block_number = 1000
        self.base_fee = 10 * GWEI
        self.priority_fee = 2 * GWEI
        self.gas_price = 5 * GWEI
        self.estimate = 22_000
        self.balance = 10**18
        self.next_nonce = 7
        self.sent = []
        self.nonce_queries = []
        self.send_error = None
        self.tx = {}
        self.receipt = None
        self.receipt_error = None
        self.estimate_calls = []

    @property
    def max_priority_fee(self):
        if self.priority_fee is None:
            raise ValueError("method not supported")
        return self.priority_fee

    def get_block(self, ident):
        if isinstance(ident, int):
            return {"timestamp": 1_757_000_000}
        block = {"number": self.block_number}
        if self.base_fee is not None:
            block["baseFeePerGas"] = self.base_fee
        return block

    def fee_history(self, blocks, newest, percentiles):
        return {"reward": [[1 * GWEI], [3 * GWEI], [2 * GWEI]]}

    def estimate_gas(self, tx):
        self.estimate_calls.append(tx)
        return self.estimate

    def get_balance(self, address):
        return self.balance

    def get_transaction_count(self, address, block="latest"):
        self.nonce_queries.append(block)
        value = self.next_nonce
        if block == "pending":
            self.next_nonce += 1
        return value

    def send_raw_transaction(self, raw):
        if self.send_error:
            raise self.send_error
        self.sent.append(raw)
        return HexBytes("0x" + "cd" * 32)

    def get_transaction(self, tx_hash):
        if isinstance(self.tx, Exception):
            raise self.tx
        return self.tx

    def get_transaction_receipt(self, tx_hash):
        if self.receipt_error:
            raise self.receipt_error
        return self.receipt

    def wait_for_transaction_receipt(self, tx_hash, timeout=120, poll_latency=0.1):
        if self.receipt_error:
            raise self.receipt_error
        return self.receipt


def make_connector(eth=None, **kwargs):
    eth = eth or FakeEth()
    connector = BlockchainConnector("http://rpc.invalid", ACCOUNT.key.hex(), chain_id=eth.chain_id, **kwargs)
    connector.w3 = SimpleNamespace(eth=eth)
    return connector, eth


def receipt(status=1, block=990, **extra):
    return {"status": status, "blockNumber": block, "blockHash": HexBytes("0x" + "11" * 32),
            "gasUsed": 22_100, "effectiveGasPrice": 12 * GWEI, **extra}


# --------------------------------------------------------------------------- config


class TestChainConfig:
    ENV = {
        "BLOCKCHAIN_RPC_URL": "http://rpc.invalid",
        "BLOCKCHAIN_PRIVATE_KEY": "0xabc",
        "BLOCKCHAIN_CHAIN_ID": "11155111",
        "BLOCKCHAIN_NETWORK_NAME": "Ethereum Sepolia",
        "BLOCKCHAIN_EXPLORER_URL": "https://sepolia.etherscan.io/",
    }

    def apply(self, monkeypatch, **overrides):
        for key in (*self.ENV, "BLOCKCHAIN_RECEIPT_TIMEOUT_SECONDS", "BLOCKCHAIN_CONFIRMATIONS"):
            monkeypatch.delenv(key, raising=False)
        for key, value in {**self.ENV, **overrides}.items():
            if value is not None:
                monkeypatch.setenv(key, value)

    def test_full_sepolia_config(self, monkeypatch):
        self.apply(monkeypatch)
        config = ChainConfig.from_env()
        assert config.chain_id == 11155111
        assert config.network_name == "Ethereum Sepolia"
        assert config.explorer_url == "https://sepolia.etherscan.io"
        assert config.confirmations == 1

    def test_anvil_config_stays_valid_without_name_or_explorer(self, monkeypatch):
        self.apply(monkeypatch, BLOCKCHAIN_CHAIN_ID="31337", BLOCKCHAIN_NETWORK_NAME=None,
                   BLOCKCHAIN_EXPLORER_URL=None)
        config = ChainConfig.from_env()
        assert config.chain_id == 31337
        assert config.explorer_url is None

    @pytest.mark.parametrize("name", ["BLOCKCHAIN_RPC_URL", "BLOCKCHAIN_PRIVATE_KEY", "BLOCKCHAIN_CHAIN_ID"])
    def test_missing_required_value_fails(self, monkeypatch, name):
        self.apply(monkeypatch, **{name: None})
        with pytest.raises(ChainConfigError):
            ChainConfig.from_env()

    @pytest.mark.parametrize("value", ["abc", "0", "-5"])
    def test_invalid_chain_id_fails(self, monkeypatch, value):
        self.apply(monkeypatch, BLOCKCHAIN_CHAIN_ID=value)
        with pytest.raises(ChainConfigError):
            ChainConfig.from_env()

    def test_secrets_never_appear_in_repr(self, monkeypatch):
        self.apply(monkeypatch, BLOCKCHAIN_PRIVATE_KEY="0xSECRETKEY", BLOCKCHAIN_RPC_URL="http://rpc/APIKEY123")
        config = ChainConfig.from_env()
        connector = BlockchainConnector.from_config(config)
        for text in (repr(config), str(config), repr(connector)):
            assert "SECRETKEY" not in text and "APIKEY123" not in text


class TestChainIdValidation:
    def test_matching_chain_connects(self):
        with patch.object(bc, "Web3") as web3:
            web3.return_value.is_connected.return_value = True
            web3.return_value.eth.chain_id = 11155111
            connector = BlockchainConnector("http://x", "0x1", chain_id=11155111)
            connector.verify_chain()
            assert connector.w3 is web3.return_value

    def test_mismatch_fails_fast_with_both_ids_in_message(self):
        with patch.object(bc, "Web3") as web3:
            web3.return_value.is_connected.return_value = True
            web3.return_value.eth.chain_id = 31337
            connector = BlockchainConnector("http://x", "0x1", chain_id=11155111)
            with pytest.raises(ChainMismatch) as exc:
                connector.verify_chain()
        assert "11155111" in exc.value.detail and "31337" in exc.value.detail
        assert connector.w3 is None  # never cached after a mismatch

    def test_anvil_chain_id_still_accepted(self):
        with patch.object(bc, "Web3") as web3:
            web3.return_value.is_connected.return_value = True
            web3.return_value.eth.chain_id = 31337
            BlockchainConnector("http://x", "0x1", chain_id=31337).verify_chain()


# --------------------------------------------------------------------------- transaction


class TestTransactionBuilding:
    def decode(self, eth):
        return Account.recover_transaction(eth.sent[0])

    def test_calldata_is_value_prefix_plus_hash_self_transfer_zero_value(self):
        connector, eth = make_connector()
        submission = connector.submit_anchor(HASH)
        call = eth.estimate_calls[0]
        assert call["to"] == call["from"] == ACCOUNT.address
        assert call["value"] == 0
        assert bytes.fromhex(call["data"][2:]) == VALUE_PREFIX + HASH.encode()
        assert self.decode(eth) == ACCOUNT.address
        assert submission.wallet == ACCOUNT.address
        assert submission.tx_hash == "cd" * 32

    def test_signed_with_configured_chain_id(self):
        connector, eth = make_connector(FakeEth(chain_id=11155111))
        connector.submit_anchor(HASH)
        from eth_account.typed_transactions import TypedTransaction

        parsed = TypedTransaction.from_bytes(HexBytes(eth.sent[0])).as_dict()
        assert parsed["chainId"] == 11155111

    def test_gas_is_estimate_plus_documented_buffer(self):
        connector, eth = make_connector()
        eth.estimate = 22_000
        tx = connector.build_transaction(connector.w3, ACCOUNT.address, "0x00", 0)
        assert tx["gas"] == 22_000 * (100 + GAS_ESTIMATE_BUFFER_PERCENT) // 100
        assert tx["gas"] != 50_000  # the old Anvil-era constant is gone

    def test_dynamic_eip1559_fees_from_base_fee_and_node_priority(self):
        connector, eth = make_connector()
        tx = connector.build_transaction(connector.w3, ACCOUNT.address, "0x00", 0)
        assert tx["maxPriorityFeePerGas"] == 2 * GWEI
        assert tx["maxFeePerGas"] == 10 * GWEI * BASE_FEE_MULTIPLIER + 2 * GWEI
        assert "gasPrice" not in tx

    def test_priority_fee_falls_back_to_fee_history_median(self):
        connector, eth = make_connector()
        eth.priority_fee = None
        tx = connector.build_transaction(connector.w3, ACCOUNT.address, "0x00", 0)
        assert tx["maxPriorityFeePerGas"] == 2 * GWEI  # median of 1,3,2 gwei

    def test_legacy_chain_without_base_fee_uses_gas_price(self):
        connector, eth = make_connector()
        eth.base_fee = None
        tx = connector.build_transaction(connector.w3, ACCOUNT.address, "0x00", 0)
        assert tx["gasPrice"] == 5 * GWEI
        assert "maxFeePerGas" not in tx

    def test_insufficient_funds_is_a_clear_503_before_broadcast(self):
        connector, eth = make_connector()
        eth.balance = 1
        with pytest.raises(HTTPException) as exc:
            connector.submit_anchor(HASH)
        assert exc.value.status_code == 503
        assert eth.sent == []

    def test_send_failure_becomes_502_without_leaking_node_message(self):
        connector, eth = make_connector()
        eth.send_error = ValueError("nonce too low: secret-provider-detail")
        with pytest.raises(HTTPException) as exc:
            connector.submit_anchor(HASH)
        assert exc.value.status_code == 502
        assert "secret-provider-detail" not in exc.value.detail


# --------------------------------------------------------------------------- nonce


class TestNonceSafety:
    def test_uses_pending_nonce(self):
        connector, eth = make_connector()
        submission = connector.submit_anchor(HASH)
        assert eth.nonce_queries == ["pending"]
        assert submission.nonce == 7

    def test_lock_is_held_across_nonce_read_and_broadcast(self):
        events = []

        @contextmanager
        def lock(chain_id, address):
            events.append(("acquire", chain_id, address))
            yield
            events.append(("release",))

        connector, eth = make_connector(signer_lock=lock)
        original_send = eth.send_raw_transaction
        eth.send_raw_transaction = lambda raw: (events.append(("send",)), original_send(raw))[1]
        connector.submit_anchor(HASH)
        assert events == [("acquire", 11155111, ACCOUNT.address), ("send",), ("release",)]

    def test_concurrent_submissions_get_distinct_nonces(self):
        connector, eth = make_connector(signer_lock=lambda chain_id, address: signer_lock(chain_id, address, engine=sqlite_engine()))
        original = eth.get_transaction_count

        def slow_count(address, block="latest"):
            value = original(address, block)
            time.sleep(0.05)  # widens the read->broadcast race window
            return value

        eth.get_transaction_count = slow_count
        results, errors = [], []

        def run():
            try:
                results.append(connector.submit_anchor(HASH).nonce)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors
        assert sorted(results) == [7, 8, 9, 10, 11]

    def test_lock_key_is_stable_per_chain_and_signer(self):
        assert signer_lock_key(1, ACCOUNT.address) == signer_lock_key(1, ACCOUNT.address.lower())
        assert signer_lock_key(1, ACCOUNT.address) != signer_lock_key(2, ACCOUNT.address)
        assert -(2**63) <= signer_lock_key(1, ACCOUNT.address) < 2**63


def sqlite_engine():
    from sqlalchemy import create_engine

    return create_engine("sqlite://")


# --------------------------------------------------------------------------- receipts


class TestReceipts:
    def test_confirmed_receipt(self):
        connector, eth = make_connector()
        eth.receipt = receipt(block=990)
        result = connector.wait_for_anchor_receipt("0x" + "cd" * 32)
        assert result.success and result.block_number == 990
        assert result.confirmations == 11  # 1000 - 990 + 1
        assert result.gas_used == 22_100 and result.effective_gas_price == 12 * GWEI
        assert result.block_hash == "11" * 32

    def test_failed_receipt(self):
        connector, eth = make_connector()
        eth.receipt = receipt(status=0)
        assert connector.wait_for_anchor_receipt("0xab").success is False

    def test_timeout_is_explicit_504(self):
        connector, eth = make_connector()
        eth.receipt_error = TimeExhausted("no receipt")
        with pytest.raises(HTTPException) as exc:
            connector.wait_for_anchor_receipt("0xab")
        assert exc.value.status_code == 504

    def test_wait_passes_a_finite_timeout(self):
        connector, eth = make_connector(receipt_timeout_seconds=17)
        eth.receipt = receipt()
        seen = {}
        original = eth.wait_for_transaction_receipt
        eth.wait_for_transaction_receipt = lambda h, timeout, poll_latency: (seen.update(timeout=timeout), original(h))[1]
        connector.wait_for_anchor_receipt("0xab")
        assert seen["timeout"] == 17

    def test_non_blocking_lookup_returns_none_while_unmined(self):
        connector, eth = make_connector()
        eth.receipt_error = TransactionNotFound("not yet")
        assert connector.get_anchor_receipt("0xab") is None

    def test_non_blocking_lookup_returns_receipt_when_mined(self):
        connector, eth = make_connector()
        eth.receipt = receipt()
        assert connector.get_anchor_receipt("0xab").success is True


# --------------------------------------------------------------------------- reading


class TestReading:
    def tx(self, **overrides):
        return {"input": VALUE_PREFIX + HASH.encode(), "blockNumber": 5, "from": ACCOUNT.address, **overrides}

    def test_valid_anchor(self):
        connector, eth = make_connector()
        eth.tx = self.tx()
        result = connector.read_transaction_value("0xab")
        assert result["value"] == HASH
        assert result["from"] == ACCOUNT.address.lower()

    def test_missing_transaction(self):
        connector, eth = make_connector()
        eth.tx = TransactionNotFound("gone")
        with pytest.raises(bc.AnchorTransactionNotFound):
            connector.read_transaction_value("0xab")

    def test_malformed_calldata(self):
        connector, eth = make_connector()
        eth.tx = self.tx(input=b"NOT-AN-ANCHOR")
        with pytest.raises(bc.UnexpectedPayload):
            connector.read_transaction_value("0xab")

    def test_undecodable_value_bytes(self):
        connector, eth = make_connector()
        eth.tx = self.tx(input=VALUE_PREFIX + b"\xff\xfe")
        with pytest.raises(bc.UnexpectedPayload):
            connector.read_transaction_value("0xab")

    def test_unmined_transaction_is_pending(self):
        connector, eth = make_connector()
        eth.tx = self.tx(blockNumber=None)
        with pytest.raises(bc.AnchorPending):
            connector.read_transaction_value("0xab")


# --------------------------------------------------------------------------- nonce floor (stale public RPC)


class MemoryNonceStore:
    def __init__(self, floor=None):
        self.floor, self.puts = floor, []

    def get_floor(self, chain_id, address, ttl_seconds):
        return self.floor

    def put_next(self, chain_id, address, next_nonce):
        self.floor = next_nonce
        self.puts.append(next_nonce)


class TestNonceFloor:
    """Regression for the Phase 2 live finding: right after a broadcast, a load-balanced public RPC kept
    answering "pending" with the OLD nonce, so the next anchor reused it ("replacement transaction underpriced")."""

    def stale(self, eth):
        eth.get_transaction_count = lambda address, block="latest": 7  # RPC never advances

    def test_floor_overrides_a_stale_rpc_nonce(self):
        store = MemoryNonceStore(floor=9)
        connector, eth = make_connector(nonce_store=store)
        self.stale(eth)
        assert connector.submit_anchor(HASH).nonce == 9
        assert store.puts == [10]

    def test_rpc_nonce_wins_when_ahead_of_the_floor(self):
        store = MemoryNonceStore(floor=3)
        connector, eth = make_connector(nonce_store=store)
        self.stale(eth)
        assert connector.submit_anchor(HASH).nonce == 7
        assert store.puts == [8]

    def test_no_floor_means_plain_pending_nonce(self):
        connector, eth = make_connector(nonce_store=MemoryNonceStore(floor=None))
        self.stale(eth)
        assert connector.submit_anchor(HASH).nonce == 7

    def test_failed_broadcast_does_not_advance_the_floor(self):
        store = MemoryNonceStore(floor=None)
        connector, eth = make_connector(nonce_store=store)
        eth.send_error = ValueError("boom")
        with pytest.raises(HTTPException):
            connector.submit_anchor(HASH)
        assert store.puts == []

    def test_sequential_anchors_against_a_stale_rpc_get_consecutive_nonces(self):
        store = MemoryNonceStore()
        connector, eth = make_connector(nonce_store=store)
        self.stale(eth)
        assert [connector.submit_anchor(HASH).nonce for _ in range(4)] == [7, 8, 9, 10]

    def test_without_a_store_a_stale_rpc_reuses_the_nonce(self):
        connector, eth = make_connector()
        self.stale(eth)
        assert [connector.submit_anchor(HASH).nonce for _ in range(2)] == [7, 7]  # the bug the floor fixes

    def test_concurrent_anchors_against_a_stale_rpc_stay_distinct_with_lock_and_store(self):
        connector, eth = make_connector(signer_lock=lambda c, a: signer_lock(c, a, engine=sqlite_engine()),
                                        nonce_store=MemoryNonceStore())
        self.stale(eth)
        nonces, errors = [], []

        def run():
            try:
                nonces.append(connector.submit_anchor(HASH).nonce)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors and sorted(nonces) == [7, 8, 9, 10, 11, 12]


class TestDbNonceStore:
    def store(self):
        from sqlalchemy.pool import StaticPool

        from trustmark.infra.db import Base
        from trustmark.infra.nonce_lock import DbNonceStore
        from trustmark.registry import models  # noqa: F401
        from sqlalchemy import create_engine

        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        return DbNonceStore(engine), engine

    def test_roundtrip_and_update(self):
        store, _ = self.store()
        assert store.get_floor(1, ACCOUNT.address, 120) is None
        store.put_next(1, ACCOUNT.address, 5)
        assert store.get_floor(1, ACCOUNT.address.lower(), 120) == 5
        store.put_next(1, ACCOUNT.address, 6)
        assert store.get_floor(1, ACCOUNT.address, 120) == 6

    def test_chains_are_independent(self):
        store, _ = self.store()
        store.put_next(1, ACCOUNT.address, 5)
        assert store.get_floor(2, ACCOUNT.address, 120) is None

    def test_floor_expires_so_a_dropped_transaction_cannot_leave_a_permanent_gap(self):
        from sqlalchemy import text

        store, engine = self.store()
        store.put_next(1, ACCOUNT.address, 5)
        with engine.begin() as conn:
            conn.execute(text("UPDATE anchor_signer_nonces SET updated_at = '2000-01-01 00:00:00'"))
        assert store.get_floor(1, ACCOUNT.address, 120) is None
