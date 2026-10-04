import uuid

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from trustmark.infra.db import Base

LEGACY_LOCAL_CHAIN_ID = 31337

ANCHOR_PENDING = "pending"
ANCHOR_CONFIRMED = "confirmed"
ANCHOR_FAILED = "failed"
ANCHOR_LEGACY_LOCAL = "legacy_local"


class RegistryRecord(Base):
    __tablename__ = "registry_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    transaction_hash: Mapped[str] = mapped_column(String(66), unique=True, nullable=False)
    issuer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Anchor metadata. The server defaults describe rows written before chain tracking
    # existed (all anchored on the local Anvil chain); new rows always set these explicitly.
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default=str(LEGACY_LOCAL_CHAIN_ID))
    anchor_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="calldata-v1")
    anchor_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=ANCHOR_LEGACY_LOCAL)
    anchor_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    block_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    gas_used: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    effective_gas_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    submitted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnchorSignerNonce(Base):
    """Last nonce handed out per (chain, signer) - a floor against stale pending-nonce reads on public RPC."""

    __tablename__ = "anchor_signer_nonces"

    chain_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(42), primary_key=True)
    next_nonce: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
