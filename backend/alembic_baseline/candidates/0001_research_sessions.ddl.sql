-- Research history table: reviewable DDL candidate.
--
-- STATUS: NOT APPLIED. This file is generated from app/models/research.py
-- (SQLAlchemy PostgreSQL dialect) and exists only for review. It is not
-- part of any Alembic revision and nothing executes it automatically.
--
-- The table is excluded from Base.metadata.create_all() at application
-- startup (see app/models/research.py::ALEMBIC_MANAGED_TABLES), so it will
-- NOT appear in the running database until a reviewed migration is applied.

CREATE TABLE research_sessions (
	id SERIAL NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	question TEXT NOT NULL, 
	answer TEXT NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	evidence_sufficient BOOLEAN NOT NULL, 
	generation_mode VARCHAR(32), 
	model_name VARCHAR(128), 
	failure_reason VARCHAR(64), 
	failure_message TEXT, 
	failure_detail TEXT, 
	citation_valid BOOLEAN, 
	citation_invalid_ids JSON, 
	citation_available_count INTEGER, 
	sources JSON NOT NULL, 
	entities JSON, 
	relationships JSON, 
	intent JSON, 
	warnings JSON, 
	elapsed_ms FLOAT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT researchmode CHECK (mode IN ('rag', 'agent')), 
	CONSTRAINT researchstatus CHECK (status IN ('completed', 'degraded', 'insufficient_evidence', 'failed'))
);

CREATE INDEX ix_research_sessions_created_at ON research_sessions (created_at);

CREATE INDEX ix_research_sessions_mode_created_at ON research_sessions (mode, created_at);

CREATE INDEX ix_research_sessions_status ON research_sessions (status);
