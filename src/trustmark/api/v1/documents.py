from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from trustmark.infra.auth.keycloak import Principal, require_roles
from trustmark.infra.blockchain_connector import BlockchainConnector
from trustmark.infra.commons import settings
from trustmark.infra.db import get_db
from trustmark.infra.hash_engine import generate_hash_from_bytes
from trustmark.registry.models import RegistryRecord

router_upload = APIRouter()
router_verify = APIRouter()


def _get_blockchain_service() -> BlockchainConnector:
    rpc_url = settings.get("BLOCKCHAIN_RPC_URL")
    private_key = settings.get("BLOCKCHAIN_PRIVATE_KEY")
    if not rpc_url or not private_key:
        raise HTTPException(500, detail="Blockchain settings are not configured")
    return BlockchainConnector(rpc_url=str(rpc_url), private_key=str(private_key))


def _max_upload_bytes() -> int:
    return int(settings.get("MAX_UPLOAD_BYTES", 20 * 1024 * 1024))


async def _read_upload(file: UploadFile) -> bytes:
    content = await file.read()
    if not content:
        raise HTTPException(400, detail="Uploaded file is empty")
    if len(content) > _max_upload_bytes():
        raise HTTPException(413, detail="Uploaded file exceeds the configured size limit")
    return content


def _record_payload(record: RegistryRecord) -> dict:
    return {
        "record_id": record.id,
        "content_hash": record.content_hash,
        "transaction_hash": record.transaction_hash,
        "issuer_id": record.issuer_id,
        "filename": record.original_filename,
        "content_type": record.content_type,
        "created_at": str(record.created_at),
    }


@router_upload.post("/register")
async def register(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_roles("publisher")),
    db: Session = Depends(get_db),
):
    content = await _read_upload(file)
    content_hash = generate_hash_from_bytes(content)

    existing = db.query(RegistryRecord).filter_by(content_hash=content_hash).first()
    if existing:
        return {"stored": True, "already_existed": True, **_record_payload(existing)}

    blockchain_service = _get_blockchain_service()
    tx_hash = blockchain_service.create_transaction(content_hash)
    blockchain_service.wait_for_receipt(tx_hash)

    record = RegistryRecord(
        content_hash=content_hash,
        transaction_hash=tx_hash,
        issuer_id=principal.sub,
        original_filename=file.filename,
        content_type=file.content_type,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {"stored": True, "already_existed": False, **_record_payload(record)}


@router_upload.get("/records")
def list_records(
    principal: Principal = Depends(require_roles("publisher")),
    db: Session = Depends(get_db),
):
    records = db.query(RegistryRecord).order_by(RegistryRecord.created_at.desc()).all()
    return {"records": [_record_payload(record) for record in records]}


@router_verify.post("/verify")
async def verify_full(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await _read_upload(file)
    content_hash = generate_hash_from_bytes(content)

    existing = db.query(RegistryRecord).filter_by(content_hash=content_hash).first()
    if not existing:
        return {"valid": False, "content_hash": content_hash}

    blockchain_service = _get_blockchain_service()
    onchain_value = blockchain_service.read_transaction_value(existing.transaction_hash)
    if onchain_value["value"] != existing.content_hash:
        return {"valid": False, "content_hash": content_hash, "record_id": existing.id}

    return {
        "valid": True,
        **_record_payload(existing),
        "blockchain_timestamp": str(onchain_value["timestamp"]),
    }


@router_verify.get("/verify/{file_hash}")
def verify_by_hash(file_hash: str, db: Session = Depends(get_db)):
    content_hash = file_hash.lower().strip()
    if len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
        raise HTTPException(400, detail="file_hash must be a 64-character SHA-256 hexadecimal value")

    existing = db.query(RegistryRecord).filter_by(content_hash=content_hash).first()
    if not existing:
        return {"valid": False, "content_hash": content_hash}

    blockchain_service = _get_blockchain_service()
    onchain_value = blockchain_service.read_transaction_value(existing.transaction_hash)
    if onchain_value["value"] != existing.content_hash:
        return {"valid": False, "content_hash": content_hash, "record_id": existing.id}

    return {
        "valid": True,
        **_record_payload(existing),
        "blockchain_timestamp": str(onchain_value["timestamp"]),
    }
