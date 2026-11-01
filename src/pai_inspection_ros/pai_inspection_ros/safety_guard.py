"""Independent process; the only ROS publisher connected to the drive bridge."""
import json
import math

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from rclpy.clock import ClockType
from geometry_msgs.msg import TwistStamped
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from std_srvs.srv import Trigger
from action_msgs.srv import CancelGoal
from tf2_ros import Buffer, TransformListener, TransformException
from pai_inspection_interfaces.msg import SafetyState
from pai_inspection_interfaces.srv import SetMotionPermit
from .safety_core import SafetyPolicy


def stamp_seconds(stamp):
    return stamp.sec + stamp.nanosec * 1e-9


class SafetyGuard(Node):
    def __init__(self):
        super().__init__('safety_guard')
        self.declare_parameter('base_frame', 'base_footprint')
        self.policy = SafetyPolicy()
        self.velocity_pub = self.create_publisher(TwistStamped, '/safe_cmd_vel', 1)
        state_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                               durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.state_pub = self.create_publisher(SafetyState, '/pai/safety_state', state_qos)
        self.create_subscription(TwistStamped, '/nav_cmd_vel', self.on_velocity, 1)
        self.create_subscription(Clock, '/clock', self.on_clock, qos_profile_sensor_data)
        self.create_subscription(LaserScan, '/scan', self.on_scan, qos_profile_sensor_data)
        self.create_subscription(Odometry, '/odom', self.on_odom, qos_profile_sensor_data)
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.cancel_client = self.create_client(CancelGoal, '/navigate_to_pose/_action/cancel_goal')
        self.create_service(Trigger, '/pai/stop', self.on_stop)
        self.create_service(Trigger, '/pai/resume', self.on_resume)
        self.create_service(Trigger, '/pai/lock_navigation_fault', self.on_navigation_fault)
        self.create_service(SetMotionPermit, '/pai/set_motion_permit', self.on_permit)
        self.timer = self.create_timer(0.05, self.tick, clock=rclpy.clock.Clock(clock_type=ClockType.STEADY_TIME))
        self.last_generation = self.policy.generation
        self.publish(0.0, 0.0)
        self.get_logger().info('Safety guard started LOCKED; independent /pai/stop is ready.')

    def on_clock(self, msg):
        self.policy.observe_clock(stamp_seconds(msg.clock))

    def on_scan(self, msg):
        if not msg.ranges or all(math.isnan(v) for v in msg.ranges):
            self.policy.stop('invalid_scan')
            return
        self.policy.observe('scan', stamp_seconds(msg.header.stamp))

    def on_odom(self, msg):
        pose, twist = msg.pose.pose, msg.twist.twist
        values = (pose.position.x, pose.position.y, pose.orientation.x,
                  pose.orientation.y, pose.orientation.z, pose.orientation.w,
                  twist.linear.x, twist.angular.z)
        if not all(math.isfinite(v) for v in values):
            self.policy.stop('invalid_odom')
            return
        self.policy.observe('odom', stamp_seconds(msg.header.stamp))

    def on_velocity(self, msg):
        t = msg.twist
        self.policy.observe_command((t.linear.x, t.linear.y, t.linear.z,
                                     t.angular.x, t.angular.y, t.angular.z),
                                    stamp_seconds(msg.header.stamp))

    def cancel_navigation(self):
        # Zero output has already been published. Cancellation never blocks it.
        if self.cancel_client.service_is_ready():
            self.cancel_client.call_async(CancelGoal.Request())  # zero UUID/stamp = all goals

    def publish(self, vx, wz):
        msg = TwistStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.get_parameter('base_frame').value
        msg.twist.linear.x, msg.twist.angular.z = vx, wz
        self.velocity_pub.publish(msg)
        state = self.policy.state()
        status = SafetyState()
        status.locked, status.motion_permitted = state.locked, state.motion_permitted
        status.generation, status.reason = state.generation, state.reason
        self.state_pub.publish(status)
        if state.generation != self.last_generation:
            self.last_generation = state.generation
            if state.locked:
                self.cancel_navigation()
                self.get_logger().warning('Safety locked: ' + state.reason)
                with self.policy.mutex:
                    diagnostic = self.policy.last_lock_diagnostic
                    if diagnostic is not None and diagnostic['generation'] == state.generation:
                        self.get_logger().info('PAI_GUARD_LOCK ' + json.dumps(
                            diagnostic, ensure_ascii=False, allow_nan=False))

    def tick(self):
        try:
            transform = self.tf_buffer.lookup_transform(
                'map', self.get_parameter('base_frame').value, Time())
            self.policy.observe('tf', stamp_seconds(transform.header.stamp))
        except TransformException:
            pass  # Missing or expired TF is checked by the policy.
        self.publish(*self.policy.output())

    def on_stop(self, _request, response):
        self.policy.stop('manual_stop')
        self.publish(0.0, 0.0)
        self.cancel_navigation()
        response.success, response.message = True, 'locked; navigation cancellation requested; pending task invalidated'
        return response

    def on_navigation_fault(self, _request, response):
        self.policy.lock_navigation_fault()
        self.publish(0.0, 0.0)
        self.cancel_navigation()
        response.success = True
        response.message = 'navigation cancel unconfirmed; restart whole simulation/Nav2/guard chain required'
        return response

    def on_resume(self, _request, response):
        response.success, response.message = self.policy.resume()
        self.publish(0.0, 0.0)
        return response

    def on_permit(self, request, response):
        response.accepted, response.reason = self.policy.set_permit(request.generation, request.enabled)
        if not request.enabled or not response.accepted:
            self.publish(0.0, 0.0)
        return response


def main(args=None):
    rclpy.init(args=args)
    node = SafetyGuard()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.policy.stop('guard_shutdown')
        node.publish(0.0, 0.0)
        node.destroy_node()
        rclpy.shutdown()
