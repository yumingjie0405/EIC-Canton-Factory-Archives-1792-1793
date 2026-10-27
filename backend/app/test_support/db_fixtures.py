"""Test-only database fixtures.

Everything here routes through the fail-closed guard in
:mod:`app.database_guard`; production code must never import this module.

Two tiers of tests are supported:

``sqlite`` tier
    ``TEST_DATABASE_URL=sqlite://`` (pure in-memory, no file created). Fast, but
    **not** PostgreSQL: ENUM/JSONB/ILIKE/FK-deferral/locking semantics differ.

``postgresql`` tier
    ``TEST_DATABASE_URL=postgresql://.../ocr_platform_test[_suffix]`` plus
    ``EIC_TEST_DATABASE_ALLOWLIST=host:port/dbname``. Required for tests that
    depend on real PostgreSQL behaviour; such suites must be annotated with
    :func:`requires_postgres_test_db` so they can never run against the
    sqlite tier and silently "pass".
"""
from __future__ import annotations

import unittest
from typing import Callable, Optional

from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine
from app.database_guard import (
    TEST_DATABASE_URL_ENV,
    TEST_MODE_ENV,
    MissingTestDatabaseConfig,
    TestIsolationError,
    assert_test_database_safe,
    is_test_mode,
    redact_database_url,
    resolve_test_database_url,
)

_PG_BACKENDS = ("postgresql", "postgres")


def _require_test_mode() -> None:
    if not is_test_mode():
        raise MissingTestDatabaseConfig(
            "%s is not enabled; test fixtures refuse to create engines unless "
            "test mode is explicitly turned on" % TEST_MODE_ENV
        )


def make_test_engine(url: Optional[str] = None, *, create_schema: bool = True) -> Engine:
    """Validate the target, then create an Engine for it.

    The guard runs **before** :func:`app.database.build_engine`, so an unsafe
    target can never even be handed to SQLAlchemy.
    """
    _require_test_mode()
    target = resolve_test_database_url() if url is None else str(url)
    assert_test_database_safe(target)
    engine = build_engine(target)
    if create_schema:
        Base.metadata.create_all(bind=engine)
    return engine


def make_session_factory(engine: Engine) -> Callable[..., object]:
    """Sessionmaker bound to a test engine (never to the application engine)."""
    assert_engine_is_test_engine(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def assert_engine_is_test_engine(engine: Engine) -> None:
    """Re-validate an existing Engine's target (defence in depth)."""
    assert_test_database_safe(str(engine.url))


def drop_test_schema(engine: Engine) -> None:
    """Drop the schema created for a test engine (never touches other targets)."""
    assert_engine_is_test_engine(engine)
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def postgres_requirement_reason() -> Optional[str]:
    """Return a skip reason when no authorized PostgreSQL test target exists."""
    if not is_test_mode():
        return (
            "requires an isolated PostgreSQL test database; %s is not enabled"
            % TEST_MODE_ENV
        )
    try:
        url = resolve_test_database_url()
    except TestIsolationError as exc:
        return "requires an isolated PostgreSQL test database: %s" % exc
    try:
        backend = make_url(url).get_backend_name()
    except Exception:  # noqa: BLE001 - unparsable target is a skip, not a pass
        return "requires an isolated PostgreSQL test database (unparsable target)"
    if backend not in _PG_BACKENDS:
        return (
            "requires an isolated PostgreSQL test database; %s points at a "
            "%s target (%s)"
            % (TEST_DATABASE_URL_ENV, backend, redact_database_url(url))
        )
    return None


def requires_postgres_test_db(cls):
    """Class decorator: skip (never fake-pass) when no PG test target is authorized."""
    reason = postgres_requirement_reason()
    if reason:
        return unittest.skip(reason)(cls)
    cls.REQUIRES_POSTGRES_TEST_DB = True
    return cls
