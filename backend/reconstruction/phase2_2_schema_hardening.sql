-- =============================================================================
-- Stage 8 Phase 2.2: schema hardening migration
--
-- Target: ocr_platform_recon (host 127.0.0.1:55432, container recon_postgres,
--         volume eic_reconstruction_pgdata, PostgreSQL 15.19)
--
-- THIS FILE CONTAINS DDL ONLY. It MUST be executed inside a single transaction
-- (psql --single-transaction, or via phase2_2_apply_hardening.py which owns the
-- transaction and rolls back on any failure). PostgreSQL DDL is transactional,
-- so a mid-file failure leaves the previous structure untouched.
--
-- What this migration changes
-- ---------------------------
-- FIX A  legacy_id_map target FKs: ON DELETE SET NULL -> ON DELETE RESTRICT
--        Deleting a document/page/block that is still referenced by a mapping
--        is now refused by the FOREIGN KEY itself (SQLSTATE 23503), instead of
--        being indirectly refused by a CHECK constraint (23514). No mapping
--        row is rewritten, cleared or downgraded to UNRESOLVED as a side effect.
--
-- FIX B  review_status / mapping lineage:
--        * new column superseded_by_id (NULL for every non-superseded row);
--        * review_status='superseded'  <=>  superseded_by_id IS NOT NULL;
--        * a row may not supersede itself;
--        * the successor must be a mapping row with the SAME entity_type and
--          the SAME legacy_id (enforced by a composite self-referencing FK, not
--          by a plain id-only FK);
--        * the successor cannot be deleted while it is still pointed at
--          (ON DELETE RESTRICT);
--        * review_status='confirmed' now requires an actual mapping target.
--        Note: confidence and review_status stay orthogonal. CONFIRMED+pending,
--        CONFIRMED+rejected and AMBIGUOUS+confirmed(with a target) remain legal.
--
-- FIX C  bbox: NO schema change. Real-data evidence (51,454 blocks, 611 with
--        x2 == declared width, 60 with y2 == declared height, 226 distinct
--        declared sizes) shows a naive range CHECK would reject genuine
--        material. bbox boundary rules live in the import-layer contract
--        (phase2_2_bbox_contract.py) instead, so nothing is silently loosened
--        here and nothing genuine is refused.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Preconditions. Everything below aborts the whole transaction rather than
-- proceeding on an unexpected structure. This also makes a double apply fail
-- loudly instead of silently re-creating already-migrated constraints.
-- ---------------------------------------------------------------------------
DO $guard$
DECLARE
    missing TEXT;
    non_empty TEXT;
    fk_def TEXT;
BEGIN
    SELECT string_agg(t, ', ') INTO missing
    FROM unnest(ARRAY['documents', 'pages', 'blocks', 'legacy_id_map']) AS t
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = t
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'phase2_2 precondition failed: missing tables: %', missing;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public'
                 AND table_name = 'legacy_id_map'
                 AND column_name = 'superseded_by_id') THEN
        RAISE EXCEPTION 'phase2_2 precondition failed: legacy_id_map.superseded_by_id already exists (migration already applied)';
    END IF;

    -- The hardening assumes the business tables are empty: it never backfills or
    -- rewrites mapping rows, and non-empty tables would mean the structure is
    -- being changed underneath real data. Reached only when all four exist.
    non_empty := NULL;
    IF EXISTS (SELECT 1 FROM documents) THEN
        non_empty := 'documents';
    END IF;
    IF EXISTS (SELECT 1 FROM pages) THEN
        non_empty := concat_ws(', ', non_empty, 'pages');
    END IF;
    IF EXISTS (SELECT 1 FROM blocks) THEN
        non_empty := concat_ws(', ', non_empty, 'blocks');
    END IF;
    IF EXISTS (SELECT 1 FROM legacy_id_map) THEN
        non_empty := concat_ws(', ', non_empty, 'legacy_id_map');
    END IF;
    IF non_empty IS NOT NULL THEN
        RAISE EXCEPTION 'phase2_2 precondition failed: tables not empty: %', non_empty;
    END IF;

    -- Only migrate the known Phase 2 definition (SET NULL). If the FK is
    -- already RESTRICT, or was changed by someone else, stop.
    SELECT string_agg(pg_get_constraintdef(oid), ' | ' ORDER BY conname) INTO fk_def
    FROM pg_constraint
    WHERE connamespace = 'public'::regnamespace
      AND conrelid = 'public.legacy_id_map'::regclass
      AND conname IN ('legacy_id_map_new_document_id_fkey',
                      'legacy_id_map_new_page_id_fkey',
                      'legacy_id_map_new_block_id_fkey');
    IF fk_def IS NULL OR fk_def NOT LIKE '%ON DELETE SET NULL%' THEN
        RAISE EXCEPTION 'phase2_2 precondition failed: unexpected target FK definitions: %', coalesce(fk_def, '<none>');
    END IF;
    IF fk_def LIKE '%RESTRICT%' THEN
        RAISE EXCEPTION 'phase2_2 precondition failed: a target FK is already RESTRICT: %', fk_def;
    END IF;
END
$guard$;

-- ---------------------------------------------------------------------------
-- FIX A: target FKs now protect themselves.
-- Column set, index set and the existing CHECK constraints are unchanged.
-- ---------------------------------------------------------------------------
ALTER TABLE legacy_id_map DROP CONSTRAINT legacy_id_map_new_document_id_fkey;
ALTER TABLE legacy_id_map ADD CONSTRAINT legacy_id_map_new_document_id_fkey
    FOREIGN KEY (new_document_id) REFERENCES documents (id) ON DELETE RESTRICT;

ALTER TABLE legacy_id_map DROP CONSTRAINT legacy_id_map_new_page_id_fkey;
ALTER TABLE legacy_id_map ADD CONSTRAINT legacy_id_map_new_page_id_fkey
    FOREIGN KEY (new_page_id) REFERENCES pages (id) ON DELETE RESTRICT;

ALTER TABLE legacy_id_map DROP CONSTRAINT legacy_id_map_new_block_id_fkey;
ALTER TABLE legacy_id_map ADD CONSTRAINT legacy_id_map_new_block_id_fkey
    FOREIGN KEY (new_block_id) REFERENCES blocks (id) ON DELETE RESTRICT;

-- ---------------------------------------------------------------------------
-- FIX B: supersession pointer + lineage integrity.
-- ---------------------------------------------------------------------------
ALTER TABLE legacy_id_map ADD COLUMN superseded_by_id BIGINT;

-- Needed as the referenced side of the composite FK below. Redundant with the
-- primary key in terms of uniqueness, but PostgreSQL requires a unique
-- constraint on exactly the referenced column list.
ALTER TABLE legacy_id_map
    ADD CONSTRAINT uq_lim_id_entity_legacy UNIQUE (id, entity_type, legacy_id);

-- Composite self-FK: the successor must be a real row with the same
-- entity_type AND the same legacy_id. A plain (superseded_by_id -> id) FK would
-- only prove "some mapping exists", which is not the business relation we need.
-- MATCH SIMPLE means the constraint is skipped while superseded_by_id is NULL.
ALTER TABLE legacy_id_map
    ADD CONSTRAINT legacy_id_map_superseded_by_fkey
    FOREIGN KEY (superseded_by_id, entity_type, legacy_id)
    REFERENCES legacy_id_map (id, entity_type, legacy_id)
    ON DELETE RESTRICT;

-- superseded <=> pointer present. Non-superseded rows must not carry a pointer,
-- and a superseded row must not be left without one.
ALTER TABLE legacy_id_map
    ADD CONSTRAINT ck_lim_superseded_pointer
    CHECK ((review_status = 'superseded') = (superseded_by_id IS NOT NULL));

ALTER TABLE legacy_id_map
    ADD CONSTRAINT ck_lim_superseded_not_self
    CHECK (superseded_by_id IS NULL OR superseded_by_id <> id);

-- review_status describes the human lifecycle, confidence describes the
-- strength of the identity evidence: they stay independent. The only coupling
-- enforced here is that a human "confirmed" verdict must actually point at a
-- mapped record. AMBIGUOUS+confirmed (with a target), CONFIRMED+pending and
-- CONFIRMED+rejected stay legal on purpose.
ALTER TABLE legacy_id_map
    ADD CONSTRAINT ck_lim_confirmed_review_has_target
    CHECK (review_status <> 'confirmed'
           OR num_nonnulls(new_document_id, new_page_id, new_block_id) = 1);

CREATE INDEX ix_lim_superseded_by_id ON legacy_id_map (superseded_by_id);

COMMENT ON COLUMN legacy_id_map.superseded_by_id IS
    'Successor mapping row (same entity_type + legacy_id). Required iff review_status = superseded.';
