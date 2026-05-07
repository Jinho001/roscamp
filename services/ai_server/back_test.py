import socket
import json
import time
import threading
import base64
import cv2
import numpy as np

# =========================
# 주소 설정
# =========================
MAIN_SERVER_IP   = "192.168.1.120"
CAMERA_UDP_PORT  = 7017

AI_SERVER_IP     = "192.168.1.121"
AI_UDP_PORT      = 7018
AI_TCP_PORT      = 7019

ROS_TARGET_IP    = "192.168.1.113"
ROS_TARGET_PORT  = 7020

UDP_BUFFER_SIZE  = 65535
TCP_BUFFER_SIZE  = 65535
TCP_RECONNECT_SEC = 3.0

# =========================
# 검증 옵션
# =========================
SHOW_PREVIEW = False   # cv2 윈도우 표시 여부
DISPLAY_MAX_W = 960
DISPLAY_MAX_H = 540

CAMERA_IMAGE_KEYS = ("image_b64", "frame_b64", "jpeg_b64", "jpg_b64", "preview_b64")
AI_IMAGE_KEYS = ("annotated_b64", "result_b64", "image_b64", "frame_b64", "jpeg_b64", "jpg_b64", "preview_b64")

# =========================
# 공유 상태
# =========================
latest_ai_result   = None
ai_result_lock     = threading.Lock()

latest_camera_frame = None
camera_frame_lock   = threading.Lock()

# 통계
_stats = {
    "udp_recv":   0,
    "tcp_recv":   0,
    "udp_errors": 0,
    "tcp_errors": 0,
    "last_latency_ms": 0.0,
}
_stats_lock = threading.Lock()


def decode_image_from_payload(payload: dict, keys):
    """가능하면 preview보다 원본/고해상도 이미지 필드를 우선 사용."""
    for key in keys:
        image_b64 = payload.get(key)
        if not image_b64:
            continue

        try:
            img_bytes = base64.b64decode(image_b64)
            img_array = np.frombuffer(img_bytes, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        except Exception as e:
            print(f"[PREVIEW] Decode error for {key}: {e}")
            continue

        if frame is not None:
            return frame, key

    return None, None


def resize_for_display(frame, max_w=DISPLAY_MAX_W, max_h=DISPLAY_MAX_H):
    h, w = frame.shape[:2]
    scale = min(max_w / w, max_h / h)

    if scale <= 0:
        return frame

    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))

    if new_w == w and new_h == h:
        return frame

    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4
    return cv2.resize(frame, (new_w, new_h), interpolation=interpolation)


# =========================
# AI 결과 수신 (TCP client)
# =========================
def ai_tcp_receiver():
    global latest_ai_result

    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((AI_SERVER_IP, AI_TCP_PORT))
            print(f"[TCP] Connected to AI server {AI_SERVER_IP}:{AI_TCP_PORT}")

            buf = b""
            while True:
                chunk = sock.recv(TCP_BUFFER_SIZE)
                if not chunk:
                    print("[TCP] AI server disconnected")
                    break
                buf += chunk

                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        result = json.loads(line.decode("utf-8"))

                        # 수신 시각 기록 → 지연 계산
                        sent_ts = result.get("timestamp", 0)
                        latency_ms = (time.time() - sent_ts) * 1000.0 if sent_ts else 0.0

                        with ai_result_lock:
                            latest_ai_result = result
                        with _stats_lock:
                            _stats["tcp_recv"] += 1
                            _stats["last_latency_ms"] = latency_ms

                        dets = result.get("detections", [])
                        print(
                            f"[TCP ✓] robot={result.get('robot_id')} "
                            f"frame={result.get('frame_id')} "
                            f"detections={len(dets)} "
                            f"ai_process={result.get('process_ms')}ms "
                            f"latency={latency_ms:.1f}ms"
                        )
                        for i, d in enumerate(dets):
                            print(
                                f"        [{i}] {d.get('class_name')} "
                                f"conf={d.get('confidence')} "
                                f"bbox={d.get('bbox')}"
                            )

                        send_to_raspberry(result)

                    except json.JSONDecodeError as e:
                        with _stats_lock:
                            _stats["tcp_errors"] += 1
                        print(f"[TCP ✗] JSON parse error: {e}")

        except (ConnectionRefusedError, OSError) as e:
            print(f"[TCP] Connection failed: {e} — retry in {TCP_RECONNECT_SEC}s")
        finally:
            try:
                sock.close()
            except Exception:
                pass
            time.sleep(TCP_RECONNECT_SEC)


# =========================
# 라즈베리파이4 전달
# =========================
_ros_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_to_raspberry(result: dict):
    try:
        payload = json.dumps(result).encode("utf-8")
        _ros_sock.sendto(payload, (ROS_TARGET_IP, ROS_TARGET_PORT))
        print(f"[ROS/UDP] Sent: robot={result.get('robot_id')} frame={result.get('frame_id')} {len(payload)}bytes")
    except Exception as e:
        print(f"[ROS/UDP] Send error: {e}")


# =========================
# 카메라 UDP 수신 + AI 포워딩
# =========================
def camera_udp_server():
    global latest_camera_frame

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", CAMERA_UDP_PORT))
    print(f"[UDP] Listening for camera frames on port {CAMERA_UDP_PORT}")

    forward_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    while True:
        try:
            data, addr = sock.recvfrom(UDP_BUFFER_SIZE)
        except OSError as e:
            print(f"[UDP] Recv error: {e}")
            continue

        try:
            payload = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as e:
            with _stats_lock:
                _stats["udp_errors"] += 1
            print(f"[UDP ✗] JSON parse error from {addr}: {e}")
            continue

        robot_id = payload.get("robot_id", "?")
        frame_id = payload.get("frame_id", "?")
        with _stats_lock:
            _stats["udp_recv"] += 1

        print(
            f"[UDP ✓] camera {addr} → robot={robot_id} "
            f"frame={frame_id} {len(data)}bytes"
        )

        # AI server 포워딩
        try:
            forward_sock.sendto(data, (AI_SERVER_IP, AI_UDP_PORT))
        except Exception as e:
            print(f"[UDP] Forward error: {e}")

        # 카메라 이미지 디코딩 → 공유 저장
        frame, image_key = decode_image_from_payload(payload, CAMERA_IMAGE_KEYS)
        if frame is not None:
            with camera_frame_lock:
                latest_camera_frame = frame
            if image_key != "preview_b64":
                print(f"[PREVIEW] Camera image source={image_key} size={frame.shape[1]}x{frame.shape[0]}")


# =========================
# 검증 디스플레이 스레드
# =========================
def display_thread():
    """카메라 수신 영상 + AI 결과를 별도 스레드에서 표시."""
    while True:
        # --- 카메라 수신 프레임 ---
        with camera_frame_lock:
            cam_frame = latest_camera_frame.copy() if latest_camera_frame is not None else None

        if cam_frame is not None:
            with _stats_lock:
                udp_n = _stats["udp_recv"]
                udp_e = _stats["udp_errors"]
            cam_frame = resize_for_display(cam_frame)
            cv2.putText(
                cam_frame,
                f"UDP recv={udp_n} err={udp_e}",
                (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2,
            )
            cv2.imshow("[RECV] Camera UDP", cam_frame)

        # --- AI 결과 프레임 ---
        with ai_result_lock:
            result = latest_ai_result

        if result:
            try:
                result_img, _ = decode_image_from_payload(result, AI_IMAGE_KEYS)
                if result_img is not None:
                    with _stats_lock:
                        tcp_n    = _stats["tcp_recv"]
                        tcp_e    = _stats["tcp_errors"]
                        latency  = _stats["last_latency_ms"]

                    dets = result.get("detections", [])
                    info_lines = [
                        f"TCP recv={tcp_n} err={tcp_e}",
                        f"robot={result.get('robot_id')} frame={result.get('frame_id')}",
                        f"detections={len(dets)} ai={result.get('process_ms')}ms latency={latency:.1f}ms",
                    ]
                    result_img = resize_for_display(result_img)

                    for i, line in enumerate(info_lines):
                        cv2.putText(
                            result_img, line,
                            (8, 22 + i * 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2,
                        )
                    cv2.imshow("[RECV] AI Result (TCP)", result_img)
            except Exception as e:
                print(f"[DISPLAY] AI result decode error: {e}")

        if cv2.waitKey(30) & 0xFF == ord("q"):
            break

        time.sleep(0.03)


# =========================
# 통계 출력 스레드
# =========================
def stats_thread():
    while True:
        time.sleep(5)
        with _stats_lock:
            s = _stats.copy()
        print(
            f"\n[STATS] UDP recv={s['udp_recv']} err={s['udp_errors']} | "
            f"TCP recv={s['tcp_recv']} err={s['tcp_errors']} | "
            f"last_latency={s['last_latency_ms']:.1f}ms\n"
        )


# =========================
# 메인
# =========================
def main():
    print("=" * 55)
    print("[MAIN SERVER] Starting...")
    print(f"  Camera UDP listen : 0.0.0.0:{CAMERA_UDP_PORT}")
    print(f"  AI server UDP fwd : {AI_SERVER_IP}:{AI_UDP_PORT}")
    print(f"  AI server TCP recv: {AI_SERVER_IP}:{AI_TCP_PORT}")
    print(f"  Raspberry UDP fwd : {ROS_TARGET_IP}:{ROS_TARGET_PORT}")
    print(f"  SHOW_PREVIEW      : {SHOW_PREVIEW}")
    print("=" * 55)

    threading.Thread(target=ai_tcp_receiver, daemon=True).start()
    threading.Thread(target=stats_thread,    daemon=True).start()

    if SHOW_PREVIEW:
        threading.Thread(target=display_thread, daemon=True).start()

    try:
        camera_udp_server()
    except KeyboardInterrupt:
        print("\n[MAIN SERVER] Stopped")
    finally:
        _ros_sock.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
