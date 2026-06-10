# 포트폴리오: Eye-in-Hand 비전 기반 협동로봇 Pick & Place 파이프라인

**김진호** · ROS2 로봇 비전 파이프라인 설계 및 구현

---

## 한눈에 보기

| 항목 | 내용 |
|------|------|
| **프로젝트** | MSS — 무인 신발 매장 관제·물류 자동화 시스템 |
| **내 역할** | 협동로봇 비전 파이프라인 전체 설계·구현 (개인 기여도 100%) |
| **기간** | 5주 (기획 → HIL 현장 테스트 완비) |
| **기술 스택** | ROS2 Jazzy · Python 3.12 · OpenCV 4.9+ · NumPy · PyMyCobot |
| **하드웨어** | MyCobot 280 (6-DOF 협동로봇) · USB 단안 RGB 카메라 |

| 📷 렌즈 왜곡 RMS | 📐 Hand-Eye 캘리브 잔차 | 🎯 파지 좌표 오차 | ⏱ 사이클 타임 | 🔁 초기 파지 검증 |
| :---: | :---: | :---: | :---: | :---: |
| **0.257 px** | **1.245 mm** | **< 0.95 mm** | **12.62 s** | **8회 연속 성공** |

---

## 1. 프로젝트 배경: 무엇을, 왜 만들었나

### 시스템 전체 그림

고객이 키오스크로 신발을 선택하면 AMR과 협동로봇이 자동으로 픽업·배달하는 완전 무인 신발 매장 시스템입니다. 저는 그 중 **협동로봇이 상자를 집어드는 비전 파이프라인 전체**를 담당했습니다.

```
고객 선택 → FMS → AMR 이동 → [협동로봇 비전 Pick & Place] → AMR 이동 → 선반 적재
                                        ↑
                                    내 기여 범위
```

### 핵심 제약 조건

이 프로젝트를 기술적으로 흥미롭게 만든 것은 제약 조건이었습니다.

- **Depth 카메라 없음**: 예산 제한으로 RGB 카메라 1대만 사용 가능
- **데이터셋 없음**: 딥러닝 기반 3D 포즈 추정 모델 학습 불가
- **팀 공유 로봇**: 원격 환경에서 알고리즘을 검증할 방법이 필요
- **5주 제한**: 설계·구현·현장 테스트를 모두 포함

이 제약들이 아래 기술 결정들을 이끌었습니다.

---

## 2. 내가 구현한 것 (6개 컴포넌트)

> **v1 / v2 구분**: 초기 구현(v1)은 모든 로직이 ROS2 노드에 통합된 배포판입니다. v2는 수학 코어와 ROS2 인터페이스를 분리해 독립 검증을 가능하게 한 리팩토링 버전입니다. 아래는 두 버전을 포함한 전체 구현 범위입니다.

비전 파이프라인을 구성하는 모든 컴포넌트를 처음부터 설계·구현했습니다.

| # | 컴포넌트 | 무엇을 해결했나 |
|---|---------|--------------|
| 1 | **UDP 영상 스트리밍** (`stream_sender.py`) | 카메라 → AI 서버 전송. JPEG quality=50으로 인코딩 후 단일 UDP 패킷 전송. 65KB 초과 시 quality를 10씩 낮춰 재압축. `--targets` 인자로 동일 프레임을 다수 수신지에 동시 전송 가능 |
| 2 | **OBB 검출 서버** (`cv_detect_server.py`) | 흰색 상자의 위치(cx, cy)·방향(θ)·크기 검출. LAB L채널 CLAHE 조명 정규화 → HSV 마스킹 → Morphology → minAreaRect. FastAPI HTTP 서버 + ROS2 토픽 발행 |
| 3 | **Hand-Eye 캘리브레이션** (결과: `vision_params.yaml`) | 카메라-로봇 간 상대 변환 행렬 실측. Tsai-Lenz AX=XB, 25포즈, RMS 1.245 mm. 결과 행렬이 YAML 파라미터로 노드에 주입됨 |
| 4 | **좌표 변환 코어** (`core/coord_transform.py`) | 2D 픽셀 → 3D 파지 좌표 복원. Ray-Plane 교점, ROS2 의존성 없는 순수 NumPy 구현 |
| 5 | **모션 제어** (`core/motion.py`) | Pick & Place 시퀀스 제어. `set_end_type(0/1)` 전환으로 Flange 좌표 읽기, Approach → Grasp → Retreat 패턴 |
| 6 | **ROS2 Action Server + 격리 검증 도구** (`nodes/vision_pick_place_node.py` + `scripts/verify.py`) | core 모듈 조합 Action Server. verify.py로 ROS2·로봇 없이 Step A(픽셀 스케일) / B(좌표 오차) / C(Yaw 오차) 독립 검증 |

---

## 3. 핵심 기술 도전 4가지

### 도전 1: Depth 센서 없이 3D 좌표 복원

**문제**: 단안 카메라로는 픽셀에서 3D 좌표를 구할 때 해가 무한히 많습니다. 카메라에서 뻗어나가는 광선(Ray) 위 어느 점이든 동일한 픽셀에 투영되기 때문입니다.

**해결: Ray-Plane Intersection**

"상자는 항상 알려진 높이 $Z_{surface}$ 위에 있다"는 물리적 구속 조건을 수학적으로 추가하면 광선과 평면의 교점이 유일하게 결정됩니다.

```
픽셀 (u, v)
    ↓  K_inv                      카메라 내부 행렬 역행렬
방향 벡터 v_c (카메라 좌표계)
    ↓  R_e^b · R_c^e              Hand-Eye + 순방향 기구학
방향 벡터 v_b (Base 좌표계)
    ↓  t = (Z_surface - cam_z) / v_b_z    평면 교점 파라미터
P_base = cam_origin + t · v_b   ←  파지 좌표 (mm)
```

**결과**: Depth 센서 없이 평균 오차 **0.95 mm 이내** 달성. `verify.py` Step B로 실측 위치 대비 추정 위치를 검증했습니다.

**트레이드오프**: 이 방법은 물체가 평면 위에 있을 때만 동작합니다. 신발 상자가 항상 트레이나 선반 위에 놓인다는 도메인 지식이 전제 조건입니다.

---

### 도전 2: Hand-Eye 캘리브레이션 파이프라인 직접 구축

**문제**: 카메라가 로봇 팔 끝단(EE)에 붙어 있어 로봇이 움직이면 카메라도 따라 움직입니다. "EE 기준으로 카메라가 어디에 어떤 방향으로 붙어 있는가($T_c^e$)"를 실측으로 구해야만 3D 좌표 변환이 가능합니다.

**해결**: AX = XB 연립방정식 (Tsai-Lenz)

```
로봇을 N개 자세로 이동할 때마다:
  A_i = T_base2ee   (관절 각도에서 계산)
  B_i = T_cam2board (체커보드 solvePnP로 계산)

N개 (A, B) 쌍 → cv2.calibrateHandEye(TSAI) → T_ee2cam (고정 변환 행렬)
```

**왜 TCP가 아닌 Flange 기준인가**: TCP는 장착된 공구에 따라 달라지는 가변값입니다. Flange는 공구 교체와 무관한 물리적 고정점이므로, `T_flange2cam`은 공구가 바뀌어도 재캘리브레이션 없이 그대로 유효합니다. TCP 기준으로 캘리브레이션하면 그리퍼 교체 시마다 재수행이 필요합니다.

**수집 전략**: 실제 Pick 동작이 observe 자세 근방에서 일어나므로, 그 영역에서 ±30~50 mm / ±15° 소변위 포즈 25개를 집중 수집했습니다. 넓게 분산된 포즈보다 실운용 영역에서의 정밀도가 중요하다고 판단했습니다.

**결과**: RMS **1.245 mm** (기준 5 mm 이하 달성). 그리퍼 파지 여유 공간(20 mm) 대비 6% 수준의 오차입니다.

---

### 도전 3: 타이밍 동기화 — 이동 중 프레임 배제

**문제**: 로봇이 이동하는 동안 카메라도 함께 움직입니다. 이동 중에 캡처된 OBB 검출 결과로 좌표를 계산하면 오파지가 발생합니다. 비전(비동기 연속 스트림)과 로봇 모션(동기 시리얼)을 타이밍 레이스 없이 연결해야 했습니다.

**v2 해결: 순차 실행으로 타이밍 보장**

v2에서는 `_run_pick()`이 순차 실행으로 이 문제를 해결합니다.

```python
# nodes/vision_pick_place_node.py — _run_pick()

# 1. observe_pose로 이동 (blocking: 완전히 정지할 때까지 대기)
self._motion.move_to(observe_pose)

# 2. 로봇이 정지한 현재 위치의 실제 flange 좌표 읽기
flange = self._motion.get_flange_coords()

# 3. 실측 flange 좌표로 T_base2cam 재계산
self._transformer.update_pose(flange)

# 4. 정지 후 첫 유효 OBB 검출 (HTTP 폴링)
obb = self._detector.wait_for_detection(timeout=10.0)

# 5. Pick 실행
pt = self._transformer.pixel_to_base(obb['cx'], obb['cy'], z_surface)
self._motion.pick(pt[0] + offset[0], pt[1] + offset[1], z_surface, yaw, profile)
```

`move_to()`가 완전히 반환된 이후에만 `wait_for_detection()`이 호출되므로, 이동 중 프레임은 자동으로 배제됩니다.

**참고 — v1 배포판**: v1에서는 `coord_transform_node`가 별도 ROS2 노드로 분리되어 있었고, `SetBool` 서비스로 게이팅을 명시적으로 제어했습니다. v2에서 단일 노드의 순차 실행으로 단순화하면서 같은 타이밍 보장을 더 적은 코드로 달성했습니다. (v1→v2 아키텍처 진화는 도전 4 참고)

**결과**: 이동 중 잔여 프레임으로 인한 오파지 제거. 결정론적 제어 흐름 확보.

---

### 도전 4: 로봇 없이 알고리즘 검증 — Clean Architecture

**문제**: 현장 로봇은 팀 공유 자원이고, 원격 환경에서는 수학 로직 하나를 수정할 때마다 수 분씩 대기해야 했습니다. v1에서는 좌표 변환 코드가 ROS2 노드 내부에 묶여 있어 `import rclpy` 자체가 실패하면 어떤 검증도 불가능했습니다.

**해결**: Core/Node 분리 (v2)

```
jetcobot_vision_v2/
├── core/                   ← ROS2 의존성 0
│   ├── coord_transform.py  # 순수 NumPy 수학
│   ├── detector.py         # HTTP 클라이언트
│   └── motion.py           # PyMyCobot 시리얼
└── nodes/                  ← core를 조합하는 얇은 ROS2 래퍼
    └── vision_pick_place_node.py
```

`scripts/verify.py`는 `core/`를 직접 import해 Step A(픽셀 스케일), Step B(좌표 오차), Step C(Yaw 오차)를 ROS2 없이, 로봇 팔 이동 없이 측정합니다. 카메라와 CV 서버만 있으면 실행 가능합니다.

**효과**:

| | v1 ROS2 모놀리식 | v2 Core/Node 분리 |
|---|---|---|
| **수정 → 검증** | 로봇 연결 + ROS2 부팅 (수 분) | `python verify.py` 단독 실행 (수 초) |
| **단위 테스트** | 불가 (rclpy 의존) | 가능 |
| **C++ 포팅 범위** | 전체 재작성 | `core/`만 |
| **디버깅 사이클** | 로봇 이동 + ROS2 부팅 필수 | **ROS2 없이, 로봇 팔 이동 없이 카메라만으로 검증 가능** |

---

## 4. HIL 트러블슈팅: 두 가지 체계적 오차 해결

통합 테스트에서 발견된 오차들을 추측 없이 격리 스크립트로 원인을 확정했습니다.

### 오차 1: Z축 110 mm — z_surface 측정 시 그리퍼 길이 미반영

로봇이 상자보다 정확히 110 mm 높은 공중에서 파지를 시도했습니다.

**원인 분석**: `z_surface`(상자 표면의 Base frame 높이)를 측정할 때, 그리퍼 tip을 상자 상단에 접촉시킨 상태에서 Flange 좌표를 읽었습니다. Flange는 그리퍼 tip보다 그리퍼 길이(110 mm)만큼 위에 있으므로, `z_surface = 실제 표면 높이 + 110 mm`로 과대 측정됐습니다. Ray-Plane 교점 공식에 이 값이 입력되면 모든 3D 파지 좌표가 110 mm 높게 계산됩니다.

```
z_surface 오측정 예시:
  실제 상자 표면 (base frame Z): +10 mm
  Flange 좌표 읽기 결과:         +10 + 110(그리퍼 길이) = +120 mm
  → Ray-Plane: t = (120 - cam_z) / ray_z  → 모든 파지 좌표 110 mm 상승
```

**보정 과정**:
1. **1차**: Flange 측정값에서 그리퍼 길이(110 mm)를 수동으로 차감해 `z_surface` 보정
2. **개선**: `set_end_type(1)` (TCP 모드)로 전환 후 측정 → 그리퍼 tip 좌표 직접 획득, 수동 차감 불필요

**결과**: Z축 오차 110 mm → **0 mm**

---

### 오차 2: Y축 16.3 mm 편향 — 캘리브 bias + 조립 공차

X축은 정확한데 Y 방향으로만 일정하게 16.3 mm 치우쳤습니다.

**원인 분석**: `verify.py` Step B로 상자를 알려진 위치(x=150, y=0 mm)에 놓고 추정값과 실측값을 비교 → Y 오차 16.3 mm 재현 확인. 일관적 편향이므로 (1) Tsai-Lenz 과결정 방정식의 local minima 수렴, (2) 카메라 브래킷 기구 조립 공차 누적 중 하나 또는 둘 다로 판단했습니다.

**트레이드오프 결정**: 동일 조건에서 반복 측정했을 때 편향이 16.3 mm로 안정적으로 재현됐습니다. 일관성 있는 편향이므로 정적 오프셋 보정이 가장 실용적이라고 판단했습니다.

**보정**: `pick_place_profiles.yaml`에 `pick_offset_mm: [0, -16.3, 0]` 추가.

**결과**: Y축 오차 16.3 mm → **0.95 mm 이하**

---

## 5. 기술 의사결정 요약

| 결정 | 선택 | 근거 |
|------|------|------|
| 3D 좌표 추출 | 단안 핀홀 역투영 | Depth 카메라 없이 0.95 mm 오차 실측. 평면 구속 조건이 도메인에서 보장됨 |
| 영상 전송 | UDP 단일 패킷 | 실측 평균 74.8 ms. ROS2 Image Topic은 직렬화·QoS 오버헤드로 더 높은 레이턴시 예상 |
| Hand-Eye 캘리브 | Tsai-Lenz | 캘리브레이션 잔차 1.245 mm. Flange 기준으로 수행해 그리퍼 교체 시에도 재수행 불필요 |
| 소프트웨어 구조 | Core/Node 분리 | ROS2 없이 `verify.py`로 수학 로직 독립 검증 가능. 로봇 팔 이동 없이 카메라·CV 서버만으로 실행 |
| 물체 검출 | OpenCV HSV + OBB | 흰색 단색 상자 환경에서 딥러닝보다 빠르고, HSV 파라미터 직관적 조정 가능 |
| 로봇 통신 | PyMyCobot 시리얼 | ROS2 Action 오버헤드 없이 단순·빠른 제어. 팀 내 기존 사용 라이브러리 |

---

## 6. 정량 결과 전체

| 지표 | 결과 | 측정 방법 | 허용 기준 |
|------|:---:|---------|:---:|
| 렌즈 왜곡 Reprojection RMS | **0.257 px** | Zhang's Method, 8×5 체커보드 30장 | < 0.5 px |
| Hand-Eye 캘리브레이션 잔차 | **1.245 mm** | Tsai-Lenz 캘리브 데이터 기반 잔차 (동일 포즈 역산) | < 5.0 mm |
| 파지 좌표 평균 오차 | **< 0.95 mm** | verify.py Step B 실측 | < 5.0 mm |
| 비전 처리 지연 | 평균 **74.8 ms** / 최대 384.6 ms | --measure-cycle 10회 | — |
| Pick & Place 사이클 타임 | 평균 **12.62 s** | --measure-cycle 10회 | — |
| 초기 파지 검증 | **8회 연속 성공** (n=8) | 임의 위치 배치 8회 연속 실파지 | — |

---

## 7. 시스템 아키텍처 상세

### v2 데이터 흐름 (본 저장소 기준)

```
[카메라 (USB)]
  stream_sender.py
  JPEG quality=50 인코딩, 65KB 초과 시 quality 자동 감소
      │ 단일 UDP 패킷 (65000 bytes 이하)
      ▼
[AI 서버 PC]
  cv_detect_server.py
  LAB CLAHE → HSV 마스킹 → Morphology → minAreaRect
  → OBB (cx, cy, w, h, θ, confidence)
      │ HTTP GET /latest
      ▼
[ROS2 노드 — 로봇 PC]
  vision_pick_place_node.py
    1. move_to(observe_pose)               [blocking]
    2. get_flange_coords()                 [Flange 좌표 읽기]
    3. transformer.update_pose(flange)     [T_base2cam 재계산]
    4. detector.wait_for_detection()       [HTTP 폴링]
    5. pixel_to_base(cx, cy, z_surface)    [Ray-Plane 교점]
    6. motion.pick(x, y, z, yaw, profile)
      │ PyMyCobot 시리얼
      ▼
[MyCobot 280]
  send_coords() / set_gripper_value()
```

### v2 ROS2 인터페이스

```
토픽 (Subscribe)
  /jetcobot/cmd/pick    std_msgs/String  — location 문자열
  /jetcobot/cmd/place   std_msgs/String  — location 문자열

토픽 (Publish)
  /jetcobot/result/pick   std_msgs/String  — "success" | "fail"
  /jetcobot/result/place  std_msgs/String  — "success" | "fail"

Action Server
  /vision_pick    VisionPick
  /vision_place   VisionPlace
```

> **v1 배포판 차이**: v1에서는 `coord_transform_node`가 별도 ROS2 노드로 존재했으며, `cv_detect_server`가 `/detect_bridge_node/obb_boxes` 토픽을 발행하면 `coord_transform_node`가 구독해 PickPoint로 변환했습니다. `vision_pick_place_node`는 `/coord_transform_node/enable`(SetBool), `/coord_transform_node/update_pose` 서비스로 게이팅을 제어했습니다.

---

## 8. 향후 개선 방향

5주 제약에서 우선순위는 '성공률 100% 달성'이었습니다. 다음 항목들은 시스템의 한계를 명확히 인식하고 다음 단계로 미룬 것입니다.

### C++ 포팅 (최우선)

**근거**: 평균 74.8 ms는 허용 범위지만, 최대 384.6 ms가 발생합니다. `core/`를 Eigen + OpenCV C++ API로 재작성하면 레이턴시를 **20 ms 이내**로 줄이고 편차도 대폭 감소할 것으로 예상합니다. Clean Architecture 덕분에 `core/`만 재작성하면 됩니다.

### Visual Servoing (고도화)

**근거**: 현재는 개루프(Open-loop) 제어입니다. observe 자세에서 한 번 좌표를 계산하고 그대로 이동합니다. 카메라 피드백 기반 폐루프 제어로 전환하면 성공률을 더 극한 조건에서도 유지할 수 있습니다.

### ROS2 Lifecycle Node

**근거**: 현재 게이팅은 순차 코드 흐름에 의존합니다. ROS2 표준 상태 머신(`unconfigured` → `inactive` → `active`)을 따르면 노드 재시작 시 안전한 초기화와 더 명시적인 상태 관리가 가능합니다.

---

## 참고 문서

| 문서 | 내용 |
|------|------|
| [docs/case_study_mss.md](docs/case_study_mss.md) | STAR 기법 기반 엔지니어링 케이스 스터디 |
| [docs/vision_pipeline_tech_doc.md](docs/vision_pipeline_tech_doc.md) | Zhang's Method / Tsai-Lenz 수학적 배경 |

---

*작성일: 2026-06-10*
