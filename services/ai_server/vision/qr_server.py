#!/usr/bin/env python3
"""
QR 코드 인식 서버 (AI 서버 실행)
=================================
stream_sender.py가 보낸 UDP JPEG 프레임을 수신해 QR을 인식하고
FastAPI HTTP로 결과를 제공한다.

qr_yo.py 대비 개선사항:
  - UDP 수신 방식 (카메라 직접 접근 불필요)
  - headless 모드 (cv2.imshow 제거)
  - 후보 우선순위 재정렬: 경량 후보 먼저 (gray → adaptive → otsu → clahe → sharp → bgr)
  - CLAHE 객체 재사용 (매 프레임 생성 제거)
  - ROI CLI 인자 및 /config 엔드포인트로 런타임 변경 가능
  - 웹훅: QR 인식 성공 시 moosinsa_service로 POST (--webhook-url 지정 시 활성)

실행:
  python3 qr_server.py --udp-port 5010 --port 8083
  python3 qr_server.py --udp-port 5010 --port 8083 --roi 0.10 0.50 0.90 1.00
  python3 qr_server.py --udp-port 5010 --port 8083 \
      --webhook-url http://moosinsa-service:8005/qr_product_info \
      --robot-id front_jet
"""

import argparse
import json
import socket
import threading
import time
from typing import Optional

import cv2
import numpy as np
import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── 전역 파라미터 ──────────────────────────────────────────────────────────────
ROI_RATIOS = [0.20, 0.55, 0.80, 1.00]   # [x1, y1, x2, y2] 비율
WEBHOOK_URL: str = ""                    # --webhook-url 로 설정; 비어있으면 비활성
ROBOT_ID: str = "unknown"               # --robot-id 로 설정
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="QR Detect Server")

latest_result = {
    "detected": False,
    "data":      None,
    "raw":       "",
    "mode":      None,
    "timestamp": 0.0,
}

# CLAHE 객체 재사용 (매 프레임 생성 방지)
_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
_detector = cv2.QRCodeDetector()
_detector.setEpsX(0.3)
_detector.setEpsY(0.3)

_latest_frame: Optional[tuple] = None   # (frame, result)
_frame_lock = threading.Lock()

# 중복 웹훅 전송 방지: 마지막으로 전송한 QR raw 값 보관
_last_sent_raw: str = ""


# ── API 모델 ──────────────────────────────────────────────────────────────────
class RoiConfig(BaseModel):
    roi: list[float]  # [x1_ratio, y1_ratio, x2_ratio, y2_ratio]


@app.get("/latest")
async def get_latest():
    return latest_result


@app.post("/config")
async def update_config(cfg: RoiConfig):
    global ROI_RATIOS
    if len(cfg.roi) != 4:
        return JSONResponse({"ok": False, "error": "roi는 4개 값 필요 [x1,y1,x2,y2]"}, 400)
    ROI_RATIOS = cfg.roi
    print(f"[CONFIG] ROI 업데이트: {ROI_RATIOS}")
    return {"ok": True, "roi": ROI_RATIOS}


# ── 전처리 후보 생성 (경량 순서) ──────────────────────────────────────────────
def _build_candidates(roi_bgr: np.ndarray) -> list:
    """
    경량 후보를 먼저 시도해 평균 처리 시간 단축.
    QR 데이터에 mode=adaptive가 명시되므로 adaptive를 두 번째 우선순위로 배치.
    """
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    gray_clahe = _clahe.apply(gray)

    adaptive = cv2.adaptiveThreshold(
        gray_clahe, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31, 5,
    )
    _, otsu = cv2.threshold(
        gray_clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    sharp = cv2.filter2D(roi_bgr, -1, kernel)

    return [
        ("gray",     gray),       # 가장 빠름
        ("adaptive", adaptive),   # 명시된 인식 모드
        ("otsu",     otsu),
        ("clahe",    gray_clahe),
        ("sharp",    sharp),
        ("bgr",      roi_bgr),    # 가장 느림
    ]


def _try_decode(frame: np.ndarray):
    """ROI 추출 → 전처리 후보 순차 시도 → 첫 성공 시 즉시 반환."""
    h, w = frame.shape[:2]
    x1 = int(w * ROI_RATIOS[0])
    y1 = int(h * ROI_RATIOS[1])
    x2 = int(w * ROI_RATIOS[2])
    y2 = int(h * ROI_RATIOS[3])
    roi = frame[y1:y2, x1:x2]

    for mode_name, img in _build_candidates(roi):
        data, points, _ = _detector.detectAndDecode(img)
        if data:
            return data, mode_name

    return "", None


def _draw_overlay(frame: np.ndarray, result: dict) -> np.ndarray:
    """프리뷰용 오버레이 — ROI 박스 + QR 인식 결과."""
    display = frame.copy()
    h, w = display.shape[:2]

    # ROI 박스 (파란색)
    x1 = int(w * ROI_RATIOS[0])
    y1 = int(h * ROI_RATIOS[1])
    x2 = int(w * ROI_RATIOS[2])
    y2 = int(h * ROI_RATIOS[3])
    cv2.rectangle(display, (x1, y1), (x2, y2), (255, 200, 0), 2)
    cv2.putText(display, "ROI", (x1, max(y1 - 8, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 0), 2)

    if result["detected"]:
        label = f"{result['raw']} | {result['mode']}"
        cv2.putText(display, label, (x1, y1 + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
        cv2.putText(display, "DETECTED", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
    else:
        cv2.putText(display, "SCANNING...", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)

    return display


# ── 웹훅 ──────────────────────────────────────────────────────────────────────
def _send_webhook(raw: str, mode: str) -> None:
    """QR 인식 결과를 moosinsa_service로 POST. 실패 시 로그만 남기고 종료."""
    payload = {
        "robot_id":    ROBOT_ID,
        "product_id":  raw,
        "raw_payload": raw,
    }
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=3)
        print(f"[WEBHOOK] POST {WEBHOOK_URL} → {resp.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"[WEBHOOK] 전송 실패 (무시): {e}", flush=True)


def _process_frame(frame: np.ndarray) -> None:
    global latest_result, _latest_frame, _last_sent_raw

    raw, mode = _try_decode(frame)

    if raw:
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw}

        latest_result = {
            "detected":  True,
            "data":      parsed,
            "raw":       raw,
            "mode":      mode,
            "timestamp": time.time(),
        }
        print(f"[QR] {raw} (mode={mode})")

        # 웹훅: 같은 QR이 연속으로 인식되면 중복 전송 방지
        if WEBHOOK_URL and raw != _last_sent_raw:
            _last_sent_raw = raw
            threading.Thread(
                target=_send_webhook, args=(raw, mode), daemon=True
            ).start()
    else:
        latest_result = {
            "detected":  False,
            "data":      None,
            "raw":       "",
            "mode":      None,
            "timestamp": time.time(),
        }
        # QR이 사라지면 리셋 → 같은 QR이 다시 나타날 때 재전송
        _last_sent_raw = ""

    with _frame_lock:
        _latest_frame = (frame, latest_result)


# ── UDP 수신 루프 ─────────────────────────────────────────────────────────────
def _udp_receiver_loop(udp_port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", udp_port))
    sock.settimeout(1.0)
    print(f"[UDP] 수신 대기 중: 0.0.0.0:{udp_port}")

    while True:
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except Exception as e:
            print(f"[UDP] 수신 오류: {e}")
            continue

        nparr = np.frombuffer(data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            continue

        _process_frame(frame)


def main() -> None:
    parser = argparse.ArgumentParser(description="QR 코드 인식 서버")
    parser.add_argument("--udp-port",    type=int, default=5010,
                        help="stream_sender UDP 수신 포트 (기본: 5010)")
    parser.add_argument("--host",        default="0.0.0.0")
    parser.add_argument("--port",        type=int, default=8083,
                        help="HTTP 서버 포트 (기본: 8083)")
    parser.add_argument("--roi",         type=float, nargs=4,
                        default=[0.20, 0.55, 0.80, 1.00],
                        metavar=("X1", "Y1", "X2", "Y2"),
                        help="ROI 비율 (기본: 0.20 0.55 0.80 1.00)")
    parser.add_argument("--webhook-url", default="",
                        help="QR 인식 결과를 전송할 웹훅 URL "
                             "(예: http://moosinsa-service:8005/qr_product_info). "
                             "미지정 시 웹훅 비활성.")
    parser.add_argument("--robot-id",    default="unknown",
                        help="웹훅 payload의 robot_id (기본: unknown)")
    parser.add_argument("--preview",     action="store_true",
                        help="cv2.imshow로 인식 결과 실시간 표시")
    args = parser.parse_args()

    global ROI_RATIOS, WEBHOOK_URL, ROBOT_ID
    ROI_RATIOS  = args.roi
    WEBHOOK_URL = args.webhook_url
    ROBOT_ID    = args.robot_id

    print(f"[INFO] QR 인식 서버 시작")
    print(f"  UDP 수신:  0.0.0.0:{args.udp_port}")
    print(f"  HTTP 서버: http://{args.host}:{args.port}")
    print(f"  ROI:       {ROI_RATIOS}")
    if WEBHOOK_URL:
        print(f"  웹훅:      {WEBHOOK_URL}  (robot_id={ROBOT_ID})")
    else:
        print(f"  웹훅:      비활성 (--webhook-url 미지정)")

    udp_thread = threading.Thread(
        target=_udp_receiver_loop, args=(args.udp_port,), daemon=True
    )
    udp_thread.start()

    if args.preview:
        # uvicorn을 백그라운드, imshow를 메인 스레드에서 실행
        server_thread = threading.Thread(
            target=uvicorn.run,
            kwargs={"app": app, "host": args.host, "port": args.port},
            daemon=True,
        )
        server_thread.start()

        cv2.namedWindow("QR Server Preview", cv2.WINDOW_NORMAL)
        print("[PREVIEW] 'q' 키로 종료")
        while True:
            with _frame_lock:
                data = _latest_frame

            if data is not None:
                frame, result = data
                display = _draw_overlay(frame, result)
                cv2.imshow("QR Server Preview", display)

            if cv2.waitKey(30) & 0xFF == ord("q"):
                break
        cv2.destroyAllWindows()
    else:
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()