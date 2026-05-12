#!/usr/bin/env python3
"""
tcp_result_receiver.py  — Main Server (192.168.1.120)

AI 서버 (192.168.1.121) TCP 결과 수신 → ROS2 PoseStamped 발행

프로토콜: [4B big-endian 길이][JSON 페이로드]  (hands_seat_ai.py 송신 포맷과 동일)
"""

import argparse
import json
import logging
import math
import os
import socket
import struct
import threading
import time
from pathlib import Path

import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "main_server", ".env"))


def load_env(path: str = ENV_PATH) -> dict:
    env = {}
    if not os.path.exists(path):
        return env

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.replace("\ufeff", "").replace("\xa0", " ").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.split("#", 1)[0].strip().strip('"').strip("'")
            if key:
                env[key] = value

    return env


def env_str(env: dict, key: str, default: str) -> str:
    value = env.get(key)
    return value if value not in (None, "") else default


def env_int(env: dict, key: str, default: int) -> int:
    try:
        return int(env.get(key, default))
    except (TypeError, ValueError):
        return default


# ── 기본 설정 ──────────────────────────────────────────────────────────
ENV = load_env()
AI_SERVER_IP     = env_str(ENV, "YOLO_SERVER_IP", "192.168.1.121")
AI_TCP_PORT      = env_int(ENV, "YOLO_RESULT_LISTEN_PORT", 7019)
TOPIC_NAME       = "/hand_raise_goal"

HEADER_SIZE      = 4
MAX_JSON_SIZE    = 20 * 1024 * 1024   # 20 MB 상한 — 초과 시 프로토콜 오류로 처리
RECV_TIMEOUT_SEC = 30.0               # 이 시간 동안 데이터 없으면 dead connection으로 판단
RECONNECT_SEC    = 3.0
DEFAULT_COOLDOWN = 1.0


# ── 소켓 헬퍼 ─────────────────────────────────────────────────────────
def _set_keepalive(sock: socket.socket, idle: int = 10, intvl: int = 3, cnt: int = 5):
    """TCP keepalive: idle초 후 intvl 간격으로 cnt번 probe → dead connection 조기 감지."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket, "TCP_KEEPIDLE"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE,  idle)
    if hasattr(socket, "TCP_KEEPINTVL"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, intvl)
    if hasattr(socket, "TCP_KEEPCNT"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT,   cnt)


def recv_exact_bytes(sock: socket.socket, n: int):
    """정확히 n바이트를 읽는다. 연결 끊기면 None 반환."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


# ══════════════════════════════════════════════════════════════════════
# ROS2 노드
# ══════════════════════════════════════════════════════════════════════
class TcpGoalBridge(Node):
    def __init__(self, topic_name: str, frame_id: str, cooldown_sec: float):
        super().__init__("tcp_result_to_hand_raise_goal")
        self._pub        = self.create_publisher(PoseStamped, topic_name, 10)
        self._frame_id   = frame_id
        self._cooldown   = cooldown_sec
        self._last_pub_t = 0.0
        self._lock       = threading.Lock()

        # 통계
        self._stat = {"vision": 0, "seat": 0, "goal_pub": 0, "dropped": 0, "err": 0}

        self.get_logger().info(f"ROS2 topic  : {topic_name}")
        self.get_logger().info(f"frame_id    : {frame_id}")
        self.get_logger().info(f"goal cooldown: {cooldown_sec}s")

    # ── 결과 디스패치 ─────────────────────────────────────────────────
    def handle_result(self, result: dict, raw_size: int):
        rtype = result.get("type", "")
        if rtype == "vision_result":
            self._stat["vision"] += 1
            self._log_vision(result, raw_size)
            self._publish_goals(result)
        elif rtype == "seat_result":
            self._stat["seat"] += 1
        else:
            self.get_logger().warning(f"알 수 없는 result type: {rtype!r}")

    # ── vision_result 로깅 ────────────────────────────────────────────
    def _log_vision(self, result: dict, raw_size: int):
        sent_ts    = result.get("timestamp", 0)
        latency_ms = (time.time() - sent_ts) * 1000.0 if sent_ts else 0.0
        dets       = result.get("detections", [])
        goals      = result.get("goals", [])
        self.get_logger().info(
            f"[VISION] robot={result.get('robot_id')} frame={result.get('frame_id')} "
            f"dets={len(dets)} goals={len(goals)} "
            f"ai={result.get('process_ms')}ms latency={latency_ms:.1f}ms bytes={raw_size}"
        )
        for i, d in enumerate(dets):
            self.get_logger().debug(
                f"  [{i}] {d.get('class_name')} conf={d.get('confidence')} bbox={d.get('bbox')}"
            )

    # ── goal 발행 ─────────────────────────────────────────────────────
    def _publish_goals(self, result: dict):
        robot_id = result.get("robot_id")
        frame_id = result.get("frame_id")
        for goal in result.get("goals", []):
            if goal.get("pose") != "hands_up":
                continue

            with self._lock:
                now = time.time()
                if now - self._last_pub_t < self._cooldown:
                    self._stat["dropped"] += 1
                    continue
                self._last_pub_t = now

            map_g = goal.get("map", {})
            try:
                x     = float(map_g["x"])
                y     = float(map_g["y"])
                theta = float(map_g.get("theta", 0.0))
            except (KeyError, TypeError, ValueError):
                self.get_logger().warning(f"map 데이터 오류: {goal}")
                self._stat["err"] += 1
                continue

            msg = PoseStamped()
            msg.header.stamp       = self.get_clock().now().to_msg()
            msg.header.frame_id    = self._frame_id
            msg.pose.position.x    = x
            msg.pose.position.y    = y
            msg.pose.position.z    = 0.0
            msg.pose.orientation.z = math.sin(theta / 2.0)
            msg.pose.orientation.w = math.cos(theta / 2.0)
            self._pub.publish(msg)
            self._stat["goal_pub"] += 1

            self.get_logger().info(
                f"  → GOAL robot={robot_id} frame={frame_id} "
                f"x={x:.3f} y={y:.3f} theta={theta:.3f}"
            )

    # ── 5초 통계 ─────────────────────────────────────────────────────
    def log_stats(self):
        s = self._stat
        self.get_logger().info(
            f"[STATS] vision={s['vision']} seat={s['seat']} "
            f"goal_pub={s['goal_pub']} dropped={s['dropped']} err={s['err']}"
        )


# ══════════════════════════════════════════════════════════════════════
# TCP 수신
# ══════════════════════════════════════════════════════════════════════
def receive_tcp_message(sock: socket.socket):
    """cv_server.py 의 send_tcp_message() 와 짝이 되는 [4B 길이][JSON] 1건 수신."""
    hdr = recv_exact_bytes(sock, HEADER_SIZE)
    if hdr is None:
        raise ConnectionError("AI 서버 연결 끊김 (헤더)")

    msg_len = struct.unpack("!I", hdr)[0]
    if msg_len <= 0 or msg_len > MAX_JSON_SIZE:
        raise ValueError(f"비정상 JSON 크기: {msg_len}B")

    body = recv_exact_bytes(sock, msg_len)
    if body is None:
        raise ConnectionError("AI 서버 연결 끊김 (바디)")

    result = json.loads(body.decode("utf-8"))
    return result, msg_len


def receive_tcp_messages(sock: socket.socket, bridge: TcpGoalBridge):
    """[4B 길이][JSON] 스트림을 반복 수신."""
    while True:
        try:
            result, msg_len = receive_tcp_message(sock)
        except json.JSONDecodeError as e:
            bridge.get_logger().error(f"JSON 파싱 오류 (1패킷 스킵): {e}")
            continue

        bridge.handle_result(result, msg_len)


# ══════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════
# YOLOResultServer — moosinsa_service.py 에서 임포트해 사용
# AI 서버(tcp_main_ai.py)가 TCP로 접속해 결과를 전송하면 수신하는 서버
# ══════════════════════════════════════════════════════════════════════

_TCP_BACKLOG_PATH = Path(__file__).parent / "tcp_backlog.log"
_TCP_BACKLOG_MAX  = 30
_tcp_backlog_lock = threading.Lock()

def _write_tcp_backlog(line: str):
    with _tcp_backlog_lock:
        lines = []
        if _TCP_BACKLOG_PATH.exists():
            lines = _TCP_BACKLOG_PATH.read_text(encoding="utf-8").splitlines()
        lines.append(line)
        if len(lines) > _TCP_BACKLOG_MAX:
            lines = lines[-_TCP_BACKLOG_MAX:]
        _TCP_BACKLOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

_tcp_log = logging.getLogger("tcp_result")


class YOLOResultServer:
    """
    AI 서버(tcp_main_ai.py)가 접속해 추론 결과를 전송하면 수신하는 TCP 서버.
    결과는 tcp_backlog.log 에 기록되고 터미널에는 연결 이벤트만 출력된다.
    수신 프로토콜: [4B big-endian 길이][JSON bytes]
    """

    def __init__(self, listen_ip: str, listen_port: int,
                 robot_bridge=None, on_seat_status=None):
        self.listen_ip        = listen_ip
        self.listen_port      = listen_port
        self.robot_bridge     = robot_bridge
        self._on_seat_status  = on_seat_status
        self.latest_result: dict | None       = None
        self.latest_seat_status: list | None  = None
        self._lock   = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        _tcp_log.info("[TCP] YOLO 결과 서버 시작: %s:%d", self.listen_ip, self.listen_port)

    def get_latest(self) -> dict | None:
        with self._lock:
            return self.latest_result

    def get_latest_seat_status(self) -> list | None:
        with self._lock:
            return self.latest_seat_status

    def _run(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server_sock.bind((self.listen_ip, self.listen_port))
        except OSError as e:
            _tcp_log.error("[TCP] bind 실패 %s:%d — %s", self.listen_ip, self.listen_port, e)
            return
        server_sock.listen(5)
        while True:
            try:
                conn, addr = server_sock.accept()
                _tcp_log.info("[TCP] AI 서버 연결: %s", addr)
                threading.Thread(target=self._handle_conn, args=(conn, addr), daemon=True).start()
            except Exception as e:
                _tcp_log.error("[TCP] accept 오류: %s", e)

    def _handle_conn(self, conn: socket.socket, addr):
        try:
            while True:
                raw_len = recv_exact_bytes(conn, 4)
                if not raw_len:
                    return
                length = struct.unpack("!I", raw_len)[0]
                if length <= 0 or length > MAX_JSON_SIZE:
                    raise ValueError(f"비정상 JSON 크기: {length}B")

                raw_data = recv_exact_bytes(conn, length)
                if not raw_data:
                    return

                result = json.loads(raw_data.decode("utf-8"))
                msg_type = result.get("type") or ("seat_status" if "seats" in result else None)
                ts = time.strftime("%Y-%m-%d %H:%M:%S")

                if msg_type in ("seat_status", "seat_result"):
                    seats = result.get("seats")
                    if seats is None:
                        seats = result.get("seat_status")
                    with self._lock:
                        self.latest_seat_status = seats
                    _write_tcp_backlog(f"{ts} [TCP 수신 완료] seat_result seat_status={seats}")
                    if self._on_seat_status is not None:
                        self._on_seat_status(seats)
                else:
                    with self._lock:
                        self.latest_result = result
                    _write_tcp_backlog(
                        f"{ts} [TCP 수신 완료] "
                        f"type={result.get('type')} "
                        f"frame_id={result.get('frame_id')} "
                        f"goals={len(result.get('goals') or [])} "
                        f"process_ms={result.get('process_ms')}ms"
                    )
                    if self.robot_bridge is not None:
                        self.robot_bridge.handle_result(result)
                    self._forward_to_cam_ui(raw_data)

        except Exception as e:
            _tcp_log.error("[TCP] 처리 오류: %s", e)
        finally:
            conn.close()

    def _forward_to_cam_ui(self, raw_data: bytes):
        cam_ui_ip   = os.getenv("CAM_UI_IP")
        cam_ui_port = os.getenv("CAM_UI_PORT")
        if not cam_ui_ip or not cam_ui_port:
            return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((cam_ui_ip, int(cam_ui_port)))
            s.sendall(struct.pack("!I", len(raw_data)) + raw_data)
            s.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="AI TCP 결과 → ROS2 PoseStamped 브릿지")
    parser.add_argument("--host",     default=AI_SERVER_IP,   help="AI 서버 IP")
    parser.add_argument("--port",     type=int, default=AI_TCP_PORT)
    parser.add_argument("--topic",    default=TOPIC_NAME)
    parser.add_argument("--frame-id", default="map")
    parser.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN,
                        help="goal 발행 최소 간격(초)")
    args = parser.parse_args()

    rclpy.init()
    bridge = TcpGoalBridge(args.topic, args.frame_id, args.cooldown)

    # ROS2 spin을 별도 스레드로 분리 → TCP recv 블로킹 중에도 ROS2 콜백 유지
    spin_thread = threading.Thread(target=rclpy.spin, args=(bridge,), daemon=True)
    spin_thread.start()

    bridge.get_logger().info("=" * 55)
    bridge.get_logger().info("[AI TCP → ROS2 BRIDGE] 시작")
    bridge.get_logger().info(f"  .env 경로: {ENV_PATH}")
    bridge.get_logger().info(f"  연결 대상: {args.host}:{args.port}")
    bridge.get_logger().info(f"  프로토콜 : [4B 길이][JSON]")
    bridge.get_logger().info(f"  ROS2 토픽: {args.topic}")
    bridge.get_logger().info("=" * 55)

    last_stat_t = time.time()

    try:
        while rclpy.ok():
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)                    # connect 타임아웃
                sock.connect((args.host, args.port))
                sock.settimeout(RECV_TIMEOUT_SEC)       # recv 타임아웃 (dead connection 감지)
                _set_keepalive(sock)
                bridge.get_logger().info(f"[TCP] 연결됨: {args.host}:{args.port}")
                receive_tcp_messages(sock, bridge)

            except (OSError, ConnectionError) as e:
                bridge.get_logger().warning(f"[TCP] {e} — {RECONNECT_SEC}s 후 재시도")
            except ValueError as e:
                bridge.get_logger().error(f"[TCP] 프로토콜 오류: {e} — 재연결")
            except Exception as e:
                bridge.get_logger().error(f"[TCP] 예외: {e} — 재연결")
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

            if time.time() - last_stat_t >= 5.0:
                bridge.log_stats()
                last_stat_t = time.time()

            time.sleep(RECONNECT_SEC)

    except KeyboardInterrupt:
        bridge.get_logger().info("[STOP] 종료")
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
