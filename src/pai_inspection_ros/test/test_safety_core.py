import math
from pathlib import Path
import unittest
import yaml
from pai_inspection_ros.safety_core import SafetyPolicy
from pai_inspection_ros.execution_core import ArrivalCheck, GoalBudget


class FakeTime:
    wall = 10.0
    def __call__(self):
        return self.wall


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.time = FakeTime()
        self.guard = SafetyPolicy(now=self.time)
        self.healthy()

    def healthy(self, sim=10.0):
        self.guard.observe_clock(sim)
        for topic in ('scan', 'odom', 'tf'):
            self.guard.observe(topic, sim)

    def arm(self):
        self.assertTrue(self.guard.resume()[0])
        gen = self.guard.generation
        self.assertTrue(self.guard.set_permit(gen, True)[0])
        self.guard.observe_command((0.15, 0, 0, 0, 0, 0.5), 10.0)
        return gen

    def test_default_locked_and_resume_does_not_move(self):
        self.assertEqual(self.guard.output(), (0, 0))
        self.assertFalse(self.guard.set_permit(self.guard.generation, True)[0])
        self.assertTrue(self.guard.resume()[0])
        self.assertEqual(self.guard.output(), (0, 0))

    def test_stop_invalidates_old_task_even_after_resume(self):
        generation = self.arm()
        self.assertEqual(self.guard.output(), (0.15, 0.5))
        self.guard.stop()
        self.guard.observe_command((0.15, 0, 0, 0, 0, 0.5), 10.0)
        self.assertEqual(self.guard.output(), (0, 0))
        self.assertFalse(self.guard.set_permit(generation, True)[0])
        self.assertTrue(self.guard.resume()[0])
        self.assertFalse(self.guard.set_permit(generation, True)[0])
        self.assertEqual(self.guard.output(), (0, 0))

    def test_limits_and_rejects_nonplanar_nonfinite(self):
        self.arm()
        self.guard.observe_command((9, 0, 0, 0, 0, -9), 10.0)
        self.assertEqual(self.guard.output(), (0.15, -0.5))
        self.guard.observe_command((0.1, 0.1, 0, 0, 0, 0), 10.0)
        self.assertTrue(self.guard.locked)
        self.assertEqual(self.guard.output(), (0, 0))
        self.assertTrue(self.guard.resume()[0])
        self.guard.set_permit(self.guard.generation, True)
        self.guard.observe_command((math.nan, 0, 0, 0, 0, 0), 10.0)
        self.assertEqual(self.guard.output(), (0, 0))
        self.assertTrue(self.guard.locked)

    def test_sensor_loss_latches_and_does_not_recover_automatically(self):
        self.arm()
        self.time.wall += 0.6
        self.guard.observe_clock(10.6)
        self.guard.observe('scan', 10.6)
        self.guard.observe('tf', 10.6)
        self.assertEqual(self.guard.output(), (0, 0))
        self.assertEqual(self.guard.reason, 'odom_stale')
        self.healthy(10.6)
        self.assertTrue(self.guard.locked)

    def test_paused_clock_latches_even_when_messages_keep_arriving(self):
        self.arm()
        self.time.wall += 0.6
        self.guard.observe_clock(10.0)
        for topic in ('scan', 'odom', 'tf'):
            self.guard.observe(topic, 10.0)
        self.assertEqual(self.guard.output(), (0, 0))
        self.assertEqual(self.guard.reason, 'clock_stale_or_paused')

    def test_stale_nav_input_latches(self):
        self.arm()
        self.time.wall += 0.31
        self.healthy(10.31)
        self.guard.set_permit(self.guard.generation, True)
        self.assertEqual(self.guard.output(), (0, 0))
        self.assertEqual(self.guard.reason, 'velocity_stale')

    def test_lock_diagnostic_preserves_expired_receipt_before_zeroing(self):
        self.arm()
        self.time.wall += .31
        self.healthy(10.31)
        self.assertEqual(self.guard.output(), (0, 0))
        diagnostic = self.guard.last_lock_diagnostic
        self.assertEqual(diagnostic['reason'], 'velocity_stale')
        self.assertAlmostEqual(diagnostic['command_age_wall_s'], .31)
        self.assertEqual(diagnostic['command_receipt_wall_seconds'], 10.0)
        self.assertEqual(diagnostic['last_command'], (.15, .5))
        self.assertEqual(diagnostic['clock_progress_age_wall_s'], 0)
        self.assertEqual(diagnostic['sensor_ages']['scan']['age_wall_s'], 0)
        self.assertIsNone(self.guard.command)
        self.assertIsNone(self.guard.command_wall)
        self.guard.stop('velocity_stale')
        self.assertIs(self.guard.last_lock_diagnostic, diagnostic)
        self.assertEqual(self.guard.output(), (0, 0))

    def test_renewal_does_not_refresh_old_velocity(self):
        gen = self.arm()
        self.time.wall += 0.31
        self.healthy(10.31)
        self.assertTrue(self.guard.set_permit(gen, True)[0])
        self.assertEqual(self.guard.output(), (0, 0))

    def test_executor_lease_expires_despite_live_nav(self):
        self.arm()
        self.time.wall += 0.8
        self.healthy(10.8)
        self.guard.observe_command((0.15, 0, 0, 0, 0, 0), 10.8)
        self.assertEqual(self.guard.output(), (0, 0))
        self.assertEqual(self.guard.reason, 'executor_permit_expired')

    def test_clock_reset_requires_restart_even_after_manual_stop(self):
        self.arm()
        self.guard.observe_clock(0.0)
        self.guard.stop()
        self.time.wall += 0.1
        self.healthy(11.0)
        self.assertFalse(self.guard.resume()[0])
        self.assertEqual(self.guard.resume()[1], 'clock_reset_restart_required')

    def test_unconfirmed_navigation_fault_cannot_be_manually_resumed(self):
        old_generation = self.arm()
        self.guard.lock_navigation_fault()
        self.assertEqual(self.guard.output(), (0, 0))
        self.guard.stop()  # Manual stop cannot erase the navigation fault.
        self.assertFalse(self.guard.resume()[0])
        self.assertEqual(self.guard.resume()[1], 'navigation_cancel_unconfirmed_restart_chain_required')
        self.assertFalse(self.guard.set_permit(old_generation, True)[0])
        self.assertFalse(self.guard.set_permit(self.guard.generation, True)[0])

    def test_sim_stamp_staleness_and_missing_source_reject_resume(self):
        self.guard.observe('scan', 1.0)
        self.assertEqual(self.guard.resume(), (False, 'scan_stale'))
        self.guard.samples.pop('scan')
        self.assertEqual(self.guard.resume(), (False, 'scan_missing'))


class ArrivalAndBudgetTests(unittest.TestCase):
    def test_rotation_shim_shares_dwb_limits_without_relaxing_acceptance(self):
        config = yaml.safe_load((Path(__file__).parents[1]/'config'/'nav2.yaml').read_text())
        server = config['controller_server']['ros__parameters']
        controller = server['FollowPath']
        self.assertEqual(controller['plugin'], 'nav2_rotation_shim_controller::RotationShimController')
        self.assertEqual(controller['primary_controller'], 'dwb_core::DWBLocalPlanner')
        self.assertLessEqual(controller['rotate_to_heading_angular_vel'], controller['max_vel_theta'])
        self.assertLessEqual(controller['max_vel_theta'], .5)
        self.assertLessEqual(controller['max_vel_x'], .15)
        self.assertEqual(server['progress_checker']['movement_time_allowance'], 20.0)
        self.assertEqual(server['progress_checker']['required_movement_radius'], .1)
        self.assertEqual(server['goal_checker']['xy_goal_tolerance'], .20)
        self.assertEqual(server['goal_checker']['yaw_goal_tolerance'], .25)
        self.assertFalse(controller['rotate_to_goal_heading'])
        self.assertFalse(controller['use_path_orientations'])

    def test_succeeded_requires_pose_and_continuous_standstill(self):
        clock = FakeTime()
        check = ArrivalCheck(now=clock)
        target = (3.0, -2.0, 0.0)
        self.assertFalse(check.update(target, target, 0, 0, succeeded=False))
        self.assertFalse(check.update(target, target, 0.03, 0, succeeded=True))
        self.assertFalse(check.update(target, target, 0, 0, succeeded=True))
        clock.wall += 0.3
        self.assertFalse(check.update(target, target, 0.03, 0, succeeded=True))
        self.assertFalse(check.update(target, target, 0, 0, succeeded=True))
        clock.wall += 0.51
        self.assertTrue(check.update(target, target, 0, 0, succeeded=True))
        self.assertFalse(check.update((3.21, -2.0, 0), target, 0, 0, succeeded=True))

    def test_stale_odom_and_yaw_outside_tolerance_do_not_arrive(self):
        clock = FakeTime()
        check = ArrivalCheck(now=clock)
        target = (0, 0, math.pi - 0.05)
        self.assertFalse(check.update((0, 0, -math.pi + 0.05), target, 0, 0, succeeded=True))
        clock.wall += 0.51
        self.assertTrue(check.update((0, 0, -math.pi + 0.05), target, 0, 0, succeeded=True))
        self.assertFalse(check.update(target, target, 0, 0, fresh=False, succeeded=True))
        self.assertFalse(check.update((0, 0, 0), target, 0, 0, succeeded=True))

    def test_one_retry_shares_original_sim_and_wall_budget(self):
        budget = GoalBudget(20, 100)
        self.assertTrue(budget.start_attempt(20, 100))
        self.assertTrue(budget.start_attempt(130, 200))
        self.assertFalse(budget.start_attempt(131, 201))
        self.assertTrue(budget.expired(140, 200))
        self.assertTrue(budget.expired(30, 400))
        self.assertTrue(budget.expired(0, 110))
        self.assertEqual((budget.sim_started, budget.wall_started), (20, 100))


if __name__ == '__main__':
    unittest.main()
