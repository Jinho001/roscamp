#!/usr/bin/env python3
"""
HSV 필터 디버그 뷰어
====================
레이아웃 — 좌우 2분할:
  ┌──────────────────────┬──────────────────────┐
  │  왼쪽: 모드별 표시    │  오른쪽: 필터 단계    │
  └──────────────────────┴──────────────────────┘

키 조작:
  q / ESC  : 종료
  Tab      : 오른쪽 패널 전환 (HSV마스크 → 형태학마스크 → 필터단계)
  s        : 현재 파라미터 터미널 출력

실행:
    python3 services/ai_server/vision/hsv_debug_viewer.py
    python3 services/ai_server/vision/hsv_debug_viewer.py --min-solidity 0.40
"""

import argparse
import math
import socket
import threading
import time
from typing import Optional

import cv2
import numpy as np

# ── 파라미터 (cv_detect_server.py와 동기화) ───────────────────────────────────
HSV_LOWER        = np.array([0,   0,  208], dtype=np.uint8)
HSV_UPPER        = np.array([158, 30, 255], dtype=np.uint8)
MIN_AREA         = 1000
MAX_AREA         = 80000
MIN_W, MAX_W     = 0, 400
MIN_H, MAX_H     = 0, 400
MORPH_K          = 7
MIN_SOLIDITY     = 0.40
MIN_ASPECT_RATIO = 0.35
# ─────────────────────────────────────────────────────────────────────────────

_latest_frame: Optional[np.ndarray] = None
_frame_lock = threading.Lock()

RIGHT_MODES = ["HSV 마스크 (raw)", "형태학 연산 후", "필터 단계별"]
right_mode_idx = 0


def _udp_receiver(udp_port: int) -> None:
    global _latest_frame
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", udp_port))
    sock.settimeout(1.0)
    print(f"[UDP] 수신 대기 중: 0.0.0.0:{udp_port}")
    while True:
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        nparr = np.frombuffer(data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None:
            with _frame_lock:
                _latest_frame = img


def _put_text_bg(img, text, pos, scale=0.7, color=(255, 255, 255), thickness=2):
    """배경 사각형을 깔아 텍스트 가독성 향상."""
    x, y = pos
    (tw, th), bl = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    cv2.rectangle(img, (x - 2, y - th - 4), (x + tw + 2, y + bl), (0, 0, 0), -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness)


def _run_pipeline(img: np.ndarray) -> dict:
    original = img.copy()

    # CLAHE
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    img_clahe = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    # HSV 마스크
    blurred  = cv2.GaussianBlur(img_clahe, (5, 5), 0)
    hsv      = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    hsv_mask = cv2.inRange(hsv, HSV_LOWER, HSV_UPPER)

    # 형태학 연산
    kernel     = cv2.getStructuringElement(cv2.MORPH_RECT, (MORPH_K, MORPH_K))
    morph_mask = cv2.morphologyEx(hsv_mask, cv2.MORPH_CLOSE, kernel)
    morph_mask = cv2.morphologyEx(morph_mask, cv2.MORPH_OPEN,  kernel)

    contours, _ = cv2.findContours(morph_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    result_img  = original.copy()
    filter_img  = original.copy()
    detections  = []

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < MIN_AREA or area > MAX_AREA:
            cv2.drawContours(filter_img, [cnt], -1, (80, 80, 80), 1)
            continue

        rect   = cv2.minAreaRect(cnt)
        cx, cy = float(rect[0][0]), float(rect[0][1])
        w,  h  = float(rect[1][0]), float(rect[1][1])
        angle  = rect[2]

        # size_ok = (MIN_W <= w <= MAX_W and MIN_H <= h <= MAX_H) or \
        #           (MIN_W <= h <= MAX_W and MIN_H <= w <= MAX_H)
        # if not size_ok:
        #     box = cv2.boxPoints(((cx, cy), (w, h), angle)).astype(np.int32)
        #     cv2.polylines(filter_img, [box], True, (0, 200, 200), 2)
        #     _put_text_bg(filter_img, f"size  w{w:.0f}xh{h:.0f}",
        #                  (int(cx) + 6, int(cy) - 10), 0.6, (0, 200, 200))
        #     continue

        if w < h:
            w, h = h, w
            angle += 90.0
        while angle >= 90.0:  angle -= 180.0
        while angle < -90.0:  angle += 180.0

        hull      = cv2.convexHull(cnt)
        hull_area = cv2.contourArea(hull)
        solidity  = min(area / hull_area, 1.0) if hull_area > 0 else 0.0

        # if solidity < MIN_SOLIDITY:
        #     box = cv2.boxPoints(((cx, cy), (w, h), angle)).astype(np.int32)
        #     cv2.polylines(filter_img, [box], True, (0, 0, 255), 2)
        #     _put_text_bg(filter_img, f"S={solidity:.2f} (< {MIN_SOLIDITY})",
        #                  (int(cx) + 6, int(cy) - 10), 0.65, (0, 80, 255))
        #     continue

        aspect = h / w if w > 0 else 0.0
        # if aspect < MIN_ASPECT_RATIO:
        #     box = cv2.boxPoints(((cx, cy), (w, h), angle)).astype(np.int32)
        #     cv2.polylines(filter_img, [box], True, (255, 80, 0), 2)
        #     _put_text_bg(filter_img, f"A={aspect:.2f} (< {MIN_ASPECT_RATIO})",
        #                  (int(cx) + 6, int(cy) - 10), 0.65, (255, 120, 0))
        #     continue

        # 최종 통과
        box = cv2.boxPoints(((cx, cy), (w, h), angle)).astype(np.int32)
        cv2.polylines(filter_img, [box], True, (0, 255, 0), 3)
        cv2.polylines(result_img,  [box], True, (0, 255, 0), 3)
        cv2.circle(result_img, (int(cx), int(cy)), 7, (0, 0, 255), -1)

        _put_text_bg(filter_img,
                     f"cx={cx:.0f} cy={cy:.0f}  S={solidity:.2f}  A={aspect:.2f}  w={w:.0f} h={h:.0f}",
                     (int(cx) + 6, int(cy) - 10), 0.65, (0, 255, 0))
        _put_text_bg(result_img,
                     f"cx={cx:.0f} cy={cy:.0f}  w={w:.0f} h={h:.0f}",
                     (int(cx) + 6, int(cy) - 10), 0.65, (0, 255, 180))

        detections.append({"cx": cx, "cy": cy, "w": w, "h": h,
                            "solidity": round(solidity, 3), "aspect": round(aspect, 3)})

    return {
        "original":   original,
        "clahe":      img_clahe,
        "clahe_l":    lab[:, :, 0],
        "hsv_mask":   hsv_mask,
        "morph_mask": morph_mask,
        "result_img": result_img,
        "filter_img": filter_img,
        "detections": detections,
    }


def _add_title(img: np.ndarray, title: str) -> np.ndarray:
    bar = np.zeros((32, img.shape[1], 3), dtype=np.uint8)
    cv2.putText(bar, title, (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (220, 220, 100), 2)
    return np.vstack([bar, img])


def _mask_bgr(mask: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)


def _build_display(panels: dict, win_w: int, win_h: int,
                   right_mode: int, fps: float) -> np.ndarray:
    ph = win_h - 40   # 하단 상태바 40px
    pw = win_w // 2

    left = cv2.resize(panels["result_img"], (pw, ph))
    left = _add_title(left, "원본 + 검출 결과")

    if right_mode == 0:
        right_img = _mask_bgr(panels["hsv_mask"])
        title = "HSV 마스크 (흰=HSV 통과, 형태학 전)"
    elif right_mode == 1:
        right_img = _mask_bgr(panels["morph_mask"])
        title = "형태학 연산 후 마스크 (contour 후보)"
    else:
        right_img = panels["filter_img"]
        title = "필터 단계별  녹=통과  빨=Solidity탈락  파=Aspect탈락  노=크기탈락"

    right = cv2.resize(right_img, (pw, ph))
    right = _add_title(right, title)

    # 왼쪽 패널 높이를 오른쪽과 맞춤
    lh, rh = left.shape[0], right.shape[0]
    if lh != rh:
        target = max(lh, rh)
        if lh < target:
            left  = np.vstack([left,  np.zeros((target - lh, pw, 3), dtype=np.uint8)])
        else:
            right = np.vstack([right, np.zeros((target - rh, pw, 3), dtype=np.uint8)])

    grid = np.hstack([left, right])

    # 하단 상태바
    n = len(panels["detections"])
    status = (f"  FPS {fps:.1f}   detected={n}   "
              f"HSV {HSV_LOWER.tolist()}~{HSV_UPPER.tolist()}   "
              f"solidity>={MIN_SOLIDITY}   aspect>={MIN_ASPECT_RATIO}   "
              f"morph_k={MORPH_K}   "
              f"[Tab] 오른쪽 전환   [s] 파라미터 출력   [q] 종료")
    bar = np.zeros((40, win_w, 3), dtype=np.uint8)
    cv2.putText(bar, status, (4, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

    total_h = grid.shape[0] + bar.shape[0]
    if total_h != win_h:
        grid = cv2.resize(grid, (win_w, win_h - 40))

    return np.vstack([grid, bar])


def main() -> None:
    global HSV_LOWER, HSV_UPPER, MIN_AREA, MAX_AREA
    global MIN_W, MAX_W, MIN_H, MAX_H, MORPH_K, MIN_SOLIDITY, MIN_ASPECT_RATIO
    global right_mode_idx, _latest_frame

    parser = argparse.ArgumentParser(description="HSV 필터 디버그 뷰어")
    parser.add_argument("--image",            type=str,   default=None,
                        help="정적 이미지 파일 경로 (지정 시 UDP 대신 이 이미지를 사용)")
    parser.add_argument("--udp-port",         type=int,   default=5000,
                        help="UDP 수신 포트 (FrontJet=5000, WareJet=5001)")
    parser.add_argument("--hsv-lower",        type=int,   nargs=3, default=None)
    parser.add_argument("--hsv-upper",        type=int,   nargs=3, default=None)
    parser.add_argument("--min-solidity",     type=float, default=None)
    parser.add_argument("--min-aspect-ratio", type=float, default=None)
    parser.add_argument("--morph-k",          type=int,   default=None)
    parser.add_argument("--width",            type=int,   default=1600)
    parser.add_argument("--height",           type=int,   default=720)
    args = parser.parse_args()

    if args.hsv_lower:        HSV_LOWER        = np.array(args.hsv_lower, dtype=np.uint8)
    if args.hsv_upper:        HSV_UPPER        = np.array(args.hsv_upper, dtype=np.uint8)
    if args.min_solidity:     MIN_SOLIDITY     = args.min_solidity
    if args.min_aspect_ratio: MIN_ASPECT_RATIO = args.min_aspect_ratio
    if args.morph_k:          MORPH_K          = args.morph_k

    if args.image:
        # 정적 이미지 모드: UDP 스트림 없이 파일에서 로드
        static_img = cv2.imread(args.image)
        if static_img is None:
            print(f"[ERROR] 이미지를 불러올 수 없습니다: {args.image}")
            return
        # 스트림(640x480)과 동일한 해상도로 리사이즈 → 픽셀 기반 필터 수치 일치
        static_img = cv2.resize(static_img, (640, 480))
        with _frame_lock:
            _latest_frame = static_img
        print(f"[IMAGE] 정적 이미지 로드 완료 (640x480 리사이즈): {args.image}")
    else:
        threading.Thread(target=_udp_receiver, args=(args.udp_port,), daemon=True).start()

    cv2.namedWindow("HSV Debug Viewer", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("HSV Debug Viewer", args.width, args.height)

    # ── 실시간 파라미터 튜닝용 트랙바(슬라이더) 창 추가 ──
    cv2.namedWindow("Trackbars", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Trackbars", 400, 300)
    
    def nothing(x): pass
    cv2.createTrackbar("H_MIN", "Trackbars", int(HSV_LOWER[0]), 179, nothing)
    cv2.createTrackbar("S_MIN", "Trackbars", int(HSV_LOWER[1]), 255, nothing)
    cv2.createTrackbar("V_MIN", "Trackbars", int(HSV_LOWER[2]), 255, nothing)
    cv2.createTrackbar("H_MAX", "Trackbars", int(HSV_UPPER[0]), 179, nothing)
    cv2.createTrackbar("S_MAX", "Trackbars", int(HSV_UPPER[1]), 255, nothing)
    cv2.createTrackbar("V_MAX", "Trackbars", int(HSV_UPPER[2]), 255, nothing)
    cv2.createTrackbar("MORPH_K", "Trackbars", int(MORPH_K), 51, nothing)

    print("=" * 60)
    print("HSV 디버그 뷰어  키: [Tab] 오른쪽 전환  [s] 캡처 및 파라미터 저장  [q] 종료")
    print(" (Trackbars 창의 슬라이더를 움직여 실시간으로 파라미터를 조정하세요!)")
    print("=" * 60)

    fps, frame_count, prev_time = 0.0, 0, time.time()

    while True:
        # 트랙바 값 실시간 반영
        HSV_LOWER[0] = cv2.getTrackbarPos("H_MIN", "Trackbars")
        HSV_LOWER[1] = cv2.getTrackbarPos("S_MIN", "Trackbars")
        HSV_LOWER[2] = cv2.getTrackbarPos("V_MIN", "Trackbars")
        HSV_UPPER[0] = cv2.getTrackbarPos("H_MAX", "Trackbars")
        HSV_UPPER[1] = cv2.getTrackbarPos("S_MAX", "Trackbars")
        HSV_UPPER[2] = cv2.getTrackbarPos("V_MAX", "Trackbars")
        
        # 커널 사이즈는 홀수여야 함
        mk = cv2.getTrackbarPos("MORPH_K", "Trackbars")
        MORPH_K = mk if mk % 2 != 0 else mk + 1
        
        with _frame_lock:
            img = _latest_frame.copy() if _latest_frame is not None else None

        key = cv2.waitKey(30) & 0xFF
        if key in (ord('q'), 27):
            break
        elif key == 9:   # Tab
            right_mode_idx = (right_mode_idx + 1) % 3
            print(f"[오른쪽] {RIGHT_MODES[right_mode_idx]}")
        elif key == ord('s'):
            print(f"\n[파라미터]")
            print(f"  HSV: {HSV_LOWER.tolist()} ~ {HSV_UPPER.tolist()}")
            print(f"  면적: {MIN_AREA}~{MAX_AREA}  크기: {MIN_W}~{MAX_W} x {MIN_H}~{MAX_H}")
            print(f"  morph_k={MORPH_K}  solidity>={MIN_SOLIDITY}  aspect>={MIN_ASPECT_RATIO}")

            if img is not None:
                import os
                from datetime import datetime
                ts = datetime.now().strftime("%H%M%S")
                out_dir = "presentation_images"
                os.makedirs(out_dir, exist_ok=True)

                p = _run_pipeline(img)
                cv2.imwrite(f"{out_dir}/01_raw_{ts}.jpg", p["original"])
                cv2.imwrite(f"{out_dir}/02_clahe_{ts}.jpg", p["clahe"])
                cv2.imwrite(f"{out_dir}/02_clahe_L_channel_{ts}.jpg", p["clahe_l"])
                cv2.imwrite(f"{out_dir}/03_mask_before_morph_{ts}.jpg", p["hsv_mask"])
                cv2.imwrite(f"{out_dir}/04_mask_after_morph_{ts}.jpg", p["morph_mask"])
                cv2.imwrite(f"{out_dir}/05_final_obb_{ts}.jpg", p["result_img"])

                curr_display = _build_display(p, args.width, args.height, right_mode_idx, fps)
                cv2.imwrite(f"{out_dir}/00_debugger_view_{ts}.jpg", curr_display)

                print(f"[SUCCESS] 단계별 이미지 7장 저장 완료: '{out_dir}/*_{ts}.jpg'\n")

        if img is None:
            blank = np.zeros((args.height, args.width, 3), dtype=np.uint8)
            cv2.putText(blank, "UDP 수신 대기 중... (stream_sender 실행 확인)",
                        (60, args.height // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (120, 120, 120), 2)
            cv2.imshow("HSV Debug Viewer", blank)
            continue

        frame_count += 1
        now = time.time()
        if now - prev_time >= 1.0:
            fps = frame_count / (now - prev_time)
            frame_count, prev_time = 0, now

        panels  = _run_pipeline(img)
        display = _build_display(panels, args.width, args.height, right_mode_idx, fps)
        cv2.imshow("HSV Debug Viewer", display)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
