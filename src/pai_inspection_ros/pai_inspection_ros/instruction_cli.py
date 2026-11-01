"""Terminal client. Raw instruction travels through ExecuteInstruction action."""
import argparse
import json
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
from pai_inspection_interfaces.action import ExecuteInstruction


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['send', 'stop', 'resume'])
    parser.add_argument('instruction', nargs='?', default='')
    ns = parser.parse_args(args)
    rclpy.init()
    node = Node('instruction_cli')
    try:
        if ns.operation in ('stop', 'resume'):
            client = node.create_client(Trigger, '/pai/' + ns.operation)
            if not client.wait_for_service(timeout_sec=5.0):
                raise RuntimeError('independent safety service unavailable')
            future = client.call_async(Trigger.Request())
            rclpy.spin_until_future_complete(node, future, timeout_sec=5.0)
            response = future.result()
            if response is None:
                raise RuntimeError('safety service response timeout')
            print(json.dumps({'success': response.success, 'message': response.message}))
        else:
            if not ns.instruction.strip():
                parser.error('send requires an instruction')
            client = ActionClient(node, ExecuteInstruction, '/pai/execute_instruction')
            if not client.wait_for_server(timeout_sec=5.0):
                raise RuntimeError('instruction action unavailable')
            goal = ExecuteInstruction.Goal()
            goal.raw_instruction = ns.instruction
            future = client.send_goal_async(goal, feedback_callback=lambda msg: print(
                json.dumps({'stage': msg.feedback.stage, 'place_id': msg.feedback.place_id,
                            'message': msg.feedback.message}, ensure_ascii=False), flush=True))
            rclpy.spin_until_future_complete(node, future, timeout_sec=5.0)
            handle = future.result()
            if handle is None or not handle.accepted:
                raise RuntimeError('instruction goal rejected or response timeout')
            result_future = handle.get_result_async()
            rclpy.spin_until_future_complete(node, result_future)
            print(result_future.result().result.result_json)
    finally:
        node.destroy_node()
        rclpy.shutdown()
