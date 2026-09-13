"""
Shared fixtures for E2E API tests against the live PITS backend
(https://pits.pangkalandata.id). These tests hit the deployed server directly
over HTTP - they are NOT unit tests and do not import the FastAPI app.

Run with:
    .venv-qa/bin/python -m pytest tests/api/ -v

Credentials come from _credentials/19-project-trustmark-pits.md (see repo
README / project memory `reference_project_trustmark_pits_credentials`).
"""
from __future__ import annotations

import io
import time
import uuid

import pytest
import requests

BASE_URL = "https://pits.pangkalandata.id/api/v1"
KEYCLOAK_TOKEN_URL = (
    "https://keycloak.pangkalandata.id/realms/nextjs-kc/protocol/openid-connect/token"
)
CLIENT_ID = "nextjs-web"
CLIENT_SECRET = "pits-local-client-secret"

PUBLISHER_USERNAME = "uat-tester"
PUBLISHER_PASSWORD = "Uat2026!"

# Matches docker/.env MAX_UPLOAD_BYTES on this deployment. If the production
# server was configured with a different value, REG-3/REG-4 will surface the
# mismatch as a failing assertion rather than silently passing.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def fetch_token(username: str, password: str) -> dict:
    """Password-grant login against Keycloak. Returns the full token response."""
    resp = requests.post(
        KEYCLOAK_TOKEN_URL,
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


def auth_header(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture()
def publisher_token() -> str:
    """Fresh access token for uat-tester (publisher role). Tokens are short-lived
    (~5 min), so this is function-scoped and fetched fresh for every test."""
    return fetch_token(PUBLISHER_USERNAME, PUBLISHER_PASSWORD)["access_token"]


@pytest.fixture(scope="session", autouse=True)
def stale_token_capture():
    """Captures a token + issue time once per test session so AUTH-3 (expired
    token) can wait out the real Keycloak TTL instead of forging a signature
    we don't have the private key for."""
    token_resp = fetch_token(PUBLISHER_USERNAME, PUBLISHER_PASSWORD)
    return {
        "access_token": token_resp["access_token"],
        "expires_in": token_resp["expires_in"],
        "issued_at": time.monotonic(),
    }


def unique_pdf_bytes(tag: str | None = None) -> bytes:
    """Minimal, unique fake-PDF payload so tests never collide with documents
    registered in earlier runs or earlier sessions (production DB is not
    reset between test runs). A random suffix is ALWAYS appended, even when a
    readable tag is given, so re-running the suite never reuses a previous
    run's content_hash."""
    label = tag or "doc"
    marker = f"{label}-{uuid.uuid4().hex}"
    return f"%PDF-1.4\n% QA test document marker={marker}\n".encode() + b"0" * 256


def upload_files(content: bytes, filename: str = "doc.pdf", content_type: str = "application/pdf"):
    return {"file": (filename, io.BytesIO(content), content_type)}


def register(token: str | None, content: bytes, **kwargs):
    headers = auth_header(token) if token else {}
    return requests.post(
        f"{BASE_URL}/register",
        headers=headers,
        files=upload_files(content, **kwargs),
        timeout=60,
    )


def verify(content: bytes, **kwargs):
    return requests.post(
        f"{BASE_URL}/verify",
        files=upload_files(content, **kwargs),
        timeout=60,
    )


def verify_by_hash(file_hash: str):
    return requests.get(f"{BASE_URL}/verify/{file_hash}", timeout=30)


def list_records(token: str | None):
    headers = auth_header(token) if token else {}
    return requests.get(f"{BASE_URL}/records", headers=headers, timeout=30)
