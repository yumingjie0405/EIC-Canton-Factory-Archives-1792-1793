"""Regression tests for the Agent tool argument contract.

The contract is enforced by :class:`ToolRegistry.execute_tool` *before* a tool
runs:

* a required argument that is missing, ``None``, empty or whitespace-only is a
  tool execution failure (``status="error"``) with a message naming the argument;
* values are never coerced (``None`` must not become ``"None"``) and never
  widened into a broader query;
* alternative identifiers (``person_id`` / ``person_name``) require at least one.

Pure: the registry is exercised with ``db=None`` and, where the tool would touch
the database, its implementation is replaced by a spy, so nothing connects.
"""
import asyncio
import unittest

from app.services.agent.tool_registry import _is_blank_argument, tool_registry


def _call(tool_name, arguments, db=None):
    record, output = asyncio.run(
        tool_registry.execute_tool(
            db=db, tool_name=tool_name, arguments=arguments, call_id="contract-test"
        )
    )
    return record, output


class BlankArgumentTests(unittest.TestCase):
    def test_none_empty_and_whitespace_are_blank(self):
        self.assertTrue(_is_blank_argument(None))
        self.assertTrue(_is_blank_argument(""))
        self.assertTrue(_is_blank_argument("   "))
        self.assertTrue(_is_blank_argument([]))
        self.assertFalse(_is_blank_argument("Howqua"))
        self.assertFalse(_is_blank_argument(0))  # person_id 0 is a value, not "missing"
        self.assertFalse(_is_blank_argument(42))


class RequiredArgumentContractTests(unittest.TestCase):
    def test_missing_required_argument_is_reported_not_executed(self):
        record, output = _call("search_archival_evidence", {})
        self.assertEqual(record.status, "error")
        self.assertIsNone(output)
        self.assertIn("query", record.error_message)
        self.assertIn("missing required argument", record.output_summary)

    def test_none_is_rejected_instead_of_raising_inside_the_tool(self):
        record, _ = _call("search_archival_evidence", {"query": None})
        self.assertEqual(record.status, "error")
        self.assertIn("query", record.error_message)
        # The old behaviour leaked a Python TypeError from ``re.findall(None)``.
        self.assertNotIn("expected string or bytes-like object", record.error_message)

    def test_blank_strings_are_rejected(self):
        for blank in ("", "   ", "\t\n"):
            record, _ = _call("search_archival_evidence", {"query": blank})
            self.assertEqual(record.status, "error", repr(blank))
            self.assertIn("query", record.error_message)

    def test_graph_rag_query_requires_question(self):
        record, _ = _call("graph_rag_query", {"question": None})
        self.assertEqual(record.status, "error")
        self.assertIn("question", record.error_message)

    def test_search_persons_and_analyze_query_require_query(self):
        for tool in ("search_persons", "analyze_query"):
            record, _ = _call(tool, {"query": "  "})
            self.assertEqual(record.status, "error", tool)
            self.assertIn("query", record.error_message, tool)

    def test_relationship_tool_requires_one_identifier(self):
        record, _ = _call("get_person_relationships", {})
        self.assertEqual(record.status, "error")
        self.assertIn("person_id or person_name", record.error_message)

        record, _ = _call("get_external_authorities", {"person_name": "   "})
        self.assertEqual(record.status, "error")
        self.assertIn("person_id or person_name", record.error_message)

    def test_unknown_tool_is_still_rejected(self):
        record, _ = _call("drop_everything", {"query": "x"})
        self.assertEqual(record.status, "error")
        self.assertIn("whitelist", record.output_summary)

    def test_contract_does_not_reject_a_valid_identifier(self):
        calls = []

        def spy(db=None, person_name=None):
            calls.append(person_name)
            return []

        original = tool_registry._tools["get_person_relationships"]["func"]
        tool_registry._tools["get_person_relationships"]["func"] = spy
        try:
            record, output = _call("get_person_relationships", {"person_name": "Howqua"})
        finally:
            tool_registry._tools["get_person_relationships"]["func"] = original

        self.assertEqual(record.status, "success")
        self.assertEqual(output, [])
        self.assertEqual(calls, ["Howqua"])

    def test_optional_arguments_are_passed_through_without_coercion(self):
        captured = {}

        def spy(db=None, query=None, keywords=None, entities=None, time_filter=None, top_k=5):
            captured.update(
                query=query, keywords=keywords, entities=entities,
                time_filter=time_filter, top_k=top_k,
            )
            return []

        original = tool_registry._tools["search_archival_evidence"]["func"]
        tool_registry._tools["search_archival_evidence"]["func"] = spy
        try:
            record, _ = _call("search_archival_evidence", {"query": "woollen contract"})
        finally:
            tool_registry._tools["search_archival_evidence"]["func"] = original

        self.assertEqual(record.status, "success")
        self.assertEqual(captured["query"], "woollen contract")
        self.assertIsNone(captured["keywords"], "omitted optionals stay None, never the string 'None'")
        self.assertIsNone(captured["entities"])
        self.assertIsNone(captured["time_filter"], "no year filter unless one is requested")
        self.assertEqual(captured["top_k"], 5)

    def test_rejection_message_never_echoes_the_argument_value(self):
        secret = "-".join(("sk", "proj", "CONTRACTFAKE" * 2))
        record, _ = _call("search_archival_evidence", {"query": "   "})
        self.assertEqual(record.status, "error")
        self.assertNotIn(secret, record.error_message or "")
        self.assertNotIn(secret, record.output_summary or "")
        self.assertIn("query", record.error_message)


if __name__ == "__main__":
    unittest.main()
