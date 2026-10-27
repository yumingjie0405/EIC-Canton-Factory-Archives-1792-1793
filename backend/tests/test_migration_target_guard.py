"""Pure unit tests for :mod:`app.migration_target_guard`.

These tests never create an Engine, never open a connection, never execute SQL
and never invoke the Alembic CLI or API. Every URL in this file is fictional.

Run directly (the module imports no engine-creating code):

    .\\venv\\Scripts\\python.exe -m unittest tests.test_migration_target_guard -v
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from app import database_guard as guard
from app import migration_target_guard as mtg
from app.config import MissingDatabaseConfig, settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_PY = BACKEND_DIR / "alembic" / "env.py"

DEMO_URL = "postgresql://demo_user:demo_pass@127.0.0.1:5432/migrate_demo"
DEMO_ALLOWLIST = "127.0.0.1:5432/migrate_demo"
PROD_URL = "postgresql://demo_user:demo_pass@127.0.0.1:5432/ocr_platform"
TEST_URL = "postgresql://demo_user:demo_pass@127.0.0.1:5432/ocr_platform_test"


class _ProductionContext:
    """Pretend this process is not a test run, without touching os.environ."""

    def __enter__(self):
        self._patcher = patch.object(guard, "_runner_hint", return_value=None)
        self._patcher.start()
        return self

    def __exit__(self, *exc_info):
        self._patcher.stop()
        return False


def _env(**overrides):
    base = {mtg.ALLOWED_TARGETS_ENV: DEMO_ALLOWLIST, mtg.MIGRATIONS_ENABLED_ENV: "1"}
    base.update(overrides)
    return base


class _FakeOpts:
    def __init__(self, cmd):
        self.cmd = cmd


class _FakeConfig:
    def __init__(self, cmd):
        self.cmd_opts = None if cmd is None else _FakeOpts(cmd)


class TargetNormalisation(unittest.TestCase):
    def test_T1_credentials_do_not_change_the_target(self):
        a = mtg.normalize_target("postgresql://alice:one@127.0.0.1:5432/demo")
        b = mtg.normalize_target("postgresql://bob:two@127.0.0.1:5432/demo")
        self.assertEqual(a, b)
        self.assertEqual(a, ("postgresql", "127.0.0.1", 5432, "demo"))

    def test_T2_described_target_has_no_credentials(self):
        text = mtg.describe_target(DEMO_URL)
        self.assertEqual(text, "postgresql://127.0.0.1:5432/migrate_demo")
        self.assertNotIn("demo_pass", text)
        self.assertNotIn("demo_user", text)

    def test_T3_allowlist_parser_drops_malformed_entries(self):
        parsed = mtg.parse_allowed_targets(
            " ,127.0.0.1:5432/demo, garbage, 10.0.0.1:notaport/db, "
            "127.0.0.1:5432, 127.0.0.1:5432/two; 127.0.0.1:6543/three"
        )
        self.assertEqual(
            parsed,
            {("127.0.0.1", 5432, "demo"), ("127.0.0.1", 5432, "two"), ("127.0.0.1", 6543, "three")},
        )

    def test_T4_unparsable_url_is_invalid(self):
        with _ProductionContext():
            with self.assertRaises(mtg.InvalidMigrationTarget):
                mtg.assert_migration_target("this is not a url", command="upgrade", env=_env())

    def test_T5_empty_url_is_missing(self):
        with _ProductionContext():
            for bad in (None, "", "   ", "\t\n"):
                with self.assertRaises(mtg.MissingMigrationTarget, msg=repr(bad)):
                    mtg.assert_migration_target(bad, command="upgrade", env=_env())


class TargetAuthorization(unittest.TestCase):
    def setUp(self):
        self._ctx = _ProductionContext()
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_T6_missing_allowlist_refuses_everything(self):
        for command in ("upgrade", "downgrade", "stamp", "current"):
            with self.assertRaises(mtg.MissingMigrationAuthorization, msg=command):
                mtg.assert_migration_target(DEMO_URL, command=command, env={})
            with self.assertRaises(mtg.MissingMigrationAuthorization, msg=command):
                mtg.assert_migration_target(
                    DEMO_URL, command=command, env={mtg.ALLOWED_TARGETS_ENV: "   "}
                )

    def test_T7_host_mismatch_is_refused(self):
        with self.assertRaises(mtg.MigrationTargetNotAllowed):
            mtg.assert_migration_target(
                "postgresql://demo_user:demo_pass@10.0.0.9:5432/migrate_demo",
                command="upgrade", env=_env(),
            )

    def test_T8_port_mismatch_is_refused(self):
        with self.assertRaises(mtg.MigrationTargetNotAllowed):
            mtg.assert_migration_target(
                "postgresql://demo_user:demo_pass@127.0.0.1:6543/migrate_demo",
                command="upgrade", env=_env(),
            )

    def test_T9_database_mismatch_is_refused(self):
        with self.assertRaises(mtg.MigrationTargetNotAllowed):
            mtg.assert_migration_target(
                "postgresql://demo_user:demo_pass@127.0.0.1:5432/migrate_demo_other",
                command="upgrade", env=_env(),
            )

    def test_T10_authorized_target_is_returned_unchanged(self):
        self.assertEqual(
            mtg.assert_migration_target(DEMO_URL, command="upgrade", env=_env()), DEMO_URL
        )

    def test_T11_production_target_needs_a_second_authorization(self):
        env = _env(**{mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/ocr_platform"})
        with self.assertRaises(mtg.MigrationTargetNotAllowed) as ctx:
            mtg.assert_migration_target(PROD_URL, command="upgrade", env=env)
        self.assertIn(mtg.ALLOW_PRODUCTION_ENV, str(ctx.exception))

    def test_T12_production_target_allowed_only_with_the_extra_flag(self):
        env = _env(
            **{
                mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/ocr_platform",
                mtg.ALLOW_PRODUCTION_ENV: "1",
            }
        )
        self.assertEqual(
            mtg.assert_migration_target(PROD_URL, command="upgrade", env=env), PROD_URL
        )

    def test_T13_maintenance_databases_are_always_refused(self):
        for dbname in sorted(mtg.FORBIDDEN_MIGRATION_DATABASES):
            env = _env(
                **{
                    mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/%s" % dbname,
                    mtg.ALLOW_PRODUCTION_ENV: "1",
                }
            )
            with self.assertRaises(mtg.MigrationTargetNotAllowed, msg=dbname):
                mtg.assert_migration_target(
                    "postgresql://demo_user:demo_pass@127.0.0.1:5432/%s" % dbname,
                    command="upgrade", env=env,
                )

    def test_T14_non_postgres_targets_are_refused(self):
        with self.assertRaises(mtg.MigrationTargetNotAllowed):
            mtg.assert_migration_target("sqlite://", command="upgrade", env=_env())

    def test_T15_error_messages_never_leak_credentials(self):
        cases = [
            ("postgresql://demo_user:demo_pass@10.0.0.9:5432/migrate_demo", _env()),
            ("postgresql://demo_user:demo_pass@127.0.0.1:5432/migrate_demo", {}),
            (
                "postgresql://demo_user:demo_pass@127.0.0.1:5432/ocr_platform",
                _env(**{mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/ocr_platform"}),
            ),
        ]
        for url, env in cases:
            with self.assertRaises(mtg.MigrationTargetError) as ctx:
                mtg.assert_migration_target(url, command="upgrade", env=env)
            message = str(ctx.exception)
            self.assertNotIn("demo_pass", message)
            self.assertNotIn("demo_user", message)
            self.assertNotIn(url, message)


class CommandAuthorization(unittest.TestCase):
    def setUp(self):
        self._ctx = _ProductionContext()
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_T16_read_only_commands_do_not_need_the_write_switch(self):
        for command in sorted(mtg.READ_ONLY_COMMANDS):
            self.assertEqual(
                mtg.assert_migration_target(
                    DEMO_URL, command=command,
                    env={mtg.ALLOWED_TARGETS_ENV: DEMO_ALLOWLIST},
                ),
                DEMO_URL,
            )

    def test_T17_write_commands_require_the_switch(self):
        for command in sorted(mtg.WRITE_COMMANDS):
            with self.assertRaises(mtg.MissingMigrationAuthorization, msg=command):
                mtg.assert_migration_target(
                    DEMO_URL, command=command,
                    env={mtg.ALLOWED_TARGETS_ENV: DEMO_ALLOWLIST},
                )
            self.assertEqual(
                mtg.assert_migration_target(DEMO_URL, command=command, env=_env()), DEMO_URL
            )

    def test_T18_unknown_or_empty_command_is_treated_as_a_write(self):
        for command in ("", "  ", "frobnicate"):
            with self.assertRaises(mtg.MissingMigrationAuthorization, msg=repr(command)):
                mtg.assert_migration_target(
                    DEMO_URL, command=command,
                    env={mtg.ALLOWED_TARGETS_ENV: DEMO_ALLOWLIST},
                )

    def test_T19_command_detection_from_a_cli_namespace(self):
        self.assertEqual(mtg.migration_command(_FakeConfig(("upgrade",))), ("upgrade", False))
        self.assertEqual(mtg.migration_command(_FakeConfig(["stamp"])), ("stamp", False))
        self.assertEqual(mtg.migration_command(_FakeConfig(None)), ("", True))
        self.assertEqual(mtg.migration_command(None), ("", True))

    def test_T20_api_invocation_requires_an_explicit_acknowledgement(self):
        env = _env()
        with self.assertRaises(mtg.MissingMigrationAuthorization) as ctx:
            mtg.assert_migration_target(DEMO_URL, command="upgrade", api_call=True, env=env)
        self.assertIn(mtg.API_CALL_ENV, str(ctx.exception))
        env[mtg.API_CALL_ENV] = "1"
        self.assertEqual(
            mtg.assert_migration_target(DEMO_URL, command="upgrade", api_call=True, env=env),
            DEMO_URL,
        )

    def test_T21_guard_entry_point_uses_the_configured_url(self):
        saved = settings.database_url
        try:
            settings.database_url = DEMO_URL
            self.assertEqual(mtg.guard_migration_environment(_FakeConfig(("upgrade",)), env=_env()), DEMO_URL)
            settings.database_url = ""
            with self.assertRaises(MissingDatabaseConfig):
                mtg.guard_migration_environment(_FakeConfig(("upgrade",)), env=_env())
        finally:
            settings.database_url = saved


class TestIsolationStillApplies(unittest.TestCase):
    """A migration run inside a test process must satisfy the isolation guard."""

    def test_T22_test_context_cannot_migrate_the_product_database(self):
        env = {
            "EIC_TEST_MODE": "1",
            "TEST_DATABASE_URL": PROD_URL,
            "EIC_TEST_DATABASE_ALLOWLIST": "127.0.0.1:5432/ocr_platform",
            mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/ocr_platform",
            mtg.MIGRATIONS_ENABLED_ENV: "1",
            mtg.ALLOW_PRODUCTION_ENV: "1",
        }
        with self.assertRaises(mtg.MigrationTargetNotAllowed) as ctx:
            mtg.assert_migration_target(PROD_URL, command="upgrade", env=env)
        self.assertIn("test run", str(ctx.exception))

    def test_T23_test_context_target_must_match_the_test_configuration(self):
        env = {
            "EIC_TEST_MODE": "1",
            "TEST_DATABASE_URL": TEST_URL,
            "EIC_TEST_DATABASE_ALLOWLIST": "127.0.0.1:5432/ocr_platform_test",
            mtg.ALLOWED_TARGETS_ENV: DEMO_ALLOWLIST,
            mtg.MIGRATIONS_ENABLED_ENV: "1",
        }
        with self.assertRaises(mtg.MigrationTargetNotAllowed):
            mtg.assert_migration_target(DEMO_URL, command="upgrade", env=env)

    def test_T24_fully_configured_test_target_is_accepted(self):
        env = {
            "EIC_TEST_MODE": "1",
            "TEST_DATABASE_URL": TEST_URL,
            "EIC_TEST_DATABASE_ALLOWLIST": "127.0.0.1:5432/ocr_platform_test",
            mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/ocr_platform_test",
            mtg.MIGRATIONS_ENABLED_ENV: "1",
        }
        self.assertEqual(
            mtg.assert_migration_target(TEST_URL, command="upgrade", env=env), TEST_URL
        )


class EnvPyWiring(unittest.TestCase):
    """Static checks: env.py must go through the guard."""

    def setUp(self):
        self.source = ENV_PY.read_text(encoding="utf-8")

    def test_T25_env_py_calls_the_guard(self):
        self.assertIn("guard_migration_environment(config)", self.source)

    def test_T26_env_py_no_longer_reads_settings_directly(self):
        self.assertNotIn("settings.database_url", self.source)
        self.assertNotIn("from app.config import settings", self.source)

    def test_T27_env_py_compiles(self):
        compile(self.source, str(ENV_PY), "exec")


if __name__ == "__main__":
    unittest.main()
