"""Models module exports."""
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.page import Page
from app.models.block import Block, ReviewStatus
from app.models.task import Task, TaskType, TaskStatus
from app.models.export import Export, ExportFormat, ExportStatus
from app.models.research import (
    ALEMBIC_MANAGED_TABLES,
    ResearchMode,
    ResearchSession,
    ResearchStatus,
)
from app.models.research_group import (
    ALEMBIC_MANAGED_GROUP_TABLES,
    GroupImportStatus,
    ResearchGroup,
    ResearchGroupDocument,
)
from app.models.kg import (
    AuthoritySource,
    ClaimStatus,
    Directionality,
    EvidenceRole,
    ExternalAuthority,
    IdentityStatus,
    KGBlockVersion,
    KGGraphSnapshot,
    KGReviewAudit,
    MentionType,
    Person,
    PersonMention,
    RelationType,
    Relationship,
    RelationshipEvidence,
    ResolutionStatus,
    ReviewStatusKG,
    SNAGraphMetric,
    SourceKind,
    TokenSource,
)

# Knowledge workbench 2.0 models
from app.models.archival_metadata import ArchivalMetadata, CompletenessStatus
from app.models.source_unit import SourceUnit, SourceUnitType, SegmentationStatus, DatePrecision
from app.models.source_unit_span import SourceUnitSpan
from app.models.text_revision import TextRevision, TextType
from app.models.evidence_span import EvidenceSpan, LocationPrecision
from app.models.entity import Entity, EntityType
from app.models.entity_mention import EntityMention, MentionStatus
from app.models.identity_decision import IdentityDecision, DecisionStatus
from app.models.event_mention import EventMention, EventMentionStatus
from app.models.event import Event, EventStatus
from app.models.participation import Participation
from app.models.event_relation import EventRelation, EventRelationType
from app.models.assertion import Assertion, AssertionStatus, SourceType
from app.models.assertion_evidence import AssertionEvidence, EvidenceRelation
from app.models.review_decision import ReviewDecision, ReviewTargetType, ReviewDecisionType
from app.models.extraction_run import ExtractionRun, ExtractionStatus
from app.models.search_chunk import SearchChunk, ChunkStatus

__all__ = [
    # Original models
    "Document",
    "DocumentStatus",
    "DocumentType",
    "Page",
    "Block",
    "ReviewStatus",
    "Task",
    "TaskType",
    "TaskStatus",
    "Export",
    "ExportFormat",
    "ExportStatus",
    # Research history
    "ALEMBIC_MANAGED_TABLES",
    "ResearchMode",
    "ResearchSession",
    "ResearchStatus",
    # Research groups
    "ALEMBIC_MANAGED_GROUP_TABLES",
    "GroupImportStatus",
    "ResearchGroup",
    "ResearchGroupDocument",
    # Knowledge Graph Models & Enums
    "Person",
    "PersonMention",
    "Relationship",
    "RelationshipEvidence",
    "ExternalAuthority",
    "SNAGraphMetric",
    "IdentityStatus",
    "ReviewStatusKG",
    "ResolutionStatus",
    "RelationType",
    "Directionality",
    "ClaimStatus",
    "EvidenceRole",
    "AuthoritySource",
    "MentionType",
    "SourceKind",
    "TokenSource",
    "KGReviewAudit",
    "KGGraphSnapshot",
    "KGBlockVersion",
    # Archival metadata
    "ArchivalMetadata",
    "CompletenessStatus",
    # Source units
    "SourceUnit",
    "SourceUnitType",
    "SegmentationStatus",
    "DatePrecision",
    "SourceUnitSpan",
    # Text and evidence
    "TextRevision",
    "TextType",
    "EvidenceSpan",
    "LocationPrecision",
    # Entities
    "Entity",
    "EntityType",
    "EntityMention",
    "MentionStatus",
    "IdentityDecision",
    "DecisionStatus",
    # Events
    "EventMention",
    "EventMentionStatus",
    "Event",
    "EventStatus",
    "Participation",
    "EventRelation",
    "EventRelationType",
    # Assertions
    "Assertion",
    "AssertionStatus",
    "SourceType",
    "AssertionEvidence",
    "EvidenceRelation",
    # Review
    "ReviewDecision",
    "ReviewTargetType",
    "ReviewDecisionType",
    # Extraction and search
    "ExtractionRun",
    "ExtractionStatus",
    "SearchChunk",
    "ChunkStatus",
]
from app.models.workbench import Annotation, AnnotationReview, LegacyMapping, ResearchCorpus, CorpusMembership, ExtractionLease
from app.workbench import versioning  # register transactional text history hooks
__all__ += ['Annotation', 'AnnotationReview', 'LegacyMapping', 'ResearchCorpus', 'CorpusMembership', 'ExtractionLease']
