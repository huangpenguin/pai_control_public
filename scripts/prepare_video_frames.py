#!/usr/bin/env python3
"""Offline actual-camera / logged-trajectory layout; no simulation or model call."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import yaml


def main():
    cv2.setNumThreads(1)
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture-dir', type=Path, required=True)
    parser.add_argument('--speed', type=float, default=4)
    args = parser.parse_args()
    if not 1 <= args.speed <= 16:
        parser.error('Speed must be within [1,16]')
    capture = json.loads((args.capture_dir / 'capture.json').read_text())
    frames = capture['frames']
    if len(frames) < 2:
        parser.error('At least two actual frames required')
    output = args.capture_dir / f'render-x{args.speed:g}'
    output.mkdir()  # Preserve any previous encoding attempt.
    root = Path('/opt/pai/simulation/maps')
    map_yaml = yaml.safe_load((root / 'inspection.yaml').read_text())
    map_path = root / map_yaml['image']
    if hashlib.sha256(map_path.read_bytes()).hexdigest() != capture['map_sha256']:
        raise ValueError('Offline map differs from actual capture map')
    raster = cv2.imread(str(map_path), cv2.IMREAD_GRAYSCALE)
    map_height, map_width = raster.shape
    scale = 2
    map_image = cv2.cvtColor(cv2.resize(raster, (map_width * scale, map_height * scale),
                                      interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
    origin_x, origin_y, _ = map_yaml['origin']
    resolution = map_yaml['resolution']
    trail = []
    concat = []
    for i, frame in enumerate(frames):
        camera = cv2.imread(str(args.capture_dir / frame['file']))
        if camera is None:
            raise ValueError('Actual camera frame missing')
        camera = cv2.resize(camera, (640, 480))
        canvas = np.full((560, 1060, 3), 245, np.uint8)
        canvas[40:520, :640] = camera
        current_map = map_image.copy()
        pose = frame['map_pose']
        if pose:
            point = (round((pose['x'] - origin_x) / resolution * scale),
                     round((map_height - 1 - (pose['y'] - origin_y) / resolution) * scale))
            trail.append(point)
        if len(trail) >= 2:
            cv2.polylines(current_map, [np.asarray(trail, dtype=np.int32)], False, (170, 80, 0), 2)
        if trail:
            cv2.circle(current_map, trail[-1], 5, (0, 0, 230), -1)
        canvas[80:80 + current_map.shape[0], 642:642 + current_map.shape[1]] = current_map
        labels = [('Gazebo camera: actual sampled frames', (8, 25)),
                  ('Map / TF trajectory: log visualization', (648, 25)),
                  (f'{args.speed:g}x replay | wall +{frame["wall_s"] - frames[0]["wall_s"]:.1f}s | camera sim {frame["image_sim_s"]:.2f}s', (8, 545))]
        state = frame.get('safety')
        if state:
            labels.append((f'Guard locked={state["locked"]} gen={state["generation"]}', (648, 455)))
        if pose:
            labels.append((f'actual map x={pose["x"]:.2f} y={pose["y"]:.2f}', (648, 485)))
        for text, pos in labels:
            cv2.putText(canvas, text, pos, cv2.FONT_HERSHEY_SIMPLEX, .47, (20, 20, 20), 1, cv2.LINE_AA)
        name = f'composite-{i:06d}.jpg'
        cv2.imwrite(str(output / name), canvas, [cv2.IMWRITE_JPEG_QUALITY, 85])
        next_wall = frames[i + 1]['wall_s'] if i + 1 < len(frames) else capture['collection_ended_wall_s']
        duration = max(next_wall - frame['wall_s'], .001) / args.speed
        concat += [f"file '{name}'", f'duration {duration:.9f}']
    concat.append(f"file 'composite-{len(frames)-1:06d}.jpg'")
    (output / 'concat.txt').write_text('\n'.join(concat) + '\n')
    (output / 'render.json').write_text(json.dumps({'actual_capture_source': str(args.capture_dir),
        'capture_sha256': hashlib.sha256((args.capture_dir / 'capture.json').read_bytes()).hexdigest(),
        'speedup': args.speed, 'actual_wall_start_s': frames[0]['wall_s'],
        'actual_wall_end_s': capture['collection_ended_wall_s'],
        'expected_video_seconds': (capture['collection_ended_wall_s'] - frames[0]['wall_s']) / args.speed,
        'left': 'actual sampled Gazebo camera, JPEG compressed',
        'right': 'logged real TF positions on matching static occupancy map, visualization',
        'model_calls': 0, 'simulation_started': False}, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
