--
-- PostgreSQL database dump
--


-- Dumped from database version 15.19
-- Dumped by pg_dump version 15.19

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: documentstatus; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.documentstatus AS ENUM (
    'PENDING',
    'PROCESSING',
    'OCR_COMPLETED',
    'POLISHING',
    'COMPLETED',
    'FAILED'
);


ALTER TYPE public.documentstatus OWNER TO postgres;

--
-- Name: documenttype; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.documenttype AS ENUM (
    'PDF',
    'IMAGE'
);


ALTER TYPE public.documenttype OWNER TO postgres;

--
-- Name: exportformat; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.exportformat AS ENUM (
    'JSON',
    'MARKDOWN',
    'TXT',
    'DOCX',
    'PDF'
);


ALTER TYPE public.exportformat OWNER TO postgres;

--
-- Name: exportstatus; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.exportstatus AS ENUM (
    'PENDING',
    'PROCESSING',
    'COMPLETED',
    'FAILED'
);


ALTER TYPE public.exportstatus OWNER TO postgres;

--
-- Name: reviewstatus; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.reviewstatus AS ENUM (
    'PENDING',
    'APPROVED',
    'REJECTED',
    'NEEDS_REVIEW'
);


ALTER TYPE public.reviewstatus OWNER TO postgres;

--
-- Name: taskstatus; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.taskstatus AS ENUM (
    'PENDING',
    'RUNNING',
    'COMPLETED',
    'FAILED',
    'CANCELLED'
);


ALTER TYPE public.taskstatus OWNER TO postgres;

--
-- Name: tasktype; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.tasktype AS ENUM (
    'OCR',
    'OCR_CORRECTION',
    'MODERN_ENGLISH',
    'TRANSLATION_ZH',
    'EXPORT'
);


ALTER TYPE public.tasktype OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: blocks; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.blocks (
    id integer NOT NULL,
    page_id integer NOT NULL,
    block_index integer NOT NULL,
    block_label character varying(100),
    bbox json NOT NULL,
    confidence double precision,
    ocr_text_raw text,
    ocr_text_corrected text,
    ocr_correction_source_text text,
    modern_english text,
    translation_zh text,
    review_status public.reviewstatus,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


ALTER TABLE public.blocks OWNER TO postgres;

--
-- Name: blocks_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.blocks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.blocks_id_seq OWNER TO postgres;

--
-- Name: blocks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.blocks_id_seq OWNED BY public.blocks.id;


--
-- Name: documents; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.documents (
    id integer NOT NULL,
    filename character varying(255) NOT NULL,
    original_filename character varying(255) NOT NULL,
    file_path character varying(512) NOT NULL,
    file_type public.documenttype NOT NULL,
    file_size integer NOT NULL,
    page_count integer,
    full_markdown text,
    status public.documentstatus,
    error_message text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


ALTER TABLE public.documents OWNER TO postgres;

--
-- Name: documents_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.documents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.documents_id_seq OWNER TO postgres;

--
-- Name: documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.documents_id_seq OWNED BY public.documents.id;


--
-- Name: entity_operations_audit; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.entity_operations_audit (
    id integer NOT NULL,
    operation_type character varying(50) NOT NULL,
    primary_person_id integer NOT NULL,
    secondary_person_id integer,
    snapshot_details json NOT NULL,
    reason text,
    operator character varying(100) NOT NULL,
    is_reverted boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);


ALTER TABLE public.entity_operations_audit OWNER TO postgres;

--
-- Name: entity_operations_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.entity_operations_audit_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.entity_operations_audit_id_seq OWNER TO postgres;

--
-- Name: entity_operations_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.entity_operations_audit_id_seq OWNED BY public.entity_operations_audit.id;


--
-- Name: exports; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.exports (
    id integer NOT NULL,
    document_id integer NOT NULL,
    format public.exportformat NOT NULL,
    status public.exportstatus,
    file_path character varying(512),
    file_size integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


ALTER TABLE public.exports OWNER TO postgres;

--
-- Name: exports_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.exports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.exports_id_seq OWNER TO postgres;

--
-- Name: exports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.exports_id_seq OWNED BY public.exports.id;


--
-- Name: external_authorities; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.external_authorities (
    id integer NOT NULL,
    person_id integer NOT NULL,
    authority_source character varying(100) NOT NULL,
    external_id character varying(255),
    external_url text,
    record_title character varying(500),
    historical_notes text,
    match_confidence double precision,
    review_status character varying(30) NOT NULL,
    claim_status character varying(30) NOT NULL,
    accessed_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    CONSTRAINT chk_authority_confidence_range CHECK (((match_confidence IS NULL) OR ((match_confidence >= (0.0)::double precision) AND (match_confidence <= (1.0)::double precision))))
);


ALTER TABLE public.external_authorities OWNER TO postgres;

--
-- Name: external_authorities_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.external_authorities_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.external_authorities_id_seq OWNER TO postgres;

--
-- Name: external_authorities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.external_authorities_id_seq OWNED BY public.external_authorities.id;


--
-- Name: pages; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.pages (
    id integer NOT NULL,
    document_id integer NOT NULL,
    page_number integer NOT NULL,
    image_path character varying(512),
    visualization_path character varying(512),
    markdown_content text,
    pruned_result_path character varying(512),
    width integer,
    height integer,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


ALTER TABLE public.pages OWNER TO postgres;

--
-- Name: pages_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.pages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.pages_id_seq OWNER TO postgres;

--
-- Name: pages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.pages_id_seq OWNED BY public.pages.id;


--
-- Name: person_mentions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.person_mentions (
    id integer NOT NULL,
    block_id integer NOT NULL,
    person_id integer,
    raw_text text NOT NULL,
    normalized_text text,
    mention_type character varying(50) NOT NULL,
    temporal_date date,
    temporal_year smallint,
    spatial_location character varying(255),
    confidence double precision,
    resolution_status character varying(30) NOT NULL,
    review_status character varying(30) NOT NULL,
    candidate_person_ids json,
    evidence_text text,
    start_offset integer,
    end_offset integer,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    CONSTRAINT chk_mention_confidence_range CHECK (((confidence IS NULL) OR ((confidence >= (0.0)::double precision) AND (confidence <= (1.0)::double precision)))),
    CONSTRAINT chk_mention_raw_text_nonempty CHECK ((length(TRIM(BOTH FROM raw_text)) > 0))
);


ALTER TABLE public.person_mentions OWNER TO postgres;

--
-- Name: person_mentions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.person_mentions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.person_mentions_id_seq OWNER TO postgres;

--
-- Name: person_mentions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.person_mentions_id_seq OWNED BY public.person_mentions.id;


--
-- Name: persons; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.persons (
    id integer NOT NULL,
    canonical_name_en character varying(255),
    canonical_name_zh character varying(255),
    display_name character varying(255) NOT NULL,
    aliases json,
    primary_role character varying(100),
    organization character varying(255),
    birth_year smallint,
    death_year smallint,
    active_start date,
    active_end date,
    base_location character varying(255),
    biographical_notes text,
    identity_status character varying(30) NOT NULL,
    review_status character varying(30) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    CONSTRAINT chk_person_active_dates CHECK (((active_start IS NULL) OR (active_end IS NULL) OR (active_start <= active_end))),
    CONSTRAINT chk_person_birth_death CHECK (((birth_year IS NULL) OR (death_year IS NULL) OR (birth_year <= death_year))),
    CONSTRAINT chk_person_display_name_nonempty CHECK ((length(TRIM(BOTH FROM display_name)) > 0))
);


ALTER TABLE public.persons OWNER TO postgres;

--
-- Name: persons_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.persons_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.persons_id_seq OWNER TO postgres;

--
-- Name: persons_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.persons_id_seq OWNED BY public.persons.id;


--
-- Name: relationship_evidences; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.relationship_evidences (
    id integer NOT NULL,
    relationship_id integer NOT NULL,
    person_mention_id integer,
    block_id integer NOT NULL,
    evidence_role character varying(30) NOT NULL,
    quote_text text,
    created_at timestamp without time zone NOT NULL
);


ALTER TABLE public.relationship_evidences OWNER TO postgres;

--
-- Name: relationship_evidences_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.relationship_evidences_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.relationship_evidences_id_seq OWNER TO postgres;

--
-- Name: relationship_evidences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.relationship_evidences_id_seq OWNED BY public.relationship_evidences.id;


--
-- Name: relationships; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.relationships (
    id integer NOT NULL,
    source_person_id integer NOT NULL,
    target_person_id integer NOT NULL,
    relation_type character varying(80) NOT NULL,
    relation_label character varying(255),
    directionality character varying(20) NOT NULL,
    weight double precision,
    valid_from date,
    valid_to date,
    time_description character varying(255),
    spatial_location character varying(255),
    evidence_summary text,
    confidence double precision,
    review_status character varying(30) NOT NULL,
    claim_status character varying(30) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    CONSTRAINT chk_relationship_confidence_range CHECK (((confidence IS NULL) OR ((confidence >= (0.0)::double precision) AND (confidence <= (1.0)::double precision)))),
    CONSTRAINT chk_relationship_dates CHECK (((valid_from IS NULL) OR (valid_to IS NULL) OR (valid_from <= valid_to))),
    CONSTRAINT chk_relationship_weight_positive CHECK (((weight IS NULL) OR (weight >= (0.0)::double precision)))
);


ALTER TABLE public.relationships OWNER TO postgres;

--
-- Name: relationships_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.relationships_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.relationships_id_seq OWNER TO postgres;

--
-- Name: relationships_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.relationships_id_seq OWNED BY public.relationships.id;


--
-- Name: sna_graph_metrics; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.sna_graph_metrics (
    id integer NOT NULL,
    person_id integer NOT NULL,
    run_id character varying(64) NOT NULL,
    snapshot_at timestamp without time zone NOT NULL,
    period_start date,
    period_end date,
    graph_scope character varying(100) NOT NULL,
    degree_centrality double precision,
    betweenness_centrality double precision,
    closeness_centrality double precision,
    pagerank double precision,
    community_id character varying(100),
    algorithm_version character varying(100) NOT NULL,
    parameters json,
    created_at timestamp without time zone NOT NULL
);


ALTER TABLE public.sna_graph_metrics OWNER TO postgres;

--
-- Name: sna_graph_metrics_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.sna_graph_metrics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.sna_graph_metrics_id_seq OWNER TO postgres;

--
-- Name: sna_graph_metrics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.sna_graph_metrics_id_seq OWNED BY public.sna_graph_metrics.id;


--
-- Name: tasks; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tasks (
    id integer NOT NULL,
    celery_task_id character varying(255),
    document_id integer,
    page_id integer,
    block_id integer,
    task_type public.tasktype NOT NULL,
    status public.taskstatus,
    progress integer,
    result json,
    error_message text,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


ALTER TABLE public.tasks OWNER TO postgres;

--
-- Name: tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tasks_id_seq OWNER TO postgres;

--
-- Name: tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.tasks_id_seq OWNED BY public.tasks.id;


--
-- Name: blocks id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blocks ALTER COLUMN id SET DEFAULT nextval('public.blocks_id_seq'::regclass);


--
-- Name: documents id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.documents ALTER COLUMN id SET DEFAULT nextval('public.documents_id_seq'::regclass);


--
-- Name: entity_operations_audit id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.entity_operations_audit ALTER COLUMN id SET DEFAULT nextval('public.entity_operations_audit_id_seq'::regclass);


--
-- Name: exports id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.exports ALTER COLUMN id SET DEFAULT nextval('public.exports_id_seq'::regclass);


--
-- Name: external_authorities id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.external_authorities ALTER COLUMN id SET DEFAULT nextval('public.external_authorities_id_seq'::regclass);


--
-- Name: pages id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.pages ALTER COLUMN id SET DEFAULT nextval('public.pages_id_seq'::regclass);


--
-- Name: person_mentions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.person_mentions ALTER COLUMN id SET DEFAULT nextval('public.person_mentions_id_seq'::regclass);


--
-- Name: persons id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.persons ALTER COLUMN id SET DEFAULT nextval('public.persons_id_seq'::regclass);


--
-- Name: relationship_evidences id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.relationship_evidences ALTER COLUMN id SET DEFAULT nextval('public.relationship_evidences_id_seq'::regclass);


--
-- Name: relationships id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.relationships ALTER COLUMN id SET DEFAULT nextval('public.relationships_id_seq'::regclass);


--
-- Name: sna_graph_metrics id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sna_graph_metrics ALTER COLUMN id SET DEFAULT nextval('public.sna_graph_metrics_id_seq'::regclass);


--
-- Name: tasks id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tasks ALTER COLUMN id SET DEFAULT nextval('public.tasks_id_seq'::regclass);


--
-- Name: blocks blocks_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blocks
    ADD CONSTRAINT blocks_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: entity_operations_audit entity_operations_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.entity_operations_audit
    ADD CONSTRAINT entity_operations_audit_pkey PRIMARY KEY (id);


--
-- Name: exports exports_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.exports
    ADD CONSTRAINT exports_pkey PRIMARY KEY (id);


--
-- Name: external_authorities external_authorities_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.external_authorities
    ADD CONSTRAINT external_authorities_pkey PRIMARY KEY (id);


--
-- Name: pages pages_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_pkey PRIMARY KEY (id);


--
-- Name: person_mentions person_mentions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.person_mentions
    ADD CONSTRAINT person_mentions_pkey PRIMARY KEY (id);


--
-- Name: persons persons_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.persons
    ADD CONSTRAINT persons_pkey PRIMARY KEY (id);


--
-- Name: relationship_evidences relationship_evidences_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.relationship_evidences
    ADD CONSTRAINT relationship_evidences_pkey PRIMARY KEY (id);


--
-- Name: relationships relationships_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.relationships
    ADD CONSTRAINT relationships_pkey PRIMARY KEY (id);


--
-- Name: sna_graph_metrics sna_graph_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sna_graph_metrics
    ADD CONSTRAINT sna_graph_metrics_pkey PRIMARY KEY (id);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: sna_graph_metrics uq_sna_run_person; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sna_graph_metrics
    ADD CONSTRAINT uq_sna_run_person UNIQUE (run_id, person_id);


--
-- Name: ix_blocks_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_blocks_id ON public.blocks USING btree (id);


--
-- Name: ix_blocks_page_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_blocks_page_id ON public.blocks USING btree (page_id);


--
-- Name: ix_documents_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_documents_id ON public.documents USING btree (id);


--
-- Name: ix_entity_operations_audit_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_entity_operations_audit_created_at ON public.entity_operations_audit USING btree (created_at);


--
-- Name: ix_entity_operations_audit_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_entity_operations_audit_id ON public.entity_operations_audit USING btree (id);


--
-- Name: ix_entity_operations_audit_is_reverted; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_entity_operations_audit_is_reverted ON public.entity_operations_audit USING btree (is_reverted);


--
-- Name: ix_entity_operations_audit_operation_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_entity_operations_audit_operation_type ON public.entity_operations_audit USING btree (operation_type);


--
-- Name: ix_entity_operations_audit_primary_person_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_entity_operations_audit_primary_person_id ON public.entity_operations_audit USING btree (primary_person_id);


--
-- Name: ix_entity_operations_audit_secondary_person_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_entity_operations_audit_secondary_person_id ON public.entity_operations_audit USING btree (secondary_person_id);


--
-- Name: ix_exports_document_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_exports_document_id ON public.exports USING btree (document_id);


--
-- Name: ix_exports_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_exports_id ON public.exports USING btree (id);


--
-- Name: ix_external_authorities_authority_source; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_external_authorities_authority_source ON public.external_authorities USING btree (authority_source);


--
-- Name: ix_external_authorities_claim_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_external_authorities_claim_status ON public.external_authorities USING btree (claim_status);


--
-- Name: ix_external_authorities_external_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_external_authorities_external_id ON public.external_authorities USING btree (external_id);


--
-- Name: ix_external_authorities_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_external_authorities_id ON public.external_authorities USING btree (id);


--
-- Name: ix_external_authorities_person_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_external_authorities_person_id ON public.external_authorities USING btree (person_id);


--
-- Name: ix_external_authorities_review_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_external_authorities_review_status ON public.external_authorities USING btree (review_status);


--
-- Name: ix_external_authorities_source_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_external_authorities_source_id ON public.external_authorities USING btree (authority_source, external_id);


--
-- Name: ix_pages_document_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_pages_document_id ON public.pages USING btree (document_id);


--
-- Name: ix_pages_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_pages_id ON public.pages USING btree (id);


--
-- Name: ix_person_mentions_block_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_person_mentions_block_id ON public.person_mentions USING btree (block_id);


--
-- Name: ix_person_mentions_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_person_mentions_id ON public.person_mentions USING btree (id);


--
-- Name: ix_person_mentions_normalized_text; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_person_mentions_normalized_text ON public.person_mentions USING btree (normalized_text);


--
-- Name: ix_person_mentions_person_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_person_mentions_person_id ON public.person_mentions USING btree (person_id);


--
-- Name: ix_person_mentions_resolution_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_person_mentions_resolution_status ON public.person_mentions USING btree (resolution_status);


--
-- Name: ix_person_mentions_review_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_person_mentions_review_status ON public.person_mentions USING btree (review_status);


--
-- Name: ix_person_mentions_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_person_mentions_status ON public.person_mentions USING btree (resolution_status, review_status);


--
-- Name: ix_person_mentions_temporal_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_person_mentions_temporal_date ON public.person_mentions USING btree (temporal_date);


--
-- Name: ix_persons_canonical_name_en; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_persons_canonical_name_en ON public.persons USING btree (canonical_name_en);


--
-- Name: ix_persons_canonical_name_zh; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_persons_canonical_name_zh ON public.persons USING btree (canonical_name_zh);


--
-- Name: ix_persons_display_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_persons_display_name ON public.persons USING btree (display_name);


--
-- Name: ix_persons_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_persons_id ON public.persons USING btree (id);


--
-- Name: ix_persons_identity_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_persons_identity_status ON public.persons USING btree (identity_status);


--
-- Name: ix_persons_primary_role; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_persons_primary_role ON public.persons USING btree (primary_role);


--
-- Name: ix_persons_review_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_persons_review_status ON public.persons USING btree (review_status);


--
-- Name: ix_relationship_evidences_block_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationship_evidences_block_id ON public.relationship_evidences USING btree (block_id);


--
-- Name: ix_relationship_evidences_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationship_evidences_id ON public.relationship_evidences USING btree (id);


--
-- Name: ix_relationship_evidences_person_mention_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationship_evidences_person_mention_id ON public.relationship_evidences USING btree (person_mention_id);


--
-- Name: ix_relationship_evidences_relationship_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationship_evidences_relationship_id ON public.relationship_evidences USING btree (relationship_id);


--
-- Name: ix_relationships_claim_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationships_claim_status ON public.relationships USING btree (claim_status);


--
-- Name: ix_relationships_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationships_id ON public.relationships USING btree (id);


--
-- Name: ix_relationships_relation_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationships_relation_type ON public.relationships USING btree (relation_type);


--
-- Name: ix_relationships_review_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationships_review_status ON public.relationships USING btree (review_status);


--
-- Name: ix_relationships_source_person_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationships_source_person_id ON public.relationships USING btree (source_person_id);


--
-- Name: ix_relationships_target_person_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationships_target_person_id ON public.relationships USING btree (target_person_id);


--
-- Name: ix_relationships_temporal; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_relationships_temporal ON public.relationships USING btree (valid_from, valid_to);


--
-- Name: ix_sna_graph_metrics_community_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sna_graph_metrics_community_id ON public.sna_graph_metrics USING btree (community_id);


--
-- Name: ix_sna_graph_metrics_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sna_graph_metrics_id ON public.sna_graph_metrics USING btree (id);


--
-- Name: ix_sna_graph_metrics_person_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sna_graph_metrics_person_id ON public.sna_graph_metrics USING btree (person_id);


--
-- Name: ix_sna_graph_metrics_run_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sna_graph_metrics_run_id ON public.sna_graph_metrics USING btree (run_id);


--
-- Name: ix_sna_metrics_run_scope; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_sna_metrics_run_scope ON public.sna_graph_metrics USING btree (run_id, graph_scope);


--
-- Name: ix_tasks_celery_task_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ix_tasks_celery_task_id ON public.tasks USING btree (celery_task_id);


--
-- Name: ix_tasks_document_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_tasks_document_id ON public.tasks USING btree (document_id);


--
-- Name: ix_tasks_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_tasks_id ON public.tasks USING btree (id);


--
-- Name: blocks blocks_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blocks
    ADD CONSTRAINT blocks_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: entity_operations_audit entity_operations_audit_primary_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.entity_operations_audit
    ADD CONSTRAINT entity_operations_audit_primary_person_id_fkey FOREIGN KEY (primary_person_id) REFERENCES public.persons(id) ON DELETE CASCADE;


--
-- Name: entity_operations_audit entity_operations_audit_secondary_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.entity_operations_audit
    ADD CONSTRAINT entity_operations_audit_secondary_person_id_fkey FOREIGN KEY (secondary_person_id) REFERENCES public.persons(id) ON DELETE SET NULL;


--
-- Name: exports exports_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.exports
    ADD CONSTRAINT exports_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: external_authorities external_authorities_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.external_authorities
    ADD CONSTRAINT external_authorities_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id) ON DELETE CASCADE;


--
-- Name: pages pages_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: person_mentions person_mentions_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.person_mentions
    ADD CONSTRAINT person_mentions_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id) ON DELETE RESTRICT;


--
-- Name: person_mentions person_mentions_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.person_mentions
    ADD CONSTRAINT person_mentions_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id) ON DELETE SET NULL;


--
-- Name: relationship_evidences relationship_evidences_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.relationship_evidences
    ADD CONSTRAINT relationship_evidences_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id) ON DELETE RESTRICT;


--
-- Name: relationship_evidences relationship_evidences_person_mention_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.relationship_evidences
    ADD CONSTRAINT relationship_evidences_person_mention_id_fkey FOREIGN KEY (person_mention_id) REFERENCES public.person_mentions(id) ON DELETE SET NULL;


--
-- Name: relationship_evidences relationship_evidences_relationship_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.relationship_evidences
    ADD CONSTRAINT relationship_evidences_relationship_id_fkey FOREIGN KEY (relationship_id) REFERENCES public.relationships(id) ON DELETE CASCADE;


--
-- Name: relationships relationships_source_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.relationships
    ADD CONSTRAINT relationships_source_person_id_fkey FOREIGN KEY (source_person_id) REFERENCES public.persons(id) ON DELETE CASCADE;


--
-- Name: relationships relationships_target_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.relationships
    ADD CONSTRAINT relationships_target_person_id_fkey FOREIGN KEY (target_person_id) REFERENCES public.persons(id) ON DELETE CASCADE;


--
-- Name: sna_graph_metrics sna_graph_metrics_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.sna_graph_metrics
    ADD CONSTRAINT sna_graph_metrics_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id) ON DELETE CASCADE;


--
-- Name: tasks tasks_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


