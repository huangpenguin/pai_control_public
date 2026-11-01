#!/usr/bin/env bash
set -eo pipefail
mkdir -p "$HOME"
source /opt/ros/jazzy/setup.bash
source /workspace/install/setup.bash
export PATH="/opt/pai/venv/bin:$PATH"
# ament console scripts use /usr/bin/python3; expose the locked SDK to them too.
export PYTHONPATH="/opt/pai/core:/opt/pai/venv/lib/python3.12/site-packages:${PYTHONPATH:-}"
export GZ_SIM_RESOURCE_PATH="/workspace/install/turtlebot3_gazebo/share/turtlebot3_gazebo/models:/opt/pai/simulation/models:${GZ_SIM_RESOURCE_PATH:-}"
export GZ_SIM_SYSTEM_PLUGIN_PATH="/workspace/install/pai_sim_safety/lib:${GZ_SIM_SYSTEM_PLUGIN_PATH:-}"
export ROS_LOG_DIR=/opt/pai/logs/ros
export PAI_SIM_MODEL_PATH=/tmp/pai-models/waffle_pi.sdf
export PAI_SIM_WORLD_PATH=/tmp/pai-world/inspection.sdf
python3 /opt/pai/scripts/prepare_simulation.py --output-dir /tmp/pai-models
python3 /opt/pai/scripts/prepare_visual_scenario.py --output-dir /tmp/pai-world
exec "$@"
