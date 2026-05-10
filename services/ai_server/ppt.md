# AI Server Troubleshooting

---

## Slide 1 — 표지

# AI Server Troubleshooting
### 비전·통신·ROS2 연동 장애 대응

---

## Slide 2 — 시스템 구성

# 시스템 구성

```
로봇 / 카메라
     ↓  UDP  (port 7018)
  AI 서버
     ↓  TCP  (port 7019)
 ROS2 브릿지
     ↓  ROS2 topic
  FMS / 로봇
```

- 영상은 UDP 청크로 AI 서버에 수신
- 추론 결과는 TCP로 메인 서버에 전달
- 최종 goal은 ROS2 topic으로 로봇에 발행

> **[이미지]** 위 흐름도를 화살표 다이어그램으로 시각화.  
> 각 화살표 위에 프로토콜과 포트 번호 표기.

---

## Slide 3 — 장애 유형 분류

# 장애는 어디서 발생하는가

| 구간 | 대표 증상 |
|------|-----------|
| UDP 수신 | 화면이 안 들어오거나 프레임이 멈춤 |
| AI 추론 | 손들기 반응이 몇 초씩 늦음 |
| TCP 전송 | JSON 파싱 오류, 결과 누락 |
| ROS2 발행 | goal topic echo가 비어 있음 |

> **[이미지]** 4개 구간을 블록으로 표현한 파이프라인.  
> 정상 구간은 초록, 장애 구간은 빨간색으로 강조.

---

## Slide 4 — UDP 프레임 디코딩 실패

# UDP 프레임 디코딩 실패

**원인**
- UDP는 패킷 유실이 발생하면 이미지 buffer가 불완전
- OpenCV `imdecode()`는 불완전한 buffer에서 `None` 반환

**해결**
- 이미지를 청크로 분할해 전송 → 수신 측에서 재조합
- 청크가 모두 도착했을 때만 디코딩 수행

**수치**
- 청크 크기 상한: **60,000 bytes**
- 수신 버퍼: **8 MB**
- 미완성 청크 폐기 기준: **2.0초**

```
[UDP] packets=120  chunks=360  frames=0  queue_drops=12
      ↑ frames=0 → 재조합 실패 상태
```

> **[이미지]** 청크 분할·전송·재조합 과정 그림.  
> 정상(청크 전부 도착 → 복원)과 비정상(청크 유실 → 폐기) 두 케이스 비교.

---

## Slide 5 — 추론 지연

# 추론 지연으로 손들기 반응이 늦음

**원인**
- 수신과 추론이 한 흐름 → 추론이 느리면 프레임이 쌓임
- 오래된 프레임을 처리하는 동안 실제 상황과 간격이 벌어짐

**해결**
- UDP 수신 thread / YOLO 추론 thread 분리
- Queue `maxsize=1` → 항상 최신 프레임 1개만 유지

**수치**
- frame queue: **maxsize = 1**
- TCP 송신 queue: **maxsize = 2**
- 지연 확인 지표: `queue_wait_ms` (값이 클수록 추론이 밀리는 중)

> **[이미지]** Before/After 구조 비교.  
> Before: 수신→추론→전송 직렬 연결.  
> After: 수신 thread와 추론 thread를 Queue(maxsize=1)로 분리.

---

## Slide 6 — TCP JSON 파싱 실패

# TCP 결과가 잘리거나 JSON 파싱 실패

**원인**
- TCP는 message boundary 없음
- `recv()` 한 번으로 전체 payload가 온다는 보장 없음

**해결**
- `[4B length][JSON payload]` length-prefix framing 적용
- `recv_exact(n)` 으로 정확히 n bytes가 올 때까지 반복 수신

**수치**
- 헤더 크기: **4 bytes** (big-endian)
- JSON 최대 허용 크기: **20 MB**

```
[ 4 bytes: payload 길이 ][ JSON payload (N bytes) ]
```

> **[이미지]** 패킷 구조 그림.  
> Before: 경계 없는 stream → 중간에 잘린 JSON.  
> After: length-prefix → `recv_exact()`로 정확히 N bytes 수신.

---

## Slide 7 — TCP 연결 복구

# TCP 연결이 끊긴 뒤 복구되지 않음

**원인**
- 메인 서버 재시작 후 half-open 상태 → AI 추론 결과 전달 불가
- 연결이 끊겼는지 감지하지 못하면 old socket을 계속 사용

**해결**
- TCP keepalive로 dead connection 자동 감지
- connection monitor thread에서 `recv(1)`로 peer close 감지
- 끊기면 새 connection 자동 수락

**수치**
- keepalive idle: **10초** / probe 간격: **3초** / probe 횟수: **5회**
- dead connection 감지: 최대 **25초** 이내
- recv timeout: **30초**
- 재연결 대기: **3.0초**

> **[이미지]** 타임라인 그림.  
> 메인 서버 재시작 시점 → keepalive probe 구간 → 25초 내 감지 → 3초 후 재연결 성공.

---

## Slide 8 — ROS2 Goal Topic 미발행

# ROS2 goal topic이 발행되지 않음

**원인**
- topic 이름·message type·QoS 세 가지가 모두 일치해야 통신
- Fast DDS SHM: 프로세스 비정상 종료 후 stale segment가 통신 차단

**해결**

```bash
# 1. topic 상태 확인
ros2 topic info /hand_raise_goal --verbose

# 2. stale segment 정리
rm -f /dev/shm/fastrtps_*
```

> **[이미지]** ROS2 topic 매칭 조건 표.  
> 이름 ✓ / type ✓ / QoS ✓ → 통신 성공.  
> 하나라도 ✗ → 데이터 흐르지 않음.

---

## Slide 9 — 손들기 오탐·미탐

# 손들기 오탐·미탐

**원인**
- YOLO confidence 단독으로는 애매한 자세 구분에 한계

**해결**
- 기하학적 보정(solidity, reach ratio)을 confidence와 병행
- 기하학 판정이 있으면 YOLO class보다 우선

**수치**
- YOLO confidence 기준: **0.40**
- Solidity 임계값: **0.85** (외곽 대비 실제 면적 비율)
- Reach ratio 임계값: **1.80** (팔 뻗음 비율)
- goal cooldown: **5.0초** (중복 발행 방지)

> **[이미지]** hands_up / hands_down 실제 카메라 캡처 비교.  
> 각 자세에서 solidity·reach ratio 값이 어떻게 달라지는지 수치와 함께 표기.

---

## Slide 10 — Pixel → Map 좌표 변환 오류

# Pixel 좌표가 Map 좌표로 잘못 변환됨

**원인**
- Homography(`H.npy`)가 현재 카메라 위치와 맞지 않으면 goal이 어긋남
- 카메라 위치·해상도 변경 시 이전 H.npy 재사용 불가

**해결**
- 카메라 변경 시 `findHomography()`로 H.npy 재생성
- map YAML의 `resolution`, `origin`이 실제 map과 일치하는지 확인

**변환식**
```
x = origin_x + transformed_x × resolution
y = origin_y + (map_height − transformed_y) × resolution
```

> **[이미지]** 매장 map(pgm) 위에 픽셀 좌표 → map 좌표 변환 화살표 표시.  
> Before(잘못된 H.npy): 실제 사람 위치와 robot goal이 어긋난 그림.  
> After(올바른 H.npy): 사람 위치와 goal이 일치하는 그림.

---

## Slide 11 — 좌석 점유 상태 불안정

# 좌석 점유 상태가 계속 흔들림

**원인**
- 순간 가림·조명 변화로 감지 실패 → OCCUPIED/EMPTY 반복
- tracker ID는 영상 조건에 따라 바뀔 수 있음

**해결**
- `persist=True` tracking으로 ID 유지
- ReID(ResNet18 512차원 + HSV histogram)로 tracker ID 변경 시 재식별
- 30초 debounce로 순간 튐 무시

**수치**
- figure confidence: **0.50**
- ReID combined score 기준: **0.45**
- 좌석 상태 확정 debounce: **30초**
- seat result 전송 주기: **1.0초**

> **[이미지]** Debounce 타임라인.  
> 감지 수 변화가 30초 유지될 때만 상태 변경, 순간 튐은 무시.  
> ReID 구조: bbox crop → ResNet18(512차원) + HSV → combined score → 0.45 이상이면 동일 target.

---

## Slide 12 — 통합 테스트 진단 흐름

# 어느 구간에서 끊겼는지 모를 때

**점검 순서**

```
Step 1  로봇 UDP 수신 확인    [로봇] from=... frame=... chunk=...
Step 2  카메라 JSON 확인      [카메라] from=... size=...
Step 3  AI TCP 결과 확인      [AI 결과 수신] robot_id / type / process_ms
Step 4  ROS2 goal 확인        [STATS] goal_pub=... dropped=...
```

**구간별 판단**

| 증상 | 끊긴 구간 |
|------|-----------|
| `[로봇]` 로그 없음 | 로봇 → 메인 서버 UDP |
| `[AI 결과 수신]` 없음 | AI 서버 추론 또는 TCP |
| `goal_pub=0` 유지 | TCP 파싱 또는 ROS2 publish |
| `topic echo` 비어 있음 | ROS2 bridge 또는 DDS |

> **[이미지]** Step 1~4를 블록으로 표현한 점검 흐름도.  
> 각 블록 옆에 정상 로그 예시 한 줄씩.

---

## Slide 13 — 핵심 수치 한눈에 보기

# 핵심 수치 정리

| 항목 | 값 |
|------|----|
| UDP 청크 크기 상한 | 60,000 bytes |
| UDP 수신 버퍼 | 8 MB |
| 미완성 청크 폐기 | 2.0초 |
| frame queue | maxsize = 1 |
| TCP 송신 queue | maxsize = 2 |
| TCP JSON 최대 크기 | 20 MB |
| dead connection 감지 | 최대 25초 |
| 재연결 대기 | 3.0초 |
| YOLO confidence | 0.40 |
| Solidity 임계값 | 0.85 |
| Reach ratio 임계값 | 1.80 |
| 손들기 cooldown | 5.0초 |
| figure confidence | 0.50 |
| ReID score 기준 | 0.45 |
| 좌석 debounce | 30초 |
| seat result 전송 주기 | 1.0초 |

---

## Slide 14 — 마무리

# 장애 대응 핵심 원칙

1. **UDP** — 청크 재조합, 수신 버퍼 확보, timeout 폐기
2. **추론 지연** — 수신/추론 thread 분리, 최신 프레임 우선
3. **TCP** — length-prefix framing, keepalive, 재연결
4. **ROS2** — name·type·QoS 3조건 확인, SHM 정리
5. **손들기** — confidence + 기하학 보정 병행
6. **좌석** — persist tracking + ReID + debounce
