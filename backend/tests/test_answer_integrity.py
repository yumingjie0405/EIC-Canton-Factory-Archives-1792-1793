"""Pure unit tests for answer provenance, LLM failure classification and citation checks.

No database, no network, no Alembic: the model client is always replaced by an
``AsyncMock`` and every URL/credential below is synthetic.
"""
import unittest
from unittest import mock

from app.schemas.rag import (
    QueryIntentAnalysis,
    RagEntityReference,
    RagRelationshipReference,
    RagSourceEvidence,
)
from app.services.rag import answer_integrity as ai
from app.services.rag.rag_service import RagService
from app.services.agent.agent_runner import AgentRunner


def _source(idx: int, quote: str = "根据商馆会议记录，Browne 向 Howqua 支付货款。") -> RagSourceEvidence:
    return RagSourceEvidence(
        source_id="证据 #%d" % idx,
        document_name="Canton Consultations 1792",
        page_number=12,
        block_id=100 + idx,
        quote_text=quote,
        relevance_score=0.9,
    )


class TestCitationIntegrity(unittest.TestCase):
    """[证据 #N] markers must map onto evidence that was really retrieved."""

    def test_all_markers_valid(self):
        sources = [_source(1), _source(2)]
        check = ai.check_citation_integrity("结论一[证据 #1]，结论二[证据 #2]。", sources)
        self.assertTrue(check.valid)
        self.assertEqual(check.invalid_ids, [])
        self.assertEqual(check.cited_ids, [1, 2])
        self.assertEqual(check.available_count, 2)

    def test_out_of_range_marker_is_invalid(self):
        check = ai.check_citation_integrity("结论[证据 #1]，另一结论[证据 #3]。", [_source(1), _source(2)])
        self.assertFalse(check.valid)
        self.assertEqual(check.invalid_ids, [3])

    def test_zero_marker_is_invalid(self):
        check = ai.check_citation_integrity("结论[证据 #0]。", [_source(1)])
        self.assertFalse(check.valid)
        self.assertEqual(check.invalid_ids, [0])

    def test_marker_with_no_evidence_available(self):
        check = ai.check_citation_integrity("结论[证据 #1]。", [])
        self.assertFalse(check.valid)
        self.assertEqual(check.invalid_ids, [1])
        self.assertEqual(check.available_count, 0)

    def test_answer_without_markers_is_valid(self):
        check = ai.check_citation_integrity("无角标叙述。", [_source(1)])
        self.assertTrue(check.valid)
        self.assertEqual(check.cited_ids, [])

    def test_spacing_variants_are_recognised(self):
        sources = [_source(1), _source(2)]
        check = ai.check_citation_integrity("甲[证据#2]，乙[证据 # 1]。", sources)
        self.assertTrue(check.valid)
        self.assertEqual(check.cited_ids, [1, 2])

    def test_empty_answer_does_not_crash(self):
        check = ai.check_citation_integrity("", [_source(1)])
        self.assertTrue(check.valid)
        self.assertEqual(check.cited_ids, [])


# --- fake provider exceptions: classification is based on class name through the MRO ---


class AuthenticationError(Exception):
    pass


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class RateLimitError(Exception):
    pass


class APIStatusError(Exception):
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


class TestFailureClassification(unittest.TestCase):
    """An expired key (401), a timeout and an outage must be distinguishable."""

    def test_authentication_error(self):
        reason, message = ai.classify_llm_failure(AuthenticationError("Error code: 401"))
        self.assertEqual(reason, ai.FAILURE_AUTHENTICATION)
        self.assertIn("GPT_API_KEY", message)

    def test_timeout(self):
        reason, _ = ai.classify_llm_failure(APITimeoutError("timed out"))
        self.assertEqual(reason, ai.FAILURE_TIMEOUT)

    def test_connection_error(self):
        reason, _ = ai.classify_llm_failure(APIConnectionError("unreachable"))
        self.assertEqual(reason, ai.FAILURE_CONNECTION)

    def test_rate_limit(self):
        reason, _ = ai.classify_llm_failure(RateLimitError("429"))
        self.assertEqual(reason, ai.FAILURE_RATE_LIMIT)

    def test_status_500_is_server_error(self):
        reason, _ = ai.classify_llm_failure(APIStatusError("boom", status_code=500))
        self.assertEqual(reason, ai.FAILURE_SERVER)

    def test_status_401_without_typed_exception(self):
        reason, _ = ai.classify_llm_failure(APIStatusError("unauthorized", status_code=401))
        self.assertEqual(reason, ai.FAILURE_AUTHENTICATION)

    def test_status_400_is_client_error(self):
        reason, _ = ai.classify_llm_failure(APIStatusError("bad request", status_code=400))
        self.assertEqual(reason, ai.FAILURE_CLIENT)

    def test_missing_api_key_is_not_configured(self):
        from app.core import GPTServiceError

        reason, message = ai.classify_llm_failure(
            GPTServiceError("GPT API key is not configured. Please set GPT_API_KEY in backend .env.")
        )
        self.assertEqual(reason, ai.FAILURE_NOT_CONFIGURED)
        self.assertIn("GPT_API_KEY", message)

    def test_unknown_exception(self):
        reason, _ = ai.classify_llm_failure(ValueError("unexpected"))
        self.assertEqual(reason, ai.FAILURE_UNEXPECTED)

    def test_real_openai_authentication_error_maps_to_authentication_failed(self):
        """The mapping must hold for the installed library, not only for stubs."""
        import httpx
        import openai

        request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
        response = httpx.Response(401, request=request, json={"error": {"message": "invalid key"}})
        exc = openai.AuthenticationError("Error code: 401", response=response, body=None)
        reason, _ = ai.classify_llm_failure(exc)
        self.assertEqual(reason, ai.FAILURE_AUTHENTICATION)


class TestSecretScrubbing(unittest.TestCase):
    """Failure detail is echoed to clients, so it must never carry credentials."""

    def test_openai_style_key_is_redacted(self):
        scrubbed = ai.scrub_secrets("key=sk-live-abcdef123456 rejected")
        self.assertNotIn("sk-live-abcdef123456", scrubbed)
        self.assertIn("<redacted>", scrubbed)

    def test_bearer_token_is_redacted(self):
        self.assertNotIn("s3cr3t", ai.scrub_secrets("Authorization: Bearer s3cr3t-token-value"))

    def test_dsn_password_is_redacted(self):
        scrubbed = ai.scrub_secrets("postgresql://ocr_platform:hunter2@127.0.0.1:5432/ocr_platform")
        self.assertNotIn("hunter2", scrubbed)

    def test_safe_failure_detail_is_bounded_and_scrubbed(self):
        detail = ai.safe_failure_detail(RuntimeError("sk-live-abcdef123456 " + "x" * 500))
        self.assertNotIn("sk-live-abcdef123456", detail)
        self.assertLessEqual(len(detail), ai._MAX_DETAIL_LENGTH + 1)

    def test_classification_messages_never_contain_a_url_or_key(self):
        for exc in (
            AuthenticationError("sk-live-abcdef123456"),
            APIConnectionError("https://user:pw@provider.invalid/v1"),
            APIStatusError("boom", status_code=503),
        ):
            reason, message = ai.classify_llm_failure(exc)
            self.assertIn(reason, ai._REASON_MESSAGES)
            self.assertNotIn("sk-live", message)
            self.assertNotIn("provider.invalid", message)


class TestGenerationBuilders(unittest.TestCase):
    def test_model_generation_is_not_degraded(self):
        generation = ai.build_model_generation("deepseek-ai/DeepSeek-V3")
        self.assertEqual(generation.mode, ai.MODE_MODEL)
        self.assertFalse(generation.degraded)
        self.assertEqual(generation.model_name, "deepseek-ai/DeepSeek-V3")
        self.assertIsNone(generation.failure_reason)

    def test_deterministic_generation_is_degraded(self):
        generation = ai.build_deterministic_generation(
            ai.FAILURE_AUTHENTICATION, "msg", "detail"
        )
        self.assertEqual(generation.mode, ai.MODE_DETERMINISTIC)
        self.assertTrue(generation.degraded)
        self.assertEqual(generation.failure_reason, ai.FAILURE_AUTHENTICATION)
        self.assertEqual(generation.failure_detail, "detail")

    def test_refusal_generation_mode(self):
        generation = ai.build_refusal_generation()
        self.assertEqual(generation.mode, ai.MODE_REFUSED)
        self.assertFalse(generation.degraded)


class TestRagGenerationReporting(unittest.IsolatedAsyncioTestCase):
    """The service must report model failure instead of pretending success."""

    def setUp(self):
        self.service = RagService()
        self.sources = [_source(1), _source(2)]

    def _patch(self, side_effect=None, return_value=None):
        from app.services.kg import llm_client as llm_module

        return mock.patch.object(
            llm_module.kg_llm_client,
            "call_extraction_llm",
            new=mock.AsyncMock(side_effect=side_effect, return_value=return_value),
        )

    async def test_success_reports_model_mode(self):
        with self._patch(return_value=("布朗向浩官支付货款[证据 #1]。", None)):
            text, generation = await self.service._generate_llm_answer(
                "q", "ctx", [], [], self.sources
            )
        self.assertEqual(generation.mode, ai.MODE_MODEL)
        self.assertFalse(generation.degraded)
        self.assertEqual(text, "布朗向浩官支付货款[证据 #1]。")

    async def test_authentication_failure_is_degraded_and_labelled(self):
        with self._patch(side_effect=AuthenticationError("Error code: 401")):
            text, generation = await self.service._generate_llm_answer(
                "Howqua 与 Browne 的关系?", "ctx", [], [], self.sources
            )
        self.assertTrue(generation.degraded)
        self.assertEqual(generation.mode, ai.MODE_DETERMINISTIC)
        self.assertEqual(generation.failure_reason, ai.FAILURE_AUTHENTICATION)
        self.assertIn("模型服务不可用", text)
        self.assertIn(ai.FAILURE_AUTHENTICATION, text)
        self.assertIn("证据 #1", text)  # grounded fallback still cites real evidence

    async def test_empty_model_response_is_degraded(self):
        with self._patch(return_value=("   ", None)):
            text, generation = await self.service._generate_llm_answer("q", "ctx", [], [], self.sources)
        self.assertTrue(generation.degraded)
        self.assertEqual(generation.failure_reason, ai.FAILURE_EMPTY_RESPONSE)
        self.assertIn(ai.FAILURE_EMPTY_RESPONSE, text)


class TestAgentGenerationReporting(unittest.IsolatedAsyncioTestCase):
    """The Agent must degrade visibly and must not invent external scholarship."""

    def setUp(self):
        self.runner = AgentRunner()
        self.sources = [_source(1), _source(2)]
        self.entities = [
            RagEntityReference(id=1, display_name="Howqua", primary_role="Hong merchant")
        ]
        self.relationships = [
            RagRelationshipReference(
                source_person_name="Howqua",
                target_person_name="Henry Browne",
                relation_type="business",
                evidence_quote="档案记载 Howqua 向 Browne 交付茶叶",
            )
        ]

    def _patch(self, side_effect=None, return_value=None):
        from app.services.kg import llm_client as llm_module

        return mock.patch.object(
            llm_module.kg_llm_client,
            "call_extraction_llm",
            new=mock.AsyncMock(side_effect=side_effect, return_value=return_value),
        )

    async def test_success_reports_model_mode_and_parses_categorization(self):
        answer = "### 档案直接记载\n- 布朗向浩官支付货款[证据 #1]。\n\n### 图谱关系事实\n- 二人存在商业往来。"
        with self._patch(return_value=(answer, None)):
            text, categorization, generation = await self.runner._synthesize_answer(
                "q", "ctx", self.entities, self.relationships, self.sources
            )
        self.assertEqual(generation.mode, ai.MODE_MODEL)
        self.assertFalse(generation.degraded)
        self.assertEqual(len(categorization.archival_facts), 1)
        self.assertEqual(len(categorization.kg_relationships), 1)
        self.assertTrue(text.startswith("### 档案直接记载"))

    async def test_authentication_failure_degrades_with_banner(self):
        with self._patch(side_effect=AuthenticationError("Error code: 401")):
            text, categorization, generation = await self.runner._synthesize_answer(
                "Howqua 与 Browne 的关系?", "ctx", self.entities, self.relationships, self.sources
            )
        self.assertTrue(generation.degraded)
        self.assertEqual(generation.failure_reason, ai.FAILURE_AUTHENTICATION)
        self.assertIn("模型服务不可用", text)
        self.assertIn("档案直接记载", text)
        self.assertTrue(len(categorization.archival_facts) >= 1)

    async def test_fallback_does_not_fabricate_external_scholarship(self):
        with self._patch(side_effect=APIConnectionError("unreachable")):
            text, categorization, _ = await self.runner._synthesize_answer(
                "Howqua 与 Browne 的关系?", "ctx", self.entities, self.relationships, self.sources
            )
        self.assertNotIn("梁嘉彬", text)
        self.assertNotIn("广东十三行考", text)
        # The section may carry a disclaimer bullet, but it must not assert a
        # source, an author, a book title or a page number.
        for item in categorization.external_scholarship:
            self.assertNotIn("梁嘉彬", item)
            self.assertNotIn("十三行考", item)
            self.assertNotIn("页", item)
            self.assertNotIn("《", item)

    async def test_fallback_declines_to_generate_new_inferences(self):
        with self._patch(side_effect=APIConnectionError("unreachable")):
            text, categorization, _ = await self.runner._synthesize_answer(
                "q", "ctx", self.entities, self.relationships, self.sources
            )
        self.assertIn("离线合成模式不产生新的历史推断", text)
        for item in categorization.inferences_and_synthesis:
            self.assertNotIn("据此推断", item)

    async def test_fallback_does_not_invent_citation_numbers(self):
        with self._patch(side_effect=APIConnectionError("unreachable")):
            text, _, _ = await self.runner._synthesize_answer(
                "q", "ctx", self.entities, self.relationships, self.sources
            )
        check = ai.check_citation_integrity(text, self.sources)
        self.assertTrue(check.valid, "fallback must only cite evidence that exists")

    async def test_timeout_degrades(self):
        with self._patch(side_effect=APITimeoutError("read timeout")):
            _, _, generation = await self.runner._synthesize_answer(
                "q", "ctx", self.entities, self.relationships, self.sources
            )
        self.assertTrue(generation.degraded)
        self.assertEqual(generation.failure_reason, ai.FAILURE_TIMEOUT)


class TestPrependBanners(unittest.TestCase):
    def test_degraded_prefix_names_the_reason(self):
        text = ai.degraded_answer_prefix(ai.FAILURE_TIMEOUT)
        self.assertIn(ai.FAILURE_TIMEOUT, text)
        self.assertIn("模型服务不可用", text)

    def test_citation_violation_prefix_lists_ids(self):
        text = ai.citation_violation_prefix([3, 5])
        self.assertIn("证据 #3", text)
        self.assertIn("证据 #5", text)
        self.assertIn("引用校验未通过", text)


class TestAnswerQuestionOrchestration(unittest.IsolatedAsyncioTestCase):
    """End-to-end service orchestration with mocked retrieval (no database).

    Verifies that a research request reaches retrieval, that the dual-path
    results reach context fusion, that the sufficiency gate is not bypassed and
    that generation provenance is reported back to the caller.
    """

    def setUp(self):
        from importlib import import_module

        # The package re-exports the singleton under the same name as the
        # submodule, so resolve the module through sys.modules explicitly.
        self.rag_module = import_module("app.services.rag.rag_service")
        self.service = RagService()
        self.sources = [_source(1), _source(2)]
        self.intent = QueryIntentAnalysis(
            raw_query="Howqua 与 Browne 的毛织品合同",
            query_type="relational",
            entities=["Howqua", "Henry Browne"],
            time_filter=1792,
            search_keywords=["woolen", "contract"],
        )
        self.entities = [
            RagEntityReference(id=1, display_name="Howqua", primary_role="Security Merchant"),
            RagEntityReference(id=2, display_name="Henry Browne", primary_role="Supercargo"),
        ]
        self.relationships = [
            RagRelationshipReference(
                source_person_name="Howqua",
                target_person_name="Henry Browne",
                relation_type="commercial",
                evidence_quote="settled the woolen contracts",
            )
        ]

    def _patch_pipeline(self, is_sufficient=True, generation_stub="text"):
        patches = [
            mock.patch.object(
                self.rag_module.query_analyzer, "analyze_query", return_value=self.intent
            ),
            mock.patch.object(
                self.rag_module.graph_retriever,
                "retrieve_graph_context",
                return_value=(self.entities, self.relationships, [self.sources[0]]),
            ),
            mock.patch.object(
                self.rag_module.vector_retriever,
                "retrieve_text_evidence",
                return_value=[self.sources[1]],
            ),
            mock.patch.object(
                self.rag_module.context_builder,
                "fuse_and_build_context",
                return_value=("CTX", list(self.sources), is_sufficient),
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return patches

    async def test_request_reaches_retrieval_and_reports_model_provenance(self):
        self._patch_pipeline(generation_stub="string")
        from app.schemas.rag import RagQueryRequest

        with mock.patch.object(
            self.service,
            "_generate_llm_answer",
            new=mock.AsyncMock(return_value="毛织品合同已议定[证据 #1]。"),
        ):
            response = await self.service.answer_question(
                db=None,
                request=RagQueryRequest(question="Howqua 与 Browne 的毛织品合同", time_filter=1792),
            )

        self.assertTrue(response.evidence_sufficient)
        self.assertEqual(response.generation.mode, ai.MODE_MODEL)
        self.assertFalse(response.generation.degraded)
        self.assertIsNotNone(response.generation.citation_check)
        self.assertTrue(response.generation.citation_check.valid)
        self.assertEqual(len(response.sources), 2)
        self.assertEqual(response.intent.entities[0], "Howqua")

    async def test_degraded_generation_propagates_to_response(self):
        self._patch_pipeline()
        from app.schemas.rag import RagQueryRequest

        degraded_generation = ai.build_deterministic_generation(
            ai.FAILURE_AUTHENTICATION, "msg", "detail"
        )
        with mock.patch.object(
            self.service,
            "_generate_llm_answer",
            new=mock.AsyncMock(return_value=("降级文本[证据 #1]。", degraded_generation)),
        ):
            response = await self.service.answer_question(
                db=None, request=RagQueryRequest(question="Howqua 与 Browne 的毛织品合同")
            )

        self.assertTrue(response.generation.degraded)
        self.assertEqual(response.generation.failure_reason, ai.FAILURE_AUTHENTICATION)

    async def test_fabricated_citation_is_flagged_and_annotated(self):
        self._patch_pipeline()
        from app.schemas.rag import RagQueryRequest

        with mock.patch.object(
            self.service,
            "_generate_llm_answer",
            new=mock.AsyncMock(return_value="编造引文[证据 #9]。[证据 #1]"),
        ):
            response = await self.service.answer_question(
                db=None, request=RagQueryRequest(question="Howqua 与 Browne 的毛织品合同")
            )

        self.assertFalse(response.generation.citation_check.valid)
        self.assertEqual(response.generation.citation_check.invalid_ids, [9])
        self.assertTrue(response.generation.degraded)
        self.assertIn("引用校验未通过", response.answer)

    async def test_insufficient_evidence_gate_refuses_without_calling_model(self):
        self._patch_pipeline(is_sufficient=False)
        from app.schemas.rag import RagQueryRequest

        with mock.patch.object(
            self.service, "_generate_llm_answer", new=mock.AsyncMock()
        ) as mocked:
            response = await self.service.answer_question(
                db=None, request=RagQueryRequest(question="完全不相关的提问内容")
            )

        mocked.assert_not_awaited()
        self.assertFalse(response.evidence_sufficient)
        self.assertEqual(response.sources, [])
        self.assertEqual(response.generation.mode, ai.MODE_REFUSED)
        self.assertIn("没有足够证据", response.answer)


class TestRouterPassThrough(unittest.IsolatedAsyncioTestCase):
    """The API layer must forward the request and surface the generation status.

    The service is mocked, so no database session is ever created (``db=None``).
    """

    def _agent_response(self, generation) -> object:
        from app.schemas.agent import (
            AgentResearchResponse,
            AnswerCategorization,
        )

        return AgentResearchResponse(
            task_id="agent-test",
            question="Howqua 与 Browne 的关系?",
            final_answer="### 档案直接记载\n- 记录[证据 #1]。",
            categorization=AnswerCategorization(archival_facts=["记录[证据 #1]。"]),
            evidence_sufficient=True,
            generation=generation,
        )

    async def test_agent_router_forwards_request_and_returns_generation(self):
        from app.api.routers import agent as agent_router_module
        from app.schemas.agent import AgentResearchRequest

        generation = ai.build_deterministic_generation(
            ai.FAILURE_AUTHENTICATION, "msg", "detail"
        )
        expected = self._agent_response(generation)

        with mock.patch.object(
            agent_router_module.agent_service, "run_research", new=mock.AsyncMock(return_value=expected)
        ) as mocked:
            result = await agent_router_module.run_agent_research(
                request=AgentResearchRequest(question="Howqua 与 Browne 的关系?", time_filter=1792),
                db=None,
            )

        request_arg = mocked.await_args.kwargs["request"]
        self.assertEqual(request_arg.question, "Howqua 与 Browne 的关系?")
        self.assertEqual(request_arg.time_filter, 1792)
        self.assertIsNone(mocked.await_args.kwargs["db"])
        self.assertTrue(result.generation.degraded)
        self.assertEqual(result.generation.failure_reason, ai.FAILURE_AUTHENTICATION)
        payload = result.model_dump()
        self.assertEqual(payload["generation"]["mode"], ai.MODE_DETERMINISTIC)
        self.assertTrue(payload["generation"]["degraded"])

    async def test_agent_router_turns_service_failure_into_http_error(self):
        from fastapi import HTTPException
        from app.api.routers import agent as agent_router_module
        from app.schemas.agent import AgentResearchRequest

        with mock.patch.object(
            agent_router_module.agent_service, "run_research", new=mock.AsyncMock(side_effect=RuntimeError("db down"))
        ):
            with self.assertRaises(HTTPException) as ctx:
                await agent_router_module.run_agent_research(
                    request=AgentResearchRequest(question="Howqua 是谁"), db=None
                )
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertNotIn("sk-", str(ctx.exception.detail))

    async def test_agent_router_error_detail_redacts_dsn_password(self):
        from fastapi import HTTPException
        from app.api.routers import agent as agent_router_module
        from app.schemas.agent import AgentResearchRequest

        leaky = RuntimeError(
            "(psycopg2.OperationalError) could not connect to "
            "postgresql://ocr_platform:sup3rsecret@127.0.0.1:5432/ocr_platform"
        )
        with mock.patch.object(
            agent_router_module.agent_service, "run_research", new=mock.AsyncMock(side_effect=leaky)
        ):
            with self.assertRaises(HTTPException) as ctx:
                await agent_router_module.run_agent_research(
                    request=AgentResearchRequest(question="Howqua 是谁"), db=None
                )
        self.assertNotIn("sup3rsecret", str(ctx.exception.detail))
        self.assertIn("Agent research workflow failed", str(ctx.exception.detail))

    async def test_rag_router_returns_generation_status(self):
        from app.api.routers import rag as rag_router_module
        from app.schemas.rag import RagQueryRequest, RagQueryResponse

        response = RagQueryResponse(
            question="Howqua 是谁?",
            answer="Howqua 是行商[证据 #1]。",
            evidence_sufficient=True,
            sources=[_source(1)],
            generation=ai.build_model_generation("test-model"),
        )
        with mock.patch.object(
            rag_router_module.rag_service, "answer_question", new=mock.AsyncMock(return_value=response)
        ):
            result = await rag_router_module.query_graph_rag(
                request=RagQueryRequest(question="Howqua 是谁?"), db=None
            )

        self.assertFalse(result.generation.degraded)
        self.assertEqual(result.generation.model_name, "test-model")
        self.assertTrue(result.generation.citation_check is None)  # set by the service, not the router


if __name__ == "__main__":
    unittest.main(verbosity=2)
