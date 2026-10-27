"""Stage 8 Phase 2.2: read-only structure introspection for the recon schema.

Shared by the hardening applier, the regression suite and the canonical-schema
equivalence check so that all three describe structure in exactly the same way.
No DDL, no DML.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

BUSINESS_TABLES = ("documents", "pages", "blocks", "legacy_id_map")


def snapshot(cur, schema: str = "public") -> Dict[str, object]:
    """Return a normalized, comparable description of the schema structure."""
    cur.execute(
        """SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename""",
        (schema,),
    )
    tables = [r[0] for r in cur.fetchall()]

    cur.execute(
        """SELECT table_name, column_name, data_type, is_nullable,
                  coalesce(column_default, ''), coalesce(character_maximum_length, -1)
           FROM information_schema.columns
           WHERE table_schema = %s
           ORDER BY table_name, ordinal_position""",
        (schema,),
    )
    columns: Dict[str, List[Tuple]] = {}
    for row in cur.fetchall():
        columns.setdefault(row[0], []).append(tuple(row[1:]))

    cur.execute(
        """SELECT c.conrelid::regclass::text, c.conname, c.contype::text,
                  pg_get_constraintdef(c.oid)
           FROM pg_constraint c
           WHERE c.connamespace = %s::regnamespace
           ORDER BY 1, 2""",
        (schema,),
    )
    constraints: Dict[str, List[Tuple]] = {}
    for rel, name, contype, definition in cur.fetchall():
        constraints.setdefault(rel, []).append((name, contype, definition))

    cur.execute(
        """SELECT tablename, indexname, regexp_replace(indexdef, '^CREATE (UNIQUE )?INDEX \\S+ ON ', '')
           FROM pg_indexes WHERE schemaname = %s ORDER BY 1, 2""",
        (schema,),
    )
    indexes: Dict[str, List[Tuple]] = {}
    for table, name, definition in cur.fetchall():
        indexes.setdefault(table, []).append((name, definition))

    cur.execute(
        """SELECT count(*) FROM information_schema.triggers WHERE trigger_schema = %s""",
        (schema,),
    )
    trigger_count = cur.fetchone()[0]

    return {
        "schema": schema,
        "tables": tables,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "trigger_count": trigger_count,
    }


def constraint_types(snap: Dict[str, object]) -> Dict[str, int]:
    """Count constraints by type letter across all tables in the snapshot."""
    tally: Dict[str, int] = {}
    for rows in snap["constraints"].values():  # type: ignore[index]
        for _name, contype, _definition in rows:
            tally[contype] = tally.get(contype, 0) + 1
    return tally


def constraint_def(snap: Dict[str, object], table: str, name: str) -> str | None:
    for cname, _contype, definition in snap["constraints"].get(table, []):  # type: ignore[index]
        if cname == name:
            return definition
    return None


def diff(before: Dict[str, object], after: Dict[str, object]) -> List[str]:
    """Human-readable list of structural differences (empty when identical)."""
    out: List[str] = []
    if before["tables"] != after["tables"]:
        out.append("tables: %r -> %r" % (before["tables"], after["tables"]))
    for table in sorted(set(before["columns"]) | set(after["columns"])):  # type: ignore[arg-type]
        b = before["columns"].get(table, [])  # type: ignore[index]
        a = after["columns"].get(table, [])  # type: ignore[index]
        if b != a:
            out.append("columns[%s]: %r -> %r" % (table, b, a))
    for table in sorted(set(before["constraints"]) | set(after["constraints"])):  # type: ignore[arg-type]
        b = {n: (t, d) for n, t, d in before["constraints"].get(table, [])}  # type: ignore[index]
        a = {n: (t, d) for n, t, d in after["constraints"].get(table, [])}  # type: ignore[index]
        for name in sorted(set(b) | set(a)):
            if b.get(name) != a.get(name):
                out.append("constraint[%s.%s]: %r -> %r" % (table, name, b.get(name), a.get(name)))
    for table in sorted(set(before["indexes"]) | set(after["indexes"])):  # type: ignore[arg-type]
        b = dict(before["indexes"].get(table, []))  # type: ignore[index]
        a = dict(after["indexes"].get(table, []))  # type: ignore[index]
        for name in sorted(set(b) | set(a)):
            if b.get(name) != a.get(name):
                out.append("index[%s.%s]: %r -> %r" % (table, name, b.get(name), a.get(name)))
    if before["trigger_count"] != after["trigger_count"]:
        out.append("triggers: %r -> %r" % (before["trigger_count"], after["trigger_count"]))
    return out
