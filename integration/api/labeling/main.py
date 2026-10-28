"""Standalone ASGI entrypoint: `uvicorn api.labeling.main:app`. Wires the real JWT identity and CVAT client from env.
The marketplace does not use this module; it binds the routers to its own identity/session stack."""

import os

import httpx

from .app import create_app
from .auth import JwksKeys, JwtConfig, JwtIdentity
from .config import Settings
from .cvat_client import CvatClient
from .db import make_engine, make_session_factory
from .fixtures import FixtureMedia, LocalClipSource
from .service import Ctx


def build_app():
    settings = Settings()
    issuer = os.environ["LABELING_JWT_ISSUER"]
    clients = tuple(c for c in os.environ.get("LABELING_JWT_CLIENT_IDS", "portal-web").split(",") if c)
    jwks_uri = os.environ.get("LABELING_JWKS_URI") or httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=10).json()["jwks_uri"]
    sf = make_session_factory(make_engine())
    identity = JwtIdentity(JwtConfig(issuer, clients), JwksKeys(jwks_uri), sf)
    ctx = Ctx(settings=settings, cvat=CvatClient(settings.cvat_url, settings.cvat_service_token), media=FixtureMedia({}),
              clips=LocalClipSource(os.environ.get("LABELING_CLIP_ROOT", "fixtures/generated")))
    return create_app(settings, sf, identity=identity, ctx=ctx)


app = build_app()
