#!/usr/bin/env bash
# Execute a ROS command in the running project's fully sourced environment.
set -euo pipefail
cd "$(dirname "$0")/.."
exec docker compose exec -T simulation /opt/pai/scripts/container_entrypoint.sh "$@"
