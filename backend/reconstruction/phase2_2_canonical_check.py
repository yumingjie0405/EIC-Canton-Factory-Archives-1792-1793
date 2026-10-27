"""Stage 8 Phase 2.2: prove the canonical schema matches the migrated database.

Builds the canonical "rebuild from empty" DDL (phase2_schema.sql) inside a
throw-away schema in the SAME isolated database, inside ONE transaction that is
always rolled back. Nothing persists: no table, no schema row, no data.

This is the only way to answer "would rebuilding from empty give the same
structure?" without creating a second database or disturbing public.
"""
from __future__ import annotations

import os
import sys

import recon_db
import phase2_2_schema_probe as probe

HERE = os.path.dirname(os.path.abspath(__file__))
SQL_PATH = os.path.join(HERE, "phase2_schema.sql")
SCRATCH = "p22_canonical_check"


def normalize(snap, schema):
    """Strip the schema qualifier that pg_indexes emits in index definitions."""
    prefix = schema + "."
    indexes = {}
    for table, rows in snap["indexes"].items():
        indexes[table] = [(name, definition.replace(prefix, "")) for name, definition in rows]
    out = dict(snap)
    out["indexes"] = indexes
    out["schema"] = "<normalized>"
    return out


def main() -> int:
    print("[canonical] config: " + recon_db.describe_dsn_safe(recon_db.get_dsn()))
    problems = recon_db.assert_docker_scope()
    if problems:
        print("[canonical] ABORT: docker scope check failed:")
        for p in problems:
            print("   - " + p)
        return 2

    conn = recon_db.connect()
    try:
        for line in recon_db.assert_db_identity(conn, expected_tables=4):
            print("[canonical] identity: " + line)

        with conn.cursor() as cur:
            with open(SQL_PATH, encoding="utf-8") as fh:
                ddl = fh.read()

            cur.execute("CREATE SCHEMA %s" % SCRATCH)
            cur.execute("SET LOCAL search_path TO %s" % SCRATCH)
            cur.execute(ddl)
            canonical = normalize(probe.snapshot(cur, SCRATCH), SCRATCH)

            cur.execute("SET LOCAL search_path TO public")
            live = normalize(probe.snapshot(cur, "public"), "public")

            cur.execute(
                "SELECT count(*) FROM information_schema.schemata WHERE schema_name = %s",
                (SCRATCH,),
            )
            scratch_tables = len(canonical["tables"])

        diffs = probe.diff(live, canonical)

        print("[canonical] canonical tables: %s" % canonical["tables"])
        print("[canonical] live tables     : %s" % live["tables"])
        print("[canonical] canonical constraint types: %s" % probe.constraint_types(canonical))
        print("[canonical] live      constraint types: %s" % probe.constraint_types(live))
        print("[canonical] canonical indexes=%d triggers=%d"
              % (sum(len(v) for v in canonical["indexes"].values()), canonical["trigger_count"]))
        print("[canonical] live      indexes=%d triggers=%d"
              % (sum(len(v) for v in live["indexes"].values()), live["trigger_count"]))

        if diffs:
            print("[canonical] RESULT: MISMATCH (%d entr(y/ies))" % len(diffs))
            for line in diffs:
                print("[canonical]   * " + line)
            return 1

        print("[canonical] RESULT: canonical schema is structurally IDENTICAL to "
              "the migrated database (%d tables, %d indexes, 0 triggers)"
              % (scratch_tables, sum(len(v) for v in live["indexes"].values())))
        return 0
    finally:
        # Always discard the scratch schema and every object built in it.
        conn.rollback()
        conn.close()

    # unreachable
    return 0


if __name__ == "__main__":
    sys.exit(main())
