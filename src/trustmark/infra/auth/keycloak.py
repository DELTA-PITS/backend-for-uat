from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Set
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, ExpiredSignatureError, JWTError

import logging
import requests
import time

from trustmark.infra.commons import settings

logger = logging.getLogger(__name__)


_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    sub: str
    username: Optional[str]
    email: Optional[str]
    roles: Set[str]
    claims: dict[str, Any]


class KeycloakVerifier:
    def __init__(
        self,
        issuer_url: str,
        expected_issuer: str,
        audience: str,
        jwks_ttl_seconds: int = 3600,
    ):
        self.issuer_url = issuer_url.rstrip("/")
        self.expected_issuer = expected_issuer.rstrip("/")
        self.audience = audience
        self.jwks_ttl_seconds = jwks_ttl_seconds
        self._jwks: Optional[dict[str, Any]] = None
        self._jwks_fetched_at: float = 0.0

    def _fetch_jwks(self) -> dict[str, Any]:
        try:
            oidc_response = requests.get(f"{self.issuer_url}/.well-known/openid-configuration", timeout=5)
            oidc_response.raise_for_status()
            oidc = oidc_response.json()

        except requests.RequestException as e:
            logger.exception("Failed to fetch Keylcoak OIDC configuration: %s", e)
            raise HTTPException(status_code=503, detail="Authentication provider unavailable")

        except ValueError as e:
            logger.exception("Invalid JSON in Keycloak OIDC configuration: %s", e)
            raise HTTPException(
                status_code=500,
                detail="Authentication provider returned invalid configuration",
            )

        jwks_uri = oidc["jwks_uri"]

        if not isinstance(jwks_uri, str) or not jwks_uri:
            logger.exception("Keycloak OIDC configuration missing valid jwks_uri %r", oidc)
            raise HTTPException(
                status_code=500,
                detail="Authentication provider returned invalid configuration",
            )

        try:
            jwks_response = requests.get(jwks_uri, timeout=5)
            jwks_response.raise_for_status()
            jwks = jwks_response.json()

        except requests.RequestException as e:
            logger.exception("failed to fetch JWKS: %s", e)
            raise HTTPException(status_code=503, detail="Authentication provider unavailable")

        except ValueError as e:
            logger.exception("Invalid JSON in Keycloak JWKS response: %s", e)
            raise HTTPException(
                status_code=500,
                detail="Authentication provider returned invalid configuration",
            )

        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            logger.exception("Keycloak JWKS response has invalid structure: %r", jwks)
            raise HTTPException(
                status_code=500,
                detail="Authentication provider returned invalid configuration",
            )

        return jwks

    def jwks(self) -> dict[str, Any]:
        now = time.time()
        if self._jwks and (now - self._jwks_fetched_at) < self.jwks_ttl_seconds:
            return self._jwks

        self._jwks = self._fetch_jwks()
        self._jwks_fetched_at = now
        return self._jwks

    def verify(self, token: str) -> dict[str, Any]:
        headers = jwt.get_unverified_header(token)
        kid = headers.get("kid")
        if not kid:
            raise HTTPException(status_code=401, detail="Token missing kid header")

        jwks = self.jwks()
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key:
            # Key rotation: refresh once
            self._jwks = None
            jwks = self.jwks()
            key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)

        if not key:
            raise HTTPException(status_code=401, detail="Unknown signing key")

        try:
            return jwt.decode(
                token,
                key,
                algorithms=["RS256", "PS256"],
                issuer=self.expected_issuer,
                audience=self.audience,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )

        except ExpiredSignatureError as e:
            logger.info("Keycloak token expired: %s", e)
            raise HTTPException(status_code=401, detail="Token expired")
        # except JWTClaimsError as e:
        #     # typically iss/aud mismatch
        #     logger.info("Keycloak token claims invalid: %s", e)
        #     raise HTTPException(status_code=401, detail=f"Invalid token claims: {e}")
        except JWTError as e:
            # signature / format / etc.
            logger.info("Keycloak token invalid (JWTError): %s", e)
            raise HTTPException(status_code=401, detail=f"Invalid token {e}")
        except Exception as e:
            logger.exception("Unexpected error verifying token: %s", e)
            raise HTTPException(status_code=401, detail=f"Invalid token {e}")


def _extract_roles(claims: dict[str, Any], client_id: Optional[str]) -> Set[str]:
    roles: Set[str] = set()

    realm_roles = (claims.get("realm_access") or {}).get("roles") or []
    roles.update(realm_roles)

    if client_id:
        client_roles = ((claims.get("resource_access") or {}).get(client_id, {}).get("roles", [])) or []
        roles.update(client_roles)

    return roles


_issuer_url = str(settings.get("KEYCLOAK_ISSUER_URL", settings.KEYCLOAK_ISSUER))
_verifier = KeycloakVerifier(
    issuer_url=_issuer_url,
    expected_issuer=str(settings.KEYCLOAK_ISSUER),
    audience=str(settings.KEYCLOAK_AUDIENCE),
)


async def get_current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing bearer token")

    claims = _verifier.verify(creds.credentials)
    roles = _extract_roles(claims, client_id=getattr(settings, "KEYCLOAK_ROLES_CLIENT_ID", None))

    return Principal(
        sub=claims.get("sub", ""),
        username=claims.get("preferred_username"),
        email=claims.get("email"),
        roles=roles,
        claims=claims,
    )


def require_roles(*required: str):
    required_set = set(required)

    async def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not required_set.issubset(principal.roles):
            raise HTTPException(status_code=403, detail="Forbidden")
        return principal

    return _dep
