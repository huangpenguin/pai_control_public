#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
build_log="logs/build-$(date -u +%Y%m%dT%H%M%SZ).log"
docker compose --progress plain build 2>&1 | tee "$build_log" logs/build.log
