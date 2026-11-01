# Independent control review (2026-10-11)

Read-only review by the core implementation agent; ROS/simulation owner made the fixes. This is source-review evidence, not a claim of Gazebo/ROS runtime acceptance.

| Original finding | Reviewed correction | Remaining verification |
| --- | --- | --- |
| Upstream gz-math odometry type/header accidentally renamed | `gz/math/DiffDriveOdometry.hh` and `math::DiffDriveOdometry` restored | Full Harmonic compile in container |
| Limiter history/cached wheel velocity read/write could race with transport disable/reset | UpdateVelocity holds mutex; PreUpdate wheel-write block holds same mutex and rechecks command lease/reset; disable clears target, wheel cache and limiter history | Driver stop and guard-kill motion tests |
| Late Nav2 goal response could register after stop/attempt invalidation | Check/register are one RLock block; monotonically changing navigation attempt invalidates obsolete callbacks | ROS callback suite in built container |
| Timeout canceled without confirming Nav2 terminal state | Disable permit before cancel; bounded two-second terminal wait; explicit terminal status required; exception/None/nonterminal results request independent stop and abort | ROS callback tests for missing/exception terminal response |
| Unknown cancellation could otherwise be followed by ordinary resume | Executor locally rejects new actions; guard latches navigation restart fault; stop/resume cannot clear it; pending send/result futures get a bounded terminal check | Pure lock test and 12 ROS callback tests; runtime full-chain recovery pending |
| A failed single-point request falsely reported return requested | `return_requested` derives from actual accepted return step; separate recommendation for failures | Inspect action result JSON in runtime |

Reviewed arrival semantics require Nav2 SUCCEEDED, position/yaw tolerances, fresh TF/odometry, low measured velocity and 0.5 seconds continuous settling. Guard uses steady time for freshness and permit/command timeouts. Pause/reset locks guard; simulator command lease expires independently in 500 milliseconds before actuator writes. Command bridge consumes only the guard's safe velocity output.

Pure core test evidence is separate in `container-core-tests-final.txt` (47 passed with normal plugin autoload); stub semantic evidence is separate in `stub-semantic.json` (16/16). The final Jazzy image ran all 12 ROS callback tests plus 14 pure safety tests (26 passed, zero skipped), and the C++ lease test exited 0; see `runtime-static-regressions-final.json`. ROS callbacks use transport/test doubles. Real Vertex authentication/model calls and real Nav2 motion acceptance remain pending.
