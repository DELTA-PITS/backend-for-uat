"""
PASS 5 — Section 8: Failure / resilience testing (RES-01..07).

All actions target the LOCAL disposable stack only (docker compose stop/
start/restart of individual services in this repo's docker-compose.yml).
Nothing here touches production. RES-01 (Postgres unavailable) and RES-03
(Anvil unavailable during registration) are exercised more thoroughly as
DB-INT-10 and BC-INT-09 respectively (see those files) - this file covers
what those don't: Keycloak down, Anvil down during VERIFY specifically,
and a backend container restart's effect on already-registered data.
"""
from __future__ import annotations

import subprocess
import time

import requests

from conftest import (
    BACKEND_URL,
    register,
    verify,
    unique_pdf_bytes,
    fetch_token,
)

COMPOSE_DIR = "/Users/laksaersa/GitHub/_personal/_PITS/backend-for-uat/docker"


def _stop(service):
    subprocess.run(["docker", "compose", "stop", service], cwd=COMPOSE_DIR, check=True, capture_output=True)


def _start(service):
    subprocess.run(["docker", "compose", "start", service], cwd=COMPOSE_DIR, check=True, capture_output=True)


class TestResilience:
    def test_res_02_keycloak_unavailable(self, token_a):
        """Backend caches JWKS for up to 3600s (KeycloakVerifier default
        jwks_ttl_seconds) - so a token already verified once may keep
        working after Keycloak goes down, until a NEW kid needs fetching.
        Test: stop Keycloak, then try to fetch a brand NEW token (must fail
        - proves login itself is unavailable) and separately try to use the
        EXISTING token_a against the backend (may still work due to caching -
        recorded either way, not assumed)."""
        _stop("keycloak")
        time.sleep(2)
        try:
            login_failed = False
            try:
                fetch_token("publisher-a", "PassA-2026!")
            except requests.exceptions.RequestException as e:
                login_failed = True
                print(f"RES-02: new login attempt with Keycloak down failed as expected: {e}")
            assert login_failed, "new Keycloak login unexpectedly succeeded while Keycloak was stopped"

            resp = register(token_a, unique_pdf_bytes("RES-02"))
            print(f"RES-02: register() with an ALREADY-ISSUED token while Keycloak is down -> "
                  f"{resp.status_code}: {resp.text[:300]}")
        finally:
            _start("keycloak")
            for _ in range(30):
                try:
                    r = requests.get("http://127.0.0.1:8080/realms/master/.well-known/openid-configuration", timeout=3)
                    if r.status_code == 200:
                        break
                except requests.exceptions.RequestException:
                    pass
                time.sleep(2)

    def test_res_04_anvil_unavailable_during_verification(self, token_a):
        """Register a document while Anvil is UP (so a valid record + tx
        exists), then stop Anvil and attempt to verify that same document -
        verify_full() calls read_transaction_value() with no try/except
        around it, so this documents whether that propagates as an unhandled
        500 or something cleaner."""
        content = unique_pdf_bytes("RES-04")
        reg = register(token_a, content)
        assert reg.status_code == 200, reg.text

        _stop("anvil")
        time.sleep(2)
        try:
            resp = verify(content)
            print(f"RES-04: verify() with Anvil down for an ALREADY-REGISTERED doc -> "
                  f"{resp.status_code}: {resp.text[:300]}")
        finally:
            _start("anvil")
            for _ in range(20):
                try:
                    r = requests.post(
                        "http://127.0.0.1:8545", json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}, timeout=3
                    )
                    if r.status_code == 200:
                        break
                except requests.exceptions.RequestException:
                    pass
                time.sleep(2)

    def test_res_06_backend_restart_preserves_data(self, token_a, db_conn):
        """Register a document, restart the trustmark-app container (not
        Postgres/Anvil), and confirm the record is still retrievable
        afterwards - proxy for 'does a backend restart lose in-flight or
        recently committed state'."""
        content = unique_pdf_bytes("RES-06")
        reg = register(token_a, content)
        assert reg.status_code == 200, reg.text
        content_hash = reg.json()["content_hash"]

        subprocess.run(["docker", "compose", "restart", "trustmark-app"], cwd=COMPOSE_DIR, check=True, capture_output=True)

        healthy = False
        for _ in range(30):
            try:
                r = requests.get(f"{BACKEND_URL.replace('/api/v1', '')}/health", timeout=3)
                if r.status_code == 200:
                    healthy = True
                    break
            except requests.exceptions.RequestException:
                pass
            time.sleep(2)
        assert healthy, "backend did not come back healthy after restart within 60s"

        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM registry_records WHERE content_hash = %s", (content_hash,))
            row = cur.fetchone()
        print(f"RES-06: record present after backend restart: {row is not None}")
        assert row is not None, "record registered before backend restart was lost"
