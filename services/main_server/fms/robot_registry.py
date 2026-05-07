"""
RobotRegistry: 로봇 상태 추적 및 rosbridge 연결 관리.

domain_bridge를 통해 domain 99의 네임스페이스 토픽을 구독하여
각 로봇의 실시간 상태(pose, battery, nav_status)를 관리한다.
"""
import os
import threading
import time
from typing import Optional, Callable

import roslibpy

from fms.config import ROBOTS, ROSBRIDGE_HOST, ROSBRIDGE_PORT

_ROS_STUB = os.getenv("ROS_STUB", "0") == "1"

CONNECT_TIMEOUT = 4
RECONNECT_INTERVAL = 5
STALENESS_TIMEOUT = 10.0  # 메시지 미수신 시 offline 판정 (초)


class RobotState:
    """개별 로봇의 실시간 상태."""

    def __init__(self, robot_id: str, cfg: dict):
        self.robot_id = robot_id
        self.type = cfg["type"]
        self.namespace = cfg.get("namespace", robot_id)
        self.connected = False
        self.battery: float | None = None
        self.pose: dict | None = None
        self.joint_states: dict | None = None
        self.busy = False
        self.last_work_complete: str | None = None
        self._last_msg_time: float = 0.0  # 마지막 메시지 수신 시각

        # 시나리오 상태 (Coordinator가 관리)
        self.delivery_stage: int | None = None
        self._last_arrival_time: float = 0.0

        # 시착 시나리오
        self.tryon_stage: int | None = None
        self.tryon_seat: int | None = None
        self.tryon_product_id: str | None = None
        self.tryon_color: str | None = None
        self.tryon_size: str | None = None

        # nav2 도착 판정
        self._goal_sent_time: float = 0.0
        self._nav_succeeded_at: float = 0.0

        # 입고 시나리오
        self.inbound_task_id: Optional[str] = None
        self.inbound_stage: Optional[int] = None

        # 회수 시나리오
        self.retrieval_task_id: Optional[str] = None
        self.retrieval_stage: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "robot_id": self.robot_id,
            "type": self.type,
            "namespace": self.namespace,
            "connected": self.connected,
            "battery": self.battery,
            "pose": self.pose,
            "joint_states": self.joint_states,
            "busy": self.busy,
            "last_work_complete": self.last_work_complete,
            "delivery_stage": self.delivery_stage,
            "tryon_stage": self.tryon_stage,
            "tryon_seat": self.tryon_seat,
            "tryon_product_id": self.tryon_product_id,
            "inbound_task_id": self.inbound_task_id,
            "inbound_stage": self.inbound_stage,
            "retrieval_task_id": self.retrieval_task_id,
            "retrieval_stage": self.retrieval_stage,
        }

    def reset_live_data(self):
        self.battery = None
        self.pose = None
        self.joint_states = None

    def touch(self):
        """메시지 수신 시 호출 — staleness 판정용."""
        self._last_msg_time = time.time()

    def is_stale(self) -> bool:
        """마지막 메시지 수신 후 STALENESS_TIMEOUT 초 경과 여부."""
        if self._last_msg_time == 0.0:
            return True
        return (time.time() - self._last_msg_time) > STALENESS_TIMEOUT


class RobotRegistry:
    """
    모든 로봇의 상태를 관리하고 rosbridge 연결을 유지한다.
    domain_bridge 환경에서는 단일 rosbridge(domain 99)에 연결하여
    /{namespace}/topic 형태로 구독/발행한다.
    """

    def __init__(self):
        self._client: Optional[roslibpy.Ros] = None
        self._publishers: dict[str, dict[str, roslibpy.Topic]] = {}
        self._states: dict[str, RobotState] = {
            rid: RobotState(rid, cfg) for rid, cfg in ROBOTS.items()
        }
        self._lock = threading.Lock()
        self._running = False

        # 외부에서 등록하는 콜백 (Coordinator가 설정)
        self.on_pose_update: Optional[Callable[[RobotState, dict], None]] = None
        self.on_nav_status: Optional[Callable[[RobotState, dict], None]] = None
        self.on_work_complete: Optional[Callable[[RobotState, str], None]] = None

    @property
    def states(self) -> dict[str, RobotState]:
        return self._states

    def get_state(self, robot_id: str) -> Optional[RobotState]:
        return self._states.get(robot_id)

    def get_all_states(self) -> list[dict]:
        return [s.to_dict() for s in self._states.values()]

    # ── 연결 관리 ────────────────────────────────────────────────────

    def connect(self):
        """단일 rosbridge(domain 99)에 연결하고 모든 로봇 토픽을 구독."""
        if _ROS_STUB:
            for robot_id, cfg in ROBOTS.items():
                if cfg.get("type") == "pinky":
                    self._states[robot_id].connected = True
            print("[registry] STUB 모드 — 모든 pinky 로봇 연결 완료 (가상)")
            return

        try:
            self._client = roslibpy.Ros(host=ROSBRIDGE_HOST, port=ROSBRIDGE_PORT)
            self._client.run(timeout=CONNECT_TIMEOUT)
            self._client.on("close", self._on_connection_lost)
            print(f"[registry] rosbridge 연결 완료 → ws://{ROSBRIDGE_HOST}:{ROSBRIDGE_PORT}")

            # 모든 로봇 토픽 구독
            for robot_id, cfg in ROBOTS.items():
                self._subscribe(robot_id, cfg)
                self._states[robot_id].connected = True

        except Exception as e:
            print(f"[registry] rosbridge 연결 실패: {e}")

    def _on_connection_lost(self, *_):
        """rosbridge 연결 끊김 시 모든 로봇 offline 처리."""
        print("[registry] rosbridge 연결 끊김 — 모든 로봇 offline")
        for state in self._states.values():
            state.connected = False
            state.reset_live_data()

    def start_monitor_loop(self):
        """백그라운드 스레드: 재연결 + staleness 체크."""
        self._running = True
        if _ROS_STUB:
            return
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        while self._running:
            time.sleep(RECONNECT_INTERVAL)
            # rosbridge 연결 확인
            if self._client is None or not self._client.is_connected:
                print("[registry] rosbridge 재연결 시도...")
                self.connect()
                continue
            # staleness 체크 — 메시지 미수신 로봇 offline 처리
            for state in self._states.values():
                if state.type == "pinky" and state.is_stale() and state.connected:
                    state.connected = False
                    print(f"[registry] {state.robot_id} stale → offline")
                elif state.type == "pinky" and not state.is_stale() and not state.connected:
                    state.connected = True
                    print(f"[registry] {state.robot_id} 메시지 수신 → online")

    def close(self):
        self._running = False
        if self._client and self._client.is_connected:
            self._client.terminate()

    # ── 토픽 구독 ────────────────────────────────────────────────────

    def _subscribe(self, robot_id: str, cfg: dict):
        """로봇 타입별 토픽 구독 (네임스페이스 접두사 포함)."""
        state = self._states[robot_id]
        ns = cfg.get("namespace", robot_id)

        if cfg["type"] == "pinky":
            self._sub(f"/{ns}/battery/percent", "std_msgs/Float32",
                      lambda m, s=state: self._on_battery(s, m))
            self._sub(f"/{ns}/amcl_pose", "geometry_msgs/PoseWithCovarianceStamped",
                      lambda m, s=state: self._on_amcl_pose(s, m))
            self._sub(f"/{ns}/navigate_to_pose/_action/status", "action_msgs/GoalStatusArray",
                      lambda m, s=state: self._on_nav_status_msg(s, m))
        elif cfg["type"] == "jetcobot":
            joint_topic = cfg.get("joint_topic", f"/{ns}/joint_states")
            self._sub(joint_topic, "sensor_msgs/JointState",
                      lambda m, s=state: self._on_joint_states(s, m))
            self._sub(f"/{ns}/work_complete", "std_msgs/String",
                      lambda m, s=state: self._on_work_complete_msg(s, m))

    def _sub(self, topic: str, msg_type: str, cb):
        if self._client:
            t = roslibpy.Topic(self._client, topic, msg_type)
            t.subscribe(cb)

    # ── 토픽 콜백 ────────────────────────────────────────────────────

    def _on_battery(self, state: RobotState, msg: dict):
        state.battery = msg.get("data")
        state.touch()

    def _on_amcl_pose(self, state: RobotState, msg: dict):
        pos = msg.get("pose", {}).get("pose", {}).get("position", {})
        state.pose = {"x": round(pos.get("x", 0), 3), "y": round(pos.get("y", 0), 3)}
        state.touch()
        if self.on_pose_update:
            self.on_pose_update(state, msg)

    def _on_nav_status_msg(self, state: RobotState, msg: dict):
        state.touch()
        if self.on_nav_status:
            self.on_nav_status(state, msg)

    def _on_joint_states(self, state: RobotState, msg: dict):
        state.joint_states = {
            "names": msg.get("name", []),
            "positions": [round(v, 4) for v in msg.get("position", [])],
        }
        state.touch()

    def _on_work_complete_msg(self, state: RobotState, msg: dict):
        data = msg.get("data", "")
        state.last_work_complete = data
        state.touch()
        if self.on_work_complete:
            self.on_work_complete(state, data)

    # ── 토픽 발행 ────────────────────────────────────────────────────

    def publish_goal_pose(self, robot_id: str, x: float, y: float, theta: float) -> bool:
        """/{namespace}/goal_pose 발행."""
        if _ROS_STUB:
            ns = ROBOTS[robot_id].get("namespace", robot_id)
            print(f"[STUB] {robot_id} → goal_pose x={x} y={y} theta={theta:.2f}")
            return True
        ns = ROBOTS[robot_id].get("namespace", robot_id)
        return self._publish(robot_id, f"/{ns}/goal_pose", "geometry_msgs/PoseStamped", {
            "header": {"frame_id": "map"},
            "pose": {
                "position": {"x": x, "y": y, "z": 0.0},
                "orientation": {
                    "x": 0.0, "y": 0.0,
                    "z": round((__import__("math").sin(theta / 2)), 6),
                    "w": round((__import__("math").cos(theta / 2)), 6),
                },
            },
        })

    def publish_cmd_vel(self, robot_id: str, linear_x: float, angular_z: float) -> bool:
        """/{namespace}/cmd_vel 발행."""
        if _ROS_STUB:
            return True
        ns = ROBOTS[robot_id].get("namespace", robot_id)
        return self._publish(robot_id, f"/{ns}/cmd_vel", "geometry_msgs/Twist", {
            "linear": {"x": linear_x, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0, "y": 0.0, "z": angular_z},
        })

    def publish_trigger_work(self, robot_id: str, sshopy_id: str) -> bool:
        """/{namespace}/trigger_work 발행."""
        if _ROS_STUB:
            return True
        ns = ROBOTS[robot_id].get("namespace", robot_id)
        return self._publish(robot_id, f"/{ns}/trigger_work", "std_msgs/String", {
            "data": sshopy_id,
        })

    def _publish(self, robot_id: str, topic: str, msg_type: str, message: dict) -> bool:
        if not self._client or not self._client.is_connected:
            return False
        with self._lock:
            pubs = self._publishers.setdefault(robot_id, {})
            if topic not in pubs:
                t = roslibpy.Topic(self._client, topic, msg_type)
                t.advertise()
                pubs[topic] = t
        self._publishers[robot_id][topic].publish(roslibpy.Message(message))
        return True


# 싱글턴
registry = RobotRegistry()
