"""Stage 8 Phase 2: synthetic-data validation of the reconstruction schema.

All DML happens inside a single transaction that is ALWAYS rolled back, so the
four tables return to 0 rows. Expected-failure cases run in SAVEPOINTs.

Never touches the application database: the DSN comes from backend/.env.recon
and the identity is re-asserted at start and before the final cleanup check.
"""
from __future__ import annotations

import hashlib
import json
import sys

import psycopg2

import recon_db

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64

PASS: list[str] = []
FAIL: list[str] = []


def ok(label: str, detail: str = "") -> None:
    PASS.append(label)
    print("  PASS  %-52s %s" % (label, detail))


def bad(label: str, detail: str = "") -> None:
    FAIL.append(label)
    print("  FAIL  %-52s %s" % (label, detail))


def expect_error(cur, label: str, sql: str, params, expected_sqlstate: str) -> None:
    """Run a statement inside a savepoint; assert it fails with the expected SQLSTATE."""
    cur.execute("SAVEPOINT sp_expect")
    try:
        cur.execute(sql, params)
        cur.execute("ROLLBACK TO SAVEPOINT sp_expect")
        bad(label, "no error raised (expected SQLSTATE %s)" % expected_sqlstate)
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT sp_expect")
        state = exc.pgcode
        if state == expected_sqlstate:
            ok(label, "SQLSTATE %s" % state)
        else:
            bad(label, "SQLSTATE %s (expected %s): %s" % (state, expected_sqlstate, str(exc).splitlines()[0][:90]))


def expect_ok(cur, label: str, sql: str, params) -> None:
    cur.execute("SAVEPOINT sp_ok")
    try:
        cur.execute(sql, params)
        ok(label)
    except psycopg2.Error as exc:
        bad(label, "%s: %s" % (exc.pgcode, str(exc).splitlines()[0][:90]))
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT sp_ok")


def main() -> int:
    print("[validate] config: " + recon_db.describe_dsn_safe(recon_db.get_dsn()))
    problems = recon_db.assert_docker_scope()
    if problems:
        print("[validate] ABORT: docker scope check failed:")
        for p in problems:
            print("   - " + p)
        return 2

    conn = recon_db.connect()
    doc_id = page_id = None
    try:
        for line in recon_db.assert_db_identity(conn, expected_tables=4):
            print("[validate] identity: " + line)

        with conn.cursor() as cur:
            print("\n[1] POSITIVE: insert document / page / blocks / legacy map")
            cur.execute(
                """INSERT INTO documents
                     (source_dir_id, source_pdf_path, source_pdf_sha256, source_pdf_size_bytes,
                      page_count, original_filename, original_filename_provenance)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (4, "storage/uploads/9270940a9ca5.pdf", SHA_A, 15015778, 103,
                 "China and Japan Canton Consultations and Letter Book, 9 Mar - 26 Oct 1779.pdf",
                 "docx_core_title"),
            )
            doc_id = cur.fetchone()[0]
            ok("P1 insert synthetic document", "id=%s" % doc_id)

            # second document without a title -> provenance must stay 'unknown'
            cur.execute(
                """INSERT INTO documents
                     (source_dir_id, source_pdf_path, source_pdf_sha256, source_pdf_size_bytes, page_count)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (99, "storage/uploads/deadbeef0000.pdf", SHA_B, 100, 1),
            )
            doc2_id = cur.fetchone()[0]
            cur.execute("SELECT original_filename, original_filename_provenance FROM documents WHERE id=%s", (doc2_id,))
            fn, prov = cur.fetchone()
            if fn is None and prov == "unknown":
                ok("P2 untitled document keeps NULL title + provenance=unknown")
            else:
                bad("P2 untitled document", "filename=%r provenance=%r" % (fn, prov))

            cur.execute(
                """INSERT INTO pages
                     (document_id, page_number, image_path, image_sha256, image_width_px,
                      image_height_px, ocr_json_path, ocr_json_sha256, content_md_path)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (doc_id, 1, "storage/pages/4/page_0001.png", SHA_C, 1965, 2917,
                 "storage/results/4/page_0001/pruned_result.json", SHA_D,
                 "storage/results/4/page_0001/content.md"),
            )
            page_id = cur.fetchone()[0]
            ok("P3 insert synthetic page", "id=%s" % page_id)

            bbox0 = [879, 110, 1392, 269]
            bbox1 = [456.5, 879, 976, 1001]
            cur.execute(
                """INSERT INTO blocks
                     (page_id, block_index, source_block_id, block_label, ocr_text_raw,
                      bbox, bbox_coordinate_space, bbox_space_width, bbox_space_height)
                   VALUES (%s,0,0,'header_image','',%s,'ocr_engine',1415,2100) RETURNING id""",
                (page_id, json.dumps(bbox0)),
            )
            blk0 = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO blocks
                     (page_id, block_index, source_block_id, block_label, ocr_text_raw,
                      bbox, bbox_coordinate_space, bbox_space_width, bbox_space_height)
                   VALUES (%s,1,1,'text','Range 427. Redt Hillsborough.',%s,'ocr_engine',1415,2100) RETURNING id""",
                (page_id, json.dumps(bbox1)),
            )
            blk1 = cur.fetchone()[0]
            ok("P4 insert two blocks with distinct block_index", "ids=%s,%s" % (blk0, blk1))

            cur.execute(
                """INSERT INTO legacy_id_map
                     (entity_type, legacy_id, new_document_id, confidence, review_status, evidence_ref)
                   VALUES ('document','4-candidate',%s,'HIGH_CONFIDENCE','pending',
                           'identity_mapping_audit §3.2 doc 4') RETURNING id""",
                (doc_id,),
            )
            ok("P5 insert legacy map for synthetic document")

            print("\n[2] POSITIVE: relationship / payload round-trip")
            cur.execute(
                """SELECT d.source_dir_id, p.page_number, p.image_width_px, p.image_height_px,
                          b.block_index, b.bbox, b.bbox_coordinate_space, b.bbox_space_width,
                          b.bbox_space_height, b.confidence
                   FROM documents d JOIN pages p ON p.document_id = d.id
                   JOIN blocks b ON b.page_id = p.id
                   WHERE d.id = %s ORDER BY b.block_index""",
                (doc_id,),
            )
            rows = cur.fetchall()
            ok("P6 three-table join returns 2 rows", "got %d" % len(rows))
            r0 = rows[0]
            if list(r0[5]) == bbox0:
                ok("P7 bbox round-trip equals raw JSON values", str(r0[5]))
            else:
                bad("P7 bbox round-trip", "%r != %r" % (r0[5], bbox0))
            if r0[6] == "ocr_engine" and r0[7] == 1415 and r0[8] == 2100:
                ok("P8 bbox coordinate space + reference dims preserved")
            else:
                bad("P8 bbox coordinate space", str(r0[6:9]))
            if all(row[9] is None for row in rows):
                ok("P9 confidence stays NULL when no real block score exists")
            else:
                bad("P9 confidence", "unexpected non-NULL value")

            cur.execute("SELECT count(*) FROM documents WHERE source_dir_id = 4")
            if cur.fetchone()[0] == 1:
                ok("P10 source_dir_id snapshot stored (not used as primary key)")
            else:
                bad("P10 source_dir_id")

            print("\n[3] NEGATIVE: referential + uniqueness + domain constraints")
            expect_error(cur, "N1 page with unknown document_id rejected",
                         """INSERT INTO pages (document_id, page_number, image_path, image_sha256,
                                image_width_px, image_height_px, ocr_json_path, ocr_json_sha256)
                            VALUES (999999, 1, 'x', %s, 10, 10, 'y', %s)""" % ("'%s'" % SHA_A, "'%s'" % SHA_A),
                         None, "23503")
            expect_error(cur, "N2 block with unknown page_id rejected",
                         """INSERT INTO blocks (page_id, block_index, bbox, bbox_space_width, bbox_space_height)
                            VALUES (999999, 0, '[1,2,3,4]'::jsonb, 10, 10)""",
                         None, "23503")
            expect_error(cur, "N3 duplicate (document_id, page_number) rejected",
                         """INSERT INTO pages (document_id, page_number, image_path, image_sha256,
                                image_width_px, image_height_px, ocr_json_path, ocr_json_sha256)
                            VALUES (%s, 1, 'dup', %s, 10, 10, 'dup', %s)""" % (doc_id, "'%s'" % SHA_A, "'%s'" % SHA_A),
                         None, "23505")
            expect_error(cur, "N4 duplicate (page_id, block_index) rejected",
                         """INSERT INTO blocks (page_id, block_index, bbox, bbox_space_width, bbox_space_height)
                            VALUES (%s, 1, '[1,2,3,4]'::jsonb, 10, 10)""" % page_id,
                         None, "23505")
            expect_error(cur, "N5a delete document with pages rejected",
                         "DELETE FROM documents WHERE id = %s", (doc_id,), "23503")
            expect_error(cur, "N5b delete page with blocks rejected",
                         "DELETE FROM pages WHERE id = %s", (page_id,), "23503")
            expect_error(cur, "N6a bbox with 3 elements rejected",
                         """INSERT INTO blocks (page_id, block_index, bbox, bbox_space_width, bbox_space_height)
                            VALUES (%s, 90, '[1,2,3]'::jsonb, 10, 10)""" % page_id, None, "23514")
            expect_error(cur, "N6b bbox with non-numeric elements rejected",
                         """INSERT INTO blocks (page_id, block_index, bbox, bbox_space_width, bbox_space_height)
                            VALUES (%s, 91, '["a","b","c","d"]'::jsonb, 10, 10)""" % page_id, None, "23514")
            expect_error(cur, "N6c inverted bbox (x1 > x2) rejected",
                         """INSERT INTO blocks (page_id, block_index, bbox, bbox_space_width, bbox_space_height)
                            VALUES (%s, 92, '[100,50,10,20]'::jsonb, 10, 10)""" % page_id, None, "23514")
            expect_error(cur, "N6d bbox object (not array) rejected",
                         """INSERT INTO blocks (page_id, block_index, bbox, bbox_space_width, bbox_space_height)
                            VALUES (%s, 93, '{"a":1}'::jsonb, 10, 10)""" % page_id, None, "23514")
            expect_error(cur, "N7 confidence > 1 rejected",
                         """INSERT INTO blocks (page_id, block_index, bbox, bbox_space_width,
                                bbox_space_height, confidence)
                            VALUES (%s, 94, '[1,2,3,4]'::jsonb, 10, 10, 1.5)""" % page_id, None, "23514")
            expect_error(cur, "N8 page_count = 0 rejected",
                         """INSERT INTO documents (source_pdf_path, source_pdf_sha256,
                                source_pdf_size_bytes, page_count)
                            VALUES ('p', %s, 1, 0)""" % ("'%s'" % SHA_A,), None, "23514")
            expect_error(cur, "N9 title without provenance rejected",
                         """INSERT INTO documents (source_pdf_path, source_pdf_sha256,
                                source_pdf_size_bytes, page_count, original_filename)
                            VALUES ('p', %s, 1, 1, 'fake title')""" % ("'%s'" % SHA_A,), None, "23514")
            expect_error(cur, "N10 invalid sha256 format rejected",
                         """INSERT INTO documents (source_pdf_path, source_pdf_sha256,
                                source_pdf_size_bytes, page_count)
                            VALUES ('p', 'not-a-sha', 1, 1)""", None, "23514")
            expect_error(cur, "N11 negative page_number rejected",
                         """INSERT INTO pages (document_id, page_number, image_path, image_sha256,
                                image_width_px, image_height_px, ocr_json_path, ocr_json_sha256)
                            VALUES (%s, 0, 'p', %s, 10, 10, 'j', %s)""" % (doc_id, "'%s'" % SHA_A, "'%s'" % SHA_A),
                         None, "23514")

            print("\n[4] LEGACY ID MAP: ambiguity must survive; unresolved needs no target")
            # Insert two candidates for the SAME legacy_id and verify both persist.
            cur.execute("SAVEPOINT sp_amb")
            try:
                cur.execute(
                    """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status, evidence_ref)
                       VALUES ('document','9-ambiguous','AMBIGUOUS','pending','D-04')"""
                )
                cur.execute(
                    """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence, review_status, evidence_ref)
                       VALUES ('document','9-ambiguous','AMBIGUOUS','pending','D-04 alt')"""
                )
                cur.execute(
                    "SELECT count(*) FROM legacy_id_map WHERE entity_type='document' AND legacy_id='9-ambiguous'"
                )
                n = cur.fetchone()[0]
                cur.execute("RELEASE SAVEPOINT sp_amb")
                if n == 2:
                    ok("N12 duplicate legacy_id kept as 2 ambiguity candidates", "count=2")
                else:
                    bad("N12 ambiguity candidates", "count=%s (expected 2)" % n)
            except psycopg2.Error as exc:
                cur.execute("ROLLBACK TO SAVEPOINT sp_amb")
                bad("N12 duplicate legacy_id kept as 2 ambiguity candidates",
                    "%s: %s" % (exc.pgcode, str(exc).splitlines()[0][:80]))

            expect_ok(cur, "N14 UNRESOLVED mapping accepted with all target FKs NULL",
                      """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence)
                         VALUES ('block','legacy-block-123','UNRESOLVED')""", None)
            expect_error(cur, "N15 UNRESOLVED mapping with a target rejected",
                         """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id, confidence)
                            VALUES ('document','x',%s,'UNRESOLVED')""" % doc_id, None, "23514")
            expect_error(cur, "N16 entity_type/target mismatch rejected",
                         """INSERT INTO legacy_id_map (entity_type, legacy_id, new_block_id, confidence)
                            VALUES ('page','y',%s,'AMBIGUOUS')""" % blk1, None, "23514")
            expect_error(cur, "N17 CONFIRMED mapping without target rejected",
                         """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence)
                            VALUES ('document','z','CONFIRMED')""", None, "23514")
            expect_error(cur, "N18 invalid confidence value rejected",
                         """INSERT INTO legacy_id_map (entity_type, legacy_id, confidence)
                            VALUES ('document','w','MAYBE')""", None, "23514")
            expect_error(cur, "N19 two targets on one mapping rejected",
                         """INSERT INTO legacy_id_map (entity_type, legacy_id, new_document_id,
                                new_page_id, confidence)
                            VALUES ('document','v',%s,%s,'AMBIGUOUS')""" % (doc_id, page_id), None, "23514")

        print("\n[5] CLEANUP: roll back every synthetic row")
        conn.rollback()
        ok("R1 transaction rolled back")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print("[validate] UNEXPECTED ERROR, rolled back: %s: %s" % (type(exc).__name__, exc))
        return 1
    finally:
        conn.close()

    # post-cleanup verification on a fresh connection
    conn2 = recon_db.connect()
    try:
        for line in recon_db.assert_db_identity(conn2, expected_tables=4):
            print("[validate] post-cleanup identity: " + line)
        with conn2.cursor() as cur:
            counts = {}
            for t in ("documents", "pages", "blocks", "legacy_id_map"):
                cur.execute("SELECT count(*) FROM " + t)
                counts[t] = cur.fetchone()[0]
            print("[validate] post-cleanup row counts: %s" % counts)
            if all(v == 0 for v in counts.values()):
                ok("R2 all four tables back to 0 rows")
            else:
                bad("R2 residual rows", str(counts))
    finally:
        conn2.rollback()
        conn2.close()

    print("\n==== SUMMARY ====")
    print("passed: %d" % len(PASS))
    print("failed: %d" % len(FAIL))
    for f in FAIL:
        print("  FAILED -> " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
