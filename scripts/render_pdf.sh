#!/usr/bin/env bash
# Render a local self-contained Japanese report after navigation stops.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# != 2 || ! -f "$1" || -e "$2" ]]; then
  echo 'Usage: scripts/render_pdf.sh report.html NEW_report.pdf' >&2
  exit 2
fi
if [[ -n $(docker compose ps --status running -q simulation) ]]; then
  echo 'Stop the navigation simulation before PDF rendering.' >&2
  exit 2
fi
source_html=$(realpath "$1")
destination=$(realpath -m "$2")
mkdir -p "$(dirname "$destination")"
profile=$(mktemp -d /tmp/pai-pdf-browser-XXXXXX)
trap 'rm -rf "$profile"' EXIT
chromium --headless --disable-gpu --no-sandbox --disable-dev-shm-usage \
  --disable-background-networking --host-resolver-rules='MAP * ~NOTFOUND' \
  --user-data-dir="$profile" --no-pdf-header-footer --allow-file-access-from-files \
  --print-to-pdf="$destination" "file://$source_html" > "$destination-render.log" 2>&1
[[ -s "$destination" ]] || { echo 'Chromium did not produce a PDF; retained rendering log.' >&2; exit 1; }
echo "$destination"
