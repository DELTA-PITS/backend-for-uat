"""
Explicit-migration policy for registry_records: startup only CHECKS the schema; changing it is a
separate, deliberate command. Also guards the SQL migration file against drifting from the models and
the rehearsal-database pin.

Run with:
    python -m pytest tests/trustmark/test_schema_migration.py
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import StaticPool

from trustmark import main
from trustmark.infra import schema
from trustmark.infra.db import Base
from trustmark.registry import models  # noqa: F401  (registers the model)

LEGACY_COLUMNS = {"id", "content_hash", "transaction_hash", "issuer_id", "original_filename", "content_type",
                  "created_at"}


def sqlite():
    return create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


def legacy_engine():
    engine = sqlite()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE registry_records (id VARCHAR(36) PRIMARY KEY, content_hash VARCHAR(64) NOT NULL, "
            "transaction_hash VARCHAR(66) NOT NULL, issuer_id VARCHAR(128) NOT NULL, original_filename VARCHAR(255), "
            "content_type VARCHAR(128), created_at DATETIME NOT NULL)"))
        conn.execute(text("INSERT INTO registry_records VALUES ('r1','h','t','u','f','c','2026-09-01 10:00:00')"))
    return engine


class TestCheckIsReadOnly:
    def test_check_reports_missing_columns_without_changing_anything(self):
        engine = legacy_engine()
        with pytest.raises(schema.SchemaOutOfDate, match="chain_id") as exc:
            schema.check_schema(engine)
        assert "python -m trustmark.infra.schema migrate" in str(exc.value)
        assert "chain_id" not in {c["name"] for c in __import__("sqlalchemy").inspect(engine).get_columns("registry_records")}

    def test_check_reports_missing_table(self):
        assert schema.missing_schema(sqlite()) == ["registry_records"]

    def test_check_passes_after_migrate(self):
        engine = legacy_engine()
        schema.migrate(engine)
        schema.check_schema(engine)


class TestStartupPolicy:
    def run_lifespan_schema_step(self, monkeypatch, engine, auto):
        monkeypatch.setattr(main, "engine", engine)
        monkeypatch.setattr(main, "_check_blockchain_chain", lambda: None)
        if auto is None:
            monkeypatch.delenv("SCHEMA_AUTO_APPLY", raising=False)
        else:
            monkeypatch.setenv("SCHEMA_AUTO_APPLY", auto)

        import asyncio

        async def go():
            async with main.lifespan(main.app):
                pass

        asyncio.run(go())

    def test_default_startup_refuses_outdated_schema_and_does_not_alter_it(self, monkeypatch):
        engine = legacy_engine()
        with pytest.raises(schema.SchemaOutOfDate):
            self.run_lifespan_schema_step(monkeypatch, engine, auto=None)
        assert schema.missing_schema(engine)  # still outdated: nothing was mutated

    def test_explicit_opt_in_migrates_on_startup(self, monkeypatch):
        engine = legacy_engine()
        self.run_lifespan_schema_step(monkeypatch, engine, auto="true")
        assert schema.missing_schema(engine) == []

    def test_startup_with_current_schema_is_a_no_op(self, monkeypatch):
        engine = sqlite()
        schema.migrate(engine)
        self.run_lifespan_schema_step(monkeypatch, engine, auto=None)


class TestMigrate:
    def test_adds_columns_keeps_rows_backfills_legacy_defaults(self):
        engine = legacy_engine()
        added = schema.migrate(engine)
        assert {"registry_records.chain_id", "registry_records.anchor_status"} <= set(added)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT chain_id, anchor_status, anchor_type, submitted_at, confirmed_at FROM registry_records")).one()
        assert (row.chain_id, row.anchor_status, row.anchor_type) == (31337, "legacy_local", "calldata-v1")
        assert str(row.submitted_at).startswith("2026-09-01") and row.confirmed_at is None

    def test_idempotent(self):
        engine = legacy_engine()
        schema.migrate(engine)
        assert schema.migrate(engine) == []

    def test_cli_check_exit_codes(self, monkeypatch, capsys):
        engine = legacy_engine()
        import trustmark.infra.db as db

        monkeypatch.setattr(db, "engine", engine)
        assert schema.main(["check"]) == 1
        assert schema.main(["migrate"]) == 0
        assert schema.main(["check"]) == 0
        assert schema.main(["bogus"]) == 2


class TestSqlFileMatchesModels:
    """The PostgreSQL path runs the .sql file, the SQLite path derives DDL from the models; this keeps them equal."""

    PATTERN = re.compile(
        r"ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS (\w+) ([A-Z0-9()]+)( NOT NULL)?(?: DEFAULT '?([^';]+)'?)?$")
    TYPE_ALIASES = {"TIMESTAMPTZ": "TIMESTAMP WITH TIME ZONE"}

    def sql_columns(self):
        found = {}
        for statement in schema.migration_statements():
            match = self.PATTERN.match(statement)
            if match:
                name, type_, not_null, default = match.groups()
                found[name] = (self.TYPE_ALIASES.get(type_, type_), bool(not_null), default)
        return found

    def test_every_new_model_column_is_in_the_sql_with_same_type_nullability_default(self):
        table = Base.metadata.tables["registry_records"]
        expected = {}
        for column in table.columns:
            if column.name in LEGACY_COLUMNS:
                continue
            default = column.server_default.arg if column.server_default is not None else None
            expected[column.name] = (column.type.compile(postgresql.dialect()), not column.nullable,
                                     str(default) if default is not None else None)
        assert self.sql_columns() == expected

    def test_sql_is_idempotent_by_construction(self):
        for statement in schema.migration_statements():
            if statement.startswith("ALTER TABLE"):
                assert "IF NOT EXISTS" in statement
            else:
                assert statement.startswith("UPDATE registry_records SET submitted_at = created_at WHERE submitted_at IS NULL")


class TestRehearsalDatabasePin:
    def test_pin_blocks_wrong_database(self, monkeypatch):
        monkeypatch.setattr(main, "engine", create_engine("postgresql+psycopg2://u:p@localhost/trustmark"))
        monkeypatch.setenv("REQUIRE_DATABASE_NAME", "trustmark_sepolia")
        with pytest.raises(RuntimeError, match="trustmark_sepolia"):
            main._check_database_name()

    def test_pin_allows_matching_database(self, monkeypatch):
        monkeypatch.setattr(main, "engine", create_engine("postgresql+psycopg2://u:p@localhost/trustmark_sepolia"))
        monkeypatch.setenv("REQUIRE_DATABASE_NAME", "trustmark_sepolia")
        main._check_database_name()

    def test_no_pin_means_no_restriction(self, monkeypatch):
        monkeypatch.setattr(main, "engine", create_engine("postgresql+psycopg2://u:p@localhost/trustmark"))
        monkeypatch.delenv("REQUIRE_DATABASE_NAME", raising=False)
        main._check_database_name()


def test_schema_module_alone_registers_the_tables():
    """Regression (found on real PostgreSQL in Phase 2): the CLI imports only trustmark.infra.schema, so
    the model must be registered by that import - otherwise create_all/check silently see an empty schema."""
    import subprocess
    import sys

    code = ("import os; os.environ.setdefault('DATABASE_URL','sqlite://');"
            "from trustmark.infra import schema; from trustmark.infra.db import Base;"
            "print(sorted(Base.metadata.tables))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={**__import__("os").environ, "DATABASE_URL": "sqlite://"})
    assert "registry_records" in out.stdout
