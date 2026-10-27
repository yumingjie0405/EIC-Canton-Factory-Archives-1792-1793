"""Query understanding and intent analysis module for Graph RAG."""
import re
from typing import List, Optional, Set
from sqlalchemy.orm import Session

from app.models.kg.person import Person
from app.schemas.rag import QueryIntentAnalysis
from app.services.rag.temporal_resolver import YEAR_PATTERN


# Historical canonical organizations & keywords in 18th-century Canton
CANTON_ORGANIZATIONS = [
    "东印度公司",
    "英国东印度公司",
    "East India Company",
    "EIC",
    "十三行",
    "广州商馆",
    "Canton Factory",
    "公行",
    "Cohong",
    "怡和行",
    "同文行",
    "广利行",
    "丰泰行",
    "Select Committee",
    "商馆委员会",
    "粤海关",
]

# Historical cross-lingual lexicon mapping for 18th-century EIC archives
HISTORICAL_BILINGUAL_MAP = {
    "毛织品": ["woolen", "woolens", "cloth"],
    "呢绒": ["woolen", "woolens", "camlets"],
    "茶叶": ["tea", "bohea", "congou", "hyson"],
    "生丝": ["silk", "raw silk"],
    "丝绸": ["silk"],
    "白银": ["silver", "dollars"],
    "贸易": ["trade", "commerce"],
    "合同": ["contract", "contracts"],
    "行商": ["merchant", "merchants", "hong merchant"],
    "公行": ["co-hong", "cohong"],
    "商馆": ["factory", "factories"],
    "会议": ["consultation", "consultations"],
    "大班": ["supercargo"],
    "东印度公司": ["east india company", "company"],
}


class QueryAnalyzer:
    """Analyzes natural language queries to extract target entities, time bounds, and query intent."""

    def analyze_query(self, query: str, db: Optional[Session] = None) -> QueryIntentAnalysis:
        cleaned_query = query.strip()
        entities = self._extract_entities(cleaned_query, db)
        time_filter = self._extract_year(cleaned_query)
        spatial_filter = self._extract_location(cleaned_query)
        query_type = self._classify_intent(cleaned_query, entities)
        keywords = self._extract_keywords(cleaned_query, entities)

        return QueryIntentAnalysis(
            raw_query=cleaned_query,
            query_type=query_type,
            entities=entities,
            time_filter=time_filter,
            spatial_filter=spatial_filter,
            search_keywords=keywords,
        )

    def _extract_year(self, query: str) -> Optional[int]:
        """Extract explicit 18th-19th century year mentioned in query."""
        match = YEAR_PATTERN.search(query or "")
        if match:
            return int(match.group(1))
        return None

    def _extract_location(self, query: str) -> Optional[str]:
        """Extract location filter."""
        locations = ["广州", "Canton", "澳门", "Macao", "北京", "Peking", "黄埔", "Whampoa", "伦敦", "London"]
        for loc in locations:
            if loc.lower() in query.lower():
                return loc
        return None

    def _classify_intent(self, query: str, entities: List[str]) -> str:
        """Classify question into relational, factual, or historical_analysis."""
        q_lower = query.lower()

        # 关系查询
        if any(w in q_lower for w in ["关系", "往来", "交往", "联系", "互动", "relationship", "relation", "between"]):
            return "relational"

        # 历史原因与深度分析
        if any(w in q_lower for w in ["为什么", "为何", "原因", "变化", "影响", "制度", "分析", "演化", "背景", "why", "reason", "impact"]):
            return "historical_analysis"

        # 事实与生平查询
        if any(w in q_lower for w in ["是谁", "何人", "身份", "简介", "生平", "生卒", "字号", "行商", "who is", "who was", "biography"]):
            return "factual"

        if len(entities) >= 2:
            return "relational"
        elif len(entities) == 1:
            return "factual"

        return "historical_analysis"

    def _extract_entities(self, query: str, db: Optional[Session] = None) -> List[str]:
        """Extract historical entities using database gazetteer and known priors."""
        found_entities: Set[str] = set()

        # 1. 匹配组织/机构
        for org in CANTON_ORGANIZATIONS:
            if org.lower() in query.lower():
                found_entities.add(org)

        # 2. 如果提供了 db，匹配数据库中已确认的人物姓名和别名
        if db:
            persons = db.query(Person).all()
            for p in persons:
                candidates = [p.display_name, p.canonical_name_zh, p.canonical_name_en]
                if p.aliases:
                    if isinstance(p.aliases, list):
                        candidates.extend(p.aliases)
                    elif isinstance(p.aliases, dict):
                        candidates.extend(p.aliases.values())

                for name in candidates:
                    if name and len(name.strip()) >= 2:
                        name_clean = name.strip()
                        # Case-insensitive substring match
                        if name_clean.lower() in query.lower():
                            found_entities.add(p.display_name)
                            break

        # 3. 常见历史人物常见别称启发式规则补充
        priors = {
            "浩官": "Howqua (Wu Bingjian)",
            "howqua": "Howqua (Wu Bingjian)",
            "伍秉鉴": "Howqua (Wu Bingjian)",
            "伍国莹": "Howqua I (Wu Guoying)",
            "布朗": "Henry Browne",
            "browne": "Henry Browne",
            "马戛尔尼": "George Lord Macartney",
            "macartney": "George Lord Macartney",
            "潘致祥": "Puankhequa (Pan Zhixiang)",
            "puankhequa": "Puankhequa (Pan Zhixiang)",
            "蔡昭复": "Munqua (Cai Zhaofu)",
            "munqua": "Munqua (Cai Zhaofu)",
            "卢观恒": "Mowqua (Lu Guanheng)",
            "mowqua": "Mowqua (Lu Guanheng)",
        }
        for k, v in priors.items():
            if k in query.lower():
                found_entities.add(v)

        return sorted(list(found_entities))

    def _extract_keywords(self, query: str, entities: List[str]) -> List[str]:
        """Extract search keywords by removing stop words and expanding cross-lingual domain terms."""
        stop_words = {
            "什么", "是谁", "为何", "怎么", "在", "的", "和", "与", "了", "有", "是", "请问",
            "对", "如何", "之间", "在1792", "在1793", "是如何", "开展", "开展的", "情况",
            "进行", "进行了", "发生", "做过", "干了", "去过",
            "what", "is", "was", "the", "and", "between",
            "in", "of", "to", "for", "with", "?", "？", "！", "!", ",", "，", "。", "."
        }

        keywords: List[str] = []

        # 1. 匹配中英历史领域核心词汇并注入对应英文/中文术语
        for zh_term, en_terms in HISTORICAL_BILINGUAL_MAP.items():
            if zh_term in query:
                keywords.append(zh_term)
                keywords.extend(en_terms)

        # 2. 提取英文词汇与数字年份
        en_tokens = re.findall(r"[a-zA-Z0-9]+", query)
        for t in en_tokens:
            if t.lower() not in stop_words and len(t) >= 2:
                keywords.append(t)

        # 3. 提取用户提问中的专有历史名词（过滤语气词与疑问助词）
        cleaned = re.sub(r"(什么|是谁|为何|怎么|是如何|开展的|之间|进行了|在|的|与|和|？|\?|！|!|了|有|是)", " ", query)
        for part in cleaned.split():
            part = part.strip()
            if len(part) >= 2 and part not in stop_words and part not in keywords:
                keywords.append(part)

        # 4. 添加识别出的实体
        for ent in entities:
            if ent not in keywords:
                keywords.append(ent)

        return list(dict.fromkeys(keywords))


query_analyzer = QueryAnalyzer()
