"""Pure helpers for the archival block context view.

The neighbour selection is deliberately a pure function of an ordered block
list, so it can be verified without a database: if the ordering data is absent
or the block is not in the list, the function returns no neighbours rather than
inventing "previous"/"next" content.
"""
from typing import List, Optional, Sequence

from app.models.block import Block
from app.schemas.block_context import BlockContextBlock


#: Neighbours are only taken from the same page, in ``block_index`` order.
NEIGHBOUR_SCOPE = "same_page"

NOTES = [
    "相邻区块仅取自同一页，按 block_index 排序；不跨页推测上下文。",
    "仅返回档案页面的 OCR 转录（原始 OCR 与校订 OCR），不包含模型生成的现代英文或中文译文。",
    "若区块缺少可用转录文本，原文位置会明确显示为空，而不是由系统补写。",
]


def to_context_block(block: Block) -> BlockContextBlock:
    """Map an ORM block onto the response item (transcriptions only)."""
    raw_text = block.ocr_text_raw
    corrected_text = block.ocr_text_corrected
    has_text = bool((raw_text or "").strip() or (corrected_text or "").strip())
    return BlockContextBlock(
        block_id=block.id,
        page_id=block.page_id,
        block_index=block.block_index,
        block_label=block.block_label,
        raw_text=raw_text,
        corrected_text=corrected_text,
        has_text=has_text,
    )


def build_block_context(
    target: Block,
    siblings: Sequence[Block],
    window: int = 1,
) -> dict:
    """Return ``previous`` / ``next`` neighbours around *target*.

    ``siblings`` must already be ordered by ``block_index`` (as
    :meth:`app.repositories.block_repository.BlockRepository.get_by_page` does).
    If *target* is missing from the list, or the window is zero, no neighbours
    are returned — neighbouring text is never guessed.
    """
    ordered: List[Block] = [block for block in siblings if block.page_id == target.page_id]
    ordered.sort(key=lambda block: (block.block_index, block.id))

    position = next((index for index, block in enumerate(ordered) if block.id == target.id), None)
    if position is None or window <= 0:
        return {"previous": [], "next": []}

    start = max(0, position - window)
    end = min(len(ordered), position + window + 1)
    previous = [to_context_block(block) for block in ordered[start:position]]
    following = [to_context_block(block) for block in ordered[position + 1:end]]
    return {"previous": previous, "next": following}


def describe_target_block(block: Optional[Block]) -> Optional[BlockContextBlock]:
    """Safe mapper that tolerates ``None`` (missing block)."""
    return None if block is None else to_context_block(block)
