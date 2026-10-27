"""Graph + Vector context fusion and prompt builder for Graph RAG."""
from typing import List, Tuple
from app.schemas.rag import (
    QueryIntentAnalysis,
    RagEntityReference,
    RagRelationshipReference,
    RagSourceEvidence,
)


class ContextBuilder:
    """Fuses structured graph knowledge with unstructured archival text chunks into an anti-hallucination context."""

    def fuse_and_build_context(
        self,
        query: str,
        intent: QueryIntentAnalysis,
        entities: List[RagEntityReference],
        relationships: List[RagRelationshipReference],
        graph_evidences: List[RagSourceEvidence],
        text_evidences: List[RagSourceEvidence],
    ) -> Tuple[str, List[RagSourceEvidence], bool]:
        """Merge graph facts and text excerpts, assign citations, and construct prompt context."""
        # 1. 证据去重与统一标号
        unified_sources: List[RagSourceEvidence] = []
        seen_block_ids = set()
        citation_index = 1

        # 优先加入图谱关系所锚定的强证据
        for ev in graph_evidences:
            if ev.block_id not in seen_block_ids:
                ev_copy = ev.model_copy()
                ev_copy.source_id = f"证据 #{citation_index}"
                unified_sources.append(ev_copy)
                seen_block_ids.add(ev.block_id)
                citation_index += 1

        # 加入向量检索发现的文本证据
        for ev in text_evidences:
            if ev.block_id not in seen_block_ids:
                ev_copy = ev.model_copy()
                ev_copy.source_id = f"证据 #{citation_index}"
                unified_sources.append(ev_copy)
                seen_block_ids.add(ev.block_id)
                citation_index += 1

        # 2. 评估证据充分性 (Evidence Sufficiency Gate)
        has_real_person = any(e for e in entities if e.primary_role or str(e.identity_status).lower() == "accepted")
        has_relationships = len(relationships) > 0
        has_sources = len(unified_sources) > 0

        if not entities and not has_sources:
            is_sufficient = False
        elif not has_real_person and not has_relationships:
            # 当缺乏图谱实体与关系支撑时，文本证据必须具备实质核心相关性
            max_rel = max([s.relevance_score for s in unified_sources], default=0.0)
            if max_rel < 0.25:
                is_sufficient = False
            else:
                # 排除通用背景语境词后，检查是否命中了核心疑问词
                background_stop = {
                    "canton", "factory", "factories", "广州", "商馆", "十三行", "eic",
                    "company", "east india company", "merchant", "merchants", "hong merchant",
                    "co-hong", "cohong", "公行", "行商", "东印度公司", "商谈", "会议",
                    "consultation", "consultations", "广州十三行商馆", "十三行商馆"
                }
                core_terms = [k.lower() for k in intent.search_keywords if k.lower() not in background_stop and len(k) >= 2]
                matched_any_core = any(
                    any(c in s.quote_text.lower() for c in core_terms)
                    for s in unified_sources
                ) if core_terms else False
                is_sufficient = matched_any_core
        else:
            is_sufficient = True

        # 如果证据不足，清空来源避免模型产生虚假引用
        if not is_sufficient:
            unified_sources = []

        # 3. 组织结构化 Context 字符串
        lines: List[str] = []
        lines.append("【用户研究提问】")
        lines.append(f"{query}\n")

        # 知识图谱信息部分
        if entities or relationships:
            lines.append("【历史知识图谱结构化事实 (Knowledge Graph Facts)】")
            if entities:
                lines.append("• 涉及历史人物与考证：")
                for p in entities:
                    auth_info = f"；外部考证记录：{', '.join(p.external_records)}" if p.external_records else ""
                    role_info = f" [{p.primary_role or '历史人物'}]" if p.primary_role else ""
                    org_info = f" 所属：{p.organization}" if p.organization else ""
                    lines.append(f"  - {p.display_name}{role_info}{org_info} (状态: {p.identity_status}){auth_info}")

            if relationships:
                lines.append("\n• 关联历史关系事实：")
                for r in relationships:
                    time_info = f" 时间：{r.time_description}" if r.time_description else ""
                    conf_info = f" (置信度: {r.confidence})" if r.confidence else ""
                    if r.evidence_quote:
                        ev_info = f" —— 档案一手记录：\"{r.evidence_quote}\""
                    else:
                        ev_info = " [注：此条目为知识图谱结构化记录，未关联到经验证的一手原卷文本块]"
                    lines.append(
                        f"  - {r.source_person_name} ↔ {r.target_person_name} | 类型: {r.relation_type}{time_info}{conf_info}{ev_info}"
                    )
            lines.append("")

        # 原始档案证据部分
        if unified_sources:
            lines.append("【原始档案文本证据 (Archival Primary Sources)】")
            for src in unified_sources:
                page_str = f" 第 {src.page_number} 页" if src.page_number else ""
                temp_str = f"（卷宗年代: {src.temporal_coverage}）" if src.temporal_coverage else ""
                lines.append(
                    f"[{src.source_id}] 来源文献：《{src.document_name}》{temp_str}{page_str} (文本块 Block #{src.block_id})"
                )
                lines.append(f"原文引录：\"{src.quote_text}\"\n")
        else:
            lines.append("【原始档案文本证据】：暂无直接匹配的文本块引用。\n")

        # 严格约束要求
        lines.append("【史学研究生成约束】")
        lines.append("1. 必须完全基于上述【历史知识图谱】与【原始档案文本证据】作答，严禁编造未在证据中出现的年代、人名或事实；")
        lines.append("2. 针对每一个论断，必须在句子后显式注明引用来源标记（例如：根据商馆会议记录，布朗向浩官支付了货款[证据 #1]）；")
        lines.append("3. 严格年代对齐：所引用的文献年代必须与问题限定的年份吻合，严禁将其他年份材料当成目标年份的直接记载；")
        lines.append("4. 禁止无据套用职务：档案若仅记录姓名（如 Henry Browne Esqr），严禁在档案直接记载中断言其具体官职；")
        lines.append("5. 如果上述图谱事实与档案证据无法支持回答用户的问题，必须明确回答：“当前知识库没有足够证据，无法得出确定结论。”，严禁强行推断。")

        context_text = "\n".join(lines)
        return context_text, unified_sources, is_sufficient


context_builder = ContextBuilder()
