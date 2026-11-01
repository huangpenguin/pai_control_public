#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
report_name="static-$(date -u +%Y%m%dT%H%M%SZ)"
docker compose exec -T simulation /opt/pai/scripts/container_entrypoint.sh \
  python3 /opt/pai/scripts/diagnose.py --seconds "${1:-15}" \
  --output "/opt/pai/logs/$report_name.json" | tee "logs/$report_name.log"
cp "logs/$report_name.json" logs/static-diagnostics.json
if [[ -f "logs/$report_name-camera.png" ]]; then
  cp "logs/$report_name-camera.png" logs/static-camera.png
fi
