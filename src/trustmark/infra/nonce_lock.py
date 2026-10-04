import hashlib
import threading
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.engine import Engine

LOCK_WAIT_TIMEOUT_SECONDS = 30

_process_locks: dict[int, threading.Lock] = {}
_process_locks_guard = threading.Lock()


def signer_lock_key(chain_id: int, address: str) -> int:
    """Stable signed 64-bit key (the range pg_advisory_lock accepts) per (chain, signer)."""
    digest = hashlib.sha256(f"{chain_id}:{address.lower()}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def signer_lock(chain_id: int, address: str, engine: Engine | None = None):
    """Serialises nonce selection + broadcast for one signer on one chain.

    PostgreSQL: a transaction-scoped advisory lock on a dedicated connection, released
    on commit/rollback (or automatically if the connection dies), so it also protects
    across several backend processes/containers sharing the database.
    Other dialects (SQLite dev/test): a process-local lock.
    """
    if engine is None:
        from trustmark.infra.db import engine as default_engine

        engine = default_engine

    key = signer_lock_key(chain_id, address)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_WAIT_TIMEOUT_SECONDS}s'"))
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            yield
        return

    with _process_locks_guard:
        lock = _process_locks.setdefault(key, threading.Lock())
    with lock:
        yield


class DbNonceStore:
    """NonceStore backed by the anchor_signer_nonces table. Callers hold signer_lock around get/put, so the
    read-modify-write needs no extra concurrency control."""

    def __init__(self, engine: Engine | None = None):
        self._engine = engine

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            from trustmark.infra.db import engine as default_engine

            self._engine = default_engine
        return self._engine

    def get_floor(self, chain_id: int, address: str, ttl_seconds: int) -> int | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT next_nonce, updated_at FROM anchor_signer_nonces WHERE chain_id=:c AND address=:a"),
                {"c": chain_id, "a": address.lower()},
            ).first()
        if row is None:
            return None
        updated_at = row.updated_at
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - updated_at > timedelta(seconds=ttl_seconds):
            return None
        return int(row.next_nonce)

    def put_next(self, chain_id: int, address: str, next_nonce: int) -> None:
        params = {"c": chain_id, "a": address.lower(), "n": next_nonce, "t": datetime.now(timezone.utc)}
        with self.engine.begin() as conn:
            updated = conn.execute(
                text("UPDATE anchor_signer_nonces SET next_nonce=:n, updated_at=:t WHERE chain_id=:c AND address=:a")
                .bindparams(bindparam("t", type_=DateTime(timezone=True))),
                params,
            ).rowcount
            if not updated:
                conn.execute(
                    text("INSERT INTO anchor_signer_nonces (chain_id, address, next_nonce, updated_at) "
                         "VALUES (:c, :a, :n, :t)").bindparams(bindparam("t", type_=DateTime(timezone=True))),
                    params,
                )
