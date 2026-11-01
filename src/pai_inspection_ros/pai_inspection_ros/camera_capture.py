"""A bounded latest-frame cache; optional camera faults yield inspection unknown."""
import hashlib
import threading
import time
from pathlib import Path

import cv2
from cv_bridge import CvBridge


class CameraCapture:
    def __init__(self, now=time.monotonic):
        self.now = now
        self.mutex = threading.Lock()
        self.message = None
        self.receipt_wall = None
        self.bridge = CvBridge()

    def receive(self, message):
        with self.mutex:
            self.message, self.receipt_wall = message, self.now()

    def fresh_frame(self, *, after_wall, after_sim, sim_now, max_age=0.5):
        with self.mutex:
            message, receipt = self.message, self.receipt_wall
        if message is None:
            return None, 'no_image'
        stamp = message.header.stamp.sec + message.header.stamp.nanosec*1e-9
        if (receipt <= after_wall or stamp < after_sim or
                not 0 <= self.now()-receipt <= max_age or not 0 <= sim_now-stamp <= max_age):
            return None, 'stale'
        return (message, receipt), None

    def to_rgb(self, message):
        # The ROS message's encoding governs conversion, never the world color.
        return self.bridge.imgmsg_to_cv2(message, desired_encoding='rgb8').copy()

    @staticmethod
    def save_png(rgb, directory, filename):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        # Do not overwrite an earlier inspection or replay its artifact.
        encoded_ok, encoded = cv2.imencode('.png', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if not encoded_ok:
            raise ValueError('png_encoding_failed')
        data = encoded.tobytes()
        with path.open('xb') as stream:
            stream.write(data)
        return {'png_path': str(path), 'png_sha256': hashlib.sha256(data).hexdigest()}
