"""Schemas for the read-only archival block context view."""
from typing import List, Optional

from pydantic import BaseModel, Field


class BlockContextBlock(BaseModel):
    """One archival text block as stored in the database.

    Only transcriptions of the archival page are exposed here:
    ``raw_text`` is the untouched OCR output and ``corrected_text`` is the
    human/AI-corrected OCR. The model-derived fields (``modern_english``,
    ``translation_zh``) are deliberately **not** returned, so a researcher can
    never mistake generated prose for the archival original.
    """

    block_id: int
    page_id: int
    block_index: int
    block_label: Optional[str] = None
    raw_text: Optional[str] = None
    corrected_text: Optional[str] = None
    has_text: bool = Field(False, description="True when at least one transcription is non-empty")


class BlockContextResponse(BaseModel):
    """The cited block plus its same-page neighbours."""

    document_id: int
    document_name: str
    page_id: int
    page_number: Optional[int] = None
    block: BlockContextBlock
    previous: List[BlockContextBlock] = Field(default_factory=list)
    next: List[BlockContextBlock] = Field(default_factory=list)
    neighbour_scope: str = Field(
        "same_page",
        description="Neighbours are limited to the same page; page boundaries are never crossed",
    )
    notes: List[str] = Field(
        default_factory=list,
        description="Explicit statements about what this response does and does not contain",
    )
