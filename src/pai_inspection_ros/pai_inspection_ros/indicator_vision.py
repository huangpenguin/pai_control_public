"""Pixel-only red/green indicator classification; never reads station/world state."""
from dataclasses import asdict, dataclass
import hashlib
import json

import cv2
import numpy as np


@dataclass(frozen=True)
class IndicatorThresholds:
    # Broad enough for allowed arrival yaw/lateral error; exclude the near floor.
    roi: tuple = (0.03, 0.10, 0.97, 0.64)
    saturation_min: int = 80
    value_min: int = 50
    red_hue_ranges: tuple = ((0, 12), (168, 179))
    green_hue_range: tuple = (45, 90)
    minimum_component_fraction: float = 0.02
    maximum_other_fraction: float = 0.005
    minimum_component_fill: float = 0.55
    minimum_focus_variance: float = 12.0


def classify_indicator(rgb, thresholds=None):
    thresholds = thresholds or IndicatorThresholds()
    config = {'algorithm': 'connected-HSV-rgb-focus-v1', **asdict(thresholds)}
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    result = {'color': 'unknown', 'reason': 'invalid_image', 'thresholds': config,
              'thresholds_sha256': digest, 'roi_pixels': None, 'measurements': {}}
    if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or
            rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 16):
        return result
    height, width = rgb.shape[:2]
    x0, y0, x1, y1 = thresholds.roi
    bounds = (int(width*x0), int(height*y0), int(width*x1), int(height*y1))
    left, top, right, bottom = bounds
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        result['reason'] = 'invalid_roi'
        return result
    region = rgb[top:bottom, left:right]
    result['roi_pixels'] = list(bounds)
    # Chromatic edges can have equal grayscale luminance (especially green).
    focus = max(float(cv2.Laplacian(region[:, :, channel], cv2.CV_64F).var())
                for channel in range(3))
    hsv = cv2.cvtColor(region, cv2.COLOR_RGB2HSV)
    red = np.zeros(region.shape[:2], dtype=np.uint8)
    for low, high in thresholds.red_hue_ranges:
        red |= cv2.inRange(hsv, (low, thresholds.saturation_min, thresholds.value_min), (high, 255, 255))
    green = cv2.inRange(hsv, (thresholds.green_hue_range[0], thresholds.saturation_min, thresholds.value_min),
                       (thresholds.green_hue_range[1], 255, 255))
    area = region.shape[0]*region.shape[1]
    measurements = {'focus_variance': focus}
    components = {}
    for color, mask in (('red', red), ('green', green)):
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if count <= 1:
            component_area, fill, box = 0, 0.0, None
        else:
            largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            component_area = int(stats[largest, cv2.CC_STAT_AREA])
            x, y, w, h = (int(stats[largest, i]) for i in range(4))
            fill, box = component_area/(w*h), [x+left, y+top, w, h]
        measurements[color] = {'pixel_fraction': float(np.count_nonzero(mask))/area,
                               'largest_component_fraction': component_area/area,
                               'component_fill': fill, 'bounding_box': box}
        components[color] = component_area/area >= thresholds.minimum_component_fraction and fill >= thresholds.minimum_component_fill
    result['measurements'] = measurements
    if focus < thresholds.minimum_focus_variance:
        result['reason'] = 'unclear_image'
    elif components['red'] and components['green']:
        result['reason'] = 'ambiguous_colors'
    else:
        for color, other in (('red', 'green'), ('green', 'red')):
            if components[color] and measurements[other]['pixel_fraction'] <= thresholds.maximum_other_fraction:
                result['color'], result['reason'] = color, 'indicator_detected'
                break
        else:
            result['reason'] = 'no_clear_indicator'
    return result
