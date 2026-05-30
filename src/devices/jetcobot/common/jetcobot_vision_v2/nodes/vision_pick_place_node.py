#!/usr/bin/env python3
"""
nodes/vision_pick_place_node.py
================================
ROS2 래핑 노드 — core 모듈(CoordTransformer, Detector, MotionController)을 조합.
비즈니스 로직은 core에 위임하고, 이 파일은 ROS2 인터페이스만 담당.

인터페이스:
  Action Server : /vision_pick  (VisionPick)
                  /vision_place (VisionPlace)
  토픽 Sub      : /jetcobot/cmd/pick  (std_msgs/String) — location 문자열
                  /jetcobot/cmd/place (std_msgs/String) — location 문자열
  토픽 Pub      : /jetcobot/result/pick  (std_msgs/String) — "success" | "fail"
                  /jetcobot/result/place (std_msgs/String) — "success" | "fail"
"""

import os
import sys
import yaml
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from std_msgs.msg import String

# core 모듈 import (패키지 루트 기준)
_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from core.coord_transform import CoordTransformer
from core.detector import Detector
from core.motion import MotionController

try:
    from jetcobot_vision_msgs.action import VisionPick, VisionPlace
    _ACTIONS_OK = True
except ImportError:
    _ACTIONS_OK = False


def _load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


class VisionPickPlaceNode(Node):

    def __init__(self):
        super().__init__('vision_pick_place_node')

        # 파라미터
        self.declare_parameter('config', '')
        self.declare_parameter('role', 'front_jet')

        config_path = self.get_parameter('config').value
        role = self.get_parameter('role').value

        if not config_path:
            pkg_config = _PKG_ROOT / 'config' / f'params_{role}.yaml'
            config_path = str(pkg_config)

        self.get_logger().info(f"config: {config_path}")
        cfg = _load_config(config_path)

        # core 모듈 초기화
        ct_cfg  = cfg['coord_transform']
        mot_cfg = cfg['motion']
        det_cfg = cfg['detector']

        self._transformer = CoordTransformer(
            handeye_matrix    = ct_cfg['handeye_matrix'],
            camera_intrinsics = ct_cfg['camera_intrinsics'],
        )
        self._detector = Detector(det_cfg['server_url'])
        self._detect_timeout = det_cfg.get('detect_timeout_sec', 10.0)

        self._motion = MotionController(
            port       = mot_cfg['port'],
            baud       = mot_cfg['baud'],
            tcp_offset = mot_cfg['tcp_offset'],
        )

        self._profiles = cfg.get('profiles', {})
        self._cb_group = ReentrantCallbackGroup()

        # 토픽 인터페이스
        self._pub_pick  = self.create_publisher(String, '/jetcobot/result/pick',  10)
        self._pub_place = self.create_publisher(String, '/jetcobot/result/place', 10)
        self.create_subscription(String, '/jetcobot/cmd/pick',
                                 self._on_cmd_pick,  10,
                                 callback_group=self._cb_group)
        self.create_subscription(String, '/jetcobot/cmd/place',
                                 self._on_cmd_place, 10,
                                 callback_group=self._cb_group)

        # Action Server (jetcobot_vision_msgs 있을 때만)
        if _ACTIONS_OK:
            self._pick_action = ActionServer(
                self, VisionPick, '/vision_pick',
                execute_callback    = self._execute_pick,
                goal_callback       = lambda _: GoalResponse.ACCEPT,
                cancel_callback     = lambda _: CancelResponse.ACCEPT,
                callback_group      = self._cb_group,
            )
            self._place_action = ActionServer(
                self, VisionPlace, '/vision_place',
                execute_callback    = self._execute_place,
                goal_callback       = lambda _: GoalResponse.ACCEPT,
                cancel_callback     = lambda _: CancelResponse.ACCEPT,
                callback_group      = self._cb_group,
            )

        self.get_logger().info("VisionPickPlaceNode 준비 완료")

    # ── 토픽 핸들러 ─────────────────────────────────────────────────────────

    def _on_cmd_pick(self, msg: String) -> None:
        location = msg.data.strip()
        success = self._run_pick(location)
        result = String(data="success" if success else "fail")
        self._pub_pick.publish(result)

    def _on_cmd_place(self, msg: String) -> None:
        location = msg.data.strip()
        success = self._run_place(location)
        result = String(data="success" if success else "fail")
        self._pub_place.publish(result)

    # ── Action 핸들러 ────────────────────────────────────────────────────────

    async def _execute_pick(self, goal_handle):
        location   = goal_handle.request.location
        box_index  = goal_handle.request.box_index

        feedback = VisionPick.Feedback()
        feedback.phase = "picking"
        goal_handle.publish_feedback(feedback)

        success = self._run_pick(location, box_index)

        result = VisionPick.Result()
        result.success = success
        result.message = "ok" if success else "pick failed"
        if success:
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return result

    async def _execute_place(self, goal_handle):
        location  = goal_handle.request.location
        box_index = goal_handle.request.box_index

        feedback = VisionPlace.Feedback()
        feedback.phase = "placing"
        goal_handle.publish_feedback(feedback)

        success = self._run_place(location, box_index)

        result = VisionPlace.Result()
        result.success = success
        result.message = "ok" if success else "place failed"
        if success:
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return result

    # ── 핵심 Pick/Place 로직 ─────────────────────────────────────────────────

    def _run_pick(self, location: str, box_index: int = -1) -> bool:
        profile = self._profiles.get(location)
        if profile is None:
            self.get_logger().error(f"알 수 없는 location: {location}")
            return False

        observe_pose = profile['observe_pose']
        z_surface    = profile['z_surface_mm']
        pick_offset  = profile.get('pick_offset_mm', [0.0, 0.0, 0.0])

        # 1. observe_pose로 이동 (TCP 좌표)
        self.get_logger().info(f"[Pick:{location}] observe_pose로 이동")
        if not self._motion.move_to(observe_pose):
            self.get_logger().error("observe_pose 이동 실패")
            return False

        # 2. flange 좌표 읽기 → CoordTransformer 갱신
        flange = self._motion.get_flange_coords()
        if flange is None:
            self.get_logger().error("flange 좌표 읽기 실패")
            return False
        self._transformer.update_pose(flange)
        self.get_logger().info(f"[Pick:{location}] T_base2cam 갱신 (flange={[round(v,1) for v in flange]})")

        # 3. OBB 검출 대기
        self.get_logger().info(f"[Pick:{location}] 검출 대기 중...")
        obb = self._detector.wait_for_detection(self._detect_timeout)
        if obb is None:
            self.get_logger().error(f"검출 타임아웃 ({self._detect_timeout}s)")
            return False

        # 4. 픽셀 → base_link 3D 좌표 변환
        pt = self._transformer.pixel_to_base(obb['cx'], obb['cy'], z_surface)
        if pt is None:
            self.get_logger().error("픽셀→base_link 변환 실패")
            return False
        x_mm, y_mm, z_mm = pt

        # 5. yaw 변환
        yaw = self._transformer.theta_to_yaw(obb['theta'])
        if yaw is None:
            self.get_logger().error("yaw 변환 실패")
            return False

        # 6. pick_offset 적용
        x_mm += pick_offset[0]
        y_mm += pick_offset[1]
        z_mm += pick_offset[2]

        self.get_logger().info(
            f"[Pick:{location}] target=({x_mm:.1f}, {y_mm:.1f}, {z_mm:.1f}) yaw={yaw:.1f}°"
        )

        # 7. Pick 실행
        return self._motion.pick(x_mm, y_mm, z_mm, yaw, profile)

    def _run_place(self, location: str, box_index: int = -1) -> bool:
        profile = self._profiles.get(location)
        if profile is None:
            self.get_logger().error(f"알 수 없는 location: {location}")
            return False

        observe_pose = profile['observe_pose']
        z_surface    = profile['z_surface_mm']
        pick_offset  = profile.get('pick_offset_mm', [0.0, 0.0, 0.0])

        # 1. observe_pose로 이동
        self.get_logger().info(f"[Place:{location}] observe_pose로 이동")
        if not self._motion.move_to(observe_pose):
            self.get_logger().error("observe_pose 이동 실패")
            return False

        # 2. flange 좌표 읽기 → CoordTransformer 갱신
        flange = self._motion.get_flange_coords()
        if flange is None:
            self.get_logger().error("flange 좌표 읽기 실패")
            return False
        self._transformer.update_pose(flange)

        # 3. OBB 검출 대기
        self.get_logger().info(f"[Place:{location}] 검출 대기 중...")
        obb = self._detector.wait_for_detection(self._detect_timeout)
        if obb is None:
            self.get_logger().error(f"검출 타임아웃 ({self._detect_timeout}s)")
            return False

        # 4~5. 좌표/yaw 변환
        pt = self._transformer.pixel_to_base(obb['cx'], obb['cy'], z_surface)
        if pt is None:
            return False
        x_mm, y_mm, z_mm = pt
        yaw = self._transformer.theta_to_yaw(obb['theta'])
        if yaw is None:
            return False

        x_mm += pick_offset[0]
        y_mm += pick_offset[1]
        z_mm += pick_offset[2]

        self.get_logger().info(
            f"[Place:{location}] target=({x_mm:.1f}, {y_mm:.1f}, {z_mm:.1f}) yaw={yaw:.1f}°"
        )

        # 6. Place 실행
        return self._motion.place(x_mm, y_mm, z_mm, yaw, profile)


def main(args=None):
    rclpy.init(args=args)
    node = VisionPickPlaceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
