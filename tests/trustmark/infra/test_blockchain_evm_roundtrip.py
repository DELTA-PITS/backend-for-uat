"""
Round-trip against a real in-process EVM (eth-tester / py-evm) instead of a hand-written fake:
dynamic fee + gas estimate, pending nonce, signing with the chain's real chain ID, receipt and
calldata readback. Skipped automatically when the optional `web3[tester]` extra is not installed.

Run with:
    python -m pytest tests/trustmark/infra/test_blockchain_evm_roundtrip.py
"""
from __future__ import annotations

import pytest

pytest.importorskip("eth_tester")

from eth_account import Account  # noqa: E402
from web3 import EthereumTesterProvider, Web3  # noqa: E402

from trustmark.infra.blockchain_connector import BlockchainConnector, ChainMismatch  # noqa: E402

HASH = "ab" * 32


@pytest.fixture()
def evm():
    w3 = Web3(EthereumTesterProvider())
    signer = Account.create()
    w3.eth.send_transaction({"from": w3.eth.accounts[0], "to": signer.address, "value": 10**18})
    connector = BlockchainConnector("http://unused", signer.key.hex(), chain_id=w3.eth.chain_id)
    connector.w3 = w3
    return connector, w3, signer


def test_anchor_round_trip(evm):
    connector, w3, signer = evm
    submission = connector.submit_anchor(HASH)
    receipt = connector.wait_for_anchor_receipt(submission.tx_hash)
    assert receipt.success and receipt.confirmations >= 1
    onchain = connector.read_transaction_value(submission.tx_hash)
    assert onchain["value"] == HASH
    assert onchain["from"] == signer.address.lower()


def test_sequential_anchors_use_consecutive_nonces(evm):
    connector, w3, _ = evm
    first = connector.submit_anchor("11" * 32)
    second = connector.submit_anchor("22" * 32)
    assert second.nonce == first.nonce + 1


def test_receipt_gas_stays_within_estimated_limit(evm):
    connector, w3, _ = evm
    submission = connector.submit_anchor(HASH)
    receipt = connector.wait_for_anchor_receipt(submission.tx_hash)
    tx = w3.eth.get_transaction(submission.tx_hash)
    assert receipt.gas_used <= tx["gas"]
    assert tx["gas"] != 50_000


def test_wrong_configured_chain_id_is_rejected(evm):
    _, w3, signer = evm
    connector = BlockchainConnector("http://unused", signer.key.hex(), chain_id=11155111)
    from unittest.mock import patch

    with patch("trustmark.infra.blockchain_connector.Web3") as web3:
        web3.return_value = w3
        with pytest.raises(ChainMismatch):
            connector.verify_chain()
