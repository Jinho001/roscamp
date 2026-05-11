"""
입고 데모 v2 — RMF mutex group 기반 오케스트레이터.

설계 (v1 대비):
    - threading.Lock + 0.4m buffer (_wait_clear) 제거
    - mutex_groups (Phase 2) 로 zone 단위 직렬화
    - zones.yaml (Phase 1) 의 approach / center / exit waypoint 사용

Leg 패턴:
    [zone leg]
        acquire(zone) ─ 큐에 들어가서 락 획득까지 대기
        → approach 이동
        → center 이동
        → 작업 (SSH or STUB sleep)
        → exit 이동
        release(zone)

    [subzone leg — 입고 데모 마지막 leg]
        acquire(subzone) → subzone_approach → subzone_center → release(subzone)
        ※ subzone 은 종착이라 exit / 작업 없음

    [home leg]
        get_parking_spot(rid) → goal_pose

is_last task 는 subzone 스킵 후 바로 home.

v1 (inbound_demo.py) 은 그대로 보존되며, 이 모듈은 별도 인스턴스로 동작한다.
"""
import math
import os
import threading
import time
from typing import Optional

from fms.scenarios.mutex_group import mutex_groups
from fms.scenarios.traffic_manager import PRIORITY_INBOUND
from fms.scenarios.zones import load_zones, get_waypoint, get_parking_spot

_ROS_STUB = os.getenv("ROS_STUB", "0") == "1"


# ── stage 상수 (50번대 — v1의 40번대와 분리) ─────────────────────────────────
DEMO_STAGE_V2_QUEUED    = 50
DEMO_STAGE_V2_WAIT_LOCK = 51
DEMO_STAGE_V2_MOVING    = 52
DEMO_STAGE_V2_WORKING   = 53
DEMO_STAGE_V2_TO_HOME   = 54
DEMO_STAGE_V2_DONE      = 55
DEMO_STAGE_V2_FAILED    = 56

DEMO_STAGE_V2_LABELS = {
    DEMO_STAGE_V2_QUEUED:    "대기열",
    DEMO_STAGE_V2_WAIT_LOCK: "락 대기",
    DEMO_STAGE_V2_MOVING:    "이동 중",
    DEMO_STAGE_V2_WORKING:   "작업 중",
    DEMO_STAGE_V2_TO_HOME:   "홈 복귀 중",
    DEMO_STAGE_V2_DONE:      "완료",
    DEMO_STAGE_V2_FAILED:    "실패",
}

# ── 동작 파라미터 ──────────────────────────────────────────────────────────
ARRIVAL_POLL_INTERVAL = 0.2
LOCK_TIMEOUT          = 600.0   # I1: 120→600 (3대 시 3rd robot 대기 시간 ↑)
ARRIVAL_TIMEOUT       = 60.0
FRONTJET_SIM_SECONDS  = 3.0
WAREJET_SIM_SECONDS   = 4.0
START_STAGGER         = 0.2
EXTRA_TASKS           = 1

# ── leg 정의 — 일반 zone (frontjet → warejet) ────────────────────────────
LEGS = [
    {
        "zone":         "frontjet",
        "approach":     "frontjet_approach",
        "center":       "frontjet_center",
        "exit":         "frontjet_exit",
        "ssh_target":   "front_jet",
        "ssh_script":   "inbound_load",
        "sim_seconds":  FRONTJET_SIM_SECONDS,
    },
    {
        "zone":         "warejet",
        "approach":     "warejet_approach",
        "center":       "warejet_center",
        "exit":         "warejet_exit",
        "ssh_target":   "ware_jet",
        "ssh_script":   "warehouse_store",
        "sim_seconds":  WAREJET_SIM_SECONDS,
    },
]

# subzone 은 종착 — work 없음, exit는 subzone→home 회랑 진입점
SUBZONE_LEG = {
    "zone":     "subzone",
    "approach": "subzone_approach",
    "center":   "subzone_center",
    "exit":     "subzone_exit",
}


class InboundDemoV2Orchestrator:
    """
    RMF mutex group 기반 입고 데모.
    fleet (RobotManager) 에 의존:
        fleet._states[rid]      _RobotState (pose, _nav_succeeded_at)
        fleet._is_robot_idle()
        fleet.goal_pose()       Nav2 이동
        fleet.cmd_vel()         정지
        fleet._ssh_exec(host, script)
        fleet._SCRIPTS          dict
    """

    def __init__(self, fleet):
        self.fleet = fleet
        self._stop = threading.Event()
        self._guard = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._active = False
        self._started_at: float = 0.0
        self._robot_status: dict[str, dict] = {}
        self._task_counter_lock = threading.Lock()
        self._tasks_remaining = 0
        self._tasks_total = 0

    # ── public API ────────────────────────────────────────────────────────

    def is_active(self) -> bool:
        return self._active

    def get_status(self) -> dict:
        return {
            "active":          self._active,
            "elapsed":         round(time.time() - self._started_at, 1) if self._active else 0.0,
            "tasks_total":     self._tasks_total,
            "tasks_remaining": self._tasks_remaining,
            "robots":          {rid: dict(info) for rid, info in self._robot_status.items()},
            "mutex_groups":    mutex_groups.status(),
        }

    def start(self, robot_ids: list[str]) -> tuple[bool, str]:
        with self._guard:
            if self._active:
                return False, "이미 진행 중인 v2 데모 있음"

            valid: list[str] = []
            for rid in robot_ids:
                state = self.fleet._states.get(rid)
                if not state or state.type != "pinky":
                    return False, f"{rid}: pinky 아님/미등록"
                if not state.connected:
                    return False, f"{rid}: 연결 안됨"
                if not self.fleet._is_robot_idle(state):
                    return False, f"{rid}: 다른 시나리오 진행 중"
                valid.append(rid)

            if not valid:
                return False, "유효 sshopy 없음"

            # FrontJet center 좌표 기준 가까운 sshopy 부터 정렬
            frontjet = get_waypoint("frontjet_center")

            def _dist(rid: str) -> float:
                pose = self.fleet._states[rid].pose
                if not pose:
                    return float("inf")
                return math.hypot(pose["x"] - frontjet["x"], pose["y"] - frontjet["y"])

            valid.sort(key=_dist)

            self._stop.clear()
            self._active = True
            self._started_at = time.time()
            self._threads = []
            self._robot_status = {
                rid: {"stage": DEMO_STAGE_V2_QUEUED, "zone": None, "waypoint": None}
                for rid in valid
            }
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
            print(f"[demo-v2] 시작 — 우선순위: {order}, 총 task={self._tasks_total}")
            return True, f"입고 데모 v2 시작 — 우선순위: {order}, 총 task={self._tasks_total}"

    def cancel(self) -> tuple[bool, str]:
        if not self._active:
            return False, "진행 중인 v2 데모 없음"
        self._stop.set()
        # 즉시 정지
        for rid in list(self._robot_status.keys()):
            self.fleet.cmd_vel(rid, 0.0, 0.0)
        # 우리가 보유 중이던 mutex 만 강제 해제
        for group in ("frontjet", "warejet", "subzone"):
            owner = mutex_groups.status()[group]["owner"]
            if owner in self._robot_status:
                mutex_groups.force_release(group)
                print(f"[demo-v2] 취소 — mutex '{group}' 강제 해제 (was owner={owner})")
        # 홈 복귀
        for rid in list(self._robot_status.keys()):
            home = get_parking_spot(rid)
            if home:
                self.fleet.goal_pose(rid, home["x"], home["y"], home["theta"])
        return True, "취소 — 홈 복귀 명령 발행"

    # ── internal ──────────────────────────────────────────────────────────

    def _wait_done(self):
        for t in self._threads:
            t.join()
        self._active = False
        print("[demo-v2] 모든 sshopy 종료")

    def _set_status(self, rid: str, **kwargs):
        info = self._robot_status.get(rid, {})
        info.update(kwargs)
        self._robot_status[rid] = info
        stage = info.get("stage")
        zone = info.get("zone") or "-"
        wp = info.get("waypoint") or "-"
        print(f"[demo-v2] {rid} → stage {stage} "
              f"({DEMO_STAGE_V2_LABELS.get(stage, '?')}, zone={zone}, wp={wp})")

    def _claim_task(self) -> Optional[bool]:
        with self._task_counter_lock:
            if self._tasks_remaining <= 0:
                return None
            self._tasks_remaining -= 1
            is_last = (self._tasks_remaining == 0)
            print(f"[demo-v2] task 획득 — 남은={self._tasks_remaining}, is_last={is_last}")
            return is_last

    def _run_robot(self, rid: str, start_delay: float):
        time.sleep(start_delay)
        # E: traffic_manager 등록 — 다른 sshopy 와 0.35m 이내 접근 시 자동 yield
        tm = getattr(self.fleet, "traffic_mgr", None)
        if tm is not None:
            tm.register(rid, PRIORITY_INBOUND)
        try:
            while not self._stop.is_set():
                claim = self._claim_task()
                if claim is None:
                    print(f"[demo-v2] {rid} 모든 task 소진")
                    break
                ok = self._do_one_cycle(rid, is_last=claim)
                if not ok:
                    self._set_status(rid, stage=DEMO_STAGE_V2_FAILED)
                    break
        except Exception as e:
            print(f"[demo-v2] {rid} 예외: {e}")
            self._set_status(rid, stage=DEMO_STAGE_V2_FAILED)
        finally:
            if tm is not None:
                tm.unregister(rid)

    def _do_one_cycle(self, rid: str, is_last: bool) -> bool:
        for leg in LEGS:
            if not self._do_zone_leg(rid, leg):
                return False
        if not is_last:
            if not self._do_subzone_leg(rid):
                return False
        return self._do_home_leg(rid)

    def _do_zone_leg(self, rid: str, leg: dict) -> bool:
        zone = leg["zone"]
        self._set_status(rid, stage=DEMO_STAGE_V2_WAIT_LOCK, zone=zone, waypoint=None)
        if not mutex_groups.acquire(zone, rid, timeout=LOCK_TIMEOUT):
            print(f"[demo-v2] {rid} {zone} 락 timeout")
            return False
        try:
            if self._stop.is_set():
                return False
            self._set_status(rid, stage=DEMO_STAGE_V2_MOVING, waypoint=leg["approach"])
            if not self._move_and_wait(rid, get_waypoint(leg["approach"])):
                return False

            if self._stop.is_set():
                return False
            self._set_status(rid, waypoint=leg["center"])
            if not self._move_and_wait(rid, get_waypoint(leg["center"])):
                return False

            self._set_status(rid, stage=DEMO_STAGE_V2_WORKING)
            ok = self._do_work(leg["ssh_target"], leg["ssh_script"], leg["sim_seconds"])
            print(f"[demo-v2] {rid} {zone} work {'완료' if ok else '실패(계속)'}")
            if self._stop.is_set():
                return False

            if leg.get("exit"):
                self._set_status(rid, stage=DEMO_STAGE_V2_MOVING, waypoint=leg["exit"])
                if not self._move_and_wait(rid, get_waypoint(leg["exit"])):
                    return False
        finally:
            mutex_groups.release(zone, rid)
        return True

    def _do_subzone_leg(self, rid: str) -> bool:
        zone = SUBZONE_LEG["zone"]
        self._set_status(rid, stage=DEMO_STAGE_V2_WAIT_LOCK, zone=zone, waypoint=None)
        if not mutex_groups.acquire(zone, rid, timeout=LOCK_TIMEOUT):
            print(f"[demo-v2] {rid} {zone} 락 timeout")
            return False
        try:
            if self._stop.is_set():
                return False
            self._set_status(rid, stage=DEMO_STAGE_V2_MOVING, waypoint=SUBZONE_LEG["approach"])
            if not self._move_and_wait(rid, get_waypoint(SUBZONE_LEG["approach"])):
                return False

            if self._stop.is_set():
                return False
            self._set_status(rid, waypoint=SUBZONE_LEG["center"])
            if not self._move_and_wait(rid, get_waypoint(SUBZONE_LEG["center"])):
                return False

            # exit — subzone→home 회랑 진입점 (사용자 amcl 측정 path 따름)
            if SUBZONE_LEG.get("exit"):
                if self._stop.is_set():
                    return False
                self._set_status(rid, stage=DEMO_STAGE_V2_MOVING, waypoint=SUBZONE_LEG["exit"])
                if not self._move_and_wait(rid, get_waypoint(SUBZONE_LEG["exit"])):
                    return False
        finally:
            mutex_groups.release(zone, rid)
        return True

    def _do_home_leg(self, rid: str) -> bool:
        home = get_parking_spot(rid)
        if home is None:
            print(f"[demo-v2] {rid} parking_spot 없음 — 종료")
            self._set_status(rid, stage=DEMO_STAGE_V2_DONE, zone=None, waypoint=None)
            return True
        self._set_status(rid, stage=DEMO_STAGE_V2_TO_HOME, zone=None, waypoint=f"home_{rid}")
        ok = self._move_and_wait(rid, home)
        if ok:
            self._set_status(rid, stage=DEMO_STAGE_V2_DONE, waypoint=None)
        return ok

    def _move_and_wait(self, rid: str, wp: dict) -> bool:
        sent_at = time.time()
        ok = self.fleet.goal_pose(rid, wp["x"], wp["y"], wp["theta"])
        if not ok:
            print(f"[demo-v2] {rid} goal_pose 발행 실패 ({wp['x']:.2f}, {wp['y']:.2f})")
            return False
        return self._wait_arrival(rid, wp, sent_at)

    def _wait_arrival(self, rid: str, target: dict, sent_at: float) -> bool:
        state = self.fleet._states.get(rid)
        if not state:
            return False
        threshold = load_zones()["arrival"].get("threshold", 0.30)
        deadline = time.time() + ARRIVAL_TIMEOUT
        while time.time() < deadline and not self._stop.is_set():
            if state.pose:
                dist = math.hypot(state.pose["x"] - target["x"],
                                  state.pose["y"] - target["y"])
                nav_ok = state._nav_succeeded_at >= sent_at
                if dist < threshold and nav_ok:
                    return True
            time.sleep(ARRIVAL_POLL_INTERVAL)
        if not self._stop.is_set():
            print(f"[demo-v2] {rid} 도착 timeout (target={target['x']:.2f},{target['y']:.2f})")
        return False

    def _do_work(self, ssh_target: str, script_name: str, sim_seconds: float) -> bool:
        if _ROS_STUB:
            print(f"[demo-v2] STUB ssh skip: {ssh_target} {script_name}, sleep {sim_seconds}s")
            self._stop.wait(sim_seconds)
            return True
        try:
            return bool(self.fleet._ssh_exec(ssh_target, self.fleet._SCRIPTS[script_name]))
        except Exception as e:
            print(f"[demo-v2] ssh exec 실패: {e}")
            return False
