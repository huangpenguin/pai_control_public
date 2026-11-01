#!/usr/bin/env python3
"""Generate a static metric map matching collision geometry; no Gazebo/Fuel downloads."""
from pathlib import Path
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1] / 'simulation'
boxes = [('west_wall', -5, 0, .2, 8.2), ('east_wall', 5, 0, .2, 8.2),
         ('south_wall', 0, -4, 10.2, .2), ('north_wall', 0, 4, 10.2, .2),
         ('central_obstacle', 0, 0, 1.4, 1.4)]
models = []
for name, x, y, sx, sy in boxes:
    color = '0.25 0.35 0.5 1' if name == 'central_obstacle' else '0.75 0.75 0.75 1'
    models.append(f'''<model name="{name}"><static>true</static><pose>{x} {y} 0.5 0 0 0</pose>
      <link name="body"><collision name="collision"><geometry><box><size>{sx} {sy} 1</size></box></geometry></collision>
      <sensor name="obstacle_contact" type="contact"><always_on>true</always_on>
        <contact><collision>collision</collision><topic>/pai/contacts/{name}</topic></contact></sensor>
      <visual name="visual"><geometry><box><size>{sx} {sy} 1</size></box></geometry>
      <material><ambient>{color}</ambient><diffuse>{color}</diffuse></material></visual></link></model>''')
for name, x, y, yaw in [('station_a', 4, -2, 0), ('station_b', 3, 3, 1.5707963267948966)]:
    # Visual-only panels face each station's arrival direction. Navigation
    # collision geometry and the matching occupancy map stay unchanged.
    models.append(f'''<model name="{name}_panel"><static>true</static><pose>{x} {y} 0.15 0 0 {yaw}</pose>
      <link name="panel"><visual name="frame"><geometry><box><size>.03 .56 .32</size></box></geometry>
        <material><ambient>.08 .08 .08 1</ambient><diffuse>.08 .08 .08 1</diffuse></material></visual>
      <visual name="indicator"><pose>-.02 0 0 0 0 0</pose><geometry><box><size>.012 .46 .24</size></box></geometry>
        <material><ambient>.45 .45 .45 1</ambient><diffuse>.45 .45 .45 1</diffuse><emissive>.1 .1 .1 1</emissive></material></visual>
      </link></model>''')
for name, x, y, color in [('base',-3,-2,'0.2 0.6 0.9 1'), ('station_a',3,-2,'0.2 0.9 0.3 1'),
                         ('station_b',3,2,'0.9 0.6 0.2 1')]:
    models.append(f'''<model name="{name}_marker"><static>true</static><pose>{x} {y} 0.002 0 0 0</pose>
      <link name="marker"><visual name="visual"><geometry><box><size>.5 .5 .004</size></box></geometry>
      <material><ambient>{color}</ambient><diffuse>{color}</diffuse></material></visual></link></model>''')
world = '''<?xml version="1.0"?><sdf version="1.9"><world name="inspection">
  <physics name="default_physics" type="ignored"><max_step_size>0.004</max_step_size><real_time_factor>1</real_time_factor></physics>
  <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
  <plugin filename="gz-sim-contact-system" name="gz::sim::systems::Contact"/>
  <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
  <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
  <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
  <plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>
  <scene><ambient>0.8 0.8 0.8 1</ambient><background>0.8 0.85 0.9 1</background><shadows>false</shadows></scene>
  <light name="sun" type="directional"><pose>0 0 10 0 0 0</pose><diffuse>0.8 0.8 0.8 1</diffuse><specular>0.1 0.1 0.1 1</specular><direction>-0.5 0.1 -0.9</direction><cast_shadows>false</cast_shadows></light>
  <model name="ground"><static>true</static><link name="ground"><collision name="ground"><geometry><plane><normal>0 0 1</normal><size>20 20</size></plane></geometry></collision>
  <visual name="ground"><geometry><plane><normal>0 0 1</normal><size>20 20</size></plane></geometry><material><ambient>0.7 0.7 0.7 1</ambient><diffuse>0.7 0.7 0.7 1</diffuse></material></visual></link></model>
''' + '\n'.join(models) + '\n</world></sdf>\n'
(root / 'worlds').mkdir(parents=True, exist_ok=True)
(root / 'worlds/inspection.sdf').write_text(world)
# Independent positive control for the contact sensor/bridge/recorder pipeline.
# A small cube falls under native physics onto central_obstacle; no task robot,
# navigation stack, image renderer, planner, credentials or motion commands.
probe = ET.fromstring(world)
probe_world = probe.find('world')
probe_world.set('name', 'contact_smoke')
for plugin in list(probe_world.findall('plugin')):
    if plugin.get('name') not in ('gz::sim::systems::Physics', 'gz::sim::systems::Contact'):
        probe_world.remove(plugin)
for model in list(probe_world.findall('model')):
    if model.get('name') not in ('ground', *(name for name, *_ in boxes)):
        probe_world.remove(model)
probe_world.append(ET.fromstring('''<model name="contact_probe_robot"><pose>0 0 2 0 0 0</pose>
  <link name="probe"><inertial><mass>1</mass><inertia><ixx>.0066667</ixx><iyy>.0066667</iyy><izz>.0066667</izz></inertia></inertial>
  <collision name="probe_collision"><geometry><box><size>.2 .2 .2</size></box></geometry></collision>
  </link></model>'''))
ET.ElementTree(probe).write(root / 'worlds/contact_smoke.sdf', encoding='utf-8', xml_declaration=True)
width, height, resolution = 208, 168, .05
pixels = []
for row in range(height):
    y = -4.2 + (height - row - .5) * resolution
    values = []
    for col in range(width):
        x = -5.2 + (col + .5) * resolution
        occupied = any(abs(x-bx) <= sx/2 and abs(y-by) <= sy/2 for _, bx, by, sx, sy in boxes)
        values.append('0' if occupied else '254')
    pixels.append(' '.join(values))
(root / 'maps/inspection.pgm').write_text(f'P2\n# matching inspection.sdf at 0.05m/cell\n{width} {height}\n255\n' + '\n'.join(pixels) + '\n')
