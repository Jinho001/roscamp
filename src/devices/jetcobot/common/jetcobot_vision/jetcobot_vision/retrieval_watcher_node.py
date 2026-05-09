#!/usr/bin/env python3
"""
RetrievalWatcherNode
====================
역할
  회수존 8칸(4×2)을 감시하다가 특정 슬롯에 상자가 일정 시간 이상
  연속 감지되면 FMS에 회수 요청(POST /retrieval/start?slot_id=N)을 보낸다.

슬롯 번호 (4×2 구성):
  [ 4 ][ 5 ][ 6 ][ 7 ]   ← 상단열 (cy ≤ cy_mid)
  [ 0 ][ 1 ][ 2 ][ 3 ]   ← 하단열 (cy > cy_mid)

슬롯 상태:
  IDLE      → 감지 없음
  DETECTING → 감지 중 (confirm_secs 미만)
  WAITING   → 회수 요청 완료, 외부 리셋 대기

파라미터 (vision_params.yaml):
  obb_topic             str    구독할 ObbBoxArray 토픽
  confirm_secs          float  연속 감지 확정 시간 (초)
  slot_x_start          float  회수존 좌측 끝 cx (px)
  slot_x_end            float  회수존 우측 끝 cx (px)
  slot_x_boundaries     float[] 열 경계 cx 리스트 (3개)
  slot_cy_mid           float  상단/하단 구분선 cy (px)
  fms_url               str    FMS 서버 주소

서비스:
  ~/reset_slot  [std_srvs/SetBool]  FMS 회수 완료 후 WAITING → IDLE 전체 리셋
  ~/set_watch   [std_srvs/SetBool]  감시 활성화/비활성화 (true=감시, false=중지)
"""

import threading
import time

import requests
import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy, QoSDurabilityPolicy
from std_srvs.srv import SetBool

from jetcobot_vision_msgs.msg import ObbBoxArray


class RetrievalWatcherNode(Node):

    def __init__(self) -> None:
        super().__init__("retrieval_watcher_node")

        _cb = ReentrantCallbackGroup()

        self.declare_parameter("obb_topic",          "/detect_bridge_node/obb_boxes")
        self.declare_parameter("confirm_secs",        3.0)
        self.declare_parameter("slot_x_start",        86.0)
        self.declare_parameter("slot_x_end",         562.0)
        self.declare_parameter("slot_x_boundaries",  [167.0, 327.0, 484.0])
        self.declare_parameter("slot_cy_mid",         222.0)
        self.declare_parameter("fms_url",            "http://192.168.1.130:8002")

        obb_topic             = self.get_parameter("obb_topic").value
        self._confirm_secs    = float(self.get_parameter("confirm_secs").value)
        self._slot_x_start    = float(self.get_parameter("slot_x_start").value)
        self._slot_x_end      = float(self.get_parameter("slot_x_end").value)
        self._slot_x_bounds   = list(self.get_parameter("slot_x_boundaries").value)
        self._slot_cy_mid     = float(self.get_parameter("slot_cy_mid").value)
        self._fms_url         = self.get_parameter("fms_url").value

        # 감시 활성화 플래그 — 기본 비활성, vision_pick_place_node가 제어
        self._watching = False
        self._slot_states:    dict[int, str]   = {i: "IDLE" for i in range(8)}
        self._slot_detect_at: dict[int, float] = {i: 0.0    for i in range(8)}
        self._lock = threading.Lock()

        sensor_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            durability=QoSDurabilityPolicy.VOLATILE,
            depth=1,
        )
        self.create_subscription(ObbBoxArray, obb_topic, self._on_obb, sensor_qos,
                                 callback_group=_cb)

        self.create_service(SetBool, "~/reset_slot", self._on_reset_srv,
                            callback_group=_cb)
        self.create_service(SetBool, "~/set_watch",  self._on_set_watch_srv,
                            callback_group=_cb)

        self.get_logger().info(
            f"RetrievalWatcherNode 시작  confirm={self._confirm_secs}s  "
            f"x={self._slot_x_start}~{self._slot_x_end}  cy_mid={self._slot_cy_mid}"
        )

    # ── OBB 수신 ─────────────────────────────────────────────────────────────

    def _on_obb(self, msg: ObbBoxArray) -> None:
        if not self._watching:
            return
        now = time.time()
        detected = set()
        for box in msg.boxes:
            slot = self._to_slot(box.cx, box.cy)
            if slot >= 0:
                detected.add(slot)
        self._update(detected, now)

    # ── 슬롯 매핑 ────────────────────────────────────────────────────────────

    def _to_slot(self, cx: float, cy: float) -> int:
        """픽셀 (cx, cy) → 슬롯 번호 0~7. 구간 밖이면 -1."""
        if cx < self._slot_x_start or cx > self._slot_x_end:
            return -1

        col = sum(1 for b in self._slot_x_bounds if cx > b)  # 0~3
        row = 0 if cy > self._slot_cy_mid else 1             # 하단=0, 상단=1
        return row * 4 + col

    # ── 상태 머신 ─────────────────────────────────────────────────────────────

    def _update(self, detected: set, now: float) -> None:
        with self._lock:
            for slot_id in range(8):
                state = self._slot_states[slot_id]

                if state == "WAITING":
                    continue

                if state == "IDLE":
                    if slot_id in detected:
                        self._slot_states[slot_id]    = "DETECTING"
                        self._slot_detect_at[slot_id] = now
                        self.get_logger().info(f"[Watcher] slot={slot_id} 감지 시작")

                elif state == "DETECTING":
                    if slot_id not in detected:
                        self._slot_states[slot_id] = "IDLE"
                        self.get_logger().info(f"[Watcher] slot={slot_id} 감지 끊김 → IDLE")
                    elif now - self._slot_detect_at[slot_id] >= self._confirm_secs:
                        self._slot_states[slot_id] = "WAITING"
                        self.get_logger().info(f"[Watcher] slot={slot_id} 확정 → FMS 알림")
                        threading.Thread(
                            target=self._notify_fms, args=(slot_id,), daemon=True
                        ).start()

    # ── FMS 알림 ─────────────────────────────────────────────────────────────

    def _notify_fms(self, slot_id: int) -> None:
        url = self._fms_url.rstrip("/") + "/retrieval/start"
        try:
            resp = requests.post(url, params={"slot_id": slot_id}, timeout=3.0)
            data = resp.json()
            self.get_logger().info(
                f"[Watcher] slot={slot_id} FMS 알림 성공: "
                f"task_id={data.get('task_id')} msg={data.get('message')}"
            )
        except Exception as e:
            self.get_logger().warn(f"[Watcher] slot={slot_id} FMS 알림 실패: {e}")
            with self._lock:
                self._slot_states[slot_id] = "IDLE"

    # ── 감시 활성화 서비스 ───────────────────────────────────────────────────

    def _on_set_watch_srv(self, req: SetBool.Request, res: SetBool.Response) -> SetBool.Response:
        """vision_pick_place_node가 pick/place 완료 후 호출.
        data=true → 감시 시작, data=false → 감시 중지 + 슬롯 DETECTING 리셋
        """
        self._watching = req.data
        if not req.data:
            with self._lock:
                for i, state in self._slot_states.items():
                    if state == "DETECTING":
                        self._slot_states[i] = "IDLE"
        self.get_logger().info(f"[Watcher] 감시 {'시작' if req.data else '중지'}")
        res.success, res.message = True, f"watching={self._watching}"
        return res

    # ── 슬롯 리셋 서비스 ─────────────────────────────────────────────────────

    def _on_reset_srv(self, req: SetBool.Request, res: SetBool.Response) -> SetBool.Response:
        """FMS 회수 완료 후 호출. WAITING 슬롯 전체 IDLE 복귀."""
        with self._lock:
            for i, state in self._slot_states.items():
                if state == "WAITING":
                    self._slot_states[i] = "IDLE"
                    self.get_logger().info(f"[Watcher] slot={i} 리셋 → IDLE")
        res.success, res.message = True, "슬롯 리셋 완료"
        return res


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RetrievalWatcherNode()
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
