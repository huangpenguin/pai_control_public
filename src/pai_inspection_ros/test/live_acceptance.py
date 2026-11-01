#!/usr/bin/env python3
"""Single controller for a real simulated run; stub explicitly remains a test double.

Run after the environment's static diagnostics. Other agents must not publish
motion goals during this run. Evidence uses wall times independently of /clock.
"""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, qos_profile_sensor_data
from geometry_msgs.msg import TwistStamped
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import LaserScan
from std_srvs.srv import Trigger
from rclpy.time import Time
from tf2_ros import Buffer, TransformListener
from pai_inspection_interfaces.action import ExecuteInstruction
from pai_inspection_interfaces.msg import SafetyState


class Run(Node):
    def __init__(self, run_id):
        super().__init__('pai_live_acceptance')
        self.started = time.monotonic()
        self.evidence = {'run_id': run_id, 'source': 'real_ros_gazebo_with_explicit_stub_planner',
                         'events': [], 'samples': []}
        self.state = None
        self.moving_wall = None
        self.stop_requested = None
        self.first_safe_zero = None
        self.settled_since = None
        self.settled_wall = None
        self.last_sample_wall = None
        self.last_scan_wall = None
        self.last_scan_sim = None
        self.sim_now = None
        self.buffer = Buffer()
        self.listener = TransformListener(self.buffer, self)
        self.resume = self.create_client(Trigger, '/pai/resume')
        self.stop = self.create_client(Trigger, '/pai/stop')
        self.action = ActionClient(self, ExecuteInstruction, '/pai/execute_instruction')
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(SafetyState, '/pai/safety_state', self.on_safety, qos)
        self.create_subscription(Odometry, '/odom', self.on_odom, qos_profile_sensor_data)
        self.create_subscription(TwistStamped, '/safe_cmd_vel', self.on_velocity, 1)
        self.create_subscription(Clock, '/clock', self.on_clock, qos_profile_sensor_data)
        self.create_subscription(LaserScan, '/scan', self.on_scan, qos_profile_sensor_data)

    def on_clock(self, msg):
        self.sim_now = msg.clock.sec + msg.clock.nanosec*1e-9

    def on_scan(self, msg):
        self.last_scan_wall = time.monotonic()
        self.last_scan_sim = msg.header.stamp.sec + msg.header.stamp.nanosec*1e-9

    def event(self, kind, **fields):
        now = time.monotonic()
        self.evidence['events'].append({'event': kind, 'wall_seconds': now,
            'sim_seconds': self.sim_now,
            'scan_wall_age': now-self.last_scan_wall if self.last_scan_wall else None,
            'scan_sim_age': self.sim_now-self.last_scan_sim if self.sim_now is not None and
                self.last_scan_sim is not None else None, **fields})

    def on_safety(self, msg):
        if self.state is None or (msg.locked, msg.generation, msg.reason) != (
                self.state.locked, self.state.generation, self.state.reason):
            self.event('safety_state', locked=msg.locked, generation=msg.generation,
                       reason=msg.reason, motion_permitted=msg.motion_permitted)
        self.state = msg

    def on_feedback(self, msg):
        feedback = msg.feedback
        self.event('task_feedback', stage=feedback.stage, place_id=feedback.place_id,
                   message=feedback.message)

    def on_velocity(self, msg):
        if (self.stop_requested is not None and self.first_safe_zero is None and
                abs(msg.twist.linear.x) < 1e-9 and abs(msg.twist.angular.z) < 1e-9):
            self.first_safe_zero = time.monotonic()
            self.event('first_ros_safe_command_zero')

    def on_odom(self, msg):
        now = time.monotonic()
        v, w = msg.twist.twist.linear.x, msg.twist.twist.angular.z
        if abs(v) > 0.03 or abs(w) > 0.05:
            if self.moving_wall is None:
                self.moving_wall = now
                self.event('first_actual_motion')
        if self.stop_requested is not None:
            if abs(v) <= 0.01 and abs(w) <= 0.02:
                if self.settled_since is None:
                    self.settled_since = now
                if now - self.settled_since >= 0.5 and self.settled_wall is None:
                    self.settled_wall = now
                    self.event('actual_odom_continuous_standstill')
            else:
                self.settled_since = None
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        yaw = math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))
        if self.last_sample_wall is None or now - self.last_sample_wall >= 0.1:
            self.evidence['samples'].append({'wall_seconds': now, 'sim_seconds':
                msg.header.stamp.sec + msg.header.stamp.nanosec*1e-9,
                'odom_pose': [p.x, p.y, yaw], 'linear_velocity': v, 'angular_velocity': w,
                'scan_wall_age': now-self.last_scan_wall if self.last_scan_wall else None,
                'scan_sim_age': self.sim_now-self.last_scan_sim if self.sim_now is not None
                    and self.last_scan_sim is not None else None})
            self.last_sample_wall = now

    def final_pose(self):
        try:
            transform = self.buffer.lookup_transform('map', 'base_footprint', Time()).transform
            q = transform.rotation
            yaw = math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))
            return [transform.translation.x, transform.translation.y, yaw]
        except Exception as exc:
            return {'unavailable': str(exc)}

    def spin_until(self, future, seconds):
        started = time.monotonic()
        while not future.done() and time.monotonic() - started < seconds:
            rclpy.spin_once(self, timeout_sec=0.05)
        if not future.done():
            raise RuntimeError('response timeout')
        return future.result()


def terminate_node(case):
    """Terminate exactly one owned ROS process; launch does not respawn it."""
    token = (b'/safety_guard' if case == 'guard-loss' else b'__node:=safe_velocity_bridge')
    matched = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            args = (process / 'cmdline').read_bytes().split(b'\0')
            if any((arg.endswith(token) if case == 'guard-loss' else arg == token)
                   for arg in args):
                matched.append(int(process.name))
        except (OSError, ProcessLookupError):
            pass
    if len(matched) != 1:
        raise RuntimeError('fault injection requires exactly one target process: '+str(matched))
    os.kill(matched[0], signal.SIGTERM)
    return matched[0]


def set_paused(paused):
    result = subprocess.run(['gz', 'service', '-s', '/world/inspection/control',
        '--reqtype', 'gz.msgs.WorldControl', '--reptype', 'gz.msgs.Boolean',
        '--timeout', '2000', '--req', 'pause: '+str(paused).lower()],
        capture_output=True, text=True, timeout=3.0, check=True)
    if 'true' not in result.stdout:
        raise RuntimeError('Gazebo pause request not acknowledged: '+result.stdout)
    return result.stdout.strip()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=['singlepoint', 'multipoint', 'manual-stop', 'guard-loss',
                                         'bridge-loss', 'pause'], required=True)
    parser.add_argument('--instruction', default='去设备 A')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wait-seconds', type=float, default=320.0)
    args = parser.parse_args()
    if not 1 <= args.wait_seconds <= 2400:
        parser.error('--wait-seconds must be between 1 and 2400')
    if os.environ.get('PAI_PLANNER_MODE') != 'stub':
        raise SystemExit('Live acceptance requires explicit PAI_PLANNER_MODE=stub.')
    rclpy.init()
    node = Run(args.run_id)
    passed = False
    try:
        if not node.resume.wait_for_service(timeout_sec=10.0):
            raise RuntimeError('guard resume service missing')
        response = node.spin_until(node.resume.call_async(Trigger.Request()), 3.0)
        node.event('resume_response', success=response.success, reason=response.message)
        if not response.success:
            raise RuntimeError(response.message)
        state_wait = time.monotonic()
        while (node.state is None or node.state.locked) and time.monotonic()-state_wait < 3.0:
            rclpy.spin_once(node, timeout_sec=0.05)
        if node.state is None or node.state.locked:
            raise RuntimeError('resume safety state not observed')
        if not node.action.wait_for_server(timeout_sec=10.0):
            raise RuntimeError('instruction action missing')
        goal = ExecuteInstruction.Goal()
        goal.raw_instruction = args.instruction
        node.event('raw_instruction_submitted', instruction=args.instruction)
        handle = node.spin_until(node.action.send_goal_async(goal, feedback_callback=node.on_feedback), 5.0)
        if not handle.accepted:
            raise RuntimeError('instruction rejected')
        result_future = handle.get_result_async()
        started = time.monotonic()
        resumed_after_pause = False
        cleanup_stop_sent = False
        while not result_future.done() and time.monotonic()-started < args.wait_seconds:
            rclpy.spin_once(node, timeout_sec=0.05)
            if (args.case not in ('singlepoint', 'multipoint') and node.moving_wall is not None and
                    node.stop_requested is None and time.monotonic()-node.moving_wall >= 2.0):
                node.stop_requested = time.monotonic()
                if args.case == 'manual-stop':
                    node.event('manual_stop_request')
                    stop_response = node.spin_until(node.stop.call_async(Trigger.Request()), 3.0)
                    node.event('manual_stop_response', success=stop_response.success,
                               reason=stop_response.message)
                elif args.case == 'pause':
                    node.event('gazebo_pause_request', acknowledgement=set_paused(True))
                else:
                    node.event('fault_injected', case=args.case, pid=terminate_node(args.case))
            if args.case == 'pause' and node.stop_requested is not None and not resumed_after_pause:
                if time.monotonic()-node.stop_requested >= 0.8:
                    node.event('gazebo_resume_request', acknowledgement=set_paused(False))
                    resumed_after_pause = True
            if (args.case == 'bridge-loss' and node.stop_requested is not None and
                    not cleanup_stop_sent and time.monotonic()-node.stop_requested >= 5.0):
                # Enough observation before cleanup: driver must stop without ROS zero reaching it.
                node.event('fault_observation_complete_cleanup_stop')
                node.spin_until(node.stop.call_async(Trigger.Request()), 3.0)
                cleanup_stop_sent = True
        if not result_future.done():
            raise RuntimeError('instruction total wait timeout')
        if args.case == 'pause' and node.stop_requested is not None and not resumed_after_pause:
            while time.monotonic()-node.stop_requested < 0.8:
                rclpy.spin_once(node, timeout_sec=0.05)
            node.event('gazebo_resume_request', acknowledgement=set_paused(False))
            resumed_after_pause = True
        result = result_future.result()
        parsed = json.loads(result.result.result_json)
        node.event('instruction_terminal', action_status=result.status, result=parsed)
        # Each run must explicitly be a test double, never counted as P1 AI.
        if parsed.get('planner_mode') != 'stub-test-double':
            raise RuntimeError('this harness requires explicit stub test mode')
        extra_started = time.monotonic()
        while time.monotonic()-extra_started < 2.0:
            rclpy.spin_once(node, timeout_sec=0.05)
        if args.case == 'multipoint':
            steps = parsed.get('steps', [])
            passed = (result.result.outcome == 'completed' and
                [step.get('place_id') for step in steps] == ['station_b', 'station_a', 'base'] and
                all(step.get('nav2_status') == 4 and step.get('outcome') == 'arrived' for step in steps))
        elif args.case == 'singlepoint':
            passed = result.result.outcome == 'completed' and any(
                step.get('nav2_status') == 4 and step.get('outcome') == 'arrived'
                for step in parsed.get('steps', []))
        else:
            if args.case == 'manual-stop':
                passed = (result.result.outcome == 'stopped' and node.first_safe_zero is not None
                          and node.settled_wall is not None and node.state is not None and node.state.locked)
            elif args.case == 'guard-loss':
                # Loss of the safety control service is a control fault; an abort is correct.
                passed = (result.result.outcome in ('stopped', 'aborted') and
                          node.settled_wall is not None)
            else:
                passed = (result.result.outcome == 'stopped' and node.settled_wall is not None
                          and node.state is not None and node.state.locked)
            if node.settled_wall is not None:
                after_settle = [sample for sample in node.evidence['samples']
                                if sample['wall_seconds'] >= node.settled_wall]
                passed = passed and bool(after_settle) and all(
                    abs(sample['linear_velocity']) <= 0.01 and
                    abs(sample['angular_velocity']) <= 0.02 for sample in after_settle)
            node.evidence.update(stop_request_wall=node.stop_requested,
                                 first_ros_safe_zero_wall=node.first_safe_zero,
                                 odom_settled_wall=node.settled_wall,
                                 ros_command_zero_delay=(node.first_safe_zero-node.stop_requested
                                     if node.first_safe_zero is not None else None),
                                 continuous_odom_standstill_delay=(node.settled_wall-node.stop_requested
                                     if node.settled_wall is not None else None))
    except Exception as exc:
        node.event('failure', error=str(exc))
        if node.stop.service_is_ready():
            try:
                node.spin_until(node.stop.call_async(Trigger.Request()), 3.0)
            except Exception:
                pass
    finally:
        if args.case == 'pause':
            try:
                set_paused(False)
            except Exception:
                pass
        node.evidence.update(case=args.case, pass_=passed, real_ai_verified=False,
                             final_map_pose=node.final_pose())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(node.evidence, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({'run_id': args.run_id, 'pass': passed, 'real_ai_verified': False,
                          'evidence': str(args.output)}, ensure_ascii=False))
        node.destroy_node()
        rclpy.shutdown()
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
