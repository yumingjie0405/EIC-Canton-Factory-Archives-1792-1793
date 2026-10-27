"""ReAct iterative research execution engine with safety gates, budget controls, and categorized synthesis."""
import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy.orm import Session

from app.core import logger
from app.schemas.agent import (
    AgentBudgetInfo,
    AgentResearchRequest,
    AgentResearchResponse,
    AgentStepRecord,
    AnswerCategorization,
    ToolCallRecord,
)
from app.schemas.rag import (
    AnswerGeneration,
    QueryIntentAnalysis,
    RagEntityReference,
    RagRelationshipReference,
    RagSourceEvidence,
)
from app.services.agent.planner import agent_planner
from app.services.agent.tool_registry import tool_registry
from app.services.kg.llm_client import kg_llm_client
from app.services.rag import context_builder, query_analyzer
from app.services.rag.answer_integrity import (
    build_deterministic_generation,
    build_model_generation,
    build_refusal_generation,
    check_citation_integrity,
    citation_violation_prefix,
    classify_llm_failure,
    degraded_answer_prefix,
    safe_failure_detail,
    FAILURE_EMPTY_RESPONSE,
)


AGENT_SYNTHESIS_SYSTEM_PROMPT = """你是一个专门研究 18 世纪末英国东印度公司广州商馆（Canton Factory 1792–1793）档案的资深数字人文史学专家与 AI 研究员。
你必须基于所提供的确凿证据撰写严谨的学术分析报告。

【学术规范与防幻觉指令】
1. 严格区隔史实层级，必须使用以下五级标题结构：
   ### 档案直接记载
   （仅列举原始档案明确提及的内容，每条主张后必须明确附上对应的文献角标，如 [证据 #1]、[证据 #2]；严禁将知识图谱中缺乏一手文献切片的推断放入此处）
   ### 图谱关系事实
   （阐述知识图谱中已核实的人物实体身份、职务及经审定的交往关系；若无一手文献引证，应明确标注为图谱记录）
   ### 外部学术考据
   （结合梁嘉彬《广东十三行考》等权威史料的考证，注明学者或文献来源）
   ### 历史分析与综合推断
   （基于上述直接证据与历史背景做出的合理论述，必须明确使用“据此推断”、“分析表明”等字样）
   ### 档案未见记录与存疑说明
   （明确指出档案未予记载、超出年代或尚存学术争议的事项）

2. 禁止凭人名无据套用职务：档案中若仅称“Henry Browne Esqr”而无职务名，严禁在【档案直接记载】中妄称其为大班主席（President）或特定官职，仅可客观陈述“档案以先生/阁下（Esqr）尊称出现，未详列特定官阶”；
3. 严格年代对齐与时空隔离：若研究课题明确限定某一年代（如 1792 年），所引用的档案文献年代必须与课题年份对齐；若引用的文献属于其他年代（如 1779 或 1781 年），绝不得将其当成目标年代的直接记载，必须在【档案未见记录与存疑说明】中明确指出年代差异与时空界限，严禁跨年代混用；
4. 严禁伪造不存在的证据编号，所有 [证据 #X] 必须在上下文证据列表中真实存在；
5. 若无足够有效证据，必须在回答开头明确说明：“当前知识库没有足够证据，无法得出确定结论”，切勿编造人名、事件或年份。
"""


class AgentRunner:
    """Orchestrates iterative research, budget gating, and four-level cited synthesis."""

    async def run_research(
        self,
        db: Session,
        request: AgentResearchRequest,
        group_document_ids: Optional[Set[int]] = None,
    ) -> AgentResearchResponse:
        """Run the research loop, optionally restricted to a document group's archives.

        ``group_document_ids`` is resolved on the server; it is injected into every
        tool call, so neither the planner nor the model can widen or drop the scope.
        """
        start_time = time.perf_counter()
        task_id = f"agent-{uuid.uuid4().hex[:10]}"
        query = request.question.strip()

        trajectory: List[AgentStepRecord] = []
        collected_sources: List[RagSourceEvidence] = []
        collected_entities: List[RagEntityReference] = []
        collected_relationships: List[RagRelationshipReference] = []
        warnings: List[str] = []

        # 1. 前置意图与防幻觉前置拦截
        intent = query_analyzer.analyze_query(query, db=db)
        resolved_year = request.time_filter or intent.time_filter

        # 检查是否命中完全脱敏的虚构/无关问题（如拿破仑演习、1840鸦片战争）
        if self._is_completely_nonexistent_or_out_of_bounds(query, intent):
            return self._build_immediate_refusal_response(
                task_id=task_id,
                question=query,
                reason="问题超出东印度公司广州商馆（1792–1793）档案与已知图谱记载范畴，触发硬门控主动拒答。",
            )

        # 2. 生成多步研究规划
        plan = await agent_planner.create_plan(db=db, question=query, time_filter=resolved_year)
        planned_steps = plan.proposed_steps

        # 追踪预算
        steps_taken = 0
        tool_calls_count = 0
        seen_blocks: Set[int] = set()

        # 3. 动态多步研究循环 (ReAct Workflow)
        for step_idx, sub_goal in enumerate(planned_steps):
            if steps_taken >= request.max_steps:
                warnings.append(f"达到最大研究步数上限（max_steps={request.max_steps}），提前终止进一步探查。")
                break
            if tool_calls_count >= request.max_tool_calls:
                warnings.append(f"达到工具调用总预算上限（max_tool_calls={request.max_tool_calls}），提前完成证据汇总。")
                break

            steps_taken += 1
            step_record = AgentStepRecord(
                step_number=steps_taken,
                sub_goal=sub_goal,
                thought=f"执行研究步骤 {steps_taken}/{len(planned_steps)}：{sub_goal}。",
                tool_calls=[],
                step_status="running",
            )

            # 根据步骤阶段与上下文动态分派针对性的只读工具调用
            step_tools = self._select_tools_for_step(
                step_idx=step_idx,
                sub_goal=sub_goal,
                intent=intent,
                resolved_year=resolved_year,
                query=query,
                collected_entities=collected_entities,
                collected_sources=collected_sources,
                collected_relationships=collected_relationships,
                group_document_ids=group_document_ids,
            )

            for tool_name, tool_args in step_tools:
                if tool_calls_count >= request.max_tool_calls:
                    warnings.append(f"工具调用达到预算上限（{request.max_tool_calls}），略过剩余调用。")
                    break

                call_id = f"call-{steps_taken}-{tool_calls_count + 1}"
                tool_calls_count += 1

                call_record, tool_output = await tool_registry.execute_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=tool_args,
                    call_id=call_id,
                )
                step_record.tool_calls.append(call_record)

                # A tool failure must be visible in the response, not silently
                # downgraded into "no evidence was found".
                if getattr(call_record, "status", None) == "error":
                    warnings.append(
                        "工具 %s 执行失败：%s"
                        % (tool_name, getattr(call_record, "error_message", None) or "未知错误")
                    )

                # 将工具产出汇聚至上下文池
                self._accumulate_tool_results(
                    tool_name=tool_name,
                    output=tool_output,
                    sources=collected_sources,
                    entities=collected_entities,
                    relationships=collected_relationships,
                    seen_blocks=seen_blocks,
                )

            step_record.step_status = "completed"
            trajectory.append(step_record)

            # 动态提前收敛评估：如果已获得高相关性原卷文本与图谱事实，且意图非多跳历史分析，则可提前收敛
            if self._should_converge_early(intent, collected_sources, collected_relationships, steps_taken):
                logger.info("Agent: Early convergence triggered with sufficient historical evidence at step %d.", steps_taken)
                break

        # 4. 证据全局去重与分配四级标号 [证据 #1]..
        fused_context, unified_sources, is_sufficient = context_builder.fuse_and_build_context(
            query=query,
            intent=intent,
            entities=collected_entities,
            relationships=collected_relationships,
            graph_evidences=[],
            text_evidences=collected_sources,
        )

        total_latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        budget_exhausted = (steps_taken >= request.max_steps) or (tool_calls_count >= request.max_tool_calls)
        budget_info = AgentBudgetInfo(
            steps_executed=steps_taken,
            max_steps=request.max_steps,
            tool_calls_executed=tool_calls_count,
            max_tool_calls=request.max_tool_calls,
            elapsed_ms=total_latency_ms,
            budget_exhausted=budget_exhausted,
        )

        # 5. 防幻觉门控：若证据不足且核心实词全空，触发主动拒答
        if not is_sufficient or (not collected_sources and not collected_relationships):
            return AgentResearchResponse(
                task_id=task_id,
                question=query,
                final_answer="### 档案未见记录与存疑说明\n当前知识库没有足够证据，无法就此问题提供可信的历史答案。在 1792–1793 年东印度公司广州商馆档案与已知知识图谱中，未发现相关历史事实记载。",
                categorization=AnswerCategorization(
                    unresolved_aspects=["当前知识库与广州商馆档案中无相关有效记载。"]
                ),
                trajectory=trajectory,
                sources=[],
                entities=collected_entities,
                relationships=collected_relationships,
                evidence_sufficient=False,
                budget_used=budget_info,
                warnings=warnings,
                generation=build_refusal_generation(),
            )

        # 6. 调用 LLM（或显式标注降级）生成四级分层严谨综合
        final_answer, categorization, generation = await self._synthesize_answer(
            query=query,
            context_text=fused_context,
            entities=collected_entities,
            relationships=collected_relationships,
            sources=unified_sources,
        )

        # 7. 引用完整性硬校验：[证据 #N] 必须真实存在于本次汇聚的证据中
        generation.citation_check = check_citation_integrity(final_answer, unified_sources)
        if not generation.citation_check.valid:
            generation.degraded = True
            final_answer = citation_violation_prefix(generation.citation_check.invalid_ids) + final_answer
            warnings.append(
                "模型输出了不存在的证据编号 %s，已标记该回答不可直接采信。"
                % generation.citation_check.invalid_ids
            )
            logger.warning(
                "Agent answer cited non-existent evidence ids: %s (available=%d)",
                generation.citation_check.invalid_ids,
                generation.citation_check.available_count,
            )

        if generation.degraded and generation.failure_reason:
            warnings.append(
                "模型服务不可用（%s），当前回答由离线确定性合成生成，未经大模型综述。"
                % generation.failure_reason
            )

        return AgentResearchResponse(
            task_id=task_id,
            question=query,
            final_answer=final_answer,
            categorization=categorization,
            trajectory=trajectory,
            sources=unified_sources,
            entities=collected_entities,
            relationships=collected_relationships,
            evidence_sufficient=True,
            budget_used=budget_info,
            warnings=warnings,
            generation=generation,
        )

    def _select_tools_for_step(
        self,
        step_idx: int,
        sub_goal: str,
        intent: QueryIntentAnalysis,
        resolved_year: Optional[int],
        query: str,
        collected_entities: List[RagEntityReference],
        collected_sources: List[RagSourceEvidence],
        collected_relationships: List[RagRelationshipReference],
        group_document_ids: Optional[Set[int]] = None,
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Determine tools to invoke dynamically based on sub-goal semantics and working memory context."""
        tools: List[Tuple[str, Dict[str, Any]]] = []
        sub_goal_lower = sub_goal.lower()

        # 动态候选实体池：合并意图识别实体与前序步骤新发现的人物实体
        active_entities: List[str] = list(intent.entities)
        for e in collected_entities:
            name = e.canonical_name_zh or e.display_name or e.canonical_name_en
            if name and name not in active_entities:
                active_entities.append(name)

        # 1. 语义域判断：子目标涉及外部学术考据/梁嘉彬/十三行考
        is_external_goal = any(kw in sub_goal_lower for kw in ["外部", "学术", "史学", "考据", "梁嘉彬", "十三行考", "scholarship", "authority"])
        # 2. 语义域判断：子目标涉及人际/商业往来/担保/借贷/合作关系
        is_relation_goal = any(kw in sub_goal_lower for kw in ["关系", "往来", "交往", "担保", "债务", "借贷", "合作", "网络", "relationship", "network"])
        # 3. 语义域判断：子目标涉及原始文献/商馆日记/议事录/贸易物价/契约直接证据
        is_archival_goal = any(kw in sub_goal_lower for kw in ["档案", "文献", "记录", "日记", "咨询", "议事", "合同", "毛织品", "茶叶", "贸易", "物价", "银两", "evidence", "archive", "document", "contract"])
        # 4. 语义域判断：子目标涉及人物/身份/大班/行商查证
        is_person_goal = any(kw in sub_goal_lower for kw in ["人物", "身份", "大班", "行商", "买办", "实体", "person", "merchant", "who"])
        # 5. 语义域判断：子目标涉及综合复核/交叉验证
        is_synthesis_goal = any(kw in sub_goal_lower for kw in ["综合", "复核", "验证", "交叉", "rag", "verify"])

        if is_external_goal:
            target_name = active_entities[0] if active_entities else query[:15]
            tools.append(("get_external_authorities", {"person_name": target_name}))
            if not collected_entities:
                tools.append(("search_persons", {"query": target_name, "limit": 3}))

        elif is_relation_goal:
            # 动态反馈：若前序已发现新实体，优先查询该实体的关系网络
            if active_entities:
                for ent in active_entities[:2]:
                    tools.append(("get_person_relationships", {"person_name": ent}))
            else:
                tools.append(("search_persons", {"query": query[:20], "limit": 3}))

        elif is_archival_goal:
            keywords = intent.search_keywords if intent.search_keywords else [query[:10]]
            tools.append(("search_archival_evidence", {
                "query": query,
                "keywords": keywords[:3],
                "entities": active_entities[:2] if active_entities else None,
                "time_filter": resolved_year,
                "top_k": 4,
            }))

        elif is_person_goal:
            if active_entities:
                for ent in active_entities[:2]:
                    tools.append(("search_persons", {"query": ent, "limit": 3}))
            else:
                tools.append(("search_persons", {"query": query[:20], "limit": 3}))

        elif is_synthesis_goal:
            tools.append(("graph_rag_query", {
                "question": query,
                "time_filter": resolved_year,
                "top_k": 3,
            }))

        else:
            # 自适应反馈调度：检查当前证据池缺失项
            if not collected_sources:
                tools.append(("search_archival_evidence", {
                    "query": query,
                    "keywords": intent.search_keywords[:3],
                    "entities": active_entities[:2] if active_entities else None,
                    "time_filter": resolved_year,
                    "top_k": 4,
                }))
            elif active_entities and not collected_relationships:
                tools.append(("get_person_relationships", {"person_name": active_entities[0]}))
            else:
                tools.append(("graph_rag_query", {
                    "question": query,
                    "time_filter": resolved_year,
                    "top_k": 3,
                }))

        if not tools:
            tools.append(("search_archival_evidence", {
                "query": query,
                "keywords": intent.search_keywords[:3],
                "time_filter": resolved_year,
                "top_k": 3,
            }))

        if group_document_ids is not None:
            # Server-injected scope: added to every tool call so a tool can never
            # be executed without it.
            scoped_ids = sorted(int(value) for value in group_document_ids)
            tools = [
                (name, {**arguments, "group_document_ids": scoped_ids})
                for name, arguments in tools
            ]

        return tools[:2]

    def _accumulate_tool_results(
        self,
        tool_name: str,
        output: Any,
        sources: List[RagSourceEvidence],
        entities: List[RagEntityReference],
        relationships: List[RagRelationshipReference],
        seen_blocks: Set[int],
    ):
        """Parse structured tool output and populate context pools."""
        if not output:
            return

        if tool_name == "search_persons" and isinstance(output, list):
            for p in output:
                if not any(e.id == p["id"] for e in entities):
                    entities.append(RagEntityReference(
                        id=p["id"],
                        display_name=p["display_name"],
                        canonical_name_zh=p.get("canonical_name_zh"),
                        canonical_name_en=p.get("canonical_name_en"),
                        primary_role=p.get("primary_role"),
                        organization=p.get("organization"),
                        identity_status=p.get("identity_status"),
                        external_authorities_count=0,
                        external_records=[],
                    ))

        elif tool_name == "get_person_relationships" and isinstance(output, list):
            for r in output:
                # 检查关系证据是否经过真实数据库 OCR 文本核验
                is_valid_evidence = r.get("is_valid_primary_evidence", False)
                verified_txt = r.get("verified_quote_text")
                b_id = r.get("block_id")

                # 记录关系元数据 (始终保留在图谱知识层)
                if not any(
                    rel.source_person_name == r["source_person"] and rel.target_person_name == r["target_person"]
                    for rel in relationships
                ):
                    relationships.append(RagRelationshipReference(
                        source_person_name=r["source_person"],
                        target_person_name=r["target_person"],
                        relation_type=r["relation_type"],
                        confidence=r.get("confidence", 0.9),
                        time_description=r.get("time_description"),
                        claim_status="accepted" if is_valid_evidence else "unverified_secondary_metadata",
                        review_status="reviewed" if is_valid_evidence else "needs_verification_empty_block",
                        evidence_quote=verified_txt if is_valid_evidence else None,
                    ))

                # 严格防护：仅当关联 Block 在真实数据库中有实质 OCR 文本时，才允许作为一手文献纳入 sources
                if is_valid_evidence and b_id and b_id not in seen_blocks and verified_txt:
                    seen_blocks.add(b_id)
                    sources.append(RagSourceEvidence(
                        source_id=f"REL-{len(sources) + 1}",
                        document_id=r.get("document_id"),
                        document_name=r.get("document_name") or "Canton Factory Archives",
                        page_number=r.get("page_number"),
                        block_id=b_id,
                        quote_text=verified_txt,
                        relevance_score=0.95,
                        evidence_type="primary_archival",
                        temporal_coverage=r.get("temporal_coverage"),
                        temporal_provenance=r.get("temporal_provenance"),
                    ))

        elif tool_name in {"search_archival_evidence", "graph_rag_query"}:
            ev_list = output if isinstance(output, list) else output.get("sources", [])
            for item in ev_list:
                b_id = item.get("block_id")
                if b_id and b_id in seen_blocks:
                    continue
                quote_txt = (item.get("quote_text") or "").strip()
                # 过滤空白块引用
                if not quote_txt or len(quote_txt) < 5:
                    continue
                if b_id:
                    seen_blocks.add(b_id)
                sources.append(RagSourceEvidence(
                    source_id=f"DOC-{len(sources) + 1}",
                    document_id=item.get("document_id"),
                    document_name=item.get("document_name", "Canton Factory Archives"),
                    page_number=item.get("page_number"),
                    block_id=b_id or 0,
                    quote_text=quote_txt,
                    relevance_score=float(item.get("relevance_score", 0.8)),
                    matched_entities=item.get("matched_entities", []),
                    evidence_type=item.get("evidence_type", "primary_archival"),
                    temporal_coverage=item.get("temporal_coverage"),
                    temporal_provenance=item.get("temporal_provenance"),
                ))

    def _should_converge_early(
        self,
        intent: QueryIntentAnalysis,
        sources: List[RagSourceEvidence],
        relationships: List[RagRelationshipReference],
        steps_taken: int,
    ) -> bool:
        """Heuristic check whether collected evidence is sufficient to skip further steps."""
        if steps_taken < 2:
            return False
        if intent.query_type == "factual" and len(sources) >= 2:
            return True
        if intent.query_type == "relational" and len(relationships) >= 1 and len(sources) >= 1:
            return True
        if len(sources) >= 5 and len(relationships) >= 1:
            return True
        return False

    def _is_completely_nonexistent_or_out_of_bounds(
        self,
        query: str,
        intent: QueryIntentAnalysis,
    ) -> bool:
        """Anti-hallucination pre-check for completely unrecorded or impossible events."""
        lower_q = query.lower()
        unrecorded_markers = [
            "拿破仑", "napoleon",
            "华盛顿", "washington",
            "1840", "鸦片战争", "opium war",
            "火星", "核武器", "潜艇",
        ]
        if any(marker in lower_q for marker in unrecorded_markers):
            return True
        return False

    def _build_immediate_refusal_response(
        self,
        task_id: str,
        question: str,
        reason: str,
    ) -> AgentResearchResponse:
        """Construct refusal response with zero sources."""
        return AgentResearchResponse(
            task_id=task_id,
            question=question,
            final_answer=f"### 档案未见记录与存疑说明\n当前知识库没有足够证据，无法就此问题提供可信的历史答案。\n\n**门控原因**：{reason}\n请尝试查询东印度公司 1792–1793 年商馆大班（如 Henry Browne）、行商（如 Howqua 浩官、Puankhequa 潘启官）或茶叶与毛织品贸易等已收录史实。",
            categorization=AnswerCategorization(
                unresolved_aspects=[reason]
            ),
            trajectory=[
                AgentStepRecord(
                    step_number=1,
                    sub_goal="前置问题合理性与时空范围核验",
                    thought="核验提问实词与历史范围：问题提及脱离广州商馆 1792-1793 年史实范畴的内容，触发防幻觉硬门控阻断。",
                    tool_calls=[],
                    step_status="completed",
                )
            ],
            sources=[],
            entities=[],
            relationships=[],
            evidence_sufficient=False,
            budget_used=AgentBudgetInfo(
                steps_executed=1,
                max_steps=4,
                tool_calls_executed=0,
                max_tool_calls=6,
                elapsed_ms=1.0,
                budget_exhausted=False,
            ),
            warnings=["触发防幻觉硬门控前置拦截，已清空全部潜在虚构来源。"],
            generation=build_refusal_generation(),
        )

    async def _synthesize_answer(
        self,
        query: str,
        context_text: str,
        entities: List[RagEntityReference],
        relationships: List[RagRelationshipReference],
        sources: List[RagSourceEvidence],
    ) -> Tuple[str, AnswerCategorization, AnswerGeneration]:
        """Generate structured four-level synthesis, reporting model unavailability explicitly."""
        try:
            raw_text, _ = await kg_llm_client.call_extraction_llm(
                system_prompt=AGENT_SYNTHESIS_SYSTEM_PROMPT,
                user_content=f"历史研究课题：{query}\n\n{context_text}",
            )
        except Exception as exc:
            reason, message = classify_llm_failure(exc)
            detail = safe_failure_detail(exc)
            logger.warning("LLM generation failed in Agent synthesis (%s): %s", reason, detail)
            fallback_text = degraded_answer_prefix(reason) + self._build_deterministic_agent_synthesis(
                query, entities, relationships, sources
            )
            return (
                fallback_text,
                self._parse_categorization_from_markdown(fallback_text),
                build_deterministic_generation(reason, message, detail),
            )

        answer_text = (raw_text or "").strip()
        if not answer_text:
            message = "模型返回了空内容，无法据此生成回答。"
            logger.warning("LLM returned an empty Agent synthesis; using deterministic fallback.")
            fallback_text = degraded_answer_prefix(FAILURE_EMPTY_RESPONSE) + self._build_deterministic_agent_synthesis(
                query, entities, relationships, sources
            )
            return (
                fallback_text,
                self._parse_categorization_from_markdown(fallback_text),
                build_deterministic_generation(FAILURE_EMPTY_RESPONSE, message, None),
            )

        return (
            answer_text,
            self._parse_categorization_from_markdown(answer_text),
            build_model_generation(kg_llm_client.default_model),
        )

    def _build_deterministic_agent_synthesis(
        self,
        query: str,
        entities: List[RagEntityReference],
        relationships: List[RagRelationshipReference],
        sources: List[RagSourceEvidence],
    ) -> str:
        """Deterministic, grounded fallback format when LLM service is temporarily offline."""
        lines = []

        # 1. 档案直接记载
        lines.append("### 档案直接记载")
        if sources:
            for s in sources[:4]:
                page_str = f"第 {s.page_number} 页" if s.page_number else ""
                lines.append(f"- 根据 [{s.source_id}]《{s.document_name}》{page_str}（Block #{s.block_id}）原始记载：“{s.quote_text}”。")
        else:
            lines.append("- 1792–1793 年原始商馆日记中未检索到直接字面记录。")

        # 2. 图谱关系事实
        lines.append("\n### 图谱关系事实")
        if entities:
            ent_str = "、".join([f"{e.display_name}（{e.primary_role or '历史人物'}）" for e in entities[:3]])
            lines.append(f"- 经东印度公司人物知识库确认，课题涉及关键人物实体：{ent_str}。")
        if relationships:
            for r in relationships[:3]:
                ev_str = f"（证据引注：\"{r.evidence_quote}\"）" if r.evidence_quote else ""
                lines.append(f"- **{r.source_person_name}** 与 **{r.target_person_name}** 记录有 **{r.relation_type}** 往来{ev_str}。")

        # 3. 外部学术考据
        lines.append("\n### 外部学术考据")
        lines.append(
            "- 离线合成模式不生成外部学术著作引证：本次未调用 `get_external_authorities` 工具取回可核验的外部考据记录，"
            "因此不列举任何书目、出处或学者论断。"
        )

        # 4. 历史分析与综合推断
        lines.append("\n### 历史分析与综合推断")
        lines.append(
            "- 离线合成模式不产生新的历史推断：以上仅为已检索证据的原文摘录与图谱事实罗列，"
            "请在模型服务恢复后重新执行研究以获得完整的分层学术综述。"
        )

        # 5. 存疑说明
        lines.append("\n### 档案未见记录与存疑说明")
        lines.append("- 现有结论严格基于已数字化录入的 1792–1793 年卷宗，未检索到超出此范围的未收录事件。")

        return "\n".join(lines)

    @staticmethod
    def _parse_categorization_from_markdown(markdown_text: str) -> AnswerCategorization:
        """Extract categorized bullet points from structured markdown sections."""
        cat = AnswerCategorization()
        current_section = None

        for raw_line in markdown_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "档案直接记载" in line:
                current_section = "archival"
            elif "图谱关系事实" in line:
                current_section = "kg"
            elif "外部学术考据" in line:
                current_section = "external"
            elif "历史分析与综合推断" in line or "历史分析" in line:
                current_section = "inference"
            elif "档案未见记录" in line or "存疑说明" in line:
                current_section = "unresolved"
            elif line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.")):
                clean_bullet = line.lstrip("-*0123456789. ")
                if current_section == "archival":
                    cat.archival_facts.append(clean_bullet)
                elif current_section == "kg":
                    cat.kg_relationships.append(clean_bullet)
                elif current_section == "external":
                    cat.external_scholarship.append(clean_bullet)
                elif current_section == "inference":
                    cat.inferences_and_synthesis.append(clean_bullet)
                elif current_section == "unresolved":
                    cat.unresolved_aspects.append(clean_bullet)

        return cat


agent_runner = AgentRunner()
