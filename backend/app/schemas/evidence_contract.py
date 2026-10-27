"""Controlled epistemological vocabularies, evidence classification contracts, and research claim schemas.

Stage 8 Phase 3.1: Academic Source Criticism and Evidence Grounding Data Contract.
"""
from datetime import datetime
import enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ============================================================================
# Concept 1: Evidence Source Type (证据来源类型)
# Indicates the structural generation origin of the material.
# NOTE: 'primary_source' denotes source category only, NOT that it is free of
# errors, omissions, or bias. 'graph_inference' must never disguise as raw quote.
# ============================================================================
class EvidenceSourceType(str, enum.Enum):
    """Structural origin category of research evidence."""
    PRIMARY_SOURCE = "primary_source"  # Direct Canton Factory consultations archival text/manuscript
    GRAPH_INFERENCE = "graph_inference"  # Derived from structured entities, relationships, or graph algorithms
    EXTERNAL_SECONDARY_SOURCE = "external_secondary_source"  # Authoritative external scholarship (Liang, Peng, CBDB)
    UNKNOWN = "unknown"  # Unconfirmed or ambiguous source origin


# ============================================================================
# Concept 2: Text Provenance & Version (文本来源与版本)
# Independent description of text representation status and revision trail.
# NOTE: 'human_transcription' (transcribing manually from manuscript image)
# and 'human_verified' (proofreading/auditing against manuscript) are strictly
# distinguished. Machine text that 'looks correct' must NOT be marked verified.
# ============================================================================
class TextProvenance(str, enum.Enum):
    """Textual representation layer and editorial provenance."""
    RAW_OCR = "raw_ocr"  # Raw OCR output from vision model (may have noise, archaic long-s, etc.)
    CORRECTED_OCR = "corrected_ocr"  # Corrected by LLM or automated post-processing rules
    HUMAN_TRANSCRIPTION = "human_transcription"  # Transcribed manually from manuscript image by human reader
    HUMAN_VERIFIED = "human_verified"  # Audited and proofread against original manuscript by domain scholar
    UNKNOWN = "unknown"  # Text provenance unconfirmed or untracked


# ============================================================================
# Concept 3: Claim-Evidence Relationship (断言与证据的逻辑关系)
# Independent semantic relation between an assertion and supporting evidence.
# Stored independently of verification methods, model verdicts, and review statuses.
# NOTE: 'unassessed' must NOT be auto-converted to 'insufficient'.
# NOTE: 'mentions_only' must NEVER be treated as 'supports'.
# ============================================================================
class EvidenceRelationship(str, enum.Enum):
    """Semantic relationship between an evidence excerpt and an assertion."""
    SUPPORTS = "supports"  # Evidence logically entails or directly corroborates the claim
    CONTRADICTS = "contradicts"  # Evidence directly contradicts or refutes the claim
    PARTIALLY_SUPPORTS = "partially_supports"  # Evidence supports part of claim or with qualifications
    MENTIONS_ONLY = "mentions_only"  # Mentions related entities/topics without corroborating or refuting
    INSUFFICIENT = "insufficient"  # Content is insufficient or out-of-scope to determine claim truth
    UNASSESSED = "unassessed"  # Relationship has not yet been assessed by model or scholar


# ============================================================================
# Additional Controlled Enums for Veracity, Auditing, and Provenance
# ============================================================================
class ModelVerdict(str, enum.Enum):
    """Automated model evaluation verdict."""
    SUPPORTED = "supported"
    REFUTED = "refuted"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNVERIFIABLE = "unverifiable"


class HumanVerdict(str, enum.Enum):
    """Domain scholar audit verdict."""
    SUPPORTED = "supported"
    REFUTED = "refuted"
    PARTIALLY_SUPPORTED = "partially_supported"
    INSUFFICIENT = "insufficient"


class ContractReviewStatus(str, enum.Enum):
    """Workflow review status for assertions and evidence links."""
    UNREVIEWED = "unreviewed"  # Pending initial review
    IN_REVIEW = "in_review"  # Currently assigned to or being examined by reviewer
    CONFIRMED = "confirmed"  # Confirmed by human domain expert (authoritative lock)
    CONTESTED = "contested"  # Subject to active scholarly dispute or conflicting sources
    REJECTED = "rejected"  # Explicitly refuted or dismissed during review


class ProvenanceStatus(str, enum.Enum):
    """Explicit status of source location grounding in archival database."""
    VERIFIED_BLOCK = "verified_block"  # Grounded in a verified, existent archival Block ID
    MISSING_BLOCK = "missing_block"  # Referenced block is absent, null, or deleted from database
    EXTERNAL_RECORD = "external_record"  # Referenced in external authority file (CBDB, VIAF, etc.)
    UNRESOLVED = "unresolved"  # Archival location ungrounded or pending resolution


class ClaimCategory(str, enum.Enum):
    """Typology of historical assertions."""
    FACTUAL = "factual"  # Direct factual event or consultation statement
    RELATIONAL = "relational"  # Person-to-person or institution social relation
    EXTERNAL = "external"  # External scholarly or authority claim
    INFERENCE = "inference"  # Synthetic deduction or historical reasoning
    UNRESOLVED = "unresolved"  # Unclassified claim


class VerificationMethod(str, enum.Enum):
    """Methodology used to assess claim-evidence entailment."""
    UNVERIFIED = "unverified"
    LEXICAL_OVERLAP = "lexical_overlap"
    CROSS_ENCODER_NLI = "cross_encoder_nli"
    LLM_AS_A_JUDGE = "llm_as_a_judge"
    MANUAL_AUDIT = "manual_audit"


# ============================================================================
# Controlled-Vocabulary Normalization
# DEF-STAGE8-P3-02: enum members, already-serialized strings, and legacy
# 'ModelVerdict.SUPPORTED'-style artifacts must all collapse onto the canonical
# lowercase string value before any downstream comparison is made.
# ============================================================================
MODEL_VERDICT_VALUES = frozenset(v.value for v in ModelVerdict)
HUMAN_VERDICT_VALUES = frozenset(v.value for v in HumanVerdict)
EVIDENCE_RELATIONSHIP_VALUES = frozenset(v.value for v in EvidenceRelationship)
REVIEW_STATUS_VALUES = frozenset(v.value for v in ContractReviewStatus)

# Vocabulary groupings used by conflict aggregation. Neutral states are kept out
# on purpose: 'mentions_only', 'insufficient', 'unassessed' and 'unverifiable'
# neither support nor refute a claim.
SUPPORTING_VERDICTS = frozenset({
    ModelVerdict.SUPPORTED.value,
    EvidenceRelationship.SUPPORTS.value,
})
REFUTING_VERDICTS = frozenset({
    ModelVerdict.REFUTED.value,
    EvidenceRelationship.CONTRADICTS.value,
})
PARTIAL_VERDICTS = frozenset({
    ModelVerdict.PARTIALLY_SUPPORTED.value,
    EvidenceRelationship.PARTIALLY_SUPPORTS.value,
})
NEUTRAL_VERDICTS = frozenset({
    ModelVerdict.UNVERIFIABLE.value,
    HumanVerdict.INSUFFICIENT.value,
    EvidenceRelationship.INSUFFICIENT.value,
    EvidenceRelationship.MENTIONS_ONLY.value,
    EvidenceRelationship.UNASSESSED.value,
})

# Model verdict -> reflected evidence relationship. Unknown verdicts are absent,
# so an unrecognized automated result can never mutate the recorded relationship.
MODEL_VERDICT_TO_RELATIONSHIP = {
    ModelVerdict.SUPPORTED.value: EvidenceRelationship.SUPPORTS.value,
    ModelVerdict.REFUTED.value: EvidenceRelationship.CONTRADICTS.value,
    ModelVerdict.PARTIALLY_SUPPORTED.value: EvidenceRelationship.PARTIALLY_SUPPORTS.value,
    ModelVerdict.UNVERIFIABLE.value: EvidenceRelationship.INSUFFICIENT.value,
}


def _enum_value(value: Any) -> Optional[str]:
    """Return the canonical lowercase string value of an enum member or string.

    Accepts enum instances, already-serialized strings, and legacy values written
    as ``'ModelVerdict.SUPPORTED'`` before DEF-STAGE8-P3-02 was fixed. Returns
    None for None, blank strings, and non-string payloads.
    """
    if value is None:
        return None
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        return None
    normalized = raw.strip()
    if not normalized:
        return None
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]
    return normalized.lower()


def _normalize_model_verdict(value: Any) -> Optional[str]:
    """Normalize an automated verdict, returning None when it is not recognized."""
    normalized = _enum_value(value)
    return normalized if normalized in MODEL_VERDICT_VALUES else None


def _normalize_human_verdict(value: Any) -> Optional[str]:
    """Normalize a scholar verdict, returning None when it is not recognized."""
    normalized = _enum_value(value)
    return normalized if normalized in HUMAN_VERDICT_VALUES else None


def _normalize_relationship(value: Any) -> Optional[str]:
    """Normalize an evidence relationship, returning None when it is not recognized."""
    normalized = _enum_value(value)
    return normalized if normalized in EVIDENCE_RELATIONSHIP_VALUES else None


def _normalize_review_status(value: Any) -> Optional[str]:
    """Normalize a review status, returning None when it is not recognized."""
    normalized = _enum_value(value)
    return normalized if normalized in REVIEW_STATUS_VALUES else None


# ============================================================================
# Pydantic Schemas for Research Claim and Evidence Grounding
# ============================================================================
class ResearchClaim(BaseModel):
    """Independent historical research proposition or claim."""
    model_config = ConfigDict(use_enum_values=True)

    claim_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Stable unique claim identifier")
    claim_text: str = Field(..., min_length=2, max_length=2000, description="Atomic historical research proposition")
    claim_category: ClaimCategory = Field(default=ClaimCategory.FACTUAL, description="Typology of the assertion")
    target_year: Optional[int] = Field(None, ge=1500, le=2050, description="Optional target year context")
    entity_ids: List[int] = Field(default_factory=list, description="Associated Person or Organization entity IDs")
    entity_names: List[str] = Field(default_factory=list, description="Associated historical entity names")
    context_description: Optional[str] = Field(None, description="Contextual background or query prompt")
    created_by: str = Field("agent", description="Claim generator, e.g. 'agent', 'kg_extraction', 'scholar'")
    source_task_id: Optional[str] = Field(None, description="Optional Agent or RAG task ID")
    review_status: ContractReviewStatus = Field(default=ContractReviewStatus.UNREVIEWED, description="Workflow review state")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("claim_text")
    @classmethod
    def validate_claim_text_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 2:
            raise ValueError("claim_text must have at least 2 non-whitespace characters")
        return stripped


class EvidenceGrounding(BaseModel):
    """Fine-grained link grounding a research claim in archival or external evidence.
    
    Enforces epistemological boundaries:
    - Separation of automated model verdict from human domain verdict.
    - Explicit provenance tracking.
    - Prohibition of ungrounded or empty quotes as primary archival citations.
    """
    model_config = ConfigDict(use_enum_values=True)

    grounding_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique grounding reference ID")
    claim_id: str = Field(..., min_length=1, description="Associated ResearchClaim ID")
    source_type: EvidenceSourceType = Field(..., description="Structural source tier")
    provenance_status: ProvenanceStatus = Field(default=ProvenanceStatus.UNRESOLVED, description="Archival grounding state")
    
    # Archival Location References (May be absent if external or unresolved, but status must be explicit)
    document_id: Optional[int] = Field(None, ge=1, description="PostgreSQL Document ID")
    document_name: Optional[str] = Field(None, description="Archival volume title")
    page_number: Optional[int] = Field(None, ge=1, description="Archival physical page number")
    block_id: Optional[int] = Field(None, ge=1, description="Archival OCR block ID")
    quote_text: Optional[str] = Field(None, description="Direct historical textual excerpt")
    text_provenance: TextProvenance = Field(default=TextProvenance.UNKNOWN, description="Text generation/proofreading level")
    
    # Semantic Relation & Method
    evidence_relationship: EvidenceRelationship = Field(
        default=EvidenceRelationship.UNASSESSED,
        description="Independent logical relation between excerpt and claim"
    )
    verification_method: VerificationMethod = Field(
        default=VerificationMethod.UNVERIFIED,
        description="Technique used for relationship assessment"
    )
    model_version: Optional[str] = Field(None, description="Model identifier used for automated verification")
    
    # Automated Model Results (Separated from Human Verdict)
    model_verdict: Optional[ModelVerdict] = Field(None, description="Automated classification output")
    entailment_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="NLI or judge confidence score (0.0 to 1.0)")
    
    # Human Review & Precedence
    human_review_status: ContractReviewStatus = Field(
        default=ContractReviewStatus.UNREVIEWED,
        description="Human editorial review state"
    )
    human_verdict: Optional[HumanVerdict] = Field(None, description="Expert scholar conclusive verdict")
    reviewer: Optional[str] = Field(None, description="Reviewer identifier or scholar username")
    review_notes: Optional[str] = Field(None, description="Scholarly textual commentary or dispute explanation")
    
    # Contestation Flag
    is_contested: bool = Field(False, description="Flag indicating unresolved conflicting evidence")
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ------------------------------------------------------------------------
    # Invariant Validations
    # ------------------------------------------------------------------------
    @model_validator(mode="after")
    def validate_provenance_and_grounding_consistency(self) -> "EvidenceGrounding":
        # Rule 1: A verified block MUST provide a block_id and a document_id
        if self.provenance_status == ProvenanceStatus.VERIFIED_BLOCK.value:
            if self.block_id is None or self.document_id is None:
                raise ValueError("provenance_status 'verified_block' requires both document_id and block_id")
            if not self.quote_text or len(self.quote_text.strip()) < 5:
                raise ValueError("provenance_status 'verified_block' requires non-empty quote_text (>= 5 chars)")
        
        # Rule 2: If block_id is missing or null, provenance_status cannot be verified_block
        if self.block_id is None and self.provenance_status == ProvenanceStatus.VERIFIED_BLOCK.value:
            raise ValueError("block_id cannot be None when provenance_status is verified_block")

        # Rule 3: graph_inference must not masquerade as raw_ocr or human_transcription
        if self.source_type == EvidenceSourceType.GRAPH_INFERENCE.value:
            if self.text_provenance in (TextProvenance.RAW_OCR.value, TextProvenance.HUMAN_TRANSCRIPTION.value):
                raise ValueError("graph_inference evidence cannot claim raw_ocr or human_transcription text provenance")

        return self

    # ------------------------------------------------------------------------
    # Domain Logic & Invariant Properties
    # ------------------------------------------------------------------------
    @property
    def is_traceable_primary_evidence(self) -> bool:
        """Invariant 6: Only primary source with verified block, valid doc_id, block_id and non-empty quote is traceable."""
        if self.source_type != EvidenceSourceType.PRIMARY_SOURCE.value:
            return False
        if self.provenance_status != ProvenanceStatus.VERIFIED_BLOCK.value:
            return False
        if self.document_id is None or self.block_id is None:
            return False
        if not self.quote_text or len(self.quote_text.strip()) < 5:
            return False
        return True

    @property
    def is_scholarly_contested(self) -> bool:
        """Whether this grounding is under an active historiographical dispute."""
        if self.is_contested:
            return True
        return _normalize_review_status(self.human_review_status) == ContractReviewStatus.CONTESTED.value

    def _scholar_authority_asserted(self) -> bool:
        """Whether a scholar has asserted authority over this grounding.

        Each status is matched explicitly so the distinct semantics are visible:
        CONFIRMED = conclusion is final, REJECTED = evidence dismissed, CONTESTED =
        dispute stands. IN_REVIEW and UNREVIEWED have no scholar conclusion yet.
        """
        status = _normalize_review_status(self.human_review_status)
        if status == ContractReviewStatus.CONFIRMED.value:
            return True
        if status == ContractReviewStatus.REJECTED.value:
            return True
        if status == ContractReviewStatus.CONTESTED.value:
            return True
        return False

    @property
    def human_authoritative_verdict(self) -> Optional[str]:
        """Scholar verdict that outranks any automated re-run, or None if absent."""
        if not self._scholar_authority_asserted():
            return None
        return _normalize_human_verdict(self.human_verdict)

    @property
    def effective_verdict(self) -> str:
        """Invariant 1 & 3: Scholar authority outranks automated output.

        Every review status is handled by its own branch so the differing
        semantics stay explicit:

        - CONFIRMED: the scholar conclusion is final.
        - REJECTED: the scholar dismissed the evidence; the dismissal stands.
        - CONTESTED: the historiographical dispute stands.
        - IN_REVIEW / UNREVIEWED: no scholar conclusion yet, automated output may stand.
        - Unrecognized status: automated output is never promoted.

        Values are always returned as canonical lowercase strings (DEF-STAGE8-P3-02),
        and unrecognized stored values are never promoted to a supporting verdict.
        """
        status = _normalize_review_status(self.human_review_status)
        fallback = _normalize_relationship(self.evidence_relationship) or EvidenceRelationship.UNASSESSED.value

        if status == ContractReviewStatus.CONFIRMED.value:
            return _normalize_human_verdict(self.human_verdict) or fallback
        if status == ContractReviewStatus.REJECTED.value:
            return _normalize_human_verdict(self.human_verdict) or fallback
        if status == ContractReviewStatus.CONTESTED.value:
            return _normalize_human_verdict(self.human_verdict) or fallback
        if status == ContractReviewStatus.IN_REVIEW.value:
            return _normalize_model_verdict(self.model_verdict) or fallback
        if status == ContractReviewStatus.UNREVIEWED.value:
            return _normalize_model_verdict(self.model_verdict) or fallback
        return fallback

    def is_historical_fact_proven(self) -> bool:
        """Invariant 2: Model confidence CANNOT be directly converted into historical truth.
        Only a confirmed human verdict of supported can formally confirm historical fact veracity.
        """
        return (
            _normalize_review_status(self.human_review_status) == ContractReviewStatus.CONFIRMED.value
            and _normalize_human_verdict(self.human_verdict) == HumanVerdict.SUPPORTED.value
        )

    def apply_model_verification(
        self,
        verdict: Any,
        entailment_score: float,
        method: Any,
        model_version: str,
    ) -> None:
        """Invariant 1: Re-running automated model verification MUST NOT overwrite a scholar record.

        Automated provenance (verdict, score, method, version, timestamp) is always
        recorded so the audit trail shows what the model claimed. The reflected
        ``evidence_relationship`` is only rewritten while no scholar authority exists,
        i.e. for UNREVIEWED and IN_REVIEW. Human verdict, reviewer, review notes,
        review status and the contestation flag are never touched.

        Raises:
            ValueError: if ``verdict`` is neither a ModelVerdict member nor one of
                its recognized string values, so corrupt automated output cannot be
                silently stored as a valid verdict.
        """
        normalized_verdict = _normalize_model_verdict(verdict)
        if normalized_verdict is None:
            raise ValueError(
                f"Unrecognized model verdict {verdict!r}; expected one of "
                f"{sorted(MODEL_VERDICT_VALUES)}"
            )

        self.model_verdict = normalized_verdict
        self.entailment_score = entailment_score
        normalized_method = _enum_value(method)
        if normalized_method is not None:
            self.verification_method = normalized_method
        self.model_version = model_version
        self.updated_at = datetime.utcnow()

        status = _normalize_review_status(self.human_review_status)
        model_may_update_relationship = False
        if status == ContractReviewStatus.UNREVIEWED.value:
            model_may_update_relationship = True
        elif status == ContractReviewStatus.IN_REVIEW.value:
            model_may_update_relationship = True

        if not model_may_update_relationship:
            return

        reflected = MODEL_VERDICT_TO_RELATIONSHIP.get(normalized_verdict)
        if reflected is not None:
            self.evidence_relationship = reflected


class ClaimEvidenceSet(BaseModel):
    """Composite container representing a claim and all its associated groundings.
    Supports multi-evidence evaluation, conflict preservation, and contestation flags.
    """
    model_config = ConfigDict(use_enum_values=True)

    claim: ResearchClaim
    groundings: List[EvidenceGrounding] = Field(default_factory=list)

    def add_grounding(self, grounding: EvidenceGrounding) -> None:
        """Add an evidence grounding to this claim set."""
        if grounding.claim_id != self.claim.claim_id:
            raise ValueError(f"Grounding claim_id '{grounding.claim_id}' does not match '{self.claim.claim_id}'")
        self.groundings.append(grounding)

    @property
    def has_contested_groundings(self) -> bool:
        """Whether any grounding is under an explicit scholarly dispute."""
        return any(g.is_scholarly_contested for g in self.groundings)

    @property
    def has_conflicts(self) -> bool:
        """Invariant 4 & 5: Detect whether the evidence both supports and contradicts the claim.

        Neutral verdicts ('mentions_only', 'insufficient', 'unassessed',
        'unverifiable') never count as support or refutation. Partial support is
        treated as a supporting signal for conflict purposes.
        """
        has_support = False
        has_contradict = False

        for g in self.groundings:
            eff = g.effective_verdict
            if eff in SUPPORTING_VERDICTS or eff in PARTIAL_VERDICTS:
                has_support = True
            elif eff in REFUTING_VERDICTS:
                has_contradict = True

        return has_support and has_contradict

    @property
    def aggregated_status(self) -> str:
        """Invariant 5: Conflicting or disputed evidence must NOT be collapsed into a supported state."""
        if not self.groundings:
            return "no_evidence"

        if self.has_contested_groundings or self.has_conflicts:
            return "contested"

        verdicts = [g.effective_verdict for g in self.groundings]

        if all(v == EvidenceRelationship.UNASSESSED.value for v in verdicts):
            return "unassessed"

        if all(v == EvidenceRelationship.MENTIONS_ONLY.value for v in verdicts):
            return "mentions_only"

        if all(v in NEUTRAL_VERDICTS for v in verdicts):
            return "inconclusive"

        if any(v in SUPPORTING_VERDICTS for v in verdicts):
            return "supported"

        if any(v in REFUTING_VERDICTS for v in verdicts):
            return "refuted"

        if any(v in PARTIAL_VERDICTS for v in verdicts):
            return "partially_supported"

        return "inconclusive"
