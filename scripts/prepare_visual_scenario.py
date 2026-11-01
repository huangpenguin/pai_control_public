#!/usr/bin/env python3
"""Select a visual fixture without changing any collision or navigation geometry."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import yaml

COLORS = {'red': ('0.95 0.03 0.03 1', '0.4 0.01 0.01 1'),
          'green': ('0.03 0.95 0.03 1', '0.01 0.4 0.01 1'),
          'gray': ('0.45 0.45 0.45 1', '0.1 0.1 0.1 1')}


def prepare(root, scenario, output_dir):
    config = yaml.safe_load((root / 'config/visual_scenarios.yaml').read_text())
    scenario = scenario or config['default']
    if scenario not in config['scenarios']:
        raise ValueError('Unknown PAI_VISUAL_SCENARIO; select a configured visual fixture')
    states = config['scenarios'][scenario]
    if set(states) != {'station_a', 'station_b'} or any(v not in COLORS for v in states.values()):
        raise ValueError('Invalid visual fixture states')
    source = root / 'worlds/inspection.sdf'
    tree = ET.parse(source)
    for station, color in states.items():
        material = tree.getroot().find(f"world/model[@name='{station}_panel']/link/visual[@name='indicator']/material")
        if material is None:
            raise ValueError('World has no expected visual panel')
        diffuse, emissive = COLORS[color]
        material.find('ambient').text = diffuse
        material.find('diffuse').text = diffuse
        material.find('emissive').text = emissive
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / 'inspection.sdf'
    tree.write(dest, encoding='utf-8', xml_declaration=True)
    # This metadata belongs to simulation evidence, never to classifier input.
    (output_dir / 'visual-fixture.json').write_text(json.dumps({
        'scenario': scenario, 'states': states,
        'source_world_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'runtime_world_sha256': hashlib.sha256(dest.read_bytes()).hexdigest(),
        'classifier_reads_fixture_config': False,
    }, indent=2) + '\n')
    return dest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--simulation-root', type=Path, default=Path('/opt/pai/simulation'))
    parser.add_argument('--scenario', default=os.getenv('PAI_VISUAL_SCENARIO'))
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/pai-world'))
    args = parser.parse_args()
    prepare(args.simulation_root, args.scenario, args.output_dir)
