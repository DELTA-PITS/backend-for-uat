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
        mock_web3_instance = MagicMock()
        mock_web3_instance.is_connected.return_value = True
        mock_web3_instance.eth.get_transaction.return_value = {
            "input": VALUE_PREFIX + b"test value"
        }
        mock_web3.return_value = mock_web3_instance

        value = connector.read_transaction_value("0x1234")
        assert value == "test value"

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
