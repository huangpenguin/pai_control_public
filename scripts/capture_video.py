#!/usr/bin/env python3
"""Sample real ROS camera frames and TF/state; encode the video after navigation."""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import time

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data, QoSProfile, DurabilityPolicy, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import Image
from pai_inspection_interfaces.msg import SafetyState
from tf2_ros import Buffer, TransformListener, TransformException


class Capture(Node):
    def __init__(self, directory, fps):
        super().__init__('pai_video_capture', parameter_overrides=[Parameter('use_sim_time', value=True)])
        cv2.setNumThreads(1)
        self.directory, self.fps = directory, fps
        self.frames = []
        self.started = time.monotonic()
        self.last_capture = None
        self.keep_running = True
        self.buffer = Buffer()
        self.listener = TransformListener(self.buffer, self)
        self.safety = None
        state_qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                              reliability=ReliabilityPolicy.RELIABLE)
        self.state_sub = self.create_subscription(SafetyState, '/pai/safety_state', self.on_state, state_qos)
        self.image_sub = self.create_subscription(Image, '/camera/image_raw', self.on_image,
                                                  qos_profile_sensor_data)

    def on_state(self, msg):
        self.safety = {'locked': msg.locked, 'motion_permitted': msg.motion_permitted,
                       'generation': msg.generation, 'reason': msg.reason}

    def on_image(self, msg):
        wall = time.monotonic()
        if self.last_capture is not None and wall - self.last_capture < 1 / self.fps:
            return
        if len(self.frames) >= 1200:
            self.keep_running = False
            return
        if msg.encoding not in ('rgb8', 'bgr8', 'rgba8', 'bgra8'):
            return
        channels = 4 if msg.encoding.endswith('a8') else 3
        raw = np.frombuffer(bytes(msg.data), dtype=np.uint8)
        pixels = raw.reshape(msg.height, msg.step)[:, :msg.width * channels].reshape(msg.height, msg.width, channels)
        if msg.encoding == 'rgb8':
            bgr = cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
        elif msg.encoding == 'rgba8':
            bgr = cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGR)
        elif msg.encoding == 'bgra8':
            bgr = cv2.cvtColor(pixels, cv2.COLOR_BGRA2BGR)
        else:
            bgr = pixels
        name = f'frame-{len(self.frames):06d}.jpg'
        if not cv2.imwrite(str(self.directory / name), bgr, [cv2.IMWRITE_JPEG_QUALITY, 75]):
            raise RuntimeError('Cannot write actual camera frame')
        pose = transform_stamp = None
        try:
            transform = self.buffer.lookup_transform('map', 'base_footprint', Time())
            p, q = transform.transform.translation, transform.transform.rotation
            pose = {'x': p.x, 'y': p.y, 'qx': q.x, 'qy': q.y, 'qz': q.z, 'qw': q.w}
            transform_stamp = transform.header.stamp.sec + transform.header.stamp.nanosec * 1e-9
        except TransformException:
            pass
        self.frames.append({'file': name, 'wall_s': wall,
            'image_sim_s': msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
            'image_frame_id': msg.header.frame_id, 'encoding': msg.encoding,
            'width': msg.width, 'height': msg.height,
            'raw_image_sha256': hashlib.sha256(raw.tobytes()).hexdigest(),
            'map_pose': pose, 'map_transform_sim_s': transform_stamp, 'safety': self.safety})
        if len(self.frames) == 1:
            (self.directory / 'ready.json').write_text(json.dumps({
                'collection_started_wall_s': self.started, 'first_camera_wall_s': wall,
                'first_image_sim_s': self.frames[0]['image_sim_s'],
                'actual_image_received': True, 'use_sim_time': True,
            }, indent=2) + '\n')
        self.last_capture = wall


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=600)
    parser.add_argument('--fps', type=float, default=1)
    args = parser.parse_args()
    if args.output_dir.exists() or not 0 < args.fps <= 2 or not 1 <= args.seconds <= 1200:
        parser.error('Choose a new directory, fps within (0,2], duration within [1,1200]')
    args.output_dir.mkdir(parents=True)
    rclpy.init()
    capture = Capture(args.output_dir, args.fps)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: setattr(capture, 'keep_running', False))
    deadline = capture.started + args.seconds
    try:
        while capture.keep_running and time.monotonic() < deadline:
            rclpy.spin_once(capture, timeout_sec=0.1)
    finally:
        ended = time.monotonic()
        report = {'source': 'actual_ros_gazebo_camera_and_tf', 'control_commands_published': 0,
            'collection_started_wall_s': capture.started, 'collection_ended_wall_s': ended,
            'first_camera_wall_s': capture.frames[0]['wall_s'] if capture.frames else None,
            'last_camera_wall_s': capture.frames[-1]['wall_s'] if capture.frames else None,
            'sampling_max_wall_fps': args.fps, 'frame_limit': 1200,
            'recorder_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'map_sha256': hashlib.sha256(Path('/opt/pai/simulation/maps/inspection.pgm').read_bytes()).hexdigest(),
            'frames': capture.frames,
            'limits': 'Sampled actual frames compressed to JPEG; latest actual TF recorded separately, not exact camera/TF time synchronization. Encoding occurs after navigation.'}
        (args.output_dir / 'capture.json').write_text(json.dumps(report, indent=2) + '\n')
        capture.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0 if len(capture.frames) >= 2 else 2


if __name__ == '__main__':
    raise SystemExit(main())
