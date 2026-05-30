#!/usr/bin/env python3
"""
VisionPickPlaceNode — Unified Pick & Place Action Server
===========================================================
pick과 place를 하나의 노드에서 관리. Serial port 충돌 방지.

Roles:
  1. /vision_pick action server
  2. /vision_place action server
  3. MyCobot280 serial 제어 (단일 instance)

Action:
  /vision_pick  [VisionPick]
  /vision_place [VisionPlace]
"""

import json
import os
import time
import threading
from typing import Optional

import requests
import yaml
from ament_index_python.packages import get_package_share_directory
import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import SetBool
from rcl_interfaces.srv import SetParameters
from rcl_interfaces.msg import Parameter, ParameterValue, ParameterType

from jetcobot_vision_msgs.action import VisionPick, VisionPlace
from jetcobot_vision_msgs.msg import PickPoint
from jetcobot_vision_msgs.srv import UpdatePose

try:
    from pymycobot import MyCobot280 as _MC280
    _MC_OK = True
except ImportError:
    _MC_OK = False

_PICK_SPEED    = 30
_PLACE_SPEED   = 30
_GRIPPER_OPEN  = 100
_GRIPPER_CLOSE = 0
_MAX_MOVE_WAIT = 30.0


def _normalize_angle(angle: float) -> float:
    """각도를 -180 ~ 0 범위로 정규화 (그리퍼 회전 방향 통일)."""
    while angle > 180.0:
        angle -= 360.0
    while angle < -180.0:
        angle += 360.0
    
    # 0 ~ 180도 사이인 경우 -180을 적용하여 0 ~ -180 범위로 맞춤
    if angle > 0.0:
        angle -= 180.0
        
    return angle


def _load_profiles() -> dict:
    try:
        pkg_dir = get_package_share_directory("jetcobot_vision")
        path = os.path.join(pkg_dir, "config", "pick_place_profiles.yaml")
    except Exception:
        path = os.path.join(os.path.dirname(__file__), "..", "config", "pick_place_profiles.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


_PROFILES: dict[str, dict] = _load_profiles()


class VisionPickPlaceNode(Node):

    def __init__(self) -> None:
        super().__init__("vision_pick_place_node")

        self._cb_group = ReentrantCallbackGroup()

        # MyCobot 초기화 (단일 instance)
        self.declare_parameter("port",                "/dev/ttyJETCOBOT")
        self.declare_parameter("baud",                1_000_000)
        self.declare_parameter("grasp_roll",          -178.81)
        self.declare_parameter("grasp_pitch",           0.94)
        self.declare_parameter("grasp_yaw_offset",      0.0)
        self.declare_parameter("tcp_offset",           [0.0, 20.0, 100.0])
        self.declare_parameter("pick_z_offset_mm",     20.0)
        self.declare_parameter("detect_timeout_sec",   10.0)
        self.declare_parameter("coord_topic",          "/coord_transform_node/pick_point")
        self.declare_parameter("coord_enable_service", "/coord_transform_node/enable")
        self.declare_parameter("cv_detect_server_url", "http://192.168.1.121:8081")

        port                      = self.get_parameter("port").value
        baud                      = self.get_parameter("baud").value
        self._grasp_roll          = self.get_parameter("grasp_roll").value
        self._grasp_pitch         = self.get_parameter("grasp_pitch").value
        self._grasp_yaw_off       = self.get_parameter("grasp_yaw_offset").value
        self._pick_z_off_mm       = self.get_parameter("pick_z_offset_mm").value
        self._tcp_offset          = list(self.get_parameter("tcp_offset").value)
        self._detect_timeout      = self.get_parameter("detect_timeout_sec").value
        coord_topic               = self.get_parameter("coord_topic").value
        coord_enable_service      = self.get_parameter("coord_enable_service").value
        self._cv_server_url       = self.get_parameter("cv_detect_server_url").value

        self._mc = None
        self._hardware_tcp_active = False
        if _MC_OK:
            try:
                self.get_logger().info(f"MyCobot 연결 중: {port} @ {baud}")
                self._mc = _MC280(port, baud)
                time.sleep(0.5)
                self.get_logger().info("MyCobot 연결 완료")

                # 하드웨어 TCP 초기 설정 적용
                self._init_tcp_settings()

                self.get_logger().info("초기 자세로 이동: [0,0,0,0,0,0]")
                self._mc.send_angles([0, 0, 0, 0, 0, 0], _PICK_SPEED)
                self._wait_moving()
                self._mc.set_gripper_value(_GRIPPER_OPEN, 30)
            except Exception as exc:
                self.get_logger().error(f"MyCobot 연결 실패: {exc}")
                self._mc = None

        # 동시 실행 방지 (한 번에 pick 또는 place 중 하나만)
        self._action_lock = threading.Lock()

        # PickPoint 구독 (pick/place 공용)
        self._pick_points: list[PickPoint] = []
        self._pick_lock = threading.Lock()

        self.create_subscription(
            PickPoint, coord_topic, self._on_pick_point, 10,
            callback_group=self._cb_group,
        )

        # Coord Transform 서비스 클라이언트
        self._coord_enable_client = self.create_client(
            SetBool, coord_enable_service, callback_group=self._cb_group
        )
        self._set_params_client = self.create_client(
            SetParameters, "/coord_transform_node/set_parameters", callback_group=self._cb_group
        )
        self._update_pose_client = self.create_client(
            UpdatePose, "/coord_transform_node/update_pose", callback_group=self._cb_group
        )

        # Watcher 제어 서비스 클라이언트
        self._set_watch_client = self.create_client(
            SetBool, "/retrieval_watcher_node/set_watch", callback_group=self._cb_group
        )

        # Action Servers
        self._pick_server = ActionServer(
            self, VisionPick, "/vision_pick",
            execute_callback=self._execute_pick,
            goal_callback=self._goal_cb,
            cancel_callback=self._cancel_cb,
            callback_group=self._cb_group,
        )
        self._place_server = ActionServer(
            self, VisionPlace, "/vision_place",
            execute_callback=self._execute_place,
            goal_callback=self._goal_cb,
            cancel_callback=self._cancel_cb,
            callback_group=self._cb_group,
        )

        # FMS 토픽 인터페이스 (roslibpy → rosbridge 경유)
        self._pick_result_pub  = self.create_publisher(String, "/jetcobot/result/pick",  10)
        self._place_result_pub = self.create_publisher(String, "/jetcobot/result/place", 10)
        self._reset_result_pub = self.create_publisher(String, "/jetcobot/result/reset", 10)
        self.create_subscription(String, "/jetcobot/cmd/pick",
                                 self._on_cmd_pick,  10, callback_group=self._cb_group)
        self.create_subscription(String, "/jetcobot/cmd/place",
                                 self._on_cmd_place, 10, callback_group=self._cb_group)
        self.create_subscription(String, "/jetcobot/cmd/reset",
                                 self._on_cmd_reset, 10, callback_group=self._cb_group)

        self.get_logger().info("VisionPickPlaceNode 시작 완료")

    def _on_pick_point(self, msg: PickPoint) -> None:
        with self._pick_lock:
            self._pick_points.append(msg)

    def _goal_cb(self, goal_request) -> GoalResponse:
        loc = goal_request.location
        self.get_logger().info(f"Goal 수락: location={loc}")
        return GoalResponse.ACCEPT

    def _cancel_cb(self, _) -> CancelResponse:
        self.get_logger().info("취소 요청 수락")
        return CancelResponse.ACCEPT

    # ── FMS 토픽 인터페이스 ───────────────────────────────────────────────────

    def _on_cmd_pick(self, msg: String) -> None:
        """FMS가 /jetcobot/cmd/pick 토픽으로 Pick 명령 전달 시 처리."""
        try:
            cmd = json.loads(msg.data)
        except Exception as e:
            self.get_logger().warn(f"[cmd/pick] JSON 파싱 실패: {e}")
            return

        location  = cmd.get("location", "")
        box_index = cmd.get("box_index", -1)
        self.get_logger().info(f"[cmd/pick] 수신: location={location} box_index={box_index}")

        threading.Thread(
            target=self._run_pick_and_publish,
            args=(location, box_index),
            daemon=True,
        ).start()

    def _on_cmd_place(self, msg: String) -> None:
        """FMS가 /jetcobot/cmd/place 토픽으로 Place 명령 전달 시 처리."""
        try:
            cmd = json.loads(msg.data)
        except Exception as e:
            self.get_logger().warn(f"[cmd/place] JSON 파싱 실패: {e}")
            return

        location  = cmd.get("location", "")
        box_index = cmd.get("box_index", -1)
        self.get_logger().info(f"[cmd/place] 수신: location={location} box_index={box_index}")

        threading.Thread(
            target=self._run_place_and_publish,
            args=(location, box_index),
            daemon=True,
        ).start()

    def _on_cmd_reset(self, msg: String) -> None:
        """FMS가 /jetcobot/cmd/reset 토픽으로 리셋 명령 전달 시 처리."""
        self.get_logger().info("[cmd/reset] 수신")
        threading.Thread(target=self._run_reset_and_publish, daemon=True).start()

    def _run_reset_and_publish(self) -> None:
        ok = self._run_reset()
        self._reset_result_pub.publish(String(data=json.dumps({"success": ok})))
        self.get_logger().info(f"[result/reset] 발행: success={ok}")

    def _run_reset(self) -> bool:
        if self._mc is None:
            return False
        try:
            self._mc.send_angles([0, 0, 0, 0, 0, 0], _PICK_SPEED)
            if not self._wait_moving():
                return False
            self._mc.set_gripper_value(_GRIPPER_OPEN, 30)
            return True
        except Exception as e:
            self.get_logger().error(f"reset 실패: {e}")
            return False

    def _run_pick_and_publish(self, location: str, box_index: int) -> None:
        """토픽 명령으로 Pick 실행 후 결과를 /jetcobot/result/pick에 발행."""
        result = self._run_pick(location, box_index)
        self._pick_result_pub.publish(String(data=json.dumps(result)))
        self.get_logger().info(f"[result/pick] 발행: {result}")

    def _run_place_and_publish(self, location: str, box_index: int) -> None:
        """토픽 명령으로 Place 실행 후 결과를 /jetcobot/result/place에 발행."""
        result = self._run_place(location, box_index)
        self._place_result_pub.publish(String(data=json.dumps(result)))
        self.get_logger().info(f"[result/place] 발행: {result}")

    def _run_pick(self, location: str, box_index: int = -1) -> dict:
        """Pick 핵심 로직. Action/토픽 양쪽에서 공통 사용."""
        if not self._action_lock.acquire(blocking=False):
            return {"success": False, "message": "다른 동작(Pick/Place) 진행 중"}

        try:
            profile = self._load_profile(location)
            if profile is None:
                return {"success": False, "message": f"알 수 없는 location: '{location}'"}

            self._send_cv_config(profile)
            self._set_coord_transform(False)

            if not self._move_to_observe(location, profile):
                self._set_coord_transform(False)
                return {"success": False, "message": f"observe_pose 이동 실패: {location}"}

            if self._mc is not None:
                actual = self._get_flange_coords()
                if actual and len(actual) == 6:
                    self._update_coord_transform_pose(actual)

            self._set_coord_transform(True)
            pick_pt = self._wait_for_point(box_index)
            self._set_coord_transform(False)

            if pick_pt is None:
                return {"success": False, "message": "검출 타임아웃"}

            offset = profile.get("pick_offset_mm", [0.0, 0.0, 0.0])
            x_mm = pick_pt.x * 1000.0 + offset[0]
            y_mm = pick_pt.y * 1000.0 + offset[1]
            z_mm = pick_pt.z * 1000.0 + offset[2]

            if not self._do_pick(x_mm, y_mm, z_mm, pick_pt.yaw_deg, profile):
                return {"success": False, "message": "픽업 동작 실패"}

            result = {
                "success": True,
                "message": f"location={location} 픽업 완료",
                "pick_point_base": {"x": pick_pt.x, "y": pick_pt.y, "z": pick_pt.z},
            }
            return result
        finally:
            self._action_lock.release()
            # Pick 완료 후 감시 자세 복귀 (성공/실패 무관)
            threading.Thread(target=self._return_to_watch, daemon=True).start()

    def _run_place(self, location: str, box_index: int = -1) -> dict:
        """Place 핵심 로직. Action/토픽 양쪽에서 공통 사용."""
        if not self._action_lock.acquire(blocking=False):
            return {"success": False, "message": "다른 동작(Pick/Place) 진행 중"}

        try:
            profile = self._load_profile(location)
            if profile is None:
                return {"success": False, "message": f"알 수 없는 location: '{location}'"}

            self._send_cv_config(profile)
            self._set_coord_transform(False)

            if not self._move_to_observe(location, profile):
                self._set_coord_transform(False)
                return {"success": False, "message": f"observe_pose 이동 실패: {location}"}

            if self._mc is not None:
                actual = self._get_flange_coords()
                if actual and len(actual) == 6:
                    self._update_coord_transform_pose(actual)

            self._set_coord_transform(True)
            place_pt = self._wait_for_point(box_index)
            self._set_coord_transform(False)

            if place_pt is None:
                return {"success": False, "message": "검출 타임아웃"}

            offset = profile.get("pick_offset_mm", [0.0, 0.0, 0.0])
            x_mm = place_pt.x * 1000.0 + offset[0]
            y_mm = place_pt.y * 1000.0 + offset[1]
            z_mm = place_pt.z * 1000.0 + offset[2]

            if not self._do_place(x_mm, y_mm, z_mm, place_pt.yaw_deg, profile):
                return {"success": False, "message": "적재 동작 실패"}

            return {
                "success": True,
                "message": f"location={location} 적재 완료",
            }
        finally:
            self._action_lock.release()
            # Place 완료 후 감시 자세 복귀 (성공/실패 무관)
            threading.Thread(target=self._return_to_watch, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────

    async def _execute_pick(self, goal_handle) -> VisionPick.Result:
        """Pick Action 실행 — _run_pick() 위임."""
        location  = goal_handle.request.location
        box_index = goal_handle.request.box_index if hasattr(goal_handle.request, "box_index") else -1
        fb = VisionPick.Feedback()
        res = VisionPick.Result()

        self._fb(goal_handle, fb, "moving", 0.10)
        result = self._run_pick(location, box_index)

        self._fb(goal_handle, fb, "done", 1.00)
        res.success = result["success"]
        res.message = result["message"]
        if result["success"] and "pick_point_base" in result:
            pt = result["pick_point_base"]
            res.pick_point_base.x = pt["x"]
            res.pick_point_base.y = pt["y"]
            res.pick_point_base.z = pt["z"]

        if res.success:
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return res

    async def _execute_place(self, goal_handle) -> VisionPlace.Result:
        """Place Action 실행 — _run_place() 위임."""
        location  = goal_handle.request.location
        box_index = goal_handle.request.box_index if hasattr(goal_handle.request, "box_index") else -1
        fb = VisionPlace.Feedback()
        res = VisionPlace.Result()

        self._fb(goal_handle, fb, "moving", 0.10)
        result = self._run_place(location, box_index)

        self._fb(goal_handle, fb, "done", 1.00)
        res.success = result["success"]
        res.message = result["message"]

        if res.success:
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return res

    # ── 헬퍼 메서드 ──────────────────────────────────────────────────────────

    def _load_profile(self, location: str) -> Optional[dict]:
        return _PROFILES.get(location)

    def _fb(self, goal_handle, fb, phase: str, progress: float) -> None:
        fb.phase = phase
        fb.progress = progress
        goal_handle.publish_feedback(fb)

    def _abort(self, goal_handle, res: VisionPick.Result, msg: str) -> VisionPick.Result:
        self.get_logger().error(msg)
        res.success = False
        res.message = msg
        goal_handle.abort()
        return res

    def _set_coord_transform(self, enable: bool) -> None:
        if not self._coord_enable_client.service_is_ready():
            self.get_logger().warn("coord_enable 서비스 미준비 — 스킵")
            return
        req = SetBool.Request()
        req.data = enable
        self._coord_enable_client.call(req)

    def _send_cv_config(self, profile: dict) -> None:
        if "z_surface_mm" in profile and profile["z_surface_mm"] is not None:
            z = float(profile["z_surface_mm"])
            try:
                if self._set_params_client.service_is_ready():
                    req = SetParameters.Request()
                    p = Parameter()
                    p.name = "z_surface_mm"
                    p.value = ParameterValue(type=ParameterType.PARAMETER_DOUBLE, double_value=z)
                    req.parameters = [p]
                    self._set_params_client.call(req)
            except Exception as exc:
                self.get_logger().warn(f"z_surface_mm 업데이트 실패: {exc}")

        cfg = {}
        for key in ("hsv_lower", "hsv_upper", "min_area", "max_area",
                    "min_w", "max_w", "min_h", "max_h", "morph_k"):
            if key in profile and profile[key] is not None:
                cfg[key] = profile[key]
        if cfg:
            try:
                url = self._cv_server_url.rstrip("/") + "/config"
                requests.post(url, json=cfg, timeout=2.0)
            except Exception as exc:
                self.get_logger().warn(f"cv_detect_server 전송 실패: {exc}")

    def _return_to_watch(self) -> None:
        """Pick/Place 완료 후 회수존 감시 자세로 복귀.
        1. receiving_zone observe_pose로 이동
        2. cv_detect_server에 receiving_zone 파라미터 전송
        3. Watcher 활성화
        """
        profile = _PROFILES.get("receiving_zone")
        if profile is None:
            self.get_logger().warn("[watch] receiving_zone 프로파일 없음 — 복귀 생략")
            return

        self.get_logger().info("[watch] 감시 자세 복귀 시작")
        self._send_cv_config(profile)
        self._move_to_observe("receiving_zone", profile)

        # Watcher 활성화
        if self._set_watch_client.service_is_ready():
            try:
                req = SetBool.Request()
                req.data = True
                self._set_watch_client.call(req)
                self.get_logger().info("[watch] Watcher 활성화 완료")
            except Exception as exc:
                self.get_logger().warn(f"[watch] Watcher 활성화 실패: {exc}")
        else:
            self.get_logger().warn("[watch] set_watch 서비스 미준비 — Watcher 활성화 생략")

    def _init_tcp_settings(self) -> None:
        """MyCobot의 하드웨어 TCP reference를 주입하고 툴 좌표계를 활성화."""
        if self._mc is None:
            return
        try:
            ox, oy, oz = self._tcp_offset
            # 만약 tcp_offset이 유효하다면 하드웨어 TCP reference 적용
            if ox != 0.0 or oy != 0.0 or oz != 0.0:
                has_tcp = hasattr(self._mc, 'set_tool_reference') and hasattr(self._mc, 'set_end_type')
                if has_tcp:
                    self.get_logger().info(f"pymycobot 하드웨어 TCP 설정 적용 중: {[ox, oy, oz]}")
                    self._mc.set_tool_reference([ox, oy, oz, 0.0, 0.0, 0.0])
                    time.sleep(0.1)
                    self._mc.set_end_type(1) # 툴 좌표계 활성화
                    time.sleep(0.1)
                    self._hardware_tcp_active = True
                    self.get_logger().info(
                        f"TCP 설정 완료: tool_ref={self._mc.get_tool_reference()}, end_type={self._mc.get_end_type()}"
                    )
                else:
                    self.get_logger().warn("pymycobot 라이브러리에 TCP 설정 API가 존재하지 않음 (flange 모드로 동작)")
        except Exception as exc:
            self.get_logger().error(f"하드웨어 TCP 설정 중 오류 발생: {exc}")

    def _get_flange_coords(self) -> Optional[list]:
        """Hand-Eye 정밀도를 보장하기 위해, 일시적으로 flange 좌표계 모드로 변환하여 현재 좌표를 취득."""
        if self._mc is None:
            return None
        try:
            has_tcp = hasattr(self._mc, 'set_end_type') and hasattr(self._mc, 'get_end_type')
            if has_tcp and self._hardware_tcp_active:
                prev_end = self._mc.get_end_type()
                if prev_end != 0:
                    self._mc.set_end_type(0) # flange 모드로 일시 전환
                    time.sleep(0.05)
                coords = self._mc.get_coords()
                if prev_end != 0:
                    self._mc.set_end_type(prev_end) # 이전 모드 복원
                    time.sleep(0.05)
                return coords
            else:
                return self._mc.get_coords()
        except Exception as exc:
            self.get_logger().warn(f"flange 좌표 취득 실패: {exc}")
            return self._mc.get_coords()

    def _compute_motion_params(self, x_mm: float, y_mm: float, z_mm: float, yaw_deg: float, profile: Optional[dict] = None) -> tuple:
        """두 모션(_do_pick, _do_place)에서 공통으로 사용되는 자세 파라미터 계산 (DRY 원칙)."""
        roll = profile.get("grasp_roll", self._grasp_roll) if profile else self._grasp_roll
        pitch = profile.get("grasp_pitch", self._grasp_pitch) if profile else self._grasp_pitch
        yaw_offset = profile.get("grasp_yaw_offset", self._grasp_yaw_off) if profile else self._grasp_yaw_off
        z_offset = profile.get("pick_z_offset_mm", self._pick_z_off_mm) if profile else self._pick_z_off_mm

        rz = _normalize_angle(yaw_deg + yaw_offset)
        
        # 하드웨어 TCP가 활성화되어 있으면 소프트웨어 offset은 0으로 처리 (중복 적용 방지)
        if self._hardware_tcp_active:
            ox, oy, oz = 0.0, 0.0, 0.0
        else:
            ox, oy, oz = self._tcp_offset

        return roll, pitch, rz, z_offset, ox, oy, oz

    def _move_to_observe(self, location: str, profile: dict) -> bool:
        if self._mc is None:
            return False
        obs = profile.get("observe_pose")
        if not obs or len(obs) != 6:
            return False
        try:
            self._mc.send_coords(obs, _PICK_SPEED)
            return self._wait_moving()
        except Exception as exc:
            self.get_logger().error(f"observe_pose 이동 실패: {exc}")
            return False

    def _update_coord_transform_pose(self, actual: list) -> None:
        if not self._update_pose_client.service_is_ready():
            return
        try:
            req = UpdatePose.Request()
            req.coords = actual[:6]
            self._update_pose_client.call(req)
        except Exception as exc:
            self.get_logger().warn(f"pose 업데이트 실패: {exc}")

    def _wait_for_point(self, box_index: int = -1) -> Optional[PickPoint]:
        with self._pick_lock:
            self._pick_points.clear()
        deadline = time.monotonic() + self._detect_timeout
        while time.monotonic() < deadline:
            with self._pick_lock:
                if self._pick_points:
                    if box_index < 0:
                        selected = max(self._pick_points, key=lambda p: p.confidence)
                    else:
                        selected = next((p for p in self._pick_points if p.box_index == box_index), None)
                    if selected is not None:
                        self.get_logger().info(f"선택된 상자: index={selected.box_index} conf={selected.confidence:.2f}")
                        return selected
            time.sleep(0.05)
        self.get_logger().warn(f"검출 타임아웃 ({self._detect_timeout:.1f}s)")
        return None

    def _do_pick(self, x_mm: float, y_mm: float, z_mm: float, yaw_deg: float, profile: Optional[dict] = None) -> bool:
        if self._mc is None:
            return False

        roll, pitch, rz, z_offset, ox, oy, oz = self._compute_motion_params(x_mm, y_mm, z_mm, yaw_deg, profile)

        try:
            # Approach
            approach = [x_mm + ox, y_mm + oy, z_mm + oz + z_offset, roll, pitch, rz]
            self.get_logger().info(f"[Pick] Approach: {[round(v, 2) for v in approach]}")
            self._mc.send_coords(approach, _PICK_SPEED)
            if not self._wait_moving():
                return False

            # Touch target
            target = [x_mm + ox, y_mm + oy, z_mm + oz, roll, pitch, rz]
            self.get_logger().info(f"[Pick] Target: {[round(v, 2) for v in target]}")
            self._mc.send_coords(target, _PICK_SPEED)
            if not self._wait_moving():
                return False

            # Grasp
            self.get_logger().info("[Pick] Gripper Close")
            self._mc.set_gripper_value(_GRIPPER_CLOSE, 30)
            time.sleep(1.0)

            # Retreat
            retreat = [x_mm + ox, y_mm + oy, z_mm + oz + z_offset, roll, pitch, rz]
            self.get_logger().info(f"[Pick] Retreat: {[round(v, 2) for v in retreat]}")
            self._mc.send_coords(retreat, _PICK_SPEED)
            if not self._wait_moving():
                return False

            return True
        except Exception as exc:
            self.get_logger().error(f"pick 동작 실패: {exc}")
            return False

    def _do_place(self, x_mm: float, y_mm: float, z_mm: float, yaw_deg: float, profile: Optional[dict] = None) -> bool:
        if self._mc is None:
            return False

        roll, pitch, rz, z_offset, ox, oy, oz = self._compute_motion_params(x_mm, y_mm, z_mm, yaw_deg, profile)

        try:
            # Approach
            approach = [x_mm + ox, y_mm + oy, z_mm + oz + z_offset, roll, pitch, rz]
            self.get_logger().info(f"[Place] Approach: {[round(v, 2) for v in approach]}")
            self._mc.send_coords(approach, _PLACE_SPEED)
            if not self._wait_moving():
                return False

            # Release
            self.get_logger().info("[Place] Gripper Open")
            self._mc.set_gripper_value(_GRIPPER_OPEN, 30)
            time.sleep(1.0)

            # Retreat
            retreat = [x_mm + ox, y_mm + oy, z_mm + oz + z_offset + 50.0, roll, pitch, rz]
            self.get_logger().info(f"[Place] Retreat: {[round(v, 2) for v in retreat]}")
            self._mc.send_coords(retreat, _PLACE_SPEED)
            if not self._wait_moving():
                return False

            return True
        except Exception as exc:
            self.get_logger().error(f"place 동작 실패: {exc}")
            return False

    def _wait_moving(self) -> bool:
        if self._mc is None:
            return False
        deadline = time.monotonic() + _MAX_MOVE_WAIT
        while time.monotonic() < deadline:
            if not self._mc.is_moving():
                return True
            time.sleep(0.1)
        self.get_logger().error(f"모션 대기 초과 ({_MAX_MOVE_WAIT}초)")
        return False


def main(args=None) -> None:
    rclpy.init(args=args)
    node = VisionPickPlaceNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
