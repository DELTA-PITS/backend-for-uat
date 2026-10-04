import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from trustmark.infra.blockchain_connector import (
    AnchorReceipt,
    BlockchainConnector,
    ChainConfig,
    explorer_tx_url,
)
from trustmark.registry.models import (
    ANCHOR_CONFIRMED,
    ANCHOR_FAILED,
    ANCHOR_LEGACY_LOCAL,
    ANCHOR_PENDING,
    LEGACY_LOCAL_CHAIN_ID,
    RegistryRecord,
)

logger = logging.getLogger(__name__)

LEGACY_LOCAL_NETWORK_NAME = "Local development chain (Anvil)"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def chain_metadata(record: RegistryRecord, config: ChainConfig | None) -> dict:
    """Network/explorer description for the chain a record was anchored on.

    Name and explorer come from the active configuration when the record belongs to the
    configured chain; records from any other chain get a neutral description with no
    explorer link rather than a guess.
    """
    tx_hash = record.transaction_hash
    network_name, explorer_url = None, None
    if config is not None and record.chain_id == config.chain_id:
        network_name, explorer_url = config.network_name, config.explorer_url
    if network_name is None and record.chain_id == LEGACY_LOCAL_CHAIN_ID:
        network_name = LEGACY_LOCAL_NETWORK_NAME

    return {
        "chain_id": record.chain_id,
        "network_name": network_name or f"Chain {record.chain_id}",
        "anchor_type": record.anchor_type,
        "anchor_status": record.anchor_status,
        "transaction_hash": tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}",
        "explorer_url": explorer_url,
        "explorer_tx_url": explorer_tx_url(explorer_url, tx_hash),
        "block_number": record.block_number,
        "submitted_at": str(record.submitted_at) if record.submitted_at else None,
        "confirmed_at": str(record.confirmed_at) if record.confirmed_at else None,
    }


def apply_receipt(record: RegistryRecord, receipt: AnchorReceipt | None, required_confirmations: int) -> None:
    """Moves a record along pending -> confirmed/failed from a receipt lookup.

    No receipt, or fewer than the required confirmations, leaves the record pending.
    """
    if receipt is None:
        return
    if not receipt.success:
        record.anchor_status = ANCHOR_FAILED
        record.block_number = receipt.block_number
        record.block_hash = receipt.block_hash
        record.gas_used = receipt.gas_used
        record.effective_gas_price = receipt.effective_gas_price
        return
    if receipt.confirmations < required_confirmations:
        return
    record.anchor_status = ANCHOR_CONFIRMED
    record.block_number = receipt.block_number
    record.block_hash = receipt.block_hash
    record.gas_used = receipt.gas_used
    record.effective_gas_price = receipt.effective_gas_price
    record.confirmed_at = _utcnow()


def refresh_pending(record: RegistryRecord, connector: BlockchainConnector, db: Session) -> RegistryRecord:
    """One non-blocking confirmation attempt for a pending record on the connector's chain."""
    if record.anchor_status != ANCHOR_PENDING or record.chain_id != connector.chain_id:
        return record
    apply_receipt(record, connector.get_anchor_receipt(record.transaction_hash), connector.confirmations)
    if record.anchor_status != ANCHOR_PENDING:
        db.commit()
        db.refresh(record)
    return record


def is_verifiable_status(status: str) -> bool:
    return status in (ANCHOR_CONFIRMED, ANCHOR_LEGACY_LOCAL)
