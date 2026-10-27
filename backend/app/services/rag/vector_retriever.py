import math
import re
from typing import Dict, List, Optional, Set, Tuple
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.block import Block
from app.models.document import Document
from app.models.page import Page
from app.schemas.rag import RagSourceEvidence
from app.services.rag.temporal_resolver import temporal_resolver


class VectorRetriever:
    """Retrieves grounded archival text evidence across blocks, pages, and documents."""

    def retrieve_text_evidence(
        self,
        db: Session,
        query: str,
        keywords: List[str],
        entities: List[str],
        top_k: int = 5,
        min_relevance: float = 0.15,
        time_filter: Optional[int] = None,
        group_document_ids: Optional[Set[int]] = None,
    ) -> List[RagSourceEvidence]:
        """Search text blocks, compute TF-IDF vector relevance scores, and trace back to document & page.

        ``group_document_ids`` (server-side research scope): when provided, only
        documents inside that set can produce evidence. An empty set yields no
        evidence — it never degrades into an unscoped search.
        """
        tokens = self._tokenize(query)
        for kw in keywords:
            tokens.extend(self._tokenize(kw))
        for ent in entities:
            tokens.extend(self._tokenize(ent))

        query_terms = list(dict.fromkeys([t.lower() for t in tokens if len(t) >= 2]))
        if not query_terms:
            return []

        # 1. 结构化时空范围限定：明确区分严格单年份检索、跨年代比较和无明确年份的探索性查询
        is_cross_era = temporal_resolver.is_cross_era_query(query)
        detected_years = temporal_resolver.detect_query_years(query)

        allowed_doc_ids: Optional[Set[int]] = None
        query_mode = "exploratory"  # "strict_single_year" | "cross_era" | "exploratory"

        effective_year = time_filter
        if not effective_year and not is_cross_era:
            if len(detected_years) == 1:
                effective_year = detected_years[0]

        if effective_year and not is_cross_era:
            query_mode = "strict_single_year"
            # 严格单年份检索：候选文档必须可信地覆盖该目标年份（含覆盖该年份的跨年卷宗）
            matched_ids = temporal_resolver.get_document_ids_for_year(db, effective_year)
            allowed_doc_ids = matched_ids if matched_ids else set()
        elif is_cross_era:
            query_mode = "cross_era"
            # 跨年代比较模式：若查询出现多个明确年份，允许涵盖任意目标时期的档案
            if detected_years:
                allowed_doc_ids = set()
                for y in detected_years:
                    allowed_doc_ids.update(temporal_resolver.get_document_ids_for_year(db, y))
        else:
            query_mode = "exploratory"
            # 探索性查询：不设年份硬约束，各结果保留原生年代或未确定年代标记
            allowed_doc_ids = None

        # 1b. 研究组范围（服务端注入）：与年份约束取交集；空交集即空结果
        if group_document_ids is not None:
            scoped_ids = set(group_document_ids)
            if not scoped_ids:
                return []
            if query_mode == "exploratory":
                query_mode = "group_scoped"
                allowed_doc_ids = scoped_ids
            else:
                allowed_doc_ids = (allowed_doc_ids or set()).intersection(scoped_ids)
                if not allowed_doc_ids:
                    return []

        # 2. 粗排筛选：使用 SQL 快速捞出包含查询词或实体的候选 blocks (优先选择有实质内容的 blocks)
        conditions = []
        for term in query_terms:
            if len(term) >= 3 and term not in ["the", "and", "for", "with"]:
                term_pat = f"%{term}%"
                conditions.append(Block.ocr_text_corrected.ilike(term_pat))
                conditions.append(Block.ocr_text_raw.ilike(term_pat))
                conditions.append(Block.modern_english.ilike(term_pat))

        base_query = db.query(Block).options(joinedload(Block.page).joinedload(Page.document))

        # 严格年代隔离与候选池构建
        if query_mode == "strict_single_year":
            # 严格单年份：必须严格来自覆盖该年份的文档，绝对禁止仅凭正文出现年份字样放行其他年代或未知年代文档
            if not allowed_doc_ids:
                return []
            base_query = base_query.join(Block.page).filter(Page.document_id.in_(allowed_doc_ids))
        elif query_mode == "cross_era" and allowed_doc_ids:
            # 跨年代比较：来自任意涉及的合法年代文档
            base_query = base_query.join(Block.page).filter(Page.document_id.in_(allowed_doc_ids))
        elif query_mode == "group_scoped":
            # 研究组范围：只允许组内文档（无年份约束时不回退到全库）
            base_query = base_query.join(Block.page).filter(Page.document_id.in_(allowed_doc_ids))

        if conditions:
            # 限制候选池在 150 条高质量命中块以内
            blocks = base_query.filter(or_(*conditions)).limit(150).all()
        else:
            blocks = (
                base_query
                .filter(
                    (Block.ocr_text_corrected.isnot(None))
                    | (Block.ocr_text_raw.isnot(None))
                    | (Block.modern_english.isnot(None))
                )
                .limit(100)
                .all()
            )

        if not blocks:
            return []

        # 3. 向量空间模型打分 (TF-IDF Cosine Similarity)
        scored_blocks: List[Tuple[Block, float, List[str]]] = []

        # 计算 IDF
        doc_count = len(blocks)
        doc_freq: Dict[str, int] = {t: 0 for t in query_terms}
        for b in blocks:
            text = self._get_best_text(b).lower()
            for t in query_terms:
                if t in text:
                    doc_freq[t] += 1

        idf: Dict[str, float] = {}
        for t in query_terms:
            df = doc_freq[t]
            idf[t] = math.log((doc_count + 1) / (df + 1)) + 1.0

        for b in blocks:
            text = self._get_best_text(b)
            if not text or len(text.strip()) < 5:
                continue

            score, matched_terms = self._compute_relevance(text, query_terms, idf, entities)
            if score >= min_relevance:
                scored_blocks.append((b, score, matched_terms))

        # 按相似度降序排序
        scored_blocks.sort(key=lambda x: x[1], reverse=True)
        top_blocks = scored_blocks[:top_k]

        # 4. 组装来源引证结构 (绑定真实文档名与年代元数据)
        evidence_list: List[RagSourceEvidence] = []
        for idx, (b, score, matched_terms) in enumerate(top_blocks, start=1):
            doc_name = "东印度公司广州商馆档案卷宗 (IOR/G/12)"
            page_no = None
            doc_id = None
            temporal_coverage = None
            temporal_provenance = None

            if b.page:
                page_no = b.page.page_number
                if b.page.document:
                    doc_id = b.page.document.id
                    orig_filename = b.page.document.original_filename
                    doc_name = orig_filename or b.page.document.filename or doc_name
                    t_info = temporal_resolver.resolve_document_temporal_info(doc_id, orig_filename)
                    temporal_coverage = f"{t_info['temporal_label']} [{t_info['provenance']}]"
                    temporal_provenance = t_info["provenance"]

            raw_excerpt = self._get_best_text(b)
            if not raw_excerpt or len(raw_excerpt.strip()) < 5:
                continue

            # Truncate clean quote
            clean_quote = raw_excerpt.strip().replace("\n", " ")
            if len(clean_quote) > 300:
                clean_quote = clean_quote[:297] + "..."

            evidence_list.append(
                RagSourceEvidence(
                    source_id=f"档案文献证据 #{idx}",
                    document_id=doc_id,
                    document_name=doc_name,
                    page_number=page_no,
                    block_id=b.id,
                    quote_text=clean_quote,
                    relevance_score=round(min(1.0, score), 4),
                    matched_entities=matched_terms,
                    evidence_type="primary_archival",
                    temporal_coverage=temporal_coverage,
                    temporal_provenance=temporal_provenance,
                )
            )

        return evidence_list

    def _get_best_text(self, block: Block) -> str:
        """Pick the highest quality text representation available in block."""
        if block.ocr_text_corrected and len(block.ocr_text_corrected.strip()) > 3:
            return block.ocr_text_corrected
        if block.modern_english and len(block.modern_english.strip()) > 3:
            return block.modern_english
        if block.translation_zh and len(block.translation_zh.strip()) > 3:
            return block.translation_zh
        return block.ocr_text_raw or ""

    def _tokenize(self, text: str) -> List[str]:
        """Split text into alphanumeric and CJK word tokens."""
        return re.findall(r"[\u4e00-\u9fa5]+|[a-zA-Z0-9]+", text)

    def _compute_relevance(
        self,
        doc_text: str,
        query_terms: List[str],
        idf: Dict[str, float],
        entities: List[str],
    ) -> Tuple[float, List[str]]:
        """Compute TF-IDF similarity score with historical entity bonus."""
        text_lower = doc_text.lower()
        matched: List[str] = []
        score = 0.0

        for t in query_terms:
            count = text_lower.count(t)
            if count > 0:
                # Term Frequency with sublinear scaling
                tf = 1.0 + math.log(count)
                score += tf * idf.get(t, 1.0)
                matched.append(t)

        # Distinctive topical commodities & terms get a strong relevance bonus
        topical_terms = {"woolen", "woolens", "tea", "silk", "silver", "cloth", "camlets", "contract", "contracts"}
        topical_boost = 0.0
        for t in query_terms:
            if t.lower() in topical_terms and t.lower() in text_lower:
                topical_boost += 3.0

        # Entity bonus: If an explicit historical person/org is named in text, boost relevance
        entity_boost = 0.0
        for ent in entities:
            if len(ent) >= 3 and ent.lower() in text_lower:
                entity_boost += 2.0
                if ent not in matched:
                    matched.append(ent)

        normalized_score = (score + entity_boost + topical_boost) / (1.0 + math.sqrt(len(doc_text) / 50.0))
        # Sigmoid scale into 0.0 ~ 1.0
        scaled_score = 2.0 / (1.0 + math.exp(-normalized_score * 0.5)) - 1.0
        return max(0.0, scaled_score), matched


vector_retriever = VectorRetriever()
