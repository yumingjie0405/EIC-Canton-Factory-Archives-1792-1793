"""Pure unit tests for the independent (D+E) Alembic baseline environment.

What is exercised here
----------------------
* the wiring claim: after the guard passes, the *validated* URL really is what the
  Alembic config hands to ``engine_from_config`` / the offline context;
* the new environment's structure and its independence from the legacy chain;
* the guard behaviour (no allow-list, mismatched host/port/database, unauthorized
  write command, API invocation, test-run isolation, credential-free messages)
  reached through the new entry point.

What is deliberately NOT done here: no Engine is created, no connection is
opened, no SQL is executed and no Alembic command (``upgrade``/``downgrade``/
``stamp``/``revision``/``current`` ...) is invoked. ``alembic.config.Config``
objects are only constructed and inspected; that reads an ini file and nothing
else. All URLs are fictional.

Run directly:

    .\\venv\\Scripts\\python.exe -m unittest tests.test_alembic_baseline_environment -v
"""

import ast
import hashlib
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from alembic import util as alembic_util
from mako.template import Template

from app import database_guard as guard
from app import migration_target_guard as mtg
from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
BASELINE_INI = BACKEND_DIR / "alembic_baseline.ini"
BASELINE_DIR = BACKEND_DIR / "alembic_baseline"
BASELINE_ENV = BASELINE_DIR / "env.py"
BASELINE_TEMPLATE = BASELINE_DIR / "script.py.mako"
BASELINE_VERSIONS = BASELINE_DIR / "versions"
LEGACY_INI = BACKEND_DIR / "alembic.ini"
LEGACY_ENV = BACKEND_DIR / "alembic" / "env.py"
LEGACY_VERSIONS = BACKEND_DIR / "alembic" / "versions"

VALIDATED = "postgresql://demo_user:demo_pass@127.0.0.1:5432/migrate_demo"
PLACEHOLDER = "driver://user:pass@localhost/dbname"
ALLOWLIST = "127.0.0.1:5432/migrate_demo"


class _ProductionContext:
    """Pretend this process is not a test run (patching a hint, not the env)."""

    def __enter__(self):
        self._patcher = patch.object(guard, "_runner_hint", return_value=None)
        self._patcher.start()
        return self

    def __exit__(self, *exc_info):
        self._patcher.stop()
        return False


def _env(**overrides):
    base = {mtg.ALLOWED_TARGETS_ENV: ALLOWLIST, mtg.MIGRATIONS_ENABLED_ENV: "1"}
    base.update(overrides)
    return base


def _write_ini(directory, body):
    path = Path(directory) / "custom.ini"
    path.write_text(body, encoding="utf-8")
    return str(path)


class Wiring(unittest.TestCase):
    """The validated URL must be the one Alembic actually reads (requirement 2.4)."""

    def setUp(self):
        self._saved = settings.database_url
        settings.database_url = VALIDATED
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        settings.database_url = self._saved

    def _config(self):
        """A CLI-style read-only config; write commands are covered elsewhere."""
        return AlembicConfig(
            self._ini(), cmd_opts=SimpleNamespace(cmd=("current",))
        )

    def _ini(self):
        return _write_ini(
            self._tmp.name,
            "[alembic]\nscript_location = %(here)s/scripts\n"
            "sqlalchemy.url = " + PLACEHOLDER + "\n",
        )

    def test_W1_set_main_option_reaches_the_section_engine_from_config_uses(self):
        config = self._config()
        self.assertEqual(config.get_main_option("sqlalchemy.url"), PLACEHOLDER)

        config.set_main_option("sqlalchemy.url", VALIDATED)

        section = config.get_section(config.config_ini_section, {})
        self.assertEqual(section["sqlalchemy.url"], VALIDATED)
        self.assertNotEqual(section["sqlalchemy.url"], PLACEHOLDER)

    def test_W2_apply_guard_injects_the_validated_url(self):
        config = self._config()
        with _ProductionContext():
            returned = mtg.apply_guard_to_config(config, env=_env())
        self.assertEqual(returned, VALIDATED)
        section = config.get_section(config.config_ini_section, {})
        self.assertEqual(section["sqlalchemy.url"], VALIDATED)

    def test_W3_offline_mode_reads_the_validated_url(self):
        config = self._config()
        with _ProductionContext():
            mtg.apply_guard_to_config(config, env=_env())
        # run_migrations_offline() uses exactly this value
        self.assertEqual(config.get_main_option("sqlalchemy.url"), VALIDATED)

    def test_W4_rejected_target_never_reaches_the_config(self):
        config = self._config()
        with _ProductionContext():
            with self.assertRaises(mtg.MissingMigrationAuthorization):
                mtg.apply_guard_to_config(config, env={})
        self.assertEqual(config.get_main_option("sqlalchemy.url"), PLACEHOLDER)

    def test_W5_config_that_cannot_store_the_url_fails_closed(self):
        class _NoSetter:
            cmd_opts = SimpleNamespace(cmd=("current",))

        with _ProductionContext():
            with self.assertRaises(mtg.InvalidMigrationTarget):
                mtg.apply_guard_to_config(_NoSetter(), env=_env())

    def test_W6_extra_config_sections_are_not_used_for_the_url(self):
        config = self._config()
        with _ProductionContext():
            mtg.apply_guard_to_config(config, env=_env())
        own = config.get_section(config.config_ini_section, {})["sqlalchemy.url"]
        self.assertEqual(own, VALIDATED)
        self.assertNotEqual(own, "driver://user:pass@localhost/dbname")


class NewEnvironmentStructure(unittest.TestCase):
    def test_N1_ini_points_at_the_new_script_location(self):
        config = AlembicConfig(str(BASELINE_INI))
        resolved = Path(config.get_main_option("script_location")).resolve()
        self.assertEqual(resolved, BASELINE_DIR.resolve())
        self.assertNotEqual(resolved, (BACKEND_DIR / "alembic").resolve())

    def test_N2_named_section_resolves_to_the_same_guarded_directory(self):
        config = AlembicConfig(str(BASELINE_INI), ini_section="alembic_baseline")
        resolved = Path(config.get_main_option("script_location")).resolve()
        self.assertEqual(resolved, BASELINE_DIR.resolve())

    def test_N3_unknown_named_section_cannot_start_a_run(self):
        config = AlembicConfig(str(BASELINE_INI), ini_section="does_not_exist")
        with self.assertRaises(alembic_util.CommandError):
            config.get_main_option("script_location")

    def test_N4_env_py_uses_the_shared_guard(self):
        source = BASELINE_ENV.read_text(encoding="utf-8")
        self.assertIn("from app.migration_target_guard import apply_guard_to_config", source)
        self.assertIn("validated_url = apply_guard_to_config(config)", source)
        self.assertNotIn("settings.database_url", source)

    def test_N5_guard_runs_before_the_offline_online_branch(self):
        source = BASELINE_ENV.read_text(encoding="utf-8")
        guard_at = source.index("apply_guard_to_config(config)")
        branch_at = source.index("if context.is_offline_mode():")
        self.assertLess(guard_at, branch_at)
        offline = source[source.index("def run_migrations_offline"):source.index("def run_migrations_online")]
        self.assertIn("url=validated_url", offline)
        self.assertNotIn("get_main_option(\"sqlalchemy.url\")", offline)

    def test_N6_version_table_is_independent(self):
        source = BASELINE_ENV.read_text(encoding="utf-8")
        self.assertIn('VERSION_TABLE = "alembic_version_baseline"', source)
        self.assertIn("version_table=VERSION_TABLE", source)
        self.assertNotIn('version_table="alembic_version"', source)

    def test_N7_only_reviewed_revisions_exist(self):
        # Each entry was reviewed in its own round; anything else appearing here would
        # be unreviewed. Keep this list explicit instead of globbing so a stray
        # generated revision cannot slip into the chain unnoticed.
        entries = sorted(p.name for p in BASELINE_VERSIONS.iterdir() if p.suffix == ".py")
        self.assertEqual(
            entries,
            [
                "0001_baseline_ocr_schema.py",
                "0002_research_sessions.py",
                "0003_research_groups.py",
                "0004_document_type_docx.py",
                "0005_kg_extraction_runs.py",
                "0006_kg_auto_extract_on_import.py",
                "0007_extraction_safety_and_reset_audit.py",
                "0008_provenance.py",
                "0009_workflow.py",
                "0010_attribution_refinement.py",
                "0011_person_roles.py",
            ],
        )

    def test_N8_template_is_present_and_usable(self):
        template = (BASELINE_DIR / "script.py.mako").read_text(encoding="utf-8")
        for marker in ("revision: str", "down_revision", "def upgrade()", "def downgrade()"):
            self.assertIn(marker, template)

    def test_N9_env_py_compiles(self):
        compile(BASELINE_ENV.read_text(encoding="utf-8"), str(BASELINE_ENV), "exec")

    def test_N10_baseline_env_does_not_reference_legacy_versions(self):
        source = BASELINE_ENV.read_text(encoding="utf-8")
        # ignore the module docstring, which explains the relationship in prose
        code = source[source.index("from logging.config"):]
        self.assertNotIn("alembic/versions", code)
        self.assertNotIn("1de0638c0c1b", code)
        self.assertNotIn("2f7c9e1b5a3d", code)


class LegacyEnvironmentUntouched(unittest.TestCase):
    """The legacy chain keeps its own config, env and revisions."""

    def test_O1_legacy_ini_still_points_at_the_legacy_script_location(self):
        config = AlembicConfig(str(LEGACY_INI))
        self.assertEqual(Path(config.get_main_option("script_location")).resolve(),
                         (BACKEND_DIR / "alembic").resolve())

    def test_O2_legacy_env_still_uses_the_guard(self):
        source = LEGACY_ENV.read_text(encoding="utf-8")
        self.assertIn("apply_guard_to_config(config)", source)
        self.assertNotIn("settings.database_url", source)

    def test_O3_legacy_revisions_are_present_and_unchanged_in_shape(self):
        names = sorted(p.name for p in LEGACY_VERSIONS.glob("*.py"))
        self.assertEqual(
            names,
            [
                "000_initial.py",
                "001_knowledge_workbench.py",
                "002_search_chunks_blocks.py",
                "003_evidence_workbench.py",
                "004_source_revision.py",
                "005_evidence_images.py",
                "006_legacy_projections.py",
                "007_annotation_entities.py",
                "008_document_recycle.py",
                "1de0638c0c1b_add_knowledge_graph_tables.py",
                "2f7c9e1b5a3d_add_entity_operation_audit_table.py",
            ],
        )


    def test_O4_the_two_environments_do_not_share_a_directory(self):
        self.assertNotEqual(BASELINE_DIR.resolve(), (BACKEND_DIR / "alembic").resolve())
        self.assertNotEqual(BASELINE_INI.resolve(), LEGACY_INI.resolve())


class GuardReachedThroughTheNewEntryPoint(unittest.TestCase):
    def setUp(self):
        self._saved = settings.database_url
        settings.database_url = VALIDATED
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._ctx = _ProductionContext()
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        settings.database_url = self._saved

    def _config(self, cmd=("current",), ini_section="alembic"):
        ini = _write_ini(self._tmp.name, "[alembic]\nscript_location = %(here)s/x\n")
        return AlembicConfig(
            ini, ini_section=ini_section,
            cmd_opts=None if cmd is None else SimpleNamespace(cmd=cmd),
        )

    def test_G1_no_allowlist_refuses(self):
        for cmd in (("current",), ("upgrade",)):
            with self.assertRaises(mtg.MissingMigrationAuthorization, msg=str(cmd)):
                mtg.apply_guard_to_config(self._config(cmd), env={})

    def test_G2_host_mismatch_refuses(self):
        with self.assertRaises(mtg.MigrationTargetNotAllowed):
            mtg.apply_guard_to_config(self._config(), env=_env(
                **{mtg.ALLOWED_TARGETS_ENV: "10.0.0.9:5432/migrate_demo"}))

    def test_G3_port_mismatch_refuses(self):
        with self.assertRaises(mtg.MigrationTargetNotAllowed):
            mtg.apply_guard_to_config(self._config(), env=_env(
                **{mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:6543/migrate_demo"}))

    def test_G4_database_mismatch_refuses(self):
        with self.assertRaises(mtg.MigrationTargetNotAllowed):
            mtg.apply_guard_to_config(self._config(), env=_env(
                **{mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/other_database"}))

    def test_G5_write_command_needs_explicit_authorization(self):
        env = {mtg.ALLOWED_TARGETS_ENV: ALLOWLIST}
        for cmd in (("upgrade",), ("downgrade",), ("stamp",)):
            with self.assertRaises(mtg.MissingMigrationAuthorization, msg=str(cmd)):
                mtg.apply_guard_to_config(self._config(cmd), env=env)
        self.assertEqual(mtg.apply_guard_to_config(self._config(("upgrade",)), env=_env()), VALIDATED)

    def test_G6_read_only_command_needs_only_the_allowlist(self):
        env = {mtg.ALLOWED_TARGETS_ENV: ALLOWLIST}
        for cmd in (("current",), ("check",)):
            self.assertEqual(mtg.apply_guard_to_config(self._config(cmd), env=env), VALIDATED)

    def test_G7_api_invocation_needs_an_acknowledgement(self):
        env = {mtg.ALLOWED_TARGETS_ENV: ALLOWLIST}
        with self.assertRaises(mtg.MissingMigrationAuthorization) as ctx:
            mtg.apply_guard_to_config(self._config(None), env=env)
        self.assertIn(mtg.API_CALL_ENV, str(ctx.exception))

        # a direct API call is also a write path: both switches are required
        env[mtg.API_CALL_ENV] = "1"
        with self.assertRaises(mtg.MissingMigrationAuthorization) as ctx2:
            mtg.apply_guard_to_config(self._config(None), env=env)
        self.assertIn(mtg.MIGRATIONS_ENABLED_ENV, str(ctx2.exception))

        env[mtg.MIGRATIONS_ENABLED_ENV] = "1"
        self.assertEqual(mtg.apply_guard_to_config(self._config(None), env=env), VALIDATED)

    def test_G8_production_database_needs_the_second_switch(self):
        settings.database_url = "postgresql://demo_user:demo_pass@127.0.0.1:5432/ocr_platform"
        env = _env(**{mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/ocr_platform"})
        with self.assertRaises(mtg.MigrationTargetNotAllowed):
            mtg.apply_guard_to_config(self._config(("upgrade",)), env=env)

    def test_G9_error_messages_never_leak_credentials(self):
        cases = [
            (self._config(("upgrade",)), {}),
            (self._config(), _env(**{mtg.ALLOWED_TARGETS_ENV: "10.0.0.9:5432/migrate_demo"})),
            (self._config(None), {mtg.ALLOWED_TARGETS_ENV: ALLOWLIST}),
        ]
        for config, env in cases:
            with self.assertRaises(mtg.MigrationTargetError) as ctx:
                mtg.apply_guard_to_config(config, env=env)
            message = str(ctx.exception)
            self.assertNotIn("demo_pass", message)
            self.assertNotIn("demo_user", message)
            self.assertNotIn(VALIDATED, message)


class TestRunIsolation(unittest.TestCase):
    """Inside a test process the new entry point must still honour the guard."""

    def setUp(self):
        self._saved = settings.database_url
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        settings.database_url = self._saved

    def _config(self):
        ini = _write_ini(self._tmp.name, "[alembic]\nscript_location = %(here)s/x\n")
        return AlembicConfig(ini, cmd_opts=SimpleNamespace(cmd=("upgrade",)))

    def test_S1_test_mode_requires_the_test_allowlist(self):
        settings.database_url = "postgresql://demo_user:demo_pass@127.0.0.1:5432/ocr_platform_test"
        env = {
            "EIC_TEST_MODE": "1",
            "TEST_DATABASE_URL": "postgresql://demo_user:demo_pass@127.0.0.1:5432/ocr_platform_test",
            "EIC_TEST_DATABASE_ALLOWLIST": "127.0.0.1:5432/ocr_platform_test",
            mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/ocr_platform_test",
            mtg.MIGRATIONS_ENABLED_ENV: "1",
        }
        self.assertEqual(mtg.apply_guard_to_config(self._config(), env=env), settings.database_url)

    def test_S2_test_mode_cannot_target_the_product_database(self):
        settings.database_url = "postgresql://demo_user:demo_pass@127.0.0.1:5432/ocr_platform"
        env = {
            "EIC_TEST_MODE": "1",
            "TEST_DATABASE_URL": "postgresql://demo_user:demo_pass@127.0.0.1:5432/ocr_platform",
            "EIC_TEST_DATABASE_ALLOWLIST": "127.0.0.1:5432/ocr_platform",
            mtg.ALLOWED_TARGETS_ENV: "127.0.0.1:5432/ocr_platform",
            mtg.MIGRATIONS_ENABLED_ENV: "1",
            mtg.ALLOW_PRODUCTION_ENV: "1",
        }
        with self.assertRaises(mtg.MigrationTargetNotAllowed) as ctx:
            mtg.apply_guard_to_config(self._config(), env=env)
        self.assertIn("test run", str(ctx.exception))


class DowngradeSafety(unittest.TestCase):
    """The baseline template must make rollback impossible by default.

    The template is rendered with mako and the *generated source* is compiled and
    executed, so these tests observe the real behaviour of a freshly generated
    revision's ``downgrade()`` rather than the presence of a string.
    """

    RENDER_ARGS = {
        "message": "demo revision",
        "up_revision": "deadbeef",
        "down_revision": None,
        "branch_labels": None,
        "depends_on": None,
        "create_date": "2026-01-01 00:00:00.000000",
        "imports": "",
        "upgrades": "",
        "downgrades": "",
        # Alembic registers this as the ``comma`` filter when it renders scripts
        "comma": alembic_util.format_as_comma,
    }

    def _render(self, **overrides):
        args = dict(self.RENDER_ARGS)
        args.update(overrides)
        return Template(filename=str(BASELINE_TEMPLATE), input_encoding="utf-8").render(**args)

    def _namespace(self, source):
        namespace = {"__name__": "generated_revision"}
        exec(compile(source, "<generated revision>", "exec"), namespace)
        return namespace

    def _downgrade_function(self, source):
        tree = ast.parse(source)
        return next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )

    def test_D1_generated_downgrade_raises_instead_of_succeeding(self):
        namespace = self._namespace(self._render())
        self.assertTrue(callable(namespace["downgrade"]))
        with self.assertRaises(RuntimeError) as ctx:
            namespace["downgrade"]()
        message = str(ctx.exception).lower()
        self.assertIn("not supported", message)
        self.assertIn("backup", message)

    def test_D2_first_statement_is_the_raise(self):
        func = self._downgrade_function(self._render())
        body = func.body
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            body = body[1:]  # skip the docstring
        self.assertTrue(body, "downgrade must contain a statement")
        self.assertIsInstance(body[0], ast.Raise, "downgrade must refuse immediately")
        self.assertEqual(len(body), 1, "the refusal must be the only executable statement")
        raised = body[0].exc
        self.assertIsInstance(raised, ast.Call)
        self.assertEqual(getattr(raised.func, "id", None), "RuntimeError")

    def test_D3_no_alembic_operation_is_reachable_from_downgrade(self):
        func = self._downgrade_function(self._render())
        touched = {
            node.value.id
            for node in ast.walk(func)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        }
        self.assertNotIn("op", touched, "downgrade must not call alembic.op")
        self.assertNotIn("sa", touched)

    def test_D4_autogenerated_downgrade_operations_are_not_emitted(self):
        source = self._render(
            upgrades="op.create_table('demo_table')",
            downgrades="op.drop_table('demo_table')\nop.drop_index('ix_demo')",
        )
        self.assertIn("op.create_table('demo_table')", source)
        self.assertNotIn("drop_table", source)
        self.assertNotIn("drop_index", source)
        with self.assertRaises(RuntimeError):
            self._namespace(source)["downgrade"]()

    def test_D5_upgrade_keeps_the_normal_generation_structure(self):
        source = self._render(upgrades="op.create_table('demo_table')")
        self.assertIn("def upgrade() -> None:", source)
        self.assertIn("op.create_table('demo_table')", source)

        empty = self._namespace(self._render(upgrades=""))
        self.assertIsNone(empty["upgrade"]())

    def test_D6_generated_revision_has_no_application_import(self):
        source = self._render()
        self.assertNotIn("from app.", source)
        self.assertNotIn("import app.", source)

    def test_D7_refusal_message_points_at_the_review_procedure(self):
        source = self._render()
        self.assertIn("stage8_phaseA_R1_0_4_6_downgrade_safety.md", source)

    def test_D8_no_credentials_in_template_or_rendered_source(self):
        template = BASELINE_TEMPLATE.read_text(encoding="utf-8")
        rendered = self._render()
        for text in (template, rendered):
            self.assertNotIn("demo_pass", text)
            self.assertIsNone(re.search(r"\w+://[^\s/]+:[^\s/@]+@", text))

    def test_D10_render_arguments_match_the_alembic_call_site(self):
        """Guard the fidelity of the rendering used by these tests."""
        import inspect

        from alembic.script.base import ScriptDirectory

        source = inspect.getsource(ScriptDirectory.generate_revision)
        for kwarg in ("up_revision=", "down_revision=", "branch_labels=",
                      "depends_on=", "message=", "create_date=", "**kw,"):
            self.assertIn(kwarg, source)
        # alembic registers the ``comma`` filter with exactly this callable
        self.assertIn("comma=util.format_as_comma", source)
        self.assertIs(self.RENDER_ARGS["comma"], alembic_util.format_as_comma)
        # upgrades / downgrades / imports are forwarded through **kw by autogenerate,
        # which is why the template receives them as plain names
        for forwarded in ("upgrades", "downgrades", "imports"):
            self.assertIn(forwarded, self.RENDER_ARGS)

    def test_D9_baseline_chain_has_one_root_and_one_head(self):
        """The chain must have exactly one root and one head, with no placeholder."""
        files = sorted(p.name for p in BASELINE_VERSIONS.iterdir() if p.suffix == ".py")
        self.assertEqual(
            files,
            [
                "0001_baseline_ocr_schema.py",
                "0002_research_sessions.py",
                "0003_research_groups.py",
                "0004_document_type_docx.py",
                "0005_kg_extraction_runs.py",
                "0006_kg_auto_extract_on_import.py",
                "0007_extraction_safety_and_reset_audit.py",
                "0008_provenance.py",
                "0009_workflow.py",
                "0010_attribution_refinement.py",
                "0011_person_roles.py",
            ],
        )

        script = ScriptDirectory.from_config(AlembicConfig(str(BASELINE_INI)))
        self.assertEqual(script.get_bases(), ["0001_baseline_ocr_schema"])
        self.assertEqual(script.get_heads(), ["0011_person_roles"])

        # Reviewers must also see a strictly linear chain: every revision after the
        # root has exactly one parent, which is the previous revision.
        expected_parent = None
        for name in files:
            source = (BASELINE_VERSIONS / name).read_text(encoding="utf-8")
            revision = re.search(r'^revision = "([^"]+)"', source, re.MULTILINE)
            down = re.search(r'^down_revision = "([^"]+)"', source, re.MULTILINE)
            self.assertIsNotNone(revision, name)
            if expected_parent is None:
                self.assertIsNone(down, name)
            else:
                self.assertIsNotNone(down, name)
                self.assertEqual(down.group(1), expected_parent, name)
            expected_parent = revision.group(1)

        for name in files:
            source = (BASELINE_VERSIONS / name).read_text(encoding="utf-8")
            self.assertNotIn("BASELINE_REVISION_ID", source, name)
            self.assertNotIn("down_revision = None  # placeholder", source, name)


class FrozenLegacyEnvironment(unittest.TestCase):
    """The legacy chain is frozen: its template, config and revisions must not move.

    These hashes are intentional tripwires. If a later round really has to change
    the legacy chain, update them in the same reviewed change.
    """

    EXPECTED = {
        "alembic/script.py.mako": "2CA4E8780429BF9D3F0542948B6F811AEC5943523F685C7F3F079E148487AC9B",
        "alembic/env.py": "FA17F40D1B0843BD3F4C354A1AAC0DAAA85DB8CB53FD148BFE705DA96781A3F2",
        "alembic.ini": "83E9C91634791423FF8A54CE6C384854F1117FBEE521F582B2138BC944476E47",
        "alembic/versions/000_initial.py":
            "E4A0CDF4C4D4721180AD136EFDC386EAD990ADF8AA26EB5B9F20EB8903731DDB",
        "alembic/versions/001_knowledge_workbench.py":
            "9747688CE3C46C3C2531ED4D7CBB857EAE461C742C37E9E6DE8B1E12042F105C",
        "alembic/versions/002_search_chunks_blocks.py":
            "775E7E9D49CC1B5FF9BDDACB2279D54F00E72FEE428747F90B6A1CD9E27B1D92",
        "alembic/versions/003_evidence_workbench.py":
            "251E31B914F1EC83EF42FA91B4AFE9AE12C24E16E20A624BBE6ECE0D52EC2947",
        "alembic/versions/004_source_revision.py":
            "4D0737B04F9A32582217D0762CF42BED881C3AF145B4C20DB6DEBF85C38466BC",
        "alembic/versions/005_evidence_images.py":
            "31C5BE7D464145E719E95E2B905CA0116993010AAFFE2F8B5752036CDCDFDAFF",
        "alembic/versions/006_legacy_projections.py":
            "7B8B0CBA8C29895AFAE096056BE5B6FA0F1910420B190CC57C61FCF271434097",
        "alembic/versions/007_annotation_entities.py":
            "834B34CBCAA74CE3ECFDE9EEB981B20FC4E07F12AE891C39BCAB27E5F1D731F1",
        "alembic/versions/008_document_recycle.py":
            "124A92A83F88536DBCD060495A0AE6BD1076C78ABA42984EC45E0F53A9191403",
        "alembic/versions/1de0638c0c1b_add_knowledge_graph_tables.py":
            "DDF22410F0B980BAD561D1D4C81318D927A4DCCC13BBEDAFC083504BBE548845",
        "alembic/versions/2f7c9e1b5a3d_add_entity_operation_audit_table.py":
            "C6B76F06E1FB72C0386A8E52D8184886107E92605B354D4A498FA584B22D983A",
    }

    def test_F1_legacy_files_are_byte_identical(self):
        for relative, expected in self.EXPECTED.items():
            path = BACKEND_DIR / relative
            digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
            self.assertEqual(digest, expected, "%s changed" % relative)

    def test_F2_baseline_template_differs_from_the_legacy_one(self):
        self.assertNotEqual(
            hashlib.sha256(BASELINE_TEMPLATE.read_bytes()).hexdigest().upper(),
            self.EXPECTED["alembic/script.py.mako"],
        )


if __name__ == "__main__":
    unittest.main()
