"""Research planning and sub-goal decomposition module for AI Agent."""
import json
from typing import List, Optional
from sqlalchemy.orm import Session

from app.core import logger
from app.schemas.agent import AgentPlanResponse
from app.services.rag import query_analyzer
from app.services.kg.llm_client import kg_llm_client


PLANNER_SYSTEM_PROMPT = """你是一个专门研究 18 世纪末英国东印度公司广州商馆（Canton Factory 1792–1793）档案的资深数字人文研究助理。
你的任务是将用户的复杂历史研究问题拆解为 2 到 4 个清晰、有序、可独立检查的研究步骤。
请输出严格的 JSON 格式，包含字段：
- "steps": 字符串数组，每个元素为一个研究步骤（如“第一步：查询...”，“第二步：检索...”）
- "target_entities": 识别到的重点历史人物或机构数组
- "rationale": 简短的规划理由

只输出 JSON，不要添加多余标记或解释。"""


class AgentPlanner:
    """Plans and decomposes complex historical research tasks."""

    async def create_plan(
        self,
        db: Session,
        question: str,
        time_filter: Optional[int] = None,
    ) -> AgentPlanResponse:
        """Decompose question into structured research steps."""
        intent = query_analyzer.analyze_query(question, db=db)
        resolved_time = time_filter or intent.time_filter
        entities = intent.entities

        # 尝试调用 LLM 生成研究规划，若异常则降级使用确定性规则规划
        steps = await self._plan_via_llm_or_fallback(
            question=question,
            intent_type=intent.query_type,
            entities=entities,
            time_filter=resolved_time,
        )

        return AgentPlanResponse(
            question=question,
            proposed_steps=steps,
            target_entities=entities,
            time_filter=resolved_time,
            estimated_tool_calls=len(steps) + 1,
        )

    async def _plan_via_llm_or_fallback(
        self,
        question: str,
        intent_type: str,
        entities: List[str],
        time_filter: Optional[int],
    ) -> List[str]:
        """Try LLM plan decomposition; smoothly fallback to deterministic steps on failure."""
        user_prompt = f"研究问题：{question}\n初步分析：意图类型={intent_type}，实体={entities}，时间约束={time_filter or '未指定'}"
        try:
            raw_response, _ = await kg_llm_client.call_extraction_llm(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_content=user_prompt,
            )
            # 解析 JSON 结果
            clean_text = raw_response.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            if clean_text.startswith("```"):
                clean_text = clean_text[3:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]

            data = json.loads(clean_text.strip())
            extracted_steps = data.get("steps", [])
            if isinstance(extracted_steps, list) and len(extracted_steps) >= 2:
                return [str(s).strip() for s in extracted_steps[:4]]
        except Exception as exc:
            logger.info("LLM dynamic planning skipped/fallback (%s), applying heuristic decomposition.", exc)

        return self._heuristic_steps(intent_type, entities, time_filter)

    @staticmethod
    def _heuristic_steps(
        intent_type: str,
        entities: List[str],
        time_filter: Optional[int],
    ) -> List[str]:
        """Deterministic research steps tailored to historical inquiry type."""
        entity_str = "、".join(entities) if entities else "相关核心历史人物"
        year_str = f"{time_filter} 年" if time_filter else "1792-1793 年"

        if intent_type == "relational":
            return [
                f"第一步：检索 {entity_str} 的人物档案、所属行商/商馆委员会背景及别名",
                f"第二步：查询图谱中记录的商业、外交与信贷往来事实及已审定的关联引据",
                f"第三步：在 {year_str} 档案文本块中深度召回合同商谈与会议记录（Consultation）原始引文",
                "第四步：融合各方证据，核实史实一致性并生成四级可溯源的综合报告",
            ]
        elif intent_type == "factual":
            return [
                f"第一步：在知识图谱人物库中精确定位 {entity_str} 的身份与任职时间",
                f"第二步：检索梁嘉彬《广东十三行考》等外部学术考据对该人物的记载",
                f"第三步：调取东印度公司广州商馆日记中涉及该人物的原始活动文献",
                "第四步：比对内源档案与外源史料，生成事实考据结论",
            ]
        else:
            return [
                f"第一步：意图与时空范围核验，锁定 {year_str} 贸易与外交核心实词",
                f"第二步：在知识图谱中检索相关人物网络与已知公行/商馆往来记录",
                f"第三步：跨 5 万余文本块执行向量与关键词精排，召回核心文献证据",
                "第四步：执行证据充分性门控评估，分层生成学术研究报告",
            ]


agent_planner = AgentPlanner()
