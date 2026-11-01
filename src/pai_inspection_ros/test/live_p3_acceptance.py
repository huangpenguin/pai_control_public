#!/usr/bin/env python3
"""Real ROS Action fault acceptance; planner/config injection is explicit."""
import argparse
import json
from pathlib import Path
import time

import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from action_msgs.msg import GoalStatusArray
from std_srvs.srv import Trigger
from pai_inspection_interfaces.action import ExecuteInstruction

from live_acceptance import Run


class FaultRun(Run):
    def __init__(self, run_id):
        super().__init__(run_id)
        self.nav_ids = set()
        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(GoalStatusArray, '/navigate_to_pose/_action/status',
                                 self.on_nav_status, qos)

    def on_nav_status(self, msg):
        for status in msg.status_list:
            goal_id = bytes(status.goal_info.goal_id.uuid).hex()
            if goal_id not in self.nav_ids:
                self.event('observed_nav2_goal_uuid', goal_uuid=goal_id, status=status.status)
                self.nav_ids.add(goal_id)

    def observe(self, duration):
        start = time.monotonic()
        while time.monotonic()-start < duration:
            rclpy.spin_once(self, timeout_sec=0.02)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=('unknown', 'unreachable', 'model-timeout'), required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rclpy.init()
    node = FaultRun(args.run_id)
    if args.case == 'model-timeout':
        node.evidence['source'] = 'real_ros_gazebo_with_injected_slow_planner'
    passed = False
    try:
        node.observe(2.0)  # Receive existing status before counting newly accepted goals.
        baseline = set(node.nav_ids)
        node.evidence['baseline_nav2_goal_uuids'] = sorted(baseline)
        if not node.resume.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('guard resume missing')
        response = node.spin_until(node.resume.call_async(Trigger.Request()), 3.0)
        node.event('resume_response', success=response.success, reason=response.message)
        if not response.success:
            raise RuntimeError(response.message)
        node.observe(0.2)
        if not node.action.wait_for_server(timeout_sec=5.0):
            raise RuntimeError('instruction Action missing')
        goal = ExecuteInstruction.Goal()
        goal.raw_instruction = ('去仓库' if args.case == 'unknown' else
            '先去设备 A，然后返回基地' if args.case == 'unreachable' else '去设备 A')
        node.event('raw_instruction_submitted', instruction=goal.raw_instruction)
        handle = node.spin_until(node.action.send_goal_async(goal,
            feedback_callback=node.on_feedback), 5.0)
        if not handle.accepted:
            raise RuntimeError('raw Action not accepted')
        result = node.spin_until(handle.get_result_async(), 60.0)
        parsed = json.loads(result.result.result_json)
        node.event('instruction_terminal', action_status=result.status, result=parsed)
        node.observe(3.0)  # SlowPlanner returns after 36 s; observe its late valid result.
        new_ids = sorted(node.nav_ids-baseline)
        node.evidence['new_nav2_goal_uuids'] = new_ids
        expected_mode = 'injected-test-double' if args.case == 'model-timeout' else 'stub-test-double'
        if parsed['planner_mode'] != expected_mode:
            raise RuntimeError('wrong explicitly required planner test profile')
        if args.case == 'unknown':
            passed = (result.result.outcome == 'rejected' and not new_ids and
                      node.state is not None and not node.state.motion_permitted)
        elif args.case == 'model-timeout':
            passed = (result.result.outcome == 'aborted' and not new_ids and
                node.state is not None and node.state.locked and not node.state.motion_permitted and
                result.result.message == 'service_or_action_response_timeout')
        else:
            steps = parsed['steps']
            passed = (result.result.outcome == 'completed_with_failures' and len(new_ids) == 3 and
                len(steps) == 2 and steps[0]['place_id'] == 'station_a' and
                steps[0]['outcome'] == 'failed' and steps[0]['attempts'] == 2 and
                steps[0]['error_code'] in (206, 208) and steps[1]['place_id'] == 'base' and
                steps[1]['outcome'] == 'arrived' and steps[1]['nav2_status'] == 4)
        if args.case != 'unreachable':
            positions = [sample['odom_pose'][:2] for sample in node.evidence['samples']]
            if not positions:
                raise RuntimeError('no actual odom observations')
            displacement = max(((x-positions[0][0])**2+(y-positions[0][1])**2)**0.5 for x,y in positions)
            node.evidence['observed_odom_displacement_max_m'] = displacement
            passed = passed and displacement < 0.02
    except Exception as exc:
        node.event('failure', error=str(exc))
    finally:
        if node.stop.service_is_ready():
            try:
                node.spin_until(node.stop.call_async(Trigger.Request()), 3.0)
            except Exception:
                pass
        node.evidence.update(case=args.case, pass_=passed, real_ai_verified=False,
            planner_is_test_double=True, final_map_pose=node.final_pose(), collision_statistics='not measured')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(node.evidence, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({'run_id':args.run_id,'pass':passed,'real_ai_verified':False}))
        node.destroy_node()
        rclpy.shutdown()
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
