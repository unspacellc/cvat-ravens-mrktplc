"""Bearer-token identity for the portal API. Verifies real JWT signatures against the issuer's JWKS (Cognito in
production, the mock IdP in dev/tests) and resolves tenant + roles from server-side membership, never from claims
or request headers. Implements the `IdentityContext` host adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .adapters import Principal
from .models import LabelingMember

ALGORITHMS = ["RS256"]


class KeyResolver(Protocol):
    def signing_key(self, token: str): ...


class JwksKeys:
    """Fetches and caches the issuer's JWKS (PyJWT handles key rotation by `kid`)."""

    def __init__(self, jwks_uri: str):
        self._client = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)

    def signing_key(self, token: str):
        try:
            return self._client.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError as e:
            raise PermissionError("signing key not found") from e


@dataclass(frozen=True)
class JwtConfig:
    issuer: str
    client_ids: tuple[str, ...]  # accepted app clients (Cognito access tokens carry `client_id`; ID tokens carry `aud`)
    leeway_s: int = 0


class JwtIdentity:
    def __init__(self, config: JwtConfig, keys: KeyResolver, session_factory: sessionmaker[Session]):
        self._config, self._keys, self._sf = config, keys, session_factory

    def verify(self, token: str) -> dict:
        try:
            claims = jwt.decode(
                token, self._keys.signing_key(token), algorithms=ALGORITHMS, issuer=self._config.issuer,
                leeway=self._config.leeway_s, options={"require": ["exp", "iss", "sub"], "verify_aud": False},
            )
        except jwt.PyJWTError as e:
            raise PermissionError(f"invalid token: {type(e).__name__}") from e
        # Cognito access tokens: token_use=access + client_id. Generic OIDC tokens: azp (authorized party), else aud.
        use = claims.get("token_use", "access")
        client = claims.get("client_id") or claims.get("azp") or claims.get("aud")
        clients = client if isinstance(client, list) else [client]
        if use != "access" or not set(clients) & set(self._config.client_ids):
            raise PermissionError("token not issued for this application")
        return claims

    def _members(self, sub: str) -> list[LabelingMember]:
        with self._sf() as s:
            return list(s.scalars(select(LabelingMember).where(LabelingMember.user_id == sub, LabelingMember.status == "active")
                                  .order_by(LabelingMember.tenant_id)))

    @staticmethod
    def _principal(m: LabelingMember) -> Principal:
        roles = frozenset(m.roles) | ({"labeler", "reviewer", "creator"} if "admin" in m.roles else frozenset())
        return Principal(user_id=m.user_id, tenant_id=m.tenant_id, roles=roles, email=m.email)

    def memberships(self, credentials: str) -> list[Principal]:
        """Every tenant the token's user belongs to (for tenant selection). Raises PermissionError on a bad token."""
        return [self._principal(m) for m in self._members(self.verify(credentials)["sub"])]

    def resolve(self, credentials: str, tenant_hint: str | None = None) -> Principal:
        rows = self._members(self.verify(credentials)["sub"])
        if tenant_hint is not None:
            rows = [r for r in rows if r.tenant_id == tenant_hint]
        if not rows:
            raise PermissionError("no active membership")
        if len(rows) > 1:
            raise PermissionError("tenant selection required")  # ambiguous: caller must send X-Labeling-Tenant
        return self._principal(rows[0])
