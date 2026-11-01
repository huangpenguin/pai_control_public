"""ROS API regression tests using injected doubles; no simulation or AI claims."""
import concurrent.futures
import hashlib
import importlib.util
import json
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

HAS_ROS = importlib.util.find_spec('rclpy') is not None
if HAS_ROS:
    from pai_inspection_ros.instruction_executor import InstructionExecutor, TaskInterrupted, NavigationFault


class Handle:
    is_active = True
    is_cancel_requested = False
    def __init__(self, raw='station_a'):
        self.request = SimpleNamespace(raw_instruction=raw)
        self.status = None
    def publish_feedback(self, _msg):
        pass
    def succeed(self):
        self.is_active, self.status = False, 'succeeded'
    def abort(self):
        self.is_active, self.status = False, 'aborted'
    def canceled(self):
        self.is_active, self.status = False, 'canceled'


class FakeNavHandle:
    accepted = True
    def __init__(self):
        self.cancel_count = 0
    def cancel_goal_async(self):
        self.cancel_count += 1
        future = concurrent.futures.Future()
        future.set_result(None)
        return future


@unittest.skipUnless(HAS_ROS, 'requires built ROS interfaces; run in the Jazzy container')
class CallbackTests(unittest.TestCase):
    def inspection_executor(self):
        obj = self.executor()
        obj.safety.motion_permitted = False
        obj.odom = SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=0, nanosec=0)),
            twist=SimpleNamespace(twist=SimpleNamespace(linear=SimpleNamespace(x=0.), angular=SimpleNamespace(z=0.))))
        obj.get_clock = lambda: SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=0))
        obj.set_permit = lambda *_args: None
        obj.feedback = lambda *_args, **_kwargs: None
        obj.tf_buffer = SimpleNamespace(lookup_transform=lambda *_args: SimpleNamespace(
            header=SimpleNamespace(stamp=SimpleNamespace(sec=0, nanosec=0)),
            transform=SimpleNamespace(translation=SimpleNamespace(x=3., y=-2.),
                rotation=SimpleNamespace(x=0., y=0., z=0., w=1.))))
        logs, clock = [], [0.]
        obj.audit = lambda kind, *_args, **fields: logs.append((kind, fields))
        check = obj.check_current
        def refresh(*args):
            obj.safety_wall = obj.odom_wall = clock[0]
            check(*args)
        obj.check_current = refresh
        return obj, logs, clock

    def test_optional_camera_missing_or_stale_is_unknown(self):
        for reason in ('no_image', 'stale'):
            obj, logs, clock = self.inspection_executor()
            obj.camera = SimpleNamespace(fresh_frame=lambda **_kwargs: (None, reason))
            with patch('pai_inspection_ros.instruction_executor.time.monotonic', lambda: clock[0]), \
                    patch('pai_inspection_ros.instruction_executor.time.sleep', lambda seconds: clock.__setitem__(0, clock[0]+seconds)):
                result = obj.inspect_indicator(Handle(), 5, 'station_a', 1)
            self.assertEqual((result['color'], result['reason']), ('unknown', reason))
            self.assertTrue(result['camera_wait_timed_out'])
            self.assertIsNone(result['png_path'])
            self.assertGreaterEqual(result['stop_verified_wall_s'], .5)
            self.assertEqual(len(logs), 1)

    def test_stop_during_camera_wait_discards_inspection(self):
        obj, logs, clock = self.inspection_executor()
        def late_frame(**_kwargs):
            obj.safety.locked = True
            obj.safety.reason = 'manual_stop'
            return None, 'no_image'
        obj.camera = SimpleNamespace(fresh_frame=late_frame)
        with patch('pai_inspection_ros.instruction_executor.time.monotonic', lambda: clock[0]), \
                patch('pai_inspection_ros.instruction_executor.time.sleep', lambda seconds: clock.__setitem__(0, clock[0]+seconds)):
            with self.assertRaises(TaskInterrupted):
                obj.inspect_indicator(Handle(), 5, 'station_a', 1)
        self.assertEqual(logs, [])

    def test_stop_during_image_processing_or_write_never_appends_or_returns(self):
        import numpy as np
        from pai_inspection_core.cli import StubPlanner
        raw = '检查设备 A，然后回基地'
        for stop_stage in ('conversion', 'write'):
            obj, logs, clock = self.inspection_executor()
            handle = Handle(raw)
            obj.planner_mode = 'stub-test-double'
            obj.places = {'station_a': {}, 'base': {}}
            plan = StubPlanner().parse(raw)
            future = concurrent.futures.Future()
            future.set_result(plan)
            obj.worker = SimpleNamespace(submit=lambda *_args: future)
            obj.get_parameter = lambda name: SimpleNamespace(value=8 if name=='max_steps' else '/unused')
            obj.request_stop = lambda: None
            visited = []
            def navigate(_handle, _generation, place):
                visited.append(place)
                return {'place_id': place, 'outcome': 'arrived', 'nav2_status': 4}
            obj.navigate = navigate
            image = SimpleNamespace(header=SimpleNamespace(frame_id='camera_rgb_frame',
                stamp=SimpleNamespace(sec=0, nanosec=0)), encoding='rgb8')
            rgb = np.full((480,640,3), 35, dtype=np.uint8)
            rgb[160:280,200:440] = (0,220,0)
            def convert(_image):
                if stop_stage=='conversion':
                    obj.safety.locked, obj.safety.reason = True, 'manual_stop'
                return rgb
            def save(*_args):
                obj.safety.generation += 1
                return {'png_path':'/obsolete-artifact.png','png_sha256':'obsolete'}
            obj.camera = SimpleNamespace(fresh_frame=lambda **_args: ((image,clock[0]),None),
                to_rgb=convert, save_png=save)
            with patch('pai_inspection_ros.instruction_executor.time.monotonic', lambda: clock[0]), \
                    patch('pai_inspection_ros.instruction_executor.time.sleep', lambda seconds: clock.__setitem__(0, clock[0]+seconds)):
                response = obj.execute(handle)
            self.assertEqual(response.outcome, 'stopped', response.message)
            self.assertEqual(visited, ['station_a'])
            self.assertEqual(len(json.loads(response.result_json)['steps']), 1)
            self.assertFalse(any(kind=='inspection_completed' for kind,_fields in logs))

    def test_camera_cache_requires_new_wall_and_sim_frame(self):
        from pai_inspection_ros.camera_capture import CameraCapture
        clock = [10.]
        cache = CameraCapture(now=lambda: clock[0])
        args = {'after_wall': 10., 'after_sim': 4., 'sim_now': 4.}
        self.assertEqual(cache.fresh_frame(**args), (None, 'no_image'))
        message = SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=4, nanosec=0)))
        cache.receive(message)
        self.assertEqual(cache.fresh_frame(**args), (None, 'stale'))
        clock[0] = 10.1
        cache.receive(message)
        self.assertIs(cache.fresh_frame(**args)[0][0], message)
        clock[0] = 10.7
        self.assertEqual(cache.fresh_frame(**args), (None, 'stale'))
        clock[0] = 10.8
        message.header.stamp.sec = 3
        cache.receive(message)
        self.assertEqual(cache.fresh_frame(**args), (None, 'stale'))

    def test_unknown_inspection_continues_return_but_navigation_fault_aborts(self):
        from pai_inspection_core.cli import StubPlanner
        for navigation_fault in (False, True):
            obj, handle = self.executor(), Handle('检查设备 A，然后回基地')
            obj.planner_mode = 'stub-test-double'
            obj.worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            obj.parse = StubPlanner().parse
            obj.get_parameter = lambda _name: SimpleNamespace(value=8)
            obj.places = {'station_a': {}, 'base': {}}
            obj.set_permit = lambda *_args: None
            stops, visited = [], []
            obj.request_stop = lambda: stops.append(True)
            def inspect(*_args):
                if navigation_fault:
                    raise NavigationFault('inspection_odom_stale')
                return {'place_id': 'station_a', 'outcome': 'inspected', 'color': 'unknown', 'reason': 'no_image'}
            obj.inspect_indicator = inspect
            def navigate(_handle, _generation, place):
                visited.append(place)
                return {'place_id': place, 'outcome': 'arrived', 'nav2_status': 4}
            obj.navigate = navigate
            response = obj.execute(handle)
            obj.worker.shutdown()
            self.assertEqual(visited, ['station_a'] if navigation_fault else ['station_a', 'base'])
            self.assertEqual(response.outcome, 'aborted' if navigation_fault else 'completed')
            self.assertEqual(bool(stops), navigation_fault)

    def test_online_path_sampling_keeps_first_attempt_and_ignores_stamp_only_changes(self):
        from live_online_acceptance import OnlineRun
        obj = OnlineRun.__new__(OnlineRun)
        obj.goal_sequence, obj.active_place = 1, 'station_a'
        obj.global_path_checks, obj.global_path_hashes = {}, {}
        obj.global_paths, obj.local_paths = [], []
        obj.last_local_path_wall, obj.sim_now = None, 10.0
        obj.evidence = {'action_uuid': 'current-action'}
        obj.final_pose = lambda: [0, 0, 0]
        point = lambda x: SimpleNamespace(pose=SimpleNamespace(
            position=SimpleNamespace(x=x, y=0, z=0),
            orientation=SimpleNamespace(x=0, y=0, z=0, w=1)))
        message = SimpleNamespace(header=SimpleNamespace(frame_id='map',
            stamp=SimpleNamespace(sec=10, nanosec=0)), poses=[point(0),point(1)])
        with patch('live_online_acceptance.time.monotonic', return_value=10.0):
            obj.on_path('planner', message)
        message.header.stamp.sec = 11
        with patch('live_online_acceptance.time.monotonic', return_value=11.1):
            obj.on_path('planner', message)
        self.assertEqual(len(obj.global_paths), 1)
        message.poses[-1] = point(2)
        with patch('live_online_acceptance.time.monotonic', return_value=11.2):
            obj.on_path('planner', message)
        self.assertEqual(len(obj.global_paths), 1)
        with patch('live_online_acceptance.time.monotonic', return_value=12.2):
            obj.on_path('planner', message)
        self.assertEqual(len(obj.global_paths), 2)
        obj.goal_sequence = 2
        with patch('live_online_acceptance.time.monotonic', return_value=12.3):
            obj.on_path('planner', message)
        self.assertEqual(len(obj.global_paths), 3)
        self.assertEqual(obj.global_paths[-1]['observed_goal_sequence'], 2)

    def executor(self):
        obj = InstructionExecutor.__new__(InstructionExecutor)
        obj.mutex = threading.RLock()
        obj.safety = SimpleNamespace(locked=False, generation=5, reason='ready')
        obj.safety_wall = time.monotonic()
        obj.nav_goal = None
        obj.nav_attempt = 7
        obj.pending_nav_future = None
        obj.pending_result_future = None
        obj.active = True
        obj.navigation_restart_required = False
        obj.get_logger = lambda: SimpleNamespace(warning=lambda _msg: None, info=lambda _msg: None)
        return obj

    def test_model_audits_keep_call_uuid_when_old_callback_finishes_last(self):
        from pai_inspection_core.cli import StubPlanner
        obj = self.executor()
        obj.planner_mode = 'vertex'
        logs, callbacks = [], {}
        obj.get_logger = lambda: SimpleNamespace(info=lambda message: logs.append(
            json.loads(message.removeprefix('PAI_AUDIT '))))
        raw_old, raw_new = '去设备 A', '去设备 B'
        def parse(raw, *, audit_callback):
            callbacks[raw] = audit_callback
            return StubPlanner().parse(raw)
        obj.parse = parse
        old = obj.parse_observed(raw_old, 'old-uuid', 5)
        new = obj.parse_observed(raw_new, 'new-uuid', 6)
        for raw, request in ((raw_new, 'new-request'), (raw_old, 'old-request')):
            callbacks[raw](json.dumps({'instruction': raw,
                'instruction_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                'request_id': request, 'validated_plan': (new if raw == raw_new else old).as_dict()}))
        completed = [log for log in logs if log['kind'] == 'planning_completed']
        self.assertEqual([log['action_uuid'] for log in completed], ['new-uuid', 'old-uuid'])
        self.assertEqual([log['planning']['request_id'] for log in completed], ['new-request', 'old-request'])
        self.assertEqual(old.steps[0].place_id, 'station_a')
        self.assertEqual(new.steps[0].place_id, 'station_b')
        self.assertEqual(obj.nav_attempt, 7)
        self.assertEqual(obj.safety.generation, 5)
        self.assertIsNone(obj.nav_goal)

    def test_wrong_call_audit_binding_rejects_without_navigation(self):
        obj = self.executor()
        obj.planner_mode = 'vertex'
        obj.get_logger = lambda: SimpleNamespace(info=lambda _message: None)
        def parse(_raw, *, audit_callback):
            audit_callback(json.dumps({'instruction': 'other', 'instruction_sha256': 'wrong'}))
        obj.parse = parse
        with self.assertRaisesRegex(ValueError, 'audit instruction binding mismatch'):
            obj.parse_observed('去设备 A', 'current-uuid', 5)
        self.assertIsNone(obj.nav_goal)

    def test_late_goal_after_manual_stop_cancels_instead_of_registering(self):
        obj, handle, nav = self.executor(), Handle(), FakeNavHandle()
        future = concurrent.futures.Future()
        future.set_result(nav)
        obj.on_safety(SimpleNamespace(locked=True, generation=6, reason='manual_stop'))
        obj.track_late_goal(future, handle, 5, 7)
        self.assertEqual(nav.cancel_count, 1)
        self.assertIsNone(obj.nav_goal)

    def test_obsolete_attempt_cancels_even_when_same_task_is_active(self):
        obj, handle, nav = self.executor(), Handle(), FakeNavHandle()
        future = concurrent.futures.Future()
        future.set_result(nav)
        obj.cancel_current_goal()  # invalidates an attempt whose response is pending
        obj.track_late_goal(future, handle, 5, 7)
        self.assertEqual(nav.cancel_count, 1)
        self.assertIsNone(obj.nav_goal)

    def test_registration_and_stop_are_serialized(self):
        obj, handle, nav = self.executor(), Handle(), FakeNavHandle()
        future = concurrent.futures.Future()
        future.set_result(nav)
        obj.track_late_goal(future, handle, 5, 7)
        self.assertIs(obj.nav_goal, nav)
        obj.on_safety(SimpleNamespace(locked=True, generation=6, reason='manual_stop'))
        self.assertEqual(nav.cancel_count, 1)

    def test_late_model_result_never_crosses_stop_generation(self):
        obj, handle = self.executor(), Handle()
        future = concurrent.futures.Future()
        result = []
        def wait():
            try:
                obj.wait_future(future, handle, 5, timeout=1)
                result.append('incorrectly_accepted')
            except TaskInterrupted:
                result.append('stopped')
        worker = threading.Thread(target=wait)
        worker.start()
        obj.on_safety(SimpleNamespace(locked=True, generation=6, reason='manual_stop'))
        future.set_result('late_model_plan')
        worker.join(timeout=2)
        self.assertEqual(result, ['stopped'])

    def test_old_goal_cannot_replace_a_new_task_goal(self):
        obj, handle, old_nav = self.executor(), Handle(), FakeNavHandle()
        future = concurrent.futures.Future()
        future.set_result(old_nav)
        obj.on_safety(SimpleNamespace(locked=False, generation=6, reason='ready'))
        new_nav = FakeNavHandle()
        obj.nav_goal = new_nav
        obj.track_late_goal(future, handle, 5, 7)
        self.assertEqual(old_nav.cancel_count, 1)
        self.assertIs(obj.nav_goal, new_nav)
        self.assertEqual(new_nav.cancel_count, 0)

    def test_timeout_zeros_before_cancel_and_unconfirmed_terminal_locks(self):
        obj, handle, nav = self.executor(), Handle(), FakeNavHandle()
        events = []
        pending = concurrent.futures.Future()
        nav.get_result_async = lambda: pending
        nav.cancel_goal_async = lambda: events.append('cancel')
        accepted = concurrent.futures.Future()
        accepted.set_result(nav)
        obj.nav = SimpleNamespace(server_is_ready=lambda: True,
                                  send_goal_async=lambda _goal: accepted)
        obj.places = {'station_a': {'x': 3, 'y': -2, 'yaw': 0}}
        obj.pose_goal = lambda _target: None
        obj.feedback = lambda *_args: None
        obj.check_current = lambda *_args: None  # guard remains live for this isolated test
        obj.set_permit = lambda enabled, *_args: events.append('permit_on' if enabled else 'permit_off')
        obj.request_navigation_fault = lambda: events.append('stop_locked')
        calls = []
        def now():
            calls.append(1)
            return SimpleNamespace(nanoseconds=0 if len(calls) < 3 else 120_000_000_000)
        obj.get_clock = lambda: SimpleNamespace(now=now)
        with self.assertRaisesRegex(NavigationFault, 'cancel_terminal_not_confirmed'):
            obj.navigate(handle, 5, 'station_a')
        self.assertLess(events.index('permit_off'), events.index('cancel'))
        self.assertEqual(events[-1], 'stop_locked')

    def test_cancel_result_exception_cannot_count_as_terminal(self):
        obj, handle, nav = self.executor(), Handle(), FakeNavHandle()
        events = []
        pending = concurrent.futures.Future()
        nav.get_result_async = lambda: pending
        def cancel():
            events.append('cancel')
            pending.set_exception(RuntimeError('result transport failed'))
        nav.cancel_goal_async = cancel
        accepted = concurrent.futures.Future()
        accepted.set_result(nav)
        obj.nav = SimpleNamespace(server_is_ready=lambda: True,
                                  send_goal_async=lambda _goal: accepted)
        obj.places = {'station_a': {'x': 3, 'y': -2, 'yaw': 0}}
        obj.pose_goal = lambda _target: None
        obj.feedback = lambda *_args: None
        obj.check_current = lambda *_args: None
        obj.set_permit = lambda enabled, *_args: events.append('permit_on' if enabled else 'permit_off')
        obj.request_navigation_fault = lambda: events.append('stop_locked')
        calls = []
        def now():
            calls.append(1)
            return SimpleNamespace(nanoseconds=0 if len(calls) < 3 else 120_000_000_000)
        obj.get_clock = lambda: SimpleNamespace(now=now)
        with self.assertRaisesRegex(NavigationFault, 'cancel_terminal_not_confirmed'):
            obj.navigate(handle, 5, 'station_a')
        self.assertLess(events.index('permit_off'), events.index('cancel'))
        self.assertEqual(events[-1], 'stop_locked')

    def test_explicit_unreachable_terminal_has_only_one_retry(self):
        from action_msgs.msg import GoalStatus
        obj, handle = self.executor(), Handle()
        goals = []
        def send(_goal):
            nav = FakeNavHandle()
            goals.append(nav)
            result = concurrent.futures.Future()
            result.set_result(SimpleNamespace(status=GoalStatus.STATUS_ABORTED,
                              result=SimpleNamespace(error_code=208, error_msg='no path')))
            nav.get_result_async = lambda: result
            future = concurrent.futures.Future()
            future.set_result(nav)
            return future
        obj.nav = SimpleNamespace(server_is_ready=lambda: True, send_goal_async=send)
        obj.places = {'station_a': {'x': 3, 'y': -2, 'yaw': 0}}
        obj.pose_goal = lambda _target: None
        obj.feedback = lambda *_args: None
        obj.set_permit = lambda *_args: None
        obj.get_clock = lambda: SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=0))
        result = obj.navigate(handle, 5, 'station_a')
        self.assertEqual(result['outcome'], 'failed')
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(len(goals), 2)

    def test_unconfirmed_fault_rejects_new_action_after_ordinary_resume(self):
        from rclpy.action import GoalResponse
        obj = self.executor()
        obj.active = False
        obj.navigation_fault = SimpleNamespace(service_is_ready=lambda: True,
                                               call_async=lambda _request: None)
        obj.request_navigation_fault()
        obj.on_safety(SimpleNamespace(locked=False, generation=6, reason='ready'))
        request = SimpleNamespace(raw_instruction='去设备 A')
        self.assertEqual(obj.on_goal(request), GoalResponse.REJECT)

    def test_confirmed_cancellation_is_not_a_restart_fault(self):
        from action_msgs.msg import GoalStatus
        obj, nav = self.executor(), FakeNavHandle()
        sent = concurrent.futures.Future()
        sent.set_result(nav)
        terminal = concurrent.futures.Future()
        terminal.set_result(SimpleNamespace(status=GoalStatus.STATUS_CANCELED))
        obj.pending_nav_future = sent
        obj.pending_result_future = terminal
        obj.nav_goal = nav
        self.assertTrue(obj.confirm_navigation_termination())
        self.assertFalse(obj.navigation_restart_required)
        self.assertIsNone(obj.pending_nav_future)

    def test_stop_before_goal_handle_cannot_resume_without_terminal(self):
        obj = self.executor()
        obj.pending_nav_future = concurrent.futures.Future()
        self.assertFalse(obj.confirm_navigation_termination())
        obj.navigation_fault = SimpleNamespace(service_is_ready=lambda: True,
                                               call_async=lambda _request: None)
        obj.request_navigation_fault()
        self.assertTrue(obj.navigation_restart_required)

    def test_unknown_place_rejects_entire_action_before_any_nav_goal(self):
        from pai_inspection_core.cli import StubPlanner
        obj, handle = self.executor(), Handle('去仓库')
        obj.planner_mode = 'stub-test-double'
        obj.worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        obj.parse = StubPlanner().parse
        obj.get_parameter = lambda _name: SimpleNamespace(value=1)
        obj.set_permit = lambda *_args: None
        obj.request_stop = lambda: None
        obj.navigate = lambda *_args: self.fail('unknown place sent a Nav2 goal')
        response = obj.execute(handle)
        obj.worker.shutdown()
        self.assertEqual(response.outcome, 'rejected')
        self.assertEqual(handle.status, 'succeeded')
        self.assertIn('stub-test-double', response.result_json)

    def test_failed_station_only_recommends_return_when_base_not_arrived(self):
        from pai_inspection_core.cli import StubPlanner
        for base_state in ('arrived', 'not_requested', 'failed'):
            returned = base_state == 'arrived'
            with self.subTest(base_state=base_state):
                raw = '去设备 A' if base_state == 'not_requested' else '先去设备 A，然后返回基地'
                obj, handle = self.executor(), Handle(raw)
                obj.planner_mode = 'stub-test-double'
                obj.worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                obj.parse = StubPlanner().parse
                obj.get_parameter = lambda _name: SimpleNamespace(value=8)
                obj.places = {'station_a': {}, 'base': {}}
                obj.set_permit = lambda *_args: None
                obj.request_stop = lambda: None
                def navigate(_handle, _generation, place):
                    if place == 'station_a':
                        return {'place_id': place, 'outcome': 'failed', 'attempts': 2, 'error_code': 208}
                    return {'place_id': 'base', 'outcome': base_state, 'nav2_status': 4 if returned else 6}
                obj.navigate = navigate
                response = obj.execute(handle)
                obj.worker.shutdown()
                payload = json.loads(response.result_json)
                self.assertEqual(response.outcome, 'aborted' if base_state == 'failed' else 'completed_with_failures')
                self.assertEqual(payload['return_recommended'], not returned)
                self.assertEqual(payload['return_requested'], base_state != 'not_requested')
                if returned:
                    self.assertIn('return to base completed', response.message)
                self.assertEqual(handle.status, 'aborted' if base_state == 'failed' else 'succeeded')


if __name__ == '__main__':
    unittest.main()
