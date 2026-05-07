"""
Coordinator: RobotManager를 래핑하여 공유 자원 잠금 + domain_bridge 기반 통신을 추가.

기존 robot_manager.py의 fleet 인터페이스를 그대로 위임하면서,
ResourceLock을 통한 front_jet/ware_jet 동시 접근 방지를 추가한다.

사용법:
    from fms.coordinator import coordinator
    # coordinator는 fleet과 동일한 인터페이스를 제공하되 자원 잠금이 적용됨.
"""
import threading
from typing import Optional, Callable

from fms.robot_manager import fleet, RobotManager
from fms.robot_registry import registry, RobotRegistry
from fms.resource_lock import front_jet_lock, ware_jet_lock


class Coordinator:
    """
    중앙 조율자.
    - fleet(RobotManager)의 모든 메서드를 위임
    - SSH 실행 시 ResourceLock으로 공유 자원 잠금
    - domain_bridge 환경에서 registry를 통한 상태 모니터링
    """

    def __init__(self, fleet_mgr: RobotManager, reg: RobotRegistry):
        self._fleet = fleet_mgr
        self._registry = reg

    # ── lifecycle (fleet에 위임) ──────────────────────────────────────

    def connect_all(self):
        self._fleet.connect_all()

    def start_reconnect_loop(self):
        self._fleet.start_reconnect_loop()

    def close_all(self):
        self._fleet.close_all()

    # ── 상태 조회 (fleet에 위임) ─────────────────────────────────────

    def get_all_states(self) -> list[dict]:
        return self._fleet.get_all_states()

    def get_robot_state(self, robot_id: str) -> Optional[dict]:
        return self._fleet.get_robot_state(robot_id)

    def get_seat_occupancy(self):
        return self._fleet.get_seat_occupancy()

    # ── 명령 (fleet에 위임) ──────────────────────────────────────────

    def cmd_vel(self, robot_id: str, linear_x: float, angular_z: float) -> bool:
        return self._fleet.cmd_vel(robot_id, linear_x, angular_z)

    def goal_pose(self, robot_id: str, x: float, y: float, theta: float = 0.0) -> bool:
        return self._fleet.goal_pose(robot_id, x, y, theta)

    def trigger_work(self, robot_id: str, sshopy_id: str) -> bool:
        return self._fleet.trigger_work(robot_id, sshopy_id)

    # ── 시착 시나리오 (fleet에 위임) ─────────────────────────────────

    def start_tryon(self, robot_id: str, seat_id: int, product_id: str,
                    color=None, size=None):
        return self._fleet.start_tryon(robot_id, seat_id, product_id, color, size)

    def complete_pickup(self, robot_id: str):
        return self._fleet.complete_pickup(robot_id)

    def cancel_tryon(self, robot_id: str):
        return self._fleet.cancel_tryon(robot_id)

    # ── 입고 시나리오 (fleet에 위임 + 자원 잠금) ─────────────────────

    def start_inbound(self, items=None, robot_id=None):
        return self._fleet.start_inbound(items=items, robot_id=robot_id)

    def notify_scan_complete(self, task_id: str, scan_result: dict):
        return self._fleet.notify_scan_complete(task_id, scan_result)

    def cancel_inbound(self, task_id: str):
        return self._fleet.cancel_inbound(task_id)

    def get_inbound_status(self, task_id: str):
        return self._fleet.get_inbound_status(task_id)

    def get_active_inbound(self, robot_id: str):
        return self._fleet.get_active_inbound(robot_id)

    def get_all_inbound_tasks(self):
        return self._fleet.get_all_inbound_tasks()

    # ── 회수 시나리오 (fleet에 위임) ─────────────────────────────────

    def start_retrieval(self, robot_id: str = "sshopy2"):
        return self._fleet.start_retrieval(robot_id)

    def identify_product(self, task_id, product_id, size=0, color="", quantity=1):
        return self._fleet.identify_product(task_id, product_id, size, color, quantity)

    def notify_db_restored(self, task_id: str):
        return self._fleet.notify_db_restored(task_id)

    def cancel_retrieval(self, task_id: str):
        return self._fleet.cancel_retrieval(task_id)

    def cancel_retrieval_by_robot(self, robot_id: str):
        return self._fleet.cancel_retrieval_by_robot(robot_id)

    def get_retrieval_status(self, task_id: str):
        return self._fleet.get_retrieval_status(task_id)

    def get_active_retrieval(self, robot_id: str):
        return self._fleet.get_active_retrieval(robot_id)

    def get_all_retrieval_tasks(self):
        return self._fleet.get_all_retrieval_tasks()

    # ── 배달 시나리오 ────────────────────────────────────────────────

    def start_delivery(self, robot_id: str):
        return self._fleet.start_delivery(robot_id)

    def cancel_delivery(self, robot_id: str):
        return self._fleet.cancel_delivery(robot_id)

    # ── ARM 제어 ─────────────────────────────────────────────────────

    def arm_reset(self, robot_id: str):
        return self._fleet.arm_reset(robot_id)

    def arm_test(self, robot_id: str):
        return self._fleet.arm_test(robot_id)

    # ── 콜백 프로퍼티 (fleet에 위임) ────────────────────────────────

    @property
    def on_inbound_stage_change(self):
        return self._fleet.on_inbound_stage_change

    @on_inbound_stage_change.setter
    def on_inbound_stage_change(self, cb):
        self._fleet.on_inbound_stage_change = cb

    @property
    def on_inbound_complete(self):
        return self._fleet.on_inbound_complete

    @on_inbound_complete.setter
    def on_inbound_complete(self, cb):
        self._fleet.on_inbound_complete = cb

    @property
    def on_retrieval_stage_change(self):
        return self._fleet.on_retrieval_stage_change

    @on_retrieval_stage_change.setter
    def on_retrieval_stage_change(self, cb):
        self._fleet.on_retrieval_stage_change = cb

    @property
    def on_retrieval_complete(self):
        return self._fleet.on_retrieval_complete

    @on_retrieval_complete.setter
    def on_retrieval_complete(self, cb):
        self._fleet.on_retrieval_complete = cb

    @property
    def get_warehouse_pos(self):
        return self._fleet.get_warehouse_pos

    @get_warehouse_pos.setter
    def get_warehouse_pos(self, cb):
        self._fleet.get_warehouse_pos = cb

    # ── ResourceLock 상태 조회 ───────────────────────────────────────

    def get_resource_status(self) -> dict:
        """공유 자원(front_jet, ware_jet) 잠금 상태 조회."""
        return {
            "front_jet": {
                "locked": front_jet_lock.is_locked,
                "owner": front_jet_lock.owner,
            },
            "ware_jet": {
                "locked": ware_jet_lock.is_locked,
                "owner": ware_jet_lock.owner,
            },
        }


# 싱글턴
coordinator = Coordinator(fleet, registry)
