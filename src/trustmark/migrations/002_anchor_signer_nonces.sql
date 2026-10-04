-- 002: per-signer nonce floor (guards against stale pending-nonce reads on load-balanced public RPC).
-- Additive and idempotent. `python -m trustmark.infra.schema migrate` also creates it from the model.
CREATE TABLE IF NOT EXISTS anchor_signer_nonces (
    chain_id INTEGER NOT NULL,
    address VARCHAR(42) NOT NULL,
    next_nonce BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (chain_id, address)
);
