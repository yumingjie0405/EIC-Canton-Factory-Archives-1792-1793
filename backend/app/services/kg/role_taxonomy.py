"""Map an extracted role wording onto the categories the interface can colour.

Why a pure module
-----------------
The graph legend advertises role categories, so the mapping from "what the text said"
to "which category" has to be explicit, testable and **deterministic**. Two temptations
are deliberately avoided:

* no fuzzy/partial matching — "merchant of Venice" is not a Hong merchant, and a
  similarity score must never decide a person's occupation;
* no silent fallback to "other": an empty or unrecognised wording is reported as
  ``unknown``, because "we do not know" and "we know it is a different role" are
  different statements.

Categories follow the project's own vocabulary (see ``ROLE_COLOR_MAP`` in the frontend):

===============  ==========================================================
hong_merchant    Security/Hong merchant, 行商/保商
foreign_merchant Supercargo, supra cargo, captain, select committee president
official         Viceroy, Hoppo, Mandarin, Governor, 总督, 海关
diplomatic       Ambassador, envoy, plenipotentiary, 使节 (kept apart from
                 ordinary officials: an embassy is not a provincial post)
interpreter      Linguist, interpreter, 通事, 引水
comprador        Comprador / 买办 — in this corpus a Chinese agent acting for
                 foreign merchants, *not* a translator, so it is not folded
                 into ``interpreter``
unknown          empty or unrecognised
===============  ==========================================================
"""
from typing import Dict, Optional, Sequence, Tuple

from app.models.kg.enums import RoleSource

ROLE_CATEGORY_HONG_MERCHANT = "hong_merchant"
ROLE_CATEGORY_FOREIGN_MERCHANT = "foreign_merchant"
ROLE_CATEGORY_OFFICIAL = "official"
ROLE_CATEGORY_DIPLOMATIC = "diplomatic"
ROLE_CATEGORY_INTERPRETER = "interpreter"
ROLE_CATEGORY_COMPRADOR = "comprador"
ROLE_CATEGORY_UNKNOWN = "unknown"

ROLE_CATEGORIES: Sequence[str] = (
    ROLE_CATEGORY_HONG_MERCHANT,
    ROLE_CATEGORY_FOREIGN_MERCHANT,
    ROLE_CATEGORY_OFFICIAL,
    ROLE_CATEGORY_DIPLOMATIC,
    ROLE_CATEGORY_INTERPRETER,
    ROLE_CATEGORY_COMPRADOR,
    ROLE_CATEGORY_UNKNOWN,
)

ROLE_CATEGORY_LABELS: Dict[str, str] = {
    ROLE_CATEGORY_HONG_MERCHANT: "行商（保商）",
    ROLE_CATEGORY_FOREIGN_MERCHANT: "外商 / 大班",
    ROLE_CATEGORY_OFFICIAL: "清廷官员",
    ROLE_CATEGORY_DIPLOMATIC: "外交使节",
    ROLE_CATEGORY_INTERPRETER: "通事 / 引水",
    ROLE_CATEGORY_COMPRADOR: "买办",
    ROLE_CATEGORY_UNKNOWN: "角色未知",
}

ROLE_SOURCE_LABELS: Dict[str, str] = {
    RoleSource.MODEL_EXTRACTED.value: "模型提取（未确认）",
    RoleSource.HUMAN_VERIFIED.value: "人工确认",
    RoleSource.HEURISTIC.value: "规则推测（未验证）",
    RoleSource.UNKNOWN.value: "来源不明",
}

#: Keyword → category, checked in order. Longer/more specific phrases come first so
#: "security merchant" wins over "merchant" and "supra cargo" wins over "cargo".
#: Matching is whole-phrase containment on a normalised lowercase string, never fuzzy.
_ROLE_KEYWORDS: Sequence[Tuple[str, str]] = (
    # ---- Hong merchants (Chinese side, licensed by the Cohong)
    ("security merchant", ROLE_CATEGORY_HONG_MERCHANT),
    ("hong merchant", ROLE_CATEGORY_HONG_MERCHANT),
    ("cohong", ROLE_CATEGORY_HONG_MERCHANT),
    ("行商", ROLE_CATEGORY_HONG_MERCHANT),
    ("保商", ROLE_CATEGORY_HONG_MERCHANT),
    # ---- Foreign merchants / supercargoes
    ("supercargo", ROLE_CATEGORY_FOREIGN_MERCHANT),
    ("supra cargo", ROLE_CATEGORY_FOREIGN_MERCHANT),
    ("select committee", ROLE_CATEGORY_FOREIGN_MERCHANT),
    ("president of the select committee", ROLE_CATEGORY_FOREIGN_MERCHANT),
    ("foreign merchant", ROLE_CATEGORY_FOREIGN_MERCHANT),
    ("ship captain", ROLE_CATEGORY_FOREIGN_MERCHANT),
    ("captain", ROLE_CATEGORY_FOREIGN_MERCHANT),
    ("大班", ROLE_CATEGORY_FOREIGN_MERCHANT),
    ("船长", ROLE_CATEGORY_FOREIGN_MERCHANT),
    # ---- Chinese officials
    ("viceroy", ROLE_CATEGORY_OFFICIAL),
    ("hoppo", ROLE_CATEGORY_OFFICIAL),
    ("mandarin", ROLE_CATEGORY_OFFICIAL),
    ("governor", ROLE_CATEGORY_OFFICIAL),
    ("prefect", ROLE_CATEGORY_OFFICIAL),
    ("总督", ROLE_CATEGORY_OFFICIAL),
    ("海关", ROLE_CATEGORY_OFFICIAL),
    ("官员", ROLE_CATEGORY_OFFICIAL),
    # ---- Diplomatic corps (kept separate from provincial officials)
    ("ambassador", ROLE_CATEGORY_DIPLOMATIC),
    ("plenipotentiary", ROLE_CATEGORY_DIPLOMATIC),
    ("envoy", ROLE_CATEGORY_DIPLOMATIC),
    ("embassy", ROLE_CATEGORY_DIPLOMATIC),
    ("使节", ROLE_CATEGORY_DIPLOMATIC),
    ("公使", ROLE_CATEGORY_DIPLOMATIC),
    # ---- Linguists / pilots
    ("linguist", ROLE_CATEGORY_INTERPRETER),
    ("interpreter", ROLE_CATEGORY_INTERPRETER),
    ("translator", ROLE_CATEGORY_INTERPRETER),
    ("通事", ROLE_CATEGORY_INTERPRETER),
    ("引水", ROLE_CATEGORY_INTERPRETER),
    # ---- Compradors (agent for foreign merchants; not a translator)
    ("comprador", ROLE_CATEGORY_COMPRADOR),
    ("买办", ROLE_CATEGORY_COMPRADOR),
)


def normalize_role_text(role_text: Optional[str]) -> str:
    """Lowercase, collapse whitespace and strip surrounding punctuation."""
    if not role_text:
        return ""
    cleaned = role_text.replace("\u3000", " ").strip().lower()
    for junk in ("()", "[]", "{}"):
        cleaned = cleaned.replace(junk[0], " ").replace(junk[1], " ")
    return " ".join(cleaned.split())


def classify_role(role_text: Optional[str]) -> str:
    """Category for one role wording; ``unknown`` when nothing matches."""
    text = normalize_role_text(role_text)
    if not text:
        return ROLE_CATEGORY_UNKNOWN
    for keyword, category in _ROLE_KEYWORDS:
        if keyword in text:
            return category
    return ROLE_CATEGORY_UNKNOWN


def role_category_label(category: Optional[str]) -> str:
    return ROLE_CATEGORY_LABELS.get(
        category or ROLE_CATEGORY_UNKNOWN, ROLE_CATEGORY_LABELS[ROLE_CATEGORY_UNKNOWN]
    )


def role_source_label(source: Optional[str]) -> str:
    return ROLE_SOURCE_LABELS.get(
        source or RoleSource.UNKNOWN.value, ROLE_SOURCE_LABELS[RoleSource.UNKNOWN.value]
    )


def is_recorded_role(role_text: Optional[str]) -> bool:
    """True when a role was actually recorded (as opposed to "no role yet")."""
    return bool(normalize_role_text(role_text))


def confidence_note(role_text: Optional[str], source: Optional[str]) -> str:
    """One-line explanation of how much the category can be trusted."""
    category = classify_role(role_text)
    if category == ROLE_CATEGORY_UNKNOWN:
        return "没有可识别的角色表述，不能据此判断身份。"
    if source == RoleSource.HUMAN_VERIFIED.value:
        return "人工已确认该角色表述。"
    if source == RoleSource.HEURISTIC.value:
        return "该分类来自规则推测（例如人名后缀习惯），尚未经原文或人工验证。"
    if source == RoleSource.MODEL_EXTRACTED.value:
        return "该分类由模型从区块原文提取，尚待人工确认。"
    return "角色表述来源不明，需人工核对。"
