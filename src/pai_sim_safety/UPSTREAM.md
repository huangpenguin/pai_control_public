Upstream: https://github.com/gazebosim/gz-sim
Revision: a9c03d6b5ddea74023978200fe02166250d01f69 (gz-sim8, Harmonic)
Files: src/systems/diff_drive/DiffDrive.cc, DiffDrive.hh
License: Apache-2.0; original headers retained.

Project changes: unique SafeDiffDrive class/library/alias; 500 ms steady-clock
command lease checked before wheel writes, clearing target, wheel caches and
limiter history. World reset/rewind permanently locks this driver until server
restart; original odometry implementation is retained. No forwarding watchdog.
