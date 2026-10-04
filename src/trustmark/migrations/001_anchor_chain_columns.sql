-- 001: chain-aware anchors for registry_records (additive, idempotent, no DROP / no rewrite).
-- Run explicitly at deployment, after a pg_dump, with:
--     python -m trustmark.infra.schema migrate
-- (runs this file in a single transaction under an advisory lock). Existing rows - all anchored on the
-- local Anvil chain - receive chain_id=31337 / anchor_status=legacy_local; submitted_at is backfilled
-- from created_at. tests/trustmark/test_schema_migration.py fails if this file drifts from the models.
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS chain_id INTEGER NOT NULL DEFAULT 31337;
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS anchor_type VARCHAR(32) NOT NULL DEFAULT 'calldata-v1';
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS anchor_status VARCHAR(16) NOT NULL DEFAULT 'legacy_local';
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS anchor_wallet VARCHAR(42);
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS block_number BIGINT;
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS block_hash VARCHAR(66);
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS gas_used BIGINT;
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS effective_gas_price BIGINT;
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;
ALTER TABLE registry_records ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
UPDATE registry_records SET submitted_at = created_at WHERE submitted_at IS NULL;
