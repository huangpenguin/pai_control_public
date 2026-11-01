"""Headless Gazebo with one safety drive and an explicit one-way command bridge."""
import os
from pathlib import Path
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    sim = Path('/opt/pai/simulation')
    share = Path(get_package_share_directory('turtlebot3_gazebo'))
    robot = (share / 'urdf/turtlebot3_waffle_pi.urdf').read_text()
    gz_launch = Path(get_package_share_directory('ros_gz_sim')) / 'launch/gz_sim.launch.py'
    inspection = Path(get_package_share_directory('pai_inspection_ros')) / 'launch/inspection.launch.py'
    return LaunchDescription([
        DeclareLaunchArgument('navigation', default_value='true'),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(gz_launch)),
            launch_arguments={'gz_args': '-r -s --headless-rendering -v3 ' + os.environ.get('PAI_SIM_WORLD_PATH', str(sim / 'worlds/inspection.sdf')),
                              'on_exit_shutdown': 'true'}.items()),
        Node(package='robot_state_publisher', executable='robot_state_publisher',
             parameters=[{'use_sim_time': True, 'robot_description': robot}], output='screen'),
        Node(package='ros_gz_sim', executable='create',
             arguments=['-world', 'inspection', '-name', 'waffle_pi', '-file', os.environ.get('PAI_SIM_MODEL_PATH', str(sim / 'models/waffle_pi.sdf')),
                        '-x', '-3', '-y', '-2', '-z', '0.01'], output='screen'),
        Node(package='ros_gz_bridge', executable='parameter_bridge',
             name='sensor_bridge',
             parameters=[{'config_file': str(sim / 'config/bridge.yaml'), 'use_sim_time': True}], output='screen'),
        Node(package='ros_gz_bridge', executable='parameter_bridge',
             name='safe_velocity_bridge',
             parameters=[{'config_file': str(sim / 'config/safe_bridge.yaml'), 'use_sim_time': True}], output='screen'),
        Node(package='ros_gz_bridge', executable='parameter_bridge',
             name='contact_bridge',
             parameters=[{'config_file': str(sim / 'config/contact_bridge.yaml'), 'use_sim_time': True}], output='screen'),
        Node(package='ros_gz_image', executable='image_bridge', arguments=['/camera/image_raw'],
             parameters=[{'use_sim_time': True}], output='screen'),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(inspection)),
             condition=IfCondition(LaunchConfiguration('navigation'))),
    ])
