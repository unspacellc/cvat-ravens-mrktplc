"""Capability harness fixtures. Requires the pinned CVAT stack (deploy/stack.sh up) and an `admin` superuser."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("CVAT_URL", "http://localhost:8080")
ADMIN_USER = os.environ.get("CVAT_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("CVAT_ADMIN_PASSWORD", "Adm1n-local-only!")
# Address CVAT containers use to reach this host (allowlisted in Smokescreen by deploy/stack.sh).
WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "host.docker.internal")
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "generated"

pytestmark = pytest.mark.capability


def pytest_collection_modifyitems(items):
    for item in items:
        if "capability" in str(item.fspath):
            item.add_marker(pytest.mark.capability)


class CvatApi:
    """Thin REST client; sends `X-Organization` when `org` is set."""

    def __init__(self, token: str, org: str | None = None):
        headers = {"Authorization": f"Token {token}"}
        if org:
            headers["X-Organization"] = org
        self.http = httpx.Client(base_url=BASE_URL, headers=headers, timeout=60)

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        return self.http.request(method, url, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)


@pytest.fixture(scope="session")
def admin_token() -> str:
    r = httpx.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"CVAT login failed: {r.status_code} {r.text}"
    return r.json()["key"]


@pytest.fixture(scope="session")
def admin(admin_token) -> CvatApi:
    return CvatApi(admin_token)


@pytest.fixture
def org(admin_token) -> tuple[CvatApi, str]:
    """A fresh organization owned by admin. Returns (org-scoped client, slug)."""
    slug = f"t{uuid.uuid4().hex[:10]}"
    r = CvatApi(admin_token).post("/api/organizations", json={"slug": slug, "name": slug})
    assert r.status_code == 201, r.text
    return CvatApi(admin_token, org=slug), slug


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="session")
def clip_cfr() -> Path:
    """120 frames, 30 fps, 320x240, constant frame rate."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURE_DIR / "cfr_120f_30fps.mp4"
    if not out.exists():
        _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=4", "-pix_fmt", "yuv420p", str(out))
    return out


@pytest.fixture(scope="session")
def clip_vfr() -> Path:
    """Variable frame rate: 2s at 30 fps then 2s at 10 fps (timestamps rewritten), nonzero start offset."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURE_DIR / "vfr_offset.mp4"
    if not out.exists():
        a, b = FIXTURE_DIR / "_a.mp4", FIXTURE_DIR / "_b.mp4"
        _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2", "-pix_fmt", "yuv420p", str(a))
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2", "-pix_fmt", "yuv420p", str(b))
        lst = FIXTURE_DIR / "_list.txt"
        lst.write_text(f"file '{a}'\nfile '{b}'\n")
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-output_ts_offset", "1.5", str(out))
    return out


@dataclass
class ReceivedHook:
    path: str
    headers: dict[str, str]
    raw_body: bytes

    def json(self):
        return json.loads(self.raw_body)


@dataclass
class WebhookReceiver:
    port: int
    hooks: list[ReceivedHook] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    status: int = 200

    @property
    def url(self) -> str:
        return f"http://{WEBHOOK_HOST}:{self.port}/hook"


@pytest.fixture
def webhook_receiver():
    recv = WebhookReceiver(port=0)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with recv.lock:
                recv.hooks.append(ReceivedHook(self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            self.send_response(recv.status)
            self.end_headers()

        def log_message(self, *a):  # silence
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    recv.port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield recv
    server.shutdown()
