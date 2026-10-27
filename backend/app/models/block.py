"""Block model."""
import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, Integer, String, DateTime, ForeignKey, Text, Float, Enum, JSON
from sqlalchemy.orm import relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.page import Page
    from app.models.kg.mention import PersonMention


class ReviewStatus(str, enum.Enum):
    """Block review status."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class Block(Base):
    """Block model representing an OCR region in a page."""

    __tablename__ = "blocks"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)
    page_id = Column(Integer, ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True)
    block_index = Column(Integer, nullable=False)
    block_label = Column(String(100), nullable=True)
    bbox = Column(JSON, nullable=False)  # [x1, y1, x2, y2]
    confidence = Column(Float, nullable=True)

    retired = Column(Boolean, nullable=False, default=False, server_default="false", index=True)

    # OCR and processed text
    ocr_text_raw = Column(Text, nullable=True)
    ocr_text_corrected = Column(Text, nullable=True)
    ocr_correction_source_text = Column(Text, nullable=True)
    modern_english = Column(Text, nullable=True)
    translation_zh = Column(Text, nullable=True)

    # Review status
    review_status = Column(Enum(ReviewStatus), default=ReviewStatus.PENDING)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    page = relationship("Page", back_populates="blocks")
    mentions = relationship("PersonMention", back_populates="block")
    source_unit_spans = relationship("SourceUnitSpan", back_populates="block", cascade="all, delete-orphan")
    text_revisions = relationship("TextRevision", back_populates="block", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Block(id={self.id}, page_id={self.page_id}, label={self.block_label})>"
