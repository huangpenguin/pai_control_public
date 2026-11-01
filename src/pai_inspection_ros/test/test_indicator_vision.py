"""Synthetic pixel fixtures; integration evidence must use actual ROS camera."""
import unittest
import cv2
import numpy as np
from pai_inspection_ros.indicator_vision import classify_indicator


class IndicatorVisionTests(unittest.TestCase):
    def frame(self, color):
        frame = np.full((480, 640, 3), 35, dtype=np.uint8)
        frame[160:280, 200:440] = color
        return frame

    def test_red_and_green_depend_on_pixels_not_place(self):
        for rgb, expected in (((220, 0, 0), 'red'), ((0, 220, 0), 'green')):
            result = classify_indicator(self.frame(rgb))
            self.assertEqual(result['color'], expected)
            self.assertGreater(result['measurements'][expected]['largest_component_fraction'], .02)

    def test_gray_missing_or_tiny_color_is_unknown(self):
        frame = self.frame((100, 100, 100))
        self.assertEqual(classify_indicator(frame)['color'], 'unknown')
        frame[180:184, 250:254] = (220, 0, 0)
        self.assertEqual(classify_indicator(frame)['color'], 'unknown')

    def test_both_colors_are_ambiguous(self):
        frame = self.frame((220, 0, 0))
        frame[160:280, 320:440] = (0, 220, 0)
        result = classify_indicator(frame)
        self.assertEqual((result['color'], result['reason']), ('unknown', 'ambiguous_colors'))

    def test_near_floor_color_is_outside_roi(self):
        frame = np.full((480, 640, 3), 35, dtype=np.uint8)
        frame[360:] = (0, 220, 0)
        self.assertEqual(classify_indicator(frame)['color'], 'unknown')

    def test_blurred_or_overexposed_image_is_unknown(self):
        blurred = cv2.GaussianBlur(self.frame((220, 0, 0)), (101, 101), 30)
        self.assertEqual(classify_indicator(blurred)['reason'], 'unclear_image')
        self.assertEqual(classify_indicator(self.frame((255, 250, 250)))['color'], 'unknown')

    def test_invalid_shape_and_dtype_are_unknown(self):
        for image in (None, np.zeros((480, 640), np.uint8), np.zeros((480, 640, 3), float)):
            self.assertEqual(classify_indicator(image)['reason'], 'invalid_image')
