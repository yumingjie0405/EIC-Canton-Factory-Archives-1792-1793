"""Stage 8 Phase 2.2: import-layer bbox validation contract.

Why this is not a database CHECK constraint
-------------------------------------------
The full scan of the surviving OCR output (5,949 pruned_result.json files,
51,454 blocks) found no structural errors, no negative coordinates, no inverted
boxes and no box exceeding its declared size. It did find 611 blocks whose
``x2`` equals the declared width and 60 whose ``y2`` equals the declared height,
spread over 226 distinct declared page sizes. So:

* a strict-inequality rule would reject genuine material;
* a hard-coded pixel size would reject genuine material;
* the OCR engine frame and the page image pixels are NOT the same frame
  (page images are about 1.389x the declared OCR size), so comparing an
  ``ocr_engine`` box against ``image_width_px`` is meaningless.

A range CHECK usable by the database would therefore have to be right about all
of that, per row, and would be very hard to reverse once real data is in. The
rule set below is enforced at the import boundary instead, where the caller can
be told exactly what is wrong.

Contract
--------
Coordinates are kept exactly as they appear in ``pruned_result.json``. The
reference size is the one declared by that same page's JSON, not a constant.

A bbox is accepted only when all of the following hold:

1. it is a sequence of exactly four real numbers (booleans are NOT numbers);
2. all four are finite (no NaN / infinity);
3. ``0 <= x1 <= x2 <= space_width`` and ``0 <= y1 <= y2 <= space_height``,
   compared against the declared reference size of that same page, using
   CLOSED intervals (touching the right/bottom edge is legal);
4. the area is strictly positive (``x1 < x2`` and ``y1 < y2``);
5. ``coordinate_space`` is known AND the reference size is present.

Missing reference size, unknown coordinate space and unsupported coordinate
values are rejections, never silent passes: a row that cannot be validated must
not be imported. Nothing is rescaled here -- a caller that wants
``page_image_px`` data must do the conversion explicitly, and it is out of scope.
"""
from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from typing import Any, Optional, Tuple

# Coordinate spaces the schema accepts (mirrors ck_blocks_bbox_space).
KNOWN_SPACES = ("ocr_engine", "page_image_px")

# Verdict codes. Stable strings so tests and importers can switch on them.
OK = "OK"
NOT_SEQUENCE = "NOT_SEQUENCE"
WRONG_LENGTH = "WRONG_LENGTH"
NON_NUMERIC = "NON_NUMERIC"
NOT_FINITE = "NOT_FINITE"
NEGATIVE_COORDINATE = "NEGATIVE_COORDINATE"
INVERTED = "INVERTED"
ZERO_AREA = "ZERO_AREA"
EXCEEDS_SPACE = "EXCEEDS_SPACE"
SPACE_UNKNOWN = "SPACE_UNKNOWN"
SPACE_SIZE_MISSING = "SPACE_SIZE_MISSING"
SPACE_SIZE_INVALID = "SPACE_SIZE_INVALID"


@dataclass(frozen=True)
class BboxVerdict:
    ok: bool
    code: str
    detail: str = ""

    def __bool__(self) -> bool:  # allow `if verdict:`
        return self.ok


class BboxContractError(ValueError):
    """Raised by require_valid_bbox()."""

    def __init__(self, verdict: BboxVerdict) -> None:
        super().__init__("%s: %s" % (verdict.code, verdict.detail))
        self.verdict = verdict


def _is_real(value: Any) -> bool:
    """True for real numbers, explicitly excluding bool (a subclass of int)."""
    if isinstance(value, bool):
        return False
    return isinstance(value, numbers.Real)


def _as_size(value: Any) -> Optional[int]:
    """Validate a declared reference dimension. Positive integer or None."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        return None
    if value <= 0:
        return None
    return int(value)


def validate_bbox(
    bbox: Any,
    space_width: Any,
    space_height: Any,
    coordinate_space: Any,
) -> BboxVerdict:
    """Validate one OCR bbox against its own page's declared reference size.

    Returns a BboxVerdict; never raises for bad input.
    """
    if coordinate_space not in KNOWN_SPACES:
        return BboxVerdict(False, SPACE_UNKNOWN,
                           "coordinate_space=%r not in %r" % (coordinate_space, KNOWN_SPACES))

    width = _as_size(space_width)
    height = _as_size(space_height)
    if width is None or height is None:
        if space_width is None or space_height is None:
            return BboxVerdict(False, SPACE_SIZE_MISSING,
                               "declared reference size missing (width=%r height=%r)"
                               % (space_width, space_height))
        return BboxVerdict(False, SPACE_SIZE_INVALID,
                           "declared reference size must be positive integers "
                           "(width=%r height=%r)" % (space_width, space_height))

    if isinstance(bbox, (str, bytes, bytearray)) or not isinstance(bbox, (list, tuple)):
        return BboxVerdict(False, NOT_SEQUENCE, "bbox must be a list/tuple, got %s"
                           % type(bbox).__name__)
    if len(bbox) != 4:
        return BboxVerdict(False, WRONG_LENGTH, "bbox has %d element(s), expected 4" % len(bbox))

    for index, value in enumerate(bbox):
        if not _is_real(value):
            return BboxVerdict(False, NON_NUMERIC,
                               "bbox[%d]=%r is not a real number" % (index, value))
        if not math.isfinite(float(value)):
            return BboxVerdict(False, NOT_FINITE, "bbox[%d]=%r is not finite" % (index, value))

    x1, y1, x2, y2 = (float(v) for v in bbox)

    if x1 < 0 or y1 < 0 or x2 < 0 or y2 < 0:
        return BboxVerdict(False, NEGATIVE_COORDINATE,
                           "negative coordinate in %r" % (list(bbox),))
    if x1 > x2 or y1 > y2:
        return BboxVerdict(False, INVERTED, "coordinates out of order: %r" % (list(bbox),))
    if x1 == x2 or y1 == y2:
        return BboxVerdict(False, ZERO_AREA, "zero width or height: %r" % (list(bbox),))
    # Closed interval on the far edges: touching the declared border is legal.
    if x2 > width or y2 > height:
        return BboxVerdict(False, EXCEEDS_SPACE,
                           "bbox %r exceeds declared size %dx%d" % (list(bbox), width, height))

    return BboxVerdict(True, OK, "")


def require_valid_bbox(bbox: Any, space_width: Any, space_height: Any,
                       coordinate_space: Any) -> Tuple[float, float, float, float]:
    """Same rules as validate_bbox, but raise BboxContractError when invalid."""
    verdict = validate_bbox(bbox, space_width, space_height, coordinate_space)
    if not verdict.ok:
        raise BboxContractError(verdict)
    return tuple(float(v) for v in bbox)  # type: ignore[return-value]
