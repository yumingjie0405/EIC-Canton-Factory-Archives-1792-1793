"""Single supported entry point for running backend tests in isolation.

Why a dedicated entry point
---------------------------
``unittest``, ``pytest`` and IDE runners all import test modules before running
any fixture. Once a module is imported it is too late to "decide" about the
database, so isolation has to be established by whoever starts the process.

This script therefore:

1. reads and validates the environment **using the standard library only**;
2. then imports :mod:`app.database_guard` (which creates no engine) and runs the
   full target validation;
3. only then imports the test modules / the test runner.

If anything is missing or unsafe the process exits with code 2 *before* any
business module is imported and before any engine can exist.

Usage (Windows PowerShell)::

    # tier 0: isolation guard unit tests, no database at all
    .\\venv\\Scripts\\python.exe run_isolated_tests.py --tier guard

    # tier 1: in-memory SQLite suites
    .\\venv\\Scripts\\python.exe run_isolated_tests.py --tier sqlite

    # tier 2: PostgreSQL suites (requires an authorized test database)
    $env:TEST_DATABASE_URL = "postgresql://<user>:<password>@127.0.0.1:5432/ocr_platform_test"
    $env:EIC_TEST_DATABASE_ALLOWLIST = "127.0.0.1:5432/ocr_platform_test"
    .\\venv\\Scripts\\python.exe run_isolated_tests.py --tier postgres

The tier argument is **required on purpose**: there is no implicit default that
could silently pick a database for you.

Warning
-------
Several PostgreSQL suites call ``drop_test_schema()``, which drops *all* tables in
the target. Never point ``--tier postgres`` at a database that also holds
demonstration or research data; use a dedicated ``ocr_platform_test_*`` instance.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
TESTS_DIR = BACKEND_DIR / "tests"

GUARD_PATTERNS = ("test_db_guard.py",)
#: Pure suites: no engine, no connection. They are still part of the single
#: entry point so a passing ad-hoc run cannot diverge from the regression set.
UNIT_PATTERNS = (
    "test_answer_integrity.py",
    "test_block_context.py",
    "test_group_scope.py",
    "test_temporal_identity.py",
    "test_agent_tool_contract.py",
    "test_docx_ingest.py",
    "test_alembic_baseline_environment.py",
    "test_kg_provenance.py",
    "test_kg_mention_linking.py",
    "test_kg_name_identity_key.py",
    "test_kg_role_taxonomy.py",
)
SQLITE_PATTERNS = ("test_kg_sna.py", "test_stage8_remediation.py", "test_research_history.py")
#: Order matters: every suite below that ends with ``drop_test_schema()`` drops
#: the *whole* schema of the target, so the suite that verifies the **migrated**
#: schema must run before them (it must never rely on ``create_all``).
POSTGRES_PATTERNS = (
    "test_research_history_postgres.py",
    "test_block_context_postgres.py",
    "test_research_workflow_postgres.py",
    "test_research_groups_postgres.py",
    "test_kg_group_graph_postgres.py",
    "test_kg_evidence_accounting_postgres.py",
    "test_kg_mention_linking_postgres.py",
    "test_kg_role_provenance_postgres.py",
    "test_kg_extraction_runs_postgres.py",
    "test_kg_auto_extraction_postgres.py",
    "test_kg_extraction_budget_postgres.py",
    "test_kg_extraction_pause_postgres.py",
    "test_kg_extraction_reset_postgres.py",
    "test_kg_graph_facets_postgres.py",
    "test_kg_workflow_postgres.py",
    "test_kg_models.py",
    "test_kg_extraction.py",
    "test_kg_disambiguation.py",
    "test_agent.py",
    "test_rag.py",
)

TRUTHY = frozenset({"1", "true", "yes", "on"})

EXIT_CONFIG = 2


def _fail(message: str) -> int:
    print("ISOLATION CONFIG ERROR: %s" % message, file=sys.stderr)
    print(
        "Nothing was imported and no database was contacted.\n"
        "See backend/tests/RUNNING_TESTS.md for the supported configuration.",
        file=sys.stderr,
    )
    return EXIT_CONFIG


def _is_truthy(value) -> bool:
    return str(value or "").strip().lower() in TRUTHY


def prepare_environment(tier: str) -> int:
    """Phase 1+2: validate configuration before any test module is imported."""
    if tier == "unit":
        # Pure tests: still mark a test run, but configure an in-memory database so
        # an accidental import can never reach a real target.
        os.environ["EIC_TEST_MODE"] = "1"
        os.environ.setdefault("TEST_DATABASE_URL", "sqlite://")
    elif tier == "sqlite":
        # The tier *is* the explicit opt-in: a purely in-memory database that
        # cannot collide with any product or shared instance.
        os.environ["EIC_TEST_MODE"] = "1"
        os.environ["TEST_DATABASE_URL"] = "sqlite://"
    elif tier == "postgres":
        if not _is_truthy(os.environ.get("EIC_TEST_MODE")):
            return _fail("tier 'postgres' requires EIC_TEST_MODE=1 to be set explicitly")
        if not str(os.environ.get("TEST_DATABASE_URL") or "").strip():
            return _fail("tier 'postgres' requires TEST_DATABASE_URL")
        if not str(os.environ.get("EIC_TEST_DATABASE_ALLOWLIST") or "").strip():
            return _fail(
                "tier 'postgres' requires EIC_TEST_DATABASE_ALLOWLIST "
                "(host:port/dbname of the authorized test database)"
            )
    # tier == "guard": the guard tests configure and scrub their own environment.
    # They deliberately assert that the runner does not touch EIC_TEST_MODE or the
    # database configuration, so this tier must be invoked from a clean shell
    # (no inherited TEST_DATABASE_URL / DATABASE_URL from another tier).

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    if tier != "guard":
        # Full validation; importing the guard creates no engine and no connection.
        from app import database_guard as guard

        try:
            target = guard.resolve_test_database_url()
        except guard.TestIsolationError as exc:
            return _fail("%s: %s" % (type(exc).__name__, exc))
        except Exception as exc:  # noqa: BLE001 - configuration problems must abort
            return _fail("%s: %s" % (type(exc).__name__, exc))

        key = guard.database_target_key(target) or ("unknown",)
        if tier == "postgres" and key[0] != "postgresql":
            return _fail(
                "tier 'postgres' requires a PostgreSQL TEST_DATABASE_URL, got %s"
                % guard.redact_database_url(target)
            )
        if tier == "sqlite" and key[0] != "sqlite":
            return _fail(
                "tier 'sqlite' requires a SQLite TEST_DATABASE_URL, got %s"
                % guard.redact_database_url(target)
            )
        print(
            "[isolation] tier=%s target=%s" % (tier, guard.redact_database_url(target)),
            file=sys.stderr,
        )

    # Marks child processes as a test run even when invoked oddly.
    os.environ["EIC_TEST_RUNNER"] = "1"
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tier",
        required=True,
        choices=("guard", "unit", "sqlite", "postgres"),
        help=(
            "guard: mock-only isolation tests; unit: pure suites with no database; "
            "sqlite: in-memory; postgres: authorized test DB"
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose test output")
    parser.add_argument(
        "--pattern",
        action="append",
        default=None,
        help="override the tier's test file patterns (repeatable)",
    )
    args = parser.parse_args(argv)

    status = prepare_environment(args.tier)
    if status:
        return status

    if not TESTS_DIR.is_dir():
        return _fail("tests directory not found at %s" % TESTS_DIR)

    patterns = args.pattern or {
        "guard": GUARD_PATTERNS,
        "unit": UNIT_PATTERNS,
        "sqlite": SQLITE_PATTERNS,
        "postgres": POSTGRES_PATTERNS,
    }[args.tier]

    import unittest

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for pattern in patterns:
        suite.addTests(loader.discover(str(TESTS_DIR), pattern=pattern))
    if not suite.countTestCases():
        print("no tests matched %s" % (patterns,), file=sys.stderr)
        return EXIT_CONFIG

    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
