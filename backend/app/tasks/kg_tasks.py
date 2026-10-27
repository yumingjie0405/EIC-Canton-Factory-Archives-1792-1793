"""Celery asynchronous tasks for knowledge graph extraction."""
import asyncio
from typing import Any, Dict, List, Optional

from celery import shared_task

from app.core import logger
from app.database import SessionLocal
from app.models.block import Block
from app.models.page import Page
from app.services.kg.extraction_service import extraction_service


@shared_task(
    bind=True,
    name="app.tasks.kg_tasks.extract_block_entities_task",
    max_retries=3,
    default_retry_delay=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def extract_block_entities_task(
    self,
    block_id: int,
    overwrite_existing: bool = False,
) -> Dict[str, Any]:
    """Asynchronous Celery task to extract entities and relations from a single block."""
    task_id = self.request.id
    logger.info("Starting KG extraction task: task_id=%s block_id=%s", task_id, block_id)

    db = SessionLocal()
    try:
        # Run async service method in synchronous Celery worker thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                extraction_service.extract_from_block(
                    db=db,
                    block_id=block_id,
                    overwrite_existing=overwrite_existing,
                    task_id=task_id,
                )
            )
        finally:
            loop.close()

        logger.info(
            "Completed KG extraction task: task_id=%s block_id=%s entities=%d relationships=%d tokens=%d",
            task_id, block_id, response.entities_extracted, response.relationships_extracted, response.metrics.total_tokens
        )
        return response.model_dump()

    except Exception as exc:
        db.rollback()
        logger.error("Failed KG extraction task: task_id=%s block_id=%s error=%s", task_id, block_id, exc)
        raise self.retry(exc=exc)

    finally:
        db.close()


@shared_task(
    bind=True,
    name="app.tasks.kg_tasks.extract_page_entities_task",
    max_retries=2,
    default_retry_delay=10,
)
def extract_page_entities_task(
    self,
    page_id: int,
    overwrite_existing: bool = False,
) -> Dict[str, Any]:
    """Asynchronous task to iterate and extract all blocks on a single page."""
    task_id = self.request.id
    logger.info("Starting page-level KG extraction task: task_id=%s page_id=%s", task_id, page_id)

    db = SessionLocal()
    try:
        blocks = db.query(Block).filter_by(page_id=page_id).order_by(Block.block_index).all()
        block_ids = [b.id for b in blocks]

        results = []
        total_entities = 0
        total_relationships = 0
        total_tokens = 0

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for b_id in block_ids:
                resp = loop.run_until_complete(
                    extraction_service.extract_from_block(
                        db=db,
                        block_id=b_id,
                        overwrite_existing=overwrite_existing,
                        task_id=task_id,
                    )
                )
                results.append(resp.model_dump())
                total_entities += resp.entities_extracted
                total_relationships += resp.relationships_extracted
                total_tokens += resp.metrics.total_tokens
        finally:
            loop.close()

        logger.info(
            "Completed page KG extraction: page_id=%s blocks=%d entities=%d relationships=%d tokens=%d",
            page_id, len(block_ids), total_entities, total_relationships, total_tokens
        )
        return {
            "page_id": page_id,
            "blocks_processed": len(block_ids),
            "total_entities": total_entities,
            "total_relationships": total_relationships,
            "total_tokens": total_tokens,
            "block_results": results,
        }

    except Exception as exc:
        db.rollback()
        logger.error("Failed page KG extraction task: page_id=%s error=%s", page_id, exc)
        raise self.retry(exc=exc)

    finally:
        db.close()

@shared_task(
    bind=True,
    name="app.tasks.kg_tasks.run_kg_extraction_batches_task",
    max_retries=2,
    default_retry_delay=15,
)
def run_kg_extraction_batches_task(
    self,
    run_id: int,
    batch_size: int = 20,
    max_batches: int = 25,
):
    """Worker entry point for a persistent extraction run.

    Each invocation processes a bounded number of batches, then re-queues itself while
    the run is still active. Progress lives in ``kg_extraction_items``, so a lost worker
    never loses work and never re-extracts a block silently.
    """
    from app.services.kg.extraction_run_service import run_until_stopped

    db = SessionLocal()
    try:
        snapshot = run_until_stopped(
            db, run_id, batch_size=batch_size, max_batches=max_batches
        )
        if snapshot.get("status") in ("queued", "running"):
            run_kg_extraction_batches_task.delay(
                run_id, batch_size=batch_size, max_batches=max_batches
            )
        return snapshot
    finally:
        db.close()
