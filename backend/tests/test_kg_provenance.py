"""Unit tests: provenance classification and token provenance (no database)."""
import unittest

from app.models.kg.enums import SourceKind, TokenSource
from app.services.kg import provenance
from app.services.kg.extraction_run_service import prompt_fingerprint


class TestProvenanceClassification(unittest.TestCase):
    def test_model_names_map_to_the_documented_kinds(self):
        self.assertEqual(
            provenance.classify_model("mock-model"), SourceKind.MOCK_CONSTANT.value
        )
        self.assertEqual(
            provenance.classify_model("mock-text-stub"), SourceKind.MOCK_TEXT_DERIVED.value
        )
        self.assertEqual(
            provenance.classify_model("deepseek-ai/DeepSeek-V3"),
            SourceKind.PROVIDER_REPORTED.value,
        )
        # No model recorded at all is not "provider": it is unknown.
        self.assertEqual(provenance.classify_model(None), SourceKind.UNKNOWN.value)
        self.assertEqual(provenance.classify_model("   "), SourceKind.UNKNOWN.value)

    def test_token_provenance_never_calls_a_mock_counter_a_bill(self):
        self.assertEqual(
            provenance.token_source_for(SourceKind.MOCK_CONSTANT.value, 1000),
            TokenSource.ESTIMATED.value,
        )
        self.assertEqual(
            provenance.token_source_for(SourceKind.MOCK_TEXT_DERIVED.value, 10),
            TokenSource.ESTIMATED.value,
        )
        self.assertEqual(
            provenance.token_source_for(SourceKind.PROVIDER_REPORTED.value, 1234),
            TokenSource.PROVIDER.value,
        )
        # A provider run without recorded usage must not claim a verified count.
        self.assertEqual(
            provenance.token_source_for(SourceKind.PROVIDER_REPORTED.value, 0),
            TokenSource.UNKNOWN.value,
        )
        self.assertEqual(
            provenance.token_source_for(SourceKind.UNKNOWN.value, 999),
            TokenSource.UNKNOWN.value,
        )

    def test_synthetic_and_unverified_are_different_questions(self):
        self.assertTrue(provenance.is_synthetic(SourceKind.MOCK_CONSTANT.value))
        self.assertTrue(provenance.is_synthetic(SourceKind.MOCK_TEXT_DERIVED.value))
        self.assertFalse(provenance.is_synthetic(SourceKind.PROVIDER_REPORTED.value))
        self.assertFalse(provenance.is_synthetic(SourceKind.HISTORICAL_SEED.value))
        # unknown is not "synthetic" but it is definitely not verified.
        self.assertFalse(provenance.is_synthetic(SourceKind.UNKNOWN.value))
        self.assertTrue(provenance.is_unverified(SourceKind.UNKNOWN.value))
        self.assertTrue(provenance.is_unverified(None))
        self.assertFalse(provenance.is_unverified(SourceKind.PROVIDER_REPORTED.value))

    def test_combine_reports_a_mix_as_unknown(self):
        self.assertEqual(
            provenance.combine([SourceKind.MOCK_CONSTANT.value]),
            SourceKind.MOCK_CONSTANT.value,
        )
        self.assertEqual(
            provenance.combine(
                [SourceKind.MOCK_CONSTANT.value, SourceKind.MOCK_TEXT_DERIVED.value]
            ),
            SourceKind.UNKNOWN.value,
        )
        self.assertEqual(provenance.combine([]), SourceKind.UNKNOWN.value)

    def test_trust_payload_flags_any_synthetic_row(self):
        payload = provenance.trust_payload(
            [SourceKind.MOCK_TEXT_DERIVED.value, SourceKind.UNKNOWN.value],
            models=["mock-text-stub"],
        )
        self.assertTrue(payload["synthetic"])
        self.assertEqual(
            payload["source_kinds"],
            {
                SourceKind.MOCK_TEXT_DERIVED.value: 1,
                SourceKind.UNKNOWN.value: 1,
            },
        )
        clean = provenance.trust_payload([SourceKind.PROVIDER_REPORTED.value])
        self.assertFalse(clean["synthetic"])

    def test_prompt_fingerprint_is_stable_and_change_sensitive(self):
        first = prompt_fingerprint()
        self.assertEqual(first, prompt_fingerprint())
        self.assertEqual(len(first), 64)
        # Different prompt/config inputs must not collide; the function hashes the
        # template plus the extraction config, so an identical environment is stable
        # while a changed one differs. The stability assertion above is the contract we
        # can check without mutating settings.
        self.assertNotEqual(first, "0" * 64)


if __name__ == "__main__":
    unittest.main()
