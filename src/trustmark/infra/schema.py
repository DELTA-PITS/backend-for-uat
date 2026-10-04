"""Explicit, additive schema management for registry_records.

Application startup never alters the schema by default: it only *checks* it
(`check_schema`) and refuses to start when it is out of date. Changing the schema is a
deliberate deployment step:

    python -m trustmark.infra.schema check     # read-only, exit 1 if out of date
    python -m trustmark.infra.schema migrate   # additive + idempotent, then re-checks

On PostgreSQL `migrate` runs src/trustmark/migrations/*.sql (the single source of truth) in ONE
transaction under an advisory lock, so it is all-or-nothing and safe if two runners overlap.
On other dialects (SQLite for dev/tests) the same additive change is derived from the models.
Setting SCHEMA_AUTO_APPLY=true makes startup run `migrate` itself - meant for local/CI only.
"""
import logging
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from trustmark.infra.db import Base

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION_LOCK_KEY = 7_201_004_001
MIGRATION_LOCK_TIMEOUT = "10s"


class SchemaOutOfDate(RuntimeError):
    pass


def missing_schema(engine: Engine) -> list[str]:
    """Read-only: tables/columns the models expect but the database lacks."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            missing.append(table.name)
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        missing.extend(f"{table.name}.{col.name}" for col in table.columns if col.name not in existing)
    return missing


def check_schema(engine: Engine) -> None:
    missing = missing_schema(engine)
    if missing:
        raise SchemaOutOfDate(
            "Database schema is out of date (missing: " + ", ".join(missing) + "). Back up the database, then run: "
            "python -m trustmark.infra.schema migrate"
        )


def migration_statements() -> list[str]:
    statements: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        body = "\n".join(line for line in path.read_text().splitlines() if not line.strip().startswith("--"))
        statements.extend(s.strip() for s in body.split(";") if s.strip())
    return statements


def _server_default_sql(column) -> str | None:
    default = column.server_default
    if default is None:
        return None
    return str(default.arg.text if hasattr(default.arg, "text") else default.arg)


def _migrate_from_models(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}"
            default = _server_default_sql(column)
            if default is not None:
                ddl += f" DEFAULT {default}" if default.lstrip("-").isdigit() else f" DEFAULT '{default}'"
            if not column.nullable:
                ddl += " NOT NULL"
            with engine.begin() as conn:
                conn.execute(text(ddl))
    with engine.begin() as conn:
        conn.execute(text("UPDATE registry_records SET submitted_at = created_at WHERE submitted_at IS NULL"))


def migrate(engine: Engine) -> list[str]:
    """Applies the additive migration. Returns what was missing before. Raises if still out of date after."""
    before = missing_schema(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL lock_timeout = '{MIGRATION_LOCK_TIMEOUT}'"))
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
            Base.metadata.create_all(bind=conn)
            for statement in migration_statements():
                conn.execute(text(statement))
    else:
        _migrate_from_models(engine)
    check_schema(engine)
    if before:
        logger.info("Schema migrated; previously missing: %s", ", ".join(before))
    return before


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] not in ("check", "migrate"):
        print("usage: python -m trustmark.infra.schema check|migrate", file=sys.stderr)
        return 2
    from trustmark.infra.db import engine

    if args[0] == "check":
        missing = missing_schema(engine)
        print("schema OK" if not missing else "schema OUT OF DATE, missing: " + ", ".join(missing))
        return 1 if missing else 0
    added = migrate(engine)
    print("migrated; added: " + (", ".join(added) if added else "nothing (already up to date)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
