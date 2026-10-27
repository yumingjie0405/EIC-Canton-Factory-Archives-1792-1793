"""Database connection and session management.

Engine creation is deliberately **lazy and centralised**:

* importing this module no longer opens (or even constructs) an Engine, so
  ``from app.database import Base`` is safe for models and for tests;
* every Engine is built by :func:`build_engine`, which routes the target URL
  through :func:`app.database_guard.assert_engine_url_authorized`. With
  ``EIC_TEST_MODE=1`` that guard refuses to use ``settings.database_url``
  (the product DSN), demands an explicitly allow-listed test target, and
  rejects any URL that is not the authorized test target;
* ``engine`` and ``SessionLocal`` keep their historical names and semantics, so
  production call sites (``app/main.py``, ``app/api/deps.py``,
  ``app/tasks/*.py``) are unchanged.
"""
from typing import Callable, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import MissingDatabaseConfig, settings
from app.database_guard import (
    UnsafeTestDatabase,
    assert_engine_url_authorized,
    is_test_context,
    is_test_mode,
    redact_database_url,
    resolve_database_url,
    same_database_target,
)

Base = declarative_base()

_engine: Optional[Engine] = None
_session_factory: Optional[Callable[..., object]] = None


def _authorized_target() -> str:
    """Resolve the Engine target, failing loudly when configuration is absent.

    The order is deliberate:

    * a test run - or a process that merely *looks* like a test run - still
      goes through :func:`resolve_database_url`, so a missing
      ``EIC_TEST_MODE`` is refused by the guard instead of falling through
      to the production branch;
    * otherwise the configured production URL is required explicitly: there
      is no built-in fallback DSN.
    """
    if is_test_mode() or is_test_context():
        return resolve_database_url(settings.database_url)
    return settings.require_database_url()


def build_engine(url: str, *, echo: Optional[bool] = None) -> Engine:
    """Create an Engine for *url*.

    The URL handed to ``create_engine`` is validated here — not merely assumed to
    have been validated by :func:`get_engine`. In a test run it must equal the
    authorized ``TEST_DATABASE_URL`` target, so a caller cannot obtain an engine
    for the product database by calling this factory directly.

    SQLite targets (test-only, in-memory) cannot accept the production pool
    arguments and need a shared single connection, so they get their own branch.
    """
    raw = "" if url is None else str(url).strip()
    if not raw:
        raise MissingDatabaseConfig(
            "build_engine() received an empty database URL; set DATABASE_URL "
            "in backend/.env (git-ignored) or pass an explicit URL"
        )
    target = assert_engine_url_authorized(raw)
    echo_flag = settings.database_echo if echo is None else echo
    if target.startswith("sqlite"):
        return create_engine(
            target,
            echo=echo_flag,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    return create_engine(
        target,
        echo=echo_flag,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def get_engine() -> Engine:
    """Return the process-wide Engine, creating it on first use."""
    global _engine
    target = _authorized_target()
    if _engine is not None:
        if is_test_context() and not same_database_target(str(_engine.url), target):
            raise UnsafeTestDatabase(
                "a cached engine for %s does not match the currently authorized "
                "test target %s; call reset_engine_state() after changing test "
                "configuration" % (redact_database_url(str(_engine.url)), redact_database_url(target))
            )
        return _engine
    _engine = build_engine(target)
    return _engine


def get_session_factory() -> Callable[..., object]:
    """Return the ``sessionmaker`` bound to the *current* engine."""
    global _session_factory
    engine = get_engine()
    bound = getattr(_session_factory, "kw", {}).get("bind") if _session_factory else None
    if _session_factory is None or bound is not engine:
        _session_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=engine
        )
    return _session_factory


def reset_engine_state() -> None:
    """Drop cached Engine/session factory (used by tests and reload paths)."""
    global _engine, _session_factory
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:  # noqa: BLE001 - disposal must never mask the reset
            pass
    _engine = None
    _session_factory = None


class _LazySessionMaker:
    """Callable stand-in for the historical module-level ``SessionLocal``.

    ``SessionLocal()`` still returns a Session, but the binding Engine is only
    resolved (and therefore only validated) at call time, not at import time.
    """

    __slots__ = ()

    def __call__(self, **kwargs):
        return get_session_factory()(**kwargs)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<lazy SessionLocal; engine not created yet>"


SessionLocal = _LazySessionMaker()


def __getattr__(name):
    """Resolve ``engine`` lazily; every other name is a genuine AttributeError."""
    if name == "engine":
        return get_engine()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def get_db():
    """Dependency for getting database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
