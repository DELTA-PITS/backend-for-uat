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

    def test_bc_int_08_pending_transaction_handling(self, db_conn):
        """Anvil in --host/--port mode (no --block-time flag in
        docker-compose.yml) auto-mines every transaction instantly, so a
        genuinely pending transaction can't occur through the normal
        register() flow (which itself blocks on wait_for_receipt() before
        ever writing a row to Postgres - the app never persists an unmined
        tx under normal operation).

        To exercise the real 'pending' branch of
        BlockchainConnector.read_transaction_value() for real (not mocked),
        this test: (1) restarts Anvil with --block-time so mining is no
        longer instant, (2) fires a transaction directly via
        BlockchainConnector.create_transaction() WITHOUT waiting for a
        receipt, (3) inserts a matching RegistryRecord row directly into
        Postgres (bypassing register(), which is exactly what would refuse
        to do this), (4) hits the real GET /verify/{hash} endpoint while the
        tx is still unmined, and (5) restores Anvil to instant mining
        afterwards so later tests are unaffected."""
        import subprocess
        import time
        import uuid

        import requests
        from web3 import Web3

        from trustmark.infra.blockchain_connector import BlockchainConnector

        compose_dir = "/Users/laksaersa/GitHub/_personal/_PITS/backend-for-uat/docker"
        block_time_seconds = 6
        temp_container = "bc-int-08-anvil"

        subprocess.run(["docker", "compose", "stop", "anvil"], cwd=compose_dir, check=True, capture_output=True)
        subprocess.run(["docker", "rm", "-f", temp_container], capture_output=True)

        run_result = subprocess.run(
            [
                "docker", "compose", "run", "--rm", "-d",
                "--use-aliases", "--service-ports", "--name", temp_container,
                "anvil", "--host", "0.0.0.0", "--port", "8545", "--block-time", str(block_time_seconds),
            ],
            cwd=compose_dir, capture_output=True, text=True,
        )
        assert run_result.returncode == 0, f"failed to start block-time Anvil: {run_result.stderr}"

        try:
            w3 = Web3(Web3.HTTPProvider(RPC_URL))
            for _ in range(20):
                if w3.is_connected():
                    break
                time.sleep(1)
            assert w3.is_connected(), "block-time Anvil never became reachable"

            connector = BlockchainConnector(
                rpc_url=RPC_URL,
                private_key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
            )
            fake_content_hash = uuid.uuid4().hex + uuid.uuid4().hex[:32]
            tx_hash = connector.create_transaction(fake_content_hash)
            print(f"BC-INT-08: fired tx {tx_hash} against a {block_time_seconds}s block-time chain, NOT waiting for receipt")

            with db_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO registry_records (id, content_hash, transaction_hash, issuer_id) "
                    "VALUES (%s, %s, %s, %s)",
                    (str(uuid.uuid4()), fake_content_hash, tx_hash, "bc-int-08"),
                )

            resp = requests.get(f"http://127.0.0.1:41012/api/v1/verify/{fake_content_hash}", timeout=15)
            print(f"BC-INT-08: verify-by-hash for a still-pending tx -> {resp.status_code}: {resp.text}")
            assert resp.status_code == 400, (
                f"expected 400 'still pending' while the tx is genuinely unmined, got {resp.status_code}: {resp.text}"
            )
            assert "pending" in resp.json().get("detail", "").lower()

            # Confirm it resolves correctly once actually mined, closing the loop.
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=block_time_seconds + 15)
            resp_after_mining = requests.get(f"http://127.0.0.1:41012/api/v1/verify/{fake_content_hash}", timeout=15)
            print(f"BC-INT-08: same hash after mining -> {resp_after_mining.status_code}: {resp_after_mining.text}")
            assert resp_after_mining.status_code == 200
            assert resp_after_mining.json()["valid"] is True
        finally:
            subprocess.run(["docker", "rm", "-f", temp_container], capture_output=True)
            subprocess.run(["docker", "compose", "up", "-d", "anvil"], cwd=compose_dir, check=True, capture_output=True)
            for _ in range(20):
                try:
                    r = requests.post(
                        RPC_URL, json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}, timeout=3
                    )
                    if r.status_code == 200:
                        break
                except requests.exceptions.RequestException:
                    pass
                time.sleep(1)

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
