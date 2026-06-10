# 면접 대비 기술 개념 설명서

포트폴리오에 등장하는 기술 개념을 면접에서 설명할 수 있는 수준으로 정리.

---

## 1. LAB L채널 CLAHE 조명 정규화

### 왜 필요했나

HSV 마스킹은 색상(Hue)과 채도(Saturation)로 물체를 구분하는데, 조명이 달라지면 같은 흰색 상자도 HSV 값이 달라집니다. 형광등 아래 흰색과 그늘 속 흰색은 카메라에서 다르게 보입니다.

### CLAHE란

Contrast Limited Adaptive Histogram Equalization. 이미지를 작은 구역으로 나눠 각 구역별로 밝기 대비를 높여줍니다. 어두운 영역은 밝게, 너무 밝은 영역은 억제합니다.

일반 히스토그램 평활화(global)와의 차이: 전체 이미지에 한 번 적용하면 밝은 영역이 과다 증폭됩니다. CLAHE는 구역별(adaptive)로 적용해서 이를 막습니다.

### 왜 LAB 색공간의 L 채널에만 적용하나

LAB 색공간은 이렇게 생겼습니다:
- **L**: 밝기 (Lightness). 0=검정, 100=흰색
- **A**: 초록 ↔ 빨강
- **B**: 파랑 ↔ 노랑

밝기(L)만 정규화하고 색 정보(A, B)는 건드리지 않으면 → HSV로 변환했을 때 색상 범위가 안정적으로 유지됩니다.

BGR이나 HSV에 직접 CLAHE를 적용하면 밝기를 조정할 때 색도 함께 변합니다. LAB는 밝기와 색을 분리했기 때문에 L만 건드릴 수 있습니다.

```python
lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
lab[:, :, 0] = clahe.apply(lab[:, :, 0])   # L 채널만
img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
# 이후 HSV 변환 → inRange 마스킹
```

### 면접 한 줄 답변

> "HSV 마스킹은 조명에 민감합니다. 색 정보는 보존하면서 밝기만 균일화하기 위해 LAB 색공간으로 변환 후 밝기 채널(L)에만 CLAHE를 적용했습니다."

---

## 2. OBB (Oriented Bounding Box) — minAreaRect

### 일반 Bounding Box와의 차이

일반 Bounding Box(AABB)는 이미지 축에 평행한 직사각형입니다. 물체가 기울어져 있으면 빈 공간이 많이 포함됩니다.

OBB는 물체에 딱 맞게 회전된 최소 면적 직사각형입니다. 상자의 방향각(θ)도 함께 얻을 수 있어서, 로봇이 상자와 정렬된 방향으로 파지할 수 있습니다.

### 코드 흐름

```python
contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
rect = cv2.minAreaRect(largest_contour)
# rect = ((cx, cy), (width, height), angle)
```

`minAreaRect`가 반환하는 angle이 곧 상자의 기울기 θ입니다. 이것을 yaw 각도로 변환해 로봇 파지 방향을 결정합니다.

### confidence 계산

```python
confidence = contour_area / hull_area
```

외곽선 면적 대비 볼록 껍질(convex hull) 면적의 비율. 직사각형 상자면 1.0에 가깝고, 노이즈나 이상한 모양이면 낮아집니다.

---

## 3. Ray-Plane Intersection — Depth 없이 3D 좌표 복원

### 문제

카메라는 2D 이미지를 찍습니다. 픽셀 하나는 카메라에서 뻗어나가는 무한한 직선(Ray) 위의 어느 점이든 될 수 있습니다. 즉, 깊이 정보 없이는 3D 위치를 알 수 없습니다.

### 핵심 가정

상자는 항상 알려진 높이 Z_surface 위에 있습니다. (트레이나 선반 위에 놓이므로 도메인에서 보장됨)

이 평면 제약 조건 하나가 무한한 해를 유일한 해로 좁혀줍니다.

### 수식 단계별 의미

```
1. 픽셀 (u, v) → 방향 벡터 (카메라 좌표계)
   ray_c = K_inv @ [u, v, 1]
   K_inv: 카메라 내부 행렬의 역행렬 (렌즈 특성 제거)

2. 카메라 좌표계 → Base 좌표계로 회전
   ray_b = R @ ray_c
   R: T_base2cam의 회전 부분 (Hand-Eye + 현재 로봇 자세)

3. 평면과의 교점 파라미터 t 계산
   t = (Z_surface - cam_origin_z) / ray_b_z
   이 t가 카메라 원점에서 평면까지의 거리(배율)

4. 3D 좌표
   P_base = cam_origin + t * ray_b
```

### 왜 Z_surface를 정확히 측정해야 하나

Z_surface가 1mm 틀리면 t 계산이 틀리고, P_base 전체가 틀립니다. 이 프로젝트에서 최초 Z_surface를 110mm 잘못 측정한 게 파지 오차의 원인이었습니다.

---

## 4. Hand-Eye Calibration (AX = XB)

### 왜 필요한가

카메라가 로봇 팔 끝(EE)에 달려 있습니다. 로봇이 움직이면 카메라도 따라 움직이므로, "Flange에서 카메라까지의 고정 변환(T_flange2cam)"을 실측해야 합니다.

이것을 모르면 카메라가 찍은 픽셀을 로봇 Base 좌표로 변환할 수 없습니다.

### AX = XB의 의미

로봇을 N가지 자세로 이동하면:

```
A_i = T_base2flange_i   (로봇 관절 각도에서 계산 — 알고 있음)
B_i = T_cam2board_i     (체커보드 solvePnP로 계산 — 알고 있음)
X   = T_flange2cam      (구하는 것 — 모든 자세에서 고정)
```

A_i와 B_i를 여러 쌍 모으면 X를 역산할 수 있습니다. `cv2.calibrateHandEye(TSAI)` 가 이걸 풀어줍니다.

### 왜 TCP가 아닌 Flange 기준인가

TCP(Tool Center Point)는 장착된 그리퍼에 따라 변하는 값입니다. 그리퍼를 교체하면 TCP 오프셋이 달라집니다.

Flange는 로봇 팔의 물리적 끝단으로, 공구와 무관하게 고정된 기준점입니다.

T_flange2cam은 카메라 브래킷을 바꾸지 않는 한 불변입니다. Flange 기준으로 캘리브하면 그리퍼 교체 시 재캘리브레이션이 필요 없습니다.

### 런타임에서의 활용

```python
# observe 자세에서:
flange = get_flange_coords()       # set_end_type(0)으로 Flange 좌표 읽기
T_base2cam = T_base2flange @ T_flange2cam   # 현재 카메라 위치 계산
```

왜 set_end_type(0)인가: PyMyCobot 기본값은 TCP 모드(1)입니다. Flange 좌표를 읽으려면 명시적으로 0으로 전환해야 합니다. 읽고 나서 1로 복원하는 것은 이후 send_coords() 명령이 TCP 기준으로 동작하게 유지하기 위해서입니다.

---

## 5. 카메라 캘리브레이션 (Zhang's Method)

### 왜 필요한가

실제 카메라 렌즈는 완벽한 핀홀 카메라가 아닙니다. 렌즈 왜곡 때문에 이미지 가장자리로 갈수록 픽셀이 실제 위치와 달라집니다.

이 왜곡을 보정하지 않으면 Ray-Plane 교점 계산에서 가장자리 물체의 좌표 오차가 커집니다.

### 무엇을 구하나

```
카메라 내부 행렬 K:
  K = [[fx,  0, cx],
       [ 0, fy, cy],
       [ 0,  0,  1]]

  fx, fy: 초점 거리 (픽셀 단위)
  cx, cy: 주점 (이미지 중심)

왜곡 계수: k1, k2, p1, p2, ...
```

### 어떻게 구하나

체커보드(격자 패턴)를 여러 각도에서 찍으면, 코너점의 실제 3D 위치와 이미지상 2D 위치를 알 수 있습니다. 이 대응 쌍들로 K와 왜곡 계수를 최적화합니다.

RMS Reprojection Error: 보정된 K로 3D 점을 다시 2D로 투영했을 때 실제 픽셀과의 평균 거리. 0.257px은 좋은 값입니다 (기준: 0.5px 이하).

---

## 6. 좌표 변환 행렬 (4×4 Homogeneous Transform)

### 기본 구조

```
T = [[R, t],   R: 3×3 회전 행렬
     [0, 1]]   t: 3×1 이동 벡터
```

로봇 비전에서 모든 좌표계 변환은 이 형태입니다.

### ZYX 오일러각 → 회전 행렬

로봇 API는 관절 자세를 (x, y, z, rx, ry, rz) 형태로 줍니다. rx, ry, rz는 ZYX 외재적 오일러각입니다.

```python
# ZYX 외재적: 먼저 Z 회전, 그 다음 Y, 그 다음 X
R = Rz(rz) @ Ry(ry) @ Rx(rx)
```

"외재적(Extrinsic)"이란 회전이 고정된 세계 좌표계 축을 기준으로 적용된다는 의미입니다.

---

## 7. z_surface 측정 방법 (트러블슈팅 배경 지식)

### 왜 그리퍼 tip이 아닌 Flange로 읽으면 틀리나

그리퍼가 상자 표면에 닿은 순간:
- 그리퍼 tip (TCP) 위치 = 상자 표면 높이
- Flange 위치 = 상자 표면 높이 + 그리퍼 길이

Flange 좌표로 읽으면 z_surface가 그리퍼 길이만큼 과대 측정됩니다.

### 해결책

`set_end_type(1)` (TCP 모드)로 전환 후 측정하면 그리퍼 tip 위치를 직접 읽을 수 있습니다.

---

*이 문서는 포트폴리오 면접 준비용 개인 학습 자료입니다.*
