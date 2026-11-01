"""Test observer timing only; no planner, ROS publisher or control state."""
import math
import time


def observed_origin(node, *, timeout=5., now=time.monotonic, spin=None):
    deadline = now()+timeout
    while node.sim_now is None or not math.isfinite(node.sim_now) or node.sim_now <= 0:
        if now() >= deadline:
            raise RuntimeError('observer_clock_not_received_before_Action')
        spin()
    return now(), node.sim_now


def elapsed_observation(started_wall, started_sim, wall_now, sim_now):
    duration = None
    reason = None
    if started_sim is None or sim_now is None:
        reason = 'sim_time_origin_or_terminal_not_observed'
    elif sim_now < started_sim:
        reason = 'observed_clock_reset'
    else:
        duration = sim_now-started_sim
    return {'task_wall_seconds': wall_now-started_wall,
            'task_observed_sim_seconds': duration,
            'task_observed_sim_duration_unavailable_reason': reason}
