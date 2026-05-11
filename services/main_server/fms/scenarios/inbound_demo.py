"""
다중-로봇 입고 데모 시나리오 (Scene 1 확장).

흐름 (각 sshopy 독립 워커 스레드):
    HOME → FrontJet → 창고존(WareJet) → 창고 서브존 → HOME

Resource Lock (mutex, 3개):
    - FrontJet  (TRYON_FRONTJET)
    - 창고존    (TRYON_WAREJET)
    - 서브존    (WAREJET_SUBZONE)

배정 순서:
    FrontJet 좌표 기준 거리 가까운 sshopy 순으로 FrontJet 락 획득.

팔 작업:
    SSH 미실행 — FRONTJET_SIM_SECONDS / WAREJET_SIM_SECONDS sleep 시뮬.

Traffic Manager:
    Python threading.Lock 자체가 traffic manager 역할. 한 sshopy가 창고존
    점유 중이면, FrontJet 끝낸 다른 sshopy는 _warejet_lock.acquire()에서
    자연스럽게 블로킹되어 대기한다.
"""

import math
import os
import threading
import time
from typing import Optional

import roslibpy

from fms.scenarios.constants import (
    TRYON_FRONTJET, TRYON_WAREJET, WAREJET_SUBZONE, tryon_home,
)

_ROS_STUB = os.getenv("ROS_STUB", "0") == "1"


# ── stage 상수 (40번대 — 기존 시나리오와 분리) ─────────────────────────────────
DEMO_STAGE_QUEUED         = 40   # FrontJet 락 대기 (초기)
DEMO_STAGE_TO_FRONTJET    = 41
DEMO_STAGE_FRONTJET_WORK  = 42
DEMO_STAGE_WAIT_WAREJET   = 48   # 창고존 락 대기 (FrontJet 작업 후)
DEMO_STAGE_TO_WAREJET     = 43
DEMO_STAGE_WAREJET_WORK   = 44
DEMO_STAGE_WAIT_SUBZONE   = 49   # 서브존 락 대기
DEMO_STAGE_TO_SUBZONE     = 45
DEMO_STAGE_TO_HOME        = 46
DEMO_STAGE_DONE           = 47

DEMO_STAGE_LABELS = {
    DEMO_STAGE_QUEUED:        "FrontJet 락 대기",
    DEMO_STAGE_TO_FRONTJET:   "FrontJet 이동 중",
    DEMO_STAGE_FRONTJET_WORK: "FrontJet 상차 중 (sim)",
    DEMO_STAGE_WAIT_WAREJET:  "창고존 락 대기 (Traffic Mgr)",
    DEMO_STAGE_TO_WAREJET:    "창고존 이동 중",
    DEMO_STAGE_WAREJET_WORK:  "WareJet 적재 중 (sim)",
    DEMO_STAGE_WAIT_SUBZONE:  "서브존 락 대기",
    DEMO_STAGE_TO_SUBZONE:    "서브존 이동 중",
    DEMO_STAGE_TO_HOME:       "홈 복귀 중",
    DEMO_STAGE_DONE:          "완료",
}

ARRIVAL_THRESHOLD     = 0.30   # m
ARRIVAL_POLL_INTERVAL = 0.2    # s
ARRIVAL_TIMEOUT       = 60.0   # s — leg 한 단위 최대 대기
JETCOBOT_TIMEOUT      = 120.0  # s — 팔 작업 최대 대기 (Pick&Place)
FRONTJET_SIM_SECONDS  = 3.0    # STUB 모드 폴백 sleep
WAREJET_SIM_SECONDS   = 4.0    # STUB 모드 폴백 sleep
START_STAGGER         = 0.2    # s — FrontJet 락 획득 순서 보장용
ZONE_BUFFER_DIST      = 0.4    # m — 이 거리 이상 zone에서 멀어지면 락 해제 (충돌 방지 buffer)
BUFFER_CLEAR_TIMEOUT  = 30.0   # s — buffer 통과 대기 최대 시간
EXTRA_TASKS           = 1      # 추가 task 개수 — 홈 도착한 유휴 sshopy가 추가로 수행


class InboundDemoOrchestrator:
    """
    다중-sshopy 입고 데모 오케스트레이터.

    fleet (RobotManager)에 의존:
        fleet._states[rid]      — _RobotState (pose, _nav_succeeded_at 사용)
        fleet._is_robot_idle()  — 시작 가능 여부
        fleet.goal_pose()       — Nav2 이동 명령
        fleet.cmd_vel()         — 즉시 정지용
    """

    def __init__(self, fleet):
        self.fleet = fleet
        self._frontjet_lock = threading.Lock()
        self._warejet_lock  = threading.Lock()
        self._subzone_lock  = threading.Lock()
        self._stop  = threading.Event()
        self._guard = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._active = False
        self._robot_stages: dict[str, int] = {}
        self._started_at: float = 0.0
        # Task pool — sshopy 수 + EXTRA_TASKS 만큼 큐 처리. 마지막 task 처리하는 sshopy가 서브존 스킵.
        self._task_counter_lock = threading.Lock()
        self._tasks_remaining = 0
        self._tasks_total = 0

    # ── public API ────────────────────────────────────────────────────────
    def is_active(self) -> bool:
        return self._active

    def get_status(self) -> dict:
        return {
            "active":  self._active,
            "elapsed": round(time.time() - self._started_at, 1) if self._active else 0.0,
            "robots": {
                rid: {
                    "stage":       stage,
                    "stage_label": DEMO_STAGE_LABELS.get(stage, "—"),
                }
                for rid, stage in self._robot_stages.items()
            },
        }

    def start(self, robot_ids: list[str]) -> tuple[bool, str]:
        with self._guard:
            if self._active:
                return False, "이미 진행 중인 데모 있음"

            valid: list[str] = []
            for rid in robot_ids:
                state = self.fleet._states.get(rid)
                if not state or state.type != "pinky":
                    return False, f"{rid}: pinky 로봇 아님/미등록"
                if not state.connected:
                    return False, f"{rid}: 연결 안됨"
                if not self.fleet._is_robot_idle(state):
                    return False, f"{rid}: 다른 시나리오 진행 중"
                valid.append(rid)

            if not valid:
                return False, "유효한 sshopy 없음"

            # ── 동적 dispatch: FrontJet에 가까운 sshopy부터 락 획득 시도 ──
            def _dist_to_frontjet(rid: str) -> float:
                pose = self.fleet._states[rid].pose
                if not pose:
                    return float("inf")
                return math.hypot(
                    pose["x"] - TRYON_FRONTJET["x"],
                    pose["y"] - TRYON_FRONTJET["y"],
                )
            valid.sort(key=_dist_to_frontjet)

            self._stop.clear()
            self._active = True
            self._started_at = time.time()
            self._threads = []
            self._robot_stages = {rid: DEMO_STAGE_QUEUED for rid in valid}
            for rid in valid:
                self.fleet._states[rid].inbound_demo_stage = DEMO_STAGE_QUEUED

            # Task pool 설정 — sshopy 수 + EXTRA_TASKS
            self._tasks_total = len(valid) + EXTRA_TASKS
            self._tasks_remaining = self._tasks_total

            for idx, rid in enumerate(valid):
                t = threading.Thread(
                    target=self._run_robot,
                    args=(rid, idx * START_STAGGER),
                    daemon=True,
                )
                t.start()
                self._threads.append(t)

            threading.Thread(target=self._wait_done, daemon=True).start()
            order = " → ".join(valid)
            print(f"[demo-inbound] 시작 (지정 우선순위): {order}, 총 task={self._tasks_total}")
            return True, f"입고 데모 시작 — 우선순위: {order}, 총 task={self._tasks_total}"

    def cancel(self) -> tuple[bool, str]:
        if not self._active:
            return False, "진행 중인 데모 없음"
        self._stop.set()
        # 진행 중 워커 스레드를 정지시킨 후, 각 sshopy를 자기 홈으로 복귀
        for rid in list(self._robot_stages.keys()):
            home = tryon_home(rid)
            ok = self.fleet.goal_pose(rid, home["x"], home["y"], home["theta"])
            print(f"[demo-inbound] 취소 → {rid} 홈 복귀 ({home['x']:.3f}, {home['y']:.3f}) ok={ok}")
        print("[demo-inbound] 취소 완료 — 모든 sshopy 홈 복귀 명령 발행")
        return True, "ok — 홈 복귀 명령 발행"

    # ── internal ──────────────────────────────────────────────────────────
    def _wait_done(self):
        for t in self._threads:
            t.join()
        self._active = False
        for rid in list(self._robot_stages.keys()):
            state = self.fleet._states.get(rid)
            if state:
                state.inbound_demo_stage = None
        print("[demo-inbound] 모든 sshopy 종료")

    def _set_stage(self, rid: str, stage: int):
        self._robot_stages[rid] = stage
        state = self.fleet._states.get(rid)
        if state:
            state.inbound_demo_stage = stage
        print(f"[demo-inbound] {rid} → stage {stage} ({DEMO_STAGE_LABELS.get(stage, '?')})")

    def _wait_arrival(self, rid: str, target: dict, sent_at: float) -> bool:
        """pose 거리 < THRESHOLD 이고 _nav_succeeded_at >= sent_at 이면 도착."""
        state = self.fleet._states.get(rid)
        if not state:
            return False
        deadline = time.time() + ARRIVAL_TIMEOUT
        while time.time() < deadline and not self._stop.is_set():
            if state.pose:
                dist = math.hypot(
                    state.pose["x"] - target["x"],
                    state.pose["y"] - target["y"],
                )
                nav_ok = state._nav_succeeded_at >= sent_at
                if dist < ARRIVAL_THRESHOLD and nav_ok:
                    return True
            time.sleep(ARRIVAL_POLL_INTERVAL)
        return False

    def _trigger_jetcobot(self, sshopy_ns: str, trigger_topic: str,
                          complete_topic: str, sim_seconds: float) -> bool:
        """
        sshopy 도착 신호 발행 → jetcobot 완료 신호까지 대기 (Bool 핸드셰이크).
        STUB 모드: rosbridge 없이 sim_seconds sleep로 폴백.
        """
        client = self.fleet._client
        if _ROS_STUB or client is None or not client.is_connected:
            print(f"[demo-inbound] {sshopy_ns} (STUB/no-bridge) {trigger_topic} sleep {sim_seconds}s")
            self._stop.wait(sim_seconds)
            return True

        full_trigger  = f"/{sshopy_ns}/{trigger_topic}"
        full_complete = f"/{sshopy_ns}/{complete_topic}"

        done = threading.Event()
        sub = roslibpy.Topic(client, full_complete, "std_msgs/Bool")

        def _on_complete(msg):
            if msg.get("data") is True:
                done.set()

        sub.subscribe(_on_complete)
        try:
            pub = roslibpy.Topic(client, full_trigger, "std_msgs/Bool")
            pub.advertise()
            time.sleep(0.2)  # advertise 정착 대기
            pub.publish(roslibpy.Message({"data": True}))
            print(f"[demo-inbound] {full_trigger} = True 발행 → {full_complete} 대기")

            deadline = time.time() + JETCOBOT_TIMEOUT
            while time.time() < deadline:
                if done.wait(0.5):
                    print(f"[demo-inbound] {full_complete} 수신 → 완료")
                    return True
                if self._stop.is_set():
                    return False
            print(f"[demo-inbound] {full_complete} timeout({JETCOBOT_TIMEOUT}s)")
            return False
        finally:
            try:
                sub.unsubscribe()
            except Exception:
                pass

    def _move_and_wait(self, rid: str, wp: dict) -> bool:
        sent_at = time.time()
        ok = self.fleet.goal_pose(rid, wp["x"], wp["y"], wp["theta"])
        if not ok:
            print(f"[demo-inbound] {rid} goal_pose 발행 실패 → ({wp['x']:.3f}, {wp['y']:.3f})")
            return False
        return self._wait_arrival(rid, wp, sent_at)

    def _wait_clear(self, rid: str, from_zone: dict,
                    buffer_dist: float = ZONE_BUFFER_DIST,
                    timeout: float = BUFFER_CLEAR_TIMEOUT) -> bool:
        """sshopy가 from_zone에서 buffer_dist 이상 멀어지면 True. timeout/cancel 시 False."""
        state = self.fleet._states.get(rid)
        if not state:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop.is_set():
            if state.pose:
                d = math.hypot(
                    state.pose["x"] - from_zone["x"],
                    state.pose["y"] - from_zone["y"],
                )
                if d >= buffer_dist:
                    print(f"[demo-inbound] {rid} buffer cleared ({d:.2f}m ≥ {buffer_dist}m)")
                    return True
            time.sleep(ARRIVAL_POLL_INTERVAL)
        return False

    def _claim_task(self) -> Optional[bool]:
        """Task pool에서 task 1개 가져오기. 마지막 task면 is_last=True. 남은 task 없으면 None."""
        with self._task_counter_lock:
            if self._tasks_remaining <= 0:
                return None
            self._tasks_remaining -= 1
            is_last = (self._tasks_remaining == 0)
            print(f"[demo-inbound] task 획득 — 남은 task={self._tasks_remaining}, is_last={is_last}")
            return is_last

    def _run_robot(self, rid: str, start_delay: float):
        """
        단일 sshopy 워커 — task pool에서 task 가져와서 사이클 수행, 끝나면 또 가져옴.
        남은 task 없으면 종료.
        """
        from fms.scenarios.traffic_manager import PRIORITY_INBOUND
        time.sleep(start_delay)
        # 트래픽 매니저에 우선순위 등록 (입고 = 3)
        tm = getattr(self.fleet, "traffic_mgr", None)
        if tm:
            tm.register(rid, PRIORITY_INBOUND)
        try:
            while not self._stop.is_set():
                is_last = self._claim_task()
                if is_last is None:
                    print(f"[demo-inbound] {rid} 모든 task 소진 — 종료")
                    return
                self._do_one_cycle(rid, is_last)
        finally:
            if tm:
                tm.unregister(rid)

    def _do_one_cycle(self, rid: str, is_last: bool):
        """
        단일 사이클: HOME → FrontJet → 창고존 → (서브존, is_last=False일 때만) → HOME.

        Lock 보유 패턴 — "buffer zone 통과까지 보유":
            FrontJet 락은 sshopy가 FrontJet에서 ZONE_BUFFER_DIST(0.4m) 이상 멀어지면 해제.
            창고존, 서브존 락도 동일 패턴.

        is_last=True: 창고존 작업 후 서브존 스킵하고 바로 홈으로 복귀.
        """
        if self._stop.is_set():
            return

        try:
            # ── leg 1: FrontJet ──────────────────────────────────────────
            self._frontjet_lock.acquire()
            try:
                if self._stop.is_set(): return
                self._set_stage(rid, DEMO_STAGE_TO_FRONTJET)
                if not self._move_and_wait(rid, TRYON_FRONTJET):
                    print(f"[demo-inbound] {rid} FrontJet 도착 실패/취소")
                    return
                self._set_stage(rid, DEMO_STAGE_FRONTJET_WORK)
                ok = self.fleet._ssh_exec("front_jet", self.fleet._SCRIPTS["inbound_load"])
                print(f"[demo-inbound] {rid} FrontJet 상차 {'완료' if ok else '실패(계속)'}")
                if self._stop.is_set(): return

                # 창고존 락 획득 — 점유 시 FrontJet 보유 상태로 대기
                self._set_stage(rid, DEMO_STAGE_WAIT_WAREJET)
                self._warejet_lock.acquire()
                warejet_held = True
                try:
                    if self._stop.is_set(): return
                    self._set_stage(rid, DEMO_STAGE_TO_WAREJET)
                    warejet_sent_at = time.time()
                    self.fleet.goal_pose(rid, TRYON_WAREJET["x"], TRYON_WAREJET["y"], TRYON_WAREJET["theta"])
                    # FrontJet에서 buffer 만큼 멀어지면 락 해제 (다음 sshopy 진입 허용)
                    self._wait_clear(rid, TRYON_FRONTJET)
                except Exception:
                    if warejet_held:
                        self._warejet_lock.release()
                        warejet_held = False
                    raise
            finally:
                self._frontjet_lock.release()

            # ── leg 2: 창고존 도착 대기 + 작업 (warejet_lock 보유 중) ─────
            try:
                if not self._wait_arrival(rid, TRYON_WAREJET, warejet_sent_at):
                    print(f"[demo-inbound] {rid} 창고존 도착 실패/취소")
                    return
                self._set_stage(rid, DEMO_STAGE_WAREJET_WORK)
                ok = self.fleet._ssh_exec("ware_jet", self.fleet._SCRIPTS["warehouse_store"])
                print(f"[demo-inbound] {rid} WareJet 적재 {'완료' if ok else '실패(계속)'}")
                if self._stop.is_set(): return

                if is_last:
                    # ── 마지막 sshopy: 서브존 스킵 → 바로 홈 ─────────────
                    print(f"[demo-inbound] {rid} 마지막 sshopy — 서브존 스킵, 바로 홈으로")
                    self._set_stage(rid, DEMO_STAGE_TO_HOME)
                    home = tryon_home(rid)
                    home_sent_at = time.time()
                    self.fleet.goal_pose(rid, home["x"], home["y"], home["theta"])
                    # 창고존에서 buffer 만큼 멀어지면 warejet_lock 해제
                    self._wait_clear(rid, TRYON_WAREJET)
                else:
                    # ── 일반 sshopy: 서브존 락 획득 후 서브존 이동 ─────
                    self._set_stage(rid, DEMO_STAGE_WAIT_SUBZONE)
                    self._subzone_lock.acquire()
                    subzone_held = True
                    try:
                        if self._stop.is_set(): return
                        self._set_stage(rid, DEMO_STAGE_TO_SUBZONE)
                        subzone_sent_at = time.time()
                        self.fleet.goal_pose(rid, WAREJET_SUBZONE["x"], WAREJET_SUBZONE["y"], WAREJET_SUBZONE["theta"])
                        # 창고존에서 buffer 만큼 멀어지면 warejet_lock 해제
                        self._wait_clear(rid, TRYON_WAREJET)
                    except Exception:
                        if subzone_held:
                            self._subzone_lock.release()
                            subzone_held = False
                        raise
            finally:
                self._warejet_lock.release()

            if is_last:
                # 마지막 sshopy는 바로 홈 도착 대기 (서브존 락 없음)
                if not self._wait_arrival(rid, tryon_home(rid), home_sent_at):
                    print(f"[demo-inbound] {rid} 홈 도착 실패/취소")
                    return
                self._set_stage(rid, DEMO_STAGE_DONE)
                return

            # ── leg 3: 서브존 도착 대기 + 홈 출발 (subzone_lock 보유 중) ──
            try:
                if not self._wait_arrival(rid, WAREJET_SUBZONE, subzone_sent_at):
                    print(f"[demo-inbound] {rid} 서브존 도착 실패/취소")
                    return
                if self._stop.is_set(): return
                self._set_stage(rid, DEMO_STAGE_TO_HOME)
                home = tryon_home(rid)
                home_sent_at = time.time()
                self.fleet.goal_pose(rid, home["x"], home["y"], home["theta"])
                # 서브존에서 buffer 만큼 멀어지면 락 해제
                self._wait_clear(rid, WAREJET_SUBZONE)
            finally:
                self._subzone_lock.release()

            # ── leg 4: 홈 도착 대기 (락 없음) ────────────────────────────
            if not self._wait_arrival(rid, tryon_home(rid), home_sent_at):
                print(f"[demo-inbound] {rid} 홈 도착 실패/취소")
                return

            self._set_stage(rid, DEMO_STAGE_DONE)
        except Exception as e:
            print(f"[demo-inbound] {rid} 예외: {e}")
