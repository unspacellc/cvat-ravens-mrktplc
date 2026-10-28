from alembic import context
from sqlalchemy import create_engine

from api.labeling.db import database_url
from api.labeling.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or database_url()
    engine = create_engine(url, future=True)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
