"""ROS-independent safety policy. Times are steady wall times unless named sim."""
from dataclasses import dataclass
import math
import threading
import time


@dataclass(frozen=True)
class GuardState:
    locked: bool
    motion_permitted: bool
    generation: int
    reason: str


class SafetyPolicy:
    def __init__(self, now=time.monotonic, sensor_timeout=0.5,
                 command_timeout=0.3, permit_timeout=0.75):
        self.now = now
        self.sensor_timeout = sensor_timeout
        self.command_timeout = command_timeout
        self.permit_timeout = permit_timeout
        self.mutex = threading.RLock()
        self.locked = True
        self.permitted = False
        self.generation = 1
        self.reason = 'startup_locked'
        self.restart_required = False
        self.navigation_restart_required = False
        self.clock_sim = None
        self.clock_progress_wall = None
        self.samples = {}
        self.command = None
        self.command_wall = None
        self.permit_wall = None
        self.permit_started = None
        self.last_lock_diagnostic = None

    def state(self):
        with self.mutex:
            return GuardState(self.locked, self.permitted, self.generation, self.reason)

    def stop(self, reason='manual_stop'):
        with self.mutex:
            if not self.locked or self.reason != reason:
                self.generation += 1
                # Capture receipt ages before clearing the motion command. This
                # record is observational and never changes lease decisions.
                now = self.now()
                self.last_lock_diagnostic = {
                    'reason': reason, 'generation': self.generation,
                    'wall_seconds': now, 'sim_seconds': self.clock_sim,
                    'command_receipt_wall_seconds': self.command_wall,
                    'command_age_wall_s': None if self.command_wall is None else now-self.command_wall,
                    'last_command': self.command,
                    'clock_progress_age_wall_s': None if self.clock_progress_wall is None else now-self.clock_progress_wall,
                    'sensor_ages': {name: {
                        'age_wall_s': now-sample[0],
                        'age_sim_s': None if self.clock_sim is None else self.clock_sim-sample[1],
                        'stamp_sim_seconds': sample[1]}
                        for name, sample in self.samples.items()},
                }
            self.locked = True
            self.permitted = False
            self.command = None
            self.command_wall = None
            self.reason = reason

    def lock_navigation_fault(self):
        with self.mutex:
            self.navigation_restart_required = True
            self.stop('navigation_cancel_unconfirmed_restart_chain_required')

    def observe_clock(self, sim):
        with self.mutex:
            if not math.isfinite(sim) or sim < 0:
                self.stop('invalid_clock')
                return
            if self.clock_sim is not None and sim < self.clock_sim:
                self.restart_required = True
                self.stop('clock_reset_restart_required')
                # Leave the previous clock to prevent resume on a reset instance.
                return
            if self.clock_sim is None or sim > self.clock_sim:
                self.clock_progress_wall = self.now()
            self.clock_sim = sim

    def observe(self, name, sim_stamp):
        with self.mutex:
            if not math.isfinite(sim_stamp):
                self.stop('invalid_' + name)
                return
            self.samples[name] = (self.now(), sim_stamp)

    def health_reason(self):
        with self.mutex:
            now = self.now()
            if self.clock_sim is None or self.clock_progress_wall is None:
                return 'clock_missing'
            if now - self.clock_progress_wall > self.sensor_timeout:
                return 'clock_stale_or_paused'
            for name in ('scan', 'odom', 'tf'):
                sample = self.samples.get(name)
                if sample is None:
                    return name + '_missing'
                wall, stamp = sample
                if now - wall > self.sensor_timeout or self.clock_sim - stamp > self.sensor_timeout:
                    return name + '_stale'
                # AMCL publishes future transforms using its tolerance (0.5 s).
                if stamp - self.clock_sim > 0.6:
                    return name + '_future'
            return None

    def resume(self):
        with self.mutex:
            if self.restart_required:
                return False, 'clock_reset_restart_required'
            if self.navigation_restart_required:
                return False, 'navigation_cancel_unconfirmed_restart_chain_required'
            reason = self.health_reason()
            if reason:
                return False, reason
            self.generation += 1
            self.locked = False
            self.permitted = False
            self.command = None
            self.command_wall = None
            self.reason = 'ready'
            return True, 'ready_new_task_required'

    def set_permit(self, generation, enabled):
        with self.mutex:
            if generation != self.generation:
                return False, 'obsolete_generation'
            if not enabled:
                self.permitted = False
                self.command = None
                self.command_wall = None
                return True, 'motion_disabled'
            reason = self.health_reason()
            if self.locked or reason:
                if reason and not self.locked:
                    self.stop(reason)
                return False, reason or self.reason
            if not self.permitted:
                self.command = None
                self.command_wall = None
                self.permit_started = self.now()
            self.permitted = True
            self.permit_wall = self.now()
            return True, 'motion_permitted'

    def observe_command(self, values, sim_stamp):
        with self.mutex:
            if (len(values) != 6 or not all(math.isfinite(v) for v in values)
                    or any(abs(values[i]) > 1e-8 for i in (1, 2, 3, 4))):
                self.stop('invalid_velocity')
                return
            if self.locked or not self.permitted:
                return
            if (self.clock_sim is None or not math.isfinite(sim_stamp)
                    or abs(sim_stamp - self.clock_sim) > self.command_timeout):
                self.stop('velocity_stamp_stale')
                return
            self.command = (max(-0.15, min(0.15, values[0])),
                            max(-0.5, min(0.5, values[5])))
            self.command_wall = self.now()

    def output(self):
        with self.mutex:
            if self.locked:
                return 0.0, 0.0
            reason = self.health_reason()
            if reason:
                self.stop(reason)
                return 0.0, 0.0
            if not self.permitted:
                return 0.0, 0.0
            now = self.now()
            if now - self.permit_wall > self.permit_timeout:
                self.stop('executor_permit_expired')
                return 0.0, 0.0
            if self.command is None:
                if now - self.permit_started > self.command_timeout:
                    self.stop('velocity_missing')
                return 0.0, 0.0
            if now - self.command_wall > self.command_timeout:
                self.stop('velocity_stale')
                return 0.0, 0.0
            return self.command
