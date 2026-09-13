"""
Unit tests for trustmark.infra.auth.keycloak — fully mocked, no live Keycloak
server, no live PITS deployment, no network I/O in the test run itself.

Purpose: this module is the code path behind two of the four findings raised
in the live-server QA pass on 2026-09-09 (malformed-token -> 500 instead of
401; missing `sub` claim -> empty issuer_id everywhere). These tests
reproduce both deterministically, in milliseconds, from the source code
alone - they do not depend on the state of any deployed server or Keycloak
realm and will keep catching a regression even if the fix is reverted later.

Run with:
    /tmp/pits-unit-venv/bin/python -m pytest tests/trustmark/infra/auth/test_keycloak.py -v
(or any venv with the project installed editable + fastapi/sqlalchemy/
dynaconf/python-jose[cryptography]/pydantic - see repo README for the
canonical dev venv once one exists; this session used a throwaway venv
because .venv-qa only has pytest+requests for the live API suite.)
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import HTTPException
from jose import ExpiredSignatureError, JWTError, jwt

from trustmark.infra.auth.keycloak import (
    KeycloakVerifier,
    Principal,
    _extract_roles,
    get_current_principal,
    require_roles,
)

MODULE = "trustmark.infra.auth.keycloak"


def make_token(kid: str | None = "key-1", claims: dict | None = None) -> str:
    """A syntactically real JWT (three dot-separated base64 segments), signed
    with an arbitrary HS256 secret. `verify()`'s own signature/issuer/
    audience checking is exercised via mocking `jwt.decode` in most cases
    below (that's python-jose's job, not this codebase's) - what these tests
    check is PITS's own branching logic around it. The one case that does
    NOT mock `jwt.decode` (test_verify_malformed_token_is_unhandled) uses a
    raw non-JWT string instead of this helper, on purpose."""
    claims = claims if claims is not None else {"sub": "user-1"}
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, key="unit-test-secret", algorithm="HS256", headers=headers)


@pytest.fixture
def verifier() -> KeycloakVerifier:
    return KeycloakVerifier(
        issuer_url="https://kc.example.test/realms/test-realm",
        expected_issuer="https://kc.example.test/realms/test-realm",
        audience="account",
        jwks_ttl_seconds=3600,
    )


VALID_JWKS = {"keys": [{"kid": "key-1", "kty": "RSA", "n": "dummy", "e": "AQAB"}]}


# ---------------------------------------------------------------------------
# KeycloakVerifier._fetch_jwks()
# ---------------------------------------------------------------------------


class TestFetchJwks:
    def test_oidc_discovery_unreachable_returns_503(self, verifier):
        with patch(f"{MODULE}.requests.get", side_effect=requests.RequestException("timeout")):
            with pytest.raises(HTTPException) as exc:
                verifier._fetch_jwks()
        assert exc.value.status_code == 503

    def test_oidc_response_invalid_json_returns_500(self, verifier):
        bad_response = MagicMock()
        bad_response.raise_for_status.return_value = None
        bad_response.json.side_effect = ValueError("not json")
        with patch(f"{MODULE}.requests.get", return_value=bad_response):
            with pytest.raises(HTTPException) as exc:
                verifier._fetch_jwks()
        assert exc.value.status_code == 500

    def test_oidc_config_missing_jwks_uri_key_is_unhandled(self, verifier):
        """AUDIT FINDING (candidate 5th bug): `oidc["jwks_uri"]` is a plain
        dict index, not `.get(...)`, and sits OUTSIDE the try/except above
        it. If Keycloak's discovery document is ever missing that key, this
        raises a raw KeyError instead of the intended 500 HTTPException -
        same shape of bug as Finding 3 (AUTH-2), just on the config path
        instead of the token path. This test documents the current
        (undesirable) behaviour so a fix shows up as this test changing from
        `pytest.raises(KeyError)` to asserting a clean 500."""
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"issuer": "https://kc.example.test"}  # no jwks_uri
        with patch(f"{MODULE}.requests.get", return_value=response):
            with pytest.raises(KeyError):
                verifier._fetch_jwks()

    @pytest.mark.parametrize("bad_jwks_uri", [None, "", 123, []])
    def test_jwks_uri_invalid_shape_returns_500(self, verifier, bad_jwks_uri):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"jwks_uri": bad_jwks_uri}
        with patch(f"{MODULE}.requests.get", return_value=response):
            with pytest.raises(HTTPException) as exc:
                verifier._fetch_jwks()
        assert exc.value.status_code == 500

    def test_jwks_endpoint_unreachable_returns_503(self, verifier):
        oidc_response = MagicMock()
        oidc_response.raise_for_status.return_value = None
        oidc_response.json.return_value = {"jwks_uri": "https://kc.example.test/jwks"}
        with patch(
            f"{MODULE}.requests.get",
            side_effect=[oidc_response, requests.RequestException("timeout")],
        ):
            with pytest.raises(HTTPException) as exc:
                verifier._fetch_jwks()
        assert exc.value.status_code == 503

    def test_jwks_response_invalid_json_returns_500(self, verifier):
        oidc_response = MagicMock()
        oidc_response.raise_for_status.return_value = None
        oidc_response.json.return_value = {"jwks_uri": "https://kc.example.test/jwks"}
        jwks_response = MagicMock()
        jwks_response.raise_for_status.return_value = None
        jwks_response.json.side_effect = ValueError("not json")
        with patch(f"{MODULE}.requests.get", side_effect=[oidc_response, jwks_response]):
            with pytest.raises(HTTPException) as exc:
                verifier._fetch_jwks()
        assert exc.value.status_code == 500

    @pytest.mark.parametrize(
        "bad_jwks_body",
        [{"keys": "not-a-list"}, {"no_keys_field": True}, ["not", "a", "dict"]],
    )
    def test_jwks_response_wrong_shape_returns_500(self, verifier, bad_jwks_body):
        oidc_response = MagicMock()
        oidc_response.raise_for_status.return_value = None
        oidc_response.json.return_value = {"jwks_uri": "https://kc.example.test/jwks"}
        jwks_response = MagicMock()
        jwks_response.raise_for_status.return_value = None
        jwks_response.json.return_value = bad_jwks_body
        with patch(f"{MODULE}.requests.get", side_effect=[oidc_response, jwks_response]):
            with pytest.raises(HTTPException) as exc:
                verifier._fetch_jwks()
        assert exc.value.status_code == 500

    def test_well_formed_jwks_returned_unchanged(self, verifier):
        oidc_response = MagicMock()
        oidc_response.raise_for_status.return_value = None
        oidc_response.json.return_value = {"jwks_uri": "https://kc.example.test/jwks"}
        jwks_response = MagicMock()
        jwks_response.raise_for_status.return_value = None
        jwks_response.json.return_value = VALID_JWKS
        with patch(f"{MODULE}.requests.get", side_effect=[oidc_response, jwks_response]):
            result = verifier._fetch_jwks()
        assert result == VALID_JWKS


# ---------------------------------------------------------------------------
# KeycloakVerifier.jwks() — caching
# ---------------------------------------------------------------------------


class TestJwksCaching:
    def test_second_call_within_ttl_does_not_refetch(self, verifier):
        with patch.object(verifier, "_fetch_jwks", return_value=VALID_JWKS) as mock_fetch:
            verifier.jwks()
            verifier.jwks()
        mock_fetch.assert_called_once()

    def test_call_after_ttl_expired_refetches(self, verifier):
        verifier.jwks_ttl_seconds = 1
        with patch.object(verifier, "_fetch_jwks", return_value=VALID_JWKS) as mock_fetch:
            verifier.jwks()
            verifier._jwks_fetched_at = time.time() - 10  # force expiry
            verifier.jwks()
        assert mock_fetch.call_count == 2


# ---------------------------------------------------------------------------
# KeycloakVerifier.verify()
# ---------------------------------------------------------------------------


class TestVerify:
    def test_well_formed_token_returns_claims(self, verifier):
        token = make_token()
        expected_claims = {"sub": "user-1", "preferred_username": "bob"}
        with patch.object(verifier, "jwks", return_value=VALID_JWKS), patch(
            f"{MODULE}.jwt.decode", return_value=expected_claims
        ):
            claims = verifier.verify(token)
        assert claims == expected_claims

    def test_token_missing_kid_header_returns_401(self, verifier):
        token = make_token(kid=None)
        with pytest.raises(HTTPException) as exc:
            verifier.verify(token)
        assert exc.value.status_code == 401
        assert "kid" in exc.value.detail.lower()

    def test_unknown_kid_found_after_one_refresh_succeeds(self, verifier):
        """Key-rotation happy path: the kid isn't in the cached JWKS, so
        verify() clears the cache and re-fetches once - and this time the
        key is there."""
        token = make_token(kid="key-2")
        rotated_jwks = {"keys": [{"kid": "key-2", "kty": "RSA", "n": "d", "e": "AQAB"}]}
        jwks_calls = [VALID_JWKS, rotated_jwks]  # stale cache, then the refreshed one

        def fake_jwks():
            return jwks_calls.pop(0)

        with patch.object(verifier, "jwks", side_effect=fake_jwks), patch(
            f"{MODULE}.jwt.decode", return_value={"sub": "user-1"}
        ):
            claims = verifier.verify(token)
        assert claims == {"sub": "user-1"}

    def test_unknown_kid_still_missing_after_refresh_returns_401(self, verifier):
        token = make_token(kid="key-does-not-exist")
        with patch.object(verifier, "jwks", return_value=VALID_JWKS):
            with pytest.raises(HTTPException) as exc:
                verifier.verify(token)
        assert exc.value.status_code == 401
        assert "unknown signing key" in exc.value.detail.lower()

    def test_expired_signature_returns_401(self, verifier):
        token = make_token()
        with patch.object(verifier, "jwks", return_value=VALID_JWKS), patch(
            f"{MODULE}.jwt.decode", side_effect=ExpiredSignatureError("expired")
        ):
            with pytest.raises(HTTPException) as exc:
                verifier.verify(token)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()

    def test_bad_signature_returns_401(self, verifier):
        token = make_token()
        with patch.object(verifier, "jwks", return_value=VALID_JWKS), patch(
            f"{MODULE}.jwt.decode", side_effect=JWTError("Signature verification failed")
        ):
            with pytest.raises(HTTPException) as exc:
                verifier.verify(token)
        assert exc.value.status_code == 401

    def test_issuer_or_audience_mismatch_returns_401(self, verifier):
        """python-jose raises JWTError (not a distinct claims-error type in
        this version) for iss/aud mismatch - it lands in the same generic
        `except JWTError` branch as a bad signature. Confirms that path
        still degrades to 401, not 500."""
        token = make_token()
        with patch.object(verifier, "jwks", return_value=VALID_JWKS), patch(
            f"{MODULE}.jwt.decode", side_effect=JWTError("Invalid audience")
        ):
            with pytest.raises(HTTPException) as exc:
                verifier.verify(token)
        assert exc.value.status_code == 401

    def test_unexpected_exception_mid_decode_still_returns_401_not_500(self, verifier):
        token = make_token()
        with patch.object(verifier, "jwks", return_value=VALID_JWKS), patch(
            f"{MODULE}.jwt.decode", side_effect=RuntimeError("something jose-internal broke")
        ):
            with pytest.raises(HTTPException) as exc:
                verifier.verify(token)
        assert exc.value.status_code == 401  # the broad `except Exception` catches this

    def test_verify_malformed_token_is_unhandled(self, verifier):
        """Reproduces Finding 3 / AUTH-2 (2026-09-09 live QA pass) in
        isolation: `jwt.get_unverified_header()` is called before any
        try/except in verify() is in scope. A string that isn't JWT-shaped
        at all - exactly what a probing client would send - raises
        JWTError straight out of verify(), uncaught. In the real FastAPI
        app this becomes an unhandled-exception 500, not the 401 every
        other bad-token case in this file correctly returns.

        This test currently PASSES because it asserts the *current* (buggy)
        behaviour. Once verify() wraps `jwt.get_unverified_header` in a
        try/except like it already does for `jwt.decode`, this test should
        be updated to assert `HTTPException` with status 401 instead - at
        which point its failure against unfixed code is exactly the
        regression guard it's meant to be.
        """
        with pytest.raises(JWTError):
            verifier.verify("this-is-not-a-jwt-at-all")


# ---------------------------------------------------------------------------
# _extract_roles()
# ---------------------------------------------------------------------------


class TestExtractRoles:
    def test_realm_roles_included(self):
        claims = {"realm_access": {"roles": ["publisher", "offline_access"]}}
        assert _extract_roles(claims, client_id=None) == {"publisher", "offline_access"}

    def test_missing_realm_access_no_crash(self):
        assert _extract_roles({}, client_id=None) == set()

    def test_realm_access_present_but_null(self):
        assert _extract_roles({"realm_access": None}, client_id=None) == set()

    def test_client_roles_included_when_client_id_given(self):
        claims = {"resource_access": {"nextjs-web": {"roles": ["viewer"]}}}
        assert _extract_roles(claims, client_id="nextjs-web") == {"viewer"}

    def test_resource_access_missing_the_given_client_id_no_crash(self):
        claims = {"resource_access": {"some-other-client": {"roles": ["admin"]}}}
        assert _extract_roles(claims, client_id="nextjs-web") == set()

    def test_client_id_none_skips_client_roles_entirely(self):
        claims = {"resource_access": {"nextjs-web": {"roles": ["viewer"]}}}
        assert _extract_roles(claims, client_id=None) == set()

    def test_realm_and_client_roles_unioned_without_duplicates(self):
        claims = {
            "realm_access": {"roles": ["publisher"]},
            "resource_access": {"nextjs-web": {"roles": ["publisher", "viewer"]}},
        }
        assert _extract_roles(claims, client_id="nextjs-web") == {"publisher", "viewer"}


# ---------------------------------------------------------------------------
# get_current_principal()
# ---------------------------------------------------------------------------


class TestGetCurrentPrincipal:
    """get_current_principal is `async def`, but has no real await-worthy I/O
    once `_verifier.verify` is mocked - run it via asyncio.run() from plain
    sync test functions rather than pulling in pytest-asyncio as a new
    project dependency for this alone."""

    def _call(self, creds):
        return asyncio.run(get_current_principal(creds))

    def test_no_credentials_returns_401(self):
        with pytest.raises(HTTPException) as exc:
            self._call(None)
        assert exc.value.status_code == 401
        assert "missing bearer token" in exc.value.detail.lower()

    def test_wrong_scheme_returns_401(self):
        creds = MagicMock(scheme="Basic", credentials="dXNlcjpwYXNz")
        with pytest.raises(HTTPException) as exc:
            self._call(creds)
        assert exc.value.status_code == 401

    def test_valid_token_populates_principal(self):
        creds = MagicMock(scheme="Bearer", credentials="whatever")
        claims = {
            "sub": "abc-123",
            "preferred_username": "bob",
            "email": "bob@example.test",
            "realm_access": {"roles": ["publisher"]},
        }
        with patch(f"{MODULE}._verifier.verify", return_value=claims):
            principal = self._call(creds)
        assert principal.sub == "abc-123"
        assert principal.username == "bob"
        assert principal.email == "bob@example.test"
        assert "publisher" in principal.roles

    def test_missing_sub_claim_reproduces_finding_2(self):
        """Reproduces Finding 2 (2026-09-09 live QA pass) in isolation: with
        no `sub` in claims - exactly the shape of the real production
        uat-tester token, confirmed by direct JWT decode - Principal.sub
        silently becomes "". Every downstream `issuer_id` write inherits
        this. This is the exact bug, demonstrated deterministically without
        needing a live Keycloak realm."""
        creds = MagicMock(scheme="Bearer", credentials="whatever")
        claims_without_sub = {
            "preferred_username": "uat-tester",
            "email": "uat-tester@pangkalandata.id",
            "realm_access": {"roles": ["publisher"]},
        }
        with patch(f"{MODULE}._verifier.verify", return_value=claims_without_sub):
            principal = self._call(creds)
        assert principal.sub == ""

    def test_missing_email_and_username_fall_back_to_none(self):
        creds = MagicMock(scheme="Bearer", credentials="whatever")
        with patch(f"{MODULE}._verifier.verify", return_value={"sub": "abc"}):
            principal = self._call(creds)
        assert principal.email is None
        assert principal.username is None


# ---------------------------------------------------------------------------
# require_roles()
# ---------------------------------------------------------------------------


class TestRequireRoles:
    def _principal(self, roles: set[str]) -> Principal:
        return Principal(sub="u", username="u", email=None, roles=roles, claims={})

    def test_principal_has_required_role_passes_through(self):
        dep = require_roles("publisher")
        principal = self._principal({"publisher"})
        result = asyncio.run(dep(principal))
        assert result is principal

    def test_principal_missing_required_role_returns_403(self):
        dep = require_roles("publisher")
        principal = self._principal({"verifier"})
        with pytest.raises(HTTPException) as exc:
            asyncio.run(dep(principal))
        assert exc.value.status_code == 403

    def test_principal_has_extra_roles_beyond_required_still_passes(self):
        dep = require_roles("publisher")
        principal = self._principal({"publisher", "admin", "extra-role"})
        result = asyncio.run(dep(principal))
        assert result is principal
