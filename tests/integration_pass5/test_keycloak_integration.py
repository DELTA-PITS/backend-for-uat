"""
PASS 5 — Section 3: Real Keycloak integration tests (KC-INT-01..13).

Uses the LOCAL disposable Keycloak (quay.io/keycloak/keycloak:26.6.2,
realm `nextjs-kc` imported from docker/realms/realm-export.json). No mocked
JWT verification anywhere in this file - every token is a real one issued by
a real Keycloak, and every check against it goes through the live FastAPI
backend's real KeycloakVerifier.

Critically: the production incident (2026-09-09) found tokens for
`uat-tester` with NO `sub` claim at all. The local realm here uses Keycloak's
default client scopes (profile/email/roles) with zero custom protocol
mappers (confirmed via realm-export.json inspection) - `sub` is a mandatory,
non-mapped OIDC claim that Keycloak populates from the internal user id
regardless of scope configuration. KC-INT-02/03 exist specifically to
determine, with real evidence, whether that production anomaly reproduces on
a stock/default Keycloak client, or whether it is isolated to the production
client/realm's own configuration.
"""
from __future__ import annotations

import base64
import json
import time

import requests

from conftest import (
    BACKEND_URL,
    KEYCLOAK_URL,
    REALM,
    CLIENT_ID,
    CLIENT_SECRET,
    fetch_token,
    register,
    list_records,
    unique_pdf_bytes,
)


def decode_claims(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + padding))


class TestKeycloakIntegration:
    def test_kc_int_01_publisher_a_login_produces_valid_token(self, token_a):
        assert token_a
        assert len(token_a.split(".")) == 3

    def test_kc_int_02_token_has_nonempty_sub(self, token_a):
        claims = decode_claims(token_a)
        print(f"KC-INT-02: claims keys={sorted(claims.keys())}, sub={claims.get('sub')!r}")
        assert claims.get("sub"), (
            "LOCAL Keycloak token has NO/empty 'sub' claim. If this fails, the "
            "production anomaly is NOT a production-only config drift - it "
            "reproduces on a stock realm/client too, implicating Keycloak "
            "version 26.6.2 default behaviour or python-jose decoding, not a "
            "one-off production misconfiguration."
        )

    def test_kc_int_03_principal_sub_equals_jwt_sub(self, token_a):
        """Registers a document as publisher-a, and confirms the backend's
        Principal.sub (as reflected in the persisted issuer_id) equals the
        token's own sub claim - proxy for the internal Principal wiring
        without instrumenting the app."""
        claims = decode_claims(token_a)
        resp = register(token_a, unique_pdf_bytes("KC-INT-03"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["issuer_id"] == claims.get("sub")

    def test_kc_int_04_registration_stores_issuer_id_equal_to_sub(self, token_b):
        claims = decode_claims(token_b)
        resp = register(token_b, unique_pdf_bytes("KC-INT-04"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["issuer_id"] == claims.get("sub")

    def test_kc_int_05_publisher_role_accepted(self, token_a):
        resp = register(token_a, unique_pdf_bytes("KC-INT-05"))
        assert resp.status_code == 200, resp.text

    def test_kc_int_06_no_role_user_rejected_from_register(self, token_c):
        resp = register(token_c, unique_pdf_bytes("KC-INT-06"))
        assert resp.status_code == 403, resp.text

    def test_kc_int_07_no_role_user_rejected_from_records(self, token_c):
        resp = list_records(token_c)
        assert resp.status_code == 403, resp.text

    def test_kc_int_08_malformed_bearer_returns_401_not_500(self):
        resp = requests.post(
            f"{BACKEND_URL}/register",
            headers={"Authorization": "Bearer this-is-not-a-jwt-at-all"},
            files={"file": ("doc.pdf", unique_pdf_bytes("KC-INT-08"), "application/pdf")},
            timeout=30,
        )
        print(f"KC-INT-08: status={resp.status_code}, body={resp.text[:300]}")
        assert resp.status_code != 500, (
            f"Malformed bearer token caused an unhandled 500, not a controlled "
            f"401. Reproduces the AUTH-2/Finding-3 shape locally. Got "
            f"{resp.status_code}: {resp.text[:300]}"
        )
        assert resp.status_code == 401

    def test_kc_int_09_expired_token_rejected(self, token_a):
        """Fetch a fresh token, wait past expires_in, then use it. Keycloak
        default access token lifespan is short; this test may take a couple
        of minutes to run for real (no forged signatures - we don't have the
        realm's private key)."""
        token_resp = fetch_token("publisher-a", "PassA-2026!")
        access_token = token_resp["access_token"]
        expires_in = token_resp["expires_in"]
        print(f"KC-INT-09: token expires_in={expires_in}s, waiting it out...")
        time.sleep(expires_in + 5)

        resp = register(access_token, unique_pdf_bytes("KC-INT-09"))
        assert resp.status_code == 401, resp.text
        assert "expired" in resp.json().get("detail", "").lower()

    def test_kc_int_10_wrong_issuer_rejected(self, token_a):
        """Tamper the `iss` claim in a copy of a real token's payload (kept
        validly signed is impossible without the realm key, so - like the
        historical AUTH-5 test - we tamper the kid instead to force the same
        401 failure path before iss is even checked). This exercises 'wrong
        issuer or unverifiable token -> 401', not a claims-level iss check in
        isolation (that would need a second local realm)."""
        header_b64, payload_b64, sig_b64 = token_a.split(".")

        def b64url_decode(s):
            return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

        def b64url_encode(b):
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

        header = json.loads(b64url_decode(header_b64))
        header["kid"] = "pass5-nonexistent-kid"
        tampered = f"{b64url_encode(json.dumps(header).encode())}.{payload_b64}.{sig_b64}"

        resp = register(tampered, unique_pdf_bytes("KC-INT-10"))
        assert resp.status_code == 401, resp.text

    def test_kc_int_11_wrong_audience_rejected(self, token_a):
        """Same tamper-based proxy as KC-INT-10 (no second client with a
        different audience configured in this pass) - documented as such,
        not claimed as a true audience-claim-level test."""
        header_b64, payload_b64, sig_b64 = token_a.split(".")

        def b64url_decode(s):
            return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

        def b64url_encode(b):
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

        header = json.loads(b64url_decode(header_b64))
        header["kid"] = "pass5-nonexistent-kid-2"
        tampered = f"{b64url_encode(json.dumps(header).encode())}.{payload_b64}.{sig_b64}"

        resp = register(tampered, unique_pdf_bytes("KC-INT-11"))
        assert resp.status_code == 401, resp.text

    def test_kc_int_12_missing_token_rejected(self):
        resp = register(None, unique_pdf_bytes("KC-INT-12"))
        assert resp.status_code == 401, resp.text

    def test_kc_int_13_jwks_flow_works_against_real_keycloak(self, token_a):
        """Direct proof the backend's KeycloakVerifier successfully fetches
        OIDC discovery + JWKS from the real local Keycloak and validates a
        real signature with it (indirect: a successful 200 on a protected
        endpoint IS the JWKS flow working end-to-end)."""
        oidc = requests.get(
            f"{KEYCLOAK_URL}/realms/{REALM}/.well-known/openid-configuration", timeout=10
        )
        assert oidc.status_code == 200
        jwks_uri = oidc.json()["jwks_uri"]
        jwks = requests.get(jwks_uri, timeout=10)
        assert jwks.status_code == 200
        assert jwks.json().get("keys")

        resp = register(token_a, unique_pdf_bytes("KC-INT-13"))
        assert resp.status_code == 200, (
            f"Register with a real, validly signed token failed - JWKS/signing "
            f"key flow against real Keycloak is not working end-to-end. "
            f"{resp.status_code}: {resp.text}"
        )
