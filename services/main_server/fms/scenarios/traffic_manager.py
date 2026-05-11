"""
Traffic Manager — 다중 시나리오 실행 시 sshopy 간 충돌 회피 (yield-based).

설계:
    - 각 시나리오 orchestrator가 task 시작/종료 시 register/unregister 호출
    - 백그라운드 스레드가 POLL_INTERVAL 주기로 활성 sshopy 쌍 거리 검사
    - YIELD_RADIUS 이내 → 우선순위 비교 → 저우선 sshopy 일시정지
    - tie: task 시작 시간이 빠른 sshopy 우선
    - 일시정지: cmd_vel(0,0) + goal_pose(현재 위치) (Nav2 cancel)
    - 자동 재개: 모든 고우선 sshopy가 YIELD_RADIUS 밖으로 나가면 원래 goal 재발행

Priority (lower number = higher priority):
    1: customer_call (TVC 손들기 호출)
    2: tryon
    3: inbound
    4: retrieval

레이어 분리:
    inbound_demo의 zone 락(FrontJet/창고존/서브존)은 그대로 유지.
    트래픽 매니저는 그 위에 얹는 별도 충돌 회피 레이어.
"""

import math
import threading
import time
from typing import Optional


# ── 우선순위 상수 ────────────────────────────────────────────────────────────
PRIORITY_CUSTOMER_CALL = 1
PRIORITY_TRYON         = 2
PRIORITY_INBOUND       = 3
PRIORITY_RETRIEVAL     = 4

# ── 동작 파라미터 ────────────────────────────────────────────────────────────
YIELD_RADIUS    = 0.35  # m — 두 sshopy 거리가 이내이면 저우선이 양보
POLL_INTERVAL   = 0.3   # s — 검사 주기


class _Registration:
    __slots__ = ("rid", "priority", "started_at", "paused", "last_goal", "pause_reason")

    def __init__(self, rid: str, priority: int):
        self.rid          = rid
        self.priority     = priority
        self.started_at   = time.time()
        self.paused       = False
        self.last_goal    = None  # {"x", "y", "theta"} — orchestrator가 발행한 마지막 goal
        self.pause_reason: Optional[str] = None


class TrafficManager:
    """
    fleet.goal_pose() 가 호출되면 notify_goal() 를 통해 last_goal을 갱신한다.
    내부 pause/resume 시에는 _traffic_internal=True 로 호출해 last_goal 갱신 우회.
    """

    def __init__(self, fleet):
        self.fleet = fleet
        self._regs: dict[str, _Registration] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[traffic] manager started")

    def stop(self):
        self._stop.set()

    # ── public API ───────────────────────────────────────────────────────
    def register(self, rid: str, priority: int):
        """Task 시작 시 호출 — 우선순위 등록."""
        with self._lock:
            reg = self._regs.get(rid)
            if reg:
                # 이미 등록 → 우선순위만 갱신 (예: scenario 전환)
                reg.priority = priority
                reg.started_at = time.time()
                print(f"[traffic] re-register {rid} priority={priority}")
            else:
                self._regs[rid] = _Registration(rid, priority)
                print(f"[traffic] register {rid} priority={priority}")

    def unregister(self, rid: str):
        """Task 종료 시 호출."""
        with self._lock:
            reg = self._regs.pop(rid, None)
        if reg and reg.paused:
            # 정지 상태로 끝나는 경우 마지막 goal 재발행하지 않고 그냥 종료
            print(f"[traffic] unregister {rid} (was paused)")
        else:
            print(f"[traffic] unregister {rid}")

    def notify_goal(self, rid: str, x: float, y: float, theta: float):
        """orchestrator가 fleet.goal_pose() 호출 시 자동 통지됨 (last_goal 추적)."""
        with self._lock:
            reg = self._regs.get(rid)
            if reg:
                reg.last_goal = {"x": x, "y": y, "theta": theta}

    def get_status(self) -> dict:
        with self._lock:
            return {
                rid: {
                    "priority":   reg.priority,
                    "paused":     reg.paused,
                    "reason":     reg.pause_reason,
                    "started_at": round(reg.started_at, 1),
                    "last_goal":  reg.last_goal,
                }
                for rid, reg in self._regs.items()
            }

    # ── 내부 ─────────────────────────────────────────────────────────────
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[traffic] tick error: {e}")
            time.sleep(POLL_INTERVAL)

    def _tick(self):
        with self._lock:
            regs = list(self._regs.values())

        # 1) Pairwise check — 충돌 임박 시 저우선 pause
        for i, a in enumerate(regs):
            for b in regs[i+1:]:
                self._check_pair(a, b)

        # 2) Paused 인 sshopy 들 — 모든 고우선 sshopy가 멀어졌는지 재확인 후 resume
        for reg in regs:
            if reg.paused:
                self._maybe_resume(reg, regs)

    def _check_pair(self, a: _Registration, b: _Registration):
        pa = self._pose(a.rid)
        pb = self._pose(b.rid)
        if not pa or not pb:
            return
        dist = math.hypot(pa["x"] - pb["x"], pa["y"] - pb["y"])
        if dist >= YIELD_RADIUS:
            return

        # 우선순위 결정 — 낮은 숫자가 높은 우선순위
        if a.priority < b.priority:
            yielder, winner = b, a
        elif a.priority > b.priority:
            yielder, winner = a, b
        else:
            # tie: started_at 빠른 쪽이 우선 (started_at 큰 쪽이 양보)
            if a.started_at <= b.started_at:
                yielder, winner = b, a
            else:
                yielder, winner = a, b

        if not yielder.paused:
            self._pause(yielder, by=winner.rid, dist=dist)

    def _maybe_resume(self, reg: _Registration, all_regs: list):
        my_pose = self._pose(reg.rid)
        if not my_pose:
            return
        # 아직도 yield 해야 할 상대가 있는지 검사
        for other in all_regs:
            if other.rid == reg.rid:
                continue
            higher = other.priority < reg.priority or (
                other.priority == reg.priority and other.started_at < reg.started_at
            )
            if not higher:
                continue
            other_pose = self._pose(other.rid)
            if not other_pose:
                continue
            dist = math.hypot(my_pose["x"] - other_pose["x"], my_pose["y"] - other_pose["y"])
            if dist < YIELD_RADIUS:
                return  # still yielding
        self._resume(reg)

    def _pause(self, reg: _Registration, by: str, dist: float):
        rid = reg.rid
        pose = self._pose(rid)
        if not pose:
            return
        # cmd_vel 0,0 + 현재 위치 goal (Nav2 cancel) — _traffic_internal=True 로 last_goal 보존
        self.fleet.cmd_vel(rid, 0.0, 0.0)
        self.fleet.goal_pose(rid, pose["x"], pose["y"], 0.0, _traffic_internal=True)
        reg.paused = True
        reg.pause_reason = f"yield to {by} (dist={dist:.2f}m)"
        print(f"[traffic] PAUSE {rid} ← yield {by} (dist={dist:.2f}m)")

    def _resume(self, reg: _Registration):
        rid = reg.rid
        if reg.last_goal:
            g = reg.last_goal
            self.fleet.goal_pose(rid, g["x"], g["y"], g["theta"], _traffic_internal=True)
            print(f"[traffic] RESUME {rid} → ({g['x']:.2f}, {g['y']:.2f})")
        else:
            print(f"[traffic] RESUME {rid} (no last_goal)")
        reg.paused = False
        reg.pause_reason = None

    def _pose(self, rid: str) -> Optional[dict]:
        state = self.fleet._states.get(rid)
        return state.pose if state else None
