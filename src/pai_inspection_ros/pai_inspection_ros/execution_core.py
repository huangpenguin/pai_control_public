"""Bounded navigation bookkeeping independent of ROS and model providers."""
from dataclasses import dataclass
import math
import time


@dataclass
class GoalBudget:
    sim_started: float
    wall_started: float
    sim_limit: float = 120.0
    wall_limit: float = 300.0
    attempts: int = 0

    def expired(self, sim_now, wall_now):
        return (sim_now < self.sim_started or
                sim_now - self.sim_started >= self.sim_limit or
                wall_now - self.wall_started >= self.wall_limit)

    def start_attempt(self, sim_now, wall_now):
        if self.attempts >= 2 or self.expired(sim_now, wall_now):
            return False
        self.attempts += 1
        return True


class ArrivalCheck:
    def __init__(self, position_tolerance=0.20, yaw_tolerance=0.25,
                 settle_time=0.5, now=time.monotonic):
        self.position_tolerance = position_tolerance
        self.yaw_tolerance = yaw_tolerance
        self.settle_time = settle_time
        self.now = now
        self.settled_since = None

    def update(self, actual, target, linear_velocity, angular_velocity,
               fresh=True, succeeded=False):
        valid = (fresh and succeeded and all(math.isfinite(v) for v in
                 (*actual, *target, linear_velocity, angular_velocity)))
        if valid:
            dx, dy = actual[0] - target[0], actual[1] - target[1]
            dyaw = math.atan2(math.sin(actual[2] - target[2]),
                              math.cos(actual[2] - target[2]))
            valid = (math.hypot(dx, dy) <= self.position_tolerance and
                     abs(dyaw) <= self.yaw_tolerance and
                     abs(linear_velocity) <= 0.01 and abs(angular_velocity) <= 0.02)
        if not valid:
            self.settled_since = None
            return False
        if self.settled_since is None:
            self.settled_since = self.now()
        return self.now() - self.settled_since >= self.settle_time
