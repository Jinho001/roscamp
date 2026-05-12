"""
ROI Otsu 테스트
  실행: python3 test_roi_otsu.py
  조작: Q = 종료 / S = 현재 결과 저장
"""
import json
import socket
import struct
import threading
import queue
from pathlib import Path

import cv2
import numpy as np

# ── 설정 ──────────────────────────────────────────────────────────────
UDP_PORT  = 7018
MAGIC     = 0xA55A
HDR_FMT   = '!HIHHBB'
HDR_SIZE  = struct.calcsize(HDR_FMT)
SEAT_SAVE = Path(__file__).parent / "seats.json"
MIN_DECREASE  = 200   # 흰픽셀 감소량 임계값
SOL_CHANGE    = 0.10  # solidity 변화 임계값 (0.10 = 10% 변화)
EMPTY_SAVE    = Path(__file__).parent / "roi_empty_areas.json"


def load_seats():
    if SEAT_SAVE.exists():
        return [tuple(s) for s in json.loads(SEAT_SAVE.read_text())]
    return []


def get_roi_stats(frame, roi):
    """ROI Otsu 마스크에서 흰픽셀 수 + solidity 반환"""
    x1, y1, x2, y2 = roi
    crop = frame[max(0, y1):y2, max(0, x1):x2]
    if crop.size == 0:
        return None, 0, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    border = np.concatenate([mask[0,:], mask[-1,:], mask[:,0], mask[:,-1]])
    if np.mean(border) > 128:
        mask = cv2.bitwise_not(mask)

    white_pixels = int(np.sum(mask == 255))

    # solidity 계산
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_SIMPLE)
    solidity = 0.0
    if cnts:
        cnt = max(cnts, key=cv2.contourArea)
        cnt_area = cv2.contourArea(cnt)
        hull_area = cv2.contourArea(cv2.convexHull(cnt))
        solidity = cnt_area / (hull_area + 1e-6) if hull_area > 0 else 0.0

    return mask, white_pixels, round(solidity, 3)


# ── UDP 수신 ──────────────────────────────────────────────────────────
frame_queue   = queue.Queue(maxsize=1)
frame_buffers = {}
fb_lock       = threading.Lock()


def udp_receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", UDP_PORT))
    sock.settimeout(0.03)
    print(f"[UDP] 수신 대기 port={UDP_PORT}")
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except Exception:
            continue
        if len(data) < HDR_SIZE:
            continue
        magic, frame_id, chunk_idx, total, pkt_type, _ = struct.unpack(
            HDR_FMT, data[:HDR_SIZE])
        if magic != MAGIC or total == 0 or chunk_idx >= total or pkt_type == 0x01:
            continue
        key = (addr, frame_id)
        with fb_lock:
            for k in [k for k in frame_buffers if k[0] == addr and k[1] < frame_id]:
                del frame_buffers[k]
            frame_buffers.setdefault(key, {"chunks": {}, "total": total})
            frame_buffers[key]["chunks"][chunk_idx] = data[HDR_SIZE:]
            if len(frame_buffers[key]["chunks"]) < total:
                continue
            full = b"".join(frame_buffers[key]["chunks"][i] for i in range(total))
            del frame_buffers[key]
        try:
            meta_len = struct.unpack('!H', full[:2])[0]
            frame = cv2.imdecode(
                np.frombuffer(full[2 + meta_len:], np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
        except Exception:
            continue
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            pass
        frame_queue.put(frame)


# ── 메인 ─────────────────────────────────────────────────────────────
def main():
    seat_rois = load_seats()
    if not seat_rois:
        print("[WARN] seats.json 없음 — hands_seat_ai.py 먼저 실행해 ROI 등록하세요")

    threading.Thread(target=udp_receiver, daemon=True).start()

    min_decrease       = MIN_DECREASE
    sol_change_thresh  = SOL_CHANGE
    save_idx = 0

    # 저장된 empty 기준 불러오기 (구형식 int / 신형식 dict 모두 처리)
    empty_areas = {}
    if EMPTY_SAVE.exists():
        raw = json.loads(EMPTY_SAVE.read_text())
        for k, v in raw.items():
            if isinstance(v, dict):
                empty_areas[int(k)] = v
            else:
                empty_areas[int(k)] = {"white": int(v), "sol": None}
        print(f"[LOAD] empty 기준 불러옴: {empty_areas}")

    print("조작키: E=empty저장  Q=종료  S=화면저장")
    print("        +/-=흰픽셀감소 임계값  [/]=solidity변화 임계값")
    print(f"현재 MIN_DECREASE={min_decrease}  SOL_CHANGE={sol_change_thresh}")

    while True:
        try:
            frame = frame_queue.get(timeout=0.5)
        except queue.Empty:
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            continue

        annotated   = frame.copy()
        debug_crops = []

        for i, roi in enumerate(seat_rois):
            x1, y1, x2, y2 = roi
            mask, white, sol = get_roi_stats(frame, roi)

            if i in empty_areas:
                base_white = empty_areas[i]["white"]
                base_sol   = empty_areas[i].get("sol")
                decrease   = base_white - white

                cond1 = decrease > min_decrease
                if base_sol is not None:
                    sol_change = abs(sol - base_sol)
                    cond2 = sol_change > sol_change_thresh
                    sol_str = f"sol={sol:.2f}→{base_sol:.2f}Δ{sol_change:.2f}[{'O' if cond2 else 'X'}]"
                else:
                    cond2 = False
                    sol_str = f"sol={sol:.2f}(E키재저장)"

                occupied = cond1 or cond2
                c1 = "O" if cond1 else "X"
                info_str = f"dec={decrease:+d}[{c1}] {sol_str}"
            else:
                occupied = False
                info_str = f"w={white} sol={sol:.2f}  (E키로 기준저장)"

            color = (0, 60, 255) if occupied else (0, 200, 80)
            label = f"#{i+1} {'OCC' if occupied else 'EMPTY'}  {info_str}"
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.putText(annotated, label, (x1, y1 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 2)

            if mask is not None:
                vis = cv2.cvtColor(cv2.resize(mask, (120, 120)), cv2.COLOR_GRAY2BGR)
                cv2.putText(vis, f"#{i+1} w={white} s={sol:.2f}", (4, 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
                debug_crops.append(vis)

        cv2.putText(annotated,
                    f"dec>{min_decrease}[+/-]  sol>{sol_change_thresh:.2f}[[/]]  [E]기준저장",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 230, 255), 2)
        cv2.imshow("ROI Otsu Test", annotated)

        if debug_crops:
            h = max(c.shape[0] for c in debug_crops)
            row = np.hstack([np.pad(c, ((0, h - c.shape[0]), (0, 0), (0, 0)),
                                    constant_values=50) for c in debug_crops])
            cv2.imshow("Otsu Masks", row)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('e'):
            for i, roi in enumerate(seat_rois):
                _, white, sol = get_roi_stats(frame, roi)
                empty_areas[i] = {"white": white, "sol": sol}
                print(f"[EMPTY] 좌석 #{i+1}  white={white}  sol={sol:.3f}")
            EMPTY_SAVE.write_text(json.dumps(empty_areas))
            print(f"[EMPTY] {EMPTY_SAVE} 저장 완료")
        elif key in (ord('+'), ord('=')):
            min_decrease = min(min_decrease + 50, 5000)
            print(f"[DEC] 흰픽셀 감소 임계값={min_decrease}")
        elif key == ord('-'):
            min_decrease = max(min_decrease - 50, 0)
            print(f"[DEC] 흰픽셀 감소 임계값={min_decrease}")
        elif key == ord(']'):
            sol_change_thresh = round(min(sol_change_thresh + 0.05, 1.0), 2)
            print(f"[SOL] solidity 변화 임계값={sol_change_thresh}")
        elif key == ord('['):
            sol_change_thresh = round(max(sol_change_thresh - 0.05, 0.01), 2)
            print(f"[SOL] solidity 변화 임계값={sol_change_thresh}")
        elif key == ord('s'):
            fname = f"otsu_result_{save_idx:03d}.jpg"
            cv2.imwrite(fname, annotated)
            print(f"[SAVE] {fname}")
            save_idx += 1

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
