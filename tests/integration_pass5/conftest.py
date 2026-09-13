"""
Shared fixtures for PASS 5 — Integration & Reproducibility tests.

These tests run against a LOCAL, DISPOSABLE full-stack replica of PITS
(docker/docker-compose.yml in this repo: real Postgres, real Keycloak, real
Anvil, real FastAPI backend). Nothing here touches production
(pits.pangkalandata.id / keycloak.pangkalandata.id).

Bring the stack up first:
    cd docker && docker compose up -d --build
    bash _docs/qa/pass5/setup_keycloak_users.sh   # creates publisher-a/b, user-c

Run with:
    .venv/bin/python -m pytest tests/integration_pass5/ -v -s
"""
from __future__ import annotations

import io
import uuid

import psycopg2
import pytest
import requests

BACKEND_URL = "http://127.0.0.1:41012/api/v1"
# Must match the backend's KEYCLOAK_ISSUER exactly (docker/.env:
# KEYCLOAK_ISSUER=http://localhost:8080/realms/nextjs-kc) - KeycloakVerifier
# does a literal string comparison on `iss`, so 127.0.0.1 vs localhost is a
# real mismatch that produces "Invalid issuer" even though both resolve to
# the same local Keycloak.
KEYCLOAK_URL = "http://localhost:8080"
REALM = "nextjs-kc"
CLIENT_ID = "nextjs-web"
CLIENT_SECRET = "pits-local-client-secret"

PUBLISHER_A = ("publisher-a", "PassA-2026!")
PUBLISHER_B = ("publisher-b", "PassB-2026!")
USER_C = ("user-c", "PassC-2026!")

DB_DSN = "postgresql://trustmark:trustmark@127.0.0.1:5432/trustmark"


def fetch_token(username: str, password: str) -> dict:
    resp = requests.post(
        f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": username,
            "password": password,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


@pytest.fixture()
def token_a() -> str:
    # function-scoped: Keycloak's default access token lifespan (realm has no
    # explicit accessTokenLifespan override) is short enough that a
    # session-scoped token expires partway through a multi-minute test run.
    return fetch_token(*PUBLISHER_A)["access_token"]


@pytest.fixture()
def token_b() -> str:
    return fetch_token(*PUBLISHER_B)["access_token"]


@pytest.fixture()
def token_c() -> str:
    return fetch_token(*USER_C)["access_token"]


@pytest.fixture()
def db_conn():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def unique_pdf_bytes(tag: str | None = None) -> bytes:
    label = tag or "doc"
    marker = f"{label}-{uuid.uuid4().hex}"
    return f"%PDF-1.4\n% PASS5 test document marker={marker}\n".encode() + b"0" * 256


def upload_files(content: bytes, filename: str = "doc.pdf", content_type: str = "application/pdf"):
    return {"file": (filename, io.BytesIO(content), content_type)}


def register(token: str | None, content: bytes, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.post(f"{BACKEND_URL}/register", headers=headers, files=upload_files(content, **kwargs), timeout=60)


def list_records(token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.get(f"{BACKEND_URL}/records", headers=headers, timeout=30)


def verify(content: bytes, **kwargs):
    return requests.post(f"{BACKEND_URL}/verify", files=upload_files(content, **kwargs), timeout=60)


def verify_by_hash(file_hash: str):
    return requests.get(f"{BACKEND_URL}/verify/{file_hash}", timeout=30)
