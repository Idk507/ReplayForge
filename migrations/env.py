"""Explicit online migrations; credentials are never stored in alembic.ini."""

import os

from alembic import context
from sqlalchemy import create_engine

url = os.environ.get("REPLAYFORGE_MIGRATION_DATABASE_URL") or os.environ.get(
    "DATABASE_URL"
)
if not url:
    raise RuntimeError("DB_CONFIG_MISSING")
if context.is_offline_mode():
    raise RuntimeError("DB_OFFLINE_MIGRATION_UNSUPPORTED")
with create_engine(
    url.replace("postgresql://", "postgresql+psycopg://", 1), hide_parameters=True
).connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
