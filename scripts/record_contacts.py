#!/usr/bin/env python3
"""Passively record physical robot/obstacle contacts; never publish control.

Native Contact publishes only nonempty messages. Publisher discovery and live
clock coverage are checked independently; no message alone never proves zero.
The reported count is observed contact episodes, not a lossless packet audit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, qos_profile_sensor_data
from rosgraph_msgs.msg import Clock
from ros_gz_interfaces.msg import Contacts

OBSTACLES = ('west_wall', 'east_wall', 'south_wall', 'north_wall', 'central_obstacle')
TOPICS = {name: '/pai/contacts/' + name for name in OBSTACLES}


def scoped_parts(name):
    return set(filter(None, re.split(r'::|/', name)))


def classify_pair(a, b, obstacle, robot_model='waffle_pi'):
    left, right = scoped_parts(a), scoped_parts(b)
    if 'ground' in left or 'ground' in right:
        return 'ground_excluded'
    if robot_model in left and robot_model in right:
        return 'self_excluded'
    if (robot_model in left and obstacle in right or
            robot_model in right and obstacle in left):
        return 'robot_obstacle'
    return 'unresolved_or_other'


class Recorder(Node):
    def __init__(self, smoke_test=False, smoke_evidence=None):
        super().__init__('pai_contact_recorder')
        self.smoke_test = smoke_test
        self.robot_model = 'contact_probe_robot' if smoke_test else 'waffle_pi'
        self.script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.bridge_sha = hashlib.sha256(Path('/opt/pai/simulation/config/contact_bridge.yaml').read_bytes()).hexdigest()
        self.smoke_evidence_sha = None
        if smoke_evidence is not None:
            data = smoke_evidence.read_bytes()
            evidence = json.loads(data)
            if (evidence.get('run_kind') != 'independent_contact_probe_not_navigation' or
                    evidence.get('status') != 'observed' or not evidence.get('observed_episode_count') or
                    evidence.get('recorder_script_sha256') != self.script_sha or
                    evidence.get('bridge_config_sha256') != self.bridge_sha):
                raise ValueError('Contact smoke evidence must prove a nonempty pipeline with this recorder/bridge version')
            self.smoke_evidence_sha = hashlib.sha256(data).hexdigest()
        self.started = time.monotonic()
        self.clock_first = self.clock_last = None
        self.clock_first_wall = None
        self.clock_last_wall = None
        self.clock_count = 0
        self.clock_max_gap = 0.0
        self.clock_monotonic = True
        self.message_counts = {name: 0 for name in OBSTACLES}
        self.episodes = []
        self.last_episode = {}
        self.exclusions = {'ground_excluded': 0, 'self_excluded': 0, 'unresolved_or_other': 0}
        self.unresolved_examples = []
        self.health_checks = []
        self.health_lock = threading.Lock()
        self.halt = threading.Event()
        self.subscriptions_kept = [self.create_subscription(Clock, '/clock', self.on_clock,
                                                          qos_profile_sensor_data)]
        for obstacle, topic in TOPICS.items():
            self.subscriptions_kept.append(self.create_subscription(Contacts, topic,
                lambda msg, name=obstacle: self.on_contacts(name, msg), QoSProfile(depth=256)))
        self.worker = threading.Thread(target=self.check_health, daemon=True)
        self.worker.start()

    def on_clock(self, msg):
        wall = time.monotonic()
        stamp = msg.clock.sec + msg.clock.nanosec * 1e-9
        if self.clock_first is None:
            self.clock_first = stamp
            self.clock_first_wall = wall
        if self.clock_last is not None and stamp < self.clock_last:
            self.clock_monotonic = False
        if self.clock_last_wall is not None:
            self.clock_max_gap = max(self.clock_max_gap, wall - self.clock_last_wall)
        self.clock_last, self.clock_last_wall = stamp, wall
        self.clock_count += 1

    def on_contacts(self, obstacle, msg):
        self.message_counts[obstacle] += 1
        wall = time.monotonic()
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        pairs = []
        for contact in msg.contacts:
            a, b = contact.collision1.name, contact.collision2.name
            kind = classify_pair(a, b, obstacle, self.robot_model)
            if kind != 'robot_obstacle':
                self.exclusions[kind] += 1
                if kind == 'unresolved_or_other' and len(self.unresolved_examples) < 20:
                    self.unresolved_examples.append({'collision1': a, 'collision2': b, 'topic': TOPICS[obstacle]})
                continue
            pairs.append((a, b))
        if not pairs:
            return
        index = self.last_episode.get(obstacle)
        previous = self.episodes[index] if index is not None else None
        if previous is None or stamp < previous['last_sim_s'] or stamp - previous['last_sim_s'] > 0.25:
            if len(self.episodes) >= 10000:
                self.halt.set()
                return
            previous = {'obstacle': obstacle, 'first_sim_s': stamp, 'last_sim_s': stamp,
                        'first_wall_s': wall, 'last_wall_s': wall, 'contact_messages': 0,
                        'example_collision_pair': pairs[0]}
            self.last_episode[obstacle] = len(self.episodes)
            self.episodes.append(previous)
        previous['last_sim_s'], previous['last_wall_s'] = stamp, wall
        previous['contact_messages'] += 1

    def check_health(self):
        while not self.halt.is_set():
            sample = {'wall_s': time.monotonic(), 'gz_publishers': {}}
            for obstacle, topic in TOPICS.items():
                try:
                    result = subprocess.run(['gz', 'topic', '-i', '-t', topic],
                        capture_output=True, text=True, timeout=3)
                    block = re.search(r'Publishers[^\n]*:\s*(.*?)(?=Subscribers|$)',
                                      result.stdout, flags=re.DOTALL)
                    sample['gz_publishers'][obstacle] = bool(
                        result.returncode == 0 and block and 'gz.msgs.Contacts' in block.group(1))
                except (OSError, subprocess.TimeoutExpired):
                    sample['gz_publishers'][obstacle] = False
            with self.health_lock:
                sample['completed_wall_s'] = time.monotonic()
                self.health_checks.append(sample)
            self.halt.wait(10)

    def report(self):
        wall = time.monotonic()
        with self.health_lock:
            checks = list(self.health_checks)
        topic_health = bool(len(checks) >= 2 and all(
            all(s['gz_publishers'].values()) for s in checks) and
            wall - checks[-1]['completed_wall_s'] < 30)
        ros_publishers = {name: self.count_publishers(topic) for name, topic in TOPICS.items()}
        topic_health = topic_health and all(ros_publishers.values())
        clock_health = bool(self.clock_monotonic and self.clock_count >= 2 and self.clock_last > self.clock_first and
                            self.clock_max_gap <= 0.5 and wall - self.clock_last_wall <= 0.5)
        unresolved = self.exclusions['unresolved_or_other'] > 0
        positive_pipeline = bool(self.episodes or self.smoke_evidence_sha)
        measured = topic_health and clock_health and not unresolved and positive_pipeline and len(self.episodes) < 10000
        return {'source': 'actual_gazebo_contact_sensors', 'control_commands_published': 0,
                'run_kind': 'independent_contact_probe_not_navigation' if self.smoke_test else 'navigation_observation',
                'robot_model_allowlist': self.robot_model,
                'recorder_script_sha256': self.script_sha,
                'bridge_config_sha256': self.bridge_sha,
                'positive_smoke_evidence_sha256': self.smoke_evidence_sha,
                'scope': 'specified robot model versus five walls/central_obstacle; ground/self excluded',
                'status': 'observed' if measured else 'unknown_not_measured',
                'observed_episode_count': len(self.episodes) if measured else None,
                'episode_gap_sim_s': 0.25, 'episodes': self.episodes,
                'message_counts': self.message_counts, 'excluded_contacts': self.exclusions,
                'unresolved_examples': self.unresolved_examples,
                'gz_publisher_health_checks': checks, 'topic_health': topic_health,
                'ros_publisher_counts': ros_publishers,
                'clock_health': clock_health, 'clock_messages': self.clock_count,
                'clock_first_sim_s': self.clock_first, 'clock_last_sim_s': self.clock_last,
                'collection_started_wall_s': self.started, 'collection_ended_wall_s': wall,
                'clock_first_callback_wall_s': self.clock_first_wall,
                'clock_last_callback_wall_s': self.clock_last_wall,
                'maximum_clock_callback_gap_wall_s': self.clock_max_gap,
                'elapsed_wall_s': wall - self.started,
                'full_packet_audit': False,
                'limitation': 'Contact-only messages and bounded ROS subscriptions; publisher and clock coverage checked. Historical runs without sensors remain unmeasured.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smoke-test', action='store_true', help='Dedicated cube probe world only; never a navigation metric')
    parser.add_argument('--smoke-evidence', type=Path, help='Successful nonempty probe evidence for this recorder/bridge; required before declaring observed zero')
    args = parser.parse_args()
    if not 20 <= args.seconds <= 1800 or args.output.exists():
        parser.error('duration must be within [20,1800] seconds; choose a new output file')
    rclpy.init()
    recorder = Recorder(smoke_test=args.smoke_test, smoke_evidence=args.smoke_evidence)
    signal.signal(signal.SIGTERM, lambda *_: recorder.halt.set())
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline and not recorder.halt.is_set():
            rclpy.spin_once(recorder, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        report = recorder.report()
        recorder.halt.set()
        recorder.worker.join(timeout=16)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        recorder.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0 if report['status'] == 'observed' and (not args.smoke_test or report['observed_episode_count'] > 0) else 2


if __name__ == '__main__':
    raise SystemExit(main())
