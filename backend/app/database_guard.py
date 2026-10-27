"""Fail-closed guards for test database isolation.

Why this module exists
----------------------
`app.database` used to build its Engine at import time from
`settings.database_url` (i.e. `backend/.env` -> the **product** database). Any
test that touched `SessionLocal()` or `engine` therefore hit production, and
`Base.metadata.create_all(bind=engine)` performed DDL there.

This module centralises the decision "which database may be opened while tests
are running" so that the decision happens **before** any Engine / connection is
created, and so that a missing or untrusted configuration fails closed instead
of falling back to the product configuration.

Design rules (do not weaken):
  * Test mode is opt-in via ``EIC_TEST_MODE=1``. It is never inferred from a
    database name, a URL shape or a missing configuration.
  * A **test run is detected independently of test mode** (:func:`is_test_context`).
    Being inside a test runner is enough to forbid the application DSN, so a
    forgotten ``EIC_TEST_MODE`` fails closed instead of quietly using production.
  * In test mode the database URL comes **only** from ``TEST_DATABASE_URL``.
    ``settings.database_url`` (the product DSN) is never used as a fallback.
  * Only in-memory SQLite, or a PostgreSQL target that matches an explicitly
    configured allow-list entry (host + port + database name), is accepted.
  * Product / reconstruction / drill / maintenance databases are always
    rejected, even if someone puts them in the allow-list.
  * The URL that actually reaches ``create_engine`` must match the authorized
    test target; a mismatch is *rejected*, never silently rewritten.
  * Errors never contain a password or a full DSN.

Nothing here opens a connection; every check is pure string/URL analysis.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Iterable, Mapping, Optional, Set, Tuple

from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import ArgumentError

# --------------------------------------------------------------------------- #
# Environment contract
# --------------------------------------------------------------------------- #
TEST_MODE_ENV = "EIC_TEST_MODE"
TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"
TEST_ALLOWLIST_ENV = "EIC_TEST_DATABASE_ALLOWLIST"
#: Set by the supported test entry point so child processes stay in test context.
TEST_RUNNER_ENV = "EIC_TEST_RUNNER"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Databases that must never be reachable from a test run. Kept as a hard deny
# list so that even a mistaken allow-list entry cannot unlock them.
FORBIDDEN_DATABASES = frozenset(
    {
        "ocr_platform",
        "ocr_platform_recon",
        "ocr_platform_restore_drill",
        "postgres",
        "template0",
        "template1",
    }
)

#: SQLite is only allowed as a pure in-memory database. File-backed SQLite would
#: create an artefact on disk and hide cross-test state, so it is rejected.
ALLOWED_SQLITE_URLS = frozenset({"sqlite://", "sqlite:///:memory:"})

#: PostgreSQL test databases must follow this naming contract ...
_TEST_DB_NAME_RE = re.compile(r"^ocr_platform_test(_[a-z0-9_]+)?$")
#: ... and must additionally match an explicit (host, port, database) allow-list.
_DEFAULT_PG_PORT = 5432

_PG_BACKENDS = frozenset({"postgresql", "postgres"})


class TestIsolationError(RuntimeError):
    """Base class for every refusal raised by this module."""


class MissingTestDatabaseConfig(TestIsolationError):
    """Test mode is active but no usable test database configuration exists."""


class UnsafeTestDatabase(TestIsolationError):
    """The configured test database target is not provably safe."""


# --------------------------------------------------------------------------- #
# Environment helpers
# --------------------------------------------------------------------------- #
def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def is_test_mode(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only when test mode was explicitly enabled."""
    return str(_env(env).get(TEST_MODE_ENV, "")).strip().lower() in _TRUTHY


#: ``__main__`` spec/module names that unambiguously mean "this is a test run".
_TEST_MAIN_SPECS = frozenset({"unittest.__main__", "pytest", "pytest.__main__", "unittest"})
#: ``__main__`` file names of the supported runners, including our own entry point.
_TEST_MAIN_FILES = {
    "unittest/__main__.py": "unittest",
    "pytest/__main__.py": "pytest",
    "py.test": "pytest",
    "run_isolated_tests.py": "eic-entry",
}

_UNSET = object()


def _runner_hint() -> Optional[str]:
    """Best-effort (stdlib-only) identification of the active test runner.

    Never imports anything and never touches the database; it only inspects the
    already-running process. ``None`` means "this looks like a production run".
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "pytest"
    if str(os.environ.get(TEST_RUNNER_ENV, "")).strip().lower() in _TRUTHY:
        return "eic-runner"
    main = sys.modules.get("__main__")
    spec_name = getattr(getattr(main, "__spec__", None), "name", None)
    if spec_name in _TEST_MAIN_SPECS:
        return str(spec_name)
    main_file = str(getattr(main, "__file__", "") or "").replace("\\", "/")
    for marker, hint in _TEST_MAIN_FILES.items():
        if main_file.endswith(marker):
            return hint
    return None


def is_test_context(env: Optional[Mapping[str, str]] = None, runner_hint=_UNSET) -> bool:
    """True when this process is a test run, **regardless of ``EIC_TEST_MODE``**.

    This is the second half of the fail-closed contract: forgetting
    ``EIC_TEST_MODE=1`` inside a test run must not silently enable the
    application (product) database.
    """
    hint = _runner_hint() if runner_hint is _UNSET else runner_hint
    if hint:
        return True
    return is_test_mode(env)


def database_target_key(url: Optional[str]) -> Optional[Tuple[str, str, int, str]]:
    """Credential-free identity of a database target, for equality comparison."""
    if url is None or not str(url).strip():
        return None
    try:
        parsed = make_url(str(url).strip())
    except ArgumentError:
        return None
    backend = parsed.get_backend_name()
    if backend == "sqlite":
        database = parsed.database
        if database in (None, "", ":memory:"):
            database = ":memory:"
        return ("sqlite", "", 0, database)
    if backend in _PG_BACKENDS:
        return (
            "postgresql",
            (parsed.host or "").lower(),
            parsed.port or _DEFAULT_PG_PORT,
            parsed.database or "",
        )
    return (backend, (parsed.host or "").lower(), parsed.port or 0, parsed.database or "")


def same_database_target(left: Optional[str], right: Optional[str]) -> bool:
    """True when both URLs name the same credential-free database target."""
    key_left = database_target_key(left)
    return key_left is not None and key_left == database_target_key(right)


def redact_database_url(url: Optional[str]) -> str:
    """Return a log-safe description of a database URL (never the password)."""
    if not url:
        return "<empty>"
    try:
        parsed = make_url(str(url))
    except Exception:  # noqa: BLE001 - never leak the raw value
        return "<unparsable>"
    backend = parsed.get_backend_name()
    if backend == "sqlite":
        return "sqlite:///%s" % (parsed.database or "<memory>")
    return "%s://%s@%s:%s/%s" % (
        backend,
        parsed.username or "<anonymous>",
        parsed.host or "<no-host>",
        parsed.port or _DEFAULT_PG_PORT,
        parsed.database or "<no-db>",
    )


def _parse_allowlist(raw: Optional[str]) -> Set[Tuple[str, int, str]]:
    """Parse ``host:port/dbname`` entries separated by commas."""
    entries: Set[Tuple[str, int, str]] = set()
    if not raw:
        return entries
    for chunk in str(raw).split(","):
        item = chunk.strip()
        if not item:
            continue
        host_port, _, dbname = item.partition("/")
        host, _, port_text = host_port.rpartition(":")
        if not host:  # no explicit port given
            host, port_text = host_port, ""
        try:
            port = int(port_text) if port_text else _DEFAULT_PG_PORT
        except ValueError:
            continue
        if host and dbname:
            entries.add((host.strip(), port, dbname.strip()))
    return entries


def load_test_allowlist(
    env: Optional[Mapping[str, str]] = None,
) -> Set[Tuple[str, int, str]]:
    """Explicit (host, port, database) allow-list for PostgreSQL test targets."""
    return _parse_allowlist(_env(env).get(TEST_ALLOWLIST_ENV))


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #
def assert_test_database_safe(
    url: Optional[str],
    allowlist: Optional[Iterable[Tuple[str, int, str]]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """Raise unless *url* is a provably safe **test** database target.

    Call this before creating any Engine. It performs no I/O.
    """
    if url is None or not str(url).strip():
        raise UnsafeTestDatabase("test database URL is empty")

    raw = str(url).strip()
    try:
        parsed = make_url(raw)
    except ArgumentError as exc:
        raise UnsafeTestDatabase(
            "test database URL is unparsable (%s)" % type(exc).__name__
        ) from None

    backend = parsed.get_backend_name()

    if backend == "sqlite":
        # ``sqlite://``, ``sqlite:///`` and ``sqlite:///:memory:`` are the same
        # purely in-memory database; any file path is a hard rejection.
        if parsed.database in (None, "", ":memory:"):
            return
        raise UnsafeTestDatabase(
            "only in-memory SQLite is allowed for tests; got %s"
            % redact_database_url(raw)
        )

    if backend not in _PG_BACKENDS:
        raise UnsafeTestDatabase(
            "unsupported test database backend %r (only sqlite in-memory "
            "or postgresql are allowed)" % backend
        )

    dbname = parsed.database
    if not dbname:
        raise UnsafeTestDatabase("test database name is missing")
    if dbname in FORBIDDEN_DATABASES:
        raise UnsafeTestDatabase(
            "refusing to use protected database %r as a test target" % dbname
        )
    if not _TEST_DB_NAME_RE.match(dbname):
        raise UnsafeTestDatabase(
            "test database name %r does not match the required pattern "
            "'ocr_platform_test[_suffix]'" % dbname
        )

    host = parsed.host
    if not host:
        raise UnsafeTestDatabase("test database host is missing (unix socket target)")
    port = parsed.port or _DEFAULT_PG_PORT

    allowed = set(allowlist) if allowlist is not None else load_test_allowlist(env)
    if not allowed:
        raise UnsafeTestDatabase(
            "no test database allow-list configured; set %s to an explicit "
            "host:port/dbname entry before using PostgreSQL in tests"
            % TEST_ALLOWLIST_ENV
        )
    if (host, port, dbname) not in allowed:
        raise UnsafeTestDatabase(
            "target %s:%s/%s is not in %s" % (host, port, dbname, TEST_ALLOWLIST_ENV)
        )


def resolve_test_database_url(env: Optional[Mapping[str, str]] = None) -> str:
    """Return the explicitly configured test DSN, or fail closed."""
    raw = _env(env).get(TEST_DATABASE_URL_ENV)
    if raw is None or not str(raw).strip():
        raise MissingTestDatabaseConfig(
            "%s is not set; tests must never fall back to the application or "
            "product database configuration" % TEST_DATABASE_URL_ENV
        )
    url = str(raw).strip()
    assert_test_database_safe(url, env=env)
    return url


def resolve_database_url(
    production_url: Optional[str], env: Optional[Mapping[str, str]] = None
) -> str:
    """Single entry point used by the Engine factory.

    * test mode        -> only ``TEST_DATABASE_URL`` (validated), never *production_url*
    * test run, no mode -> fail closed (``MissingTestDatabaseConfig``)
    * production       -> *production_url* unchanged
    """
    if is_test_mode(env):
        return resolve_test_database_url(env)
    if is_test_context(env):
        raise MissingTestDatabaseConfig(
            "a test run was detected but %s is not enabled; the application/"
            "product database is never used from a test process. Use the "
            "supported entry point (backend/run_isolated_tests.py) or set %s "
            "and %s explicitly" % (TEST_MODE_ENV, TEST_MODE_ENV, TEST_DATABASE_URL_ENV)
        )
    if production_url is None or not str(production_url).strip():
        raise TestIsolationError("application database URL is empty")
    return str(production_url)


def assert_engine_url_authorized(url: Optional[str], env: Optional[Mapping[str, str]] = None) -> str:
    """Validate the URL that is *about to* reach ``create_engine``.

    In a test run the target must (a) pass :func:`assert_test_database_safe` and
    (b) be the same database as the authorized ``TEST_DATABASE_URL``. A mismatch
    is refused outright — the target is never silently rewritten.
    """
    raw = "" if url is None else str(url).strip()
    if not is_test_context(env):
        return raw
    authorized = resolve_test_database_url(env)
    assert_test_database_safe(raw, env=env)
    if not same_database_target(raw, authorized):
        raise UnsafeTestDatabase(
            "refusing to build an engine for %s while the authorized test target "
            "is %s; targets are never redirected silently"
            % (redact_database_url(raw), redact_database_url(authorized))
        )
    return raw
