"""
vision_pick_place.launch.py
============================
사용법:
  ros2 launch jetcobot_vision_v2 vision_pick_place.launch.py role:=front_jet
  ros2 launch jetcobot_vision_v2 vision_pick_place.launch.py role:=ware_jet
"""

import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    pkg = get_package_share_directory('jetcobot_vision_v2')

    role_arg = DeclareLaunchArgument(
        'role',
        default_value='front_jet',
        description='로봇 역할: front_jet | ware_jet',
    )
    role = LaunchConfiguration('role')

    node = Node(
        package='jetcobot_vision_v2',
        executable='vision_pick_place_node',
        name='vision_pick_place_node',
        parameters=[{
            'role': role,
        }],
        output='screen',
    )

    return LaunchDescription([role_arg, node])
