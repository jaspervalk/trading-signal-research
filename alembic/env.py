"""Alembic migration environment for trading-signal-research.

Reuses the same DATABASE_URL the app does (via `app.config.load_env()`) so
migrations and the running app are always pointed at the same DB.

Target metadata is `app.models.Base.metadata`, so any ORM addition / column
change / drop becomes a candidate for `alembic revision --autogenerate`.

To run a migration against a different DB (e.g. a test fixture or a temp
empty DB used to capture a baseline), set the `DATABASE_URL` env var or
pass `-x dburl=<url>` on the alembic command line:

    alembic -x dburl=sqlite:///data/tsr-tmp.sqlite revision --autogenerate -m "..."

SQLite-specific config: `render_as_batch=True` is enabled in the migration
context so column drops / type changes / FK changes use the
copy-and-rename pattern SQLite requires (it doesn't support ALTER COLUMN).
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `app.*` importable when alembic is invoked from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from app.config import load_env  # noqa: E402  (after sys.path insert)
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_database_url() -> str:
    """Resolution order:
      1. -x dburl=<...> on the alembic CLI (highest priority — used by tests
         and the baseline-capture workflow)
      2. DATABASE_URL env var
      3. app.config.load_env().database_url (the canonical app-side default)
    """
    x_args = context.get_x_argument(as_dictionary=True)
    if "dburl" in x_args and x_args["dburl"]:
        return x_args["dburl"]
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    return load_env().database_url


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL; no DB connection)."""
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (real DB connection)."""
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = _resolve_database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.engine.url.drivername.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
