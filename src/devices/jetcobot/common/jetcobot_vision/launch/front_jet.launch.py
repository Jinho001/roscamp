"""
front_jet.launch.py
===================
FrontJet 전용 launch.

기동 노드:
  1. coord_transform_node   — Static TF + 역투영 → /coord_transform/pick_point
  2. vision_pick_place_node — Action Server /vision_pick + /vision_place
  3. retrieval_watcher_node — 회수존 8칸 감시 → FMS 알림 (FrontJet 전용)

  ※ FMS가 Pick/Place 시퀀스를 직접 제어하므로 coordinator 노드 불필요

사용법:
  ros2 launch jetcobot_vision front_jet.launch.py
  ros2 launch jetcobot_vision front_jet.launch.py params_file:=/path/to/custom.yaml
"""

import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    pkg_dir     = get_package_share_directory("jetcobot_vision")
    default_cfg = os.path.join(pkg_dir, "config", "vision_params.yaml")

    params_arg = DeclareLaunchArgument(
        "params_file",
        default_value=default_cfg,
        description="파라미터 YAML 파일 절대 경로",
    )
    params = LaunchConfiguration("params_file")

    return LaunchDescription([
        params_arg,
        Node(
            package="jetcobot_vision",
            executable="coord_transform_node",
            name="coord_transform_node",
            parameters=[params],
            output="screen",
            emulate_tty=True,
        ),
        Node(
            package="jetcobot_vision",
            executable="vision_pick_place_node",
            name="vision_pick_place_node",
            parameters=[params],
            output="screen",
            emulate_tty=True,
        ),
        Node(
            package="jetcobot_vision",
            executable="retrieval_watcher_node",
            name="retrieval_watcher_node",
            parameters=[params],
            output="screen",
            emulate_tty=True,
        ),
    ])
