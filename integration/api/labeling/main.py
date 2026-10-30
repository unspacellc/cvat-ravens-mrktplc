"""Standalone ASGI entrypoint: `uvicorn api.labeling.main:app`. Wires the real JWT identity and CVAT client from env.
The marketplace does not use this module; it binds the routers to its own identity/session stack."""

import os

import httpx

from .app import create_app
from .auth import JwksKeys, JwtConfig, JwtIdentity
from .config import Settings
from .cvat_client import CvatClient
from .db import make_engine, make_session_factory
from .fixtures import FixturePublisher, LocalClipSource, LocalDirMediaCatalog, LocalObjectStore
from .service import Ctx


def _issuer_and_jwks() -> tuple[str, str]:
    """Cognito (production): LABELING_COGNITO_REGION + LABELING_COGNITO_USER_POOL_ID; the JWKS URL is deterministic,
    so startup needs no network call. Any other OIDC issuer (dev mock IdP): LABELING_JWT_ISSUER (+ optional JWKS URI)."""
    pool = os.environ.get("LABELING_COGNITO_USER_POOL_ID")
    if pool:
        issuer = JwtConfig.cognito_issuer(os.environ["LABELING_COGNITO_REGION"], pool)
        return issuer, f"{issuer}/.well-known/jwks.json"
    issuer = os.environ["LABELING_JWT_ISSUER"]
    return issuer, os.environ.get("LABELING_JWKS_URI") or httpx.get(
        f"{issuer}/.well-known/openid-configuration", timeout=10).json()["jwks_uri"]


def build_app():
    settings = Settings()
    issuer, jwks_uri = _issuer_and_jwks()
    clients = tuple(c for c in os.environ.get("LABELING_JWT_CLIENT_IDS", "portal-web").split(",") if c)
    sf = make_session_factory(make_engine())
    identity = JwtIdentity(JwtConfig(issuer, clients), JwksKeys(jwks_uri), sf)
    clip_root = os.environ.get("LABELING_CLIP_ROOT", "fixtures/generated")  # clips live in <root>/<tenant id>/*.mp4
    ctx = Ctx(settings=settings, cvat=CvatClient(settings.cvat_url, settings.cvat_service_token), media=LocalDirMediaCatalog(clip_root),
              clips=LocalClipSource(clip_root),
              store=LocalObjectStore(os.environ.get("LABELING_STORE_ROOT", "var/objects")), publisher=FixturePublisher())
    return create_app(settings, sf, identity=identity, ctx=ctx)


app = build_app()
