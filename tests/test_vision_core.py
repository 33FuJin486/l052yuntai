from __future__ import annotations

import unittest

from app.vision_core import DetectionCandidate, select_closest_target


class VisionCoreTests(unittest.TestCase):
    def test_selects_target_closest_to_frame_center(self) -> None:
        candidates = [
            DetectionCandidate(0, 0, 20, 20, 0.99, 0),
            DetectionCandidate(300, 220, 340, 260, 0.60, 0),
        ]
        selected = select_closest_target(candidates, 640, 480)
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual((selected.center_x, selected.center_y), (320, 240))
        self.assertEqual((selected.error_x, selected.error_y), (0, 0))
        self.assertEqual(selected.confidence, 0.60)

    def test_preserves_original_axis_directions(self) -> None:
        candidate = DetectionCandidate(400, 100, 420, 120, 0.8, 0)
        selected = select_closest_target([candidate], 640, 480)
        assert selected is not None
        self.assertEqual(selected.error_x, 90)
        self.assertEqual(selected.error_y, 130)

    def test_integer_center_is_calculated_before_distance(self) -> None:
        candidate = DetectionCandidate(0.9, 0.9, 2.9, 2.9, 0.8, 0)
        selected = select_closest_target([candidate], 10, 10)
        assert selected is not None
        self.assertEqual((selected.center_x, selected.center_y), (1, 1))

    def test_tie_keeps_first_detection(self) -> None:
        first = DetectionCandidate(2, 4, 4, 6, 0.4, 3)
        second = DetectionCandidate(6, 4, 8, 6, 0.9, 7)
        selected = select_closest_target([first, second], 10, 10)
        assert selected is not None
        self.assertEqual(selected.class_id, 3)

    def test_empty_detections_return_none(self) -> None:
        self.assertIsNone(select_closest_target([], 640, 480))


if __name__ == "__main__":
    unittest.main()
