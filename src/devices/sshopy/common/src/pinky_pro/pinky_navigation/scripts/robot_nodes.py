#!/usr/bin/env python3
# ============================================================
#  robot_nodes.py  —  핑키 1대에서 실행되는 ROS2 노드
#
#  변경사항 (원본 대비):
#    - 모든 토픽/액션에 namespace 적용 → 3대가 동시 실행돼도 신호 안 섞임
#    - namespace는 launch 파라미터로 주입 (pinky_id: "pinky1" / "pinky2" / "pinky3")
#    - load_event / unload_event를 threading.Event → asyncio.Event로 교체
#      (async execute_move_callback 안에서 await 가능하도록)
#
#  실행 예시 (각 핑키에서):
#    ros2 run <패키지> robot_nodes --ros-args -p pinky_id:=pinky1
#    ros2 run <패키지> robot_nodes --ros-args -p pinky_id:=pinky2
#    ros2 run <패키지> robot_nodes --ros-args -p pinky_id:=pinky3
# ============================================================

import asyncio
import threading

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, GoalResponse, CancelResponse, ActionClient
from rclpy.action.server import ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor

from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import Bool


class SShopyNode(Node):
    def __init__(self):
        super().__init__("sshopy_node")

        # ── 파라미터: 핑키 ID (pinky1 / pinky2 / pinky3) ────
        self.declare_parameter("pinky_id", "pinky1")
        self._pinky_id = self.get_parameter("pinky_id").get_parameter_value().string_value
        ns = f"/{self._pinky_id}"   # e.g. "/pinky1"
        self.get_logger().info(f"[{self._pinky_id}] 노드 시작 (namespace: {ns})")

        self.callback_group = ReentrantCallbackGroup()

        # ── Nav2 ActionClient ────────────────────────────────
        # namespace별 navigate_to_pose에 연결
        self._nav2_client = ActionClient(
            self, NavigateToPose,
            f"{ns}/navigate_to_pose",
            callback_group=self.callback_group
        )

        # ── 발행: 핑키 → FrontJet (입고존 도착 알림) ───────────
        # FrontJet만 구독. FMS 무관.
        # 토픽: /frontjet/pinky_arrived/{pinky_id}
        self._arrived_pub = self.create_publisher(
            Bool, f"/frontjet/pinky_arrived/{self._pinky_id}", 10
        )

        # ── 구독: FrontJet → 핑키 (적재 완료 신호) ──────────
        # FrontJet이 발행, 이 핑키만 구독.
        # 토픽: /frontjet/load_complete/{pinky_id}
        self._load_complete_sub = self.create_subscription(
            Bool,
            f"/frontjet/load_complete/{self._pinky_id}",
            self._load_complete_callback,
            10,
            callback_group=self.callback_group
        )

        # ── 발행: 핑키 → WareJet (창고 도착 알림) ────────────
        # WareJet만 구독. FMS 무관.
        # 토픽: /warejet/pinky_arrived/{pinky_id}
        self._warejet_arrived_pub = self.create_publisher(
            Bool, f"/warejet/pinky_arrived/{self._pinky_id}", 10
        )

        # ── 구독: WareJet → 핑키 (하차 완료 신호) ────────────
        # WareJet이 발행, 이 핑키만 구독.
        # 토픽: /warejet/unload_complete/{pinky_id}
        self._unload_complete_sub = self.create_subscription(
            Bool,
            f"/warejet/unload_complete/{self._pinky_id}",
            self._unload_complete_callback,
            10,
            callback_group=self.callback_group
        )

        # ── Waypoint 위치 정보 ────────────────────────────────
        # ※ FMS의 config.py와 좌표를 맞춰두세요.
        self._locations = {
            "warejet": {
                "x": -0.003, "y":  0.160,
                "z":  0.026, "w":  1.000,
            },
            "frontjet": {
                "x":  0.720, "y":  0.477,
                "z":  0.686, "w":  0.727,
            },
            # 홈 위치는 핑키마다 다름 → 파라미터로 오버라이드 가능
            "home": self._home_location(),
        }

        # ── 이벤트 (async-safe) ───────────────────────────────
        # asyncio.Event는 이벤트 루프 안에서만 사용 가능.
        # 루프가 준비된 뒤 생성하기 위해 None으로 초기화.
        self._load_event: asyncio.Event | None   = None
        self._unload_event: asyncio.Event | None = None

        # threading.Event는 _load_complete_callback 등
        # 콜백 스레드에서 set() 하고 asyncio 루프에서 감지할 때 사용
        self._load_flag   = threading.Event()
        self._unload_flag = threading.Event()

        # ── TaskCoordinator로부터 작업 받는 ActionServer ──────
        # FMS가 /pinky1/sshopy/move 로 goal을 보냄
        self._move_action_server = ActionServer(
            self, NavigateToPose,
            f"{ns}/sshopy/move",
            execute_callback=self.execute_move_callback,
            goal_callback=self.goal_callback,
            cancel_callback=self.cancel_callback,
            callback_group=self.callback_group
        )

        self.get_logger().info(f"[{self._pinky_id}] 준비 완료!")

    # ── 핑키별 홈 위치 ────────────────────────────────────────
    def _home_location(self) -> dict:
        """핑키 ID에 따라 홈 좌표 반환 (config.py 값과 동일)"""
        homes = {
            "pinky1": {"x":  0.771, "y": -0.008, "z":  0.352, "w":  0.936},
            "pinky2": {"x":  0.823, "y":  0.649, "z": -0.466, "w":  0.885},
            "pinky3": {"x":  1.481, "y":  0.301, "z":  1.000, "w":  0.000},
        }
        return homes.get(self._pinky_id, homes["pinky1"])

    # ── Goal / Cancel 콜백 ────────────────────────────────────
    def goal_callback(self, goal_request) -> GoalResponse:
        self.get_logger().info(f"[{self._pinky_id}] 작업 요청 수신")
        return GoalResponse.ACCEPT

    def cancel_callback(self, goal_handle) -> CancelResponse:
        self.get_logger().info(f"[{self._pinky_id}] 작업 취소 요청")
        return CancelResponse.ACCEPT

    # ── 적재/하차 완료 콜백 (일반 스레드에서 호출됨) ──────────
    def _load_complete_callback(self, msg: Bool):
        if msg.data:
            self.get_logger().info(f"[{self._pinky_id}] ✅ 적재 완료 신호 수신")
            self._load_flag.set()

    def _unload_complete_callback(self, msg: Bool):
        if msg.data:
            self.get_logger().info(f"[{self._pinky_id}] ✅ 하차 완료 신호 수신")
            self._unload_flag.set()

    # ── 메인 시나리오 (async) ─────────────────────────────────
    async def execute_move_callback(self, goal_handle: ServerGoalHandle):
        self.get_logger().info(f"[{self._pinky_id}] 🚀 시나리오 시작!")

        # 1단계 — 입고존(FrontJet)으로 이동
        self.get_logger().info(f"[{self._pinky_id}] 1단계: 입고존 이동 중...")
        if not await self._navigate_to("frontjet"):
            self.get_logger().error(f"[{self._pinky_id}] 입고존 이동 실패")
            goal_handle.abort()
            return NavigateToPose.Result()

        # 2단계 — 입고존 도착 신호 발행
        self.get_logger().info(f"[{self._pinky_id}] 2단계: 입고존 도착 신호 발행")
        self._publish_bool(self._arrived_pub)

        # 3단계 — 적재 완료 신호 대기
        self.get_logger().info(f"[{self._pinky_id}] 3단계: 적재 완료 대기 중...")
        self._load_flag.clear()
        await self._wait_flag(self._load_flag)

        # 4단계 — 창고(WareJet)로 이동
        self.get_logger().info(f"[{self._pinky_id}] 4단계: 창고 이동 중...")
        if not await self._navigate_to("warejet"):
            self.get_logger().error(f"[{self._pinky_id}] 창고 이동 실패")
            goal_handle.abort()
            return NavigateToPose.Result()

        # 5단계 — 창고 도착 신호 발행
        self.get_logger().info(f"[{self._pinky_id}] 5단계: 창고 도착 신호 발행")
        self._publish_bool(self._warejet_arrived_pub)

        # 6단계 — 하차 완료 신호 대기
        self.get_logger().info(f"[{self._pinky_id}] 6단계: 하차 완료 대기 중...")
        self._unload_flag.clear()
        await self._wait_flag(self._unload_flag)

        # 7단계 — 홈으로 복귀
        self.get_logger().info(f"[{self._pinky_id}] 7단계: 홈 복귀 중...")
        if not await self._navigate_to("home"):
            self.get_logger().error(f"[{self._pinky_id}] 홈 이동 실패")
            goal_handle.abort()
            return NavigateToPose.Result()

        self.get_logger().info(f"[{self._pinky_id}] 🎉 시나리오 완료!")
        goal_handle.succeed()
        return NavigateToPose.Result()

    # ── navigate_to_pose 전송 ─────────────────────────────────
    async def _navigate_to(self, target: str) -> bool:
        if target not in self._locations:
            self.get_logger().error(f"[{self._pinky_id}] 알 수 없는 위치: {target}")
            return False

        loc = self._locations[target]
        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp    = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = loc["x"]
        goal.pose.pose.position.y = loc["y"]
        goal.pose.pose.orientation.z = loc["z"]
        goal.pose.pose.orientation.w = loc["w"]

        self._nav2_client.wait_for_server()
        goal_handle = await self._nav2_client.send_goal_async(goal)

        if not goal_handle.accepted:
            self.get_logger().warn(f"[{self._pinky_id}] goal 거절됨: {target}")
            return False

        result = await goal_handle.get_result_async()
        return result.status == 4  # STATUS_SUCCEEDED

    # ── 유틸: Bool 발행 ───────────────────────────────────────
    def _publish_bool(self, publisher, value: bool = True):
        msg = Bool()
        msg.data = value
        publisher.publish(msg)

    # ── 유틸: threading.Event를 async로 대기 ─────────────────
    @staticmethod
    async def _wait_flag(flag: threading.Event, poll_interval: float = 0.1):
        """
        threading.Event를 asyncio 루프를 블로킹하지 않고 대기.
        콜백 스레드에서 flag.set() 되면 반환.
        """
        while not flag.is_set():
            await asyncio.sleep(poll_interval)


# ──────────────────────────────────────────────────────────────
def main(args=None):
    rclpy.init(args=args)
    node = SShopyNode()
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