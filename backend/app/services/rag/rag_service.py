"""Graph RAG orchestration service."""
import time
from typing import List, Optional, Set
from sqlalchemy.orm import Session

from app.core import logger
from app.schemas.rag import (
    RagEntityContextResponse,
    RagQueryRequest,
    RagQueryResponse,
)
from app.services.kg.llm_client import kg_llm_client
from app.services.rag.answer_integrity import (
    FAILURE_EMPTY_RESPONSE,
    build_deterministic_generation,
    build_model_generation,
    build_refusal_generation,
    check_citation_integrity,
    citation_violation_prefix,
    classify_llm_failure,
    degraded_answer_prefix,
    safe_failure_detail,
)
from app.services.rag.context_builder import context_builder
from app.services.rag.graph_retriever import graph_retriever
from app.services.rag.query_analyzer import query_analyzer
from app.services.rag.vector_retriever import vector_retriever
from app.services.group_scope import filter_sources_by_document_ids


RAG_SYSTEM_PROMPT = """你是一个专门研究 18 世纪末英国东印度公司广州商馆（Canton Factory 1792–1793）档案的资深数字人文历史学者。
你的职责是基于提供的【历史知识图谱结构化事实】与【原始档案文本证据】，为学者提供严谨、准确、有据可查的研究级回答。

【回答规范】
1. 核心结论：首先给出清晰明确的历史事实结论；
2. 考证依据：结合知识图谱与原始档案文本展开详细论述，指出历史人物的身份、行为、关系类型以及涉及的公文或交易细节；
3. 来源引用标记：所有引用档案或事实的关键句，必须在句末使用标记（如 [证据 #1]、[证据 #2]）与提供的证据严格对应；
4. 史学认识论警示：如果事实在档案中存疑或仅为单方记录，应明确指出；
5. 证据不足拒答：如果提供的知识和证据不足以支撑回答，你必须明确回答“当前知识库没有足够证据，无法得出确定结论”，坚决杜绝虚构历史。
"""


class RagService:
    """Orchestrates Query understanding, Graph expansion, Vector search, and LLM synthesis."""

    async def answer_question(
        self,
        db: Session,
        request: RagQueryRequest,
        group_document_ids: Optional[Set[int]] = None,
    ) -> RagQueryResponse:
        """Answer a research question, optionally restricted to a group's documents.

        ``group_document_ids`` is always resolved on the server (see
        ``app.services.group_scope``); an empty set means "the group has no usable
        documents" and produces a refusal rather than an unscoped search.
        """
        start_time = time.perf_counter()
        query = request.question.strip()

        # 0. 研究组范围（服务端注入）：空组直接拒答，禁止回退全库
        if group_document_ids is not None and not group_document_ids:
            elapsed = (time.perf_counter() - start_time) * 1000
            return RagQueryResponse(
                question=query,
                answer="当前研究组内没有可用档案文本，无法在该范围内提供可信答案。请先向该文档组导入并完成 OCR 处理后再检索。",
                evidence_sufficient=False,
                intent=query_analyzer.analyze_query(query, db=db),
                entities=[],
                relationships=[],
                sources=[],
                execution_time_ms=round(elapsed, 2),
                generation=build_refusal_generation(),
            )

        # 1. Query 理解
        intent = query_analyzer.analyze_query(query, db=db)
        logger.info(
            "RAG Query: '%s' | Intent: %s | Entities: %s | Keywords: %s | group=%s",
            query, intent.query_type, intent.entities, intent.search_keywords,
            "unscoped" if group_document_ids is None else len(group_document_ids),
        )

        # 2. Graph 检索
        entities, relationships, graph_evidences = [], [], []
        if request.include_graph and intent.entities:
            entities, relationships, graph_evidences = graph_retriever.retrieve_graph_context(
                db=db,
                entity_names=intent.entities,
                time_filter=request.time_filter or intent.time_filter,
                group_document_ids=group_document_ids,
            )

        # 3. 向量与文本证据检索
        text_evidences = vector_retriever.retrieve_text_evidence(
            db=db,
            query=query,
            keywords=intent.search_keywords,
            entities=intent.entities,
            top_k=request.top_k,
            time_filter=request.time_filter or intent.time_filter,
            group_document_ids=group_document_ids,
        )

        # 4. Graph + Vector 融合
        context_text, unified_sources, is_sufficient = context_builder.fuse_and_build_context(
            query=query,
            intent=intent,
            entities=entities,
            relationships=relationships,
            graph_evidences=graph_evidences,
            text_evidences=text_evidences,
        )

        # 4b. 范围兜底校验：任何无法证明属于当前组的来源都不得进入回答
        if group_document_ids is not None:
            kept, dropped = filter_sources_by_document_ids(unified_sources, set(group_document_ids))
            if dropped:
                logger.warning(
                    "Dropped %d out-of-scope sources for group research", len(dropped)
                )
            unified_sources = kept
            if not unified_sources:
                elapsed = (time.perf_counter() - start_time) * 1000
                return RagQueryResponse(
                    question=query,
                    answer="当前研究组内没有检索到与该问题相关的档案证据，无法在该范围内提供可信答案。",
                    evidence_sufficient=False,
                    intent=intent,
                    entities=[],
                    relationships=[],
                    sources=[],
                    execution_time_ms=round(elapsed, 2),
                    generation=build_refusal_generation(),
                )

        # 5. 防幻觉门控：无有效证据时拒绝生成，避免编造
        if not is_sufficient:
            elapsed = (time.perf_counter() - start_time) * 1000
            return RagQueryResponse(
                question=query,
                answer="当前知识库没有足够证据，无法就此问题提供可信的历史答案。请尝试检索已录入的广州商馆档案人物（如 Howqua 浩官、Henry Browne 亨利·布朗、George Macartney 马戛尔尼等）或具体商馆事务。",
                evidence_sufficient=False,
                intent=intent,
                entities=[],
                relationships=[],
                sources=[],
                execution_time_ms=round(elapsed, 2),
                generation=build_refusal_generation(),
            )

        # 6. LLM 综合回答生成（失败时显式标注降级，不得伪装成正常回答）
        generated = await self._generate_llm_answer(
            query=query,
            context_text=context_text,
            entities=entities,
            relationships=relationships,
            sources=unified_sources,
        )
        if isinstance(generated, tuple):
            answer_text, generation = generated
        else:
            # Stub compatibility: callers (and existing tests) may replace
            # ``_generate_llm_answer`` with an async mock that returns bare text.
            answer_text = str(generated)
            generation = build_model_generation(kg_llm_client.default_model)

        # 7. 引用完整性硬校验：[证据 #N] 必须真实存在于本次检索到的证据中
        generation.citation_check = check_citation_integrity(answer_text, unified_sources)
        if not generation.citation_check.valid:
            generation.degraded = True
            answer_text = (
                citation_violation_prefix(generation.citation_check.invalid_ids) + answer_text
            )
            logger.warning(
                "RAG answer cited non-existent evidence ids: %s (available=%d)",
                generation.citation_check.invalid_ids,
                generation.citation_check.available_count,
            )

        elapsed = (time.perf_counter() - start_time) * 1000
        return RagQueryResponse(
            question=query,
            answer=answer_text,
            evidence_sufficient=True,
            intent=intent,
            entities=entities,
            relationships=relationships,
            sources=unified_sources,
            execution_time_ms=round(elapsed, 2),
            generation=generation,
        )

    def get_entity_context(self, db: Session, entity_id: int) -> RagEntityContextResponse:
        """Retrieve contextual graph and grounded evidences for a specific entity."""
        target, neighbors, rels, sources = graph_retriever.get_entity_subgraph_context(db=db, person_id=entity_id)
        if not target:
            from app.core import AppException
            raise AppException(f"Entity with ID {entity_id} not found in knowledge graph", code="ENTITY_NOT_FOUND")

        return RagEntityContextResponse(
            entity=target,
            neighbors=neighbors,
            relationships=rels,
            evidences=sources,
        )

    async def _generate_llm_answer(
        self,
        query: str,
        context_text: str,
        entities: list,
        relationships: list,
        sources: list,
    ) -> tuple:
        """Invoke the LLM, returning ``(answer_text, generation)``.

        A model failure is reported, not hidden: the deterministic synthesis is
        returned together with ``degraded=True`` and a stable failure reason, and
        a warning banner is prepended so the text cannot be read as a normal
        model answer.
        """
        try:
            raw_answer, _ = await kg_llm_client.call_extraction_llm(
                system_prompt=RAG_SYSTEM_PROMPT,
                user_content=context_text,
            )
        except Exception as exc:
            reason, message = classify_llm_failure(exc)
            detail = safe_failure_detail(exc)
            logger.warning("LLM API call failed during RAG answering (%s): %s", reason, detail)
            fallback = self._build_deterministic_synthesis(query, entities, relationships, sources)
            return (
                degraded_answer_prefix(reason) + fallback,
                build_deterministic_generation(reason, message, detail),
            )

        answer_text = (raw_answer or "").strip()
        if not answer_text:
            message = "模型返回了空内容，无法据此生成回答。"
            logger.warning("LLM returned an empty answer for RAG query; falling back to deterministic synthesis.")
            fallback = self._build_deterministic_synthesis(query, entities, relationships, sources)
            return (
                degraded_answer_prefix(FAILURE_EMPTY_RESPONSE) + fallback,
                build_deterministic_generation(FAILURE_EMPTY_RESPONSE, message, None),
            )

        return answer_text, build_model_generation(kg_llm_client.default_model)

    def _build_deterministic_synthesis(
        self,
        query: str,
        entities: list,
        relationships: list,
        sources: list,
    ) -> str:
        """Synthesize answer deterministically from retrieved facts if LLM is unavailable."""
        lines = []
        lines.append("### 历史事实结论\n")

        if entities:
            names = "、".join([f"{e.display_name}（{e.primary_role or '历史人物'}）" for e in entities[:3]])
            lines.append(f"根据东印度公司广州商馆知识图谱记录，问题涉及核心历史人物：{names}。")

        if relationships:
            lines.append("\n### 关系与交往考证\n")
            for r in relationships[:3]:
                ev_str = f"（引证：\"{r.evidence_quote}\"）" if r.evidence_quote else ""
                lines.append(
                    f"- **{r.source_person_name}** 与 **{r.target_person_name}** 在商馆档案中记录有 **{r.relation_type}** 往来{ev_str}。"
                )

        if sources:
            lines.append("\n### 原始档案文献依据\n")
            for s in sources[:3]:
                page_info = f"第 {s.page_number} 页" if s.page_number else ""
                lines.append(f"- [{s.source_id}] 《{s.document_name}》{page_info}（Block #{s.block_id}）：\"{s.quote_text}\"")

        return "\n".join(lines)


rag_service = RagService()
