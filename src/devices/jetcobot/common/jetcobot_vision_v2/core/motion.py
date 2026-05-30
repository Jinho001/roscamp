"""
core/motion.py
==============
MyCobot280 Pick/Place 모션 제어 (ROS2 의존성 없음).

로직 출처: jetcobot_vision/vision_pick_place_node.py
  - _init_tcp_settings, _get_flange_coords, _do_pick, _do_place, _wait_moving
"""

import math
import time

try:
    from pymycobot.mycobot280 import MyCobot280 as _MC280
    _MC_OK = True
except ImportError:
    _MC280 = None
    _MC_OK = False

_PICK_SPEED  = 30
_PLACE_SPEED = 25
_MAX_MOVE_WAIT = 30.0


def _normalize_angle(angle: float) -> float:
    """각도를 0 ~ -180 범위로 정규화 (그리퍼 회전 방향 통일)."""
    while angle > 180.0:
        angle -= 360.0
    while angle < -180.0:
        angle += 360.0
    if angle > 0.0:
        angle -= 180.0
    return angle


class MotionController:
    """
    MyCobot280 모션 제어기.

    Args:
        port      : 시리얼 포트 (예: "/dev/ttyJETCOBOT")
        baud      : 보드레이트 (예: 1000000)
        tcp_offset: [x, y, z] mm — flange→TCP 오프셋.
                    하드웨어 TCP reference로 주입되며,
                    get_flange_coords()에서 set_end_type 전환에 사용.
    """

    def __init__(self, port: str, baud: int, tcp_offset: list):
        self._tcp_offset = tcp_offset
        self._mc = None

        if not _MC_OK:
            return

        try:
            self._mc = _MC280(port, baud)
            time.sleep(0.5)
            self._init_tcp()
            self._mc.send_angles([0, 0, 0, 0, 0, 0], _PICK_SPEED)
            self._wait_moving()
        except Exception as exc:
            print(f"[MotionController] MyCobot 연결 실패: {exc}")
            self._mc = None

    def _init_tcp(self) -> None:
        """하드웨어 TCP reference 주입 + 툴 좌표계 활성화."""
        if self._mc is None:
            return
        ox, oy, oz = self._tcp_offset
        if (ox, oy, oz) == (0.0, 0.0, 0.0):
            return
        try:
            if hasattr(self._mc, 'set_tool_reference'):
                self._mc.set_tool_reference([ox, oy, oz, 0.0, 0.0, 0.0])
                time.sleep(0.1)
                self._mc.set_end_type(1)
                time.sleep(0.1)
        except Exception as exc:
            print(f"[MotionController] TCP 설정 실패: {exc}")

    def get_flange_coords(self) -> list | None:
        """
        현재 flange 6DoF 좌표 반환.
        set_end_type(0) → get_coords() → set_end_type(1) 순서로 읽어
        Hand-Eye 캘리브(flange 기준) 결과와 일관성 유지.
        """
        if self._mc is None:
            return None
        try:
            self._mc.set_end_type(0)
            time.sleep(0.05)
            coords = self._mc.get_coords()
            self._mc.set_end_type(1)
            time.sleep(0.05)
            return coords if coords and len(coords) == 6 else None
        except Exception as exc:
            print(f"[MotionController] get_flange_coords 실패: {exc}")
            try:
                self._mc.set_end_type(1)
            except Exception:
                pass
            return None

    def move_to(self, coords_6dof: list, speed: int = _PICK_SPEED) -> bool:
        """6DoF 좌표로 이동 후 완료 대기. 성공 시 True."""
        if self._mc is None:
            return False
        try:
            self._mc.send_coords(coords_6dof, speed)
            return self._wait_moving()
        except Exception as exc:
            print(f"[MotionController] move_to 실패: {exc}")
            return False

    def pick(self, x_mm: float, y_mm: float, z_mm: float,
             yaw_deg: float, profile: dict) -> bool:
        """
        Pick 동작: Approach → Target → Grasp → Retreat.

        profile 키:
            grasp_roll, grasp_pitch, grasp_yaw_offset,
            approach_z_offset_mm, pick_offset_mm (무시 — 호출 전 적용됨)
        """
        if self._mc is None:
            return False

        roll       = profile.get("grasp_roll", -178.81)
        pitch      = profile.get("grasp_pitch", 0.94)
        yaw_off    = profile.get("grasp_yaw_offset", 0.0)
        z_offset   = profile.get("approach_z_offset_mm", 20.0)
        rz         = _normalize_angle(yaw_deg + yaw_off)

        approach = [x_mm, y_mm, z_mm + z_offset, roll, pitch, rz]
        target   = [x_mm, y_mm, z_mm,             roll, pitch, rz]
        retreat  = [x_mm, y_mm, z_mm + z_offset,  roll, pitch, rz]

        try:
            self._mc.set_gripper_value(100, 30)  # 시작 전 그리퍼 열기
            time.sleep(0.5)

            print(f"[MotionController] Approach: {approach}")
            self._mc.send_coords(approach, _PICK_SPEED)
            if not self._wait_moving():
                return False

            print(f"[MotionController] Target: {target}")
            self._mc.send_coords(target, _PICK_SPEED)
            if not self._wait_moving():
                return False

            self._mc.set_gripper_value(0, 30)   # 닫기
            time.sleep(1.0)
            
            print(f"[MotionController] Retreat: {retreat}")
            self._mc.send_coords(retreat, _PICK_SPEED)
            return self._wait_moving()

        except Exception as exc:
            print(f"[MotionController] pick 실패: {exc}")
            return False

    def place(self, x_mm: float, y_mm: float, z_mm: float,
              yaw_deg: float, profile: dict) -> bool:
        """
        Place 동작: Approach → Release → Retreat.
        """
        if self._mc is None:
            return False

        roll     = profile.get("grasp_roll", -178.81)
        pitch    = profile.get("grasp_pitch", 0.94)
        yaw_off  = profile.get("grasp_yaw_offset", 0.0)
        z_offset = profile.get("approach_z_offset_mm", 20.0)
        rz       = _normalize_angle(yaw_deg + yaw_off)

        approach = [x_mm, y_mm, z_mm + z_offset,        roll, pitch, rz]
        retreat  = [x_mm, y_mm, z_mm + z_offset + 50.0, roll, pitch, rz]

        try:
            self._mc.send_coords(approach, _PLACE_SPEED)
            if not self._wait_moving():
                return False

            self._mc.set_gripper_value(100, 30)  # 열기
            time.sleep(1.0)

            self._mc.send_coords(retreat, _PLACE_SPEED)
            return self._wait_moving()

        except Exception as exc:
            print(f"[MotionController] place 실패: {exc}")
            return False

    def open_gripper(self) -> None:
        if self._mc:
            self._mc.set_gripper_value(100, 30)

    def close_gripper(self) -> None:
        if self._mc:
            self._mc.set_gripper_value(0, 30)

    def go_home(self) -> bool:
        """[0,0,0,0,0,0] 홈 자세로 복귀."""
        if self._mc is None:
            return False
        try:
            self._mc.send_angles([0, 0, 0, 0, 0, 0], _PICK_SPEED)
            return self._wait_moving()
        except Exception as exc:
            print(f"[MotionController] go_home 실패: {exc}")
            return False

    def wait_moving(self, timeout: float = _MAX_MOVE_WAIT) -> bool:
        return self._wait_moving(timeout)

    def _wait_moving(self, timeout: float = _MAX_MOVE_WAIT) -> bool:
        if self._mc is None:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._mc.is_moving():
                return True
            time.sleep(0.1)
        print(f"[MotionController] 모션 대기 초과 ({timeout}초)")
        return False

    @property
    def connected(self) -> bool:
        return self._mc is not None
