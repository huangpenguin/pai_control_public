# PAI AI Inspection Control

**English** | [简体中文](README.zh-CN.md)

Natural-language instructions → Vertex Gemini → a validated task plan → ROS 2/Nav2 → a TurtleBot3 Waffle Pi in Gazebo. The robot visits named stations, checks an indicator after stopping, and returns to base. This project targets **simulation only**.

The model selects from a small declarative interface; it never supplies executable Python, shell commands, coordinates, or wheel commands. Navigation and safety remain the responsibility of fixed ROS components.

This is a development of porizou1's [Qiita example](https://qiita.com/porizou1/items/a085d20e936946330540), which connects natural language, an LLM, ROS 2, and a simulated TurtleBot3. The additions are ordered named-station tasks, actual Nav2 feedback, and indicator recognition from fresh camera images after verified arrival and standstill. The original example generates and runs Python nodes; this implementation uses a fixed executor instead.

## What has been demonstrated

The experiments below were performed on **2026-10-11**. They measure different things and must not be combined into one success rate.

| Evaluation | Result | Interpretation |
| --- | --- | --- |
| Frozen semantic suite, Gemini 3.1 Pro Preview with prompt v3 | 24/24, all hard requirements passed | A fixed set of labeled instructions, not a guarantee for arbitrary language |
| Earlier end-to-end online navigation tasks, B → A → base | 1/3 complete tasks | The first run stopped on a stale velocity command; the second reached B and A but failed to make progress toward base; both failures are retained |
| Online rejection and unreachable-station tests | 2/2 expected behaviors | An unknown station produced no navigation goal; an isolated unreachable A was attempted twice, followed by a successful base goal |
| Formal visual tasks across three scenarios | 3/3 tasks; 5/5 fresh images | Includes swapping the colors at the same stations, and a gray panel reported as `unknown` before returning to base |

An earlier P1 check also demonstrated **real Gemini plan generation followed by saved-plan execution in Gazebo**. Its execution result was 1/2 attempts: the first test helper used the wrong clock; the second succeeded after a test-only clock correction. This was a two-stage check, not an online call within the executing process.

Rotation Shim wrapping the original DWB controller enabled the successful third online navigation run. One success does not establish long-term reliability or prove that every earlier failure cause was resolved. Formal visual tasks are a separate, later set of experiments; they do not turn the historical navigation result into 3/3.

See [status and evidence](docs/STATUS.md), [model evaluation and tuning](docs/AI_EVALUATION.md), the [Japanese assessment report](docs/REPORT_JA.md), and [compact evidence](docs/evidence).

## Architecture and supported instructions

The only plan operations are:

- `navigate_to(place_id)`
- `inspect_indicator(place_id)`
- `return_to_base()`
- `stop()`

Allowed places are `base`, `station_a`, and `station_b`. The model must provide evidence quoted from the original instruction. Strict JSON/schema checks are followed by local semantic checks for places, operations, order, and inspection intent. The executor uses the validated model steps; it does not replace them with a locally fabricated route.

The supported language is a deliberately restricted set of Chinese and Japanese instructions, canonical place IDs, and aliases. Examples include:

```text
去设备 A
先去 B，再去 A，最后回起点
まず設備Bへ行き、次に設備Aへ行って、最後に出発地点へ戻ってください。
先去设备 A 检查指示灯，再去设备 B，最后回起点。红灯记为异常，绿灯记为正常，看不清就记为未确认。
```

In the last example, the explicit inspection context carries over to B, so both stations are inspected. A plain navigation instruction does not add an inspection. The red/green/unclear recording rule does not create conditional navigation.

Unknown or ambiguous places, mixtures of known and unknown places, negation, conditional navigation, and unsupported wording reject the whole plan. There is no silent mapping to a known station. Inputs are limited to 512 characters and plans to 8 steps. Add labeled regression cases before expanding the accepted language. English prose in this README does **not** imply support for arbitrary English robot instructions.

### Localization and navigation do not require project-specific training

The map, station coordinates, and initial pose are configured by hand. There is no project-specific model training or SLAM step. AMCL matches laser scans against the static map, using odometry and TF; Nav2 plans and controls movement online. The LLM reasons about named places, not learned routes.

Coordinates in [stations.yaml](simulation/config/stations.yaml) use meters and yaw in radians:

| Place | x | y | yaw |
| --- | ---: | ---: | ---: |
| `base` | -3 | -2 | 0 |
| `station_a` | 3 | -2 | 0 |
| `station_b` | 3 | 2 | π/2 |

[generate_world.py](scripts/generate_world.py) creates the world and map from common geometry. Station markers are visual aids, not localization inputs. The camera classifies the indicator panel after arrival; it does not recognize station identity. Changes to base position also require consistent spawn and AMCL initial-pose configuration. See [simulation launch](simulation/launch/bringup.launch.py) and [Nav2 configuration](src/pai_inspection_ros/config/nav2.yaml).

## Environment and quick start

The container uses Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic, Nav2, and TurtleBot3 Waffle Pi. Simulation runs headlessly on the CPU, without a GPU or published host ports. The host needs Docker Engine and Compose v2. Builds use pinned source revisions in [dependencies.repos](dependencies.repos), a base-image digest in [Dockerfile](Dockerfile), and a Python dependency lock. Installed Debian packages are recorded inside the image at `/opt/pai/debian-packages.lock`.

The camera defaults to 640×480 at 5 simulated Hz; laser scans are configured at 10 simulated Hz. `PAI_CAMERA_UPDATE_RATE` changes the camera update rate. CPU simulation may run slower than real time; wall-clock freshness and safety deadlines still apply.

```bash
git clone https://github.com/huangpenguin/pai_control_public.git
cd pai_control_public
scripts/build.sh
scripts/start.sh                 # Real Vertex backend by default
scripts/diagnose.sh 15           # Read-only sensor/TF diagnostics; no task or LLM call
```

Without ADC, simulation and diagnostics can start, but a real planning request fails explicitly. There is no automatic fallback to a test double. To test only the simulation pipeline without paid model calls, start explicitly with `scripts/start.sh stub`; its results are labeled `stub`/`stub-test-double` and are not real AI results.

After diagnostics pass and Vertex authentication is configured, unlock and send a task:

```bash
scripts/ros.sh ros2 run pai_inspection_ros instruction_cli resume
scripts/ros.sh ros2 run pai_inspection_ros instruction_cli send '去设备 A'
# Or an ordered navigation task:
scripts/ros.sh ros2 run pai_inspection_ros instruction_cli send '先去 B，再去 A，最后回起点'
# Or a visual task:
scripts/ros.sh ros2 run pai_inspection_ros instruction_cli send '先去设备 B 检查指示灯，再去设备 A 检查指示灯，最后回起点'
```

`resume` clears a healthy safety lock and leaves the motion permit off. It does not resume an old task or call the model. A new `send` is required. Do not run `resume` during an active task: it invalidates that task. A normally completed CLI task revokes its motion permit and usually remains unlocked, so another `send` can follow directly.

Keep a separate terminal available for the independent stop command:

```bash
scripts/ros.sh ros2 run pai_inspection_ros instruction_cli stop
# Shut down the entire simulation when finished:
scripts/stop.sh
```

**Ctrl-C in the task client is not a safety stop.** After a manual stop, use `resume` and submit a new task. If a goal or cancellation terminal state cannot be confirmed, or the simulation clock resets, the system requires a full restart with `scripts/stop.sh` followed by `scripts/start.sh vertex`; ordinary `stop`/`resume` cannot clear the navigation fault lock.

### Core-only setup

The planning core can be used without ROS. The official `google-genai==2.29.0` SDK and transitive dependencies are pinned in [requirements.lock](requirements.lock).

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m pytest tests/core -q
.venv/bin/pai-plan --mode stub --text '去设备 A'
.venv/bin/pai-evaluate --mode stub --output /tmp/pai-stub-semantic.json
```

`pai-plan` and `pai-evaluate` only plan/evaluate; they never move the robot. Exit codes are 0 for an accepted plan, 2 for rejection, and 1 for authentication, budget, invalid-response, or other errors. Test-double evaluations are kept separate from real model evaluations.

## Vertex authentication and model configuration

Use an existing Google Cloud project that you are authorized to access. The project does not create cloud projects, change IAM, enable APIs, change billing, or configure cloud budgets. The SDK uses `vertexai=True` and Application Default Credentials (ADC). Imports do not read credentials.

```bash
export GOOGLE_CLOUD_PROJECT='YOUR_EXISTING_PROJECT_ID'
scripts/auth_adc.sh
export PAI_ADC_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/pai_ai_control/gcloud/application_default_credentials.json"
export GOOGLE_CLOUD_LOCATION=global
export PAI_VERTEX_MODEL=gemini-3.1-pro-preview
scripts/start.sh vertex
```

The authentication script prints the actual ADC location. Its default is the user's configuration directory under `pai_ai_control/gcloud/`; `PAI_ADC_DIR` can override it. Compose mounts only the credential file, read-only, at runtime. Local project/path settings can be stored in ignored `.local/runtime.env`; never commit credentials or private project configuration.

For host-side core requests, also set `GOOGLE_APPLICATION_CREDENTIALS="$PAI_ADC_PATH"`. A command such as `.venv/bin/pai-plan --text '去设备 A'` then makes a real, potentially billable request, subject to the application budget below.

If quota-project configuration fails after ADC was saved, do not repeat OAuth unnecessarily. After an administrator confirms the required project permission, `scripts/auth_adc.sh --quota-only` retries only quota-project setup. The historical diagnosis and its limitations are recorded in [ADC setup](docs/ADC_SETUP.md). Successful inference in that reference setup did not imply permission to read all API or quota settings.

The selected default is **`gemini-3.1-pro-preview`, global, LOW thinking**, with prompt `task-plan-v3`, a 2,048-token output limit, one SDK HTTP attempt, and a 30-second HTTP timeout. Selection followed same-prompt, same-case comparisons: Flash-Lite failed hard interface/semantic cases; Flash 3.8 scored 23/24 but failed an eight-step hard requirement; Pro passed 24/24. Only language/interface failures justify considering a stronger model; changing models does not fix ROS execution failures.

These are experiment-time results, not a claim that the preview model is permanently available or optimal. Override `PAI_VERTEX_MODEL` explicitly and rerun the frozen semantic evaluation before using a different model. There is no silent model substitution. The old `gemini-2.5-flash` configuration is rejected from its recorded Vertex retirement date, 2026-10-20. Lifecycle and pricing sources were checked on 2026-10-11; see [evaluation details](docs/AI_EVALUATION.md) and [Google's model lifecycle](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions).

## Budget and request records

The reference experiment ledger was explicitly raised to a cumulative **US$5 or 300 actual attempts**, whichever limit is reached first, with an 80% warning. **A new ledger starts at US$1/100**; an increase requires an explicit command:

```bash
.venv/bin/pai-budget show
.venv/bin/pai-budget set --max-usd 5 --max-attempts 300
```

All models and processes share the persistent SQLite policy and accounting in `state/vertex-usage.sqlite3`; Compose binds the host `state/` directory. Preserve that directory to retain cumulative accounting. Changing environment variables does not raise the budget. The policy and request reservation use the same atomic database transaction, preventing concurrent requests from independently spending the same remaining allowance.

Before sending a request, the ledger reserves a conservative cost upper bound. Actual `usage_metadata`, including thinking-related fields, is retained when returned. Missing usage or a timeout leaves the reservation marked `unknown`, rather than inventing zero cost. Invalid or timed-out planning does not initiate motion, and there is no automatic replanning loop.

The final reference ledger contains **65 attempts and US$0.414520 of accounted usage/reservations**: US$0.166363 is an unknown reservation retained from an early failed diagnostic request; US$0.248157 is a token-based estimate for known responses. Neither the reservation nor the total is a confirmed Google invoice. This application limit does not cover other applications or manual calls and is **not a Google billing hard cap**.

Standard online text prices were recorded from [Google's pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing) on 2026-10-11. The selected Pro profile conservatively uses US$2 input / US$12 generated tokens per million tokens; exact model profiles, usage accounting, and comparison evidence are in [AI evaluation](docs/AI_EVALUATION.md) and the [budget migration record](docs/evidence/budget-policy-update.json). Check current availability and prices before further paid runs.

## Navigation, vision, and safety boundaries

A navigation result requires an actual successful Nav2 terminal state, a fresh localized pose/odometry, arrival within 0.20 m and 0.25 rad, and continuous standstill for at least 0.5 wall-clock seconds. Each goal has a bounded budget and at most one retry. A known unreachable station is reported as failed, then the remaining plan continues; safety faults abort the task.

The model plans before movement. A fixed executor follows that validated plan, a safety guard controls motion permission, and a custom differential-drive plugin independently expires old commands. Stop handling does not depend on an LLM. The Rotation Shim controller wraps DWB; speed, freshness, progress, and arrival limits were not relaxed to obtain a passing run.

For inspection, the executor revokes motion permission, checks standstill again, and waits for a new camera frame. OpenCV classifies actual RGB pixels within a frozen ROI using documented color/quality thresholds; the classifier does not receive station-specific expected colors or scenario labels. A red panel is abnormal, green is normal, and unclear color is unconfirmed. Missing, stale, or timed-out camera input yields `unknown` with a reason, and navigation may continue if its required health checks remain valid. A navigation-health failure still aborts. See [vision implementation](src/pai_inspection_ros/pai_inspection_ros/indicator_vision.py) and [formal visual evidence](docs/evidence).

The formal scenes use colored panels under fixed simulated conditions. The results do not establish physical LED recognition, arbitrary lighting robustness, real-hardware safety, or long-term reliability. Healthy bounded contact recordings observed zero events in the formal tasks, backed by a separate positive probe; this is not a lossless proof of zero collisions, nor evidence for earlier unrecorded runs.

## Reproduction, reports, and provenance

Start with the current public source, its pinned dependencies, configuration, and scripts. You do **not** need access to private development commits. Older implementation commit IDs and image hashes in evidence describe internal experiment-time provenance; they are not public revisions readers must check out.

- [STATUS](docs/STATUS.md): verified results, retained failures, and evidence references.
- [AI evaluation and tuning](docs/AI_EVALUATION.md): frozen cases, hard gates, error attribution, and locations for manual improvements.
- [Original 16 labeled cases](docs/semantic_cases.json) and [24-case acceptance suite](docs/semantic_acceptance_cases.json): regression inputs and expectations.
- [Japanese assessment report, Markdown](docs/REPORT_JA.md) and [HTML](docs/REPORT_JA.html): overview, purpose, implementation choices, results, and limitations.
- [Delivery and reproduction guide](docs/DELIVERY.md): source/evidence scope and experiment reproduction commands.
- [Compact evidence directory](docs/evidence): auditable model, navigation, and visual records, including actual example images.

The verified reference runtime image passed 49 ROS/control/vision regression tests; the exact external observation-clock helper and its three targeted tests are also retained in source. They were external to that image, not retroactively claimed as baked into it. A fresh build produces its own image hash.

Full recordings, local PDF/video copies, and user-facing submission instructions can exist in ignored local `logs/`, `upload/`, `human_review/`, or historical `submission/` directories. They are optional local artifacts, **not files included in a clone or prerequisites for running the public source**. Offline video reconstruction requires the original recordings; the compact evidence alone cannot regenerate them. The final demonstrated video is a Japanese-explained 178.1-second, 2× edit of formal trial 2, with a clearly separated two-second summary of other trials; its right-hand map view visualizes TF logs, not an overhead simulator camera.

## License and source acknowledgement

Original project files use [Apache-2.0](LICENSE). Third-party components retain their licenses and notices; see [third-party notices](docs/THIRD_PARTY_NOTICES.md) and [SafeDiffDrive upstream attribution](src/pai_sim_safety/UPSTREAM.md). The Qiita article is a design reference; this project does not claim that the article's code was granted an Apache license.

The public repository is [huangpenguin/pai_control_public](https://github.com/huangpenguin/pai_control_public), with `main` as the default branch. Its history starts from a reviewed, independent public root snapshot. Private development history, credentials, and local experiment data were not published. Source delivery uses Git; no source ZIP is required.
