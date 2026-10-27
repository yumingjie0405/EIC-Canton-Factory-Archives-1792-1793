"""Alembic environment for the NEW full-schema baseline chain.

Independence
------------
* ``script_location`` is ``backend/alembic_baseline``; the legacy chain in
  ``backend/alembic/versions/`` is never read, moved or rewritten here.
* ``version_table`` is ``alembic_version_baseline``, so the two chains never
  share version state.
* no revision exists yet: this round only establishes the runtime skeleton.

Target protection
-----------------
:func:`app.migration_target_guard.apply_guard_to_config` runs at import time,
before any engine or connection exists. It refuses the run when the URL is
missing, the target is not allow-listed, the product database is targeted
without its second switch, the process is a test run, an API invocation is not
acknowledged, or a write command is not authorized. It also writes the validated
URL back into this config, so ``engine_from_config()`` (online) and ``url=``
(offline) both use exactly the target that was checked.
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

config = context.config

# The baseline ini is deliberately minimal; run logging configuration only when
# the file actually defines loggers, otherwise fileConfig() would raise.
if config.config_file_name is not None:
    _file_config = getattr(config, "file_config", None)
    if _file_config is None or _file_config.has_section("loggers"):
        fileConfig(config.config_file_name)

import os
import sys

# Ensure the backend directory is importable (mirrors the legacy env.py).
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.database import Base
import app.models  # ensure all models are imported
from app.migration_target_guard import apply_guard_to_config

target_metadata = Base.metadata

#: Independent version state for this chain (never the legacy ``alembic_version``).
VERSION_TABLE = "alembic_version_baseline"

# Fail closed. Also injects the validated URL into this config, so every later
# read of ``sqlalchemy.url`` (section or main option) yields the checked target.
validated_url = apply_guard_to_config(config)


def run_migrations_offline() -> None:
    """Emit SQL without an Engine, using only the validated target URL."""
    context.configure(
        url=validated_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Create an Engine from the already-validated section and run migrations."""
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
