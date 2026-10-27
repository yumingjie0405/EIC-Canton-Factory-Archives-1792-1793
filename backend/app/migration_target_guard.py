"""Fail-closed target validation for Alembic migrations.

Why this module exists
----------------------
``alembic/env.py`` used to hand ``settings.database_url`` straight to
SQLAlchemy.  Nothing checked *which* database that was, so a stray ``.env`` or a
stale default could point a migration at the product database, and any Alembic
invocation (CLI, ``-c other.ini``, or the Python API) would proceed.

This module is the single choke point that env.py calls before a URL reaches an
engine.  It is deliberately **pure**: it parses strings and reads environment
variables, never imports an engine, never connects and never touches SQL.

Contract
--------
* no URL                      -> :class:`MissingMigrationTarget`
* unparsable URL              -> :class:`InvalidMigrationTarget`
* no allow-list configured    -> :class:`MissingMigrationAuthorization`
* target not allow-listed     -> :class:`MigrationTargetNotAllowed`
* production database         -> additionally requires ``EIC_MIGRATION_ALLOW_PRODUCTION=1``
* maintenance databases       -> always refused, even if allow-listed
* write command without opt-in-> :class:`MissingMigrationAuthorization`
* direct API call             -> additionally requires ``EIC_ALEMBIC_API_CALL=1``
* test run                    -> the target must also satisfy
                                 :func:`app.database_guard.assert_test_database_safe`,
                                 so a test process can never migrate the product DB

Every error message is credential-free: it is built from a redacted target
description (scheme/host/port/database only), never from the raw DSN.
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional, Set, Tuple

from app.config import settings
from app.database_guard import (
    TestIsolationError,
    assert_test_database_safe,
    database_target_key,
    is_test_context,
    redact_database_url,
)

# --------------------------------------------------------------------------- #
# Environment contract
# --------------------------------------------------------------------------- #
#: Comma-separated ``host:port/dbname`` entries. Without it nothing may run.
ALLOWED_TARGETS_ENV = "EIC_MIGRATION_ALLOWED_TARGETS"
#: Required for any command that can change schema or version state.
MIGRATIONS_ENABLED_ENV = "EIC_MIGRATIONS_ENABLED"
#: Required *in addition* to the allow-list to touch the product database.
ALLOW_PRODUCTION_ENV = "EIC_MIGRATION_ALLOW_PRODUCTION"
#: Required when env.py is executed from the Python API rather than the CLI.
API_CALL_ENV = "EIC_ALEMBIC_API_CALL"

#: Never reachable through a migration, even if somebody allow-lists them.
FORBIDDEN_MIGRATION_DATABASES = frozenset({"postgres", "template0", "template1"})
#: Database whose migration requires a second, explicit authorization flag.
PRODUCTION_DATABASES = frozenset({"ocr_platform"})

#: Commands that can change the database or the version table.
WRITE_COMMANDS = frozenset({"upgrade", "downgrade", "stamp", "ensure_version"})
#: Commands that only read. Anything else - including an unknown or empty
#: command name - is treated as a write (fail closed).
READ_ONLY_COMMANDS = frozenset({"current", "check", "heads", "history", "show", "branches"})

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ENTRY = re.compile(r"^(?P<host>[^:/\s]+):(?P<port>\d{1,5})/(?P<dbname>[A-Za-z0-9_]+)$")

TargetKey = Tuple[str, str, int, str]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class MigrationTargetError(RuntimeError):
    """Base class for every refusal raised by this module."""


class MissingMigrationTarget(MigrationTargetError):
    """No database URL is configured for Alembic."""


class InvalidMigrationTarget(MigrationTargetError):
    """The configured URL cannot be normalised."""


class MissingMigrationAuthorization(MigrationTargetError):
    """An explicit authorization switch is absent."""


class MigrationTargetNotAllowed(MigrationTargetError):
    """The target is not allow-listed (or is forbidden outright)."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _first_line(text: object) -> str:
    return str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""


def describe_target(url: Optional[str]) -> str:
    """Credential-free description of a target: ``scheme://host:port/dbname``."""
    key = database_target_key(url)
    if key is None:
        return "<unparsable target>"
    scheme, host, port, dbname = key
    if scheme == "sqlite":
        return "sqlite:///%s" % dbname
    return "%s://%s:%d/%s" % (scheme, host or "<no-host>", port, dbname or "<no-database>")


def normalize_target(url: Optional[str]) -> Optional[TargetKey]:
    """Comparable ``(scheme, host, port, dbname)`` identity, or ``None``."""
    return database_target_key(url)


def parse_allowed_targets(raw: Optional[str]) -> Set[Tuple[str, int, str]]:
    """Parse ``host:port/dbname[,host:port/dbname...]``; malformed entries are dropped."""
    allowed: Set[Tuple[str, int, str]] = set()
    for chunk in str(raw or "").replace(";", ",").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        match = _ENTRY.match(entry)
        if not match:
            continue
        allowed.add(
            (match.group("host").lower(), int(match.group("port")), match.group("dbname"))
        )
    return allowed


def migration_command(config: object = None) -> Tuple[str, bool]:
    """Return ``(command_name, is_api_call)`` for the active Alembic run.

    The CLI fills ``config.cmd_opts.cmd``; a direct ``command.upgrade(...)`` call
    leaves ``cmd_opts`` as ``None``. The second element therefore means "the CLI
    wrapper was bypassed", which demands an extra acknowledgement.
    """
    opts = getattr(config, "cmd_opts", None)
    if opts is None:
        return "", True
    cmd = getattr(opts, "cmd", None)
    if isinstance(cmd, (list, tuple)):
        name = str(cmd[0]) if cmd else ""
    else:
        name = str(cmd or "")
    return name.strip().lower(), False


def is_write_command(command: str) -> bool:
    """True unless the command is a known read-only one (unknown means write)."""
    return command.strip().lower() not in READ_ONLY_COMMANDS


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #
def assert_migration_target(
    url: Optional[str],
    *,
    command: str = "",
    api_call: bool = False,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Validate *url* for the given Alembic command, or raise. Returns *url*.

    The URL is returned unchanged: a rejected target is never silently rewritten
    into a different one.
    """
    raw = "" if url is None else str(url).strip()
    if not raw:
        raise MissingMigrationTarget(
            "no database URL is configured for Alembic; set DATABASE_URL in "
            "backend/.env (git-ignored) or export it in the environment"
        )

    key = normalize_target(raw)
    if key is None:
        raise InvalidMigrationTarget(
            "the configured Alembic URL cannot be parsed; refusing to run migrations"
        )

    scheme, host, port, dbname = key
    if scheme != "postgresql":
        raise MigrationTargetNotAllowed(
            "only PostgreSQL targets are supported for migrations, got %s"
            % describe_target(raw)
        )
    if dbname in FORBIDDEN_MIGRATION_DATABASES:
        raise MigrationTargetNotAllowed(
            "refusing to run migrations against the maintenance database %r"
            % (dbname,)
        )

    environment = _env(env)
    allowed = parse_allowed_targets(environment.get(ALLOWED_TARGETS_ENV))
    if not allowed:
        raise MissingMigrationAuthorization(
            "%s is not set; every migration command is refused until the target "
            "is authorized explicitly" % ALLOWED_TARGETS_ENV
        )
    if (host, port, dbname) not in allowed:
        raise MigrationTargetNotAllowed(
            "refusing to run migrations against %s: this target is not listed in %s"
            % (describe_target(raw), ALLOWED_TARGETS_ENV)
        )

    if dbname in PRODUCTION_DATABASES and not _truthy(environment.get(ALLOW_PRODUCTION_ENV)):
        raise MigrationTargetNotAllowed(
            "refusing to run migrations against the production database %r; "
            "authorize it separately with %s=1 (never enabled by default)"
            % (dbname, ALLOW_PRODUCTION_ENV)
        )

    if is_test_context(env):
        try:
            assert_test_database_safe(raw, env=env)
        except TestIsolationError as exc:
            raise MigrationTargetNotAllowed(
                "migrations are refused in a test run: %s" % (_first_line(exc),)
            ) from None

    if api_call and not _truthy(environment.get(API_CALL_ENV)):
        raise MissingMigrationAuthorization(
            "this Alembic run was started through the Python API, not the CLI; "
            "set %s=1 to acknowledge that the entry-point checks are being bypassed"
            % API_CALL_ENV
        )

    if is_write_command(command) and not _truthy(environment.get(MIGRATIONS_ENABLED_ENV)):
        raise MissingMigrationAuthorization(
            "%r can change the database; set %s=1 to authorize it explicitly"
            % (command or "an unidentified command", MIGRATIONS_ENABLED_ENV)
        )

    return raw


def guard_migration_environment(config: object = None, env: Optional[Mapping[str, str]] = None) -> str:
    """Validate the configured URL for the active Alembic command.

    This is what ``alembic/env.py`` calls. It fails closed long before any engine
    or connection is created.
    """
    command, api_call = migration_command(config)
    url = settings.require_database_url()
    return assert_migration_target(url, command=command, api_call=api_call, env=env)


def redacted(url: Optional[str]) -> str:
    """Convenience wrapper around the guard's URL redaction for log messages."""
    return redact_database_url(url)


def apply_guard_to_config(config: object = None, env: Optional[Mapping[str, str]] = None) -> str:
    """Validate the target **and write the validated URL into** *config*.

    Passing the guard is not enough on its own: Alembic reads the URL from
    ``config.get_section(...)`` (online mode) and ``config.get_main_option(...)``
    (offline mode). If the validated value were not copied back, the placeholder
    from the ``.ini`` would still be the URL that reaches SQLAlchemy. This helper
    makes the two steps inseparable, and refuses to continue when the config
    object cannot accept the value - failing closed instead of "passing a check
    that has no effect".
    """
    url = guard_migration_environment(config, env=env)
    setter = getattr(config, "set_main_option", None)
    if not callable(setter):
        raise InvalidMigrationTarget(
            "the Alembic config object cannot store the validated URL "
            "(no set_main_option); refusing to continue"
        )
    setter("sqlalchemy.url", url)
    return url
