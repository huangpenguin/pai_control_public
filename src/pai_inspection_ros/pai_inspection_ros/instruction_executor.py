"""Raw-instruction ROS action backed by a real Vertex planner by default."""
import concurrent.futures
import hashlib
import json
import math
import os
import threading
import time

import yaml
import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, ActionClient, GoalResponse, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image
from std_srvs.srv import Trigger
from action_msgs.msg import GoalStatus
from tf2_ros import Buffer, TransformListener, TransformException
from pai_inspection_interfaces.action import ExecuteInstruction
from pai_inspection_interfaces.msg import SafetyState
from pai_inspection_interfaces.srv import SetMotionPermit
from pai_inspection_core import VertexPlanner, validate_plan, PlanValidationError
from .execution_core import GoalBudget, ArrivalCheck
from .safety_guard import stamp_seconds
from .camera_capture import CameraCapture
from .indicator_vision import classify_indicator


class TaskInterrupted(Exception):
    pass


class NavigationFault(Exception):
    pass


class NavigationCancellationUnconfirmed(NavigationFault):
    pass


class InstructionExecutor(Node):
    def __init__(self, planner=None):
        super().__init__('instruction_executor')
        self.declare_parameter('stations_file', '/opt/pai/simulation/config/stations.yaml')
        self.declare_parameter('max_steps', 8)
        self.declare_parameter('planner_mode', os.getenv('PAI_PLANNER_MODE', 'vertex'))
        self.declare_parameter('inspection_directory', '/opt/pai/logs/inspections')
        with open(self.get_parameter('stations_file').value, encoding='utf-8') as stream:
            self.places = yaml.safe_load(stream)['places']
        for place in ('base', 'station_a', 'station_b'):
            pose = self.places[place]
            if not all(math.isfinite(float(pose[key])) for key in ('x', 'y', 'yaw')):
                raise ValueError('invalid station pose: ' + place)
        mode = self.get_parameter('planner_mode').value
        if planner is not None:
            self.planner = planner
            self.planner_mode = 'injected-test-double'
        elif mode == 'vertex':
            self.planner = None  # Client/auth initialized on first explicit request.
            self.planner_mode = 'vertex'
        elif mode == 'stub':
            from pai_inspection_core.cli import StubPlanner
            self.planner = StubPlanner()
            self.planner_mode = 'stub-test-double'
            self.get_logger().warning('Explicit STUB TEST DOUBLE; this run is not real AI.')
        else:
            raise ValueError('planner_mode must be vertex or explicit stub')
        self.worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.mutex = threading.RLock()
        self.active = False
        self.navigation_restart_required = False
        self.nav_goal = None
        self.nav_attempt = 0
        self.pending_nav_future = None
        self.pending_result_future = None
        self.safety = None
        self.safety_wall = None
        self.odom = None
        self.odom_wall = None
        self.camera = CameraCapture()
        group = ReentrantCallbackGroup()
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(SafetyState, '/pai/safety_state', self.on_safety, qos,
                                 callback_group=group)
        self.create_subscription(Odometry, '/odom', self.on_odom, qos_profile_sensor_data,
                                 callback_group=group)
        self.create_subscription(Image, '/camera/image_raw', self.camera.receive,
                                 qos_profile_sensor_data, callback_group=group)
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.nav = ActionClient(self, NavigateToPose, '/navigate_to_pose', callback_group=group)
        self.permit = self.create_client(SetMotionPermit, '/pai/set_motion_permit', callback_group=group)
        self.stop = self.create_client(Trigger, '/pai/stop', callback_group=group)
        self.navigation_fault = self.create_client(Trigger, '/pai/lock_navigation_fault', callback_group=group)
        self.server = ActionServer(self, ExecuteInstruction, '/pai/execute_instruction',
                                   execute_callback=self.execute, goal_callback=self.on_goal,
                                   cancel_callback=self.on_cancel, callback_group=group)
        self.get_logger().info('Instruction action ready; planner=' + self.planner_mode)

    def on_goal(self, request):
        with self.mutex:
            if self.active or self.navigation_restart_required or not request.raw_instruction.strip():
                return GoalResponse.REJECT
            self.active = True
            return GoalResponse.ACCEPT

    def on_cancel(self, _handle):
        self.request_stop()
        return CancelResponse.ACCEPT

    def request_stop(self):
        if self.stop.service_is_ready():
            self.stop.call_async(Trigger.Request())
        with self.mutex:
            self.nav_attempt += 1
            if self.nav_goal:
                self.nav_goal.cancel_goal_async()

    def request_navigation_fault(self):
        with self.mutex:
            self.navigation_restart_required = True
            self.nav_attempt += 1
        if self.navigation_fault.service_is_ready():
            self.navigation_fault.call_async(Trigger.Request())
        else:
            self.request_stop()

    def on_safety(self, msg):
        with self.mutex:
            old = self.safety
            if msg.reason == 'navigation_cancel_unconfirmed_restart_chain_required':
                self.navigation_restart_required = True
            self.safety, self.safety_wall = msg, time.monotonic()
            if msg.locked or (old and old.generation != msg.generation):
                self.nav_attempt += 1
                if self.nav_goal:
                    self.nav_goal.cancel_goal_async()

    def on_odom(self, msg):
        with self.mutex:
            self.odom, self.odom_wall = msg, time.monotonic()

    def check_current(self, handle, generation):
        with self.mutex:
            state = self.safety
            stale = self.safety_wall is None or time.monotonic() - self.safety_wall > 0.5
            if (not handle.is_active or handle.is_cancel_requested or stale or state is None or
                    state.locked or state.generation != generation):
                raise TaskInterrupted('task invalidated: ' + ('guard_stale' if stale else
                                      (state.reason if state else 'guard_missing')))

    def wait_future(self, future, handle, generation, timeout=None):
        started = time.monotonic()
        while not future.done():
            self.check_current(handle, generation)
            if timeout is not None and time.monotonic() - started >= timeout:
                raise NavigationFault('service_or_action_response_timeout')
            time.sleep(0.02)
        self.check_current(handle, generation)
        return future.result()

    def set_permit(self, enabled, generation, handle=None):
        request = SetMotionPermit.Request()
        request.generation, request.enabled = generation, enabled
        if not self.permit.service_is_ready():
            if enabled:
                raise NavigationFault('guard_permit_service_missing')
            return
        future = self.permit.call_async(request)
        if enabled:
            result = self.wait_future(future, handle, generation, timeout=0.3)
            if not result.accepted:
                raise TaskInterrupted(result.reason)

    def feedback(self, handle, stage, place='', message=''):
        feedback = ExecuteInstruction.Feedback()
        feedback.stage, feedback.place_id, feedback.message = stage, place, message
        handle.publish_feedback(feedback)

    def parse(self, raw, *, audit_callback=None):
        if self.planner is None:
            self.planner = VertexPlanner()
        if audit_callback is not None:
            return self.planner.parse(raw, audit_callback=audit_callback)
        return self.planner.parse(raw)

    @staticmethod
    def action_uuid(handle):
        goal_id = getattr(handle, 'goal_id', None)
        return bytes(goal_id.uuid).hex() if goal_id is not None else 'test-handle-without-uuid'

    def audit(self, kind, handle=None, **fields):
        record = {'kind': kind, **fields}
        if handle is not None:
            record['action_uuid'] = self.action_uuid(handle)
        self.get_logger().info('PAI_AUDIT ' + json.dumps(record, ensure_ascii=False, allow_nan=False))

    def parse_observed(self, raw, action_uuid, generation):
        if self.planner_mode != 'vertex':
            return self.parse(raw)
        raw_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        self.audit('planning_started', action_uuid=action_uuid,
                   instruction_sha256=raw_hash, instruction=raw, task_generation=generation)
        def emit(snapshot_json):
            # Core supplies a per-call immutable snapshot from local variables.
            # A late callback keeps this old UUID; it never touches task state.
            snapshot = json.loads(snapshot_json)
            if snapshot.get('instruction') != raw or snapshot.get('instruction_sha256') != raw_hash:
                raise ValueError('model audit instruction binding mismatch')
            self.audit('planning_completed', action_uuid=action_uuid,
                       task_generation=generation, planning=snapshot)
        return self.parse(raw, audit_callback=emit)

    def pose_goal(self, target):
        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x, goal.pose.pose.position.y = float(target['x']), float(target['y'])
        yaw = float(target['yaw'])
        goal.pose.pose.orientation.z = math.sin(yaw / 2)
        goal.pose.pose.orientation.w = math.cos(yaw / 2)
        return goal

    def track_late_goal(self, future, handle, generation, attempt):
        # Check and register under one lock; stop cannot slip between them.
        try:
            goal = future.result()
            if goal and goal.accepted:
                with self.mutex:
                    try:
                        self.check_current(handle, generation)
                        if attempt != self.nav_attempt:
                            raise TaskInterrupted('obsolete_navigation_attempt')
                    except TaskInterrupted:
                        goal.cancel_goal_async()
                        return
                    self.nav_goal = goal
        except Exception as exc:
            self.get_logger().warning('Goal response failed: ' + str(exc))

    def cancel_current_goal(self):
        with self.mutex:
            self.nav_attempt += 1
            if self.nav_goal:
                self.nav_goal.cancel_goal_async()
                self.nav_goal = None

    def confirm_navigation_termination(self):
        """Stop paths may resume only after an actually confirmed Nav2 terminal."""
        self.cancel_current_goal()
        with self.mutex:
            sent = self.pending_nav_future
            result_future = self.pending_result_future
        if sent is None and result_future is None:
            return True
        started = time.monotonic()
        cancel_sent = False
        while time.monotonic() - started < 2.0:
            try:
                if result_future is None and sent is not None and sent.done():
                    nav_handle = sent.result()
                    if nav_handle is None:
                        return False
                    if not nav_handle.accepted:
                        return True
                    if not cancel_sent:
                        nav_handle.cancel_goal_async()
                        cancel_sent = True
                    result_future = nav_handle.get_result_async()
                if result_future is not None and result_future.done():
                    terminal = result_future.result()
                    confirmed = terminal is not None and terminal.status in (
                        GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_ABORTED, GoalStatus.STATUS_CANCELED)
                    if confirmed:
                        with self.mutex:
                            self.pending_nav_future = None
                            self.pending_result_future = None
                    return confirmed
            except Exception:
                return False
            time.sleep(0.02)
        return False

    def navigate(self, handle, generation, place):
        target = self.places[place]
        target_pose = tuple(float(target[k]) for k in ('x', 'y', 'yaw'))
        budget = GoalBudget(self.get_clock().now().nanoseconds / 1e9, time.monotonic())
        while budget.start_attempt(self.get_clock().now().nanoseconds / 1e9, time.monotonic()):
            self.check_current(handle, generation)
            if not self.nav.server_is_ready():
                raise NavigationFault('nav2_action_server_not_ready')
            self.set_permit(True, generation, handle)
            self.feedback(handle, 'navigating', place, 'attempt=' + str(budget.attempts))
            with self.mutex:
                self.nav_attempt += 1
                attempt = self.nav_attempt
            future = self.nav.send_goal_async(self.pose_goal(target))
            with self.mutex:
                self.pending_nav_future = future
            future.add_done_callback(lambda f, attempt_id=attempt: self.track_late_goal(f, handle, generation, attempt_id))
            try:
                nav_handle = self.wait_future(future, handle, generation, timeout=3.0)
            except NavigationFault as exc:
                raise NavigationCancellationUnconfirmed('navigation_goal_response_unconfirmed') from exc
            if not nav_handle.accepted:
                raise NavigationFault('nav2_goal_rejected')
            with self.mutex:
                self.check_current(handle, generation)
                if attempt != self.nav_attempt:
                    nav_handle.cancel_goal_async()
                    raise TaskInterrupted('obsolete_navigation_attempt')
                self.nav_goal = nav_handle
            result_future = nav_handle.get_result_async()
            with self.mutex:
                self.pending_result_future = result_future
            last_renew = time.monotonic()
            timed_out = False
            while not result_future.done():
                self.check_current(handle, generation)
                if budget.expired(self.get_clock().now().nanoseconds / 1e9, time.monotonic()):
                    timed_out = True
                    break
                if time.monotonic() - last_renew >= 0.2:
                    self.set_permit(True, generation, handle)
                    last_renew = time.monotonic()
                time.sleep(0.02)
            if timed_out:
                self.set_permit(False, generation)
                self.cancel_current_goal()
                cancel_started = time.monotonic()
                while not result_future.done() and time.monotonic() - cancel_started < 2.0:
                    self.check_current(handle, generation)
                    time.sleep(0.02)
                terminal_confirmed = False
                if result_future.done():
                    try:
                        terminal = result_future.result()
                        terminal_confirmed = terminal is not None and terminal.status in (
                            GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_ABORTED, GoalStatus.STATUS_CANCELED)
                    except Exception:
                        terminal_confirmed = False
                if not terminal_confirmed:
                    self.request_navigation_fault()
                    raise NavigationCancellationUnconfirmed('navigation_cancel_terminal_not_confirmed')
                with self.mutex:
                    self.pending_nav_future = None
                    self.pending_result_future = None
                return {'place_id': place, 'outcome': 'failed', 'reason': 'goal_total_budget_exhausted',
                        'attempts': budget.attempts}
            self.check_current(handle, generation)
            result = result_future.result()
            self.audit('navigation_terminal', handle, place_id=place,
                       nav2_goal_uuid=self.action_uuid(nav_handle), nav2_status=getattr(result, 'status', None),
                       attempt=budget.attempts,
                       error_code=int(getattr(getattr(result, 'result', None), 'error_code', 0)))
            if result is not None and result.status in (
                    GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_ABORTED, GoalStatus.STATUS_CANCELED):
                with self.mutex:
                    self.pending_nav_future = None
                    self.pending_result_future = None
            self.cancel_current_goal()
            self.set_permit(False, generation)
            if result.status == GoalStatus.STATUS_SUCCEEDED:
                self.feedback(handle, 'verifying_arrival', place)
                arrival = ArrivalCheck()
                settle_started = time.monotonic()
                arrival_observation = {'iterations': 0, 'fresh_iterations': 0,
                                       'pose_within_iterations': 0, 'stationary_iterations': 0,
                                       'max_continuous_settle_wall_s': 0.0,
                                       'transform_exceptions': 0, 'last': None}
                while time.monotonic() - settle_started <= 5.0:
                    self.check_current(handle, generation)
                    if budget.expired(self.get_clock().now().nanoseconds / 1e9, time.monotonic()):
                        raise NavigationFault('arrival_budget_exhausted')
                    try:
                        tf = self.tf_buffer.lookup_transform('map', 'base_footprint', Time())
                        q = tf.transform.rotation
                        yaw = math.atan2(2 * (q.w*q.z + q.x*q.y), 1 - 2 * (q.y*q.y + q.z*q.z))
                        pose = (tf.transform.translation.x, tf.transform.translation.y, yaw)
                        with self.mutex:
                            odom, odom_wall = self.odom, self.odom_wall
                        now_sim = self.get_clock().now().nanoseconds / 1e9
                        fresh = (odom is not None and odom_wall is not None and
                                 time.monotonic() - odom_wall <= 0.5 and
                                 0 <= now_sim - stamp_seconds(odom.header.stamp) <= 0.5 and
                                 abs(now_sim - stamp_seconds(tf.header.stamp)) <= 0.6)
                        arrival_observation['iterations'] += 1
                        arrival_observation['fresh_iterations'] += int(fresh)
                        yaw_error = math.atan2(math.sin(pose[2]-target_pose[2]),
                                               math.cos(pose[2]-target_pose[2]))
                        arrival_observation['last'] = {
                            'sim_seconds': now_sim, 'pose': list(pose),
                            'position_error_m': math.hypot(pose[0]-target_pose[0], pose[1]-target_pose[1]),
                            'yaw_error_rad': abs(yaw_error), 'fresh': fresh,
                            'odom_wall_age_s': time.monotonic()-odom_wall if odom_wall is not None else None,
                            'odom_sim_age_s': now_sim-stamp_seconds(odom.header.stamp) if odom else None,
                            'tf_sim_age_s': now_sim-stamp_seconds(tf.header.stamp),
                            'linear_velocity': odom.twist.twist.linear.x if odom else None,
                            'angular_velocity': odom.twist.twist.angular.z if odom else None}
                        observed = arrival_observation['last']
                        arrival_observation['pose_within_iterations'] += int(
                            observed['position_error_m'] <= .20 and observed['yaw_error_rad'] <= .25)
                        arrival_observation['stationary_iterations'] += int(odom is not None and
                            abs(odom.twist.twist.linear.x) <= .01 and abs(odom.twist.twist.angular.z) <= .02)
                        if odom and arrival.update(pose, target_pose, odom.twist.twist.linear.x,
                                                   odom.twist.twist.angular.z, fresh, succeeded=True):
                            self.audit('arrival_verified', handle, place_id=place,
                                       nav2_goal_uuid=self.action_uuid(nav_handle),
                                       nav2_status=result.status, attempts=budget.attempts,
                                       settled_wall_s=time.monotonic()-arrival.settled_since,
                                       observation=arrival_observation)
                            return {'place_id': place, 'outcome': 'arrived', 'attempts': budget.attempts,
                                    'pose': list(pose), 'nav2_status': result.status}
                        if arrival.settled_since is not None:
                            arrival_observation['max_continuous_settle_wall_s'] = max(
                                arrival_observation['max_continuous_settle_wall_s'],
                                time.monotonic()-arrival.settled_since)
                    except TransformException:
                        arrival_observation['transform_exceptions'] += 1
                        arrival.settled_since = None
                    time.sleep(0.02)
                self.audit('arrival_verification_failed', handle, place_id=place,
                           nav2_goal_uuid=self.action_uuid(nav_handle),
                           nav2_status=result.status, attempts=budget.attempts,
                           verification_wall_s=time.monotonic()-settle_started,
                           observation=arrival_observation)
                raise NavigationFault('succeeded_without_verified_arrival')
            error_code = int(getattr(result.result, 'error_code', 0))
            error_msg = getattr(result.result, 'error_msg', '')
            # Only explicit planner destination/path failures are recoverable.
            if result.status == GoalStatus.STATUS_ABORTED and error_code in (204, 206, 207, 208):
                if budget.attempts < 2 and not budget.expired(
                        self.get_clock().now().nanoseconds / 1e9, time.monotonic()):
                    self.feedback(handle, 'retrying', place, error_msg)
                    continue
                return {'place_id': place, 'outcome': 'failed', 'reason': error_msg,
                        'error_code': error_code, 'attempts': budget.attempts}
            raise NavigationFault('nav2_control_or_navigation_failure: ' + str(error_code) + ' ' + error_msg)
        return {'place_id': place, 'outcome': 'failed', 'reason': 'goal_total_budget_exhausted',
                'attempts': budget.attempts}

    def inspect_indicator(self, handle, generation, place, inspection_index):
        """Inspect pixels after stopped arrival; optional camera failure is unknown."""
        self.set_permit(False, generation)
        self.feedback(handle, 'verifying_inspection_stop', place)
        started, settled_since = time.monotonic(), None
        while time.monotonic()-started <= 3.0:
            self.check_current(handle, generation)
            with self.mutex:
                odom, odom_wall, state = self.odom, self.odom_wall, self.safety
            now_wall = time.monotonic()
            now_sim = self.get_clock().now().nanoseconds/1e9
            if (odom is None or odom_wall is None or now_wall-odom_wall > .5 or
                    not 0 <= now_sim-stamp_seconds(odom.header.stamp) <= .5):
                raise NavigationFault('inspection_odom_stale')
            v, w = odom.twist.twist.linear.x, odom.twist.twist.angular.z
            stationary = (math.isfinite(v) and math.isfinite(w) and abs(v) <= .01 and
                          abs(w) <= .02 and not state.motion_permitted)
            if not stationary:
                settled_since = None
            elif settled_since is None:
                settled_since = now_wall
            elif now_wall-settled_since >= .5:
                break
            time.sleep(.02)
        else:
            raise NavigationFault('inspection_stop_not_confirmed')
        self.feedback(handle, 'inspecting_indicator', place)
        after_wall, after_sim = time.monotonic(), self.get_clock().now().nanoseconds/1e9
        result = {'op': 'inspect_indicator', 'place_id': place, 'outcome': 'inspected',
                  'color': 'unknown', 'reason': 'no_image', 'png_path': None,
                  'action_uuid': self.action_uuid(handle), 'task_generation': generation,
                  'capture_after_sim_seconds': after_sim,
                  'capture_after_wall_seconds': after_wall,
                  'stop_verified_wall_s': now_wall-settled_since}
        config = classify_indicator(None)
        result.update(thresholds=config['thresholds'], thresholds_sha256=config['thresholds_sha256'])
        while time.monotonic()-after_wall <= 3.0:
            self.check_current(handle, generation)
            frame, reason = self.camera.fresh_frame(after_wall=after_wall, after_sim=after_sim,
                sim_now=self.get_clock().now().nanoseconds/1e9)
            if frame is None:
                result['reason'] = reason
                time.sleep(.02)
                continue
            image, receipt = frame
            result.update({'frame_id': image.header.frame_id,
                           'frame_stamp_sim_seconds': stamp_seconds(image.header.stamp),
                           'frame_receipt_wall_seconds': receipt, 'encoding': image.encoding})
            try:
                rgb = self.camera.to_rgb(image)
                result.update(classify_indicator(rgb))
                self.check_current(handle, generation)
                result.update(self.camera.save_png(rgb,
                    self.get_parameter('inspection_directory').value,
                    self.action_uuid(handle)+'-'+str(inspection_index)+'-'+place+'.png'))
            except TaskInterrupted:
                raise
            except Exception as exc:
                result.update(color='unknown', reason='image_processing_failed',
                              processing_error_type=type(exc).__name__, png_path=None)
            break
        else:
            result['camera_wait_timed_out'] = True
        # A stop or fault during conversion/disk I/O invalidates this result.
        self.check_current(handle, generation)
        if 'frame_stamp_sim_seconds' in result:
            result['frame_sim_age_at_decision_s'] = self.get_clock().now().nanoseconds/1e9-result['frame_stamp_sim_seconds']
            result['frame_wall_age_at_decision_s'] = time.monotonic()-result['frame_receipt_wall_seconds']
            if not (0 <= result['frame_sim_age_at_decision_s'] <= .5 and
                    0 <= result['frame_wall_age_at_decision_s'] <= .5):
                result.update(color='unknown', reason='stale')
        with self.mutex:
            odom = self.odom
        result['linear_velocity'] = odom.twist.twist.linear.x
        result['angular_velocity'] = odom.twist.twist.angular.z
        try:
            transform = self.tf_buffer.lookup_transform('map', 'base_footprint', Time())
            q = transform.transform.rotation
            result['pose'] = [transform.transform.translation.x, transform.transform.translation.y,
                math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))]
            result['pose_stamp_sim_seconds'] = stamp_seconds(transform.header.stamp)
        except TransformException:
            # The independent guard still checks necessary TF freshness.
            result['pose'] = None
        result['inspection_status'] = {'red': 'anomaly', 'green': 'normal',
                                       'unknown': 'unconfirmed'}[result['color']]
        self.audit('inspection_completed', handle, inspection=result)
        return result

    def execute(self, handle):
        steps = []
        generation = None
        outcome, message = 'rejected', ''
        return_requested = False
        parse_future = None
        try:
            with self.mutex:
                generation = self.safety.generation if self.safety else None
            if generation is None:
                raise TaskInterrupted('guard_missing')
            self.check_current(handle, generation)
            raw = handle.request.raw_instruction
            self.feedback(handle, 'planning', message='planner=' + self.planner_mode)
            parse_future = self.worker.submit(self.parse_observed, raw,
                                             self.action_uuid(handle), generation)
            plan = self.wait_future(parse_future, handle, generation, timeout=35.0)
            plan = validate_plan(plan, raw)
            if plan.decision != 'accept' or plan.unresolved:
                message = 'whole task rejected: unresolved instruction'
            elif len(plan.steps) > int(self.get_parameter('max_steps').value):
                message = 'whole task rejected: stage step limit exceeded'
            elif any(step.op not in ('navigate_to', 'return_to_base', 'stop', 'inspect_indicator') for step in plan.steps):
                message = 'whole task rejected: operation not enabled in this stage'
            else:
                return_requested = any(step.op == 'return_to_base' for step in plan.steps)
                for step in plan.steps:
                    self.check_current(handle, generation)
                    if step.op == 'stop':
                        self.request_stop()
                        steps.append({'outcome': 'stopped'})
                        outcome, message = 'stopped', 'independent safety stop requested'
                        break
                    place = 'base' if step.op == 'return_to_base' else step.place_id
                    if place not in self.places or place not in ('base', 'station_a', 'station_b'):
                        raise NavigationFault('non_whitelisted_place')
                    if step.op == 'inspect_indicator':
                        if not steps or steps[-1].get('place_id') != place or steps[-1].get('outcome') != 'arrived':
                            result = {'op': step.op, 'place_id': place, 'outcome': 'inspected',
                                      'color': 'unknown', 'inspection_status': 'unconfirmed',
                                      'reason': 'navigation_not_arrived', 'png_path': None}
                        else:
                            result = self.inspect_indicator(handle, generation, place, len(steps))
                    else:
                        result = self.navigate(handle, generation, place)
                    with self.mutex:
                        self.check_current(handle, generation)
                        steps.append(result)
                    if result['outcome'] == 'failed' and place == 'base':
                        raise NavigationFault('return_to_base_failed')
                else:
                    outcome = 'completed' if all(s['outcome'] in ('arrived', 'inspected') for s in steps) else 'completed_with_failures'
                    message = 'task completed' if outcome == 'completed' else 'known station failed; remaining steps completed; submit a new return instruction if needed'
                handle.succeed()
        except NavigationCancellationUnconfirmed as exc:
            outcome, message = 'aborted', str(exc) + '; restart whole simulation/Nav2/guard chain required'
            if generation is not None:
                self.set_permit(False, generation)
            self.cancel_current_goal()
            self.request_navigation_fault()
            handle.abort()
        except PlanValidationError as exc:
            outcome, message = 'rejected', str(exc)
            handle.succeed()
        except TaskInterrupted as exc:
            outcome, message = 'stopped', str(exc)
            if generation is not None:
                self.set_permit(False, generation)
            if not self.confirm_navigation_termination():
                self.request_navigation_fault()
                message += '; restart whole simulation/Nav2/guard chain required'
            if handle.is_cancel_requested:
                handle.canceled()
            else:
                handle.abort()
        except Exception as exc:
            outcome, message = 'aborted', str(exc)
            if generation is not None:
                self.set_permit(False, generation)
            if self.confirm_navigation_termination():
                self.request_stop()
            else:
                self.request_navigation_fault()
                message += '; restart whole simulation/Nav2/guard chain required'
            handle.abort()
        finally:
            if parse_future is not None and not parse_future.done():
                parse_future.cancel()  # Discard queued work; a running HTTP response remains obsolete.
            if generation is not None:
                self.set_permit(False, generation)
            if handle.is_active:
                handle.succeed()  # A rejected plan is a completed application-level decision.
            with self.mutex:
                self.active = False
        response = ExecuteInstruction.Result()
        returned_to_base = any(s.get('place_id') == 'base' and s.get('outcome') == 'arrived' for s in steps)
        if outcome == 'completed_with_failures' and returned_to_base:
            message = 'known station failed; remaining steps and return to base completed'
        response.outcome, response.message = outcome, message
        response.result_json = json.dumps({'outcome': outcome, 'message': message,
                                           'planner_mode': self.planner_mode, 'steps': steps,
                                           'return_requested': return_requested,
                                          'return_recommended': any(s.get('outcome') == 'failed' for s in steps)
                                                                and not returned_to_base},
                                          ensure_ascii=False, allow_nan=False)
        return response


def main(args=None):
    rclpy.init(args=args)
    node = InstructionExecutor()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.request_stop()
        node.worker.shutdown(wait=False, cancel_futures=True)
        executor.shutdown(timeout_sec=2.0)
        node.destroy_node()
        rclpy.shutdown()
