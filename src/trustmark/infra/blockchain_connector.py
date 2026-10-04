import logging
import os
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, ContextManager

from eth_account import Account
from eth_account.signers.local import LocalAccount
from fastapi import HTTPException
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import TimeExhausted, TransactionNotFound

logger = logging.getLogger(__name__)

VALUE_PREFIX = b"VALUE:"
ANCHOR_TYPE = "calldata-v1"

# Headroom applied to the node's gas estimate. The anchor payload has a fixed
# shape (self-transfer + ~70 bytes calldata), so the estimate is stable; 20%
# only absorbs estimator variance between RPC providers.
GAS_ESTIMATE_BUFFER_PERCENT = 20
# EIP-1559 maxFeePerGas = BASE_FEE_MULTIPLIER * current base fee + priority fee.
# The base fee can rise at most 12.5% per block, so 2x survives ~6 consecutive
# full blocks before the transaction would be priced out (standard wallet practice).
BASE_FEE_MULTIPLIER = 2
# Fee-history window used only when the node does not expose eth_maxPriorityFeePerGas.
FEE_HISTORY_BLOCKS = 5
FEE_HISTORY_REWARD_PERCENTILE = 50

DEFAULT_RECEIPT_TIMEOUT_SECONDS = 45
DEFAULT_RECEIPT_POLL_SECONDS = 1.0
DEFAULT_RPC_TIMEOUT_SECONDS = 30
DEFAULT_CONFIRMATIONS = 1

SignerLock = Callable[[int, str], ContextManager]


class ChainConfigError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(500, detail=detail)


class ChainMismatch(HTTPException):
    def __init__(self, expected: int, actual: int):
        super().__init__(
            500,
            detail=f"Blockchain chain ID mismatch: configured {expected}, RPC reports {actual}",
        )


class AnchorBroadcastFailed(HTTPException):
    def __init__(self, detail: str = "Blockchain broadcast failed"):
        super().__init__(502, detail=detail)


class InsufficientFunds(HTTPException):
    def __init__(self):
        super().__init__(503, detail="Anchor wallet has insufficient funds")


class ReceiptTimeout(HTTPException):
    def __init__(self):
        super().__init__(504, detail="Timed out waiting for the transaction receipt")


class AnchorPending(HTTPException):
    def __init__(self):
        super().__init__(400, detail="Transaction is still pending")


class UnexpectedPayload(HTTPException):
    def __init__(self):
        super().__init__(500, detail="Unexpected payload format")


class AnchorTransactionNotFound(HTTPException):
    def __init__(self):
        super().__init__(404, detail="Transaction not found on the configured chain")


@dataclass(frozen=True)
class ChainConfig:
    rpc_url: str
    private_key: str = field(repr=False)
    chain_id: int
    network_name: str | None = None
    explorer_url: str | None = None
    receipt_timeout_seconds: int = DEFAULT_RECEIPT_TIMEOUT_SECONDS
    confirmations: int = DEFAULT_CONFIRMATIONS

    def __repr__(self) -> str:
        return (
            f"ChainConfig(chain_id={self.chain_id}, network_name={self.network_name!r}, "
            f"rpc_url=<redacted>, private_key=<redacted>)"
        )

    @classmethod
    def from_env(cls) -> "ChainConfig":
        rpc_url = os.environ.get("BLOCKCHAIN_RPC_URL", "").strip()
        private_key = os.environ.get("BLOCKCHAIN_PRIVATE_KEY", "").strip()
        if not rpc_url or not private_key:
            raise ChainConfigError("Blockchain settings are not configured")

        raw_chain_id = os.environ.get("BLOCKCHAIN_CHAIN_ID", "").strip()
        if not raw_chain_id:
            raise ChainConfigError("BLOCKCHAIN_CHAIN_ID is not configured")
        try:
            chain_id = int(raw_chain_id)
        except ValueError as exc:
            raise ChainConfigError("BLOCKCHAIN_CHAIN_ID must be an integer") from exc
        if chain_id <= 0:
            raise ChainConfigError("BLOCKCHAIN_CHAIN_ID must be a positive integer")

        return cls(
            rpc_url=rpc_url,
            private_key=private_key,
            chain_id=chain_id,
            network_name=os.environ.get("BLOCKCHAIN_NETWORK_NAME", "").strip() or None,
            explorer_url=(os.environ.get("BLOCKCHAIN_EXPLORER_URL", "").strip().rstrip("/") or None),
            receipt_timeout_seconds=_env_positive_int(
                "BLOCKCHAIN_RECEIPT_TIMEOUT_SECONDS", DEFAULT_RECEIPT_TIMEOUT_SECONDS
            ),
            confirmations=_env_positive_int("BLOCKCHAIN_CONFIRMATIONS", DEFAULT_CONFIRMATIONS),
        )


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ChainConfigError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ChainConfigError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class AnchorSubmission:
    tx_hash: str
    wallet: str
    nonce: int
    chain_id: int | None


@dataclass(frozen=True)
class AnchorReceipt:
    success: bool
    block_number: int
    block_hash: str | None
    gas_used: int | None
    effective_gas_price: int | None
    confirmations: int


def explorer_tx_url(explorer_url: str | None, tx_hash: str) -> str | None:
    if not explorer_url:
        return None
    normalized = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
    return f"{explorer_url.rstrip('/')}/tx/{normalized}"


class BlockchainConnector:
    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        chain_id: int | None = None,
        *,
        signer_lock: SignerLock | None = None,
        receipt_timeout_seconds: int = DEFAULT_RECEIPT_TIMEOUT_SECONDS,
        rpc_timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
        confirmations: int = DEFAULT_CONFIRMATIONS,
    ):
        self.url = rpc_url
        self.private_key = private_key
        self.chain_id = chain_id
        self.signer_lock = signer_lock
        self.receipt_timeout_seconds = receipt_timeout_seconds
        self.rpc_timeout_seconds = rpc_timeout_seconds
        self.confirmations = confirmations
        self.w3: Web3 | None = None

    @classmethod
    def from_config(cls, config: ChainConfig, signer_lock: SignerLock | None = None) -> "BlockchainConnector":
        return cls(
            rpc_url=config.rpc_url,
            private_key=config.private_key,
            chain_id=config.chain_id,
            signer_lock=signer_lock,
            receipt_timeout_seconds=config.receipt_timeout_seconds,
            confirmations=config.confirmations,
        )

    def __repr__(self) -> str:
        return f"BlockchainConnector(chain_id={self.chain_id}, url=<redacted>, private_key=<redacted>)"

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
        w3 = Web3(Web3.HTTPProvider(self.url, request_kwargs={"timeout": self.rpc_timeout_seconds}))
        if not w3.is_connected():
            raise HTTPException(500, detail="Blockchain connection failed")
        if self.chain_id is not None:
            actual = w3.eth.chain_id
            if actual != self.chain_id:
                raise ChainMismatch(self.chain_id, actual)
        self.w3 = w3

    def verify_chain(self) -> None:
        """Connects and validates the RPC's chain ID against the configured one."""
        self._get_web3()

    # -- transaction building -------------------------------------------------

    def _fee_fields(self, w3: Web3) -> dict:
        latest = w3.eth.get_block("latest")
        base_fee = latest.get("baseFeePerGas")
        if base_fee is None:
            return {"gasPrice": w3.eth.gas_price}

        try:
            priority_fee = w3.eth.max_priority_fee
        except Exception:
            history = w3.eth.fee_history(FEE_HISTORY_BLOCKS, "latest", [FEE_HISTORY_REWARD_PERCENTILE])
            rewards = sorted(r[0] for r in history["reward"])
            priority_fee = rewards[len(rewards) // 2]

        return {
            "maxFeePerGas": base_fee * BASE_FEE_MULTIPLIER + priority_fee,
            "maxPriorityFeePerGas": priority_fee,
        }

    def build_transaction(self, w3: Web3, address: str, payload_hex: str, nonce: int) -> dict:
        tx = {
            "from": address,
            "to": address,
            "value": 0,
            "data": payload_hex,
            "nonce": nonce,
            "chainId": w3.eth.chain_id,
        }
        estimated = w3.eth.estimate_gas({k: tx[k] for k in ("from", "to", "value", "data")})
        tx["gas"] = estimated * (100 + GAS_ESTIMATE_BUFFER_PERCENT) // 100
        tx.update(self._fee_fields(w3))

        max_unit_price = tx.get("maxFeePerGas", tx.get("gasPrice"))
        if w3.eth.get_balance(address) < tx["gas"] * max_unit_price:
            raise InsufficientFunds()
        return tx

    def submit_anchor(self, value: str) -> AnchorSubmission:
        w3 = self._get_web3()
        acct = self._get_account()
        payload_hex = Web3.to_hex(VALUE_PREFIX + value.encode("utf-8"))

        lock = self.signer_lock(self.chain_id or w3.eth.chain_id, acct.address) if self.signer_lock else nullcontext()
        with lock:
            nonce = w3.eth.get_transaction_count(acct.address, "pending")
            tx = self.build_transaction(w3, acct.address, payload_hex, nonce)
            signed = acct.sign_transaction(tx)
            try:
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            except Exception as exc:
                logger.error("Anchor broadcast failed (%s) for wallet %s", type(exc).__name__, acct.address)
                raise AnchorBroadcastFailed() from exc

        return AnchorSubmission(tx_hash=tx_hash.hex(), wallet=acct.address, nonce=nonce, chain_id=self.chain_id)

    def create_transaction(self, value: str) -> str:
        return self.submit_anchor(value).tx_hash

    # -- receipts -------------------------------------------------------------

    def _receipt_from_raw(self, w3: Web3, raw) -> AnchorReceipt:
        block_number = raw["blockNumber"]
        latest = w3.eth.block_number
        block_hash = raw.get("blockHash")
        return AnchorReceipt(
            success=raw.get("status", 1) == 1,
            block_number=block_number,
            block_hash=block_hash.hex() if block_hash is not None else None,
            gas_used=raw.get("gasUsed"),
            effective_gas_price=raw.get("effectiveGasPrice"),
            confirmations=max(latest - block_number + 1, 0),
        )

    def wait_for_anchor_receipt(self, tx_hash: str, timeout_seconds: int | None = None) -> AnchorReceipt:
        w3 = self._get_web3()
        try:
            raw = w3.eth.wait_for_transaction_receipt(
                HexBytes(tx_hash),
                timeout=timeout_seconds or self.receipt_timeout_seconds,
                poll_latency=DEFAULT_RECEIPT_POLL_SECONDS,
            )
        except TimeExhausted as exc:
            raise ReceiptTimeout() from exc
        return self._receipt_from_raw(w3, raw)

    def get_anchor_receipt(self, tx_hash: str) -> AnchorReceipt | None:
        """Single non-blocking receipt lookup; None while the transaction is unmined."""
        w3 = self._get_web3()
        try:
            raw = w3.eth.get_transaction_receipt(HexBytes(tx_hash))
        except TransactionNotFound:
            return None
        if raw is None or raw.get("blockNumber") is None:
            return None
        return self._receipt_from_raw(w3, raw)

    def wait_for_receipt(self, tx_hash: str) -> int:
        return self.wait_for_anchor_receipt(tx_hash).block_number

    # -- reading --------------------------------------------------------------

    def read_transaction_value(self, tx_hash: str) -> dict:
        w3 = self._get_web3()
        try:
            onchain_tx = w3.eth.get_transaction(HexBytes(tx_hash))
        except TransactionNotFound as exc:
            raise AnchorTransactionNotFound() from exc
        input_bytes = onchain_tx.get("input")
        if input_bytes is None or not input_bytes.startswith(VALUE_PREFIX):
            raise UnexpectedPayload()

        block_number = onchain_tx.get("blockNumber")
        if block_number is None:
            raise AnchorPending()

        block = w3.eth.get_block(block_number)
        timestamp = block.get("timestamp")
        if timestamp is None:
            raise AnchorPending()

        try:
            value = input_bytes[len(VALUE_PREFIX) :].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnexpectedPayload() from exc

        sender = onchain_tx.get("from")
        utc_date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return {
            "value": value,
            "timestamp": utc_date,
            "from": str(sender).lower() if sender else None,
            "block_number": block_number,
        }


# Backward-compatible alias for older imports.
Blockchain_connector = BlockchainConnector
