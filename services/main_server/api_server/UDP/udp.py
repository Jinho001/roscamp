#!/usr/bin/env python3
"""
back_test.py  — Main Server (192.168.1.120)  UDP 수신/중계 전용

  수신: TOP_VIEW_Camera (192.168.1.9)  UDP binary 청크  [port 7017]
  중계: AI Server     (192.168.1.121)  UDP binary        [port 7018]

  TCP 수신/로봇 전달은 tcp_result_receiver.py 에서 담당.

패킷 포맷 (final_udp_top.py 동일):
  헤더 12B big-endian: [magic 2B][frame_id 4B][chunk_idx 2B][total 2B][pkt_type 1B][reserved 1B]
  chunk 0 payload    : [meta_len 2B][JSON meta][JPEG 시작...]
  chunk 1+ payload   : [JPEG 연속...]
  HEARTBEAT          : pkt_type=0x01, payload=JSON {"type":"heartbeat", ...}
"""

import socket
import json
import time
import struct
import math
import threading
import logging
import os
from pathlib import Path


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

# ─────────────────────────────────────────────────────────────────────────────
# 주소 / 포트 설정
# ─────────────────────────────────────────────────────────────────────────────
ENV = load_env()
CAMERA_LISTEN_IP = env_str(ENV, "CAM_LISTEN_IP", "0.0.0.0")
CAMERA_UDP_PORT  = env_int(ENV, "CAM_LISTEN_PORT", 7017)        # 카메라로부터 수신
AI_SERVER_IP     = env_str(ENV, "YOLO_SERVER_IP", "192.168.1.121")
AI_UDP_PORT      = env_int(ENV, "YOLO_SERVER_PORT", 7018)       # AI 서버로 중계

UDP_RECV_BUF    = 8 * 1024 * 1024   # 8 MB: 연속 청크 수신 시 OS 버퍼 오버플로 방지
UDP_SEND_BUF    = 4 * 1024 * 1024   # 4 MB
UDP_MAX_PACKET  = 65535

# ─────────────────────────────────────────────────────────────────────────────
# 패킷 포맷 (final_udp_top.py 와 동일)
# ─────────────────────────────────────────────────────────────────────────────
MAGIC         = 0xA55A
PKT_DATA      = 0x00
PKT_HEARTBEAT = 0x01

HDR_FMT  = '!HIHHBB'
HDR_SIZE = struct.calcsize(HDR_FMT)   # 12 bytes

RELAY_CHUNK_SIZE  = 60_000
FRAME_EXPIRE_SEC  = 2.0    # 미완성 프레임 만료 시간
MAX_FRAME_BUFFER  = 16     # 동시 버퍼링 최대 프레임 수
HEARTBEAT_TIMEOUT = 5.0    # 이 시간 이상 HB 없으면 경고

# ─────────────────────────────────────────────────────────────────────────────
# 로거
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(message)s",
    datefmt= "%H:%M:%S",
)
log = logging.getLogger("main_srv")

# ─────────────────────────────────────────────────────────────────────────────
# 통계
# ─────────────────────────────────────────────────────────────────────────────
_stats = {
    "udp_recv"       : 0,
    "frame_complete" : 0,
    "frame_expire"   : 0,   # 만료로 버린 미완성 프레임
    "frame_overflow" : 0,   # 버퍼 초과로 버린 프레임
    "heartbeat"      : 0,
    "udp_errors"     : 0,
    "last_camera_hb" : 0.0,
}
_stats_lock = threading.Lock()

# ── 백로그 파일 (터미널 대신 파일에 프레임 단위 기록) ───────────────────
_BACKLOG_PATH = Path(__file__).parent / "udp_backlog.log"
_BACKLOG_MAX  = 30
_backlog_lock = threading.Lock()

def _write_backlog(line: str):
    with _backlog_lock:
        lines = []
        if _BACKLOG_PATH.exists():
            lines = _BACKLOG_PATH.read_text(encoding="utf-8").splitlines()
        lines.append(line)
        if len(lines) > _BACKLOG_MAX:
            lines = lines[-_BACKLOG_MAX:]
        _BACKLOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

_status_lock = threading.Lock()

def _print_udp_status(frame_id: int, robot_id, jpeg_size: int):
    """터미널 한 줄을 \r로 덮어써 최신 송수신 상태만 표시한다."""
    with _status_lock:
        print(
            f"\r[UDP] 송수신 완료  frame={frame_id}  robot={robot_id}"
            f"  {jpeg_size:,}B → {AI_SERVER_IP}:{AI_UDP_PORT}   ",
            end="", flush=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# 청크 재조립 버퍼
# ─────────────────────────────────────────────────────────────────────────────
class FrameBuffer:
    """
    frame_id 단위로 UDP 청크를 모아 완성된 (meta, jpeg) 를 반환한다.
    오래된 미완성 프레임은 자동으로 만료 처리한다.
    """
    def __init__(self):
        self._buf  = {}
        self._lock = threading.Lock()

    def add_chunk(self, frame_id: int, chunk_idx: int, total: int, payload: bytes):
        """
        청크 추가. 프레임 완성 시 (meta_dict, jpeg_bytes) 반환, 미완성이면 None.
        """
        with self._lock:
            now = time.time()

            # 만료된 프레임 정리
            expired = [fid for fid, v in self._buf.items()
                       if now - v["t0"] > FRAME_EXPIRE_SEC]
            for fid in expired:
                del self._buf[fid]
            if expired:
                with _stats_lock:
                    _stats["frame_expire"] += len(expired)

            # 버퍼 용량 초과 → 가장 오래된 프레임 제거
            overflow = 0
            while len(self._buf) >= MAX_FRAME_BUFFER:
                oldest = min(self._buf, key=lambda k: self._buf[k]["t0"])
                del self._buf[oldest]
                overflow += 1
            if overflow:
                with _stats_lock:
                    _stats["frame_overflow"] += overflow

            entry = self._buf.setdefault(
                frame_id, {"chunks": {}, "total": total, "t0": now}
            )
            entry["chunks"][chunk_idx] = payload
            entry["total"] = total

            if len(entry["chunks"]) < total:
                return None

            # 모든 청크 수집 완료 → 재조립
            full = b"".join(entry["chunks"][i] for i in range(total))
            del self._buf[frame_id]
            return self._parse(full)

    @staticmethod
    def _parse(payload: bytes):
        """payload = [meta_len 2B][JSON meta][JPEG bytes]"""
        if len(payload) < 2:
            return None
        meta_len = struct.unpack('!H', payload[:2])[0]
        if len(payload) < 2 + meta_len:
            return None
        try:
            meta = json.loads(payload[2:2 + meta_len].decode('utf-8'))
        except json.JSONDecodeError:
            meta = {}
        return meta, payload[2 + meta_len:]


# ─────────────────────────────────────────────────────────────────────────────
# 헤더 빌더
# ─────────────────────────────────────────────────────────────────────────────
def build_udp_header(frame_id: int, chunk_idx: int, total: int, pkt_type: int) -> bytes:
    return struct.pack(HDR_FMT, MAGIC, frame_id & 0xFFFFFFFF,
                       chunk_idx, total, pkt_type, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 완성 프레임 → AI 서버 중계 (동일 binary 포맷으로 재청킹)
# ─────────────────────────────────────────────────────────────────────────────
def send_udp_message(sock: socket.socket, frame_id: int, meta: dict, jpeg: bytes):
    meta_b  = json.dumps(meta).encode('utf-8')
    payload = struct.pack('!H', len(meta_b)) + meta_b + jpeg
    total   = math.ceil(len(payload) / RELAY_CHUNK_SIZE)
    dest    = (AI_SERVER_IP, AI_UDP_PORT)
    for i in range(total):
        chunk = payload[i * RELAY_CHUNK_SIZE:(i + 1) * RELAY_CHUNK_SIZE]
        sock.sendto(build_udp_header(frame_id, i, total, PKT_DATA) + chunk, dest)


# ─────────────────────────────────────────────────────────────────────────────
# 카메라 UDP 수신 → 재조립 → AI 서버 중계
# ─────────────────────────────────────────────────────────────────────────────
def camera_udp_server():
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # [BUG-FIX①] 재시작 시 포트 즉시 재사용
    recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RECV_BUF)
    recv_sock.bind((CAMERA_LISTEN_IP, CAMERA_UDP_PORT))

    fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fwd_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, UDP_SEND_BUF)

    frame_buf = FrameBuffer()

    while True:
        try:
            data, addr = recv_sock.recvfrom(UDP_MAX_PACKET)

            # 최소 헤더 길이 확인
            if len(data) < HDR_SIZE:
                with _stats_lock:
                    _stats["udp_errors"] += 1
                continue

            magic, frame_id, chunk_idx, total, pkt_type, _ = struct.unpack(
                HDR_FMT, data[:HDR_SIZE]
            )

            # [BUG-FIX④] chunk_idx / total 범위 검증
            if total == 0 or chunk_idx >= total:
                log.warning("잘못된 청크 헤더: frame=%d idx=%d total=%d from=%s",
                            frame_id, chunk_idx, total, addr)
                with _stats_lock:
                    _stats["udp_errors"] += 1
                continue

            # Magic 검증: 다른 UDP 소스의 노이즈 패킷 걸러냄
            if magic != MAGIC:
                with _stats_lock:
                    _stats["udp_errors"] += 1
                log.debug("MAGIC 불일치 from %s: 0x%04X", addr, magic)
                continue

            with _stats_lock:
                _stats["udp_recv"] += 1

            payload = data[HDR_SIZE:]

            # ── HEARTBEAT ──────────────────────────────────────────────────
            if pkt_type == PKT_HEARTBEAT:
                try:
                    hb = json.loads(payload.decode('utf-8'))
                    with _stats_lock:
                        _stats["heartbeat"]     += 1
                        _stats["last_camera_hb"] = time.time()
                    log.debug("HB  robot=%s frame=%s from=%s",
                             hb.get("robot_id"), hb.get("frame_id"), addr)
                except Exception:
                    pass
                continue

            # ── DATA 청크 재조립 ───────────────────────────────────────────
            result = frame_buf.add_chunk(frame_id, chunk_idx, total, payload)
            if result is None:
                continue

            meta, jpeg = result
            with _stats_lock:
                _stats["frame_complete"] += 1

            _write_backlog(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} [UDP 송수신 완료] "
                f"frame={frame_id} robot={meta.get('robot_id')} "
                f"size={len(jpeg)}B → {AI_SERVER_IP}:{AI_UDP_PORT}"
            )
            _print_udp_status(frame_id, meta.get("robot_id"), len(jpeg))

            try:
                send_udp_message(fwd_sock, frame_id, meta, jpeg)
            except Exception as e:
                log.error("AI relay error: %s", e)

        except OSError as e:
            log.error("UDP recv error: %s", e)
        except Exception as e:                              # [BUG-FIX②] struct/메모리 등 예상외 예외도 잡아서 루프 유지
            log.exception("Unexpected error in UDP loop: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# 카메라 하트비트 감시 (타임아웃 경고)
# ─────────────────────────────────────────────────────────────────────────────
def watchdog_thread():
    """1초 간격으로 체크해 HEARTBEAT_TIMEOUT 초 이상 무응답이면 경고."""
    while True:
        time.sleep(1.0)                                     # [BUG-FIX③] 1초 간격 체크 (기존 5초 sleep → 최대 10초 지연 문제 해결)
        with _stats_lock:
            last_hb = _stats["last_camera_hb"]
        if last_hb > 0 and (time.time() - last_hb) > HEARTBEAT_TIMEOUT:
            log.warning("카메라 하트비트 타임아웃! (%.1f초 무응답)",
                        time.time() - last_hb)


# ─────────────────────────────────────────────────────────────────────────────
# 통계 출력 스레드 (5초 주기)
# ─────────────────────────────────────────────────────────────────────────────
def stats_thread():
    while True:
        time.sleep(5)
        with _stats_lock:
            s = _stats.copy()
        hb_ago = f"{time.time() - s['last_camera_hb']:.1f}s ago" \
                 if s["last_camera_hb"] else "never"
        log.info(
            "[STATS] udp_recv=%d frame_ok=%d expire=%d overflow=%d "
            "hb=%d(%s) err=%d",
            s["udp_recv"], s["frame_complete"],
            s["frame_expire"], s["frame_overflow"],
            s["heartbeat"], hb_ago, s["udp_errors"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────
class CameraUDPRelay:
    """moosinsa_service.py 에서 임포트해 사용하는 UDP 중계 클래스."""

    def __init__(self):
        self._udp_thread      = threading.Thread(target=camera_udp_server, daemon=True)
        self._watchdog_thread = threading.Thread(target=watchdog_thread,   daemon=True)

    def start(self):
        self._watchdog_thread.start()
        self._udp_thread.start()
        log.info("[UDP] 카메라 수신 시작: %s:%d → AI %s:%d",
                 CAMERA_LISTEN_IP, CAMERA_UDP_PORT, AI_SERVER_IP, AI_UDP_PORT)


def main():
    log.info("=" * 60)
    log.info("[MAIN SERVER] 시작")
    log.info("  .env 경로        : %s", ENV_PATH)
    log.info("  카메라 UDP 수신  : %s:%d", CAMERA_LISTEN_IP, CAMERA_UDP_PORT)
    log.info("  AI 서버 UDP 중계 : %s:%d", AI_SERVER_IP, AI_UDP_PORT)
    log.info("=" * 60)

    threading.Thread(target=stats_thread,    daemon=True).start()
    threading.Thread(target=watchdog_thread, daemon=True).start()

    try:
        camera_udp_server()
    except KeyboardInterrupt:
        log.info("[MAIN SERVER] 종료")


if __name__ == "__main__":
    main()
