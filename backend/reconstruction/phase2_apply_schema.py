"""Stage 8 Phase 2: apply the minimal reconstruction schema (transactional).

Guarantees
----------
* The connection is taken ONLY from backend/.env.recon (never from the app).
* Database + container + volume identity are re-verified before any DDL.
* All DDL runs in ONE transaction; any failure rolls the whole batch back.
* Refuses to run when public already contains tables (no silent overwrite,
  no CREATE TABLE IF NOT EXISTS, no DROP/TRUNCATE retries).
"""
from __future__ import annotations

import os
import sys

import psycopg2

import recon_db

HERE = os.path.dirname(os.path.abspath(__file__))
SQL_PATH = os.path.join(HERE, "phase2_schema.sql")
EXPECTED_TABLES = ["documents", "pages", "blocks", "legacy_id_map"]


def main() -> int:
    print("[apply] config: " + recon_db.describe_dsn_safe(recon_db.get_dsn()))

    problems = recon_db.assert_docker_scope()
    if problems:
        print("[apply] ABORT: docker scope check failed:")
        for p in problems:
            print("   - " + p)
        return 2
    print("[apply] docker scope: container=%s volume=%s port=127.0.0.1:55432 OK"
          % (recon_db.EXPECTED_CONTAINER, recon_db.EXPECTED_VOLUME))

    conn = recon_db.connect()
    try:
        for line in recon_db.assert_db_identity(conn, expected_tables=0):
            print("[apply] identity: " + line)

        with open(SQL_PATH, encoding="utf-8") as fh:
            ddl = fh.read()

        with conn.cursor() as cur:
            # Single implicit transaction: PostgreSQL DDL is transactional.
            cur.execute(ddl)

            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
            )
            created = [r[0] for r in cur.fetchall()]
            if sorted(created) != sorted(EXPECTED_TABLES):
                raise RuntimeError(
                    "unexpected table set after DDL: %r (expected %r)" % (created, EXPECTED_TABLES)
                )
        conn.commit()
        print("[apply] committed. tables = " + ", ".join(created))
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print("[apply] FAILED and ROLLED BACK: %s: %s" % (type(exc).__name__, exc))
        return 1
    finally:
        conn.close()

    # post-commit verification
    conn = recon_db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
            print("[apply] verify tables: %s" % [r[0] for r in cur.fetchall()])
            cur.execute(
                "SELECT conrelid::regclass::text, contype, count(*) "
                "FROM pg_constraint WHERE connamespace='public'::regnamespace "
                "AND conrelid::regclass::text = ANY(%s) GROUP BY 1,2 ORDER BY 1,2",
                (EXPECTED_TABLES,),
            )
            for row in cur.fetchall():
                print("[apply]   %-14s %s=%d" % (row[0], {"p": "PK", "f": "FK", "u": "UNIQUE", "c": "CHECK"}.get(row[1], row[1]), row[2]))
            cur.execute(
                "SELECT count(*) FROM documents UNION ALL SELECT count(*) FROM pages "
                "UNION ALL SELECT count(*) FROM blocks UNION ALL SELECT count(*) FROM legacy_id_map"
            )
            print("[apply] row counts (should all be 0): %s" % [r[0] for r in cur.fetchall()])
    finally:
        conn.rollback()
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
