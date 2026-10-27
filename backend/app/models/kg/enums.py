"""Controlled vocabularies and status enumerations for Knowledge Graph."""
import enum


class IdentityStatus(str, enum.Enum):
    """Historical person entity identity confirmation status."""
    PROVISIONAL = "provisional"  # Preliminary entity, not yet fully corroborated
    ACCEPTED = "accepted"        # Accepted canonical entity in current research
    UNCERTAIN = "uncertain"      # Identity remains uncertain/unresolved
    CONTESTED = "contested"      # Conflicting historical evidence or interpretations
    REJECTED = "rejected"        # Refuted entity, retained for audit trail


class ReviewStatusKG(str, enum.Enum):
    """General review status for entities, mentions, and relationships."""
    UNREVIEWED = "unreviewed"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"


class ResolutionStatus(str, enum.Enum):
    """Mention entity resolution (disambiguation) status."""
    UNRESOLVED = "unresolved"    # Raw mention not yet linked to any Person
    CANDIDATE = "candidate"      # Has candidate Person matches pending selection
    RESOLVED = "resolved"        # Confidently linked to a canonical Person
    UNCERTAIN = "uncertain"      # Multiple conflicting candidates or ambiguous context
    CONTESTED = "contested"      # Disputed identification
    REJECTED = "rejected"        # False positive mention (e.g. OCR error / not a person)


class RelationType(str, enum.Enum):
    """Controlled historical relationship types."""
    COMMERCIAL = "commercial"          # Trade, transaction, contract
    DIPLOMATIC = "diplomatic"          # Embassy, tribute, official mission
    GUARANTEE = "guarantee"            # Security merchant (保商), bond surety
    DEBT = "debt"                      # Loan, credit, unpaid balance
    CORRESPONDENCE = "correspondence"  # Letter, memorial, report
    KINSHIP = "kinship"                # Family, clan, inheritance
    ACCOMPANIMENT = "accompaniment"    # Travel together, retinue, embassy suite
    EMPLOYMENT = "employment"          # Master-servant, employer-clerk
    TRANSLATION = "translation"        # Linguistic mediation, interpreting
    OTHER = "other"                    # Other historical relation


class Directionality(str, enum.Enum):
    """Directionality of historical relationship."""
    DIRECTED = "directed"
    UNDIRECTED = "undirected"


class ClaimStatus(str, enum.Enum):
    """Epistemological claim status for relationships and authority records."""
    ASSERTED = "asserted"
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    CONTESTED = "contested"
    REJECTED = "rejected"


class EvidenceRole(str, enum.Enum):
    """Role of evidence supporting or refuting a relationship."""
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class AuthoritySource(str, enum.Enum):
    """External authority and historical database source codes."""
    CBDB = "CBDB"                      # China Biographical Database
    IOR = "IOR"                        # British Library India Office Records
    WIKIDATA = "Wikidata"              # Wikidata Entity ID
    VIAF = "VIAF"                      # Virtual International Authority File
    LIANG_CANTON = "Liang_Canton"      # 梁嘉彬《广东十三行考》
    PENG_COMMERCE = "Peng_Commerce"    # 彭泽益《中国近代手工业史资料》
    OTHER = "Other"                    # Other external source


class MentionType(str, enum.Enum):
    """Type of mention expression found in archives."""
    PERSON_NAME = "person_name"
    TITLE = "title"
    ALIAS = "alias"
    ABBREVIATION = "abbreviation"


class SourceKind(str, enum.Enum):
    """Where a knowledge-graph record actually came from.

    Provenance is not the same as review status and not the same as historical
    truth: a record can be reviewed and accepted inside the tool while its origin is
    still a deterministic test stub. Every surfaced record therefore carries one of
    these values, and ``unknown`` is the honest answer when the origin cannot be
    reconstructed (records written before the ledger existed).
    """

    PROVIDER_REPORTED = "provider_reported"        # real provider model, usage verifiable
    MOCK_CONSTANT = "mock_constant"                # old fixed-output mock
    MOCK_TEXT_DERIVED = "mock_text_derived"        # rule-based reading of the block text
    HISTORICAL_SEED = "historical_seed"            # human-prepared seed / test fixture
    UNKNOWN = "unknown"                            # provenance not reconstructable


class TokenSource(str, enum.Enum):
    """What the token counter on a run actually measures.

    ``tokens_used`` is only a billing-relevant number when a real provider returned a
    ``usage`` block. Mocking must never be presented as supplier consumption.
    """

    PROVIDER = "provider"      # usage reported by a real provider API
    ESTIMATED = "estimated"    # stub/mock counter or a local estimate
    UNKNOWN = "unknown"        # legacy run, no way to tell


class RoleSource(str, enum.Enum):
    """Where a person's role string came from.

    A role is a claim about who someone was, so it needs the same provenance treatment
    as any other claim. ``unknown`` (or ``NULL``) means "no role recorded", which must
    not be rendered as "other" — those are different statements.
    """

    MODEL_EXTRACTED = "model_extracted"   # produced by a model, not yet confirmed
    HUMAN_VERIFIED = "human_verified"     # a reviewer confirmed it
    HEURISTIC = "heuristic"               # rule-based guess (e.g. the -qua naming habit)
    UNKNOWN = "unknown"                   # recorded value whose origin is not known
