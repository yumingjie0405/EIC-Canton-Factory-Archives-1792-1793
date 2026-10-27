"""Knowledge Graph services module exports."""
from app.services.kg.llm_client import KGLLMClient, kg_llm_client
from app.services.kg.relation_parser import RelationParser, relation_parser, RelationParserError
from app.services.kg.kg_writer import KGWriter, kg_writer
from app.services.kg.extraction_service import ExtractionService, extraction_service

from app.services.kg.name_normalizer import NameNormalizer, name_normalizer
from app.services.kg.candidate_generator import CandidateGenerator, candidate_generator
from app.services.kg.entity_matcher import EntityMatcher, entity_matcher, MatchEvaluation
from app.services.kg.authority_linker import AuthorityLinker, authority_linker
from app.services.kg.disambiguation_service import DisambiguationService, disambiguation_service
from app.services.kg.entity_merge_service import EntityMergeService, entity_merge_service

from app.services.kg.sna_engine import SNAEngine, NetworkMetricsResult
from app.services.kg.sna_service import SNAService, sna_service

__all__ = [
    # Stage 2 Services
    "KGLLMClient",
    "kg_llm_client",
    "RelationParser",
    "relation_parser",
    "RelationParserError",
    "KGWriter",
    "kg_writer",
    "ExtractionService",
    "extraction_service",
    # Stage 3 Services
    "NameNormalizer",
    "name_normalizer",
    "CandidateGenerator",
    "candidate_generator",
    "EntityMatcher",
    "entity_matcher",
    "MatchEvaluation",
    "AuthorityLinker",
    "authority_linker",
    "DisambiguationService",
    "disambiguation_service",
    "EntityMergeService",
    "entity_merge_service",
    # Stage 4 Services
    "SNAEngine",
    "NetworkMetricsResult",
    "SNAService",
    "sna_service",
]

