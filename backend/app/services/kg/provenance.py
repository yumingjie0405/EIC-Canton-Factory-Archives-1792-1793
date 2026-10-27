"""Provenance classification for every knowledge-graph record.

One question, one answer: *what actually produced this record?* Review status
(``unreviewed`` / ``reviewed``) and claim status (``asserted`` / ``accepted``) say what
humans decided about a claim; they say nothing about whether the row came from a real
provider model, a fixed mock, a rule-based stub over the archive text, or a seed file.
Those are three independent dimensions and this module owns only the third.

Anything the ledger cannot prove is reported as ``unknown`` — never guessed.
"""
from typing import Dict, Iterable, List, Optional, Sequence

from app.config import settings
from app.models.kg.enums import SourceKind, TokenSource

#: Trust ordering used when a relationship has evidences of several origins.
#: Lower index = stronger claim about the world.
_SOURCE_PRIORITY: Sequence[str] = (
    SourceKind.PROVIDER_REPORTED.value,
    SourceKind.HISTORICAL_SEED.value,
    SourceKind.MOCK_TEXT_DERIVED.value,
    SourceKind.MOCK_CONSTANT.value,
)

SOURCE_KIND_LABELS: Dict[str, str] = {
    SourceKind.PROVIDER_REPORTED.value: "真实模型（供应商返回）",
    SourceKind.HISTORICAL_SEED.value: "人工种子 / 测试夹具",
    SourceKind.MOCK_TEXT_DERIVED.value: "规则桩（读取档案原文）",
    SourceKind.MOCK_CONSTANT.value: "固定 Mock（不读原文）",
    SourceKind.UNKNOWN.value: "来源不明",
}

TOKEN_SOURCE_LABELS: Dict[str, str] = {
    TokenSource.PROVIDER.value: "供应商上报用量",
    TokenSource.ESTIMATED.value: "桩/估算计数（非计费）",
    TokenSource.UNKNOWN.value: "未知",
}


def classify_model(model: Optional[str]) -> str:
    """Map a ``KGExtractionRun.model`` value onto a :class:`SourceKind`."""
    if not model or not str(model).strip():
        return SourceKind.UNKNOWN.value
    lowered = str(model).strip().lower()
    if lowered in settings.kg_text_derived_model_name_set:
        return SourceKind.MOCK_TEXT_DERIVED.value
    if lowered in settings.kg_mock_model_name_set:
        return SourceKind.MOCK_CONSTANT.value
    # Unknown model names are treated as a real provider: the ledger recorded a model
    # string we do not recognise as a test double, and calling it "unknown" would hide
    # a genuine provider run.
    return SourceKind.PROVIDER_REPORTED.value


def token_source_for(source_kind: str, tokens_used: int) -> str:
    """What the ``tokens_used`` counter measures for a run of this provenance."""
    if source_kind == SourceKind.PROVIDER_REPORTED.value:
        return TokenSource.PROVIDER.value if tokens_used > 0 else TokenSource.UNKNOWN.value
    if source_kind == SourceKind.UNKNOWN.value:
        return TokenSource.UNKNOWN.value
    return TokenSource.ESTIMATED.value if tokens_used > 0 else TokenSource.UNKNOWN.value


def is_synthetic(source_kind: Optional[str]) -> bool:
    """True when the record must never be presented as an archival finding."""
    return source_kind in (
        SourceKind.MOCK_CONSTANT.value,
        SourceKind.MOCK_TEXT_DERIVED.value,
    )


def is_unverified(source_kind: Optional[str]) -> bool:
    """True when the record cannot be treated as verified provenance.

    ``unknown`` is included on purpose: a row whose origin cannot be reconstructed is not
    a finding, it is an open question. Reviewers must see that before accepting it.
    """
    return is_synthetic(source_kind) or not source_kind or source_kind == SourceKind.UNKNOWN.value


def describe(source_kind: Optional[str]) -> str:
    return SOURCE_KIND_LABELS.get(
        source_kind or SourceKind.UNKNOWN.value, SOURCE_KIND_LABELS[SourceKind.UNKNOWN.value]
    )


def describe_tokens(token_source: Optional[str]) -> str:
    return TOKEN_SOURCE_LABELS.get(
        token_source or TokenSource.UNKNOWN.value, TOKEN_SOURCE_LABELS[TokenSource.UNKNOWN.value]
    )


def combine(kinds: Iterable[Optional[str]]) -> str:
    """Provenance of a claim supported by several evidences.

    A single real-provider evidence is not enough to call a claim "provider reported"
    if other evidences are mock: the caller must see the mix. The combination therefore
    returns ``unknown`` for a mixed set and the common kind when all rows agree.
    """
    present = {kind or SourceKind.UNKNOWN.value for kind in kinds}
    if not present:
        return SourceKind.UNKNOWN.value
    if len(present) == 1:
        return present.pop()
    return SourceKind.UNKNOWN.value


def summarise(kinds: Iterable[Optional[str]]) -> Dict[str, int]:
    """Count records per provenance kind (used by scope/facet summaries)."""
    counts: Dict[str, int] = {}
    for kind in kinds:
        key = kind or SourceKind.UNKNOWN.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def trust_payload(kinds: Iterable[Optional[str]], models: Iterable[Optional[str]] = ()) -> Dict[str, object]:
    """Uniform trust block embedded in API responses.

    ``synthetic`` is true when *any* contributing record is mock-derived, because a
    mixed graph must still warn the user rather than advertise the strongest kind.
    """
    kind_list: List[str] = [kind or SourceKind.UNKNOWN.value for kind in kinds]
    counts = summarise(kind_list)
    synthetic = any(is_synthetic(kind) for kind in kind_list)
    model_list = sorted({str(model) for model in models if model})
    return {
        "source_kinds": counts,
        "synthetic": synthetic,
        "models": model_list,
        "labels": {kind: describe(kind) for kind in counts},
        "note": (
            "部分或全部记录来自固定 Mock / 规则桩，只能用于流程与界面验证，不构成档案发现。"
            if synthetic
            else "记录来源为真实模型或人工种子；是否成立仍取决于人工审核。"
        ),
    }
