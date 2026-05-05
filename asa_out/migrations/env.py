"""
Alembic migration environment.

Reads DATABASE_URL from the environment (set by Docker Compose or .env),
converts the asyncpg URL to a psycopg2-compatible sync URL for migration
use only. The application itself continues to use asyncpg at runtime.

URL conversion rules:
  postgresql+asyncpg://... -> postgresql+psycopg2://...
  postgresql://...          -> postgresql+psycopg2://... (fallback)
"""
from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

# Import models so autogenerate can see the full metadata.
from atlas.models.orm import Base  # noqa: E402

target_metadata = Base.metadata


def _sync_url(url: str) -> str:
    """Convert asyncpg/bare postgres URL to psycopg2 for synchronous Alembic use."""
    url = re.sub(r"^postgresql\+asyncpg://", "postgresql+psycopg2://", url)
    url = re.sub(r"^postgresql://", "postgresql+psycopg2://", url)
    return url


def _get_url() -> str:
    """
    Read the database URL, preferring DATABASE_URL from the environment
    (set by Docker Compose) over the value in alembic.ini.

    This ensures the migration container targets the correct DB host
    ('db' inside Docker) rather than the localhost default in alembic.ini.
    """
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return _sync_url(env_url)
    ini_url = config.get_main_option("sqlalchemy.url", "")
    return _sync_url(ini_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()

    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
