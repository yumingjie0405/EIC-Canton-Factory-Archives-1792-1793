"""LLM client for knowledge graph extraction services."""
import asyncio
import time
from typing import Any, Dict, Optional, Tuple

from httpx import Timeout
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
)

from app.config import settings
from app.core import logger, GPTServiceError
from app.schemas.kg_extraction_schema import ExtractionTaskMetrics


class KGLLMClient:
    """Async OpenAI-compatible LLM client tailored for KG entity and relation extraction."""

    def __init__(self):
        self.default_model = settings.gpt_model

    def _create_client(self) -> AsyncOpenAI:
        """Create OpenAI-compatible client instance from app settings."""
        api_key = settings.gpt_api_key.strip()
        if not api_key:
            raise GPTServiceError(
                "GPT API key is not configured. Please set GPT_API_KEY in backend .env."
            )

        import httpx
        http_client = httpx.AsyncClient(
            trust_env=False,
            timeout=Timeout(
                connect=30.0,
                read=float(settings.gpt_timeout),
                write=30.0,
                pool=30.0,
            ),
        )

        return AsyncOpenAI(
            api_key=api_key,
            base_url=settings.gpt_base_url,
            http_client=http_client,
            max_retries=0,
        )

    async def call_extraction_llm(
        self,
        system_prompt: str,
        user_content: str,
        task_id: Optional[str] = None,
        block_id: Optional[int] = None,
        page_id: Optional[int] = None,
        document_id: Optional[int] = None,
    ) -> Tuple[str, ExtractionTaskMetrics]:
        """Send extraction prompt to LLM and capture latency, token usage and output text."""
        start_time = time.perf_counter()
        request_options: Dict[str, Any] = {
            "model": self.default_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
            "max_tokens": settings.gpt_page_max_tokens or 4096,
        }

        max_retries = max(0, settings.gpt_max_retries)
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                async with self._create_client() as client:
                    response = await client.chat.completions.create(**request_options)

                raw_content = response.choices[0].message.content or ""
                latency_ms = (time.perf_counter() - start_time) * 1000.0

                usage = response.usage
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                total_tokens = int(getattr(usage, "total_tokens", 0) or 0)

                metrics = ExtractionTaskMetrics(
                    task_id=task_id,
                    block_id=block_id,
                    page_id=page_id,
                    document_id=document_id,
                    model_name=self.default_model,
                    latency_ms=round(latency_ms, 2),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )

                logger.info(
                    "KG extraction LLM completed: model=%s block_id=%s latency=%.1fms tokens=%s",
                    self.default_model, block_id, latency_ms, total_tokens
                )
                return raw_content.strip(), metrics

            except AuthenticationError as exc:
                logger.error("Authentication error calling LLM for KG extraction: %s", exc)
                raise GPTServiceError("LLM authentication failed. Please verify API key.") from exc

            except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
                last_error = exc
                if attempt < max_retries:
                    backoff = (settings.gpt_retry_backoff_seconds or 1.0) * (2 ** attempt)
                    logger.warning(
                        "Transient LLM extraction error on attempt %d/%d (backoff %.1fs): %s",
                        attempt + 1, max_retries, backoff, exc
                    )
                    await asyncio.sleep(backoff)
                else:
                    break

            except Exception as exc:
                logger.error("Unexpected error in KG LLM extraction: %s", exc)
                raise GPTServiceError(f"LLM extraction request failed: {exc}") from exc

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        raise GPTServiceError(
            f"KG LLM extraction failed after {max_retries + 1} attempts ({latency_ms:.1f}ms): {last_error}"
        )


kg_llm_client = KGLLMClient()
