#!/usr/bin/env python3
"""Observe one product Vertex Action; never supply, replace or replay its plan."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from rcl_interfaces.msg import Log
from action_msgs.msg import GoalStatusArray
from geometry_msgs.msg import TwistStamped
from nav_msgs.msg import Path as NavPath
from pai_inspection_interfaces.action import ExecuteInstruction
from pai_inspection_core import TaskPlan, validate_plan
from std_srvs.srv import Trigger
from live_acceptance import Run
from observation_clock import observed_origin, elapsed_observation


def ledger_rows(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True) as connection:
        rows = connection.execute('SELECT id,model,charged_micro,status,usage_json,error FROM requests ORDER BY id').fetchall()
    return {row[0]: {'model': row[1], 'charged_micro_usd': row[2], 'status': row[3],
                     'usage_metadata': json.loads(row[4]) if row[4] else None, 'error_type': row[5]}
            for row in rows}


class OnlineRun(Run):
    def __init__(self, run_id):
        super().__init__(run_id)
        self.audits = []
        self.nav_commands = []
        self.guard_locks = []
        self.safe_commands = []
        self.permit_changes = []
        self.global_paths = []
        self.local_paths = []
        self.last_local_path_wall = None
        self.active_place = None
        self.goal_sequence = 0
        self.global_path_checks = {}
        self.global_path_hashes = {}
        self.nav_statuses = {}
        self.create_subscription(Log, '/rosout', self.on_log, 100)
        self.create_subscription(TwistStamped, '/nav_cmd_vel', self.on_command, 100)
        self.create_subscription(NavPath, '/plan', lambda message: self.on_path('planner', message), 10)
        self.create_subscription(NavPath, '/received_global_plan',
                                 lambda message: self.on_path('DWB_received', message), 10)
        self.create_subscription(NavPath, '/local_plan',
                                 lambda message: self.on_path('DWB_local', message), 10)
        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(GoalStatusArray, '/navigate_to_pose/_action/status', self.on_nav_status, qos)

    def on_log(self, message):
        if message.name == 'instruction_executor' and message.msg.startswith('PAI_AUDIT '):
            audit = json.loads(message.msg[len('PAI_AUDIT '):])
            self.audits.append(audit)
            self.event('product_audit', audit=audit)
        elif message.name == 'safety_guard' and message.msg.startswith('PAI_GUARD_LOCK '):
            diagnostic = json.loads(message.msg[len('PAI_GUARD_LOCK '):])
            self.guard_locks.append(diagnostic)
            self.event('guard_lock_diagnostic', diagnostic=diagnostic)

    def on_command(self, message):
        # Keep per-packet observations in this run only; no console logging and
        # no publication or alteration of the product's velocity stream.
        self.nav_commands.append({'receipt_wall_seconds': time.monotonic(),
            'observed_sim_seconds': self.sim_now,
            'stamp_sim_seconds': message.header.stamp.sec+message.header.stamp.nanosec*1e-9,
            'frame_id': message.header.frame_id,
            'linear_velocity': message.twist.linear.x,
            'angular_velocity': message.twist.angular.z})

    def on_velocity(self, message):
        super().on_velocity(message)
        self.safe_commands.append({'receipt_wall_seconds': time.monotonic(),
            'observed_sim_seconds': self.sim_now,
            'stamp_sim_seconds': message.header.stamp.sec+message.header.stamp.nanosec*1e-9,
            'linear_velocity': message.twist.linear.x,
            'angular_velocity': message.twist.angular.z,
            'last_observed_permit': None if self.state is None else self.state.motion_permitted,
            'last_observed_generation': None if self.state is None else self.state.generation})

    def on_safety(self, message):
        previous = self.state
        if previous is None or (previous.locked, previous.motion_permitted, previous.generation) != (
                message.locked, message.motion_permitted, message.generation):
            self.permit_changes.append({'receipt_wall_seconds': time.monotonic(),
                'observed_sim_seconds': self.sim_now, 'locked': message.locked,
                'motion_permitted': message.motion_permitted,
                'generation': message.generation, 'reason': message.reason})
        super().on_safety(message)

    def on_feedback(self, message):
        self.active_place = message.feedback.place_id
        if message.feedback.stage == 'navigating':
            self.goal_sequence += 1
        super().on_feedback(message)

    def on_path(self, source, message):
        now = time.monotonic()
        if source == 'DWB_local':
            if self.last_local_path_wall is not None and now-self.last_local_path_wall < 1:
                return
            self.last_local_path_wall = now
        else:
            key = (source, self.goal_sequence)
            if key in self.global_path_checks and now-self.global_path_checks[key] < 1:
                return
            self.global_path_checks[key] = now
        points = []
        for item in message.poses:
            p, q = item.pose.position, item.pose.orientation
            points.append({'position': [p.x, p.y, p.z], 'orientation': [q.x, q.y, q.z, q.w]})
        geometry_hash = hashlib.sha256(json.dumps(
            {'frame_id': message.header.frame_id, 'points': points},
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if source != 'DWB_local':
            if self.global_path_hashes.get(key) == geometry_hash:
                return
            self.global_path_hashes[key] = geometry_hash
        observation = {'source': source, 'receipt_wall_seconds': now,
            'observed_sim_seconds': self.sim_now, 'observed_place_id': self.active_place,
            'observed_goal_sequence': self.goal_sequence, 'geometry_sha256': geometry_hash,
            'action_uuid': self.evidence.get('action_uuid'), 'frame_id': message.header.frame_id,
            'stamp_sim_seconds': message.header.stamp.sec+message.header.stamp.nanosec*1e-9,
            'points': points}
        if source != 'DWB_local' and points:
            start = points[0]['position']
            sampled = next((point['position'] for point in points[1:]
                if math.hypot(point['position'][0]-start[0], point['position'][1]-start[1]) >= .5),
                points[-1]['position'])
            observation['sample_distance_m'] = .5
            observation['sampled_position'] = sampled
            observation['path_heading_from_start_rad'] = math.atan2(sampled[1]-start[1], sampled[0]-start[0])
            robot = self.final_pose()
            observation['robot_map_pose_at_receipt'] = robot
            if message.header.frame_id == 'map' and isinstance(robot, list):
                bearing = math.atan2(sampled[1]-robot[1], sampled[0]-robot[0])-robot[2]
                observation['sample_bearing_relative_to_receipt_map_pose_rad'] = math.atan2(
                    math.sin(bearing), math.cos(bearing))
        (self.local_paths if source == 'DWB_local' else self.global_paths).append(observation)

    def on_nav_status(self, message):
        for status in message.status_list:
            uuid = bytes(status.goal_info.goal_id.uuid).hex()
            if self.nav_statuses.get(uuid) != status.status:
                self.event('nav2_status', nav2_goal_uuid=uuid, status=status.status)
            self.nav_statuses[uuid] = status.status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--instruction', default='先去 B，再去 A，最后回起点')
    parser.add_argument('--expect', choices=['ordered_navigation', 'rejection', 'known_unreachable_return', 'indicator_inspection'], default='ordered_navigation')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, default=Path('/opt/pai/state/vertex-usage.sqlite3'))
    parser.add_argument('--image-sha256', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--test-profile', type=Path)
    parser.add_argument('--vision-phase', choices=['calibration', 'formal'])
    parser.add_argument('--expected-inspections', help='Scoring labels only, e.g. station_a:red,station_b:green; never sent to executor/model')
    parser.add_argument('--vision-config-sha256', help='Frozen calibration configuration SHA required for formal scoring')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('refusing to overwrite an existing run; no automatic retry')
    if args.expect == 'known_unreachable_return' and args.test_profile is None:
        parser.error('known_unreachable_return requires the isolated test profile evidence')
    profile_bytes = args.test_profile.read_bytes() if args.test_profile is not None else None
    expected_inspections = []
    if args.expect == 'indicator_inspection':
        if args.vision_phase is None or not args.expected_inspections:
            parser.error('inspection scoring requires a phase and explicit external ground truth')
        try:
            expected_inspections = [tuple(item.split(':')) for item in args.expected_inspections.split(',')]
            assert all(len(item)==2 and item[0] in ('station_a','station_b') and
                       item[1] in ('red','green','unknown') for item in expected_inspections)
        except Exception:
            parser.error('invalid scoring labels')
        if args.vision_phase == 'formal' and not args.vision_config_sha256:
            parser.error('formal scoring requires the previously frozen visual configuration SHA')
    rclpy.init()
    node = OnlineRun(args.run_id)
    node.evidence['passive_recording_paths']={'video_capture_json':f'/opt/pai/logs/{args.run_id}-video/capture.json','contacts_json':f'/opt/pai/logs/{args.run_id}-contacts.json','linkage':'same run_id; companion recorders are passive and independently timestamped'}
    node.evidence.update(source='online_product_VertexPlanner_then_real_ROS_Gazebo',
        online_inference_during_execution=True, original_instruction=args.instruction,
        instruction_sha256=hashlib.sha256(args.instruction.encode()).hexdigest(),
        image_sha256=args.image_sha256,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        collection_script_sha256=hashlib.sha256(Path(__file__).with_name('live_acceptance.py').read_bytes()).hexdigest(),
        expected_model=args.model, action_requests_sent=0, collision_statistics='not measured')
    if args.expect == 'indicator_inspection':
        node.evidence.update(vision_phase=args.vision_phase, inspection_scoring_labels=expected_inspections,
            frozen_vision_config_sha256=args.vision_config_sha256,
            labels_are_observer_only_and_not_model_or_controller_inputs=True)
    node.evidence['path_sampling_policy'] = {
        'global': 'first observed frame per source/navigation attempt; then geometry changes at most once per wall second; header stamp excluded from geometry hash',
        'local': 'at most once per wall second',
        'safe_commands': 'observed depth=1 receiver stream; completeness is not guaranteed'}
    if args.test_profile is not None:
        node.evidence['isolated_test_profile'] = json.loads(profile_bytes)
        node.evidence['isolated_test_profile_sha256'] = hashlib.sha256(profile_bytes).hexdigest()
    passed, before, baseline = False, None, set()
    try:
        before = ledger_rows(args.ledger)
        node.evidence['ledger_before'] = before
        if not node.resume.wait_for_service(timeout_sec=10) or not node.action.wait_for_server(timeout_sec=10):
            raise RuntimeError('product executor or guard unavailable')
        start = time.monotonic()
        while time.monotonic()-start < 2:
            rclpy.spin_once(node, timeout_sec=.05)
        baseline = set(node.nav_statuses)
        if node.state is None or not node.state.locked or node.state.motion_permitted:
            raise RuntimeError('expected startup locked; no old task may be moving')
        observed_origin(node, spin=lambda: rclpy.spin_once(node, timeout_sec=.05))
        response = node.spin_until(node.resume.call_async(Trigger.Request()), 3)
        node.event('resume_response', success=response.success, reason=response.message)
        if not response.success:
            raise RuntimeError(response.message)
        deadline = time.monotonic()+3
        while node.state.locked and time.monotonic()<deadline:
            rclpy.spin_once(node, timeout_sec=.05)
        if node.state.locked:
            raise RuntimeError('unlocked state not observed')
        goal = ExecuteInstruction.Goal()
        goal.raw_instruction = args.instruction
        started, started_sim = observed_origin(node, spin=lambda: rclpy.spin_once(node, timeout_sec=.05))
        node.evidence['action_requests_sent'] = 1
        node.event('raw_instruction_submitted', instruction=args.instruction)
        handle = node.spin_until(node.action.send_goal_async(goal, feedback_callback=node.on_feedback), 5)
        node.evidence['action_uuid'] = bytes(handle.goal_id.uuid).hex()
        if not handle.accepted:
            raise RuntimeError('product action rejected before model')
        result = node.spin_until(handle.get_result_async(), 1000)
        parsed = json.loads(result.result.result_json)
        node.event('instruction_terminal', action_status=result.status, result=parsed)
        node.evidence.update(elapsed_observation(started, started_sim, time.monotonic(), node.sim_now))
        post_start = time.monotonic()
        while time.monotonic()-post_start < 2:
            rclpy.spin_once(node, timeout_sec=.05)
        calls = [a for a in node.audits if a['kind']=='planning_completed'
                 and a['action_uuid']==node.evidence['action_uuid']]
        if len(calls) != 1:
            raise RuntimeError('missing or duplicate per-call model audit')
        planning = calls[0]['planning']
        node.evidence['planning'] = planning
        if planning['model'] != args.model or planning['instruction_sha256'] != node.evidence['instruction_sha256']:
            raise RuntimeError('online model/instruction provenance mismatch')
        plan = validate_plan(TaskPlan.from_json(planning['raw_response']), args.instruction)
        if plan.as_dict() != planning['validated_plan']:
            raise RuntimeError('raw model plan differs from actual validated plan')
        steps = parsed.get('steps', [])
        arrivals = [a for a in node.audits if a['kind']=='arrival_verified'
                    and a['action_uuid']==node.evidence['action_uuid']]
        new_goals = {key: value for key, value in node.nav_statuses.items() if key not in baseline}
        node.evidence['new_nav2_goals'] = new_goals
        node.evidence['arrival_observations'] = arrivals
        if args.expect == 'ordered_navigation':
            expected = [('navigate_to', 'station_b'), ('navigate_to', 'station_a'), ('return_to_base', None)]
            passed = (result.status == 4 and parsed.get('planner_mode') == 'vertex'
                and parsed.get('outcome') == 'completed' and planning['error'] is None
                and [(s.op, s.place_id) for s in plan.steps] == expected
                and [s.get('place_id') for s in steps] == ['station_b','station_a','base']
                and all(s.get('outcome')=='arrived' and s.get('nav2_status')==4 for s in steps)
                and [a['place_id'] for a in arrivals] == ['station_b','station_a','base']
                and all(a['settled_wall_s'] >= .5 for a in arrivals)
                and all(a['observation']['last']['fresh']
                    and a['observation']['last']['position_error_m'] <= .20
                    and a['observation']['last']['yaw_error_rad'] <= .25
                    and abs(a['observation']['last']['linear_velocity']) <= .01
                    and abs(a['observation']['last']['angular_velocity']) <= .02
                    and new_goals.get(a['nav2_goal_uuid']) == 4 for a in arrivals)
                and len(new_goals) == sum(s['attempts'] for s in steps))
        elif args.expect == 'indicator_inspection':
            inspections = [a['inspection'] for a in node.audits if a['kind']=='inspection_completed'
                and a['action_uuid']==node.evidence['action_uuid']]
            node.evidence['inspection_observations'] = inspections
            expected_plan = [step for place,_color in expected_inspections
                             for step in (('navigate_to',place),('inspect_indicator',place))]
            expected_plan.append(('return_to_base',None))
            expected_nav = [place for place,_color in expected_inspections]+['base']
            observed_labels = [(item['place_id'],item['color']) for item in inspections]
            files_verified = []
            for item in inspections:
                png = Path(item['png_path']) if item.get('png_path') else None
                files_verified.append(png is not None and png.is_file() and
                    hashlib.sha256(png.read_bytes()).hexdigest()==item.get('png_sha256'))
            passed = (result.status==4 and parsed.get('planner_mode')=='vertex'
                and parsed.get('outcome')=='completed' and planning['error'] is None
                and [(s.op,s.place_id) for s in plan.steps]==expected_plan
                and len(steps)==len(expected_plan)
                and [a['place_id'] for a in arrivals]==expected_nav
                and len(new_goals)==sum(s.get('attempts',0) for s in steps)
                and all(a['settled_wall_s']>=.5 and a['nav2_status']==4
                    and new_goals.get(a['nav2_goal_uuid'])==4
                    and a['observation']['last']['fresh']
                    and a['observation']['last']['position_error_m']<=.20
                    and a['observation']['last']['yaw_error_rad']<=.25 for a in arrivals)
                and observed_labels==expected_inspections and all(files_verified)
                and len(inspections)==len(expected_inspections)
                and all(item['stop_verified_wall_s']>=.5
                    and abs(item['linear_velocity'])<=.01 and abs(item['angular_velocity'])<=.02
                    and item['frame_receipt_wall_seconds']>item['capture_after_wall_seconds']
                    and item['frame_stamp_sim_seconds']>=item['capture_after_sim_seconds']
                    and 0<=item['frame_sim_age_at_decision_s']<=.5
                    and 0<=item['frame_wall_age_at_decision_s']<=.5
                    and (args.vision_phase=='calibration' or
                         item['thresholds_sha256']==args.vision_config_sha256) for item in inspections))
        elif args.expect == 'known_unreachable_return':
            failed_terminals = [a for a in node.audits if a['kind']=='navigation_terminal'
                and a['action_uuid']==node.evidence['action_uuid'] and a['place_id']=='station_a']
            passed = (args.test_profile is not None and result.status == 4
                and parsed.get('planner_mode') == 'vertex' and planning['error'] is None
                and parsed.get('outcome') == 'completed_with_failures'
                and not parsed.get('return_recommended')
                and [(s.op, s.place_id) for s in plan.steps] == [('navigate_to','station_a'),('return_to_base',None)]
                and len(steps)==2 and steps[0].get('place_id')=='station_a'
                and steps[0].get('outcome')=='failed' and steps[0].get('attempts')==2
                and steps[0].get('error_code') in (206,208)
                and steps[1].get('place_id')=='base' and steps[1].get('outcome')=='arrived'
                and steps[1].get('nav2_status')==4
                and len(failed_terminals)==2
                and all(a['nav2_status']==6 and a['error_code'] in (206,208)
                    and new_goals.get(a['nav2_goal_uuid'])==6 for a in failed_terminals)
                and len(arrivals)==1 and arrivals[0]['place_id']=='base'
                and arrivals[0]['settled_wall_s']>=.5 and arrivals[0]['observation']['last']['fresh']
                and arrivals[0]['observation']['last']['position_error_m']<=.20
                and arrivals[0]['observation']['last']['yaw_error_rad']<=.25
                and new_goals.get(arrivals[0]['nav2_goal_uuid'])==4
                and len(new_goals)==3)
        else:
            passed = (result.status == 4 and parsed.get('planner_mode')=='vertex'
                and parsed.get('outcome')=='rejected' and plan.decision=='reject'
                and not plan.steps and planning['error'] is None and not new_goals
                and node.moving_wall is None)
        post = [s for s in node.evidence['samples'] if s['wall_seconds']>=post_start]
        standstill = (len(post) >= 2 and post[-1]['wall_seconds']-post[0]['wall_seconds'] >= .5
            and all(abs(s['linear_velocity']) <= .01 and abs(s['angular_velocity']) <= .02 for s in post))
        node.evidence.update(post_terminal_samples=post, post_terminal_continuous_standstill=standstill)
        passed = passed and standstill
    except Exception as exc:
        passed = False
        node.event('failure', error=str(exc))
    finally:
        try:
            if node.stop.service_is_ready():
                response = node.spin_until(node.stop.call_async(Trigger.Request()), 3)
                node.event('cleanup_stop_response', success=response.success, reason=response.message)
            after = ledger_rows(args.ledger)
            new_requests = {key:value for key,value in after.items() if before is not None and key not in before}
            node.evidence.update(ledger_after=after, new_requests=new_requests,
                                 api_attempt_delta=len(after)-len(before) if before is not None else None)
            planning = node.evidence.get('planning', {})
            passed = passed and len(new_requests)==1 and planning.get('request_id') in new_requests
        except Exception as exc:
            passed = False
            node.event('cleanup_or_ledger_failure', error=str(exc))
        node.evidence.update(pass_=passed, online_real_model_execution_verified=passed,
                             final_map_pose=node.final_pose(), product_audits=node.audits,
                             nav_command_observations=node.nav_commands,
                             guard_lock_observations=node.guard_locks,
                             safe_command_observations=node.safe_commands,
                             permit_change_observations=node.permit_changes,
                             global_path_observations=node.global_paths,
                             local_path_observations=node.local_paths)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(node.evidence,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'pass':passed, 'evidence':str(args.output)},ensure_ascii=False))
        node.destroy_node()
        rclpy.shutdown()
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
