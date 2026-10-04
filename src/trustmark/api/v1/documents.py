from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

import logging

from trustmark.infra.auth.keycloak import Principal, require_roles
from trustmark.infra.blockchain_connector import (
    ANCHOR_TYPE,
    AnchorPending,
    AnchorTransactionNotFound,
    BlockchainConnector,
    ChainConfig,
    ChainConfigError,
    UnexpectedPayload,
)
from trustmark.infra.commons import settings
from trustmark.infra.db import get_db
from trustmark.infra.hash_engine import generate_hash_from_bytes
from trustmark.infra.nonce_lock import DbNonceStore, signer_lock
from trustmark.registry.models import (
    ANCHOR_FAILED,
    ANCHOR_PENDING,
    RegistryRecord,
)
from trustmark.services.anchoring import (
    apply_receipt,
    chain_metadata,
    is_verifiable_status,
    refresh_pending,
)

logger = logging.getLogger(__name__)

router_upload = APIRouter()
router_verify = APIRouter()


def _get_blockchain_service() -> BlockchainConnector:
    return BlockchainConnector.from_config(
        ChainConfig.from_env(), signer_lock=signer_lock, nonce_store=DbNonceStore()
    )


def _chain_config_or_none() -> ChainConfig | None:
    try:
        return ChainConfig.from_env()
    except ChainConfigError:
        return None


def _max_upload_bytes() -> int:
    return int(settings.get("MAX_UPLOAD_BYTES", 20 * 1024 * 1024))


MAX_FILENAME_LENGTH = 255  # matches registry_records.original_filename column (VARCHAR(255))


def _validate_filename(filename: str | None) -> None:
    if filename and len(filename) > MAX_FILENAME_LENGTH:
        raise HTTPException(
            400,
            detail=f"Filename exceeds the maximum length of {MAX_FILENAME_LENGTH} characters",
        )


async def _read_upload(file: UploadFile) -> bytes:
    content = await file.read()
    if not content:
        raise HTTPException(400, detail="Uploaded file is empty")
    if len(content) > _max_upload_bytes():
        raise HTTPException(413, detail="Uploaded file exceeds the configured size limit")
    return content


def _record_payload(record: RegistryRecord, config: ChainConfig | None = None) -> dict:
    return {
        "record_id": record.id,
        "content_hash": record.content_hash,
        "transaction_hash": record.transaction_hash,
        "issuer_id": record.issuer_id,
        "filename": record.original_filename,
        "content_type": record.content_type,
        "created_at": str(record.created_at),
        "anchor_status": record.anchor_status,
        "anchor": chain_metadata(record, config or _chain_config_or_none()),
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _broadcast_and_confirm(record: RegistryRecord | None, content_hash: str, issuer_id: str, file: UploadFile,
                                 connector: BlockchainConnector, db: Session) -> RegistryRecord:
    """Broadcasts the anchor, persists the record as pending right away (so a broadcast
    transaction hash is never lost), then waits a bounded time for the receipt."""
    submission = await run_in_threadpool(connector.submit_anchor, content_hash)

    if record is None:
        record = RegistryRecord(
            content_hash=content_hash,
            issuer_id=issuer_id,
            original_filename=file.filename,
            content_type=file.content_type,
        )
        db.add(record)
    record.transaction_hash = submission.tx_hash
    record.chain_id = connector.chain_id
    record.anchor_type = ANCHOR_TYPE
    record.anchor_status = ANCHOR_PENDING
    record.anchor_wallet = submission.wallet
    record.submitted_at = _utcnow()
    record.confirmed_at = None
    db.commit()

    try:
        receipt = await run_in_threadpool(connector.wait_for_anchor_receipt, submission.tx_hash)
    except Exception as exc:
        logger.warning("Anchor %s not confirmed within the wait window (%s); left pending", submission.tx_hash,
                       type(exc).__name__)
        receipt = None
    apply_receipt(record, receipt, connector.confirmations)
    db.commit()
    db.refresh(record)
    return record


@router_upload.post("/register")
async def register(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_roles("publisher")),
    db: Session = Depends(get_db),
):
    _validate_filename(file.filename)
    content = await _read_upload(file)
    content_hash = generate_hash_from_bytes(content)

    existing = db.query(RegistryRecord).filter_by(content_hash=content_hash).first()
    if existing and existing.anchor_status != ANCHOR_FAILED:
        if existing.anchor_status == ANCHOR_PENDING:
            existing = await run_in_threadpool(refresh_pending, existing, _get_blockchain_service(), db)
        return {"stored": True, "already_existed": True, **_record_payload(existing)}

    connector = _get_blockchain_service()
    record = await _broadcast_and_confirm(existing, content_hash, principal.sub, file, connector, db)
    return {"stored": True, "already_existed": False, **_record_payload(record)}


@router_upload.get("/records")
def list_records(
    principal: Principal = Depends(require_roles("publisher")),
    db: Session = Depends(get_db),
):
    records = (
        db.query(RegistryRecord)
        .filter_by(issuer_id=principal.sub)
        .order_by(RegistryRecord.created_at.desc())
        .all()
    )
    config = _chain_config_or_none()
    return {"records": [_record_payload(record, config) for record in records]}


def _verify_record(existing: RegistryRecord, db: Session) -> dict:
    """Checks a registered record against its own chain. Anything other than a confirmed,
    matching on-chain anchor is reported as valid=False with an explicit verification_status
    instead of an error, so clients never mistake 'cannot verify here' for 'verified'."""
    connector = _get_blockchain_service()
    base = {
        "content_hash": existing.content_hash,
        "record_id": existing.id,
        "anchor_status": existing.anchor_status,
        "anchor": chain_metadata(existing, _chain_config_or_none()),
    }

    def outcome(status: str) -> dict:
        return {"valid": False, **base, "verification_status": status}

    if existing.chain_id != connector.chain_id:
        return outcome("chain_not_available")
    if existing.anchor_status == ANCHOR_PENDING:
        existing = refresh_pending(existing, connector, db)
        base["anchor_status"] = existing.anchor_status
        base["anchor"] = chain_metadata(existing, _chain_config_or_none())
    if existing.anchor_status == ANCHOR_PENDING:
        return outcome("pending")
    if not is_verifiable_status(existing.anchor_status):
        return outcome("anchor_failed")

    try:
        onchain_value = connector.read_transaction_value(existing.transaction_hash)
    except AnchorTransactionNotFound:
        return outcome("transaction_not_found")
    except AnchorPending:
        return outcome("pending")
    except UnexpectedPayload:
        return outcome("invalid_anchor_payload")

    if onchain_value["value"] != existing.content_hash:
        return outcome("hash_mismatch")
    onchain_sender = onchain_value.get("from")
    if existing.anchor_wallet and onchain_sender and onchain_sender != existing.anchor_wallet.lower():
        return outcome("wallet_mismatch")

    return {
        "valid": True,
        **_record_payload(existing),
        "verification_status": "confirmed",
        "blockchain_timestamp": str(onchain_value["timestamp"]),
    }


@router_verify.post("/verify")
async def verify_full(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await _read_upload(file)
    content_hash = generate_hash_from_bytes(content)

    existing = db.query(RegistryRecord).filter_by(content_hash=content_hash).first()
    if not existing:
        return {"valid": False, "content_hash": content_hash}
    return await run_in_threadpool(_verify_record, existing, db)


@router_verify.get("/verify/{file_hash}")
def verify_by_hash(file_hash: str, db: Session = Depends(get_db)):
    content_hash = file_hash.lower().strip()
    if len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
        raise HTTPException(400, detail="file_hash must be a 64-character SHA-256 hexadecimal value")

    existing = db.query(RegistryRecord).filter_by(content_hash=content_hash).first()
    if not existing:
        return {"valid": False, "content_hash": content_hash}
    return _verify_record(existing, db)
