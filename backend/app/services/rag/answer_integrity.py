"""Answer provenance, model-service failure classification and citation verification.

This module is intentionally pure: it opens no database session, creates no
network client and reads no secret. It is shared by the Graph RAG service and
the research Agent so that both routes report an unavailable model service the
same way instead of silently returning a synthesised "answer".

Why this exists
---------------
Both answer paths used to wrap the model call in ``except Exception`` and fall
back to a deterministic text while still reporting ``evidence_sufficient=True``.
An expired API key (HTTP 401) therefore looked like a normal, successful
research answer. The helpers here make that state machine explicit:

``mode``           where the text came from
``degraded``       the text must not be presented as a normal model answer
``failure_reason`` stable, secret-free machine-readable cause
``citation_check`` whether the inline ``[证据 #N]`` markers are real
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from app.schemas.rag import AnswerGeneration, CitationIntegrity, RagSourceEvidence


CITATION_PATTERN = re.compile(r"证据\s*#\s*(\d+)")

MODE_MODEL = "model"
MODE_DETERMINISTIC = "deterministic_fallback"
MODE_REFUSED = "refused_no_evidence"

FAILURE_AUTHENTICATION = "authentication_failed"
FAILURE_PERMISSION = "permission_denied"
FAILURE_TIMEOUT = "timeout"
FAILURE_CONNECTION = "connection_failed"
FAILURE_RATE_LIMIT = "rate_limited"
FAILURE_NOT_CONFIGURED = "not_configured"
FAILURE_EMPTY_RESPONSE = "empty_response"
FAILURE_SERVER = "server_error"
FAILURE_CLIENT = "client_error"
FAILURE_UNEXPECTED = "unexpected_error"

#: Exception class names (matched through the MRO) mapped to a stable reason.
_EXCEPTION_NAME_TO_REASON: Dict[str, str] = {
    "AuthenticationError": FAILURE_AUTHENTICATION,
    "PermissionDeniedError": FAILURE_PERMISSION,
    "APITimeoutError": FAILURE_TIMEOUT,
    "TimeoutException": FAILURE_TIMEOUT,
    "Timeout": FAILURE_TIMEOUT,
    "APIConnectionError": FAILURE_CONNECTION,
    "ConnectError": FAILURE_CONNECTION,
    "ConnectTimeout": FAILURE_CONNECTION,
    "RateLimitError": FAILURE_RATE_LIMIT,
    "InternalServerError": FAILURE_SERVER,
}

#: Operator-facing explanation. Deliberately free of URLs, keys and payloads.
_REASON_MESSAGES: Dict[str, str] = {
    FAILURE_AUTHENTICATION: (
        "模型服务鉴权失败：API 密钥无效、已过期或无权限访问所选模型。"
        "请检查 GPT_API_KEY / GPT_BASE_URL / GPT_MODEL 配置。"
    ),
    FAILURE_PERMISSION: "模型服务拒绝访问：当前密钥无权调用所选模型。",
    FAILURE_TIMEOUT: "模型服务响应超时，未能在配置时限内返回结果。",
    FAILURE_CONNECTION: "无法连接模型服务：网络不可达或服务地址错误。",
    FAILURE_RATE_LIMIT: "模型服务限流（触发速率上限），请稍后重试。",
    FAILURE_NOT_CONFIGURED: "未配置模型服务凭据（GPT_API_KEY 为空），无法生成综述。",
    FAILURE_EMPTY_RESPONSE: "模型返回了空内容，无法据此生成回答。",
    FAILURE_SERVER: "模型服务端错误，暂时无法生成回答。",
    FAILURE_CLIENT: "模型服务拒绝了请求（请求参数或模型名称有误）。",
    FAILURE_UNEXPECTED: "调用模型服务时发生未预期的错误。",
}

#: Secrets that must never reach a response body, a log line or a report.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)//[^/\s:@]+:[^/\s@]+@"),
)

_MAX_DETAIL_LENGTH = 200


def scrub_secrets(text: str) -> str:
    """Remove credential-shaped substrings from a message before it is stored."""
    cleaned = str(text or "")
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("<redacted>", cleaned)
    return cleaned


def _is_not_configured(exc: BaseException) -> bool:
    message = str(exc or "").lower()
    return "is not configured" in message or "api key is not configured" in message


def classify_llm_failure(exc: BaseException) -> Tuple[str, str]:
    """Map an exception raised by the model call to ``(reason, safe_message)``.

    The message is built from a fixed table so it can never echo the API key,
    the request headers or the response body.
    """
    if _is_not_configured(exc):
        return FAILURE_NOT_CONFIGURED, _REASON_MESSAGES[FAILURE_NOT_CONFIGURED]

    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)

    for klass in type(exc).__mro__:
        reason = _EXCEPTION_NAME_TO_REASON.get(klass.__name__)
        if reason:
            return reason, _REASON_MESSAGES[reason]

    if isinstance(status_code, int):
        if status_code == 401:
            return FAILURE_AUTHENTICATION, _REASON_MESSAGES[FAILURE_AUTHENTICATION]
        if status_code == 403:
            return FAILURE_PERMISSION, _REASON_MESSAGES[FAILURE_PERMISSION]
        if status_code == 429:
            return FAILURE_RATE_LIMIT, _REASON_MESSAGES[FAILURE_RATE_LIMIT]
        if status_code >= 500:
            return FAILURE_SERVER, _REASON_MESSAGES[FAILURE_SERVER]
        return FAILURE_CLIENT, _REASON_MESSAGES[FAILURE_CLIENT]

    return FAILURE_UNEXPECTED, _REASON_MESSAGES[FAILURE_UNEXPECTED]


def safe_failure_detail(exc: BaseException) -> str:
    """A short, scrubbed diagnostic string suitable for logs and API responses."""
    detail = "%s: %s" % (type(exc).__name__, str(exc or "").strip())
    detail = scrub_secrets(detail)
    if len(detail) > _MAX_DETAIL_LENGTH:
        detail = detail[:_MAX_DETAIL_LENGTH] + "…"
    return detail


def check_citation_integrity(
    answer_text: str,
    sources: Sequence[RagSourceEvidence],
) -> CitationIntegrity:
    """Verify every ``[证据 #N]`` marker in the answer points at a real source.

    The prompt asks the model not to invent citation numbers; this is the
    enforcement step. A marker is invalid when ``N`` is outside
    ``1..len(sources)``, i.e. it refers to evidence that was never retrieved.
    """
    numbers: List[int] = [int(match) for match in CITATION_PATTERN.findall(answer_text or "")]
    available = len(sources or ())
    invalid = sorted({number for number in numbers if number < 1 or number > available})
    return CitationIntegrity(
        valid=not invalid,
        cited_ids=sorted(set(numbers)),
        invalid_ids=invalid,
        available_count=available,
    )


def build_model_generation(model_name: Optional[str]) -> AnswerGeneration:
    """Generation metadata for a successfully model-generated answer."""
    return AnswerGeneration(mode=MODE_MODEL, degraded=False, model_name=model_name)


def build_deterministic_generation(
    reason: str,
    message: str,
    detail: Optional[str],
    model_name: Optional[str] = None,
) -> AnswerGeneration:
    """Generation metadata for the offline deterministic fallback."""
    return AnswerGeneration(
        mode=MODE_DETERMINISTIC,
        degraded=True,
        model_name=model_name,
        failure_reason=reason,
        failure_message=message,
        failure_detail=detail,
    )


def build_refusal_generation() -> AnswerGeneration:
    """Generation metadata for the evidence-insufficiency gate."""
    return AnswerGeneration(mode=MODE_REFUSED, degraded=False)


def degraded_answer_prefix(reason: str) -> str:
    """Banner prepended to fallback text so the answer body cannot be misread."""
    return (
        "> ⚠️ **模型服务不可用（{reason}）**\n"
        "> 以下内容由离线确定性合成生成，未经大语言模型综述，"
        "仅依据已检索到的档案文本块与图谱事实整理，请务必自行核对原始出处。\n\n"
    ).format(reason=reason)


def citation_violation_prefix(invalid_ids: Sequence[int]) -> str:
    """Banner prepended when the model cited evidence numbers that do not exist."""
    ids = "、".join("证据 #%d" % number for number in invalid_ids)
    return (
        "> ⚠️ **引用校验未通过**\n"
        "> 模型输出引用了不存在的证据编号（{ids}），"
        "该段文字不可直接采信，请以右侧证据卡片为准。\n\n"
    ).format(ids=ids)
