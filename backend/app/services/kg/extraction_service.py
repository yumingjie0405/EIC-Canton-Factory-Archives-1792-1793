"""Extraction service orchestrating LLM calls, parsing, and database storage."""
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core import logger, AppException
from app.models.block import Block
from app.models.page import Page
from app.models.document import Document
from app.schemas.kg_extraction_schema import (
    BlockExtractionResponse,
    ExtractionTaskMetrics,
    KGExtractionResult,
)
from app.services.kg.llm_client import kg_llm_client
from app.services.kg.relation_parser import relation_parser
from app.services.kg.kg_writer import kg_writer


class ExtractionService:
    """Service orchestrating end-to-end historical entity and relation extraction from blocks."""

    def __init__(self):
        self.prompts_dir = Path(__file__).parent.parent.parent / "prompts"

    def _load_prompt_template(self) -> str:
        """Load prompt template from file, with fallback."""
        prompt_path = self.prompts_dir / "kg_extraction_prompt.txt"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")

        # In-code fallback if file not found
        return (
            "You are an expert digital humanities assistant extracting historical persons and relationships "
            "from British East India Company Canton Factory archives (1792-1793). "
            "Output JSON only with keys 'entities' and 'relationships'."
        )

    async def extract_from_text(
        self,
        text_content: str,
        context_info: Optional[str] = None,
        task_id: Optional[str] = None,
        block_id: Optional[int] = None,
        page_id: Optional[int] = None,
        document_id: Optional[int] = None,
    ) -> Tuple[KGExtractionResult, ExtractionTaskMetrics]:
        """Send raw historical text to LLM and parse into validated KGExtractionResult."""
        system_prompt = self._load_prompt_template()

        user_content = f"Historical Archival Text to analyze:\n\"\"\"\n{text_content.strip()}\n\"\"\""
        if context_info:
            user_content = f"Context Metadata:\n{context_info}\n\n{user_content}"

        raw_output, metrics = await kg_llm_client.call_extraction_llm(
            system_prompt=system_prompt,
            user_content=user_content,
            task_id=task_id,
            block_id=block_id,
            page_id=page_id,
            document_id=document_id,
        )

        extraction_result = relation_parser.parse_extraction_json(raw_output)

        metrics.entities_count = len(extraction_result.entities)
        metrics.relationships_count = len(extraction_result.relationships)

        return extraction_result, metrics

    async def extract_from_block(
        self,
        db: Session,
        block_id: int,
        overwrite_existing: bool = False,
        task_id: Optional[str] = None,
        run_id: Optional[int] = None,
        source_kind: Optional[str] = None,
    ) -> BlockExtractionResponse:
        """Extract entities and relations from a single block and commit to database."""
        block = db.query(Block).filter_by(id=block_id).first()
        if not block:
            raise AppException(f"Block with id {block_id} not found", code="BLOCK_NOT_FOUND")

        # Text selection: corrected text takes precedence, fallback to raw OCR
        text_to_extract = (block.ocr_text_corrected or block.ocr_text_raw or "").strip()
        if not text_to_extract:
            logger.info("Block %s has empty text, skipping extraction", block_id)
            metrics = ExtractionTaskMetrics(
                task_id=task_id,
                block_id=block_id,
                model_name=kg_llm_client.default_model,
                latency_ms=0.0,
            )
            return BlockExtractionResponse(
                block_id=block_id,
                entities_extracted=0,
                relationships_extracted=0,
                entities=[],
                relationships=[],
                metrics=metrics,
            )

        # Context collection
        context_parts = []
        page = db.query(Page).filter_by(id=block.page_id).first()
        if page:
            context_parts.append(f"Page Number: {page.page_number}")
            doc = db.query(Document).filter_by(id=page.document_id).first()
            if doc:
                context_parts.append(f"Document: {doc.original_filename}")

        context_str = " | ".join(context_parts) if context_parts else None

        # Execute extraction
        result, metrics = await self.extract_from_text(
            text_content=text_to_extract,
            context_info=context_str,
            task_id=task_id,
            block_id=block_id,
            page_id=page.id if page else None,
            document_id=doc.id if (page and doc) else None,
        )

        # Write to database
        created_mentions, created_relationships = kg_writer.write_extraction_result(
            db=db,
            block_id=block_id,
            result=result,
            overwrite_existing=overwrite_existing,
            run_id=run_id,
            source_kind=source_kind,
        )
        db.commit()

        logger.info(
            "Extraction completed for block %s: %d entities, %d relationships, tokens=%d",
            block_id, len(result.entities), len(result.relationships), metrics.total_tokens
        )

        return BlockExtractionResponse(
            block_id=block_id,
            entities_extracted=len(result.entities),
            relationships_extracted=len(result.relationships),
            entities=result.entities,
            relationships=result.relationships,
            metrics=metrics,
        )


extraction_service = ExtractionService()
