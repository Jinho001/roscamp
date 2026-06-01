# Eye-in-Hand 비전 기반 로봇 Pick & Place 파이프라인 구현기

> **한 줄 요약:** USB 카메라 + YOLO OBB 검출 + 핀홀 역산으로 로봇 팔이 임의 위치의 상자를 자율적으로 파지하는 파이프라인을 설계하고, 단계별 검증 도구로 정확도를 측정·보정했습니다.

---

## 배경

무신사 매장 내 자율주행 로봇 시스템을 개발하는 팀 프로젝트에 참여했습니다.
고객이 시착품을 선택하면, AMR(자율주행 로봇)과 ARM 로봇이 협력해 상품을 자동으로 전달하는 시스템입니다.

이 중 저는 **FrontJet(ARM 로봇)의 비전 기반 Pick & Place 파이프라인** 전담을 맡았습니다.
로봇이 테이블 위 임의의 위치에 놓인 상자를 카메라로 인식하고 자율적으로 집어드는 기능입니다.

---

## 시스템 구성

```
[USB 카메라 (Eye-in-Hand)]
        ↓ UDP 스트림
[HSV OBB 검출 서버 (cv_detect_server)]
  - 흰 상자를 HSV 필터로 이진화
  - OpenCV minAreaRect로 OBB(최소 외접 직사각형) 추출
  - cx, cy, w, h, theta 반환
        ↓ HTTP
[좌표 변환 (CoordTransformer)]
  - 픽셀 (cx, cy) → base_link 3D 좌표 (mm)
  - Ray-Plane 교점 알고리즘
        ↓
[Pick & Place (MotionController)]
  - pymycobot MyCobot280 제어
  - Approach → Target → Grasp → Retreat
```

**하드웨어:**
- 로봇: MyCobot 280 (6축 ARM, 최대 reach 280mm)
- 카메라: USB 캠 (Eye-in-Hand, flange 장착)
- 컨트롤러: Raspberry Pi 4
- OS/미들웨어: Ubuntu 24.04 + ROS2 Jazzy

---

## 핵심 설계 결정: core / ROS2 분리 구조

초기 레거시 코드는 좌표 변환, 모션 제어, ROS2 인터페이스가 하나의 클래스에 혼재되어 있었습니다.
이로 인해 ROS2 없이는 단순 수식 하나도 테스트할 수 없었고, 디버깅이 극도로 어려웠습니다.

**새 패키지 `jetcobot_vision_v2`** 에서는 다음 원칙으로 재설계했습니다:

```
core/                        ← 순수 Python, ROS2 의존성 없음
  coord_transform.py         ← 픽셀 → 3D 좌표 변환
  detector.py                ← OBB 검출 HTTP 클라이언트
  motion.py                  ← pymycobot Pick/Place 제어
nodes/
  vision_pick_place_node.py  ← ROS2 Action Server (core 조합만)
scripts/
  verify.py                  ← 단계별 검증 도구 (ROS2 불필요)
```

**효과:** `python3 scripts/verify.py` 한 줄로 ROS2 빌드 없이 파이프라인 전체를 검증할 수 있게 되었습니다.

---

## 좌표 변환 수식

카메라 픽셀 좌표를 로봇 base_link 기준 3D 좌표로 변환하는 핵심 수식입니다.

### Hand-Eye 캘리브레이션

카메라가 flange에 장착되어 있으므로, 로봇이 움직일 때마다 카메라 위치도 변합니다.
이를 처리하기 위해 **Hand-Eye 캘리브레이션**으로 `T_flange2cam` (4×4 변환 행렬)을 구했습니다.

- 캘리브 방식: Tsai (OpenCV `calibrateHandEye`)
- 캘리브 자세: 25자세 이상
- 결과 RMS: **0.2571px** (매우 정확)

### T_base2cam 계산

```python
def update_pose(self, flange_coords):
    # flange 6DoF → T_base2flange
    R = euler_deg_to_R(rx, ry, rz)
    T_base2flange = make_T(R, t)
    # T_base2cam = T_base2flange @ T_flange2cam
    self._T_base2cam = T_base2flange @ self._T_ee2cam
```

매 관측 직전 `get_flange_coords()`로 실제 flange 좌표를 읽어 `T_base2cam`을 갱신합니다.
이를 통해 로봇의 반복 정밀도 오차(±0.5mm)를 보정합니다.

### Ray-Plane 교점 (픽셀 → 3D)

Z 고정값(상자 상단면 높이)을 평면으로 두고, 카메라 광선과의 교점을 구합니다.

```python
def pixel_to_base(self, cx, cy, z_surface_mm):
    ray_c = K_inv @ [cx, cy, 1]          # 카메라 좌표계 방향벡터
    ray_b = R @ ray_c                     # base_link 좌표계로 변환
    t = (z_surface - origin_z) / ray_b_z # 평면까지의 거리
    point = origin + t * ray_b            # 교점
```

---

## 발견하고 수정한 버그들

파이프라인을 구현하면서 찾아낸 핵심 버그들입니다.

### 1. TCP / Flange 좌표계 혼재 버그

**문제:**
레거시 코드에서 `update_pose()`에 TCP 좌표를 그대로 넘기고 있었습니다.
Hand-Eye 캘리브는 `set_end_type(0)` (flange 모드)에서 수행했는데,
운용 시에는 `set_end_type(1)` (TCP 모드) 좌표를 넘기면서 카메라 위치 계산이 틀렸습니다.

```
TCP 좌표: [-58.5, -181.6, 154.5]  ← observe_pose (이동 명령용)
flange 좌표: [-60.5, -165.0, 265.0]  ← T_base2cam 계산에 사용해야 할 값
```

z 좌표만 봐도 154.5mm vs 265.0mm — **110mm 차이** (tcp_offset_z 그대로)

**수정:**
`get_flange_coords()`를 항상 `set_end_type(0)` 상태에서 호출하도록 통일하고,
`update_pose()`는 반드시 flange 좌표만 받도록 설계했습니다.

### 2. z_surface_mm 오설정

**문제:**
상자 상단면 Z 높이를 임의로 설정했더니 픽셀 스케일 역산값이 실제 상자 크기와 달랐습니다.

**측정 방법:**
그리퍼를 상자 상단면에 실제로 터치한 뒤, `set_end_type(0)`으로 flange Z 좌표를 읽었습니다.
```
flange_z = 129.3mm, tcp_offset_z = 110mm
→ z_surface = 129.3 - 110 = 19.3mm ≈ 25.5mm (Step A 검증으로 정밀화)
```

### 3. 실측 좌표계 불일치

**문제:**
Step B 검증 시 `--real-x`, `--real-y`를 그리퍼로 상자를 터치해 측정했는데,
터치 당시 그리퍼가 observe_pose와 다른 방향(rz=91.58°)이었습니다.
→ 카메라 방향이 달라서 실측값 자체가 의미없었음

**수정:**
observe_pose 자세(rz≈-179.5°)를 유지한 채로 XY만 이동해 상자에 터치해 실측값을 다시 측정했습니다.

---

## 단계별 검증 (verify.py)

ROS2 없이 파이프라인 각 단계를 독립적으로 검증하는 도구를 만들었습니다.

```bash
python3 scripts/verify.py \
  --config config/params_front_jet.yaml \
  --server http://192.168.1.4:8081 \
  --box-w 33 --box-h 25 \
  --real-x -47.3 --real-y -200.9 \
  --robot --pick
```

### Step A — 픽셀 스케일 검증

OBB w/h 픽셀값을 실제 mm로 역산해 실제 상자 크기와 비교합니다.
z_surface_mm 설정이 맞는지 확인하는 용도입니다.

```
OBB 검출:    w=159px  h=115px
카메라 높이: 235.6mm  |  d_z: 210.1mm
추정 크기:   33.72mm × 24.98mm
실제 크기:   33.0mm × 25.0mm
오차:        가로 +0.72mm / 세로 -0.02mm  ✅
```

### Step B — 중심 좌표 절대 오차

픽셀 → 3D 변환 결과를 실제 상자 위치(그리퍼 터치 실측)와 비교합니다.

```
변환 결과: x=-48.25mm  y=-217.17mm
실측 위치: x=-47.3mm   y=-200.9mm
오차:      Δx=-0.95mm  Δy=-16.27mm  총=16.30mm
```

y축 -16.27mm 체계적 오차 발견 → `pick_offset_mm`으로 보정

### Step C — Yaw 방향 검증

OBB theta를 base_link yaw로 변환한 값을 실제 상자 각도와 비교합니다.

---

## 보정 과정

### y축 16mm 오차 원인 분석

```
T_ee2cam 번역벡터: x=3.98, y=-61.35, z=22.44 (mm)
실제 카메라 물리적 위치: y≈-50mm 내외 (자로 실측)
→ 약 11mm 차이 + 추가 오차
```

핸드아이 캘리브 자체의 번역벡터 오차로 판단했습니다.
근본 해결은 재캘리브이지만, 현 단계에서는 `pick_offset_mm`으로 흡수했습니다.

```yaml
pick_offset_mm: [20.0, 16.3, -20.0]  # [x_mm, y_mm, z_mm]
```

### 파지 성공 여부 판정

그리퍼 값(0~100)으로 파지 성공 여부를 자동 판정합니다.

```python
gripper_val = mc.get_gripper_value()
grasped = gripper_val is not None and gripper_val >= 25
# 상자 파지 시: 25~35 / 헛잡기: 0~10
```

---

## 최종 결과

상자를 카메라 시야 내 임의 위치에 놓고 8회 연속 파지 테스트를 진행했습니다.

| 시도 | 위치 (x, y) | 그리퍼 값 | 결과 |
|------|------------|---------|------|
| 1 | (7.2, -171.1) | 27 | ✅ 성공 |
| 2 | (13.7, -241.3) | 30 | ✅ 성공 |
| 3 | (-19.1, -169.7) | 27 | ✅ 성공 |
| 4 | (-20.5, -241.6) | 31 | ✅ 성공 |
| 5 | (-51.8, -170.2) | 27 | ✅ 성공 |
| 6 | (-53.5, -234.8) | 29 | ✅ 성공 |
| 7 | (-84.8, -226.6) | 31 | ✅ 성공 |
| 8 | (-82.7, -171.9) | 26 | ✅ 성공 |

**8/8 성공 (100%)** (이후 자동화 검증 스크립트를 통해 10회 연속 100% 성공을 추가 달성했습니다.)

### 시스템 응답 성능 (사이클 타임 측정)
- **비전 파이프라인 (카메라 캡처 → 3D 좌표 변환)**: 평균 **74.8 ms** (최대 384.6 ms)
- **Pick & Place 전체 사이클 (Approach → Grasp → Retreat)**: 평균 **12.62 초**

> 빠른 비전 처리 속도(평균 74.8ms)를 달성하여, 실시간성이 중요한 AMR(자율주행 로봇)과의 협업 및 연동 시 지연 시간을 최소화할 수 있는 성능을 입증했습니다.

---

## 남은 과제 및 개선 방향

| 항목 | 현황 | 개선 방향 |
|------|------|---------|
| 각도 대응 | 90도 근방에서만 안정적 | 핸드아이 재캘리브 (번역벡터 정밀화) |
| y축 오차 | pick_offset으로 임시 보정 | 재캘리브로 근본 해결 |
| 상자 각도 범위 | ±20도 내 | 재캘리브 후 확장 가능 |

---

## 코드

- GitHub: [https://github.com/Jinho001/roscamp](https://github.com/Jinho001/roscamp)
- 태그: `v0.1.0-pick-mvp`
- 핵심 파일:
  - `core/coord_transform.py` — 좌표 변환
  - `core/motion.py` — Pick/Place 모션
  - `scripts/verify.py` — 단계별 검증 도구
  - `config/params_front_jet.yaml` — 파라미터 통합 관리

---

## 배운 것

1. **좌표계 통일의 중요성** — TCP/flange 혼재 버그가 16mm 오차의 원인. 설계 단계에서 좌표계 기준을 명확히 해야 함
2. **검증 도구의 가치** — verify.py 없었으면 어느 단계가 문제인지 알 수 없었음. 디버깅 시간의 70%를 절약
3. **core/ROS2 분리** — 순수 Python core 덕분에 로봇 없이도 수식을 단독 테스트할 수 있어 개발 속도 향상
4. **체계적 오차 vs 랜덤 오차** — 오차 방향이 일정하면 캘리브 문제, 들쭉날쭉하면 반복 정밀도 문제로 빠르게 분류
