"""
Restore production verification after the Anvil chain was reset: re-anchor each registry record's SHA-256 on the CURRENT
chain and point the record's transaction_hash at the new transaction.

Only records whose stored transaction can no longer be read from the chain are touched (idempotent). The only column
changed is registry_records.transaction_hash; the old->new mapping is appended to --mapping so the change is auditable and
reversible. The new on-chain timestamp is the re-anchoring time; created_at in the database is unchanged.

Run inside the backend image with the PRODUCTION environment (DATABASE_URL -> tdb, BLOCKCHAIN_RPC_URL/PRIVATE_KEY of Anvil)
plus BLOCKCHAIN_CHAIN_ID=31337:
    python tools/reanchor_to_anvil.py --mapping /out/mapping.csv [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import sys
import time

from sqlalchemy import text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    from trustmark.infra.blockchain_connector import AnchorTransactionNotFound, BlockchainConnector, ChainConfig
    from trustmark.infra.db import engine

    config = ChainConfig.from_env()
    connector = BlockchainConnector.from_config(config)  # Anvil mines instantly: no lock / nonce floor needed
    connector.verify_chain()

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, content_hash, transaction_hash FROM registry_records ORDER BY created_at")).mappings().all()

    def readable(tx_hash: str, content_hash: str) -> bool:
        try:
            return connector.read_transaction_value(tx_hash)["value"] == content_hash
        except AnchorTransactionNotFound:
            return False

    todo = [r for r in rows if not readable(r["transaction_hash"], r["content_hash"])]
    if args.limit:
        todo = todo[: args.limit]
    print(f"records={len(rows)} need re-anchoring={len(todo)} chain={config.chain_id}", flush=True)
    if args.dry_run:
        for r in todo[:3]:
            print("  would re-anchor", r["content_hash"][:16], "old tx", r["transaction_hash"][:12])
        return 0

    done = failed = 0
    with open(args.mapping, "a", newline="") as fh:
        writer = csv.writer(fh)
        for i, r in enumerate(todo, 1):
            started = time.monotonic()
            try:
                submission = connector.submit_anchor(r["content_hash"])
                receipt = connector.wait_for_anchor_receipt(submission.tx_hash)
                if not receipt.success:
                    raise RuntimeError("receipt status 0")
                with engine.begin() as conn:
                    updated = conn.execute(
                        text("UPDATE registry_records SET transaction_hash=:new WHERE id=:id AND transaction_hash=:old"),
                        {"new": submission.tx_hash, "id": r["id"], "old": r["transaction_hash"]},
                    ).rowcount
                if updated != 1:
                    raise RuntimeError(f"update affected {updated} rows")
                writer.writerow([r["id"], r["content_hash"], r["transaction_hash"], submission.tx_hash, receipt.block_number])
                fh.flush()
                done += 1
                print(f"[{i}/{len(todo)}] {r['content_hash'][:12]} -> block {receipt.block_number} ({time.monotonic() - started:.1f}s)", flush=True)
            except Exception as exc:
                failed += 1
                print(f"[{i}/{len(todo)}] {r['content_hash'][:12]} FAILED {type(exc).__name__}: {str(exc)[:100]}", flush=True)
    print(f"done: updated={done} failed={failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
