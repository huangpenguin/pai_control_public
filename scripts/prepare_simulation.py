#!/usr/bin/env python3
"""Derive the robot from the pinned ROBOTIS model; keep its sensors and geometry."""
import argparse
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from ament_index_python.packages import get_package_share_directory

share = Path(get_package_share_directory('turtlebot3_gazebo'))
parser = argparse.ArgumentParser()
parser.add_argument('--output-dir', type=Path, default=Path('/opt/pai/simulation/models'))
args = parser.parse_args()
tree = ET.parse(share / 'models/turtlebot3_waffle_pi/model.sdf')
model = tree.getroot().find('model')
model.set('name', 'waffle_pi')
drivers = [p for p in model.findall('plugin') if p.get('name') == 'gz::sim::systems::DiffDrive']
assert len(drivers) == 1, 'Pinned model must contain exactly one drive'
drive = drivers[0]
drive.set('filename', 'libpai_diff_drive.so')
drive.set('name', 'pai::sim::SafeDiffDrive')
drive.find('topic').text = '/pai/safe_cmd_vel'
# The original 30 Hz camera caused observed lidar wall-clock staleness with
# CPU-only llvmpipe. Keep its real images and geometry, reduce render workload.
camera_rate = float(os.environ.get('PAI_CAMERA_UPDATE_RATE', '5'))
if not 0 < camera_rate <= 30:
    raise ValueError('PAI_CAMERA_UPDATE_RATE must be within (0, 30] Hz')
for sensor in model.findall('.//sensor'):
    if sensor.get('type') == 'camera':
        sensor.find('update_rate').text = str(camera_rate)
dest = args.output_dir
dest.mkdir(parents=True, exist_ok=True)
tree.write(dest / 'waffle_pi.sdf', encoding='utf-8', xml_declaration=True)
