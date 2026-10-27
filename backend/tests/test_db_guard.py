"""Negative / safety tests for the test-database isolation guard.

These tests are **pure unit tests**: they never open a PostgreSQL connection and
never create a SQLite database (not even an in-memory one). Every case asserts
that a dangerous target is rejected *before* ``app.database.create_engine`` is
reached, by patching that factory and asserting it was not called.
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.engine.url import make_url

from app import database as app_database
from app import database_guard as guard
from app.config import MissingDatabaseConfig, Settings, settings
from app.test_support import db_fixtures

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
ENTRY_SCRIPT = BACKEND_DIR / "run_isolated_tests.py"

# Built by concatenation so that this file does not trip its own source scan.
_FACTORY_CALL = "create_" + "engine("
_LEGACY_SESSION = "Session" + "Local("
_PRODUCT_DDL = "create_all(bind=" + "engine)"
_ENGINE_IMPORT = re.compile(r"from\s+app\.database\s+import\s+.*\bengine\b")

_SCRUBBED_KEYS = (
    guard.TEST_MODE_ENV,
    guard.TEST_DATABASE_URL_ENV,
    guard.TEST_ALLOWLIST_ENV,
    guard.TEST_RUNNER_ENV,
    "PYTEST_CURRENT_TEST",
)

FAKE_PRODUCT_URL = "postgresql://eic:synthetic-pw@127.0.0.1:5432/ocr_platform"
FAKE_RECON_URL = "postgresql://eic:synthetic-pw@127.0.0.1:5432/ocr_platform_recon"
FAKE_DRILL_URL = "postgresql://eic:synthetic-pw@127.0.0.1:5433/ocr_platform_restore_drill"
FAKE_TEST_URL = "postgresql://eic:synthetic-pw@127.0.0.1:5432/ocr_platform_test"
FAKE_TEST_TARGET = ("127.0.0.1", 5432, "ocr_platform_test")


def clean_env(**overrides):
    """os.environ minus EIC test variables, plus *overrides*."""
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_KEYS}
    env.update(overrides)
    return env


class _TempEnv:
    """Context manager applying a scrubbed environment for the block's duration."""

    def __init__(self, **overrides):
        self._overrides = overrides
        self._patcher = None

    def __enter__(self):
        self._patcher = patch.dict(os.environ, clean_env(**self._overrides), clear=True)
        self._patcher.start()
        return self

    def __exit__(self, *exc_info):
        self._patcher.stop()
        return False


class _ProductionContext:
    """Simulate a production process (no test runner detected)."""

    def __enter__(self):
        self._patcher = patch.object(guard, "_runner_hint", return_value=None)
        self._patcher.start()
        return self

    def __exit__(self, *exc_info):
        self._patcher.stop()
        return False


class _TestRunContext:
    """Simulate being inside a test run without relying on environment variables."""

    def __enter__(self):
        self._patcher = patch.object(guard, "_runner_hint", return_value="unittest")
        self._patcher.start()
        return self

    def __exit__(self, *exc_info):
        self._patcher.stop()
        return False


def _fake_engine_factory():
    """side_effect for create_engine that records the URL without connecting."""

    def _factory(url, **kwargs):
        fake = SimpleNamespace(url=make_url(str(url)), kwargs=kwargs, disposed=False)
        fake.dispose = lambda: setattr(fake, "disposed", True)
        return fake

    return _factory


class TestGuardForbiddenTargets(unittest.TestCase):
    """Scenarios 3-6: protected and unapproved targets are refused."""

    def _assert_refused(self, url, **env):
        with _TempEnv(**env), patch.object(
            app_database, "create_engine"
        ) as fake_create:
            with self.assertRaises(guard.UnsafeTestDatabase):
                db_fixtures.make_test_engine(url, create_schema=False)
            fake_create.assert_not_called()

    def test_03_product_database_refused(self):
        self._assert_refused(
            FAKE_PRODUCT_URL,
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=FAKE_PRODUCT_URL,
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform",
        )

    def test_04_recon_database_refused(self):
        self._assert_refused(
            FAKE_RECON_URL,
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=FAKE_RECON_URL,
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform_recon",
        )

    def test_05_restore_drill_database_refused(self):
        self._assert_refused(
            FAKE_DRILL_URL,
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=FAKE_DRILL_URL,
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5433/ocr_platform_restore_drill",
        )

    def test_05b_maintenance_databases_refused(self):
        for dbname in ("postgres", "template0", "template1"):
            url = "postgresql://eic:synthetic-pw@127.0.0.1:5432/%s" % dbname
            self._assert_refused(
                url,
                EIC_TEST_MODE="1",
                TEST_DATABASE_URL=url,
                EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/%s" % dbname,
            )

    def test_06_unapproved_host_port_or_database_refused(self):
        cases = [
            # approved name, wrong host
            ("postgresql://eic:pw@db.internal:5432/ocr_platform_test", "127.0.0.1:5432/ocr_platform_test"),
            # approved name, wrong port
            ("postgresql://eic:pw@127.0.0.1:6543/ocr_platform_test", "127.0.0.1:5432/ocr_platform_test"),
            # test-looking but unlisted name
            ("postgresql://eic:pw@127.0.0.1:5432/ocr_platform_test_other", "127.0.0.1:5432/ocr_platform_test"),
            # arbitrary database
            ("postgresql://eic:pw@127.0.0.1:5432/appdb", "127.0.0.1:5432/appdb"),
            # names outside the naming contract
            ("postgresql://eic:pw@127.0.0.1:5432/ocr_platform_testx", "127.0.0.1:5432/ocr_platform_testx"),
        ]
        for url, allowlist in cases:
            self._assert_refused(
                url,
                EIC_TEST_MODE="1",
                TEST_DATABASE_URL=url,
                EIC_TEST_DATABASE_ALLOWLIST=allowlist,
            )

    def test_06b_file_backed_sqlite_refused(self):
        for url in ("sqlite:///./test.db", "sqlite:///C:/tmp/test.db"):
            self._assert_refused(url, EIC_TEST_MODE="1", TEST_DATABASE_URL=url)

    def test_06c_other_backends_refused(self):
        self._assert_refused(
            "mysql://eic:pw@127.0.0.1:3306/ocr_platform_test",
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL="mysql://eic:pw@127.0.0.1:3306/ocr_platform_test",
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:3306/ocr_platform_test",
        )

    def test_06d_password_never_appears_in_error(self):
        with _TempEnv(EIC_TEST_MODE="1", TEST_DATABASE_URL=FAKE_PRODUCT_URL):
            with self.assertRaises(guard.UnsafeTestDatabase) as ctx:
                db_fixtures.make_test_engine(FAKE_PRODUCT_URL, create_schema=False)
        message = str(ctx.exception)
        self.assertNotIn("synthetic-pw", message)
        self.assertNotIn("eic:", message)
        self.assertIn("ocr_platform", message)


class TestGuardConfigModes(unittest.TestCase):
    """Scenarios 1, 2, 7, 10, 12: configuration contract."""

    def test_01_test_mode_without_test_dsn_fails_closed(self):
        with _TempEnv(EIC_TEST_MODE="1"), patch.object(
            app_database, "create_engine"
        ) as fake_create:
            with self.assertRaises(guard.MissingTestDatabaseConfig):
                db_fixtures.make_test_engine(create_schema=False)
            with self.assertRaises(guard.MissingTestDatabaseConfig):
                app_database.get_engine()
            fake_create.assert_not_called()

    def test_02_product_configuration_is_never_used_in_test_mode(self):
        product_url = FAKE_PRODUCT_URL
        with patch.object(settings, "database_url", product_url), _TempEnv(
            EIC_TEST_MODE="1"
        ), patch.object(
            app_database, "create_engine"
        ) as fake_create:
            with self.assertRaises(guard.MissingTestDatabaseConfig):
                guard.resolve_database_url(product_url)
            fake_create.assert_not_called()
        # ... and even when TEST_DATABASE_URL is the product DSN, it is refused.
        with _TempEnv(EIC_TEST_MODE="1", TEST_DATABASE_URL=product_url):
            with self.assertRaises(guard.UnsafeTestDatabase):
                guard.resolve_database_url(product_url)

    def test_07_in_memory_sqlite_target_is_accepted(self):
        for url in ("sqlite://", "sqlite:///:memory:"):
            with _TempEnv(EIC_TEST_MODE="1", TEST_DATABASE_URL=url):
                guard.assert_test_database_safe(url)
                fake_engine = MagicMock(name="engine")
                with patch.object(
                    app_database, "create_engine", return_value=fake_engine
                ) as fake_create:
                    engine = db_fixtures.make_test_engine(url, create_schema=False)
                self.assertIs(engine, fake_engine)
                fake_create.assert_called_once()
                self.assertEqual(str(fake_create.call_args[0][0]), url)

    def test_10a_missing_allowlist_never_defaults_to_allow(self):
        with _TempEnv(EIC_TEST_MODE="1", TEST_DATABASE_URL=FAKE_TEST_URL):
            with self.assertRaises(guard.UnsafeTestDatabase) as ctx:
                guard.assert_test_database_safe(FAKE_TEST_URL)
        self.assertIn(guard.TEST_ALLOWLIST_ENV, str(ctx.exception))

    def test_10b_missing_test_mode_flag_blocks_engine_creation(self):
        with _TempEnv(TEST_DATABASE_URL="sqlite://"), patch.object(
            app_database, "create_engine"
        ) as fake_create:
            with self.assertRaises(guard.MissingTestDatabaseConfig):
                db_fixtures.make_test_engine("sqlite://", create_schema=False)
            fake_create.assert_not_called()

    def test_10c_allowlisted_postgres_target_is_accepted_without_connecting(self):
        allowlist = "127.0.0.1:5432/ocr_platform_test"
        with _TempEnv(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=FAKE_TEST_URL,
            EIC_TEST_DATABASE_ALLOWLIST=allowlist,
        ):
            guard.assert_test_database_safe(FAKE_TEST_URL)
            fake_engine = MagicMock(name="engine")
            with patch.object(
                app_database, "create_engine", return_value=fake_engine
            ) as fake_create:
                engine = db_fixtures.make_test_engine(create_schema=False)
            self.assertIs(engine, fake_engine)
            fake_create.assert_called_once()
            self.assertEqual(guard.load_test_allowlist(), {FAKE_TEST_TARGET})

    def test_12_production_path_is_unchanged(self):
        product_url = FAKE_PRODUCT_URL
        allowlist = "127.0.0.1:5432/ocr_platform_test"
        with patch.object(settings, "database_url", product_url), \
                _ProductionContext(), _TempEnv(
            TEST_DATABASE_URL=FAKE_TEST_URL, EIC_TEST_DATABASE_ALLOWLIST=allowlist
        ):
            self.assertFalse(guard.is_test_mode())
            self.assertFalse(guard.is_test_context())
            self.assertEqual(guard.resolve_database_url(product_url), product_url)
            fake_engine = MagicMock(name="engine")
            with patch.object(
                app_database, "create_engine", return_value=fake_engine
            ) as fake_create:
                app_database.reset_engine_state()
                engine = app_database.get_engine()
            self.assertIs(engine, fake_engine)
            self.assertEqual(str(fake_create.call_args[0][0]), product_url)
            self.assertTrue(fake_create.call_args[1].get("pool_pre_ping"))
            app_database.reset_engine_state()

    def test_12b_get_db_still_yields_and_closes(self):
        fake_session = MagicMock(name="session")
        with patch.object(app_database, "get_session_factory") as fake_factory:
            fake_factory.return_value.return_value = fake_session
            generator = app_database.get_db()
            yielded = next(generator)
            self.assertIs(yielded, fake_session)
            with self.assertRaises(StopIteration):
                next(generator)
        fake_session.close.assert_called_once()


class TestNoGuardBypass(unittest.TestCase):
    """Scenarios 8, 9, 11: architectural bypasses."""

    def test_08_test_modules_do_not_create_engines_or_sessions_directly(self):
        offenders = []
        for path in sorted(TESTS_DIR.glob("*.py")):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8")
            for token in (_FACTORY_CALL, _PRODUCT_DDL):
                if token in text:
                    offenders.append("%s: %s" % (path.name, token))
            if _LEGACY_SESSION in text:
                offenders.append("%s: %s" % (path.name, _LEGACY_SESSION))
            if _ENGINE_IMPORT.search(text):
                offenders.append("%s: app.database engine import" % path.name)
        self.assertEqual(
            offenders,
            [],
            "tests must obtain database access through app.test_support.db_fixtures",
        )

    def test_08b_session_factory_refuses_non_test_engine(self):
        product_engine = MagicMock(name="engine")
        product_engine.url = FAKE_PRODUCT_URL
        with _TempEnv(EIC_TEST_MODE="1", TEST_DATABASE_URL="sqlite://"):
            with self.assertRaises(guard.UnsafeTestDatabase):
                db_fixtures.make_session_factory(product_engine)

    def test_09_unreachable_target_never_falls_back(self):
        unreachable = "postgresql://eic:pw@10.255.255.1:5432/ocr_platform_test_x"
        with _TempEnv(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=unreachable,
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform_test",
        ), patch.object(app_database, "create_engine") as fake_create:
            with self.assertRaises(guard.UnsafeTestDatabase):
                app_database.get_engine()
            fake_create.assert_not_called()

    def test_11_importing_app_database_does_not_create_an_engine(self):
        script = (
            "import sys\n"
            "from app import database as d\n"
            "if getattr(d, '_engine', 'missing') is not None:\n"
            "    print('ENGINE_CREATED_AT_IMPORT'); sys.exit(3)\n"
            "if d.Base is None:\n"
            "    print('NO_BASE'); sys.exit(4)\n"
            "try:\n"
            "    d.engine\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__); sys.exit(0)\n"
            "print('NOT_REFUSED'); sys.exit(5)\n"
        )
        env = clean_env(EIC_TEST_MODE="1", TEST_DATABASE_URL=FAKE_PRODUCT_URL)
        env["PYTHONPATH"] = str(BACKEND_DIR)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(guard.UnsafeTestDatabase.__name__, result.stdout)


class TestSupportedEntryPoint(unittest.TestCase):
    """A1-A7: the supported entry point fails closed before any business import."""

    def _run_entry(self, args, env):
        run_env = dict(env)
        run_env["PYTHONPATH"] = str(BACKEND_DIR)
        run_env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(ENTRY_SCRIPT)] + list(args),
            cwd=str(BACKEND_DIR),
            env=run_env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def test_A1_entry_requires_an_explicit_tier(self):
        result = self._run_entry([], clean_env())
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--tier", result.stderr)

    def test_A2_postgres_tier_requires_allowlist(self):
        env = clean_env(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=FAKE_TEST_URL,
        )
        result = self._run_entry(["--tier", "postgres"], env)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(guard.TEST_ALLOWLIST_ENV, result.stderr)

    def test_A3_postgres_tier_rejects_sqlite_target(self):
        env = clean_env(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL="sqlite://",
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform_test",
        )
        result = self._run_entry(["--tier", "postgres"], env)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("requires a PostgreSQL", result.stderr)

    def test_A4_entry_rejects_protected_target(self):
        env = clean_env(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=FAKE_PRODUCT_URL,
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform",
        )
        result = self._run_entry(["--tier", "postgres"], env)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("protected database", result.stderr)
        self.assertNotIn("synthetic-pw", result.stderr)

    def test_A5_guard_tier_does_not_enable_test_mode(self):
        if str(BACKEND_DIR) not in sys.path:
            sys.path.insert(0, str(BACKEND_DIR))
        from run_isolated_tests import prepare_environment  # noqa: WPS433 - local import

        with _TempEnv(TEST_DATABASE_URL=FAKE_PRODUCT_URL):
            self.assertEqual(prepare_environment("guard"), 0)
            self.assertNotIn(guard.TEST_MODE_ENV, os.environ)
            self.assertEqual(os.environ.get(guard.TEST_DATABASE_URL_ENV), FAKE_PRODUCT_URL)

        # The sqlite tier is the explicit opt-in; it may only ever select memory sqlite.
        with _TempEnv():
            self.assertEqual(prepare_environment("sqlite"), 0)
            self.assertEqual(os.environ.get(guard.TEST_MODE_ENV), "1")
            self.assertEqual(os.environ.get(guard.TEST_DATABASE_URL_ENV), "sqlite://")
        app_database.reset_engine_state()

    def test_A6_db_test_modules_import_the_bootstrap(self):
        offenders = []
        for path in sorted(TESTS_DIR.glob("*.py")):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8")
            if "app.test_support.db_fixtures" in text and (
                "require_isolated_test_environment(" not in text
            ):
                offenders.append(path.name)
        self.assertEqual(
            offenders,
            [],
            "database-touching test modules must call require_isolated_test_environment() "
            "at import time so a misconfigured run aborts before any engine exists",
        )

    def _import_module_with_env(self, module, env):
        script = "import sys; sys.path.insert(0, 'tests'); import %s" % module
        run_env = dict(env)
        run_env["PYTHONPATH"] = str(BACKEND_DIR)
        run_env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(BACKEND_DIR),
            env=run_env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def test_A7a_db_test_module_import_fails_without_test_mode(self):
        result = self._import_module_with_env("test_kg_models", clean_env())
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(guard.MissingTestDatabaseConfig.__name__, result.stderr)

    def test_A7b_db_test_module_import_refuses_protected_target(self):
        env = clean_env(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=FAKE_PRODUCT_URL,
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform",
        )
        result = self._import_module_with_env("test_kg_models", env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(guard.UnsafeTestDatabase.__name__, result.stderr)
        self.assertNotIn("synthetic-pw", result.stderr)


class TestEngineFactoryHardening(unittest.TestCase):
    """B1-B8: no engine or session path escapes the guard."""

    def setUp(self):
        app_database.reset_engine_state()

    def tearDown(self):
        app_database.reset_engine_state()

    def test_B1_get_engine_refused_without_test_mode(self):
        with _TestRunContext(), _TempEnv(), patch.object(
            app_database, "create_engine"
        ) as fake_create:
            with self.assertRaises(guard.MissingTestDatabaseConfig):
                app_database.get_engine()
            fake_create.assert_not_called()

    def test_B2_session_local_refused_without_test_mode(self):
        with _TestRunContext(), _TempEnv(), patch.object(
            app_database, "create_engine"
        ) as fake_create:
            with self.assertRaises(guard.MissingTestDatabaseConfig):
                app_database.SessionLocal()
            fake_create.assert_not_called()

    def test_B3_build_engine_rejects_protected_url_even_if_allowlisted(self):
        with _TempEnv(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=FAKE_PRODUCT_URL,
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform",
        ), patch.object(app_database, "create_engine") as fake_create:
            with self.assertRaises(guard.UnsafeTestDatabase) as ctx:
                app_database.build_engine(FAKE_PRODUCT_URL)
            fake_create.assert_not_called()
        self.assertIn("protected database", str(ctx.exception))

    def test_B4_build_engine_rejects_mismatched_target(self):
        with _TempEnv(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL="sqlite://",
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform_test",
        ), patch.object(app_database, "create_engine") as fake_create:
            with self.assertRaises(guard.UnsafeTestDatabase) as ctx:
                app_database.build_engine(FAKE_TEST_URL)
            fake_create.assert_not_called()
        message = str(ctx.exception)
        self.assertIn("never redirected silently", message)
        self.assertIn("ocr_platform_test", message)

    def test_B5_build_engine_accepts_authorized_target(self):
        for url in ("sqlite://", "sqlite:///:memory:"):
            with _TempEnv(EIC_TEST_MODE="1", TEST_DATABASE_URL="sqlite://"):
                fake_engine = MagicMock(name="engine")
                with patch.object(
                    app_database, "create_engine", return_value=fake_engine
                ) as fake_create:
                    engine = app_database.build_engine(url)
                self.assertIs(engine, fake_engine)
                fake_create.assert_called_once()
                self.assertEqual(str(fake_create.call_args[0][0]), url)
                self.assertIn("poolclass", fake_create.call_args[1])

    def test_B6_rejection_paths_never_call_create_engine(self):
        cases = [
            (FAKE_PRODUCT_URL, "127.0.0.1:5432/ocr_platform"),
            (FAKE_RECON_URL, "127.0.0.1:5432/ocr_platform_recon"),
            (FAKE_DRILL_URL, "127.0.0.1:5433/ocr_platform_restore_drill"),
            ("postgresql://eic:pw@127.0.0.1:5432/postgres", "127.0.0.1:5432/postgres"),
            ("sqlite:///./forbidden.db", ""),
        ]
        for url, allowlist in cases:
            with _TempEnv(
                EIC_TEST_MODE="1",
                TEST_DATABASE_URL=url,
                EIC_TEST_DATABASE_ALLOWLIST=allowlist,
            ), patch.object(app_database, "create_engine") as fake_create:
                with self.assertRaises(guard.UnsafeTestDatabase, msg=url):
                    app_database.build_engine(url)
                self.assertEqual(fake_create.call_count, 0, url)

    def test_B7_rejection_message_has_no_credentials(self):
        secret = "top-secret-value"
        unauthorized = "postgresql://eic:%s@127.0.0.1:5432/ocr_platform_test_other" % secret
        with _TempEnv(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL="sqlite://",
            EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform_test",
        ), patch.object(app_database, "create_engine") as fake_create:
            with self.assertRaises(guard.UnsafeTestDatabase) as ctx:
                app_database.build_engine(unauthorized)
            fake_create.assert_not_called()
        message = str(ctx.exception)
        self.assertNotIn(secret, message)
        self.assertNotIn("eic:", message)

    def test_B8_cached_engine_conflict_is_detected(self):
        with _TempEnv(EIC_TEST_MODE="1", TEST_DATABASE_URL="sqlite://"):
            with patch.object(
                app_database, "create_engine", side_effect=_fake_engine_factory()
            ) as fake_create:
                first = app_database.get_engine()
                self.assertEqual(fake_create.call_count, 1)
                os.environ[guard.TEST_DATABASE_URL_ENV] = FAKE_TEST_URL
                os.environ[guard.TEST_ALLOWLIST_ENV] = "127.0.0.1:5432/ocr_platform_test"
                with self.assertRaises(guard.UnsafeTestDatabase) as ctx:
                    app_database.get_engine()
                self.assertIn("cached engine", str(ctx.exception))
                app_database.reset_engine_state()
                second = app_database.get_engine()
                self.assertIsNot(first, second)
                self.assertEqual(fake_create.call_count, 2)

    def test_B8b_session_factory_rebinds_to_current_engine(self):
        with _TempEnv(EIC_TEST_MODE="1", TEST_DATABASE_URL="sqlite://"):
            with patch.object(
                app_database, "create_engine", side_effect=_fake_engine_factory()
            ):
                factory = app_database.get_session_factory()
                self.assertIs(factory.kw.get("bind"), app_database.get_engine())
                app_database.reset_engine_state()
                factory_after = app_database.get_session_factory()
                self.assertIsNot(factory, factory_after)
                self.assertIs(factory_after.kw.get("bind"), app_database.get_engine())


class TestTargetParsingStrictness(unittest.TestCase):
    """C1-C6: URL/allow-list parsing is strict and never permissive by accident."""

    def test_C1_missing_host_rejected(self):
        url = "postgresql:///ocr_platform_test"
        with _TempEnv(
            EIC_TEST_MODE="1",
            TEST_DATABASE_URL=url,
            EIC_TEST_DATABASE_ALLOWLIST=":5432/ocr_platform_test",
        ):
            with self.assertRaises(guard.UnsafeTestDatabase) as ctx:
                guard.assert_test_database_safe(url)
        self.assertIn("host", str(ctx.exception))

    def test_C2_missing_database_name_rejected(self):
        url = "postgresql://127.0.0.1:5432"
        with _TempEnv(EIC_TEST_MODE="1", TEST_DATABASE_URL=url):
            with self.assertRaises(guard.UnsafeTestDatabase) as ctx:
                guard.assert_test_database_safe(url)
        self.assertIn("name", str(ctx.exception))

    def test_C3_allowlist_parser_ignores_malformed_entries(self):
        parsed = guard.load_test_allowlist(
            {
                guard.TEST_ALLOWLIST_ENV: (
                    "garbage,127.0.0.1:notaport/db, /db,127.0.0.1:5432/"
                )
            }
        )
        self.assertEqual(parsed, set())

    def test_C4_non_conforming_database_names_rejected(self):
        for name in ("ocr_platform_TEST", "ocr_platform_test-DB", "ocr_platform_test db"):
            url = "postgresql://eic:pw@127.0.0.1:5432/%s" % name
            with _TempEnv(
                EIC_TEST_MODE="1",
                TEST_DATABASE_URL=url,
                EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/%s" % name,
            ):
                with self.assertRaises(guard.UnsafeTestDatabase, msg=name):
                    guard.assert_test_database_safe(url)

    def test_C5_memory_sqlite_forms_are_equivalent(self):
        self.assertTrue(guard.same_database_target("sqlite://", "sqlite:///:memory:"))
        self.assertTrue(guard.same_database_target("postgresql://a@h:5432/db", "postgresql://b@h:5432/db"))
        self.assertFalse(
            guard.same_database_target("sqlite://", "postgresql://h:5432/ocr_platform_test")
        )

    def test_C6_acceptance_requires_every_condition(self):
        good_env = {
            "EIC_TEST_MODE": "1",
            "TEST_DATABASE_URL": FAKE_TEST_URL,
            "EIC_TEST_DATABASE_ALLOWLIST": "127.0.0.1:5432/ocr_platform_test",
        }
        with _TempEnv(**good_env):
            guard.assert_test_database_safe(FAKE_TEST_URL)  # all conditions met
        variations = [
            dict(good_env, EIC_TEST_DATABASE_ALLOWLIST=""),
            dict(good_env, EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5433/ocr_platform_test"),
            dict(good_env, EIC_TEST_DATABASE_ALLOWLIST="10.0.0.1:5432/ocr_platform_test"),
            dict(good_env, EIC_TEST_DATABASE_ALLOWLIST="127.0.0.1:5432/ocr_platform_test_other"),
        ]
        for env in variations:
            with _TempEnv(**env):
                with self.assertRaises(guard.UnsafeTestDatabase, msg=str(env)):
                    guard.assert_test_database_safe(FAKE_TEST_URL)


class TestProductionCompatibility(unittest.TestCase):
    """D1/D4: production behaviour survives the hardening."""

    def setUp(self):
        app_database.reset_engine_state()

    def tearDown(self):
        app_database.reset_engine_state()

    def test_D1_production_target_is_not_redirected(self):
        product_url = FAKE_PRODUCT_URL
        with patch.object(settings, "database_url", product_url), \
                _ProductionContext(), _TempEnv(TEST_DATABASE_URL="sqlite://"):
            self.assertFalse(guard.is_test_context())
            self.assertEqual(guard.resolve_database_url(product_url), product_url)
            with patch.object(
                app_database, "create_engine", side_effect=_fake_engine_factory()
            ) as fake_create:
                app_database.get_engine()
            self.assertEqual(str(fake_create.call_args[0][0]), product_url)
            self.assertTrue(fake_create.call_args[1].get("pool_pre_ping"))

    def test_D4_reset_state_creates_nothing(self):
        with _ProductionContext(), _TempEnv():
            with patch.object(
                app_database, "create_engine", side_effect=_fake_engine_factory()
            ) as fake_create:
                app_database.get_engine()
                self.assertEqual(fake_create.call_count, 1)
                app_database.reset_engine_state()
                self.assertEqual(fake_create.call_count, 1)
                self.assertIsNone(getattr(app_database, "_engine"))





class TestDatabaseConfigurationIsMandatory(unittest.TestCase):
    """E1-E12: no built-in DSN, explicit failure, no credential leak.

    Pure unit tests: no engine is created, no connection is attempted, and
    ``app.database.create_engine`` is patched wherever the code could reach it.
    """

    FAKE_DSN = "postgresql://demo_user:demo_pass@127.0.0.1:5432/demo_db"

    def setUp(self):
        app_database.reset_engine_state()
        self._saved_database_url = settings.database_url

    def tearDown(self):
        settings.database_url = self._saved_database_url
        app_database.reset_engine_state()

    def _fresh_settings(self, **kwargs):
        with patch.dict(os.environ, {}, clear=True):
            return Settings(_env_file=None, **kwargs)

    def test_E1_settings_have_no_default_database_url(self):
        self.assertEqual(self._fresh_settings().database_url, "")

    def test_E2_require_database_url_raises_when_missing(self):
        with self.assertRaises(MissingDatabaseConfig) as ctx:
            self._fresh_settings().require_database_url()
        message = str(ctx.exception)
        self.assertIn("DATABASE_URL", message)
        self.assertNotIn("://", message)
        self.assertNotIn("postgres", message.lower())

    def test_E3_explicit_configuration_is_honoured(self):
        fresh = self._fresh_settings(database_url=self.FAKE_DSN)
        self.assertEqual(fresh.require_database_url(), self.FAKE_DSN)

    def test_E4_blank_urls_normalise_to_empty(self):
        for raw in ("", "   ", "\t\n "):
            fresh = self._fresh_settings(database_url=raw)
            self.assertEqual(fresh.database_url, "", repr(raw))
            with self.assertRaises(MissingDatabaseConfig, msg=repr(raw)):
                fresh.require_database_url()

    def test_E5_config_source_contains_no_database_dsn(self):
        source = (BACKEND_DIR / "app" / "config.py").read_text(encoding="utf-8")
        self.assertNotIn("postgresql://", source)
        self.assertNotIn("postgres://", source)
        self.assertIsNone(
            re.search(r"\w+://[^\s/]+:[^\s/@]+@", source),
            "a URL with an embedded credential must not live in the settings module",
        )
        self.assertIn("# DATABASE_URL HAS NO BUILT-IN DEFAULT", source)

    def test_E6_settings_never_read_the_example_template(self):
        source = (BACKEND_DIR / "app" / "config.py").read_text(encoding="utf-8")
        self.assertIn('env_file = _backend_dir / ".env"', source)
        self.assertNotIn("else _backend_dir", source)

    def test_E7_build_engine_rejects_an_empty_url(self):
        for bad in ("", "   ", None):
            with patch.object(app_database, "create_engine") as fake_create:
                with self.assertRaises(MissingDatabaseConfig, msg=repr(bad)):
                    app_database.build_engine(bad)
                fake_create.assert_not_called()

    def test_E8_get_engine_fails_when_configuration_is_missing(self):
        settings.database_url = ""
        with _ProductionContext(), patch.object(
            app_database, "resolve_database_url"
        ) as fake_resolve, patch.object(app_database, "create_engine") as fake_create:
            with self.assertRaises(MissingDatabaseConfig):
                app_database.get_engine()
            fake_create.assert_not_called()
            fake_resolve.assert_not_called()

    def test_E9_production_target_is_returned_unchanged(self):
        settings.database_url = self.FAKE_DSN
        with _ProductionContext():
            self.assertEqual(app_database._authorized_target(), self.FAKE_DSN)

    def test_E10_test_mode_still_delegates_to_the_guard(self):
        settings.database_url = ""
        sentinel = "sqlite://"
        with patch.object(app_database, "is_test_mode", return_value=True), patch.object(
            app_database, "resolve_database_url", return_value=sentinel
        ) as fake_resolve:
            self.assertEqual(app_database._authorized_target(), sentinel)
            fake_resolve.assert_called_once_with("")

    def test_E11_test_context_without_mode_is_still_refused(self):
        """A test process that forgot EIC_TEST_MODE must never reach production."""
        settings.database_url = self.FAKE_DSN
        with patch.object(app_database, "is_test_mode", return_value=False), patch.object(
            app_database, "is_test_context", return_value=True
        ), patch.object(
            app_database,
            "resolve_database_url",
            side_effect=guard.MissingTestDatabaseConfig("x"),
        ) as fake_resolve:
            with self.assertRaises(guard.TestIsolationError):
                app_database._authorized_target()
            fake_resolve.assert_called_once_with(self.FAKE_DSN)

    def test_E12_missing_configuration_never_leaks_a_credential(self):
        settings.database_url = ""
        with _ProductionContext(), patch.object(app_database, "create_engine"):
            with self.assertRaises(MissingDatabaseConfig) as ctx:
                app_database.get_engine()
        message = str(ctx.exception).lower()
        for token in ("://", "demo_pass", "postgres"):
            self.assertNotIn(token, message)


if __name__ == "__main__":
    unittest.main()
