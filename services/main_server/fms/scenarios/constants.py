"""시나리오 공용 상수 — 웨이포인트, stage 번호, 라벨."""
import math


def _q_to_theta(oz: float, ow: float) -> float:
    return 2.0 * math.atan2(oz, ow)


# ── 공용 웨이포인트 ──────────────────────────────────────────────────
TRYON_WAREJET = {"x": -0.003, "y": 0.160, "theta": _q_to_theta(0.026, 1.000)}
TRYON_FRONTJET = {"x": 0.720, "y": 0.477, "theta": _q_to_theta(0.686, 0.727)}
WAREJET_SUBZONE = {"x": 0.010, "y": -0.038, "theta": _q_to_theta(0.025, 1.000)}

# 핑키별 홈위치 (sshopy1=1번핑기, sshopy2=2번핑키, sshopy3=3번핑키)
TRYON_HOMES = {
    "sshopy1": {"x": 1.002, "y": 0.077, "theta":  0.919},   # 핑키 초기 위치 (launch initial_pose)
    "sshopy2": {"x": 1.026, "y": 0.679, "theta": -0.810},
    "sshopy3": {"x": 1.670, "y": 0.398, "theta":  3.132},
}


def tryon_home(robot_id: str) -> dict:
    """robot_id에 해당하는 핑키 홈위치 반환."""
    return TRYON_HOMES[robot_id]


# 시착존 1~4
TRYZONES = {
    1: {"x": 1.047, "y": 0.136, "theta": _q_to_theta(0.708, 0.706)},
    2: {"x": 1.367, "y": 0.268, "theta": _q_to_theta(1.000, 0.002)},
    3: {"x": 1.217, "y": 0.550, "theta": _q_to_theta(-0.714, 0.700)},
    4: {"x": 0.881, "y": 0.431, "theta": _q_to_theta(-0.020, 1.000)},
}

# 배달 시나리오
WAYPOINTS = {
    0: {"x": 0.264, "y": 0.509, "theta": 1.674},
    1: {"x": 0.918, "y": 0.426, "theta": 1.655},
    2: {"x": 1.086, "y": 0.081, "theta": -0.362},
}
ARRIVAL_THRESHOLD = 0.3
ARRIVAL_COOLDOWN = 5.0

# ── 시착 시나리오 (Scene 2) stage ────────────────────────────────────
TRYON_STAGE_TO_WAREJET = 10
TRYON_STAGE_TO_TRYZONE = 11
TRYON_STAGE_AT_TRYZONE = 12
TRYON_STAGE_TO_FRONTJET = 13
TRYON_STAGE_TO_HOME = 14
TRYON_STAGE_AT_WAREJET = 15

# ── 회수 시나리오 (Scene 4) stage ────────────────────────────────────
RETRIEVAL_WAYPOINTS = {
    "entrance_counter": TRYON_FRONTJET,
    "warehouse": TRYON_WAREJET,
    # "home"은 robot_id별로 다르므로 tryon_home(robot_id)를 사용한다.
}

RETRIEVAL_STAGE_TO_ENTRANCE = 20
RETRIEVAL_STAGE_FRONTJET_LOAD = 21
RETRIEVAL_STAGE_IDENTIFY = 22
RETRIEVAL_STAGE_TO_WAREHOUSE = 23
RETRIEVAL_STAGE_WAREJET_STORE = 24
RETRIEVAL_STAGE_DB_RESTORE = 25
RETRIEVAL_STAGE_TO_HOME = 26

RETRIEVAL_STAGE_LABELS = {
    RETRIEVAL_STAGE_TO_ENTRANCE: "입구 카운터 이동 중",
    RETRIEVAL_STAGE_FRONTJET_LOAD: "FrontJet 상차 중",
    RETRIEVAL_STAGE_IDENTIFY: "상품 식별 대기",
    RETRIEVAL_STAGE_TO_WAREHOUSE: "창고 이동 중",
    RETRIEVAL_STAGE_WAREJET_STORE: "WareJet 적재 중",
    RETRIEVAL_STAGE_DB_RESTORE: "DB 복구/task 종료 대기",
    RETRIEVAL_STAGE_TO_HOME: "홈 복귀 중",
}

RETRIEVAL_TIMEOUT = 300

# ── 입고 시나리오 (Scene 1) stage ────────────────────────────────────
INBOUND_WAYPOINTS = {
    "frontjet": TRYON_FRONTJET,
    "warehouse": TRYON_WAREJET,
    # "home"은 robot_id별로 다르므로 tryon_home(robot_id)를 사용한다.
}

INBOUND_STAGE_TO_FRONTJET = 30
INBOUND_STAGE_FRONTJET_LOAD = 31
INBOUND_STAGE_TO_WAREHOUSE = 32
INBOUND_STAGE_SCAN_WAIT = 33
INBOUND_STAGE_WAREJET_STORE = 34
INBOUND_STAGE_TO_HOME = 35

INBOUND_STAGE_LABELS = {
    INBOUND_STAGE_TO_FRONTJET: "입고 위치 이동 중",
    INBOUND_STAGE_FRONTJET_LOAD: "FrontJet 상차 중",
    INBOUND_STAGE_TO_WAREHOUSE: "창고 이동 중",
    INBOUND_STAGE_SCAN_WAIT: "바코드 스캔/DB 갱신 대기",
    INBOUND_STAGE_WAREJET_STORE: "WareJet 적재 중",
    INBOUND_STAGE_TO_HOME: "홈 복귀 중",
}

INBOUND_TIMEOUT = 300


# ── [sshopylcd연동] 안내 시나리오 (Scene 5) stage ──────────────────────────────
# 배달(0~2)·시착(10~15)·회수(20~26)·입고(30~35) 와 충돌을 피하기 위해 40번대 사용.
# SShopy LCD UI 에서 고객이 상품 위치 안내를 요청하면, 백엔드가 해당 진열대
# (임의 좌표 GUIDE_DEMO_TARGET) 로 SShopy 를 이동시키고, 도착 후 LCD 가 polling 으로
# 완료를 감지하면 안내 완료 메시지를 표시. 사용자가 '안내 종료' 를 누르면 홈으로 복귀.

GUIDE_STAGE_TO_SHELF = 40  # 진열대 이동 중
GUIDE_STAGE_AT_SHELF = 41  # 진열대 도착 — 고객 접근 / 안내 종료 대기
GUIDE_STAGE_TO_HOME  = 42  # 홈 복귀 중

GUIDE_STAGE_LABELS = {
    GUIDE_STAGE_TO_SHELF: "진열대 이동 중",
    GUIDE_STAGE_AT_SHELF: "진열대 도착 — 안내 종료 대기",
    GUIDE_STAGE_TO_HOME:  "홈 복귀 중",
}

# [sshopylcd연동] 데모 단계 임의 좌표.
# TODO(실로봇테스트): 실제 매장의 진열대 위치로 교체. shoe_id 별 진열대 좌표 매핑이
# 정의되면 robot_manager.start_guide() 에서 shoe_id → 좌표 lookup 으로 교체.
GUIDE_DEMO_TARGET = {"x": 0.918, "y": 0.426, "theta": 1.655}

GUIDE_TIMEOUT = 300
