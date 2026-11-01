#!/usr/bin/env bash
# Encode only after navigation stops; CPU software encoding, no additional task.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo 'Usage: scripts/render_video.sh logs/CAPTURE_DIRECTORY [speedup]' >&2
  exit 2
fi
if [[ -n $(docker compose ps --status running -q simulation) ]]; then
  echo 'Stop the navigation simulation before offline video encoding.' >&2
  exit 2
fi
capture=$(realpath "$1")
logs_root=$(realpath logs)
[[ "$capture" == "$logs_root/"* ]] || { echo 'Capture must be under logs/' >&2; exit 2; }
relative=${capture#"$logs_root/"}
speed=${2:-4}
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --cpus 1 --memory 1g --user "$(id -u):$(id -g)" --tmpfs /tmp:rw,mode=1777 \
  --mount "type=bind,src=$logs_root,dst=/opt/pai/logs" pai-inspection:p1 \
  python3 /opt/pai/scripts/prepare_video_frames.py --capture-dir "/opt/pai/logs/$relative" --speed "$speed"
render=$(python3 - "$speed" <<'PY'
import sys
print(f'render-x{float(sys.argv[1]):g}')
PY
)
ffmpeg -nostdin -n -hide_banner -loglevel warning -threads 1 \
  -f concat -safe 0 -i "$capture/$render/concat.txt" -vf fps=10 \
  -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -threads 1 \
  "$capture/$render/video.mp4" > "$capture/$render/ffmpeg.log" 2>&1
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height \
  -of json "$capture/$render/video.mp4" > "$capture/$render/ffprobe.json"
echo "$capture/$render/video.mp4"
