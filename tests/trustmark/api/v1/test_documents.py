# Run tests with: python -m pytest tests/trustmark/api/v1/test_documents.py
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trustmark.api.v1 import documents
from trustmark.infra.auth.keycloak import Principal, get_current_principal
from trustmark.infra.db import Base, engine


@pytest.fixture(autouse=True)
def _tables():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def mock_blockchain(monkeypatch):
    mock = MagicMock()
    mock.create_transaction.return_value = "0x" + "a" * 64
    mock.wait_for_receipt.return_value = 1
    mock.read_transaction_value.return_value = {
        "value": None,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr(documents, "_get_blockchain_service", lambda: mock)
    return mock


def _make_client(roles=frozenset({"publisher"})):
    app = FastAPI()
    app.include_router(documents.router_upload, prefix="/api/v1")
    app.include_router(documents.router_verify, prefix="/api/v1")

    async def _override_principal():
        return Principal(
            sub="test-user",
            username="tester",
            email="tester@example.com",
            roles=set(roles),
            claims={},
        )

    app.dependency_overrides[get_current_principal] = _override_principal
    return TestClient(app)


class TestRegister:
    def test_register_golden_path(self, mock_blockchain):
        client = _make_client()
        resp = client.post("/api/v1/register", files={"file": ("a.txt", b"hello world", "text/plain")})
        assert resp.status_code == 200
        body = resp.json()
        assert body["stored"] is True
        assert body["already_existed"] is False
        assert len(body["content_hash"]) == 64
        mock_blockchain.create_transaction.assert_called_once()

    def test_register_duplicate_content_short_circuits_blockchain(self, mock_blockchain):
        client = _make_client()
        content = b"duplicate content"
        first = client.post("/api/v1/register", files={"file": ("a.txt", content, "text/plain")})
        second = client.post("/api/v1/register", files={"file": ("a-again.txt", content, "text/plain")})
        assert first.json()["already_existed"] is False
        assert second.status_code == 200
        assert second.json()["already_existed"] is True
        assert second.json()["content_hash"] == first.json()["content_hash"]
        mock_blockchain.create_transaction.assert_called_once()

    def test_register_empty_file_rejected(self, mock_blockchain):
        client = _make_client()
        resp = client.post("/api/v1/register", files={"file": ("empty.txt", b"", "text/plain")})
        assert resp.status_code == 400
        mock_blockchain.create_transaction.assert_not_called()

    def test_register_file_too_large_rejected(self, monkeypatch, mock_blockchain):
        monkeypatch.setattr(documents, "_max_upload_bytes", lambda: 5)
        client = _make_client()
        resp = client.post(
            "/api/v1/register",
            files={"file": ("big.txt", b"more than five bytes", "text/plain")},
        )
        assert resp.status_code == 413

    def test_register_without_publisher_role_forbidden(self, mock_blockchain):
        client = _make_client(roles=frozenset())
        resp = client.post("/api/v1/register", files={"file": ("a.txt", b"content", "text/plain")})
        assert resp.status_code == 403
        mock_blockchain.create_transaction.assert_not_called()


class TestListRecords:
    def test_list_records_requires_publisher_role(self, mock_blockchain):
        client = _make_client(roles=frozenset())
        resp = client.get("/api/v1/records")
        assert resp.status_code == 403

    def test_list_records_returns_registered_documents(self, mock_blockchain):
        client = _make_client()
        client.post("/api/v1/register", files={"file": ("a.txt", b"list-me", "text/plain")})
        resp = client.get("/api/v1/records")
        assert resp.status_code == 200
        filenames = [r["filename"] for r in resp.json()["records"]]
        assert "a.txt" in filenames


class TestVerify:
    def test_verify_by_hash_unknown_returns_invalid(self, mock_blockchain):
        client = _make_client()
        resp = client.get("/api/v1/verify/" + "0" * 64)
        assert resp.status_code == 200
        assert resp.json()["valid"] is False
        mock_blockchain.read_transaction_value.assert_not_called()

    def test_verify_by_hash_malformed_returns_400(self, mock_blockchain):
        client = _make_client()
        resp = client.get("/api/v1/verify/not-a-valid-hash")
        assert resp.status_code == 400

    def test_verify_by_hash_does_not_require_auth(self, mock_blockchain):
        client = _make_client(roles=frozenset())
        resp = client.get("/api/v1/verify/" + "0" * 64)
        assert resp.status_code == 200

    def test_verify_upload_matching_onchain_value_is_valid(self, mock_blockchain):
        client = _make_client()
        content = b"verify-me"
        registered = client.post("/api/v1/register", files={"file": ("v.txt", content, "text/plain")})
        content_hash = registered.json()["content_hash"]
        mock_blockchain.read_transaction_value.return_value = {
            "value": content_hash,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }

        resp = client.post("/api/v1/verify", files={"file": ("v.txt", content, "text/plain")})
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_verify_upload_tampered_onchain_value_is_invalid(self, mock_blockchain):
        client = _make_client()
        content = b"tamper-me"
        registered = client.post("/api/v1/register", files={"file": ("t.txt", content, "text/plain")})
        mock_blockchain.read_transaction_value.return_value = {
            "value": "0" * 64,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }

        resp = client.post("/api/v1/verify", files={"file": ("t.txt", content, "text/plain")})
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert body["record_id"] == registered.json()["record_id"]
