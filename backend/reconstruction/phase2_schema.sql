-- =============================================================================
-- Stage 8 Phase 2 (+ 2.2 hardening): minimal reconstruction schema
-- Target: ocr_platform_recon (host 127.0.0.1:55432) behind container recon_postgres
--
-- This file is the CANONICAL "rebuild from empty" definition. It is kept in sync
-- with the live database: Phase 2.2 changes were first shipped as the migration
-- phase2_2_schema_hardening.sql, then folded back in here. An empty database
-- built from this file must be structurally identical to the migrated one
-- (verified by phase2_2_canonical_check.py inside a rolled-back schema).
--
-- Phase 2.2 hardening summary (see the migration file for the full rationale):
--   * legacy_id_map target FKs are ON DELETE RESTRICT, so deleting a mapped
--     document/page/block is refused by the FOREIGN KEY (23503) instead of being
--     indirectly refused by a CHECK constraint (23514).
--   * legacy_id_map.superseded_by_id + composite self-FK (entity_type,
--     legacy_id) enforce mapping lineage; superseded <=> pointer present; no
--     self-reference; the successor cannot be deleted while still referenced.
--   * review_status='confirmed' requires an actual mapping target.
--   * blocks.bbox keeps structure-only CHECKs on purpose: real data contains 611
--     blocks with x2 == declared width and 60 with y2 == declared height, so a
--     naive range CHECK would reject genuine material. Boundary rules live in
--     the import-layer contract (phase2_2_bbox_contract.py).
--
-- Scope: documents / pages / blocks / legacy_id_map ONLY.
-- No knowledge-graph, claim, evidence, NLI or agent tables.
-- No real archive data is imported in this phase.
--
-- Notes on deliberate deviations from backend/app/models/*.py:
--   * documents.filename/file_type/status/error_message/full_markdown are not
--     created: they are not recoverable from source files in a trustworthy way.
--   * pages.width/height -> image_width_px/image_height_px (explicit unit).
--   * pages.markdown_content/pruned_result_path -> content_md_path/ocr_json_path
--     (artifacts are referenced by path + hash rather than duplicated inline).
--   * blocks.ocr_text_corrected / ocr_correction_source_text / modern_english /
--     translation_zh / review_status are NOT created: none of them can be
--     reconstructed from the surviving files, so creating NULL-only columns
--     would invite accidental fabrication.
--   * source_pdf_sha256 is intentionally NOT unique: the same PDF may legitimately
--     be uploaded more than once (see discrepancy D-04, doc 9 vs doc 6).
-- =============================================================================

CREATE TABLE documents (
    id                            BIGSERIAL PRIMARY KEY,
    source_dir_id                 INTEGER,
    source_pdf_path               TEXT        NOT NULL,
    source_pdf_sha256             CHAR(64)    NOT NULL,
    source_pdf_size_bytes         BIGINT      NOT NULL,
    page_count                    INTEGER     NOT NULL,
    original_filename             TEXT,
    original_filename_provenance  VARCHAR(32) NOT NULL DEFAULT 'unknown',
    data_origin                   VARCHAR(32) NOT NULL DEFAULT 'reconstructed_ocr',
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_documents_source_dir_id UNIQUE (source_dir_id),
    CONSTRAINT ck_documents_pdf_path_nonempty CHECK (length(btrim(source_pdf_path)) > 0),
    CONSTRAINT ck_documents_pdf_sha256_format CHECK (source_pdf_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_documents_pdf_size_nonnegative CHECK (source_pdf_size_bytes >= 0),
    CONSTRAINT ck_documents_page_count_positive CHECK (page_count > 0),
    CONSTRAINT ck_documents_original_filename_provenance
        CHECK (original_filename_provenance IN ('unknown', 'docx_core_title', 'historical_db', 'manual_review')),
    -- Anti-fabrication rule: a NULL title must be declared unknown, and a title
    -- must always carry a provenance other than 'unknown'.
    CONSTRAINT ck_documents_original_filename_rule CHECK (
        (original_filename IS NULL     AND original_filename_provenance = 'unknown')
        OR
        (original_filename IS NOT NULL AND original_filename_provenance <> 'unknown')
    ),
    CONSTRAINT ck_documents_data_origin CHECK (data_origin IN ('reconstructed_ocr'))
);

CREATE INDEX ix_documents_source_pdf_sha256 ON documents (source_pdf_sha256);

CREATE TABLE pages (
    id               BIGSERIAL PRIMARY KEY,
    document_id      BIGINT      NOT NULL REFERENCES documents (id) ON DELETE RESTRICT,
    page_number      INTEGER     NOT NULL,
    image_path       TEXT        NOT NULL,
    image_sha256     CHAR(64)    NOT NULL,
    image_width_px   INTEGER     NOT NULL,
    image_height_px  INTEGER     NOT NULL,
    ocr_json_path    TEXT        NOT NULL,
    ocr_json_sha256  CHAR(64)    NOT NULL,
    content_md_path  TEXT,
    data_origin      VARCHAR(32) NOT NULL DEFAULT 'reconstructed_ocr',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_pages_document_page_number UNIQUE (document_id, page_number),
    CONSTRAINT ck_pages_page_number_positive CHECK (page_number >= 1),
    CONSTRAINT ck_pages_image_path_nonempty CHECK (length(btrim(image_path)) > 0),
    CONSTRAINT ck_pages_ocr_json_path_nonempty CHECK (length(btrim(ocr_json_path)) > 0),
    CONSTRAINT ck_pages_image_sha256_format CHECK (image_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_pages_ocr_json_sha256_format CHECK (ocr_json_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_pages_dimensions_positive CHECK (image_width_px > 0 AND image_height_px > 0),
    CONSTRAINT ck_pages_data_origin CHECK (data_origin IN ('reconstructed_ocr'))
);

CREATE INDEX ix_pages_document_id ON pages (document_id);

CREATE TABLE blocks (
    id                     BIGSERIAL PRIMARY KEY,
    page_id                BIGINT           NOT NULL REFERENCES pages (id) ON DELETE RESTRICT,
    block_index            INTEGER          NOT NULL,
    source_block_id        INTEGER,
    block_label            VARCHAR(100),
    ocr_text_raw           TEXT,
    bbox                   JSONB            NOT NULL,
    bbox_coordinate_space  VARCHAR(32)      NOT NULL DEFAULT 'ocr_engine',
    bbox_space_width       INTEGER          NOT NULL,
    bbox_space_height      INTEGER          NOT NULL,
    confidence             DOUBLE PRECISION,
    data_origin            VARCHAR(32)      NOT NULL DEFAULT 'reconstructed_ocr',
    created_at             TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ      NOT NULL DEFAULT now(),
    CONSTRAINT uq_blocks_page_block_index UNIQUE (page_id, block_index),
    CONSTRAINT ck_blocks_block_index_nonnegative CHECK (block_index >= 0),
    CONSTRAINT ck_blocks_source_block_id_nonnegative CHECK (source_block_id IS NULL OR source_block_id >= 0),
    CONSTRAINT ck_blocks_bbox_space CHECK (bbox_coordinate_space IN ('ocr_engine', 'page_image_px')),
    CONSTRAINT ck_blocks_bbox_space_dims_positive CHECK (bbox_space_width > 0 AND bbox_space_height > 0),
    CONSTRAINT ck_blocks_confidence_range CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    -- Structural + geometric validation of the raw OCR bounding box.
    -- CASE guarantees short-circuit evaluation so casts are only attempted on numbers.
    CONSTRAINT ck_blocks_bbox_structure CHECK (
        CASE
            WHEN jsonb_typeof(bbox) <> 'array'                 THEN false
            WHEN jsonb_array_length(bbox) <> 4                  THEN false
            WHEN jsonb_typeof(bbox -> 0) <> 'number'            THEN false
            WHEN jsonb_typeof(bbox -> 1) <> 'number'            THEN false
            WHEN jsonb_typeof(bbox -> 2) <> 'number'            THEN false
            WHEN jsonb_typeof(bbox -> 3) <> 'number'            THEN false
            ELSE (bbox ->> 0)::numeric <= (bbox ->> 2)::numeric
             AND (bbox ->> 1)::numeric <= (bbox ->> 3)::numeric
        END
    ),
    CONSTRAINT ck_blocks_data_origin CHECK (data_origin IN ('reconstructed_ocr'))
);

CREATE INDEX ix_blocks_page_id ON blocks (page_id);

-- Candidate legacy-ID mappings. Multiple candidates per legacy_id are ALLOWED on
-- purpose: real ambiguity must stay visible instead of being collapsed by a
-- unique constraint. Exactly one target FK may be set, and its type must match
-- entity_type. This is the polymorphic-integrity workaround (separate nullable
-- FKs + CHECK) required because a single polymorphic column cannot carry FKs.
CREATE TABLE legacy_id_map (
    id               BIGSERIAL PRIMARY KEY,
    legacy_system    VARCHAR(64) NOT NULL DEFAULT 'eic_canton_historical_db',
    entity_type      VARCHAR(16) NOT NULL,
    legacy_id        TEXT        NOT NULL,
    new_document_id  BIGINT      REFERENCES documents (id) ON DELETE RESTRICT,
    new_page_id      BIGINT      REFERENCES pages (id)     ON DELETE RESTRICT,
    new_block_id     BIGINT      REFERENCES blocks (id)    ON DELETE RESTRICT,
    confidence       VARCHAR(20) NOT NULL,
    review_status    VARCHAR(20) NOT NULL DEFAULT 'pending',
    evidence_ref     TEXT,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Phase 2.2. Physically last on purpose: the live database obtained this
    -- column through ALTER TABLE ... ADD COLUMN (phase2_2_schema_hardening.sql),
    -- and keeping the ordinal identical makes "rebuild from empty" produce a
    -- structure that matches the migrated database exactly.
    superseded_by_id BIGINT,
    CONSTRAINT ck_lim_entity_type CHECK (entity_type IN ('document', 'page', 'block')),
    CONSTRAINT ck_lim_legacy_id_nonempty CHECK (length(btrim(legacy_id)) > 0),
    CONSTRAINT ck_lim_legacy_system_nonempty CHECK (length(btrim(legacy_system)) > 0),
    CONSTRAINT ck_lim_confidence CHECK (confidence IN ('CONFIRMED', 'HIGH_CONFIDENCE', 'AMBIGUOUS', 'UNRESOLVED')),
    CONSTRAINT ck_lim_review_status CHECK (review_status IN ('pending', 'confirmed', 'rejected', 'superseded')),
    CONSTRAINT ck_lim_single_target CHECK (num_nonnulls(new_document_id, new_page_id, new_block_id) <= 1),
    CONSTRAINT ck_lim_target_type_matches CHECK (
        CASE entity_type
            WHEN 'document' THEN new_page_id IS NULL AND new_block_id IS NULL
            WHEN 'page'     THEN new_document_id IS NULL AND new_block_id IS NULL
            WHEN 'block'    THEN new_document_id IS NULL AND new_page_id IS NULL
            ELSE false
        END
    ),
    CONSTRAINT ck_lim_unresolved_has_no_target CHECK (
        confidence <> 'UNRESOLVED' OR num_nonnulls(new_document_id, new_page_id, new_block_id) = 0
    ),
    CONSTRAINT ck_lim_resolved_has_target CHECK (
        confidence NOT IN ('CONFIRMED', 'HIGH_CONFIDENCE')
        OR num_nonnulls(new_document_id, new_page_id, new_block_id) = 1
    ),
    -- Phase 2.2: confidence (evidence strength) and review_status (human
    -- lifecycle) stay orthogonal. Only the human verdict is coupled to a target.
    CONSTRAINT ck_lim_confirmed_review_has_target CHECK (
        review_status <> 'confirmed'
        OR num_nonnulls(new_document_id, new_page_id, new_block_id) = 1
    ),
    -- Phase 2.2: supersession pointer lifecycle.
    CONSTRAINT ck_lim_superseded_pointer CHECK (
        (review_status = 'superseded') = (superseded_by_id IS NOT NULL)
    ),
    CONSTRAINT ck_lim_superseded_not_self CHECK (
        superseded_by_id IS NULL OR superseded_by_id <> id
    ),
    CONSTRAINT uq_lim_id_entity_legacy UNIQUE (id, entity_type, legacy_id)
);

-- Phase 2.2: the successor must be a mapping row with the SAME entity_type and
-- the SAME legacy_id. A plain (superseded_by_id -> id) FK would only prove that
-- some mapping row exists, which is not the business relation we need. MATCH
-- SIMPLE skips the check while superseded_by_id is NULL.
ALTER TABLE legacy_id_map
    ADD CONSTRAINT legacy_id_map_superseded_by_fkey
    FOREIGN KEY (superseded_by_id, entity_type, legacy_id)
    REFERENCES legacy_id_map (id, entity_type, legacy_id)
    ON DELETE RESTRICT;

CREATE INDEX ix_lim_lookup ON legacy_id_map (legacy_system, entity_type, legacy_id);
CREATE INDEX ix_lim_new_document_id ON legacy_id_map (new_document_id);
CREATE INDEX ix_lim_new_page_id ON legacy_id_map (new_page_id);
CREATE INDEX ix_lim_new_block_id ON legacy_id_map (new_block_id);
CREATE INDEX ix_lim_superseded_by_id ON legacy_id_map (superseded_by_id);

COMMENT ON TABLE documents IS 'Stage 8 reconstruction: PDF-level provenance. source_dir_id is a filesystem directory snapshot, NOT a proven historical documents.id.';
COMMENT ON TABLE pages IS 'Stage 8 reconstruction: page-level provenance (image + OCR JSON paths and hashes).';
COMMENT ON TABLE blocks IS 'Stage 8 reconstruction: raw OCR blocks only. Corrected/modern/translated text is NOT reconstructible and therefore not modelled here.';
COMMENT ON TABLE legacy_id_map IS 'Stage 8 reconstruction: candidate legacy-ID mappings. Presence of this table does not mean historical IDs were recovered.';
COMMENT ON COLUMN blocks.bbox IS 'Raw bbox exactly as found in pruned_result.json (do NOT rescale without a verified transform).';
COMMENT ON COLUMN blocks.bbox_coordinate_space IS 'Coordinate space of bbox: ocr_engine (Paddle internal) or page_image_px (page image pixels).';
COMMENT ON COLUMN legacy_id_map.superseded_by_id IS 'Successor mapping row (same entity_type + legacy_id). Required iff review_status = superseded.';
