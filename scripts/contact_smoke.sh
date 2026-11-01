#!/usr/bin/env bash
# Independent native-physics probe; run only after the navigation instance stops.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -n $(docker compose ps --status running -q simulation) ]]; then
  echo 'Stop the navigation simulation before running the independent contact probe.' >&2
  exit 2
fi
mkdir -p logs
run_id="contact-smoke-$(date -u +%Y%m%dT%H%M%SZ)"
image=${PAI_SMOKE_IMAGE:-pai-inspection:p1}
docker image inspect "$image" --format '{{.Id}}' > "logs/$run_id-image.txt"
source_mounts=()
if [[ ${PAI_SMOKE_SOURCE_OVERLAY:-0} == 1 ]]; then
  # Reviewed environment source only; this never overlays core or ROS packages.
  source_mounts=(--mount "type=bind,src=$PWD/scripts,dst=/opt/pai/scripts,readonly"
                 --mount "type=bind,src=$PWD/simulation,dst=/opt/pai/simulation,readonly")
  python3 - "$run_id" <<'PY'
import hashlib, json, pathlib, sys
files = {}
for folder in ('scripts', 'simulation'):
    for p in pathlib.Path(folder).rglob('*'):
        if p.is_file() and p.suffix in ('.py', '.sh', '.sdf', '.yaml', '.pgm'):
            files[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
pathlib.Path('logs/' + sys.argv[1] + '-source-sha.json').write_text(json.dumps({
    'test_only_environment_source_overlay': True,
    'mounted_read_only': ['scripts', 'simulation'],
    'core_or_src_overlay': False, 'files_sha256': files,
}, indent=2) + '\n')
PY
elif [[ ${PAI_SMOKE_SOURCE_OVERLAY:-0} != 0 ]]; then
  echo 'PAI_SMOKE_SOURCE_OVERLAY must be 0 or 1' >&2
  exit 2
fi
docker run --rm --name pai-contact-smoke --network none --cap-drop ALL \
  --security-opt no-new-privileges --cpus 1 --memory 1g --pids-limit 256 \
  --user "$(id -u):$(id -g)" --tmpfs /tmp:rw,mode=1777 \
  --mount "type=bind,src=$PWD/logs,dst=/opt/pai/logs" \
  "${source_mounts[@]}" \
  -e ROS_DOMAIN_ID=88 -e GZ_PARTITION=pai_contact_smoke \
  -e "PAI_SMOKE_RUN_ID=$run_id" "$image" bash -c '
set -euo pipefail
gz sim -r -s /opt/pai/simulation/worlds/contact_smoke.sdf > "/opt/pai/logs/$PAI_SMOKE_RUN_ID-gz.log" 2>&1 &
gazebo_pid=$!
ros2 run ros_gz_bridge parameter_bridge "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock" \
  --ros-args -r __node:=contact_smoke_clock_bridge \
  -p use_sim_time:=true > "/opt/pai/logs/$PAI_SMOKE_RUN_ID-clock-bridge.log" 2>&1 &
clock_bridge_pid=$!
ros2 run ros_gz_bridge parameter_bridge --ros-args -r __node:=contact_smoke_bridge \
  -p config_file:=/opt/pai/simulation/config/contact_bridge.yaml \
  -p use_sim_time:=true > "/opt/pai/logs/$PAI_SMOKE_RUN_ID-bridge.log" 2>&1 &
bridge_pid=$!
trap '\''kill "$gazebo_pid" "$bridge_pid" "$clock_bridge_pid" 2>/dev/null || true; wait || true'\'' EXIT
python3 /opt/pai/scripts/record_contacts.py --seconds 60 --smoke-test \
  --output "/opt/pai/logs/$PAI_SMOKE_RUN_ID.json"
' > "logs/$run_id.log" 2>&1
echo "logs/$run_id.json"
