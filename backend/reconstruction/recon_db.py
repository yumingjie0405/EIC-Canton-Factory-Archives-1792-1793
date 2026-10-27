"""Stage 8 reconstruction: shared connection + identity guards.

Every script in this package MUST connect through this module so that the
target database is always the isolated reconstruction instance
(`ocr_platform_recon`), never the application's default database.

Security: the password is only ever held in memory. It is never printed,
logged or embedded in exception messages.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Dict, List

import psycopg2

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.recon")

EXPECTED_DB = "ocr_platform_recon"
EXPECTED_HOST = "localhost"
EXPECTED_HOST_PORT = 55432
EXPECTED_CONTAINER = "recon_postgres"
EXPECTED_VOLUME = "eic_reconstruction_pgdata"
EXPECTED_PG_MAJOR = "15"


class IdentityError(RuntimeError):
    """Raised when the target environment is not the expected isolated one."""


def load_env(path: str = ENV_PATH) -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip()
    return cfg


def get_dsn() -> str:
    """Return the recon DSN read *only* from backend/.env.recon."""
    cfg = load_env()
    if "RECON_DATABASE_URL" not in cfg:
        raise IdentityError("RECON_DATABASE_URL missing from backend/.env.recon")
    return cfg["RECON_DATABASE_URL"]


def describe_dsn_safe(dsn: str) -> str:
    """Redacted one-line description; never includes the password."""
    m = re.match(
        r"^(?P<scheme>[a-z0-9+]+)://(?P<u>[^:@/]*)(?::(?P<p>[^@]*))?@(?P<h>[^:/]*)(?::(?P<port>\d+))?/(?P<db>.+)$",
        dsn,
    )
    if not m:
        return "<unparsable DSN>"
    d = m.groupdict()
    return "scheme=%s host=%s port=%s db=%s user=%s password_present=%s" % (
        d["scheme"], d["h"], d["port"], d["db"], d["u"], bool(d.get("p")),
    )


def _docker(args: List[str]) -> str:
    return subprocess.run(
        ["docker"] + args, capture_output=True, text=True, timeout=60
    ).stdout.strip()


def assert_docker_scope() -> List[str]:
    """Verify the container/volume/port of the recon environment. Returns findings."""
    findings: List[str] = []

    state = _docker(["inspect", EXPECTED_CONTAINER, "--format", "{{.State.Status}}"])
    if state != "running":
        findings.append("container %s state=%r (expected running)" % (EXPECTED_CONTAINER, state))

    binds = _docker(["inspect", EXPECTED_CONTAINER, "--format", "{{json .HostConfig.PortBindings}}"])
    if '"HostIp":"127.0.0.1"' not in binds or '"HostPort":"55432"' not in binds:
        findings.append("unexpected port binding: %s" % binds)

    mounts = _docker(["inspect", EXPECTED_CONTAINER, "--format", "{{range .Mounts}}{{.Name}}:{{.Destination}} {{end}}"])
    if ("%s:/var/lib/postgresql/data" % EXPECTED_VOLUME) not in mounts:
        findings.append("unexpected mounts: %r" % mounts)

    other = _docker(["ps", "--format", "{{.Names}}"])
    for required in ("ocr_postgres", "ocr_redis"):
        if required not in other.split():
            findings.append("expected untouched container %s is not running" % required)

    return findings


def assert_db_identity(conn, expected_tables: int | None = None) -> List[str]:
    """Run SQL-side identity assertions. Raises IdentityError on mismatch."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, current_schema()")
        db, user, schema = cur.fetchone()
        cur.execute("SHOW server_version")
        version = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        table_count = cur.fetchone()[0]
        cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY 1")
        databases = [r[0] for r in cur.fetchall()]

    problems: List[str] = []
    if db != EXPECTED_DB:
        problems.append("current_database=%r (expected %r)" % (db, EXPECTED_DB))
    if not version.startswith(EXPECTED_PG_MAJOR + "."):
        problems.append("server_version=%r (expected %s.x)" % (version, EXPECTED_PG_MAJOR))
    if user != "postgres":
        problems.append("current_user=%r (expected 'postgres')" % user)
    if EXPECTED_DB in databases and len(databases) < 1:
        problems.append("target database not reachable")
    if expected_tables is not None and table_count != expected_tables:
        problems.append("public table count=%d (expected %d)" % (table_count, expected_tables))
    if problems:
        raise IdentityError("database identity check failed: " + "; ".join(problems))

    return ["db=%s user=%s schema=%s version=%s public_tables=%d" % (db, user, schema, version, table_count)]


def connect() -> "psycopg2.extensions.connection":
    """Open a non-autocommit connection to the recon database."""
    dsn = get_dsn()
    cfg = load_env()
    if cfg.get("RECON_PGHOST") != EXPECTED_HOST or cfg.get("RECON_PGPORT") != str(EXPECTED_HOST_PORT):
        raise IdentityError("backend/.env.recon does not point at %s:%s" % (EXPECTED_HOST, EXPECTED_HOST_PORT))
    if cfg.get("RECON_PGDATABASE") != EXPECTED_DB:
        raise IdentityError("backend/.env.recon database is not %s" % EXPECTED_DB)
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn
