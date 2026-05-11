"""
MutexGroupManager — RMF mutex group 의 단순 구현.

같은 group 키를 가진 acquire 들 사이에서만 직렬화 (FIFO 큐).
Group 이름은 기본적으로 zones.yaml 의 mutex 필드에서 자동 수집되며,
테스트용으로 register() 로 수동 등록도 가능하다.

내부적으로 fms.resource_lock.ResourceLock (큐 + Event 기반) 을 그룹별로 보관.

Phase 2 — 모듈만 추가, 기존 시나리오는 사용 안 함.
Phase 3 (inbound_demo_v2) 에서 호출 예정.
"""
import threading
from typing import Optional

from fms.resource_lock import ResourceLock
from fms.scenarios.zones import load_zones


class MutexGroupManager:
    """
    string-keyed mutex group manager.

    기본은 strict — register 되지 않은 그룹에 acquire 하면 ValueError.
    이는 zones.yaml 에 없는 그룹 이름 오타를 빠르게 잡기 위함.
    """

    def __init__(self, preregister_from_zones: bool = True):
        self._groups: dict[str, ResourceLock] = {}
        self._guard = threading.Lock()
        if preregister_from_zones:
            try:
                zones = load_zones()
                for group in zones.get("by_mutex", {}):
                    self._groups[group] = ResourceLock(f"mutex:{group}")
            except Exception as e:
                print(f"[mutex_group] zones.yaml 로드 실패 — 빈 상태로 시작: {e}")

    # ── public API ───────────────────────────────────────────────────────

    def register(self, group: str) -> None:
        """그룹 수동 등록 — 테스트용. 이미 있으면 무시."""
        with self._guard:
            if group not in self._groups:
                self._groups[group] = ResourceLock(f"mutex:{group}")

    def acquire(self, group: str, owner: str, timeout: float = 60.0) -> bool:
        """
        owner(robot_id 또는 task_id) 가 group 에 진입.
        다른 owner 가 보유 중이면 timeout 까지 FIFO 큐에서 대기.
        Returns: True=획득, False=timeout.
        """
        lock = self._groups.get(group)
        if lock is None:
            raise ValueError(
                f"unknown mutex group: {group!r} "
                f"(registered: {sorted(self._groups)})"
            )
        return lock.acquire(owner, timeout=timeout)

    def release(self, group: str, owner: str) -> None:
        """owner 가 group 에서 퇴장. owner 가 다르면 무시 (ResourceLock 동작)."""
        lock = self._groups.get(group)
        if lock is None:
            print(f"[mutex_group] release 무시 — 그룹 없음: {group}")
            return
        lock.release(owner)

    def force_release(self, group: str) -> None:
        """비상 복구용 강제 해제."""
        lock = self._groups.get(group)
        if lock is None:
            print(f"[mutex_group] force_release 무시 — 그룹 없음: {group}")
            return
        lock.force_release()

    def status(self) -> dict:
        """
        모든 그룹의 owner / locked 상태.
        Returns: {group: {"owner": str|None, "locked": bool}}
        """
        with self._guard:
            return {
                group: {"owner": lock.owner, "locked": lock.is_locked}
                for group, lock in self._groups.items()
            }

    def groups(self) -> list[str]:
        """등록된 모든 group 이름."""
        with self._guard:
            return list(self._groups.keys())


# 싱글턴 — 프로덕션에서 import 해서 사용
mutex_groups = MutexGroupManager()


if __name__ == "__main__":
    import json
    print(f"registered groups: {mutex_groups.groups()}")
    print(json.dumps(mutex_groups.status(), indent=2, ensure_ascii=False))
