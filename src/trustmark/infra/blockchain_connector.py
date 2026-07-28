from fastapi import HTTPException
from web3 import Web3
from eth_account import Account
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from datetime import datetime, timezone

VALUE_PREFIX = b"VALUE:"


class BlockchainConnector:
    def __init__(self, rpc_url: str, private_key: str):
        self.url = rpc_url
        self.private_key = private_key
        self.w3: Web3 | None = None

    def _get_web3(self) -> Web3:
        if self.w3 is None:
            self._connect()
        if self.w3 is None:
            raise HTTPException(500, detail="Blockchain connection failed")
        return self.w3

    def _get_account(self) -> LocalAccount:
        try:
            return Account.from_key(self.private_key)
        except Exception as exc:
            raise HTTPException(500, detail="Invalid private key") from exc

    def _connect(self):
        self.w3 = Web3(Web3.HTTPProvider(self.url))
        if not self.w3.is_connected():
            raise HTTPException(500, detail="Blockchain connection failed")

    def create_transaction(self, value: str) -> str:
        w3 = self._get_web3()
        acct = self._get_account()
        payload_hex = Web3.to_hex(VALUE_PREFIX + value.encode("utf-8"))

        tx = {
            "from": acct.address,
            "to": acct.address,
            "value": 0,
            "data": payload_hex,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "chainId": w3.eth.chain_id,
            "gas": 50000,
            "maxFeePerGas": w3.to_wei(2, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        }

        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

    def read_transaction_value(self, tx_hash: str) -> dict:
        w3 = self._get_web3()
        onchain_tx = w3.eth.get_transaction(HexBytes(tx_hash))
        input_bytes = onchain_tx.get("input")
        if input_bytes is None or not input_bytes.startswith(VALUE_PREFIX):
            raise HTTPException(500, detail="Unexpected payload format")

        block_number = onchain_tx.get("blockNumber")
        if block_number is None:
            raise HTTPException(400, detail="Transaction is still pending")

        block = w3.eth.get_block(block_number)
        timestamp = block.get("timestamp")
        if timestamp is None:
            raise HTTPException(400, detail="Transaction is still pending")

        utc_date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return {
            "value": input_bytes[len(VALUE_PREFIX) :].decode("utf-8"),
            "timestamp": utc_date,
        }

    def wait_for_receipt(self, tx_hash: str) -> int:
        w3 = self._get_web3()
        receipt = w3.eth.wait_for_transaction_receipt(HexBytes(tx_hash))
        return receipt["blockNumber"]


# Backward-compatible alias for older imports.
Blockchain_connector = BlockchainConnector
