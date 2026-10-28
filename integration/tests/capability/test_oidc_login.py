"""Community-edition OIDC login via the labeling patch (allauth openid_connect). Needs `WITH_IDP=1 deploy/stack.sh up`."""

import json
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest

from .conftest import BASE_URL

pytestmark = pytest.mark.capability
IDP_BROWSER_HOST = "localhost:8081"  # host-side name for the IdP the containers know as host.docker.internal:8081


def _browser_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(netloc=IDP_BROWSER_HOST)) if parts.netloc.startswith("host.docker.internal") else url


def _idp_available() -> bool:
    """Needs both the mock IdP and a CVAT started with the OIDC overlay (WITH_IDP=1)."""
    try:
        idp_ok = httpx.get(f"http://{IDP_BROWSER_HOST}/default/.well-known/openid-configuration", timeout=3).status_code == 200
        cvat_ok = httpx.get(f"{BASE_URL}/api/auth/social/oidc/portal/login/", follow_redirects=False, timeout=5).status_code == 302
        return idp_ok and cvat_ok
    except httpx.HTTPError:
        return False


@pytest.fixture
def idp():
    if not _idp_available():
        pytest.skip("mock IdP not running (WITH_IDP=1 deploy/stack.sh up)")


def _login(username: str, email: str, *, next_path: str = "/") -> httpx.Client:
    c = httpx.Client(base_url=BASE_URL, timeout=30, follow_redirects=False)
    r = c.get("/api/auth/social/oidc/portal/login/", params={"process": "login", "next": next_path})
    assert r.status_code == 302, r.text[:200]
    authorize = _browser_url(r.headers["location"])
    idp = httpx.Client(timeout=30, follow_redirects=False)
    form = idp.get(authorize)
    assert form.status_code == 200
    claims = json.dumps({"sub": username, "email": email, "preferred_username": username, "email_verified": True})
    r = idp.post(authorize, data={"username": username, "claims": claims})
    assert r.status_code == 302, r.text[:300]
    callback = r.headers["location"]
    assert callback.startswith(BASE_URL), callback
    r = c.get(callback.replace(BASE_URL, ""))
    assert r.status_code in (302, 200), r.text[:300]
    return c


def test_oidc_login_creates_per_user_session(idp):
    name = f"oidc{uuid.uuid4().hex[:8]}"
    c = _login(name, f"{name}@example.com")
    me = c.get("/api/users/self")
    assert me.status_code == 200, me.text[:200]
    assert me.json()["username"] == name and me.json()["email"] == f"{name}@example.com"
    # second login maps to the same CVAT user (no duplicate)
    again = _login(name, f"{name}@example.com").get("/api/users/self").json()
    assert again["id"] == me.json()["id"]


def test_distinct_idp_users_get_distinct_identities(idp):
    a, b = f"oidc{uuid.uuid4().hex[:8]}", f"oidc{uuid.uuid4().hex[:8]}"
    ida = _login(a, f"{a}@example.com").get("/api/users/self").json()["id"]
    idb = _login(b, f"{b}@example.com").get("/api/users/self").json()["id"]
    assert ida != idb


def test_unauthenticated_client_has_no_session(idp):
    assert httpx.get(f"{BASE_URL}/api/users/self").status_code == 401


def test_return_url_is_restricted_to_own_host(idp):
    name = f"oidc{uuid.uuid4().hex[:8]}"
    c = httpx.Client(base_url=BASE_URL, timeout=30)
    r = c.get("/api/auth/social/oidc/portal/login/", params={"process": "login", "next": "https://evil.example/steal"})
    authorize = _browser_url(r.headers["location"]) if r.status_code == 302 else None
    idp_c = httpx.Client(timeout=30)
    claims = json.dumps({"sub": name, "email": f"{name}@example.com", "preferred_username": name})
    cb = idp_c.post(authorize, data={"username": name, "claims": claims})
    final = c.get(cb.headers["location"].replace(BASE_URL, ""))
    assert "evil.example" not in final.headers.get("location", "")
