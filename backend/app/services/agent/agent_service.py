"""High-level facade service for Autonomous Historical Research Agent."""
from typing import List, Optional, Set
from sqlalchemy.orm import Session

from app.schemas.agent import (
    AgentPlanResponse,
    AgentResearchRequest,
    AgentResearchResponse,
    ToolDefinition,
)
from app.services.agent.agent_runner import agent_runner
from app.services.agent.planner import agent_planner
from app.services.agent.tool_registry import tool_registry


class AgentService:
    """Entrypoint service for autonomous historical research orchestration."""

    async def run_research(
        self,
        db: Session,
        request: AgentResearchRequest,
        group_document_ids: Optional[Set[int]] = None,
    ) -> AgentResearchResponse:
        """Run complete autonomous research workflow."""
        return await agent_runner.run_research(
            db=db, request=request, group_document_ids=group_document_ids
        )

    async def preview_plan(
        self,
        db: Session,
        question: str,
        time_filter: Optional[int] = None,
    ) -> AgentPlanResponse:
        """Decompose question into proposed research steps without full execution."""
        return await agent_planner.create_plan(db=db, question=question, time_filter=time_filter)

    def get_available_tools(self) -> List[ToolDefinition]:
        """Return catalog of whitelisted tools and parameters."""
        return tool_registry.list_tool_definitions()


agent_service = AgentService()
