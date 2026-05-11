# 작업 일지 — 2026-05-07

브랜치: `feature/jaehoon-fms-taskupdate`

## 요약

FMS Task 구조 리팩토링과 다중 sshopy 입고 데모 시나리오(Scene 1 확장) 신규 구현.
커밋 1건(`3851d7f FMS Task UPDATE`) + 작업 중(WIP, 미커밋) 변경 다수.

---

## 1. 커밋된 작업 — `3851d7f FMS Task UPDATE` (11:26)

총 19개 파일, +1,338 / -212 lines.

### 1-1. Coordinator 패턴 도입 (진행 중)
- `services/main_server/fms/coordinator.py` (신규, 197 lines) — `RobotManager`에서 시나리오 로직을 분리하기 위한 Coordinator 컨테이너.
- `services/main_server/fms/robot_registry.py` (신규, 302 lines) — 로봇 상태/등록 정보 분리.
- `services/main_server/fms/resource_lock.py` (신규, 92 lines) — 공용 자원(zone) mutex 추상화.
- `services/main_server/fms/robot_manager.py` 대폭 정리 (+277 / -212).
- `services/main_server/fms/config.py` 갱신.

### 1-2. 시나리오 패키지화
- `services/main_server/fms/scenarios/__init__.py`
- `services/main_server/fms/scenarios/constants.py` — 공용 웨이포인트(`TRYON_WAREJET`, `TRYON_FRONTJET`, `TRYON_HOMES`) 분리.
- `services/main_server/fms/scenarios/tasks.py` (신규, 80 lines).

### 1-3. domain bridge 설정 추가
- `bridge_configs/sshopy1_bridge.yaml`, `sshopy1_to_robot.yaml`
- `bridge_configs/sshopy2_bridge.yaml`, `sshopy2_to_robot.yaml`
- `bridge_configs/sshopy3_bridge.yaml`, `sshopy3_to_robot.yaml`
- `bridge_configs/front_jet_bridge.yaml`, `ware_jet_bridge.yaml` 갱신
- `services/main_server/fms/cyclonedds.xml` (신규)
- `services/main_server/fms/start_domain_bridge.sh` (신규, 70 lines)

### 1-4. admin_ui
- `apps/admin_ui/src/App.jsx` (+163 lines) — Task 관련 UI 갱신.

---

## 2. 미커밋(WIP) 작업 — 다중 sshopy 입고 데모 + 트래픽 매니저

총 8개 파일, +404 / -9 lines.

### 2-1. 신규 파일

#### `services/main_server/fms/scenarios/inbound_demo.py` (444 lines)
다중 sshopy 입고 데모 오케스트레이터 (Scene 1 확장).
- 흐름: `HOME → FrontJet → 창고존(WareJet) → 창고 서브존 → HOME`
- 3개의 zone mutex(FrontJet/창고존/서브존)로 직렬화.
- FrontJet 좌표 기준 가까운 sshopy부터 락 획득 (동적 dispatch).
- 팔 작업은 SSH 미실행 — `FRONTJET_SIM_SECONDS`/`WAREJET_SIM_SECONDS` sleep 시뮬.
- `EXTRA_TASKS=1`: 홈 도착한 유휴 sshopy가 추가 task 수행하도록 task pool 운용.
- Stage 상수 40~49 (`DEMO_STAGE_QUEUED` ~ `DEMO_STAGE_DONE`).
- `InboundDemoOrchestrator` 클래스 — `start(robot_ids)` / `cancel()` / `get_status()`.

#### `services/main_server/fms/scenarios/traffic_manager.py` (214 lines)
sshopy 간 충돌 회피용 트래픽 매니저(yield-based).
- 우선순위: `1=customer_call`, `2=tryon`, `3=inbound`, `4=retrieval` (낮은 숫자=고우선).
- `YIELD_RADIUS=0.35m` 이내 진입 시 저우선 sshopy를 일시정지(`cmd_vel(0,0)` + `goal_pose(현재위치)`).
- 모든 고우선이 반경 밖으로 나가면 원래 goal 자동 재발행.
- `register/unregister/notify_goal/get_status` API.
- `inbound_demo`의 zone 락과 별개의 상위 충돌 회피 레이어.

### 2-2. 기존 파일 변경

#### `services/main_server/fms/main.py` (+55 lines)
신규 REST 엔드포인트:
- `POST /inbound_demo/start` — 다중 sshopy 입고 데모 시작 (기본 `["sshopy2","sshopy1","sshopy3"]`).
- `POST /inbound_demo/cancel` — 데모 취소(전 sshopy 홈 복귀).
- `GET  /inbound_demo/status` — 진행 상태 조회.
- `GET  /traffic/status` — 트래픽 매니저 상태 조회.
- `POST /traffic/dispatch` — sshopy 등록 + goal_pose 발행(테스트용).
- `POST /traffic/release` — 트래픽 매니저 등록 해제(테스트용).

#### `services/main_server/fms/robot_manager.py` (+42 / -9)
- `WAREJET_SUBZONE` 좌표 추가(`x=0.010, y=-0.038`).
- `_RobotState.inbound_demo_stage` 필드 신설.
- `to_dict()`에 `inbound_demo_stage` 노출.
- `_on_nav_status` — 시착 외 시나리오에서도 SUCCEEDED 시각(`_nav_succeeded_at`) 기록하도록 가드 제거.
- `_is_robot_idle()` — `inbound_demo_stage` 포함.
- `goal_pose(..., _traffic_internal=False)` — 외부 호출 시 트래픽 매니저에 `notify_goal` 통지.
- 모듈 끝에서 `TrafficManager`/`InboundDemoOrchestrator` 부착 (`fleet.traffic_mgr`, `fleet.inbound_demo`).

#### `services/main_server/fms/scenarios/constants.py` (+1)
`WAREJET_SUBZONE` 상수 추가.

#### `services/main_server/fms/bridge_configs/front_jet_bridge.yaml` (+19)
- Robot→Server: `/sshopy{1,2,3}/load_complete` (Bool)
- Server→Robot: `/sshopy{1,2,3}/arrived` (Bool, reversed)

#### `services/main_server/fms/bridge_configs/ware_jet_bridge.yaml` (+19)
- Robot→Server: `/sshopy{1,2,3}/warejet_unload_complete` (Bool)
- Server→Robot: `/sshopy{1,2,3}/warejet_arrived` (Bool, reversed)

#### `apps/admin_ui/src/App.jsx` (+265)
- `LOCATIONS`에 `warejet_subzone` 추가.
- `InboundDemoPanel` 컴포넌트 — 다중 sshopy 입고 데모 시작/취소 + 1초 폴링 상태 표시(stage 라벨 색상 매핑 포함).
- `TrafficTestPanel` 컴포넌트 — 우선순위/목적지 프리셋, 트래픽 매니저 dispatch/release 테스트 UI.

---

## 다음 작업

- 미커밋 변경(입고 데모 + 트래픽 매니저)은 통합 테스트 후 커밋 예정.
- 실기 환경에서 zone mutex와 트래픽 매니저 동시 동작 시나리오 검증 필요.
