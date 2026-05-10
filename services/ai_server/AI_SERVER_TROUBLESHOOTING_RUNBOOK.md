# AI Server Troubleshooting Runbook

범위: `/home/team1-ai/roscamp-repo-1/services/ai_server`  
제외: VNC 접속/화면 공유

---

## 시스템 구성

```
[로봇/카메라]
    │  UDP 청크 (port 7018)
    ▼
[AI 서버 · hands_seat_ai.py]
    │  TCP [4B length][JSON] (port 7019)
    ▼
[메인 서버 / ROS2 브릿지 · tcp_result_receiver.py]
    │  ROS2 topic
    ▼
[FMS / Robot Navigation]
    │  /hand_raise_goal (PoseStamped)
```

### 포트 · 프로세스 참조

| 포트 | 프로토콜 | 방향 | 용도 |
|------|----------|------|------|
| 7018 | UDP | 로봇/카메라 → AI 서버 | 영상 청크 수신 |
| 7019 | TCP | AI 서버 → 메인 서버 | 추론 결과 전송 |

### 프로세스 기동 순서

1. AI 서버: `python3 hands_seat_ai.py`
2. ROS2 브릿지: `python3 tcp_result_receiver.py`
3. 통합 테스트: `python3 moosinsa_service_test.py`

> AI 서버가 TCP server이므로 메인 서버/브릿지보다 먼저 띄워야 한다.

---

## 빠른 진단

| 증상 | 확인할 섹션 |
|------|-------------|
| 화면이 안 들어오거나 프레임이 가끔 멈춤 | [1. UDP 프레임 디코딩 실패](#1-udp-영상-프레임-디코딩-실패) |
| 손들기 반응이 몇 초씩 늦음 | [2. 추론 지연](#2-추론-지연으로-손들기-반응이-늦음) |
| JSON 파싱 오류 / TCP 결과 누락 | [3. TCP 결과 파싱 실패](#3-tcp-결과가-잘리거나-json-파싱-실패) |
| 메인 서버 재시작 후 결과가 안 옴 | [4. TCP 연결 복구 안 됨](#4-tcp-연결이-끊긴-뒤-복구되지-않음) |
| `/hand_raise_goal` topic echo가 비어 있음 | [5. ROS2 goal topic 문제](#5-ros2-goal-topic이-안-보이거나-발행되지-않음) |
| 손들기 오탐 또는 미탐 | [6. 손들기 오탐·미탐](#6-손들기-오탐-또는-미탐) |
| 로봇이 엉뚱한 위치로 이동 | [7. 픽셀 → map 좌표 변환 오류](#7-pixel-좌표가-map-좌표로-잘못-변환됨) |
| 좌석 상태가 OCCUPIED/EMPTY 사이에서 튐 | [8. 좌석 점유 상태 불안정](#8-좌석-점유-상태가-계속-흔들림) |
| 어느 구간에서 끊겼는지 모름 | [9. 통합 테스트 진단](#9-통합-테스트-시-어느-구간에서-끊겼는지-모름) |

---

## 1. UDP 영상 프레임 디코딩 실패

### 증상

- AI 서버 콘솔에 `[DECODE] 실패: ...` 로그가 나온다.
- 화면에 frame이 뜨지 않거나 특정 robot_id 영상이 간헐적으로 멈춘다.
- `cv2.imdecode()` 결과가 `None`이어서 추론으로 넘어가지 않는다.

### 영향

- YOLO 추론이 수행되지 않는다.
- `vision_result`가 생성되지 않아 손들기 goal과 좌석 결과가 backend로 전달되지 않는다.

### 원인

UDP는 datagram 방식이라 큰 이미지를 단일 패킷으로 안정적으로 전송하기 어렵다.  
packet 일부가 유실되면 PNG/JPEG buffer가 불완전해지고, OpenCV `imdecode()`는 invalid buffer에서 `None`을 반환한다.

### 공식 문서 근거

**청크 분할 + 재조합 방식을 사용한 이유**

- **Python Socket HOWTO** — UDP(`SOCK_DGRAM`)는 비연결형 프로토콜로, 패킷의 도달, 순서, 중복을 보장하지 않는다. 하나의 UDP 패킷 최대 크기는 이론상 65535 bytes이나 네트워크 경로 MTU(보통 1500 bytes)를 초과하면 IP 단에서 단편화되어 일부가 유실될 수 있다.  
  → 큰 이미지를 단일 UDP 패킷으로 보내는 대신, 60000 bytes 이하 청크로 나눠 각 청크에 `frame_id`, `chunk_idx`, `total`을 붙여 수신 측에서 재조합하는 방식으로 해결한다.  
  참고: https://docs.python.org/3/howto/sockets.html

- **OpenCV imgcodecs** — `cv::imdecode()`는 buffer가 너무 짧거나 유효하지 않은 데이터를 담고 있으면 empty matrix(Python에서 `None`)를 반환한다. 예외를 던지지 않으므로 반환값을 반드시 확인해야 한다.  
  → 모든 청크가 모인 뒤에만 `imdecode()`를 호출하고, 결과가 `None`이면 해당 frame을 버린다.  
  참고: https://docs.opencv.org/4.x/d4/da8/group__imgcodecs.html

### 확인 절차

AI 서버에서 포트 수신 여부를 확인한다.

```bash
ss -lunp | grep ':7018'
```

AI 서버 로그에서 chunk/frame 통계를 확인한다.

```text
[UDP] packets=... chunks=... frames=... recv_fps=... last_size=... avg_chunks/frame=... queue_drops=...
```

- `frames=0`인데 `packets/chunks`만 증가하면 frame 재조합이 완료되지 않는 상태다.
- `queue_drops`가 증가하면 수신은 되지만 추론이 따라가지 못하는 상태다.

### 해결 절차

1. UDP packet header 구조가 송수신 양쪽에서 동일한지 확인한다.
   - `hands_seat_ai.py` 41~47: `HDR_FMT = '!HIHHBB'`, `MAGIC = 0xA55A`
   - 확인 항목: `magic`, `frame_id`, `chunk_idx`, `total`, `pkt_type`

2. frame buffer timeout이 설정되어 있는지 확인한다.
   - `hands_seat_ai.py` 38~39: `FRAME_TIMEOUT_SEC = 2.0`
   - 2초 이상 미완성 청크 버퍼는 자동 정리된다.

3. 수신된 chunk가 `total` 개수만큼 모였을 때만 decode하는지 확인한다.
   - `hands_seat_ai.py` 550~569

4. `cv2.imdecode()` 결과가 `None`이면 해당 frame을 버리고 다음 frame을 처리하는지 확인한다.
   - `hands_seat_ai.py` 568~575

### 재발 방지

- 송신 측 chunk size를 `60000` bytes 이하로 유지한다.
- 수신 측 `UDP_RECV_BUF = 8 * 1024 * 1024` 설정을 유지한다.
- `[UDP] queue_drops`와 `[MODEL_IN] queue_wait_ms`를 함께 확인한다.

---

## 2. 추론 지연으로 손들기 반응이 늦음

### 증상

- 사람이 손을 든 뒤 몇 초 뒤에야 `GOAL` 로그가 나온다.
- 영상은 들어오지만 AI 화면이 실제 상황보다 늦게 따라온다.
- `[UDP] queue_drops`가 증가한다.

### 영향

- robot goal이 늦게 생성된다.
- backend/FMS가 오래된 사람 위치를 받을 수 있다.

### 원인

UDP 수신과 YOLO 추론을 같은 흐름에서 처리하면 추론이 느릴 때 frame backlog가 쌓인다.  
실시간 영상 처리는 모든 frame을 처리하는 것보다 최신 frame을 처리하는 것이 더 중요하다.

### 공식 문서 근거

**수신 thread 분리 + `Queue(maxsize=1)` 방식을 사용한 이유**

- **Python threading** — I/O-bound 작업(UDP 수신)과 CPU-bound 작업(YOLO 추론)을 같은 흐름에 두면 추론이 오래 걸리는 동안 수신이 막힌다. threading으로 두 작업을 분리하면 수신은 계속 동작하면서 추론은 독립적으로 실행된다.  
  참고: https://docs.python.org/3/library/threading.html

- **Python `queue.Queue`** — `maxsize` 파라미터로 큐 최대 크기를 제한할 수 있다. `maxsize=1`로 설정하면 큐가 가득 찼을 때 `put_nowait()`는 `Full` 예외를 발생시키므로, 이를 이용해 이전 frame을 `get_nowait()`로 꺼내 버리고 최신 frame만 유지할 수 있다. 결과적으로 추론 대기열이 쌓이지 않고 항상 최신 frame이 처리된다.  
  참고: https://docs.python.org/3/library/queue.html

### 확인 절차

AI 서버 로그에서 모델 입력 지연을 확인한다.

```text
[MODEL_IN] frames=... fps=... last_frame_id=... frame_gap=... size=... queue_wait_ms=...
```

- `queue_wait_ms`가 계속 커지면 추론 대기열이 밀리는 상태다.
- `frame_gap`이 크면 중간 frame을 건너뛰고 최신 frame 위주로 처리 중인 상태다.

### 해결 절차

1. UDP 수신과 추론 루프가 별도 thread로 분리되어 있는지 확인한다.
   - `hands_seat_ai.py` 444~448: `_frame_queue = queue.Queue(maxsize=1)`, `_udp_receiver` thread

2. 새 frame이 들어올 때 queue에 남은 이전 frame을 제거하는지 확인한다.
   - `hands_seat_ai.py` 578~583: `get_nowait()`로 이전 frame 제거 후 최신 frame 입력

3. AI model 종류를 필요한 것만 켜서 처리 부하를 줄인다.
   - `MODE_SEAT`, `MODE_POSE`, `MODE_TARGET`, `MODE_ALL` 중 필요한 모드 선택

### 재발 방지

- frame 처리율은 `[FPS]`, `[MODEL_IN]`, `[UDP]` 3개 로그를 함께 본다.
- GPU/CPU 사용률이 높으면 모드를 축소하고 필요한 기능만 켠다.

---

## 3. TCP 결과가 잘리거나 JSON 파싱 실패

### 증상

- 수신 측에서 다음 로그가 발생한다.
  - `JSON 파싱 오류 (1패킷 스킵): ...`
  - `AI 서버 연결 끊김 (헤더)`
  - `AI 서버 연결 끊김 (바디)`
  - `비정상 JSON 크기: ...B`
- backend/ROS2 bridge가 일부 결과만 받거나 멈춘다.

### 영향

- `vision_result`, `seat_result`가 누락된다.
- `/hand_raise_goal` 발행이 누락된다.

### 원인

TCP는 message boundary가 없어서 `recv()` 한 번으로 전체 payload가 온다고 보장되지 않는다.  
application layer에서 framing 처리가 없으면 JSON이 중간에 잘린다.

### 공식 문서 근거

**`[4B length][JSON]` length-prefix framing을 사용한 이유**

- **Python Socket HOWTO** — TCP는 stream 프로토콜로 message boundary가 없다. `recv(n)`은 최대 n bytes를 반환하지만 n bytes 전체가 한 번에 온다고 보장하지 않는다. 큰 payload는 여러 번의 `recv()` 호출로 나눠 수신해야 한다.  
  → 이를 해결하는 표준 패턴이 length-prefix framing이다. 먼저 고정 크기(4 bytes)의 헤더로 payload 길이를 읽고, 그 길이만큼 반복 수신한다.  
  참고: https://docs.python.org/3/howto/sockets.html

- **Python `socket.sendall()`** — `send()`와 달리 `sendall()`은 데이터 전체를 전송할 때까지 반복 시도하고, 실패 시 예외를 발생시킨다. timeout mode에서 지정 시간 내 완료되지 않으면 `TimeoutError`가 발생한다.  
  → 송신 측에서 `sendall(header + payload)`를 사용하면 partial send 문제를 방지할 수 있다.  
  참고: https://docs.python.org/3/library/socket.html

- **Python `struct` 모듈** — `struct.pack("!I", length)`는 unsigned int를 big-endian 4 bytes로 변환한다. `"!"` prefix는 network byte order(big-endian)를 의미하며, 서로 다른 아키텍처 간 통신 시 byte order 불일치를 방지한다.  
  참고: https://docs.python.org/3/library/struct.html

### 확인 절차

AI 서버 TCP listen 상태를 확인한다.

```bash
ss -ltnp | grep ':7019'
```

메인 서버/브릿지에서 연결 로그를 확인한다.

```text
[TCP] 연결됨: 192.168.1.121:7019
[VISION] robot=... frame=... dets=... goals=... ai=...ms latency=...ms bytes=...
```

### 해결 절차

1. 모든 TCP payload가 `[4B big-endian length][JSON payload]` 형식인지 확인한다.
   - 송신: `struct.pack("!I", len(payload)) + payload`
   - 수신: 4 byte header 먼저 읽고, body 길이만큼 다시 읽는다.

2. `recv_exact(sock, n)` 함수로 정확히 n byte를 받을 때까지 반복하는지 확인한다.
   - `tcp_result_receiver.py` 46~54

3. TCP 송신 queue 크기를 확인한다.
   - `hands_seat_ai.py` 111: `_tcp_send_q = queue.Queue(maxsize=2)`
   - frame 수신 queue(`maxsize=1`)와 달리 TCP 송신 queue는 `maxsize=2`로 최신 결과 2개를 유지한다.

4. JSON 최대 크기 제한이 있는지 확인한다.
   - `tcp_result_receiver.py` 164~166: `MAX_JSON_SIZE = 20 * 1024 * 1024`

5. JSON decode 실패 시 process crash가 아닌 1 packet skip으로 처리하는지 확인한다.

### 관련 코드

| 파일 | 위치 | 역할 |
|------|------|------|
| `hands_seat_ai.py` | 172~183 | `[4B length][JSON]` 송신 |
| `tcp_result_receiver.py` | 46~54 | `recv_exact()` 구현 |
| `tcp_result_receiver.py` | 157~178 | length-prefix decode 루프 |
| `tcp_result_receiver.py` | 164~166 | 비정상 JSON size 차단 |

### 재발 방지

- 새 TCP client/server를 추가할 때도 `[4B length][JSON]` 규칙을 반드시 사용한다.
- `recv(4096)` 한 번으로 JSON 전체가 온다고 가정하지 않는다.

---

## 4. TCP 연결이 끊긴 뒤 복구되지 않음

### 증상

- 메인 서버 재시작 후 AI 서버 결과가 다시 들어오지 않는다.
- AI 서버 로그에 다음이 반복된다.
  - `[TCP] Main server not connected - dropped`
  - `[TCP] Send error: ...`

### 영향

- AI 추론은 계속되지만 backend/FMS로 결과가 전달되지 않는다.

### 원인

TCP socket은 peer process가 죽거나 network가 끊긴 뒤 half-open 상태처럼 보일 수 있다.  
명시적인 peer close 감지와 재연결 처리가 없으면 old socket을 계속 사용하려다 실패한다.

### 공식 문서 근거

**connection monitor + TCP keepalive + accept loop 방식을 사용한 이유**

- **Python socket — TCP half-open 감지** — 상대방이 갑자기 죽으면(전원 차단, 프로세스 kill 등) FIN 패킷이 오지 않아 socket이 살아있는 것처럼 보인다. 이 상태에서 `recv()`를 호출하면 무한 대기에 빠진다. `recv(1)`을 짧은 timeout과 함께 사용하면 연결이 살아있는지 주기적으로 확인할 수 있으며, 상대방이 정상 종료한 경우 `recv()`는 빈 `bytes`(`b""`)를 반환한다.  
  참고: https://docs.python.org/3/library/socket.html

- **Python socket — `SO_KEEPALIVE` / `TCP_KEEPIDLE` / `TCP_KEEPINTVL` / `TCP_KEEPCNT`** — OS 레벨의 TCP keepalive를 활성화하면, 지정한 idle 시간 이후 probe 패킷을 보내 dead connection을 자동으로 감지한다. 애플리케이션 레벨 polling 없이 커널이 연결 상태를 확인하므로 더 안정적이다.  
  → `idle 10초, interval 3초, count 5회` 설정은 최대 25초 내에 dead connection을 감지한다.  
  참고: https://docs.python.org/3/library/socket.html#notes-on-socket-timeouts

### 확인 절차

AI 서버 7019 포트 listen 상태를 확인한다.

```bash
ss -ltnp | grep ':7019'
```

메인 서버/ROS2 bridge 재연결 로그를 확인한다.

```text
[TCP] ... - 3.0s 후 재시도
[TCP] 연결됨: 192.168.1.121:7019
```

### 해결 절차

1. AI 서버가 TCP server로 계속 listen하는지 확인한다.
   - `hands_seat_ai.py` 152~169

2. 새 connection이 들어오면 기존 socket을 닫고 교체하는지 확인한다.
   - `hands_seat_ai.py` 162~168

3. connection monitor thread에서 `recv(1)`로 peer close를 감지하는지 확인한다.
   - `hands_seat_ai.py` 114~130

4. 수신 측 timeout 설정을 확인한다.
   - connect timeout: 5초
   - recv timeout: 30초

5. TCP keepalive가 켜져 있는지 확인한다.
   - `tcp_result_receiver.py` 35~43: idle 10초, interval 3초, count 5회

### 재발 방지

- 메인 서버 재시작 후에는 AI 서버를 재시작하지 말고, bridge reconnect 로그를 먼저 확인한다.
- reconnect interval 기준값: `RECONNECT_SEC = 3.0`

---

## 5. ROS2 goal topic이 안 보이거나 발행되지 않음

### 증상

- 손들기 감지는 되는데 robot 이동 goal이 ROS2로 나오지 않는다.
- `/hand_raise_goal` topic echo가 비어 있다.
- bridge 로그에 `goal_pub=0`이 유지된다.

### 영향

- AI가 사람 위치를 잡아도 FMS/robot navigation으로 이어지지 않는다.

### 원인

- ROS2 topic은 publisher/subscriber의 message type과 QoS가 맞아야 통신된다.
- Fast DDS SHM 환경에서는 process crash 뒤 stale shared memory segment가 남아 통신을 막을 수 있다.

### 공식 문서 근거

**`ros2 topic info --verbose` 확인 + SHM 정리 방식을 사용한 이유**

- **ROS2 Understanding Topics** — ROS2 topic 통신은 publisher와 subscriber의 세 가지 조건이 모두 일치해야 성립한다: ① topic 이름, ② message type, ③ QoS 정책(reliability, durability 등). 하나라도 다르면 graph는 연결된 것처럼 보여도 실제 데이터는 흐르지 않는다. `ros2 topic info --verbose`는 이 세 가지를 한 번에 확인할 수 있는 공식 권장 도구다.  
  참고: https://docs.ros.org/en/rolling/Tutorials/Beginner-CLI-Tools/Understanding-ROS2-Topics/Understanding-ROS2-Topics.html

- **Fast DDS Shared Memory Transport** — SHM transport는 같은 host의 프로세스 간 통신에서 loopback 대비 성능을 높이기 위해 사용된다. 그러나 프로세스가 비정상 종료되면 `/dev/shm/fastrtps_*` 파일로 남는 shared memory segment와 port가 unhealthy 상태로 남을 수 있다. 이 stale segment가 새 프로세스의 포트 초기화를 막아 topic 통신이 시작되지 않는 경우가 발생한다.  
  → 해당 파일을 삭제하고 프로세스를 재시작하면 SHM이 새로 초기화된다.  
  참고: https://fast-dds.docs.eprosima.com/en/2.14.x/fastdds/transport/shared_memory/shared_memory.html

### 확인 절차

topic 목록과 type을 확인한다.

```bash
ros2 topic list -t
```

publisher/subscriber와 QoS를 확인한다.

```bash
ros2 topic info /hand_raise_goal --verbose
```

발행 rate를 확인한다.

```bash
ros2 topic hz /hand_raise_goal
```

payload를 직접 확인한다.

```bash
ros2 topic echo /hand_raise_goal
```

Fast DDS SHM stale file이 의심되면 확인한다.

```bash
ls -al /dev/shm | grep fastrtps
```

### 해결 절차

1. bridge가 publish하는 topic 이름과 frame_id를 확인한다.
   - `tcp_result_receiver.py` 22~25: 기본 topic `/hand_raise_goal`, frame_id `map`
   - 실행 인자 `--frame-id`로 변경 가능

2. bridge가 `vision_result.goals`만 publish하는지 확인한다.
   - `tcp_result_receiver.py` 77~86

3. `goal.pose`가 `"hands_up"`일 때만 publish하는 로직인지 확인한다.
   - `tcp_result_receiver.py` 108~110

4. map 좌표 변환 실패 시 publish하지 않고 error count를 올리는 방식인지 확인한다.
   - `tcp_result_receiver.py` 119~127

5. Fast DDS SHM stale segment를 정리한다.

```bash
rm -f /dev/shm/fastrtps_*
```

### 재발 방지

- 새 topic 추가 시 `ros2 topic info --verbose`로 type/QoS를 먼저 확인한다.
- 같은 host에서 root/non-root 프로세스를 섞어 실행하지 않는다. (Fast DDS SHM 권한 충돌 원인)

---

## 6. 손들기 오탐 또는 미탐

### 증상

- 손을 들지 않았는데 `hands_up` goal이 생성된다.
- 손을 들었는데 `hands_down`으로 남는다.
- 화면에 bbox는 뜨지만 `[GOAL] pixel=... map=...` 로그가 나오지 않는다.

### 영향

- robot이 잘못된 위치로 이동하거나, 필요한 호출에 반응하지 않는다.

### 원인

- YOLO class confidence만으로는 애매한 자세를 안정적으로 구분하기 어렵다.
- 사람/피규어 bbox가 아닌 영역이 pose model에 들어가면 오탐이 증가한다.

### 공식 문서 근거

**기하학적 보정(solidity, reach ratio)을 YOLO confidence와 병행하는 이유**

- **Ultralytics Predict Mode — `conf` 파라미터** — `conf`는 감지 결과를 채택할 최소 confidence 값이다. 값을 높이면 오탐은 줄지만 미탐이 늘고, 낮추면 반대가 된다. 손들기처럼 자세가 연속적으로 변하는 경우 threshold 하나로 오탐/미탐을 동시에 줄이기 어렵다.  
  → confidence를 단독으로 조정하는 대신, 기하학적 특징(윤곽 형태)을 추가 조건으로 사용해 판정 정확도를 높인다.  
  참고: https://docs.ultralytics.com/modes/predict/

- **OpenCV 구조 분석 — Contour, Solidity, Convex Hull** — Solidity는 `(실제 contour 면적) / (convex hull 면적)` 비율로, 값이 1에 가까울수록 볼록한 형태(팔을 올린 상태)를 나타낸다. `cv2.convexHull()`과 `cv2.contourArea()`로 계산한다. 손을 든 자세는 팔이 머리 위로 올라가 신체 외곽이 볼록해지므로, solidity가 높아지는 경향이 있다.  
  → `SOLIDITY_THRESH = 0.85`, `REACH_THRESH = 1.80`을 기준으로 YOLO class와 독립적으로 판정하고, 두 결과 중 기하학 판정을 우선한다.  
  참고: https://docs.opencv.org/4.x/d3/dc0/group__imgproc__shape.html

### 확인 절차

AI 화면 overlay를 확인한다.

```text
hands_up 0.xx
hands_down 0.xx
goal cooldown: ...s
GOAL (x,y)
```

AI 서버 로그에서 goal 생성 여부를 확인한다.

```text
[GOAL] pixel=(cx,cy) map=(mx,my)
```

### 해결 절차

1. YOLO confidence 기준을 확인한다.
   - `CONF = 0.40`

2. 기하학적 보정 기준값을 확인한다.
   - `SOLIDITY_THRESH = 0.85` — 외곽 대비 실제 면적 비율
   - `REACH_THRESH = 1.80` — 팔 뻗음 비율

3. figure bbox와 겹치는 pose만 유효하게 처리하는지 확인한다.
   - `hands_seat_ai.py` 723~726

4. 기하학적 판정 결과가 있으면 YOLO class보다 우선하는지 확인한다.
   - `hands_seat_ai.py` 728~730

5. goal cooldown을 확인한다.
   - `COOLDOWN_SEC = 5.0`: 같은 사람이 짧은 시간에 반복 발행되지 않도록 제한

### 재발 방지

- 오탐이 많으면 `CONF`, `SOLIDITY_THRESH`, `REACH_THRESH`를 함께 조정한다.
- confidence만 올리면 미탐이 늘 수 있으므로 화면 overlay와 goal log를 함께 본다.

---

## 7. Pixel 좌표가 map 좌표로 잘못 변환됨

### 증상

- `[GOAL] pixel=(...) map=(...)` 로그는 나오지만 robot이 엉뚱한 위치로 이동한다.
- map 좌표가 매장 영역 밖으로 나온다.

### 영향

- robot navigation goal이 잘못되어 충돌 또는 미동작이 발생한다.

### 원인

- Homography `H.npy`, map yaml resolution/origin, pgm height가 실제 map과 맞지 않는다.
- 카메라 위치가 바뀌었는데 이전 `H.npy`를 재사용하면 반드시 어긋난다.

### 공식 문서 근거

**Homography + map YAML 기반 좌표 변환 방식을 사용한 이유**

- **OpenCV — `findHomography` / `perspectiveTransform`** — Homography는 한 평면에서 다른 평면으로의 투영 변환을 나타내는 3×3 행렬이다. 카메라 영상(픽셀 좌표)과 실세계 바닥 평면(map 좌표) 사이의 대응점 쌍으로 `findHomography()`를 통해 구한다. 이후 `perspectiveTransform()`으로 임의의 픽셀 좌표를 실세계 좌표로 변환할 수 있다.  
  → 카메라 시점이 바뀌면 픽셀-실세계 대응 관계가 바뀌므로, 카메라 위치/각도/해상도 변경 시 반드시 `findHomography()`를 다시 실행해 `H.npy`를 갱신해야 한다.  
  참고: https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html

- **ROS2 Nav2 Map Server — map YAML 형식** — `map.yaml`의 `resolution`은 픽셀 1개가 실세계 몇 미터인지를, `origin`은 map pgm의 좌측 하단 픽셀이 ROS2 map frame 기준으로 어디에 있는지를 `[x, y, yaw]`로 정의한다. pgm 이미지 좌표는 상단이 y=0이고 ROS2 map frame은 하단이 y=0이므로 y축 반전 변환이 필요하다.  
  → `y = origin_y + (map_height - transformed_y) * resolution` 수식이 이 반전을 처리한다.  
  참고: https://nav2.org/configuration/packages/configuring-map-server.html

### 확인 절차

필요 파일이 있는지 확인한다.

```bash
ls -al robot_model/H.npy
ls -al robot_model/map/keepout_moosinsa.yaml
ls -al robot_model/map/keepout_moosinsa.pgm
```

AI 서버 시작 로그에서 map metadata를 확인한다.

```text
[INFO] 모델 로딩 중...
```

goal 로그를 확인한다.

```text
[GOAL] pixel=(cx,cy) map=(mx,my)
```

### 해결 절차

1. `H.npy`가 현재 카메라 위치 기준으로 생성된 파일인지 확인한다.

2. map yaml의 `resolution`, `origin`이 실제 navigation map과 일치하는지 확인한다.

3. `pixel_to_map_metric()` 변환식을 확인한다.
   - `x = origin_x + transformed_x * resolution`
   - `y = origin_y + (map_height - transformed_y) * resolution`
   - `hands_seat_ai.py` 324~338

4. 카메라 위치나 영상 resize가 바뀐 경우 homography를 다시 생성한다.

### 재발 방지

- 카메라 각도/해상도 변경 시 `H.npy`를 절대 재사용하지 않는다.
- map 파일이 바뀌면 `keepout_moosinsa.yaml`, `keepout_moosinsa.pgm`도 함께 갱신한다.

---

## 8. 좌석 점유 상태가 계속 흔들림

### 증상

- 같은 좌석이 `OCCUPIED`와 `EMPTY` 사이에서 반복 변경된다.
- target id가 자주 바뀐다.
- 피규어가 잠깐 가려지면 추적이 끊긴다.

### 영향

- backend가 좌석 사용 가능 여부를 잘못 판단한다.
- 시착 요청 처리에서 seat conflict가 발생한다.

### 원인

- detector 결과만으로 좌석 점유를 판단하면 frame 단위 감지 실패에 취약하다.
- tracker id는 영상 조건(조명, 가림)에 따라 바뀔 수 있다.

### 공식 문서 근거

**`persist=True` tracking + ReID + HSV histogram + debounce 조합을 사용한 이유**

- **Ultralytics Track Mode — `persist=True`** — `persist=True`는 현재 frame이 이전 frame sequence의 다음 frame임을 tracker에게 알려 track ID를 프레임 간에 유지한다. `False`로 두면 매 frame마다 tracker가 초기화되어 ID가 계속 바뀐다. 내부적으로 BoT-SORT 또는 ByteTrack 알고리즘이 IoU 기반으로 object를 매칭한다.  
  → 피규어가 잠시 가려지거나 감지 confidence가 낮아져도 이전 track과 연결되어 ID가 유지된다.  
  참고: https://docs.ultralytics.com/modes/track/

- **OpenCV — `cv2.calcHist()` HSV histogram** — BGR 대신 HSV 색공간에서 Hue, Saturation 채널의 histogram을 계산하면 조명 변화에 상대적으로 강건한 색상 특징을 얻을 수 있다. `cv2.compareHist()`로 두 histogram 간 유사도를 계산해 동일 피규어인지 판단한다.  
  → tracker ID가 바뀌어도 HSV histogram 유사도로 동일 피규어를 재식별(ReID)한다.  
  참고: https://docs.opencv.org/4.x/d6/dc7/group__imgproc__hist.html

- **torchvision — ResNet18 feature extractor** — 사전학습된 ResNet18의 마지막 classification layer를 제거하면 입력 이미지를 512차원 feature vector로 변환하는 feature extractor로 사용할 수 있다. 동일 피규어에서 추출한 feature vector는 cosine similarity가 높다.  
  → HSV histogram과 ResNet18 feature를 결합한 combined score로 ReID 정확도를 높인다.  
  참고: https://pytorch.org/vision/stable/models/resnet.html

### 확인 절차

AI 화면 overlay를 확인한다.

```text
#1 OCCUPIED
#2 EMPTY
Targets: ...
AutoTrack: ON
```

TCP payload에서 `seat_result`를 확인한다.

```json
{
  "type": "seat_result",
  "seat_status": [1, 0, 0, 1]
}
```

### 해결 절차

1. `model.track(..., persist=True)`가 사용되는지 확인한다.
   - `hands_seat_ai.py` 799~800

2. ReID feature와 HSV histogram이 함께 사용되는지 확인한다.
   - `hands_seat_ai.py` 193~234
   - combined score가 `SCORE_THRESH = 0.45` 이상일 때만 target 재식별로 인정한다.
   - `hands_seat_ai.py` 865

3. NMS로 중복 bbox가 제거되는지 확인한다.
   - `hands_seat_ai.py` 252~259

4. debounce가 적용되는지 확인한다.
   - `DEBOUNCE_SECS = 30`: 30초 동안 감지 수가 유지될 때만 stable count 변경
   - `hands_seat_ai.py` 819~831

5. 좌석 점유 판단이 figure bbox 중심이 seat ROI 안에 들어오는지 기준인지 확인한다.
   - `hands_seat_ai.py` 839~841

6. seat result 전송 간격을 확인한다.
   - `SEAT_SEND_INTERVAL = 1.0`초
   - `hands_seat_ai.py` 683~694

### 재발 방지

- 테스트 시작 전 `seats.json`에 저장된 좌석 ROI 위치가 맞는지 확인한다.
- 카메라 위치가 바뀌면 ROI를 다시 등록한다.
- tracker id만 믿지 말고 ReID fallback을 항상 유지한다.

---

## 9. 통합 테스트 시 어느 구간에서 끊겼는지 모름

### 증상

- 화면은 들어오는데 backend 결과가 없다.
- backend는 기다리는데 AI 서버 로그에는 감지가 있다.
- TCP 결과는 있는데 ROS2 goal이 없다.

### 영향

- 장애 원인 구간을 찾는 데 시간이 오래 걸린다.

### 점검 순서

**Step 1** — UDP packet이 메인 서버에 들어오는지 확인한다.

```bash
python3 moosinsa_service_test.py
```

**Step 2** — 로봇 UDP 수신 로그를 확인한다.

```text
[로봇] from=... robot=... frame=... chunk=... bytes=... 총전송=...
```

**Step 3** — 카메라 JSON 수신 로그를 확인한다.

```text
[카메라] from=... robot=... frame=... size=... process=... type=...
```

**Step 4** — AI TCP 결과 수신 로그를 확인한다.

```text
[AI 결과 수신]
robot_id:
frame_id:
type:
process_ms:
```

**Step 5** — ROS2 bridge 통계 로그를 확인한다.

```text
[STATS] vision=... seat=... goal_pub=... dropped=... err=...
```

### 구간별 판단

| 증상 | 끊긴 구간 |
|------|-----------|
| `[로봇]` 로그 없음 | 로봇 → 메인 서버 UDP |
| `[AI 결과 수신]` 없음 | AI 서버 추론 또는 TCP 전송 |
| `[STATS] goal_pub=0` 유지 | TCP 결과 파싱 또는 ROS2 publish |
| `ros2 topic echo` 비어 있음 | ROS2 bridge 내부 또는 DDS |

### 재발 방지

- 장애 보고 시 반드시 아래 4개 로그를 함께 첨부한다.
  - AI 서버: `[UDP]`, `[MODEL_IN]`, `[GOAL]`
  - TCP bridge: `[VISION]`, `[STATS]`
  - ROS2: `ros2 topic info --verbose /hand_raise_goal`
  - 테스트 스크립트: `[AI 결과 수신]`


---

## 운영 체크리스트

### AI 서버 시작 전

```bash
cd /home/team1-ai/roscamp-repo-1/services/ai_server

# 필수 파일 확인
ls -al robot_model/H.npy
ls -al robot_model/map/keepout_moosinsa.yaml
ls -al robot_model/map/keepout_moosinsa.pgm
ls -al robot_model/staindig/best.pt
ls -al robot_model/figure/best.pt
ls -al seats.json
```

### 포트 상태 확인

```bash
ss -lunp | grep ':7018'   # UDP 영상 수신
ss -ltnp | grep ':7019'   # TCP AI 결과
```

### ROS2 상태 확인

```bash
ros2 topic list -t
ros2 topic info /hand_raise_goal --verbose
ros2 topic hz /hand_raise_goal
ros2 topic echo /hand_raise_goal
```

### Fast DDS SHM 문제 의심 시

```bash
ls -al /dev/shm | grep fastrtps
rm -f /dev/shm/fastrtps_*
```

---

## 장애 보고 템플릿

```
[증상]


[발생 시각]


[영향 범위]
- UDP 수신:
- AI 추론:
- TCP 결과:
- ROS2 goal:

[확인한 로그]
- [UDP]:
- [MODEL_IN]:
- [GOAL]:
- [VISION]:
- [STATS]:

[확인 명령 결과]
- ss -lunp | grep ':7018':
- ss -ltnp | grep ':7019':
- ros2 topic info /hand_raise_goal --verbose:

[조치]


[재발 방지]
```
