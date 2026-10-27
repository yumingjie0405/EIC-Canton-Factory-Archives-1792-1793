"""Tool registry and execution whitelist for Autonomous Historical Research Agent.

Argument contract
-----------------
Every tool declares its parameters in ``register(parameters=...)``:

* ``required``      – names that must be present and non-blank. A missing value is
                      reported as a **tool execution failure** (``status="error"``
                      with a ``missing required argument(s): …`` message). Values
                      are never coerced (``None`` is not turned into ``"None"``)
                      and never widened into a broader query.
* ``required_any``  – groups of alternatives; at least one member must be present.
* optional          – an omitted/``None`` value has an explicit default, documented
                      in the tool function docstring.

Read-only tools only: nothing below writes to the database.
"""
import inspect
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, cast, Text

from app.core import logger
from app.models.block import Block
from app.models.document import Document
from app.models.page import Page
from app.models.kg import ExternalAuthority, Person, Relationship, RelationshipEvidence
from app.models.kg.enums import ClaimStatus, IdentityStatus
from app.schemas.agent import ToolCallRecord, ToolDefinition
from app.schemas.rag import (
    QueryIntentAnalysis,
    RagEntityReference,
    RagQueryRequest,
    RagRelationshipReference,
    RagSourceEvidence,
)
from app.services.rag import (
    graph_retriever,
    query_analyzer,
    rag_service,
    vector_retriever,
)
from app.services.rag.temporal_resolver import temporal_resolver


def _is_blank_argument(value: Any) -> bool:
    """True when a required argument carries no usable value.

    Strings are checked after stripping so ``"   "`` counts as missing. Numbers
    (including ``0``) are valid values for arguments such as ``person_id``.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _block_document_id(db: Session, block_id: Optional[int]) -> Optional[int]:
    """Document that owns a block (``None`` when the block is gone)."""
    if block_id is None:
        return None
    row = (
        db.query(Page.document_id)
        .join(Block, Block.page_id == Page.id)
        .filter(Block.id == block_id)
        .first()
    )
    return row[0] if row else None


def _person_grounded_in_group(
    db: Session, person_id: Optional[int], allowed_document_ids: Set[int]
) -> bool:
    """True when the person is mentioned in, or evidenced by, the group's archives."""
    if person_id is None or not allowed_document_ids:
        return False
    from app.models.kg.mention import PersonMention

    mentioned = (
        db.query(PersonMention.id)
        .join(Block, Block.id == PersonMention.block_id)
        .join(Page, Page.id == Block.page_id)
        .filter(
            PersonMention.person_id == person_id,
            Page.document_id.in_(allowed_document_ids),
        )
        .first()
    )
    if mentioned is not None:
        return True
    evidenced = (
        db.query(RelationshipEvidence.id)
        .join(Block, Block.id == RelationshipEvidence.block_id)
        .join(Page, Page.id == Block.page_id)
        .join(Relationship, Relationship.id == RelationshipEvidence.relationship_id)
        .filter(
            Page.document_id.in_(allowed_document_ids),
            or_(
                Relationship.source_person_id == person_id,
                Relationship.target_person_id == person_id,
            ),
        )
        .first()
    )
    return evidenced is not None


class ToolRegistry:
    """Registry maintaining whitelisted read-only tools for historical research."""
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._register_default_tools()

    def register(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        func: Callable,
    ):
        """Register a new whitelisted tool."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "func": func,
        }

    def list_tool_definitions(self) -> List[ToolDefinition]:
        """Return list of public tool specifications."""
        return [
            ToolDefinition(
                name=meta["name"],
                description=meta["description"],
                parameters=meta["parameters"],
            )
            for meta in self._tools.values()
        ]

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check whether tool is registered in the whitelist."""
        return tool_name in self._tools

    async def execute_tool(
        self,
        db: Session,
        tool_name: str,
        arguments: Dict[str, Any],
        call_id: str,
    ) -> Tuple[ToolCallRecord, Any]:
        """Execute a whitelisted tool with timing, exception containment and audit logging."""
        start_time = time.perf_counter()

        # Whitelist enforcement
        if not self.is_tool_allowed(tool_name):
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            logger.warning("Agent attempted unauthorized or unwhitelisted tool call: '%s'", tool_name)
            record = ToolCallRecord(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                output_summary=f"Tool '{tool_name}' is not in the authorized research whitelist.",
                latency_ms=round(latency_ms, 2),
                error_message="Unauthorized tool call intercepted by whitelist gate.",
            )
            return record, None

        tool_meta = self._tools[tool_name]
        func = tool_meta["func"]

        # Argument contract: a required parameter must be present and non-blank.
        # Missing values are reported as a tool failure instead of being coerced
        # (``None`` -> ``"None"``) or silently widened into a broader query.
        params_spec = tool_meta.get("parameters") or {}
        missing = [
            name
            for name in params_spec.get("required", [])
            if _is_blank_argument(arguments.get(name))
        ]
        for alternatives in params_spec.get("required_any", []):
            if all(_is_blank_argument(arguments.get(name)) for name in alternatives):
                missing.append(" or ".join(alternatives))
        if missing:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            message = "missing required argument(s): %s" % ", ".join(missing)
            logger.warning("Tool '%s' rejected: %s", tool_name, message)
            record = ToolCallRecord(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                output_summary=f"Invalid arguments for {tool_name}: {message}",
                latency_ms=round(latency_ms, 2),
                error_message=message,
            )
            return record, None

        try:
            # Handle sync vs async functions
            if inspect.iscoroutinefunction(func):
                raw_result = await func(db=db, **arguments)
            else:
                raw_result = func(db=db, **arguments)

            latency_ms = (time.perf_counter() - start_time) * 1000.0

            # Summarize output and count extracted evidence
            evidence_count = 0
            if isinstance(raw_result, list):
                evidence_count = len(raw_result)
                summary = f"Retrieved {evidence_count} records."
            elif isinstance(raw_result, dict):
                evidence_count = len(raw_result.get("sources", [])) or len(raw_result.get("items", [])) or 1
                summary = f"Operation completed. Keys: {list(raw_result.keys())}"
            else:
                summary = str(raw_result)[:200]

            record = ToolCallRecord(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                status="success",
                output_summary=summary,
                latency_ms=round(latency_ms, 2),
                evidence_count=evidence_count,
            )
            return record, raw_result

        except TypeError as type_err:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            logger.warning("Tool '%s' argument validation failed: %s", tool_name, type_err)
            record = ToolCallRecord(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                output_summary=f"Invalid arguments for {tool_name}: {type_err}",
                latency_ms=round(latency_ms, 2),
                error_message=str(type_err),
            )
            return record, None

        except Exception as exc:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("Exception executing tool '%s': %s", tool_name, exc, exc_info=True)
            record = ToolCallRecord(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                output_summary=f"Tool execution failed: {type(exc).__name__}",
                latency_ms=round(latency_ms, 2),
                error_message=str(exc)[:300],
            )
            return record, None

    def _register_default_tools(self):
        """Register the 6 core research tools."""

        # 1. search_persons
        self.register(
            name="search_persons",
            description="Search historical persons by Chinese/English name, alias, role or merchant house.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Person name or title to search (e.g. Howqua, Browne, 伍秉鉴)"},
                    "limit": {"type": "integer", "description": "Max results to return", "default": 5},
                },
                "required": ["query"],
            },
            func=self._tool_search_persons,
        )

        # 2. get_person_relationships
        self.register(
            name="get_person_relationships",
            description="Retrieve commercial, diplomatic or personal relationships for a specific person.",
            parameters={
                "type": "object",
                "properties": {
                    "person_id": {"type": "integer", "description": "Database ID of person"},
                    "person_name": {"type": "string", "description": "Name of person (if ID not known)"},
                },
                # One identifier is enough; neither is a contract violation.
                "required_any": [["person_id", "person_name"]],
            },
            func=self._tool_get_person_relationships,
        )

        # 3. search_archival_evidence
        self.register(
            name="search_archival_evidence",
            description="Retrieve raw archival text blocks and citations matching historical query and keywords.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Historical search prompt"},
                    "keywords": {"type": "array", "items": {"type": "string"}, "description": "Optional specific keywords"},
                    "entities": {"type": "array", "items": {"type": "string"}, "description": "Optional relevant historical entity names"},
                    "time_filter": {"type": "integer", "description": "Optional target year for post-filtering (e.g. 1792 or 1793)"},
                    "top_k": {"type": "integer", "description": "Max evidence records to retrieve", "default": 5},
                },
                "required": ["query"],
            },
            func=self._tool_search_archival_evidence,
        )

        # 4. get_external_authorities
        self.register(
            name="get_external_authorities",
            description="Retrieve external authoritative historiography citations (e.g. Liang Jiabin, CBDB) for an entity.",
            parameters={
                "type": "object",
                "properties": {
                    "person_id": {"type": "integer", "description": "Database ID of person"},
                    "person_name": {"type": "string", "description": "Name of person (if ID not known)"},
                },
                # One identifier is enough; neither is a contract violation.
                "required_any": [["person_id", "person_name"]],
            },
            func=self._tool_get_external_authorities,
        )

        # 5. graph_rag_query
        self.register(
            name="graph_rag_query",
            description="Execute comprehensive Graph RAG query combining knowledge graph facts and archival citations.",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Natural language question"},
                    "time_filter": {"type": "integer", "description": "Optional year constraint"},
                    "top_k": {"type": "integer", "description": "Number of sources", "default": 5},
                },
                "required": ["question"],
            },
            func=self._tool_graph_rag_query,
        )

        # 6. analyze_query
        self.register(
            name="analyze_query",
            description="Analyze query intent, extract historical entities, and identify time scope.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to analyze"},
                },
                "required": ["query"],
            },
            func=self._tool_analyze_query,
        )

    # Tool Implementations
    @staticmethod
    def _tool_search_persons(
        db: Session,
        query: str,
        limit: int = 5,
        group_document_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        clean_q = query.strip()
        search_filter = or_(
            Person.display_name.ilike(f"%{clean_q}%"),
            Person.canonical_name_zh.ilike(f"%{clean_q}%"),
            Person.canonical_name_en.ilike(f"%{clean_q}%"),
            cast(Person.aliases, Text).ilike(f"%{clean_q}%"),
        )
        persons = db.query(Person).filter(search_filter).limit(limit).all()
        if group_document_ids is not None:
            allowed = {int(value) for value in group_document_ids}
            persons = [p for p in persons if _person_grounded_in_group(db, p.id, allowed)]
        return [
            {
                "id": p.id,
                "display_name": p.display_name,
                "canonical_name_zh": p.canonical_name_zh,
                "canonical_name_en": p.canonical_name_en,
                "primary_role": p.primary_role,
                "organization": p.organization,
                "birth_year": p.birth_year,
                "death_year": p.death_year,
                "active_years": f"{p.active_start or ''}-{p.active_end or ''}",
                "identity_status": p.identity_status.value if hasattr(p.identity_status, "value") else str(p.identity_status),
            }
            for p in persons
        ]

    @staticmethod
    def _tool_get_person_relationships(
        db: Session,
        person_id: Optional[int] = None,
        person_name: Optional[str] = None,
        group_document_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        target_id = person_id
        if not target_id and person_name:
            p = db.query(Person).filter(
                or_(
                    Person.display_name.ilike(f"%{person_name.strip()}%"),
                    Person.canonical_name_zh.ilike(f"%{person_name.strip()}%"),
                    Person.canonical_name_en.ilike(f"%{person_name.strip()}%"),
                )
            ).first()
            if p:
                target_id = p.id

        if not target_id:
            return []

        rels = db.query(Relationship).filter(
            or_(
                Relationship.source_person_id == target_id,
                Relationship.target_person_id == target_id,
            )
        ).all()

        scope_ids = None if group_document_ids is None else {int(v) for v in group_document_ids}
        results = []
        for r in rels:
            src = db.get(Person, r.source_person_id)
            tgt = db.get(Person, r.target_person_id)
            evidences = list(r.evidences or [])
            if scope_ids is not None:
                evidences = [
                    ev for ev in evidences
                    if _block_document_id(db, ev.block_id) in scope_ids
                ]
                if not evidences:
                    continue
            ev = evidences[0] if evidences else None

            b_id = ev.block_id if ev else None
            is_valid_primary_evidence = False
            verified_quote_text = None
            doc_id = None
            doc_name = None
            page_no = None
            temporal_coverage = None
            temporal_provenance = None

            # 严格核查引用关联链：禁止拼接不一致信息，严防幽灵空白块
            if b_id:
                block_obj = (
                    db.query(Block)
                    .options(joinedload(Block.page).joinedload(Page.document))
                    .filter(Block.id == b_id)
                    .first()
                )
                if block_obj:
                    raw_txt = block_obj.ocr_text_corrected or block_obj.ocr_text_raw or block_obj.modern_english or ""
                    clean_txt = raw_txt.strip()
                    # 真实数据库 OCR 文本必须存在且具备实质内容 (>= 15 字符)
                    if len(clean_txt) >= 15:
                        is_valid_primary_evidence = True
                        verified_quote_text = clean_txt[:300]
                        if block_obj.page:
                            page_no = block_obj.page.page_number
                            if block_obj.page.document:
                                doc_id = block_obj.page.document.id
                                doc_name = block_obj.page.document.original_filename or block_obj.page.document.filename
                                t_info = temporal_resolver.resolve_document_temporal_info(doc_id, doc_name)
                                temporal_coverage = f"{t_info['temporal_label']} [{t_info['provenance']}]"
                                temporal_provenance = t_info["provenance"]

            results.append({
                "relationship_id": r.id,
                "source_person": src.display_name if src else f"Person #{r.source_person_id}",
                "target_person": tgt.display_name if tgt else f"Person #{r.target_person_id}",
                "relation_type": r.relation_type.value if hasattr(r.relation_type, "value") else str(r.relation_type),
                "confidence": r.confidence,
                "time_description": r.time_description,
                "evidence_summary": r.evidence_summary,
                "quote_text": ev.quote_text if ev else None,
                "block_id": b_id,
                "is_valid_primary_evidence": is_valid_primary_evidence,
                "verified_quote_text": verified_quote_text,
                "document_id": doc_id,
                "document_name": doc_name,
                "page_number": page_no,
                "temporal_coverage": temporal_coverage,
                "temporal_provenance": temporal_provenance,
            })
        return results

    @staticmethod
    def _tool_search_archival_evidence(
        db: Session,
        query: str,
        keywords: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        time_filter: Optional[int] = None,
        top_k: int = 5,
        group_document_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve candidate text blocks using VectorRetriever with time_filter constraint.

        Contract: ``query`` is required (a blank/None value is rejected before this
        function is called). ``keywords`` / ``entities`` default to *no extra terms*
        (the query tokens alone drive retrieval); ``time_filter=None`` means *no
        temporal restriction*, while an explicit year enables the strict
        single-year document filter. ``top_k`` defaults to 5.
        """
        # Retrieve candidate text blocks using VectorRetriever with time_filter constraint
        raw_evidences = vector_retriever.retrieve_text_evidence(
            db=db,
            query=query,
            keywords=keywords or [],
            entities=entities or [],
            top_k=top_k,
            time_filter=time_filter,
            group_document_ids=(
                None if group_document_ids is None else {int(v) for v in group_document_ids}
            ),
        )

        return [
            {
                "source_id": ev.source_id,
                "document_id": ev.document_id,
                "document_name": ev.document_name,
                "page_number": ev.page_number,
                "block_id": ev.block_id,
                "quote_text": ev.quote_text,
                "relevance_score": round(ev.relevance_score, 4),
                "matched_entities": ev.matched_entities,
                "evidence_type": ev.evidence_type,
                "temporal_coverage": ev.temporal_coverage,
                "temporal_provenance": ev.temporal_provenance,
            }
            for ev in raw_evidences
        ]

    @staticmethod
    def _tool_get_external_authorities(
        db: Session,
        person_id: Optional[int] = None,
        person_name: Optional[str] = None,
        group_document_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        target_id = person_id
        if not target_id and person_name:
            p = db.query(Person).filter(
                or_(
                    Person.display_name.ilike(f"%{person_name.strip()}%"),
                    Person.canonical_name_zh.ilike(f"%{person_name.strip()}%"),
                )
            ).first()
            if p:
                target_id = p.id

        if not target_id:
            return []

        if group_document_ids is not None:
            allowed = {int(v) for v in group_document_ids}
            if not _person_grounded_in_group(db, target_id, allowed):
                return []

        auths = db.query(ExternalAuthority).filter_by(person_id=target_id).all()
        return [
            {
                "id": a.id,
                "authority_source": a.authority_source.value if hasattr(a.authority_source, "value") else str(a.authority_source),
                "record_title": a.record_title,
                "historical_notes": a.historical_notes,
                "external_url": a.external_url,
            }
            for a in auths
        ]

    @staticmethod
    async def _tool_graph_rag_query(
        db: Session,
        question: str,
        time_filter: Optional[int] = None,
        top_k: int = 5,
        group_document_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        req = RagQueryRequest(
            question=question,
            time_filter=time_filter,
            top_k=top_k,
        )
        res = await rag_service.answer_question(
            db=db,
            request=req,
            group_document_ids=(
                None if group_document_ids is None else {int(v) for v in group_document_ids}
            ),
        )
        return {
            "answer": res.answer,
            "evidence_sufficient": res.evidence_sufficient,
            "entities_found": [e.display_name for e in res.entities],
            "relationships_found": len(res.relationships),
            "sources": [
                {
                    "source_id": s.source_id,
                    "document_id": s.document_id,
                    "document_name": s.document_name,
                    "page_number": s.page_number,
                    "block_id": s.block_id,
                    "quote_text": s.quote_text,
                }
                for s in res.sources
            ],
        }

    @staticmethod
    def _tool_analyze_query(
        db: Session,
        query: str,
    ) -> Dict[str, Any]:
        analysis = query_analyzer.analyze_query(query=query, db=db)
        return {
            "query_type": analysis.query_type,
            "entities": analysis.entities,
            "search_keywords": analysis.search_keywords,
            "time_filter": analysis.time_filter,
        }


tool_registry = ToolRegistry()
