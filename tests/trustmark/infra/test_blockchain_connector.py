# Run tests with: python -m pytest tests/trustmark/infra/test_blockchain_connector.py
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from hexbytes import HexBytes

from trustmark.infra.blockchain_connector import Blockchain_connector, VALUE_PREFIX


class TestBlockchainConnector:
    @pytest.fixture
    def valid_rpc_url(self):
        return "http://localhost:8545"

    @pytest.fixture
    def valid_private_key(self):
        return "0x" + "1" * 64  # Dummy private key for testing

    @pytest.fixture
    def connector(self, valid_rpc_url, valid_private_key):
        return Blockchain_connector(valid_rpc_url, valid_private_key)

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_connect_success(self, mock_web3, connector):
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3.return_value = mock_web3_instance

        connector._connect()
        assert connector.w3 == mock_web3_instance

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_connect_failure(self, mock_web3, connector):
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = False
        mock_web3.return_value = mock_web3_instance

        with pytest.raises(HTTPException) as exc_info:
            connector._connect()
        assert exc_info.value.status_code == 500
        assert "Blockchain connection failed" in exc_info.value.detail

    @patch("trustmark.infra.blockchain_connector.Account")
    def test_get_account_invalid_key(self, mock_account, connector):
        mock_account.from_key.side_effect = Exception("Invalid key")

        with pytest.raises(HTTPException) as exc_info:
            connector._get_account()
        assert exc_info.value.status_code == 500
        assert "Invalid private key" in exc_info.value.detail

    @patch("trustmark.infra.blockchain_connector.Web3")
    @patch("trustmark.infra.blockchain_connector.Account")
    def test_create_transaction_success(self, mock_account, mock_web3, connector):
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3_instance.eth.chain_id = 1
        mock_web3_instance.eth.get_transaction_count.return_value = 0
        mock_web3_instance.to_wei.side_effect = lambda x, y: (
            2000000000 if x == 2 and y == "gwei" else 1000000000
        )

        # HexBytes handles standard hex strings. "0x1234".hex() evaluating to "1234"
        mock_web3_instance.eth.send_raw_transaction.return_value = HexBytes("0x1234")
        mock_web3.return_value = mock_web3_instance

        mock_account_instance = MagicMock()
        mock_account_instance.address = "0x" + "a" * 40
        mock_account_instance.sign_transaction.return_value.raw_transaction = (
            b"signed_tx"
        )
        mock_account.from_key.return_value = mock_account_instance

        tx_hash = connector.create_transaction("test value")
        assert tx_hash == "1234"

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_create_transaction_connection_failure(self, mock_web3, connector):
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = False
        mock_web3.return_value = mock_web3_instance

        with pytest.raises(HTTPException) as exc_info:
            connector.create_transaction("test value")
        assert exc_info.value.status_code == 500
        assert "Blockchain connection failed" in exc_info.value.detail

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_read_transaction_value_success(self, mock_web3, connector):
        """FIXED 2026-09-09: this fixture was stale against the current
        connector contract - `read_transaction_value` requires a mined
        transaction (`blockNumber` present, and that block resolvable to a
        `timestamp`) and returns a {"value", "timestamp"} dict, not a bare
        string. The old version of this test mocked `get_transaction` with
        only an `input` field and asserted the return value was a plain
        string, so it was actually exercising the "still pending" 400 path
        by accident and failing on the assertion regardless.

        This is the exact fixture staleness the accompanying paper's §5.1
        describes fixing ("16 out of 17 ... one outdated test fixture was
        then corrected ... final test run passed 17 out of 17") - but this
        repository still had the old, broken version prior to this fix, so
        that corrected state was not actually persisted here. Re-running
        this file before this edit reproduces the paper's original 16/17
        result exactly."""
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3_instance.eth.get_transaction.return_value = {
            "input": VALUE_PREFIX + b"test value",
            "blockNumber": 42,
        }
        mock_web3_instance.eth.get_block.return_value = {"timestamp": 1_757_000_000}
        mock_web3.return_value = mock_web3_instance

        result = connector.read_transaction_value("0x1234")
        assert result["value"] == "test value"
        assert result["timestamp"].year == 2025  # 1_757_000_000 unix -> 2025-09-04 UTC

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_read_transaction_value_pending_no_block_number_returns_400(self, mock_web3, connector):
        """Gap-fill (2026-09-09): the "still pending" branch (transaction
        broadcast but not yet mined) had no coverage at all before this."""
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3_instance.eth.get_transaction.return_value = {
            "input": VALUE_PREFIX + b"test value",
            "blockNumber": None,
        }
        mock_web3.return_value = mock_web3_instance

        with pytest.raises(HTTPException) as exc_info:
            connector.read_transaction_value("0x1234")
        assert exc_info.value.status_code == 400
        assert "pending" in exc_info.value.detail.lower()

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_read_transaction_value_block_missing_timestamp_returns_400(self, mock_web3, connector):
        """Gap-fill (2026-09-09): a mined block with no resolvable timestamp
        (edge case on some dev/test chains) also had no coverage."""
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3_instance.eth.get_transaction.return_value = {
            "input": VALUE_PREFIX + b"test value",
            "blockNumber": 42,
        }
        mock_web3_instance.eth.get_block.return_value = {"timestamp": None}
        mock_web3.return_value = mock_web3_instance

        with pytest.raises(HTTPException) as exc_info:
            connector.read_transaction_value("0x1234")
        assert exc_info.value.status_code == 400
        assert "pending" in exc_info.value.detail.lower()

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_read_transaction_value_invalid_format(self, mock_web3, connector):
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3_instance.eth.get_transaction.return_value = {
            "input": b"invalid format"
        }
        mock_web3.return_value = mock_web3_instance

        with pytest.raises(HTTPException) as exc_info:
            connector.read_transaction_value("0x1234")
        assert exc_info.value.status_code == 500
        assert "Unexpected payload format" in exc_info.value.detail

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_read_transaction_value_no_input(self, mock_web3, connector):
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3_instance.eth.get_transaction.return_value = {}
        mock_web3.return_value = mock_web3_instance

        with pytest.raises(HTTPException) as exc_info:
            connector.read_transaction_value("0x1234")
        assert exc_info.value.status_code == 500
        assert "Unexpected payload format" in exc_info.value.detail

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_get_web3_connection_failure(self, mock_web3, connector):
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = False
        mock_web3.return_value = mock_web3_instance

        with pytest.raises(HTTPException) as exc_info:
            connector._get_web3()
        assert exc_info.value.status_code == 500
        assert "Blockchain connection failed" in exc_info.value.detail

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_wait_for_receipt_success(self, mock_web3, connector):
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3_instance.eth.wait_for_transaction_receipt.return_value = {
            "blockNumber": 99955
        }
        mock_web3.return_value = mock_web3_instance

        receipt = connector.wait_for_receipt("0x1234")
        assert receipt == 99955

    @patch("trustmark.infra.blockchain_connector.Web3")
    def test_wait_for_receipt_connection_failure(self, mock_web3, connector):
        """Gap-fill (2026-09-09): only the success path had coverage - the
        node-down / not-yet-connected case for this method (relevant to
        VER-8 from the 2026-09-09 live QA pass) did not."""
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = False
        mock_web3.return_value = mock_web3_instance

        with pytest.raises(HTTPException) as exc_info:
            connector.wait_for_receipt("0x1234")
        assert exc_info.value.status_code == 500
        assert "Blockchain connection failed" in exc_info.value.detail
