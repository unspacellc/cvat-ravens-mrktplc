"""Integration-DB fixtures: a throwaway Postgres database per test session, built by the real Alembic migrations."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from api.labeling import fixtures as fx
from api.labeling.config import Settings
from api.labeling.cvat_client import CvatClient
from api.labeling.db import database_url, make_session_factory
from api.labeling.service import Ctx

ROOT = Path(__file__).resolve().parents[2]


def alembic_cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "api" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def db_url():
    base = make_url(database_url())
    name = f"labeling_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    command.upgrade(alembic_cfg(url), "head")
    yield url
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine(db_url):
    e = create_engine(db_url, future=True)
    yield e
    e.dispose()


@pytest.fixture
def session(engine):
    s = make_session_factory(engine)()
    yield s
    s.rollback()
    s.close()


# ---- real-CVAT fixtures (stack must be running: deploy/stack.sh up) --------------------------------------------


CVAT_URL = "http://localhost:8080"
FIXTURES = ROOT / "fixtures" / "generated"


@pytest.fixture(scope="session")
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture(scope="session")
def cvat_token():
    r = httpx.post(f"{CVAT_URL}/api/auth/login", json={"username": "admin", "password": "Adm1n-local-only!"})
    if r.status_code != 200:
        pytest.skip("CVAT stack not available")
    return r.json()["key"]


@pytest.fixture(scope="session")
def clips():
    import subprocess

    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, rate, secs in (("p1_a", 30, 4), ("p1_b", 25, 4), ("p1_c", 30, 2)):
        p = FIXTURES / f"{name}.mp4"
        if not p.exists():
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate={rate}:duration={secs}",
                            "-pix_fmt", "yuv420p", str(p)], check=True)
        out[name] = p
    return out


def build_ctx(cvat_token, media: dict) -> Ctx:
    settings = Settings(cvat_service_token=cvat_token, webhook_master_secret="test-master-secret")
    return Ctx(settings=settings, cvat=CvatClient(CVAT_URL, cvat_token), media=fx.FixtureMedia(media), clips=fx.LocalClipSource(FIXTURES))


@pytest.fixture
def tenant_ids():
    u = uuid.uuid4().hex[:8]
    return f"tenant-a-{u}", f"tenant-b-{u}"


def user_token(username: str) -> str:
    """Per-user CVAT API token (test helper; production users sign in via OIDC)."""
    import subprocess
    import textwrap

    code = textwrap.dedent(f"""
        from django.contrib.auth import get_user_model
        from rest_framework.authtoken.models import Token
        print("TOKEN=" + Token.objects.get_or_create(user=get_user_model().objects.get(username={username!r}))[0].key)
    """)
    out = subprocess.run(["docker", "exec", "-i", "cvat_server", "python3", "/opt/cvat/manage.py", "shell"],
                         input=code, text=True, capture_output=True, check=True, timeout=120).stdout
    return next(line.split("=", 1)[1] for line in out.splitlines() if line.startswith("TOKEN="))
