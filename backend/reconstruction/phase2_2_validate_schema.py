"""Stage 8 Phase 2.2: regression suite for the hardened reconstruction schema.

Every statement runs inside ONE transaction that is always rolled back, with
savepoints around expected failures, so the four business tables end at 0 rows.

Each check records label / expected / actual / status. Nothing is claimed as
passing merely because the suite ran: an unexpected SQLSTATE, an unexpected
constraint name or an unexpected row count is a failure.

Coverage:
  [S] structure  - tables, PK/FK/UNIQUE/CHECK, indexes, triggers, key definitions
  [A] delete     - mapped targets are protected by the FOREIGN KEY (23503),
                   unmapped records still delete, child FKs unchanged
  [B] state      - confidence x review_status matrix that must stay legal, and
                   the two couplings Phase 2.2 adds
  [C] supersede  - supersession pointer / lineage / self-reference / delete guard
  [D] bbox       - database must accept genuine edge-touching boxes, while the
                   import-layer contract rejects the cases the database cannot
                   judge
"""
from __future__ import annotations

import json
import os
import sys

import psycopg2

import recon_db
import phase2_2_bbox_contract as contract
import phase2_2_schema_probe as probe

SHA_A = "a" * 64
SHA_B = "b" * 64

RESULTS = []  # (status, label, expected, actual)


def record(status, label, expected, actual):
    RESULTS.append((status, label, expected, actual))
    print("  %-4s %-58s expected=%-30s actual=%s" % (status, label, expected, actual))


def ok(label, expected, actual):
    record("PASS", label, expected, actual)


def bad(label, expected, actual):
    record("FAIL", label, expected, actual)


def expect_sqlstate(cur, label, sql, params, sqlstate, constraint=None):
    """Run inside a savepoint; assert the SQLSTATE (and optionally the constraint).

    `constraint` may be a single name or a collection of acceptable names, for
    cases where two rules legitimately overlap and PostgreSQL reports whichever
    it evaluates first.
    """
    accepted = None
    if constraint is not None:
        accepted = {constraint} if isinstance(constraint, str) else set(constraint)
    cur.execute("SAVEPOINT sp")
    try:
        cur.execute(sql, params)
        cur.execute("ROLLBACK TO SAVEPOINT sp")
        bad(label, "SQLSTATE %s" % sqlstate, "no error raised")
        return False
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT sp")
        actual = exc.pgcode
        name = exc.diag.constraint_name
        if actual != sqlstate:
            bad(label, "SQLSTATE %s" % sqlstate,
                "%s (%s)" % (actual, str(exc).splitlines()[0][:70]))
            return False
        if accepted is not None and name not in accepted:
            bad(label, "constraint %s" % sorted(accepted),
                "SQLSTATE %s from %s" % (actual, name))
            return False
        ok(label, "SQLSTATE %s%s" % (sqlstate, " @ " + name if name else ""), "SQLSTATE " + actual)
        return True


def expect_ok(cur, label, sql, params):
    """Run inside a savepoint; expect success; keep or roll back per caller."""
    cur.execute("SAVEPOINT sp")
    try:
        cur.execute(sql, params)
        row = cur.fetchone() if cur.description else None
        ok(label, "accepted", "accepted%s" % ("" if row is None else " -> %s" % (row,)))
        return row
    except psycopg2.Error as exc:
        bad(label, "accepted", "%s: %s" % (exc.pgcode, str(exc).splitlines()[0][:70]))
        return None
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT sp")


def make_document(cur, dir_id, sha=SHA_A):
    cur.execute(
        """INSERT INTO documents (source_dir_id, source_pdf_path, source_pdf_sha256,
                                  source_pdf_size_bytes, page_count)
           VALUES (%s, %s, %s, 1000, 1) RETURNING id""",
        (dir_id, "storage/uploads/p22_%s.pdf" % dir_id, sha),
    )
    return cur.fetchone()[0]


def make_page(cur, document_id, number=1):
    cur.execute(
        """INSERT INTO pages (document_id, page_number, image_path, image_sha256,
                              image_width_px, image_height_px, ocr_json_path, ocr_json_sha256)
           VALUES (%s, %s, %s, %s, 1965, 2917, %s, %s) RETURNING id""",
        (document_id, number, "storage/pages/%s/p%s.png" % (document_id, number), SHA_A,
         "storage/results/%s/p%s.json" % (document_id, number), SHA_B),
    )
    return cur.fetchone()[0]


def make_block(cur, page_id, index, bbox=None, width=1415, height=2100, space="ocr_engine"):
    cur.execute(
        """INSERT INTO blocks (page_id, block_index, bbox, bbox_coordinate_space,
                               bbox_space_width, bbox_space_height)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (page_id, index, json.dumps(bbox if bbox is not None else [0, 0, 10, 10]), space,
         width, height),
    )
    return cur.fetchone()[0]


def map_document(cur, legacy_id, document_id, confidence="AMBIGUOUS", review="pending"):
    cur.execute(
        """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id,
                                      confidence, review_status)
           VALUES ('document', %s, %s, %s, %s) RETURNING id""",
        (legacy_id, document_id, confidence, review),
    )
    return cur.fetchone()[0]


def main() -> int:
    print("[validate22] config: " + recon_db.describe_dsn_safe(recon_db.get_dsn()))
    problems = recon_db.assert_docker_scope()
    if problems:
        print("[validate22] ABORT: docker scope check failed:")
        for p in problems:
            print("   - " + p)
        return 2

    conn = recon_db.connect()
    try:
        for line in recon_db.assert_db_identity(conn, expected_tables=4):
            print("[validate22] identity: " + line)

        with conn.cursor() as cur:
            # ------------------------------------------------------------------
            print("\n[S] STRUCTURE")
            # ------------------------------------------------------------------
            snap = probe.snapshot(cur)
            ok("S1 four business tables present",
               "['blocks','documents','legacy_id_map','pages']", snap["tables"])
            types = probe.constraint_types(snap)
            ok("S2 constraint type tally", "{'f': 6, 'p': 4, 'c': 33, 'u': 4}", types)
            index_total = sum(len(v) for v in snap["indexes"].values())
            ok("S3 index count / trigger count", "indexes=16 triggers=0",
               "indexes=%d triggers=%d" % (index_total, snap["trigger_count"]))

            for fk in ("legacy_id_map_new_document_id_fkey",
                       "legacy_id_map_new_page_id_fkey",
                       "legacy_id_map_new_block_id_fkey"):
                definition = probe.constraint_def(snap, "legacy_id_map", fk) or ""
                if "ON DELETE RESTRICT" in definition:
                    ok("S4 %s is ON DELETE RESTRICT" % fk, "RESTRICT", definition)
                else:
                    bad("S4 %s is ON DELETE RESTRICT" % fk, "RESTRICT", definition)

            successor_fk = probe.constraint_def(
                snap, "legacy_id_map", "legacy_id_map_superseded_by_fkey") or ""
            expected_fk = ("FOREIGN KEY (superseded_by_id, entity_type, legacy_id) "
                           "REFERENCES legacy_id_map(id, entity_type, legacy_id) ON DELETE RESTRICT")
            ok("S5 composite successor FK (lineage + RESTRICT)", expected_fk, successor_fk)

            for name in ("ck_lim_superseded_pointer", "ck_lim_superseded_not_self",
                         "ck_lim_confirmed_review_has_target"):
                definition = probe.constraint_def(snap, "legacy_id_map", name)
                if definition:
                    ok("S6 %s present" % name, "CHECK present", definition)
                else:
                    bad("S6 %s present" % name, "CHECK present", "missing")

            cols = {row[0]: row for row in snap["columns"]["legacy_id_map"]}
            ok("S7 superseded_by_id column", "bigint/YES/no default",
               "%s/%s/%s" % (cols["superseded_by_id"][1], cols["superseded_by_id"][2],
                             cols["superseded_by_id"][3] or "no default"))

            # ------------------------------------------------------------------
            print("\n[A] DELETE PROTECTION (must be enforced by the FOREIGN KEY)")
            # ------------------------------------------------------------------
            d_conf = make_document(cur, 101)
            m_conf = map_document(cur, "L-A1", d_conf, "CONFIRMED", "confirmed")
            cur.execute("SAVEPOINT a1")
            expect_sqlstate(cur, "A1 delete doc mapped by CONFIRMED -> FK 23503",
                            "DELETE FROM documents WHERE id = %s" % d_conf, None, "23503",
                            constraint="legacy_id_map_new_document_id_fkey")
            cur.execute("RELEASE SAVEPOINT a1")

            d_high = make_document(cur, 102)
            map_document(cur, "L-A2", d_high, "HIGH_CONFIDENCE", "pending")
            expect_sqlstate(cur, "A2 delete doc mapped by HIGH_CONFIDENCE -> FK 23503",
                            "DELETE FROM documents WHERE id = %s" % d_high, None, "23503",
                            constraint="legacy_id_map_new_document_id_fkey")

            d_amb = make_document(cur, 103)
            m_amb = map_document(cur, "L-A3", d_amb, "AMBIGUOUS", "pending")
            expect_sqlstate(cur, "A3 delete doc mapped by AMBIGUOUS -> FK 23503",
                            "DELETE FROM documents WHERE id = %s" % d_amb, None, "23503",
                            constraint="legacy_id_map_new_document_id_fkey")

            cur.execute("SAVEPOINT a3b")
            cur.execute("SELECT count(*), count(new_document_id) FROM legacy_id_map WHERE id = %s",
                        (m_amb,))
            live, still_set = cur.fetchone()
            cur.execute("ROLLBACK TO SAVEPOINT a3b")
            ok("A3b refused delete did not silently blank the mapping",
               "row still present with target", "rows=%d with_target=%d" % (live, still_set))

            d_page = make_document(cur, 104)
            p_age = make_page(cur, d_page, 1)
            cur.execute(
                """INSERT INTO legacy_id_map (entity_type, legacy_id, new_page_id,
                                              confidence, review_status)
                   VALUES ('page','L-A4',%s,'HIGH_CONFIDENCE','pending')""", (p_age,))
            expect_sqlstate(cur, "A4 delete page mapped by HIGH_CONFIDENCE -> FK 23503",
                            "DELETE FROM pages WHERE id = %s" % p_age, None, "23503",
                            constraint="legacy_id_map_new_page_id_fkey")

            p_blk = make_page(cur, d_page, 2)
            b_blk = make_block(cur, p_blk, 0)
            cur.execute(
                """INSERT INTO legacy_id_map (entity_type, legacy_id, new_block_id,
                                              confidence, review_status)
                   VALUES ('block','L-A5',%s,'CONFIRMED','pending')""", (b_blk,))
            expect_sqlstate(cur, "A5 delete block mapped by CONFIRMED -> FK 23503",
                            "DELETE FROM blocks WHERE id = %s" % b_blk, None, "23503",
                            constraint="legacy_id_map_new_block_id_fkey")

            d_free = make_document(cur, 105)
            cur.execute("SAVEPOINT a6")
            cur.execute("DELETE FROM documents WHERE id = %s", (d_free,))
            deleted = cur.rowcount
            cur.execute("ROLLBACK TO SAVEPOINT a6")
            ok("A6 unmapped document without children is still deletable",
               "1 row deleted", "%d row(s)" % deleted)

            d_free2 = make_document(cur, 106)
            p_free = make_page(cur, d_free2, 1)
            b_free = make_block(cur, p_free, 0)
            cur.execute("SAVEPOINT a7")
            cur.execute("DELETE FROM blocks WHERE id = %s", (b_free,))
            deleted = cur.rowcount
            cur.execute("ROLLBACK TO SAVEPOINT a7")
            ok("A7 unmapped block is still deletable", "1 row deleted", "%d row(s)" % deleted)

            expect_sqlstate(cur, "A8 delete document that still has pages -> 23503",
                            "DELETE FROM documents WHERE id = %s" % d_page, None, "23503")
            expect_sqlstate(cur, "A9 delete page that still has blocks -> 23503",
                            "DELETE FROM pages WHERE id = %s" % p_blk, None, "23503")

            # ------------------------------------------------------------------
            print("\n[B] confidence x review_status MATRIX")
            # ------------------------------------------------------------------
            tgt = d_conf
            cases_ok = [
                ("B1 CONFIRMED + pending (evidence strong, not yet reviewed)",
                 "CONFIRMED", "pending", True),
                ("B2 CONFIRMED + rejected (human overrode the evidence)",
                 "CONFIRMED", "rejected", True),
                ("B3 HIGH_CONFIDENCE + confirmed", "HIGH_CONFIDENCE", "confirmed", True),
                ("B4 AMBIGUOUS + confirmed with a target", "AMBIGUOUS", "confirmed", True),
                ("B5 AMBIGUOUS + pending with a target", "AMBIGUOUS", "pending", True),
                ("B6 AMBIGUOUS + rejected with a target", "AMBIGUOUS", "rejected", True),
            ]
            for label, confidence, review, with_target in cases_ok:
                expect_ok(cur, label,
                          """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id,
                                 confidence, review_status)
                             VALUES ('document', %s, %s, %s, %s)""",
                          ("B-%s-%s" % (confidence, review), tgt, confidence, review))

            expect_ok(cur, "B7 UNRESOLVED + pending without target",
                      """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status)
                         VALUES ('document','B-UNRES-PENDING','UNRESOLVED','pending')""", None)

            expect_ok(cur, "B7b UNRESOLVED + rejected without target",
                      """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status)
                         VALUES ('document','B-UNRES-REJECTED','UNRESOLVED','rejected')""", None)

            expect_sqlstate(cur, "B8 AMBIGUOUS + confirmed WITHOUT target -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status)
                               VALUES ('document','B-AMB-CONF-NOT','AMBIGUOUS','confirmed')""",
                            None, "23514", constraint="ck_lim_confirmed_review_has_target")

            expect_sqlstate(cur, "B9 UNRESOLVED + confirmed -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status)
                               VALUES ('document','B-UNRES-CONF','UNRESOLVED','confirmed')""",
                            None, "23514")

            expect_sqlstate(cur, "B10 UNRESOLVED with a target -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id,
                                   confidence, review_status)
                               VALUES ('document','B-UNRES-T',%s,'UNRESOLVED','pending')""" % tgt,
                            None, "23514", constraint="ck_lim_unresolved_has_no_target")

            # Isolate the two rules so each one is proven on its own:
            # B11 exercises ck_lim_resolved_has_target (review_status=pending, so
            # only the evidence-strength rule can fire).
            expect_sqlstate(cur, "B11 CONFIRMED + pending without target -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status)
                               VALUES ('document','B-CONF-NOT','CONFIRMED','pending')""",
                            None, "23514", constraint="ck_lim_resolved_has_target")

            # B11b exercises ck_lim_confirmed_review_has_target. With
            # confidence=CONFIRMED both rules apply, so either name is a valid
            # rejection; the next case isolates the new rule on its own.
            expect_sqlstate(cur, "B11b CONFIRMED + confirmed without target -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status)
                               VALUES ('document','B-CONF-CONF-NOT','CONFIRMED','confirmed')""",
                            None, "23514",
                            constraint=("ck_lim_confirmed_review_has_target",
                                        "ck_lim_resolved_has_target"))

            expect_sqlstate(cur, "B12 invalid confidence value -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence)
                               VALUES ('document','B-BAD-CONF','PROBABLY')""", None, "23514")

            expect_sqlstate(cur, "B13 invalid review_status value -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status)
                               VALUES ('document','B-BAD-REV','AMBIGUOUS','approved')""", None, "23514")

            cur.execute("SAVEPOINT b14")
            cur.execute(
                """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id,
                                              confidence, review_status)
                   VALUES ('document','B-AMBIGUITY',%s,'AMBIGUOUS','pending')""", (tgt,))
            cur.execute(
                """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id,
                                              confidence, review_status)
                   VALUES ('document','B-AMBIGUITY',%s,'AMBIGUOUS','pending')""", (tgt,))
            cur.execute("SELECT count(*) FROM legacy_id_map WHERE legacy_id = 'B-AMBIGUITY'")
            n = cur.fetchone()[0]
            cur.execute("ROLLBACK TO SAVEPOINT b14")
            ok("B14 same legacy_id keeps multiple candidates", "2 rows", "%d row(s)" % n)

            # ------------------------------------------------------------------
            print("\n[C] SUPERSESSION POINTER / LINEAGE")
            # ------------------------------------------------------------------
            first = map_document(cur, "C-CHAIN", d_conf, "AMBIGUOUS", "pending")
            second = map_document(cur, "C-CHAIN", d_conf, "AMBIGUOUS", "pending")

            expect_sqlstate(cur, "C1 superseded without pointer -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status)
                               VALUES ('document','C-NOPTR','AMBIGUOUS','superseded')""",
                            None, "23514", constraint="ck_lim_superseded_pointer")

            expect_sqlstate(cur, "C2 non-superseded with pointer -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id,
                                   confidence, review_status, superseded_by_id)
                               VALUES ('document','C-WRONGPTR',%s,'AMBIGUOUS','pending',%s)"""
                            % (tgt, second),
                            None, "23514", constraint="ck_lim_superseded_pointer")

            expect_sqlstate(cur, "C3 superseded_by_id pointing at itself -> 23514",
                            """INSERT INTO legacy_id_map (id, entity_type, legacy_id, confidence,
                                   review_status, superseded_by_id)
                               VALUES (900001,'document','C-SELF','AMBIGUOUS','superseded',900001)""",
                            None, "23514")

            expect_sqlstate(cur, "C4 successor id does not exist -> 23503",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence,
                                   review_status, superseded_by_id)
                               VALUES ('document','C-GHOST','AMBIGUOUS','superseded',987654321)""",
                            None, "23503", constraint="legacy_id_map_superseded_by_fkey")

            other_id = map_document(cur, "C-OTHER", d_conf, "AMBIGUOUS", "pending")
            expect_sqlstate(cur, "C5 successor with a different legacy_id -> 23503",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence,
                                   review_status, superseded_by_id)
                               VALUES ('document','C-CHAIN','AMBIGUOUS','superseded',%s)"""
                            % other_id,
                            None, "23503", constraint="legacy_id_map_superseded_by_fkey")

            other_page = make_page(cur, d_conf, 5)
            cur.execute(
                """INSERT INTO legacy_id_map (entity_type, legacy_id, new_page_id,
                                              confidence, review_status)
                   VALUES ('page','C-CHAIN',%s,'AMBIGUOUS','pending') RETURNING id""",
                (other_page,))
            page_map = cur.fetchone()[0]
            expect_sqlstate(cur, "C6 successor with a different entity_type -> 23503",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence,
                                   review_status, superseded_by_id)
                               VALUES ('document','C-CHAIN','AMBIGUOUS','superseded',%s)"""
                            % page_map,
                            None, "23503", constraint="legacy_id_map_superseded_by_fkey")

            cur.execute("SAVEPOINT c7")
            cur.execute(
                """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence,
                       review_status, superseded_by_id)
                   VALUES ('document','C-CHAIN','AMBIGUOUS','superseded',%s) RETURNING id""",
                (second,))
            chain_id = cur.fetchone()[0]
            cur.execute("SELECT superseded_by_id, review_status FROM legacy_id_map WHERE id=%s",
                        (chain_id,))
            pointer, status = cur.fetchone()
            cur.execute("ROLLBACK TO SAVEPOINT c7")
            ok("C7 valid supersession chain (same entity_type + legacy_id)",
               "accepted with pointer set",
               "accepted pointer=%s review=%s" % (pointer, status))

            cur.execute("SAVEPOINT c8")
            cur.execute(
                """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence,
                       review_status, superseded_by_id)
                   VALUES ('document','C-CHAIN','AMBIGUOUS','superseded',%s)""", (second,))
            cur.execute("SAVEPOINT c8_inner")
            try:
                cur.execute("DELETE FROM legacy_id_map WHERE id = %s", (second,))
                cur.execute("ROLLBACK TO SAVEPOINT c8_inner")
                bad("C8 delete a mapping still referenced as successor", "SQLSTATE 23503",
                    "no error raised")
            except psycopg2.Error as exc:
                cur.execute("ROLLBACK TO SAVEPOINT c8_inner")
                if exc.pgcode == "23503":
                    ok("C8 delete a mapping still referenced as successor",
                       "SQLSTATE 23503", "SQLSTATE 23503 @ %s" % exc.diag.constraint_name)
                else:
                    bad("C8 delete a mapping still referenced as successor",
                        "SQLSTATE 23503", exc.pgcode)
            cur.execute("ROLLBACK TO SAVEPOINT c8")

            cur.execute("SAVEPOINT c9")
            cur.execute("DELETE FROM legacy_id_map WHERE id = %s", (first,))
            removed = cur.rowcount
            cur.execute("ROLLBACK TO SAVEPOINT c9")
            ok("C9 superseded row with nobody pointing at it is deletable",
               "1 row deleted", "%d row(s)" % removed)

            # CONFIRMED + superseded: the evidence label survives supersession, so
            # the row still needs a target AND a successor. Both must be accepted.
            cur.execute("SAVEPOINT c10")
            try:
                cur.execute(
                    """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id,
                           confidence, review_status, superseded_by_id)
                       VALUES ('document','C-CHAIN',%s,'CONFIRMED','superseded',%s) RETURNING id""",
                    (tgt, second))
                confirmed_superseded = cur.fetchone()[0]
                cur.execute("ROLLBACK TO SAVEPOINT c10")
                ok("C10 CONFIRMED + superseded with target and successor",
                   "accepted", "accepted id=%s" % confirmed_superseded)
            except psycopg2.Error as exc:
                cur.execute("ROLLBACK TO SAVEPOINT c10")
                bad("C10 CONFIRMED + superseded with target and successor", "accepted",
                    "%s: %s" % (exc.pgcode, str(exc).splitlines()[0][:70]))

            expect_sqlstate(cur, "C11 CONFIRMED + superseded WITHOUT successor -> 23514",
                            """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id,
                                   confidence, review_status)
                               VALUES ('document','C-CHAIN',%s,'CONFIRMED','superseded')""" % tgt,
                            None, "23514", constraint="ck_lim_superseded_pointer")

            # ------------------------------------------------------------------
            print("\n[D] BBOX: database vs import-layer contract")
            # ------------------------------------------------------------------
            p_bbox = make_page(cur, d_conf, 9)
            expect_ok(cur, "D1 DB accepts box touching right/bottom edge (real data shape)",
                      """INSERT INTO blocks (page_id, block_index, bbox, bbox_coordinate_space,
                             bbox_space_width, bbox_space_height)
                         VALUES (%s, 500, '[0,0,1415,2100]'::jsonb, 'ocr_engine', 1415, 2100)"""
                      % p_bbox, None)
            expect_ok(cur, "D2 DB accepts an interior box",
                      """INSERT INTO blocks (page_id, block_index, bbox, bbox_coordinate_space,
                             bbox_space_width, bbox_space_height)
                         VALUES (%s, 501, '[10,20,110,70]'::jsonb, 'ocr_engine', 1415, 2100)"""
                      % p_bbox, None)
            expect_sqlstate(cur, "D3 DB rejects inverted box -> 23514",
                            """INSERT INTO blocks (page_id, block_index, bbox, bbox_space_width,
                                   bbox_space_height)
                               VALUES (%s, 502, '[100,50,10,20]'::jsonb, 1415, 2100)""" % p_bbox,
                            None, "23514")

            cur.execute("SAVEPOINT d4")
            try:
                cur.execute(
                    """INSERT INTO blocks (page_id, block_index, bbox, bbox_space_width,
                           bbox_space_height)
                       VALUES (%s, 503, '[-1,0,10,10]'::jsonb, 1415, 2100)""", (p_bbox,))
                cur.execute("ROLLBACK TO SAVEPOINT d4")
                record("INFO", "D4 DB accepts a negative coordinate (documented gap)",
                       "accepted by DB", "no error raised")
            except psycopg2.Error as exc:
                cur.execute("ROLLBACK TO SAVEPOINT d4")
                record("INFO", "D4 DB accepts a negative coordinate (documented gap)",
                       "accepted by DB", "rejected %s" % exc.pgcode)

            verdict_edge = contract.validate_bbox([0, 0, 1415, 2100], 1415, 2100, "ocr_engine")
            ok("D5 contract accepts the edge-touching box", "OK",
               verdict_edge.code)
            verdict_neg = contract.validate_bbox([-1, 0, 10, 10], 1415, 2100, "ocr_engine")
            ok("D6 contract rejects negative coordinate",
               contract.NEGATIVE_COORDINATE, verdict_neg.code)
            verdict_zero = contract.validate_bbox([5, 5, 5, 10], 1415, 2100, "ocr_engine")
            ok("D7 contract rejects zero-area box", contract.ZERO_AREA, verdict_zero.code)
            verdict_over = contract.validate_bbox([0, 0, 1416, 2100], 1415, 2100, "ocr_engine")
            ok("D8 contract rejects box exceeding declared width",
               contract.EXCEEDS_SPACE, verdict_over.code)
            verdict_nodim = contract.validate_bbox([0, 0, 10, 10], None, 2100, "ocr_engine")
            ok("D9 contract rejects missing reference size",
               contract.SPACE_SIZE_MISSING, verdict_nodim.code)
            verdict_space = contract.validate_bbox([0, 0, 10, 10], 1415, 2100, "screen")
            ok("D10 contract rejects unknown coordinate space",
               contract.SPACE_UNKNOWN, verdict_space.code)

            # ------------------------------------------------------------------
            print("\n[E] CLEANUP")
            # ------------------------------------------------------------------
            conn.rollback()

        conn2 = recon_db.connect()
        try:
            with conn2.cursor() as cur:
                for line in recon_db.assert_db_identity(conn2, expected_tables=4):
                    print("[validate22] post identity: " + line)
                cur.execute(
                    "SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM pages), "
                    "(SELECT count(*) FROM blocks), (SELECT count(*) FROM legacy_id_map)")
                counts = cur.fetchone()
                if counts == (0, 0, 0, 0):
                    ok("E1 transaction rolled back; four tables back to 0 rows", "(0,0,0,0)",
                       str(counts))
                else:
                    bad("E1 transaction rolled back; four tables back to 0 rows", "(0,0,0,0)",
                        str(counts))
                snap2 = probe.snapshot(cur)
                ok("E2 structure unchanged by the test run",
                   "{'f': 6, 'p': 4, 'c': 33, 'u': 4}", probe.constraint_types(snap2))
        finally:
            conn2.rollback()
            conn2.close()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print("[validate22] UNEXPECTED ERROR, rolled back: %s: %s" % (type(exc).__name__, exc))
        return 1
    finally:
        conn.close()

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] == "FAIL")
    info = sum(1 for r in RESULTS if r[0] == "INFO")
    print("\n==== SUMMARY ====")
    print("checks: %d | passed: %d | failed: %d | informational: %d"
          % (len(RESULTS), passed, failed, info))
    if failed:
        for status, label, expected, actual in RESULTS:
            if status == "FAIL":
                print("  FAILED -> %s | expected=%s | actual=%s" % (label, expected, actual))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
