# Running the backend test suite safely (R0.1 test isolation)

## The single supported entry point

```powershell
cd backend

# Tier 0 - isolation guard unit tests. No database at all (mocked only).
.\venv\Scripts\python.exe run_isolated_tests.py --tier guard -v
```

`run_isolated_tests.py` is the **only** supported way to start a test run. It
validates the environment with the standard library first, then with
`app.database_guard`, and only afterwards imports the test modules. If the
configuration is missing or unsafe it exits with code `2` **before** any business
module is imported and before any engine can exist.

`--tier` is mandatory: there is deliberately no default that could pick a
database for you.

## Tiers

| Tier | What runs | Database | Command |
| --- | --- | --- | --- |
| `guard` | `test_db_guard.py` (45 isolation tests, all mocked) | none | `... --tier guard` |
| `sqlite` | `test_kg_sna.py`, `test_stage8_remediation.py` | pure in-memory SQLite | `... --tier sqlite` |
| `postgres` | `test_kg_models.py`, `test_kg_extraction.py`, `test_kg_disambiguation.py`, `test_agent.py`, `test_rag.py` | authorized isolated PostgreSQL test database | `... --tier postgres` |

`--pattern <file>` overrides the tier's default file list; `-v` gives verbose
output.

## Environment contract

| Variable | Meaning |
| --- | --- |
| `EIC_TEST_MODE` | `1` enables test mode. Set by `--tier sqlite`; must be set explicitly for `--tier postgres`. |
| `TEST_DATABASE_URL` | The **only** database a test run may use. `DATABASE_URL` from `backend/.env` is never used in a test run. |
| `EIC_TEST_DATABASE_ALLOWLIST` | Comma-separated `host:port/dbname` entries. Required for any PostgreSQL test target. |
| `EIC_TEST_RUNNER` | Set by the entry point; marks child processes as a test run. Not for manual use. |

Accepted targets:

* `sqlite://` / `sqlite:///:memory:` - pure in-memory, no file is created.
* PostgreSQL only if the database name matches `ocr_platform_test[_suffix]` **and**
  the exact `(host, port, dbname)` triple is listed in `EIC_TEST_DATABASE_ALLOWLIST`.

Always rejected, even if allow-listed: `ocr_platform`, `ocr_platform_recon`,
`ocr_platform_restore_drill`, `postgres`, `template0`, `template1`, any other
backend (MySQL...), any file-backed SQLite path, and any target without an explicit
host and database name.

## Hard rules

* **Use a throw-away database for `--tier postgres`.** Several PostgreSQL suites end
  with `drop_test_schema()`, i.e. they drop *every* table in the target. Pointing the
  tier at a database that also holds demonstration or research data destroys it. Create
  a dedicated instance instead, e.g.
  `ocr_platform_test_tier` (migrated once with
  `alembic -c alembic_baseline.ini upgrade head`), and keep any demo/seed database in a
  separate name.
* **Never** use the production DSN from `backend/.env` (or any other real
  environment file) as `TEST_DATABASE_URL`.
* **Never** fall back to the product database because "the test database could not
  be reached". Unreachable is not a reason to change target; the guard refuses and
  the run aborts.
* Direct `python -m unittest ...` and IDE runners work only when the environment is
  already correct: every database-touching test module calls
  `require_isolated_test_environment()` at import time, so a misconfigured run
  fails to import (loud, immediate) instead of touching production.
* `pytest` is not installed in this project; if it is ever added, it must be
  launched with the same environment, because the guard refuses to use the
  application DSN whenever a test runner is detected.

## PostgreSQL tier (not authorized yet)

```powershell
cd backend
$env:EIC_TEST_MODE = "1"
$env:TEST_DATABASE_URL = "postgresql://<user>:<password>@127.0.0.1:5432/ocr_platform_test"
$env:EIC_TEST_DATABASE_ALLOWLIST = "127.0.0.1:5432/ocr_platform_test"
.\venv\Scripts\python.exe run_isolated_tests.py --tier postgres -v
```

The test database must be created and authorized separately; creating it is
outside the current approval scope. Until it exists those five suites cannot run -
a skip/failure here is a deliberate fail-closed behaviour, **not** a pass.

## Why SQLite results are not PostgreSQL results

PostgreSQL ENUM becomes VARCHAR+CHECK, JSONB has no equivalent, `ILIKE` is
emulated, foreign keys are not enforced without `PRAGMA foreign_keys=ON`, and
locking / `FOR UPDATE` semantics differ. A green `sqlite` tier says nothing about
PostgreSQL behaviour.

## Do not

* Do not point `TEST_DATABASE_URL` at the product, reconstruction or restore-drill
  database "just to run the tests".
* Do not remove `@requires_postgres_test_db` to force a PostgreSQL suite onto
  SQLite; that turns PostgreSQL-specific assertions into false passes.
* Do not add `create_engine(...)`, `sessionmaker(...)` or `SessionLocal(...)` to a
  test module - use `app.test_support.db_fixtures`. `test_db_guard.py` enforces
  this statically.
