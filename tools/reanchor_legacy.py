"""
Re-anchor legacy registry records on the chain the backend is configured for (one-off recovery tool).

Reads registry_records from a SOURCE database (read-only transaction, never modified) and, for every record whose
content_hash is not yet in the TARGET database (DATABASE_URL), copies it with its original id / filename / issuer /
created_at, anchors its SHA-256 on the configured chain and stores the receipt metadata. The new blockchain timestamp is
the re-anchoring time, so these rows are marked anchor_type = "calldata-v1-reanchored"; the original registration time
stays in created_at. Idempotent: re-running skips hashes that already exist in the target.

Usage (inside the backend image, with the backend's BLOCKCHAIN_* and DATABASE_URL env):
    SRC_DATABASE_URL=postgresql+psycopg2://... python tools/reanchor_legacy.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

REANCHOR_TYPE = "calldata-v1-reanchored"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    src_url = os.environ.get("SRC_DATABASE_URL")
    if not src_url:
        print("SRC_DATABASE_URL is required", file=sys.stderr)
        return 2

    from trustmark.infra.blockchain_connector import ChainConfig, BlockchainConnector
    from trustmark.infra.db import SessionLocal, engine as target_engine
    from trustmark.infra.nonce_lock import DbNonceStore, signer_lock
    from trustmark.registry.models import ANCHOR_PENDING, RegistryRecord
    from trustmark.services.anchoring import apply_receipt

    if src_url.split("/")[-1].split("?")[0] == str(target_engine.url.database):
        print("source and target database are the same - refusing", file=sys.stderr)
        return 2

    config = ChainConfig.from_env()
    src_engine = create_engine(src_url)
    with src_engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = conn.execute(text(
            "SELECT id, content_hash, issuer_id, original_filename, content_type, created_at "
            "FROM registry_records ORDER BY created_at")).mappings().all()

    db = SessionLocal()
    existing = {h for (h,) in db.query(RegistryRecord.content_hash).all()}
    todo = [r for r in rows if r["content_hash"] not in existing]
    if args.limit:
        todo = todo[: args.limit]
    print(f"source records={len(rows)} already in target={len(rows) - len([r for r in rows if r['content_hash'] not in existing])} "
          f"to anchor={len(todo)} chain={config.chain_id} ({config.network_name})", flush=True)
    if args.dry_run:
        for r in todo[:5]:
            print("  would anchor", r["content_hash"][:16], r["original_filename"], r["created_at"])
        return 0

    connector = BlockchainConnector.from_config(config, signer_lock=signer_lock, nonce_store=DbNonceStore())
    connector.verify_chain()
    ok = failed = 0
    for i, r in enumerate(todo, 1):
        started = time.monotonic()
        try:
            submission = connector.submit_anchor(r["content_hash"])
            record = RegistryRecord(
                id=r["id"], content_hash=r["content_hash"], issuer_id=r["issuer_id"],
                original_filename=r["original_filename"], content_type=r["content_type"], created_at=r["created_at"],
                transaction_hash=submission.tx_hash, chain_id=config.chain_id, anchor_type=REANCHOR_TYPE,
                anchor_status=ANCHOR_PENDING, anchor_wallet=submission.wallet,
                submitted_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            )
            db.add(record)
            db.commit()
            apply_receipt(record, connector.wait_for_anchor_receipt(submission.tx_hash), connector.confirmations)
            db.commit()
            ok += record.anchor_status == "confirmed"
            print(f"[{i}/{len(todo)}] {r['content_hash'][:12]} -> {record.anchor_status} block={record.block_number} "
                  f"({time.monotonic() - started:.1f}s)", flush=True)
        except Exception as exc:
            db.rollback()
            failed += 1
            print(f"[{i}/{len(todo)}] {r['content_hash'][:12]} FAILED {type(exc).__name__}: {str(exc)[:120]}", flush=True)
    print(f"done: confirmed={ok} failed={failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
