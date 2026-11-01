#!/usr/bin/env python3
"""Explicit process-level planner test double; never a Vertex call."""
import time

import rclpy
from rclpy.executors import MultiThreadedExecutor

from pai_inspection_core.cli import StubPlanner
from pai_inspection_ros.instruction_executor import InstructionExecutor


class SlowPlanner:
    def parse(self, raw):
        print('INJECTED TEST DOUBLE begin wall=' + str(time.monotonic()), flush=True)
        time.sleep(36.0)  # Exceeds the unchanged production application wait of 35 s.
        result = StubPlanner().parse(raw)
        print('INJECTED TEST DOUBLE late valid result wall=' + str(time.monotonic()), flush=True)
        return result


def main():
    rclpy.init()
    node = InstructionExecutor(planner=SlowPlanner())
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
