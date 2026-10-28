"""E2E fixtures: the real mock-IdP browser flow, real CVAT sessions, portal API verifying IdP-issued JWTs via JWKS."""

from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest

from tests.integration.conftest import *  # noqa: F401,F403  (db/cvat/clips fixtures)

CVAT = "http://localhost:8080"
IDP_HOST = "localhost:8081"
ISSUER = f"http://{IDP_HOST}/default"
PORTAL_CLIENT = "portal-web"


def browser_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit(p._replace(netloc=IDP_HOST)) if p.netloc.startswith("host.docker.internal") else url


@pytest.fixture(scope="session", autouse=True)
def _idp_up():
    try:
        ok = httpx.get(f"{ISSUER}/.well-known/openid-configuration", timeout=3).status_code == 200
        ok = ok and httpx.get(f"{CVAT}/api/auth/social/oidc/portal/login/", follow_redirects=False, timeout=5).status_code == 302
    except httpx.HTTPError:
        ok = False
    if not ok:
        pytest.skip("needs WITH_IDP=1 deploy/stack.sh up")


def _claims(sub: str) -> str:
    return json.dumps({"sub": sub, "email": f"{sub}@example.com", "preferred_username": sub})


def cvat_session(launch_url: str, sub: str) -> httpx.Client:
    """Follow the portal's launch URL like a browser: CVAT -> IdP login as `sub` -> CVAT callback -> job page."""
    c = httpx.Client(base_url=CVAT, timeout=30, follow_redirects=False)
    r = c.get(launch_url.removeprefix(CVAT))
    assert r.status_code == 302, r.text[:200]
    authorize = browser_url(r.headers["location"])
    idp = httpx.Client(timeout=30)
    assert idp.get(authorize).status_code == 200
    cb = idp.post(authorize, data={"username": sub, "claims": _claims(sub)}, follow_redirects=False)
    assert cb.status_code == 302
    final = c.get(cb.headers["location"].removeprefix(CVAT))
    c.last = final  # type: ignore[attr-defined]
    return c


def idp_access_token(sub: str, client_id: str = PORTAL_CLIENT) -> str:
    """Real authorization-code exchange against the IdP; the token's signature is verified by the portal via JWKS."""
    idp = httpx.Client(timeout=30)
    redirect = "http://localhost:3000/callback"
    authorize = f"{ISSUER}/authorize"
    params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect, "scope": "openid", "state": "s", "nonce": "n"}
    r = idp.post(authorize, params=params, data={"username": sub, "claims": _claims(sub)}, follow_redirects=False)
    assert r.status_code == 302, r.text[:300]
    code = dict(httpx.URL(r.headers["location"]).params)["code"]
    t = idp.post(f"{ISSUER}/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                           "client_id": client_id, "client_secret": "x"})
    assert t.status_code == 200, t.text
    return t.json()["access_token"]
