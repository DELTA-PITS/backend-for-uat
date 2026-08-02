# Run tests with: python -m pytest tests/trustmark/infra/auth/test_keycloak.py
import asyncio
import time
from unittest.mock import MagicMock

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jose import jwk, jwt

from trustmark.infra.auth import keycloak as keycloak_module
from trustmark.infra.auth.keycloak import (
    KeycloakVerifier,
    Principal,
    _extract_roles,
    get_current_principal,
    require_roles,
)

ISSUER = "http://keycloak.test/realms/test"
AUDIENCE = "account"
KID = "test-kid"


@pytest.fixture(scope="module")
def rsa_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.fixture(scope="module")
def jwks(rsa_keys):
    _, public_pem = rsa_keys
    key_dict = jwk.construct(public_pem, algorithm="RS256").to_dict()
    key_dict["kid"] = KID
    key_dict["use"] = "sig"
    return {"keys": [key_dict]}


def _sign(private_pem: str, claims: dict, kid: str | None = KID) -> str:
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, private_pem, algorithm="RS256", headers=headers)


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "preferred_username": "tester",
        "email": "tester@example.com",
        "realm_access": {"roles": ["publisher"]},
    }
    claims.update(overrides)
    return claims


@pytest.fixture
def verifier(jwks, monkeypatch):
    v = KeycloakVerifier(issuer_url=ISSUER, expected_issuer=ISSUER, audience=AUDIENCE)
    monkeypatch.setattr(v, "jwks", lambda: jwks)
    return v


class TestVerify:
    def test_valid_token_returns_claims(self, verifier, rsa_keys):
        private_pem, _ = rsa_keys
        token = _sign(private_pem, _base_claims())
        claims = verifier.verify(token)
        assert claims["sub"] == "user-123"
        assert claims["realm_access"]["roles"] == ["publisher"]

    def test_missing_kid_raises_401(self, verifier, rsa_keys):
        private_pem, _ = rsa_keys
        token = _sign(private_pem, _base_claims(), kid=None)
        with pytest.raises(HTTPException) as exc:
            verifier.verify(token)
        assert exc.value.status_code == 401

    def test_unknown_kid_raises_401(self, verifier, rsa_keys):
        private_pem, _ = rsa_keys
        token = _sign(private_pem, _base_claims(), kid="does-not-exist")
        with pytest.raises(HTTPException) as exc:
            verifier.verify(token)
        assert exc.value.status_code == 401

    def test_expired_token_raises_401(self, verifier, rsa_keys):
        private_pem, _ = rsa_keys
        now = int(time.time())
        token = _sign(private_pem, _base_claims(iat=now - 3600, exp=now - 1800))
        with pytest.raises(HTTPException) as exc:
            verifier.verify(token)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()

    def test_wrong_audience_raises_401(self, verifier, rsa_keys):
        private_pem, _ = rsa_keys
        token = _sign(private_pem, _base_claims(aud="someone-else"))
        with pytest.raises(HTTPException) as exc:
            verifier.verify(token)
        assert exc.value.status_code == 401

    def test_wrong_issuer_raises_401(self, verifier, rsa_keys):
        private_pem, _ = rsa_keys
        token = _sign(private_pem, _base_claims(iss="http://not-the-real-issuer"))
        with pytest.raises(HTTPException) as exc:
            verifier.verify(token)
        assert exc.value.status_code == 401

    def test_unknown_kid_triggers_single_jwks_refresh(self, jwks, rsa_keys, monkeypatch):
        private_pem, _ = rsa_keys
        v = KeycloakVerifier(issuer_url=ISSUER, expected_issuer=ISSUER, audience=AUDIENCE)
        fetch = MagicMock(return_value=jwks)
        monkeypatch.setattr(v, "_fetch_jwks", fetch)
        token = _sign(private_pem, _base_claims())

        claims = v.verify(token)

        assert claims["sub"] == "user-123"
        assert fetch.call_count == 1


class TestFetchJwks:
    def test_fetch_jwks_success(self, monkeypatch, jwks):
        v = KeycloakVerifier(issuer_url=ISSUER, expected_issuer=ISSUER, audience=AUDIENCE)
        oidc_response = MagicMock()
        oidc_response.json.return_value = {"jwks_uri": f"{ISSUER}/protocol/openid-connect/certs"}
        oidc_response.raise_for_status = MagicMock()
        jwks_response = MagicMock()
        jwks_response.json.return_value = jwks
        jwks_response.raise_for_status = MagicMock()

        monkeypatch.setattr(
            keycloak_module.requests, "get", MagicMock(side_effect=[oidc_response, jwks_response])
        )
        result = v.jwks()
        assert result == jwks

    def test_fetch_jwks_oidc_unreachable_raises_503(self, monkeypatch):
        v = KeycloakVerifier(issuer_url=ISSUER, expected_issuer=ISSUER, audience=AUDIENCE)
        monkeypatch.setattr(
            keycloak_module.requests,
            "get",
            MagicMock(side_effect=requests.RequestException("boom")),
        )
        with pytest.raises(HTTPException) as exc:
            v.jwks()
        assert exc.value.status_code == 503

    def test_jwks_cached_within_ttl(self, monkeypatch, jwks):
        v = KeycloakVerifier(
            issuer_url=ISSUER, expected_issuer=ISSUER, audience=AUDIENCE, jwks_ttl_seconds=3600
        )
        fetch = MagicMock(return_value=jwks)
        monkeypatch.setattr(v, "_fetch_jwks", fetch)
        v.jwks()
        v.jwks()
        assert fetch.call_count == 1

    def test_jwks_refetched_after_ttl_expires(self, monkeypatch, jwks):
        v = KeycloakVerifier(issuer_url=ISSUER, expected_issuer=ISSUER, audience=AUDIENCE, jwks_ttl_seconds=0)
        fetch = MagicMock(return_value=jwks)
        monkeypatch.setattr(v, "_fetch_jwks", fetch)
        v.jwks()
        v.jwks()
        assert fetch.call_count == 2


class TestExtractRoles:
    def test_combines_realm_and_client_roles(self):
        claims = {
            "realm_access": {"roles": ["publisher"]},
            "resource_access": {"my-client": {"roles": ["admin"]}},
        }
        roles = _extract_roles(claims, client_id="my-client")
        assert roles == {"publisher", "admin"}

    def test_missing_claims_returns_empty_set(self):
        assert _extract_roles({}, client_id="my-client") == set()

    def test_no_client_id_ignores_client_roles(self):
        claims = {"resource_access": {"my-client": {"roles": ["admin"]}}}
        assert _extract_roles(claims, client_id=None) == set()


class TestGetCurrentPrincipal:
    def test_missing_credentials_raises_401(self):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_current_principal(creds=None))
        assert exc.value.status_code == 401

    def test_non_bearer_scheme_raises_401(self):
        creds = MagicMock(scheme="Basic", credentials="abc")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_current_principal(creds=creds))
        assert exc.value.status_code == 401

    def test_valid_credentials_returns_principal(self, monkeypatch):
        monkeypatch.setattr(
            keycloak_module._verifier,
            "verify",
            lambda token: _base_claims(),
        )
        creds = MagicMock(scheme="Bearer", credentials="valid-token")
        principal = asyncio.run(get_current_principal(creds=creds))
        assert isinstance(principal, Principal)
        assert principal.sub == "user-123"
        assert "publisher" in principal.roles


class TestRequireRoles:
    def test_allows_when_role_present(self):
        principal = Principal(sub="u1", username="u", email="u@x.com", roles={"publisher"}, claims={})
        dep = require_roles("publisher")
        result = asyncio.run(dep(principal=principal))
        assert result is principal

    def test_forbidden_when_role_missing(self):
        principal = Principal(sub="u1", username="u", email="u@x.com", roles=set(), claims={})
        dep = require_roles("publisher")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(dep(principal=principal))
        assert exc.value.status_code == 403
