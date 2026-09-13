"""
PASS 5 — Section 5: Real Anvil integration tests (BC-INT-01..10).

Uses the LOCAL disposable Anvil node (ghcr.io/foundry-rs/foundry:latest,
127.0.0.1:8545, well-known Anvil default test private key from docker/.env).
No mocking of web3/Anvil - every call goes through the real backend's
BlockchainConnector against a real running Anvil chain.

IMPORTANT SCOPE NOTE: This is Anvil / local Ethereum-compatible testing
only. Nothing here validates against a public blockchain network, and no
result from this section should be characterized in the paper as "verified
on Ethereum" - Anvil is a local, resettable development chain.
"""
from __future__ import annotations

import subprocess
import time

import requests
from web3 import Web3

from conftest import register, verify, unique_pdf_bytes

RPC_URL = "http://127.0.0.1:8545"


class TestBlockchainIntegration:
    def test_bc_int_01_connect_successfully(self):
        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        assert w3.is_connected()
        print(f"BC-INT-01: connected, chain_id={w3.eth.chain_id}, block={w3.eth.block_number}")

    def test_bc_int_02_and_03_register_and_mine(self, token_a):
        content = unique_pdf_bytes("BC-INT-02")
        resp = register(token_a, content)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["transaction_hash"]

        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        receipt = w3.eth.wait_for_transaction_receipt(body["transaction_hash"], timeout=30)
        print(f"BC-INT-03: tx {body['transaction_hash']} mined in block {receipt['blockNumber']}, status={receipt['status']}")
        assert receipt["status"] == 1

    def test_bc_int_04_and_05_read_transaction_value_matches_sha256(self, token_a):
        import hashlib

        content = unique_pdf_bytes("BC-INT-04")
        expected_hash = hashlib.sha256(content).hexdigest()
        resp = register(token_a, content)
        assert resp.status_code == 200
        body = resp.json()
        assert body["content_hash"] == expected_hash

        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        onchain_tx = w3.eth.get_transaction(body["transaction_hash"])
        input_bytes = onchain_tx["input"]
        value_prefix = b"VALUE:"
        assert input_bytes.startswith(value_prefix)
        onchain_value = input_bytes[len(value_prefix):].decode("utf-8")

        print(f"BC-INT-05: onchain value={onchain_value}, expected sha256={expected_hash}")
        assert onchain_value == expected_hash

    def test_bc_int_06_db_transaction_hash_maps_to_real_anvil_tx(self, token_a, db_conn):
        content = unique_pdf_bytes("BC-INT-06")
        resp = register(token_a, content)
        assert resp.status_code == 200
        body = resp.json()

        with db_conn.cursor() as cur:
            cur.execute("SELECT transaction_hash FROM registry_records WHERE id = %s", (body["record_id"],))
            db_tx_hash = cur.fetchone()[0]
        assert db_tx_hash == body["transaction_hash"]

        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        onchain_tx = w3.eth.get_transaction(db_tx_hash)
        assert onchain_tx is not None
        print(f"BC-INT-06: DB transaction_hash {db_tx_hash} resolves to a real Anvil tx in block {onchain_tx['blockNumber']}")

    def test_bc_int_07_unknown_transaction_hash(self):
        fake_tx = "0x" + "ab" * 32
        resp = requests.get(
            "http://127.0.0.1:41012/api/v1/verify/" + ("00" * 32), timeout=15
        )
        # This exercises verify_by_hash with a hash that was never registered
        # (no DB row -> never reaches Anvil at all). Documented for clarity.
        print(f"BC-INT-07: verify_by_hash for unregistered hash -> {resp.status_code} {resp.json()}")
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

        # Direct web3 call for a tx hash that was truly never broadcast:
        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        try:
            tx = w3.eth.get_transaction(fake_tx)
            print(f"BC-INT-07 (direct web3): unexpectedly found tx: {tx}")
        except Exception as e:
            print(f"BC-INT-07 (direct web3): get_transaction for unknown hash raised {type(e).__name__}: {e}")

    def test_bc_int_08_pending_transaction_handling(self):
        """Anvil in --host/--port mode (no --block-time flag set in
        docker-compose.yml) auto-mines every transaction immediately, so a
        genuinely 'pending' transaction is not reproducible without changing
        Anvil's mining mode. Documented as NOT RUN / not reproducible in this
        environment, rather than fabricated."""
        import pytest
        pytest.skip("Anvil auto-mines instantly in this compose config (no --block-time); "
                    "pending-transaction state is not reproducible without restarting Anvil "
                    "with interval mining, which was out of scope for this pass.")

    def test_bc_int_09_node_unavailable(self, token_a):
        compose_dir = "/Users/laksaersa/GitHub/_personal/_PITS/backend-for-uat/docker"
        subprocess.run(["docker", "compose", "stop", "anvil"], cwd=compose_dir, check=True, capture_output=True)
        time.sleep(2)
        try:
            resp = register(token_a, unique_pdf_bytes("BC-INT-09"))
            print(f"BC-INT-09: register() with Anvil down -> {resp.status_code}: {resp.text[:300]}")
            actual_status = resp.status_code
        except requests.exceptions.RequestException as e:
            actual_status = None
            print(f"BC-INT-09: request-level exception: {e}")
        finally:
            subprocess.run(["docker", "compose", "start", "anvil"], cwd=compose_dir, check=True, capture_output=True)
            for _ in range(20):
                try:
                    w3 = Web3(Web3.HTTPProvider(RPC_URL))
                    if w3.is_connected():
                        break
                except Exception:
                    pass
                time.sleep(2)
        print(f"BC-INT-09 FINAL status={actual_status}")

    def test_bc_int_10_anvil_restart_data_loss(self, token_a):
        """Registers a document, restarts the Anvil container (not just
        stop/start - a fresh container per docker-compose.yml has no
        persistent volume for Anvil's chain state), then attempts to read
        the same transaction back. Documents actual behaviour - Anvil is an
        in-memory dev chain by default; if state is lost, that is an
        environment/prototype limitation, not evidence of any real Ethereum
        network behaviour."""
        content = unique_pdf_bytes("BC-INT-10")
        resp = register(token_a, content)
        assert resp.status_code == 200
        tx_hash = resp.json()["transaction_hash"]

        compose_dir = "/Users/laksaersa/GitHub/_personal/_PITS/backend-for-uat/docker"
        subprocess.run(["docker", "compose", "restart", "anvil"], cwd=compose_dir, check=True, capture_output=True)
        time.sleep(3)

        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        for _ in range(15):
            if w3.is_connected():
                break
            time.sleep(2)

        try:
            tx = w3.eth.get_transaction(tx_hash)
            print(f"BC-INT-10: transaction SURVIVED Anvil container restart: {tx_hash}")
            survived = True
        except Exception as e:
            print(f"BC-INT-10: transaction {tx_hash} LOST after Anvil restart ({type(e).__name__}: {e}). "
                  f"This is expected for Anvil's default in-memory chain with no --state persistence "
                  f"flag configured in docker-compose.yml. Documented as an environment/prototype "
                  f"limitation - NOT a claim about public blockchain durability.")
            survived = False
        print(f"BC-INT-10 RESULT: transaction survived restart = {survived}")
