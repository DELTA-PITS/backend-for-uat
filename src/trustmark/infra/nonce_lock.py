import hashlib
import threading
from contextlib import contextmanager

from sqlalchemy import text
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
