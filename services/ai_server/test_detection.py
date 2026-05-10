"""
test_detection.py
─────────────────
UDP로 수신한 프레임에 YOLO + 기하학적 분류 결과를 실시간으로 표시.
moosinsa_ai_server.py 실행 전 인식 동작 확인용.

실행:
  python3 test_detection.py

키:
  Q : 종료
  S : 현재 프레임 저장 (snapshot.jpg)
"""

import socket
import json
import base64
import time
import cv2
import numpy as np
from ultralytics import YOLO

# =========================
# 설정
# =========================
UDP_LISTEN_PORT = 7018
MODEL_PATH      = "best.pt"
CONF_THRESHOLD  = 0.40
UDP_BUFFER_SIZE = 65535

SOLIDITY_THRESH = 0.85
REACH_THRESH    = 1.80

POSE_COLORS = {
    "hands_up":   (80,  255, 120),
    "hands_down": (200, 180, 255),
}


# =========================
# 기하학적 포즈 분류 (moosinsa_ai_server.py 와 동일)
# =========================
def classify_pose_geometric(frame, x1, y1, x2, y2):
    pad = 6
    h_img, w_img = frame.shape[:2]
    roi = frame[max(0, y1-pad):min(h_img, y2+pad),
                max(0, x1-pad):min(w_img, x2+pad)]
    if roi.size == 0:
        return None, 0.0

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    border = np.concatenate([mask[0,:], mask[-1,:], mask[:,0], mask[:,-1]])
    if np.mean(border) > 128:
        mask = cv2.bitwise_not(mask)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k, iterations=1)

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None, 0.0
    cnt = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(cnt) < 150:
        return None, 0.0

    hull_area = cv2.contourArea(cv2.convexHull(cnt))
    solidity  = cv2.contourArea(cnt) / (hull_area + 1e-6)

    fig_mask = np.zeros_like(mask)
    cv2.drawContours(fig_mask, [cnt], -1, 255, -1)
    pts = np.argwhere(fig_mask > 0).astype(np.float32)
    if len(pts) < 30:
        return None, 0.0
    dists       = np.linalg.norm(pts - pts.mean(axis=0), axis=1)
    reach_ratio = np.percentile(dists, 95) / (dists.mean() + 1e-6)

    sol_signal   = (SOLIDITY_THRESH - solidity)     / SOLIDITY_THRESH
    reach_signal = (reach_ratio - REACH_THRESH)     / (REACH_THRESH - 1.0)
    score        = min(sol_signal, reach_signal)

    if score > 0.0:
        return "hands_up",   score
    elif score < 0.0:
        return "hands_down", -score
    return None, score


# =========================
# 메인
# =========================
def main():
    print(f"[INFO] Loading model: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)
    print(f"[INFO] Classes: {model.names}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_LISTEN_PORT))
    sock.settimeout(1.0)
    print(f"[UDP] Listening on 0.0.0.0:{UDP_LISTEN_PORT}")
    print("[KEY] Q=종료  S=스냅샷 저장")

    cv2.namedWindow("Detection Test", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Detection Test", 960, 540)

    fps_t    = time.time()
    fps_cnt  = 0
    fps_val  = 0.0

    while True:
        # ── UDP 수신 ──────────────────────────────────
        try:
            data, addr = sock.recvfrom(UDP_BUFFER_SIZE)
        except socket.timeout:
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            continue

        try:
            payload = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            continue

        preview_b64 = payload.get("preview_b64")
        if not preview_b64:
            continue

        try:
            img = cv2.imdecode(
                np.frombuffer(base64.b64decode(preview_b64), dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
        except Exception:
            continue
        if img is None:
            continue

        h, w    = img.shape[:2]
        display = img.copy()

        # ── YOLO 추론 ─────────────────────────────────
        t0      = time.time()
        results = model(img, conf=CONF_THRESHOLD, verbose=False)
        result  = results[0]
        inf_ms  = (time.time() - t0) * 1000.0

        hands_up_count = 0

        if result.boxes is not None:
            for i, box in enumerate(result.boxes.xyxy):
                x1, y1, x2, y2 = map(int, box)
                model_pose = model.names[int(result.boxes.cls[i])]
                conf       = float(result.boxes.conf[i])

                geo_pose, geo_score = classify_pose_geometric(img, x1, y1, x2, y2)
                final_pose = geo_pose if geo_pose is not None else model_pose
                color      = POSE_COLORS.get(final_pose, (200, 200, 200))

                # 마스크 오버레이
                if result.masks is not None and i < len(result.masks.data):
                    mask_np = result.masks.data[i].cpu().numpy().astype(np.uint8)
                    mask_np = cv2.resize(mask_np, (w, h))
                    overlay = display.copy()
                    overlay[mask_np > 0] = color
                    cv2.addWeighted(overlay, 0.40, display, 0.60, 0, display)

                # 바운딩박스
                cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)

                # 라벨: 모델 결과 / 기하학 결과 / confidence
                geo_str = f"geo:{geo_pose}({geo_score:.2f})" if geo_pose else "geo:–"
                label   = f"{final_pose} {conf:.2f}  [{geo_str}]"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
                cv2.rectangle(display, (x1, y1 - th - 8), (x1 + tw + 4, y1), color, -1)
                cv2.putText(display, label, (x1 + 2, y1 - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)

                # 중심점
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                cv2.circle(display, (cx, cy), 5, color, -1)

                if final_pose == "hands_up":
                    hands_up_count += 1

        # ── FPS 계산 ──────────────────────────────────
        fps_cnt += 1
        if time.time() - fps_t >= 1.0:
            fps_val = fps_cnt / (time.time() - fps_t)
            fps_cnt = 0
            fps_t   = time.time()

        # ── HUD ───────────────────────────────────────
        robot_id = payload.get("robot_id", "?")
        frame_id = payload.get("frame_id", "?")

        cv2.putText(display, f"FPS:{fps_val:.1f}  inf:{inf_ms:.0f}ms",
                    (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(display, f"robot={robot_id}  frame={frame_id}",
                    (8, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        if hands_up_count > 0:
            cv2.putText(display, f"HANDS UP x{hands_up_count}",
                        (8, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (80, 255, 120), 3)

        cv2.imshow("Detection Test", display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            cv2.imwrite("snapshot.jpg", display)
            print("[SAVE] snapshot.jpg")

    sock.close()
    cv2.destroyAllWindows()
    print("[INFO] Done")


if __name__ == "__main__":
    main()
