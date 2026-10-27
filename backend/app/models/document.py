"""Document model."""

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, Integer, String, DateTime, Enum, Text
from sqlalchemy.orm import relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.page import Page
    from app.models.task import Task
    from app.models.export import Export


class DocumentStatus(str, enum.Enum):
    """Document processing status."""

    PENDING = "pending"
    PROCESSING = "processing"
    OCR_COMPLETED = "ocr_completed"
    POLISHING = "polishing"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentType(str, enum.Enum):
    """Document file type."""

    PDF = "pdf"
    IMAGE = "image"
    #: Word documents carry their text directly, so they never go through OCR.
    DOCX = "docx"


class Document(Base):
    """Document model representing an uploaded file."""

    __tablename__ = "documents"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_type = Column(Enum(DocumentType), nullable=False)
    file_size = Column(Integer, nullable=False)
    page_count = Column(Integer, default=0)
    full_markdown = Column(Text, nullable=True)
    status = Column(Enum(DocumentStatus), default=DocumentStatus.PENDING)
    error_message = Column(Text, nullable=True)
    #: SHA-256 of the uploaded bytes; the content identity used for de-duplication.
    content_hash = Column(String(64), nullable=True, index=True)
    archived_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    pages = relationship(
        "Page", back_populates="document", cascade="all, delete-orphan"
    )
    tasks = relationship(
        "Task", back_populates="document", cascade="all, delete-orphan"
    )
    exports = relationship(
        "Export", back_populates="document", cascade="all, delete-orphan"
    )
    archival_metadata = relationship(
        "ArchivalMetadata",
        back_populates="document",
        uselist=False,
        cascade="all, delete-orphan",
    )
    extraction_runs = relationship(
        "ExtractionRun", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Document(id={self.id}, filename={self.filename}, status={self.status})>"
        )
