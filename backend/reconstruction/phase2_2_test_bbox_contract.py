"""Stage 8 Phase 2.2: unit tests for the import-layer bbox contract.

Run with:  python phase2_2_test_bbox_contract.py
No database, no network, no fixture data.
"""
from __future__ import annotations

import unittest

import phase2_2_bbox_contract as contract

W, H = 1415, 2100  # a size that actually occurs in pruned_result.json


class NormalBoxes(unittest.TestCase):
    def test_interior_box_accepted(self):
        v = contract.validate_bbox([10, 20, 110, 70], W, H, "ocr_engine")
        self.assertTrue(v.ok, v)

    def test_float_coordinates_accepted(self):
        v = contract.validate_bbox([10.5, 20.25, 110.75, 70.0], W, H, "ocr_engine")
        self.assertTrue(v.ok, v)

    def test_full_page_box_accepted(self):
        v = contract.validate_bbox([0, 0, W, H], W, H, "ocr_engine")
        self.assertTrue(v.ok, v)

    def test_page_image_px_space_accepted(self):
        v = contract.validate_bbox([0, 0, 1965, 2917], 1965, 2917, "page_image_px")
        self.assertTrue(v.ok, v)

    def test_tuple_input_accepted(self):
        v = contract.validate_bbox((1, 2, 3, 4), W, H, "ocr_engine")
        self.assertTrue(v.ok, v)

    def test_require_returns_floats(self):
        self.assertEqual(contract.require_valid_bbox([1, 2, 3, 4], W, H, "ocr_engine"),
                         (1.0, 2.0, 3.0, 4.0))


class BoundaryBoxes(unittest.TestCase):
    """Real data contains 611 blocks with x2 == width and 60 with y2 == height."""

    def test_right_edge_equal_accepted(self):
        v = contract.validate_bbox([100, 100, W, 200], W, H, "ocr_engine")
        self.assertTrue(v.ok, v)

    def test_bottom_edge_equal_accepted(self):
        v = contract.validate_bbox([100, 100, 200, H], W, H, "ocr_engine")
        self.assertTrue(v.ok, v)

    def test_both_edges_equal_accepted(self):
        v = contract.validate_bbox([1300, 2050, W, H], W, H, "ocr_engine")
        self.assertTrue(v.ok, v)

    def test_zero_origin_accepted(self):
        v = contract.validate_bbox([0, 0, 10, 10], W, H, "ocr_engine")
        self.assertTrue(v.ok, v)


class RejectedBoxes(unittest.TestCase):
    def check(self, bbox, code, width=W, height=H, space="ocr_engine"):
        v = contract.validate_bbox(bbox, width, height, space)
        self.assertFalse(v.ok, "expected rejection %s but got OK" % code)
        self.assertEqual(v.code, code, v.detail)

    def test_not_a_sequence(self):
        self.check({"a": 1}, contract.NOT_SEQUENCE)

    def test_string_is_not_a_sequence(self):
        self.check("1,2,3,4", contract.NOT_SEQUENCE)

    def test_none_bbox(self):
        self.check(None, contract.NOT_SEQUENCE)

    def test_three_elements(self):
        self.check([1, 2, 3], contract.WRONG_LENGTH)

    def test_five_elements(self):
        self.check([1, 2, 3, 4, 5], contract.WRONG_LENGTH)

    def test_string_element(self):
        self.check(["1", "2", "3", "4"], contract.NON_NUMERIC)

    def test_bool_element(self):
        # bool is a subclass of int in Python: must not slip through as a number.
        self.check([True, 0, 10, 10], contract.NON_NUMERIC)

    def test_none_element(self):
        self.check([None, 0, 10, 10], contract.NON_NUMERIC)

    def test_nested_sequence_element(self):
        self.check([[1, 2], 3, 4, 5], contract.NON_NUMERIC)

    def test_nan_element(self):
        self.check([float("nan"), 0, 10, 10], contract.NOT_FINITE)

    def test_infinite_element(self):
        self.check([0, 0, float("inf"), 10], contract.NOT_FINITE)

    def test_negative_x1(self):
        self.check([-1, 0, 10, 10], contract.NEGATIVE_COORDINATE)

    def test_negative_y2(self):
        self.check([0, 0, 10, -5], contract.NEGATIVE_COORDINATE)

    def test_inverted_x(self):
        self.check([100, 0, 10, 10], contract.INVERTED)

    def test_inverted_y(self):
        self.check([0, 100, 10, 10], contract.INVERTED)

    def test_zero_width(self):
        self.check([5, 5, 5, 10], contract.ZERO_AREA)

    def test_zero_height(self):
        self.check([5, 5, 10, 5], contract.ZERO_AREA)

    def test_zero_area_point(self):
        self.check([5, 5, 5, 5], contract.ZERO_AREA)

    def test_exceeds_width(self):
        self.check([0, 0, W + 1, 10], contract.EXCEEDS_SPACE)

    def test_exceeds_height(self):
        self.check([0, 0, 10, H + 1], contract.EXCEEDS_SPACE)

    def test_missing_width(self):
        self.check([0, 0, 10, 10], contract.SPACE_SIZE_MISSING, width=None)

    def test_missing_height(self):
        self.check([0, 0, 10, 10], contract.SPACE_SIZE_MISSING, height=None)

    def test_zero_width_reference(self):
        self.check([0, 0, 10, 10], contract.SPACE_SIZE_INVALID, width=0)

    def test_negative_height_reference(self):
        self.check([0, 0, 10, 10], contract.SPACE_SIZE_INVALID, height=-1)

    def test_float_reference_rejected(self):
        self.check([0, 0, 10, 10], contract.SPACE_SIZE_INVALID, width=1415.5)

    def test_unknown_coordinate_space(self):
        self.check([0, 0, 10, 10], contract.SPACE_UNKNOWN, space="screen")

    def test_none_coordinate_space(self):
        self.check([0, 0, 10, 10], contract.SPACE_UNKNOWN, space=None)


class RaisingApi(unittest.TestCase):
    def test_require_raises_with_verdict(self):
        with self.assertRaises(contract.BboxContractError) as ctx:
            contract.require_valid_bbox([0, 0, W + 5, 10], W, H, "ocr_engine")
        self.assertEqual(ctx.exception.verdict.code, contract.EXCEEDS_SPACE)

    def test_verdict_is_falsey_when_invalid(self):
        self.assertFalse(contract.validate_bbox([1, 1, 1, 1], W, H, "ocr_engine"))
        self.assertTrue(contract.validate_bbox([1, 1, 2, 2], W, H, "ocr_engine"))


class SpaceAwareness(unittest.TestCase):
    """The same numbers are valid or invalid depending on the declared frame."""

    def test_ocr_box_revalidated_against_image_frame(self):
        # 1415x2100 is a real OCR frame; the page image is ~1.389x larger. The
        # same numbers are legal in both frames here, but the frame must always
        # be passed explicitly -- a box valid in one frame can be invalid in the
        # other (see the next assertions).
        self.assertTrue(contract.validate_bbox([0, 0, 1415, 2100], 1415, 2100, "ocr_engine").ok)
        self.assertTrue(contract.validate_bbox([0, 0, 1415, 2100], 1965, 2917, "page_image_px").ok)
        v2 = contract.validate_bbox([0, 0, 2000, 2917], 1965, 2917, "page_image_px")
        self.assertEqual(v2.code, contract.EXCEEDS_SPACE)

    def test_landscape_page_uses_its_own_size(self):
        # 11 pages in the corpus are landscape: width 2100, height 1244..1755.
        v = contract.validate_bbox([0, 0, 2100, 1364], 2100, 1364, "ocr_engine")
        self.assertTrue(v.ok, v)

    def test_portrait_size_rejects_landscape_box(self):
        v = contract.validate_bbox([0, 0, 2100, 1364], 1415, 2100, "ocr_engine")
        self.assertEqual(v.code, contract.EXCEEDS_SPACE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
