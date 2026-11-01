"""Small Nav2 stack; every motion-producing ROS node feeds the guard."""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('pai_inspection_ros')
    params = os.path.join(share, 'config', 'nav2.yaml')
    bt = os.path.join(share, 'config', 'bounded_nav.xml')
    stations = LaunchConfiguration('stations_file')
    mode = LaunchConfiguration('planner_mode')
    nodes = [
        DeclareLaunchArgument('stations_file', default_value='/opt/pai/simulation/config/stations.yaml'),
        DeclareLaunchArgument('planner_mode', default_value=os.getenv('PAI_PLANNER_MODE', 'vertex')),
        DeclareLaunchArgument('max_steps', default_value='8'),
    ]
    navigation = (
        ('nav2_map_server', 'map_server'), ('nav2_amcl', 'amcl'),
        ('nav2_controller', 'controller_server'), ('nav2_planner', 'planner_server'),
        ('nav2_behaviors', 'behavior_server'), ('nav2_bt_navigator', 'bt_navigator'),
    )
    for package, executable in navigation:
        overrides = {'default_nav_to_pose_bt_xml': bt} if executable == 'bt_navigator' else {}
        nodes.append(Node(package=package, executable=executable, name=executable,
                          output='screen', parameters=[params, overrides],
                          remappings=[('cmd_vel', '/nav_cmd_vel')]))
    nodes.append(Node(package='nav2_lifecycle_manager', executable='lifecycle_manager',
                      name='lifecycle_manager_navigation', output='screen',
                      parameters=[{'use_sim_time': True, 'autostart': True,
                                   'node_names': [name for _, name in navigation]}]))
    nodes.extend([
        Node(package='pai_inspection_ros', executable='safety_guard', output='screen',
             parameters=[{'use_sim_time': True}]),
        Node(package='pai_inspection_ros', executable='instruction_executor', output='screen',
             parameters=[{'use_sim_time': True, 'stations_file': stations,
                          'planner_mode': mode, 'max_steps': LaunchConfiguration('max_steps')}]),
    ])
    return LaunchDescription(nodes)
