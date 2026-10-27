"""API routers module."""
from app.api.routers.documents import router as documents_router
from app.api.routers.pages import router as pages_router
from app.api.routers.blocks import router as blocks_router
from app.api.routers.tasks import router as tasks_router
from app.api.routers.exports import router as exports_router
from app.api.routers.kg import router as kg_router
from app.api.routers.rag import router as rag_router
from app.api.routers.agent import router as agent_router
from app.api.routers.research import router as research_router
from app.api.routers.groups import router as groups_router

__all__ = [
    "documents_router",
    "pages_router",
    "blocks_router",
    "tasks_router",
    "exports_router",
    "kg_router",
    "rag_router",
    "agent_router",
    "research_router",
    "groups_router",
]

