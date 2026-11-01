#!/usr/bin/env python3
"""Two stages: saved real Vertex response, then real ROS/Gazebo execution.

This external acceptance harness never instantiates a Vertex client. Production
planner defaults and executor navigation/validation code remain unchanged.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from recorded_plan_provider import RecordedPlanProvider, EVIDENCE_SHA256, REQUEST_ID, INSTRUCTION, MODE


def ledger_snapshot(path):
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        rows = connection.execute(
            'SELECT id, charged_micro, status FROM requests ORDER BY id').fetchall()
    return {'attempts': len(rows), 'requests': rows}


def confirm_sim_clock(node):
    """Match product launch's sim clock; never use wall epoch for arrival age."""
    import rclpy
    from rosgraph_msgs.msg import Clock
    from rclpy.qos import qos_profile_sensor_data
    if node.get_parameter('use_sim_time').value is not True:
        raise RuntimeError('recorded executor must use simulation time')
    observed = []
    subscription = node.create_subscription(Clock, '/clock',
        lambda msg: observed.append(msg.clock.sec + msg.clock.nanosec * 1e-9),
        qos_profile_sensor_data)
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=.05)
            now = node.get_clock().now().nanoseconds / 1e9
            if observed and now > 0 and abs(now-observed[-1]) <= .05:
                node.get_logger().info('RECORDED preflight use_sim_time=true; node_clock='
                                       + str(now) + '; /clock=' + str(observed[-1]))
                return
        raise RuntimeError('ROS node clock does not match /clock')
    finally:
        node.destroy_subscription(subscription)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve', 'run'])
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--execution-attempt', type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    provider = RecordedPlanProvider(args.evidence, args.receipt, args.execution_attempt)
    import rclpy
    if args.command == 'serve':
        from rclpy.executors import MultiThreadedExecutor
        from pai_inspection_ros.instruction_executor import InstructionExecutor
        rclpy.init(args=['--ros-args', '-p', 'use_sim_time:=true'])
        node = InstructionExecutor(planner=provider)
        # Honest provenance label only in this test process, never product code.
        node.planner_mode = MODE
        node.get_logger().info('RECORDED REAL RESPONSE ONLY; request=' + REQUEST_ID
                               + '; SHA256=' + EVIDENCE_SHA256 + '; no online generation')
        confirm_sim_clock(node)
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(node)
        try:
            executor.spin()
        finally:
            node.request_stop()
            node.worker.shutdown(wait=False, cancel_futures=True)
            executor.shutdown(timeout_sec=2.0)
            node.destroy_node()
            rclpy.shutdown()
        return

    if args.output is None or args.ledger is None or args.run_id is None:
        parser.error('run requires --output, --ledger and --run-id')
    if args.output.exists():
        raise SystemExit('refusing to overwrite an existing run; no automatic replay')
    from live_acceptance import Run
    from pai_inspection_interfaces.action import ExecuteInstruction
    from std_srvs.srv import Trigger
    rclpy.init()
    node = Run(args.run_id)
    node.evidence.update(source='real_vertex_output_recorded_then_real_gazebo_execution',
        execution_attempt=args.execution_attempt,
        online_inference_during_execution=False, request_id=REQUEST_ID,
        original_instruction=INSTRUCTION, evidence_sha256=EVIDENCE_SHA256,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        provider_sha256=hashlib.sha256(Path(__file__).with_name('recorded_plan_provider.py').read_bytes()).hexdigest(),
        collection_script_sha256=hashlib.sha256(Path(__file__).with_name('live_acceptance.py').read_bytes()).hexdigest(),
        approved_model_plan=provider.plan.as_dict(), action_requests_sent=0)
    passed = False
    try:
        before = ledger_snapshot(args.ledger)
        node.evidence['ledger_before'] = before
        if before['attempts'] != 2 or REQUEST_ID not in [row[0] for row in before['requests']]:
            raise RuntimeError('ledger does not match the two authorized requests')
        if not node.resume.wait_for_service(timeout_sec=10.0):
            raise RuntimeError('guard service missing')
        deadline = time.monotonic() + 3
        while node.state is None and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
        if node.state is None or not node.state.locked or node.state.motion_permitted:
            raise RuntimeError('expected locked guard before recorded task')
        response = node.spin_until(node.resume.call_async(Trigger.Request()), 3)
        node.event('resume_response', success=response.success, reason=response.message)
        if not response.success:
            raise RuntimeError(response.message)
        deadline = time.monotonic() + 3
        while node.state.locked and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
        if node.state.locked or not node.action.wait_for_server(timeout_sec=10):
            raise RuntimeError('recorded executor not ready')
        goal = ExecuteInstruction.Goal()
        goal.raw_instruction = INSTRUCTION
        node.evidence['action_requests_sent'] = 1
        node.event('raw_instruction_submitted', instruction=INSTRUCTION)
        started_wall, started_sim = time.monotonic(), node.sim_now
        handle = node.spin_until(node.action.send_goal_async(goal, feedback_callback=node.on_feedback), 5)
        if not handle.accepted:
            raise RuntimeError('recorded instruction action rejected')
        result = node.spin_until(handle.get_result_async(), 320)
        parsed = json.loads(result.result.result_json)
        node.event('instruction_terminal', action_status=result.status, result=parsed)
        node.evidence.update(task_wall_seconds=time.monotonic()-started_wall,
                             task_sim_seconds=node.sim_now-started_sim)
        post_start = time.monotonic()
        while time.monotonic()-post_start < 2:
            rclpy.spin_once(node, timeout_sec=0.05)
        post = [s for s in node.evidence['samples'] if s['wall_seconds'] >= post_start]
        settled = (len(post) >= 2 and post[-1]['wall_seconds']-post[0]['wall_seconds'] >= .5
                   and all(abs(s['linear_velocity']) <= .01 and abs(s['angular_velocity']) <= .02 for s in post))
        steps = parsed.get('steps', [])
        expected_places = [s.place_id for s in provider.plan.steps]
        passed = (result.status == 4 and parsed.get('planner_mode') == MODE
                  and result.result.outcome == 'completed'
                  and [s.get('place_id') for s in steps] == expected_places
                  and all(s.get('outcome') == 'arrived' and s.get('nav2_status') == 4 for s in steps)
                  and settled and node.moving_wall is not None)
        node.evidence['post_terminal_continuous_standstill'] = settled
        if args.receipt.exists():
            node.evidence['consumption_receipt'] = json.loads(args.receipt.read_text())
        else:
            raise RuntimeError('real response was not consumed')
    except Exception as exc:
        passed = False
        node.event('failure', error=str(exc))
    finally:
        try:
            if node.stop.service_is_ready():
                response = node.spin_until(node.stop.call_async(Trigger.Request()), 3)
                node.event('cleanup_stop_response', success=response.success, reason=response.message)
            after = ledger_snapshot(args.ledger)
            node.evidence['ledger_after'] = after
            node.evidence['no_new_generation'] = after == node.evidence.get('ledger_before')
            passed = passed and node.evidence['no_new_generation']
        except Exception as exc:
            passed = False
            node.event('cleanup_or_ledger_failure', error=str(exc))
        pose = node.final_pose()
        if isinstance(pose, list):
            position_error = math.hypot(pose[0]-3, pose[1]+2)
            yaw_error = abs(math.atan2(math.sin(pose[2]), math.cos(pose[2])))
            node.evidence.update(final_position_error_m=position_error, final_yaw_error_rad=yaw_error)
            passed = passed and position_error <= .20 and yaw_error <= .25
        else:
            passed = False
        node.evidence.update(pass_=passed, two_stage_real_model_execution_verified=passed,
                             final_map_pose=pose, collision_statistics='not measured')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(node.evidence, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({'pass': passed, 'request_id': REQUEST_ID, 'evidence': str(args.output)}))
        node.destroy_node()
        rclpy.shutdown()
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
