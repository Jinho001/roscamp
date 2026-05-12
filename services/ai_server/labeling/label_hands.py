"""
SAM3 hands_up / hands_down 자동 라벨링 스크립트
────────────────────────────────────────────────
사용법:
  python label_hands.py
  python label_hands.py --port 7018

조작키:
  U      : hands_up 모드 (class 0)
  D      : hands_down 모드 (class 1)
  1      : 저장 대상 → train
  2      : 저장 대상 → val
  3      : 저장 대상 → test
  A      : 자동 저장 ON/OFF
  S      : 현재 프레임 수동 저장
  Q      : 종료
────────────────────────────────────────────────
클래스:
  0 = hands_up
  1 = hands_down
────────────────────────────────────────────────
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import argparse
import json
import socket
import struct
import queue
import threading
import time
import cv2
import numpy as np
from pathlib import Path
from ultralytics.models.sam import SAM3SemanticPredictor

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=7018, help="UDP 수신 포트")
args = parser.parse_args()

# ── 설정 ──────────────────────────────────────────────────────────────
UDP_PORT       = args.port
MODEL_PATH     = "/home/team1-ai/detection/sam3.pt"
DATASET_DIR    = Path("/home/team1-ai/detection/sm3_model/dataset_hands")
TARGET_COUNT   = 600
CONF_THRESH    = 0.40
AUTO_INTERVAL  = 30
INFER_INTERVAL = 1   # N프레임마다 1번 추론

# ── UDP 청킹+헤더 포맷 (hands_seat_ai.py 동일) ────────────────────────
MAGIC          = 0xA55A
PKT_DATA       = 0x00
PKT_HEARTBEAT  = 0x01
HDR_FMT        = '!HIHHBB'
HDR_SIZE       = struct.calcsize(HDR_FMT)   # 12 bytes
FRAME_TIMEOUT  = 2.0
UDP_RECV_BUF   = 8 * 1024 * 1024

# 클래스별 텍스트 프롬프트 및 ID
CLASSES = {
    "hands_up": {
        "id": 0,
        "text": ["red figure", "red figure with arms up", "red figure hands above head"],
        "color": (100, 255, 100),
    },
    "hands_down": {
        "id": 1,
        "text": ["red figure", "red figure arms at side", "red figure hands lowered"],
        "color": (100, 100, 255),
    },
}

SPLITS = ["train", "val", "test"]
SPLIT_COLORS = {
    "train": (255, 200, 50),
    "val":   (50, 200, 255),
    "test":  (200, 50, 255),
}
# ─────────────────────────────────────────────────────────────────────

DIRS = {
    split: {
        "img": DATASET_DIR / f"images/{split}",
        "lbl": DATASET_DIR / f"labels/{split}",
    }
    for split in SPLITS
}
for paths in DIRS.values():
    paths["img"].mkdir(parents=True, exist_ok=True)
    paths["lbl"].mkdir(parents=True, exist_ok=True)


def count_saved():
    total = {}
    for split in SPLITS:
        total[split] = len(list(DIRS[split]["img"].glob("*.jpg")))
    return total


def masks_to_yolo_seg(masks_xy, class_id, img_w, img_h):
    lines = []
    for pts in masks_xy:
        coords = []
        for x, y in pts:
            nx = min(max(x / img_w, 0), 1)
            ny = min(max(y / img_h, 0), 1)
            coords.append(f"{nx:.6f} {ny:.6f}")
        if len(coords) >= 3:
            lines.append(f"{class_id} " + " ".join(coords))
    return lines


def save_sample(frame, yolo_lines, split, counts):
    idx  = sum(counts.values())
    name = f"{idx:05d}"
    cv2.imwrite(str(DIRS[split]["img"] / f"{name}.jpg"), frame)
    with open(DIRS[split]["lbl"] / f"{name}.txt", "w") as f:
        f.write("\n".join(yolo_lines))


# ── 초기화 ────────────────────────────────────────────────────────────
overrides = dict(conf=CONF_THRESH, task="segment", mode="predict",
                 model=MODEL_PATH, half=False, save=False, imgsz=490)
predictor = SAM3SemanticPredictor(overrides=overrides)

# ── UDP 수신 스레드 (청킹+헤더 재조립, hands_seat_ai.py 동일 프로토콜) ──
_frame_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=2)

def _udp_recv_loop(port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RECV_BUF)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(1.0)
    print(f"[UDP] 수신 대기 :{port}  (청킹+헤더 프로토콜)")

    frame_buffers = {}   # (addr, frame_id) -> {"chunks": {}, "total": int, "t": float}
    fb_lock = threading.Lock()

    def cleanup(now):
        expired = [k for k, v in frame_buffers.items() if now - v["t"] > FRAME_TIMEOUT]
        for k in expired:
            del frame_buffers[k]

    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break

        if len(data) < HDR_SIZE:
            continue

        magic, frame_id, chunk_idx, total, pkt_type, _ = struct.unpack(HDR_FMT, data[:HDR_SIZE])

        if magic != MAGIC or total == 0 or chunk_idx >= total:
            continue
        if pkt_type == PKT_HEARTBEAT:
            continue

        chunk_data = data[HDR_SIZE:]
        buffer_key = (addr, frame_id)

        with fb_lock:
            now = time.time()
            cleanup(now)

            # 같은 송신자의 오래된 frame_id 버퍼 폐기
            old_keys = [k for k in frame_buffers if k[0] == addr and k[1] < frame_id]
            for k in old_keys:
                del frame_buffers[k]

            if buffer_key not in frame_buffers:
                frame_buffers[buffer_key] = {"chunks": {}, "total": total, "t": now}
            frame_buffers[buffer_key]["chunks"][chunk_idx] = chunk_data

            if len(frame_buffers[buffer_key]["chunks"]) < total:
                continue

            full = b"".join(frame_buffers[buffer_key]["chunks"][i] for i in range(total))
            del frame_buffers[buffer_key]

        try:
            meta_len  = struct.unpack('!H', full[:2])[0]
            img_bytes = full[2 + meta_len:]
            frame = cv2.imdecode(np.frombuffer(img_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
        except Exception as e:
            print(f"[DECODE] 실패: {e}")
            continue

        if _frame_queue.full():
            try:
                _frame_queue.get_nowait()
            except queue.Empty:
                pass
        _frame_queue.put(frame)

threading.Thread(target=_udp_recv_loop, args=(UDP_PORT,), daemon=True).start()

current_mode  = "hands_up"
current_split = "train"
auto_mode     = False
frame_count   = 0
last_masks_xy = []
last_frame    = None

counts = count_saved()
print(f"[INFO] 수집량 → train:{counts['train']}  val:{counts['val']}  test:{counts['test']}  |  목표: {TARGET_COUNT}장")
print("[INFO] U:hands_up  D:hands_down  1:train  2:val  3:test  A:자동  S:저장  Q:종료")

while True:
    try:
        frame = _frame_queue.get(timeout=2.0)
    except queue.Empty:
        print("[WARN] UDP 프레임 수신 없음 (2초 초과)")
        continue
    
    cv2.imshow("RAW FRAME", frame)
    cv2.waitKey(1)

    frame_count += 1
    counts      = count_saved()
    total_saved = sum(counts.values())
    h, w        = frame.shape[:2]
    preview     = frame.copy()

    cls         = CLASSES[current_mode]
    class_id    = cls["id"]
    cls_color   = cls["color"]
    split_color = SPLIT_COLORS[current_split]

    # INFER_INTERVAL마다만 추론, 나머지는 이전 결과 재사용
    if frame_count % INFER_INTERVAL == 0:
        predictor.set_image(frame)
        results = predictor(text=cls["text"])
        last_masks_xy = []
        for result in results:
            if result.masks is None:
                continue
            for mask_xy in result.masks.xy:
                last_masks_xy.append(mask_xy.tolist())
        last_frame = frame.copy()

    masks_xy = last_masks_xy
    for mask_xy in masks_xy:
        pts = np.array(mask_xy, dtype=np.int32)
        cv2.polylines(preview, [pts.reshape(-1, 1, 2)], True, cls_color, 2)
        if len(pts) > 0:
            cv2.putText(preview, current_mode, tuple(pts[0]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, cls_color, 2)

    do_save = auto_mode and (frame_count % AUTO_INTERVAL == 0) and masks_xy
    if do_save:
        yolo_lines = masks_to_yolo_seg(masks_xy, class_id, w, h)
        if yolo_lines:
            save_sample(frame, yolo_lines, current_split, counts)
            counts = count_saved()
            print(f"[AUTO][{current_mode}→{current_split}] {sum(counts.values())}/{TARGET_COUNT}  train:{counts['train']} val:{counts['val']} test:{counts['test']}")

    # HUD ── 진행바
    pct   = int(total_saved / TARGET_COUNT * 100)
    bar_w = int(w * 0.6)
    cv2.rectangle(preview, (10, h-30), (10+bar_w, h-10), (60, 60, 60), -1)
    cv2.rectangle(preview, (10, h-30), (10+int(bar_w*pct/100), h-10), split_color, -1)
    cv2.putText(preview, f"total {total_saved}/{TARGET_COUNT} ({pct}%)",
                (15, h-35), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # HUD ── 분할별 카운트
    cv2.putText(preview, f"train:{counts['train']}  val:{counts['val']}  test:{counts['test']}",
                (15, h-55), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

    # HUD ── 현재 모드 / 저장 대상
    cv2.putText(preview, f"class: {current_mode.upper()}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, cls_color, 2)
    cv2.putText(preview, f"split: [{current_split.upper()}]", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, split_color, 2)
    auto_txt = "AUTO:ON" if auto_mode else "AUTO:OFF"
    cv2.putText(preview, auto_txt, (10, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 255, 255) if auto_mode else (100, 100, 100), 1)

    cv2.imshow("SAM3 Hands Labeling", preview)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('u'):
        current_mode = "hands_up"
        print(f"[INFO] 클래스 → hands_up (class 0)")
    elif key == ord('d'):
        current_mode = "hands_down"
        print(f"[INFO] 클래스 → hands_down (class 1)")
    elif key == ord('1'):
        current_split = "train"
        print(f"[INFO] 저장 대상 → train")
    elif key == ord('2'):
        current_split = "val"
        print(f"[INFO] 저장 대상 → val")
    elif key == ord('3'):
        current_split = "test"
        print(f"[INFO] 저장 대상 → test")
    elif key == ord('a'):
        auto_mode = not auto_mode
        print(f"[INFO] 자동모드: {'ON' if auto_mode else 'OFF'}")
    elif key == ord('s'):
        if masks_xy and last_frame is not None:
            yolo_lines = masks_to_yolo_seg(masks_xy, class_id, w, h)
            if yolo_lines:
                save_sample(last_frame, yolo_lines, current_split, counts)
                counts = count_saved()
                print(f"[SAVE][{current_mode}→{current_split}] {sum(counts.values())}/{TARGET_COUNT}  train:{counts['train']} val:{counts['val']} test:{counts['test']}")
        else:
            print("[WARN] 감지된 객체 없음")

    if total_saved >= TARGET_COUNT:
        print(f"\n[DONE] {TARGET_COUNT}장 수집 완료!")
        counts = count_saved()
        print(f"  train:{counts['train']}  val:{counts['val']}  test:{counts['test']}")
        break

cv2.destroyAllWindows()
