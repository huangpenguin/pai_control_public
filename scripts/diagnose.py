#!/usr/bin/env python3
"""Measure actual sensor messages and TF without publishing motion commands."""
import argparse
import json
import math
from pathlib import Path
import struct
import time
import zlib

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import CameraInfo, Image, LaserScan
from tf2_ros import Buffer, TransformListener


def png_chunk(kind, payload):
    return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload))


def save_camera(message, dest):
    if message.encoding not in ('rgb8', 'bgr8', 'rgba8', 'bgra8'):
        return None
    channels = 4 if 'a8' in message.encoding else 3
    data = bytes(message.data)
    rows = []
    for y in range(message.height):
        row = data[y*message.step:y*message.step+message.width*channels]
        if message.encoding.startswith('bgr'):
            row = b''.join(bytes((row[i+2], row[i+1], row[i])) + row[i+3:i+channels]
                           for i in range(0, len(row), channels))
        rows.append(b'\0' + row)
    color = 6 if channels == 4 else 2
    header = struct.pack('!IIBBBBB', message.width, message.height, 8, color, 0, 0, 0)
    dest.write_bytes(b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', header) +
                     png_chunk(b'IDAT', zlib.compress(b''.join(rows))) + png_chunk(b'IEND', b''))
    return str(dest)


class Diagnostics(Node):
    def __init__(self):
        super().__init__('pai_static_diagnostics')
        self.first = {}
        self.last = {}
        self.counts = {}
        self.odom_positions = []
        self.buffer = Buffer()
        self.listener = TransformListener(self.buffer, self)
        self.subscriptions_kept = []
        for topic, kind in [('/clock', Clock), ('/scan', LaserScan), ('/odom', Odometry),
                            ('/camera/image_raw', Image), ('/camera/camera_info', CameraInfo)]:
            self.subscriptions_kept.append(self.create_subscription(kind, topic,
                lambda msg, t=topic: self.receive(t, msg), qos_profile_sensor_data))

    def receive(self, topic, msg):
        wall = time.monotonic()
        self.counts[topic] = self.counts.get(topic, 0) + 1
        self.first.setdefault(topic, (wall, msg))
        self.last[topic] = (wall, msg)
        if topic == '/odom':
            p = msg.pose.pose.position
            self.odom_positions.append((p.x, p.y))

    def report(self, duration, output):
        report = {'backend': 'sensor_diagnostics_no_model_call', 'duration_s': duration,
                  'message_counts': self.counts, 'rates_hz': {}, 'transforms': {}}
        for topic, (last_wall, _) in self.last.items():
            elapsed = last_wall - self.first[topic][0]
            report['rates_hz'][topic] = (self.counts[topic]-1)/elapsed if elapsed else 0
        if '/clock' in self.last:
            first_clock = self.first['/clock'][1].clock
            last_clock = self.last['/clock'][1].clock
            sim_delta = last_clock.sec-first_clock.sec + (last_clock.nanosec-first_clock.nanosec)*1e-9
            elapsed = self.last['/clock'][0]-self.first['/clock'][0]
            report.update(sim_time_advance_s=sim_delta, real_time_factor=sim_delta/elapsed if elapsed else 0)
        if '/scan' in self.last:
            scan = self.last['/scan'][1]
            finite = [float(x) for x in scan.ranges if math.isfinite(x)]
            report['scan'] = {'frame_id':scan.header.frame_id, 'samples':len(scan.ranges),
                              'finite_samples':len(finite), 'closest_m':min(finite) if finite else None}
        if self.odom_positions:
            first_x, first_y = self.odom_positions[0]
            report['stationary_displacement_max_m'] = max(math.hypot(x-first_x,y-first_y)
                                                          for x,y in self.odom_positions)
        if '/camera/image_raw' in self.last:
            msg = self.last['/camera/image_raw'][1]
            report['camera'] = {'width':msg.width,'height':msg.height,'encoding':msg.encoding,
                                'frame_id':msg.header.frame_id,
                                'snapshot':save_camera(msg, output.with_name(output.stem+'-camera.png'))}
        for target, source in [('odom','base_footprint'), ('base_footprint','base_scan'),
                               ('base_footprint','camera_rgb_frame'), ('map','base_footprint')]:
            try:
                transform = self.buffer.lookup_transform(target, source, Time())
                p = transform.transform.translation
                report['transforms'][f'{target}<-{source}'] = {'available':True,'xyz':[p.x,p.y,p.z]}
            except Exception as exc:
                report['transforms'][f'{target}<-{source}'] = {'available':False,'error':str(exc)}
        required = ['/clock','/scan','/odom','/camera/image_raw','/camera/camera_info']
        report['static_sensor_pass'] = (all(self.counts.get(topic,0)>=2 for topic in required) and
            report.get('sim_time_advance_s',0)>0 and report.get('scan',{}).get('finite_samples',0)>0 and
            report['transforms']['odom<-base_footprint']['available'] and
            report['transforms']['base_footprint<-base_scan']['available'] and
            report.get('stationary_displacement_max_m',math.inf)<.02)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2)+'\n')
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=15)
    parser.add_argument('--output',type=Path,default=Path('/opt/pai/logs/static-diagnostics.json'))
    args = parser.parse_args()
    rclpy.init()
    node = Diagnostics()
    start = time.monotonic()
    while time.monotonic()-start < args.seconds:
        rclpy.spin_once(node,timeout_sec=.1)
    report = node.report(time.monotonic()-start,args.output)
    print(json.dumps(report,indent=2))
    node.destroy_node()
    rclpy.shutdown()
    raise SystemExit(0 if report['static_sensor_pass'] else 1)


if __name__ == '__main__':
    main()
