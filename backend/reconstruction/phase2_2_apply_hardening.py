"""Stage 8 Phase 2.2: apply the schema hardening migration (transactional).

Guarantees
----------
* Connection comes ONLY from backend/.env.recon (never from the application).
* Container / volume / port / database identity are re-verified first.
* The whole migration runs in ONE transaction; any failure rolls it back and the
  process exits non-zero without touching anything else.
* Refuses to run when the business tables are not empty, or when the current
  structure is not the known Phase 2 definition, so a second run fails loudly
  instead of silently re-creating constraints.
"""
from __future__ import annotations

import os
import sys

import recon_db
import phase2_2_schema_probe as probe

HERE = os.path.dirname(os.path.abspath(__file__))
SQL_PATH = os.path.join(HERE, "phase2_2_schema_hardening.sql")

TARGET_FKS = {
    "legacy_id_map_new_document_id_fkey": "new_document_id",
    "legacy_id_map_new_page_id_fkey": "new_page_id",
    "legacy_id_map_new_block_id_fkey": "new_block_id",
}

REQUIRED_NEW_CONSTRAINTS = {
    "uq_lim_id_entity_legacy": "u",
    "legacy_id_map_superseded_by_fkey": "f",
    "ck_lim_superseded_pointer": "c",
    "ck_lim_superseded_not_self": "c",
    "ck_lim_confirmed_review_has_target": "c",
}

REQUIRED_NEW_INDEX = "ix_lim_superseded_by_id"


def row_counts(cur):
    cur.execute(
        "SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM pages), "
        "(SELECT count(*) FROM blocks), (SELECT count(*) FROM legacy_id_map)"
    )
    return dict(zip(probe.BUSINESS_TABLES, cur.fetchone()))


def constraint_lookup(snap):
    """{(table, constraint_name): (contype, definition)}"""
    return {
        (table, name): (contype, definition)
        for table, rows in snap["constraints"].items()
        for name, contype, definition in rows
    }


def verify_post(after):
    """Return a list of structural problems found after the DDL."""
    lookup = constraint_lookup(after)
    failures = []

    for name in TARGET_FKS:
        entry = lookup.get(("legacy_id_map", name))
        if entry is None:
            failures.append("%s missing" % name)
            continue
        contype, definition = entry
        if contype != "f":
            failures.append("%s is not a foreign key (%s)" % (name, contype))
        if "ON DELETE RESTRICT" not in definition:
            failures.append("%s is not ON DELETE RESTRICT: %s" % (name, definition))
        if "SET NULL" in definition:
            failures.append("%s still declares SET NULL" % name)

    for name, expected_type in REQUIRED_NEW_CONSTRAINTS.items():
        entry = lookup.get(("legacy_id_map", name))
        if entry is None:
            failures.append("new constraint %s missing" % name)
            continue
        contype, definition = entry
        if contype != expected_type:
            failures.append("%s type=%s expected=%s" % (name, contype, expected_type))
        if name == "legacy_id_map_superseded_by_fkey":
            if "(superseded_by_id, entity_type, legacy_id)" in definition:
                pass
            else:
                failures.append("successor FK is not the composite (entity_type, legacy_id) form: %s"
                                % definition)
            if "REFERENCES legacy_id_map(id, entity_type, legacy_id)" not in definition:
                failures.append("successor FK does not reference (id, entity_type, legacy_id): %s"
                                % definition)

    index_names = {name for name, _def in after["indexes"].get("legacy_id_map", [])}
    if REQUIRED_NEW_INDEX not in index_names:
        failures.append("index %s missing" % REQUIRED_NEW_INDEX)

    column_names = {row[0] for row in after["columns"].get("legacy_id_map", [])}
    if "superseded_by_id" not in column_names:
        failures.append("column legacy_id_map.superseded_by_id missing")

    return failures


def main() -> int:
    print("[apply22] config: " + recon_db.describe_dsn_safe(recon_db.get_dsn()))

    problems = recon_db.assert_docker_scope()
    if problems:
        print("[apply22] ABORT: docker scope check failed:")
        for p in problems:
            print("   - " + p)
        return 2
    print("[apply22] docker scope: container=%s volume=%s port=127.0.0.1:55432 OK"
          % (recon_db.EXPECTED_CONTAINER, recon_db.EXPECTED_VOLUME))

    conn = recon_db.connect()
    before = None
    after = None
    try:
        for line in recon_db.assert_db_identity(conn, expected_tables=4):
            print("[apply22] identity: " + line)

        with conn.cursor() as cur:
            counts = row_counts(cur)
            print("[apply22] pre row counts: %s" % counts)
            if any(counts.values()):
                print("[apply22] ABORT: business tables are not empty; refusing to alter structure")
                conn.rollback()
                return 3

            before = probe.snapshot(cur)
            lookup = constraint_lookup(before)
            for name, column in TARGET_FKS.items():
                entry = lookup.get(("legacy_id_map", name))
                if entry is None:
                    print("[apply22] ABORT: expected FK %s is missing" % name)
                    conn.rollback()
                    return 4
                definition = entry[1]
                if "ON DELETE RESTRICT" in definition:
                    print("[apply22] ABORT: %s already RESTRICT; migration already applied" % name)
                    conn.rollback()
                    return 4
                if "ON DELETE SET NULL" not in definition:
                    print("[apply22] ABORT: %s has unexpected definition: %s" % (name, definition))
                    conn.rollback()
                    return 4
                print("[apply22] pre  %-34s (%s) %s" % (name, column, definition))
            print("[apply22] pre  constraint types: %s" % probe.constraint_types(before))
            print("[apply22] pre  indexes=%d triggers=%d"
                  % (sum(len(v) for v in before["indexes"].values()), before["trigger_count"]))

            with open(SQL_PATH, encoding="utf-8") as fh:
                ddl = fh.read()

            # Single implicit transaction: PostgreSQL DDL is transactional.
            cur.execute(ddl)

            after = probe.snapshot(cur)
            failures = verify_post(after)
            counts_after = row_counts(cur)
            if any(counts_after.values()):
                failures.append("row counts changed during DDL: %s" % counts_after)
            if failures:
                raise RuntimeError("post-DDL verification failed: " + "; ".join(failures))

        conn.commit()

        print("[apply22] post constraint types: %s" % probe.constraint_types(after))
        print("[apply22] post indexes=%d triggers=%d"
              % (sum(len(v) for v in after["indexes"].values()), after["trigger_count"]))
        for name in TARGET_FKS:
            print("[apply22] post %-34s %s"
                  % (name, probe.constraint_def(after, "legacy_id_map", name)))
        print("[apply22] post %-34s %s"
              % ("legacy_id_map_superseded_by_fkey",
                 probe.constraint_def(after, "legacy_id_map", "legacy_id_map_superseded_by_fkey")))
        print("[apply22] committed.")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print("[apply22] FAILED and ROLLED BACK: %s: %s" % (type(exc).__name__, exc))
        return 1
    finally:
        conn.close()

    # Independant post-commit read-back on a fresh connection.
    conn2 = recon_db.connect()
    try:
        with conn2.cursor() as cur:
            for line in recon_db.assert_db_identity(conn2, expected_tables=4):
                print("[apply22] verify identity: " + line)
            print("[apply22] verify row counts: %s" % row_counts(cur))
            if before is not None and after is not None:
                changes = probe.diff(before, after)
                print("[apply22] STRUCTURE DIFF entries: %d" % len(changes))
                for line in changes:
                    print("[apply22]   * " + line)
    finally:
        conn2.rollback()
        conn2.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
