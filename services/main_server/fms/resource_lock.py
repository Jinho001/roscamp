"""
ResourceLock: 공유 자원(front_jet, ware_jet) 사용권 관리.

여러 시나리오가 동시에 실행될 때, jetcobot(팔 로봇)의
동시 접근을 방지하는 잠금/대기 큐.
"""
import threading
from collections import deque
from typing import Optional


class ResourceLock:
    """
    단일 자원에 대한 잠금 + 대기 큐.
    acquire() → 사용 → release() 패턴.
    """

    def __init__(self, name: str):
        self.name = name
        self._owner: Optional[str] = None  # 현재 잠금 보유 task_id
        self._queue: deque[threading.Event] = deque()
        self._lock = threading.Lock()

    @property
    def is_locked(self) -> bool:
        return self._owner is not None

    @property
    def owner(self) -> Optional[str]:
        return self._owner

    def acquire(self, task_id: str, timeout: float = 60.0) -> bool:
        """
        자원 잠금 획득. 이미 사용 중이면 timeout까지 대기.
        Returns: True=획득 성공, False=timeout
        """
        with self._lock:
            if self._owner is None:
                self._owner = task_id
                print(f"[resource] {self.name} 잠금 획득: {task_id}")
                return True
            # 대기열에 등록
            event = threading.Event()
            self._queue.append(event)

        print(f"[resource] {self.name} 대기 중: {task_id} (owner={self._owner})")
        acquired = event.wait(timeout=timeout)

        if acquired:
            with self._lock:
                self._owner = task_id
            print(f"[resource] {self.name} 잠금 획득 (대기 후): {task_id}")
        else:
            # timeout — 대기열에서 제거
            with self._lock:
                try:
                    self._queue.remove(event)
                except ValueError:
                    pass
            print(f"[resource] {self.name} 잠금 timeout: {task_id}")

        return acquired

    def release(self, task_id: str):
        """자원 잠금 해제. 대기 중인 다음 task에게 양도."""
        with self._lock:
            if self._owner != task_id:
                print(f"[resource] {self.name} 해제 무시: {task_id} (owner={self._owner})")
                return
            self._owner = None
            # 대기열에서 다음 task 깨우기
            if self._queue:
                next_event = self._queue.popleft()
                next_event.set()
            else:
                print(f"[resource] {self.name} 잠금 해제: {task_id}")

    def force_release(self):
        """강제 해제 (에러 복구용)."""
        with self._lock:
            old_owner = self._owner
            self._owner = None
            if self._queue:
                next_event = self._queue.popleft()
                next_event.set()
        if old_owner:
            print(f"[resource] {self.name} 강제 해제: {old_owner}")


# 공유 자원 싱글턴
front_jet_lock = ResourceLock("front_jet")
ware_jet_lock = ResourceLock("ware_jet")
