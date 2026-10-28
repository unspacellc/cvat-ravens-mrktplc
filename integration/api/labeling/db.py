from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "postgresql+psycopg://labeling:labeling-local-only@localhost:5433/labeling"


def database_url() -> str:
    return os.environ.get("LABELING_DATABASE_URL", DEFAULT_URL)


def make_engine(url: str | None = None):
    return create_engine(url or database_url(), pool_pre_ping=True, future=True)


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, future=True)
