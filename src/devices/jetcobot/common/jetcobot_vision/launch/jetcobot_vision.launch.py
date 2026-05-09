"""
jetcobot_vision.launch.py
=========================
FrontJet / WareJet 공통 launch.

role 인자로 역할을 지정한다:
  front_jet  — cv_detect_server 포트 8081, retrieval_watcher_node 포함
  ware_jet   — cv_detect_server 포트 8082, retrieval_watcher_node 없음

AI 서버(192.168.1.121) 실행:
  # FrontJet용
  python3 cv_detect_server.py --udp-port 5000 --port 8081
  # WareJet용
  python3 cv_detect_server.py --udp-port 5001 --port 8082

stream_sender 실행:
  # FrontJet Pi
  python3 stream_sender.py --host 192.168.1.121 --port 5000
  # WareJet Pi
  python3 stream_sender.py --host 192.168.1.121 --port 5001

사용법:
  ros2 launch jetcobot_vision jetcobot_vision.launch.py role:=front_jet
  ros2 launch jetcobot_vision jetcobot_vision.launch.py role:=ware_jet
"""

import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _generate_nodes(context, *args, **kwargs):
    pkg_dir = get_package_share_directory("jetcobot_vision")
    role    = LaunchConfiguration("role").perform(context)

    params_file = os.path.join(
        pkg_dir, "config", f"vision_params_{role}.yaml"
    )

    common_nodes = [
        Node(
            package="jetcobot_vision",
            executable="coord_transform_node",
            name="coord_transform_node",
            parameters=[params_file],
            output="screen",
            emulate_tty=True,
        ),
        Node(
            package="jetcobot_vision",
            executable="vision_pick_place_node",
            name="vision_pick_place_node",
            parameters=[params_file],
            output="screen",
            emulate_tty=True,
        ),
    ]

    if role == "front_jet":
        common_nodes.append(
            Node(
                package="jetcobot_vision",
                executable="retrieval_watcher_node",
                name="retrieval_watcher_node",
                parameters=[params_file],
                output="screen",
                emulate_tty=True,
            )
        )

    return common_nodes


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument(
            "role",
            default_value="front_jet",
            description="jetcobot 역할: front_jet | ware_jet",
        ),
        OpaqueFunction(function=_generate_nodes),
    ])
