"""Fail-closed bootstrap that every database-touching test module imports first.

The problem it solves
---------------------
``unittest`` (and ``pytest``, and IDE runners) import a test module before any
fixture runs. If that module touches ``SessionLocal()`` / ``get_engine()`` during
import, configuration must already be validated — otherwise the *first* database
access happens before any guard could run.

Importing this module and calling :func:`require_isolated_test_environment` at the
top of such a module makes the wrong case loud and immediate: the module fails to
import, so the test run aborts before an engine or a connection exists.

This module never creates an engine and never connects to anything; it validates
configuration only.
"""
from __future__ import annotations

import sys
from typing import Optional

from app.database_guard import (
    TEST_ALLOWLIST_ENV,
    TEST_DATABASE_URL_ENV,
    TEST_MODE_ENV,
    MissingTestDatabaseConfig,
    TestIsolationError,
    database_target_key,
    is_test_mode,
    redact_database_url,
    resolve_test_database_url,
)

_ANNOUNCED = False


def describe_isolation() -> dict:
    """Credential-free description of the active isolation configuration."""
    url = resolve_test_database_url()
    return {
        "test_mode": is_test_mode(),
        "target": redact_database_url(url),
        "backend": (database_target_key(url) or ("unknown",))[0],
        "allowlist_env": TEST_ALLOWLIST_ENV,
    }


def require_isolated_test_environment(module_name: Optional[str] = None) -> str:
    """Fail closed unless an authorized test database is configured.

    Returns the (credential-free) target description. Raises
    :class:`MissingTestDatabaseConfig` / :class:`UnsafeTestDatabase` otherwise.
    """
    global _ANNOUNCED
    origin = module_name or (sys._getframe(1).f_globals.get("__name__") or "unknown")
    if not is_test_mode():
        raise MissingTestDatabaseConfig(
            "%s: %s is not enabled; refusing to import a database-touching test "
            "module. Run the suite through backend/run_isolated_tests.py (or set "
            "%s=1 and %s explicitly)" % (origin, TEST_MODE_ENV, TEST_MODE_ENV, TEST_DATABASE_URL_ENV)
        )
    try:
        description = describe_isolation()
    except TestIsolationError as exc:
        raise type(exc)("%s: %s" % (origin, exc)) from None
    if not _ANNOUNCED:
        _ANNOUNCED = True
        print(
            "[isolation] test mode active; database target = %s" % description["target"],
            file=sys.stderr,
        )
    return description["target"]
