"""Parser and validator for LLM-generated entity and relationship extraction responses."""
import json
import re
from typing import Any, Dict

from app.core import logger, AppException
from app.schemas.kg_extraction_schema import (
    ExtractedEntity,
    ExtractedRelationship,
    KGExtractionResult,
)

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


class RelationParserError(AppException):
    """Exception raised when extraction parsing fails."""

    def __init__(self, message: str, raw_response: str = ""):
        super().__init__(message=message, code="EXTRACTION_PARSE_ERROR")
        self.raw_response = raw_response


class RelationParser:
    """Robust parser that transforms LLM string outputs into validated KGExtractionResult objects."""

    @staticmethod
    def strip_json_fences(content: str) -> str:
        """Strip markdown fences ```json ... ``` if present."""
        match = JSON_FENCE_PATTERN.search(content)
        if match:
            return match.group(1).strip()
        return content.strip()

    def parse_extraction_json(self, raw_output: str) -> KGExtractionResult:
        """Parse raw LLM response text into validated KGExtractionResult."""
        if not raw_output or not raw_output.strip():
            logger.warning("Empty output received from extraction LLM")
            return KGExtractionResult(entities=[], relationships=[])

        cleaned = self.strip_json_fences(raw_output)

        try:
            parsed_data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            # Fallback: attempt to find first '{' and last '}'
            start_idx = cleaned.find("{")
            end_idx = cleaned.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                try:
                    parsed_data = json.loads(cleaned[start_idx : end_idx + 1])
                except json.JSONDecodeError:
                    logger.error("Failed to parse JSON from LLM: %s | Raw: %s", exc, raw_output[:300])
                    raise RelationParserError(f"Invalid JSON format from extraction LLM: {exc}", raw_output)
            else:
                logger.error("No JSON object found in response: %s", raw_output[:300])
                raise RelationParserError(f"No JSON object detected in extraction response: {exc}", raw_output)

        if not isinstance(parsed_data, dict):
            raise RelationParserError("Extraction response JSON root must be an object", raw_output)

        # Validate entities list
        validated_entities = []
        raw_entities = parsed_data.get("entities", [])
        if isinstance(raw_entities, list):
            for item in raw_entities:
                if isinstance(item, dict):
                    try:
                        validated_entities.append(ExtractedEntity(**item))
                    except Exception as err:
                        logger.warning("Skipping invalid entity item %s: %s", item, err)

        # Validate relationships list
        validated_relationships = []
        raw_relationships = parsed_data.get("relationships", [])
        if isinstance(raw_relationships, list):
            for item in raw_relationships:
                if isinstance(item, dict):
                    try:
                        validated_relationships.append(ExtractedRelationship(**item))
                    except Exception as err:
                        logger.warning("Skipping invalid relationship item %s: %s", item, err)

        return KGExtractionResult(
            entities=validated_entities,
            relationships=validated_relationships,
            temporal_date_hint=parsed_data.get("temporal_date_hint"),
            spatial_location_hint=parsed_data.get("spatial_location_hint"),
        )


relation_parser = RelationParser()
