import unittest
from types import SimpleNamespace
from observation_clock import observed_origin, elapsed_observation


class ObservationClockTests(unittest.TestCase):
    def test_clock_must_arrive_before_origin_can_be_used(self):
        clock, node = [1.], SimpleNamespace(sim_now=None)
        def spin():
            clock[0] += .1
            node.sim_now = 5.2
        self.assertEqual(observed_origin(node, now=lambda:clock[0], spin=spin), (1.1,5.2))

    def test_missing_clock_refuses_origin_without_any_task(self):
        clock, node = [0.], SimpleNamespace(sim_now=None)
        def spin():clock[0] += 1.
        with self.assertRaisesRegex(RuntimeError, 'before_Action'):
            observed_origin(node, timeout=2, now=lambda:clock[0], spin=spin)

    def test_missing_initial_time_or_reset_does_not_crash_terminal_collection(self):
        elapsed=elapsed_observation(10., None, 20., 7.)
        self.assertEqual(elapsed['task_wall_seconds'],10.)
        self.assertIsNone(elapsed['task_observed_sim_seconds'])
        self.assertIn('not_observed',elapsed['task_observed_sim_duration_unavailable_reason'])
        self.assertEqual(elapsed_observation(10., 5., 20., 7.)['task_observed_sim_seconds'],2.)
        self.assertEqual(elapsed_observation(10., 5., 20., 4.)['task_observed_sim_duration_unavailable_reason'],'observed_clock_reset')
