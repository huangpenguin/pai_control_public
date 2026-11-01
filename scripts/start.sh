#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .local/runtime.env ]]; then
  set -a
  source .local/runtime.env
  set +a
fi
mkdir -p logs state
export PAI_UID="$(id -u)" PAI_GID="$(id -g)"
backend=${1:-vertex}
if [[ "$backend" != vertex && "$backend" != stub ]]; then
  echo 'Usage: scripts/start.sh [vertex|stub]' >&2
  exit 2
fi
export PAI_PLANNER_MODE="$backend"
if [[ "$backend" == vertex && -n ${PAI_ADC_PATH:-} && -f "$PAI_ADC_PATH" ]]; then
  : "${GOOGLE_CLOUD_PROJECT:?Set an existing Google Cloud project ID}"
  [[ -r "$PAI_ADC_PATH" ]] || { echo 'ADC file is not readable' >&2; exit 1; }
  exec docker compose -f compose.yaml -f compose.vertex.yaml up -d
fi
if [[ "$backend" == vertex ]]; then
  echo 'Starting static diagnostics with Vertex selected. ADC is absent; task requests will fail authentication.' >&2
else
  echo 'Explicit STUB TEST mode: no real AI call will be made.' >&2
fi
exec docker compose up -d
