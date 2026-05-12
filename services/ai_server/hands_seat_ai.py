"""
hands_seat_ai.py
────────────────────────────────────────────────
UDP로 카메라 프레임 수신 (PNG 청크 프로토콜) →
  1) hands_up 검출 → TCP vision_result JSON → main server 7019
  2) 피규어 감지 + ReID 추적 + 좌석 점유 → TCP seat_result JSON → main server 7019
────────────────────────────────────────────────
"""

import os
import time
import socket
import json
import struct
import math
import queue
import threading
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import cv2
import numpy as np
import torch
import torch.nn as nn
import yaml
from torchvision import models, transforms
from ultralytics import YOLO
from multi_robot_viewer import MultiRobotViewer
from pinky_pro_pose import PinkyProPoseEstimator, is_pinky_pro_robot

# ── 네트워크 설정 ──────────────────────────────────────────────────────
MAIN_SERVER_IP  = os.getenv("MAIN_SERVER_IP",  "192.168.1.120")
AI_SERVER_IP    = os.getenv("AI_LISTEN_IP",    "192.168.1.121")
AI_UDP_PORT     = int(os.getenv("AI_UDP_PORT", "7018"))
AI_TCP_PORT     = int(os.getenv("AI_TCP_PORT", "7019"))

UDP_LISTEN_IP   = "0.0.0.0"
UDP_BUFFER_SIZE = 65535

CHUNK_SIZE        = 60000
FRAME_TIMEOUT_SEC = 2.0

# ── UDP 바이너리 패킷 포맷 (final_udp_top.py / back_test.py 동일) ──────
MAGIC         = 0xA55A
PKT_DATA      = 0x00
PKT_HEARTBEAT = 0x01
HDR_FMT       = '!HIHHBB'               # magic·frame_id·chunk_idx·total·type·reserved
HDR_SIZE      = struct.calcsize(HDR_FMT) # 12 bytes
UDP_RECV_BUF  = 8 * 1024 * 1024        # 8 MB 수신 버퍼

# ── 모델 경로 ──────────────────────────────────────────────────────────
STANDING_MODEL = os.getenv("STANDING_MODEL_PATH", "/home/team1-ai/roscamp-repo-1/services/ai_server/robot_model/staindig/best.pt")
FIGURE_MODEL   = os.getenv("FIGURE_MODEL_PATH",   "/home/team1-ai/roscamp-repo-1/services/ai_server/robot_model/figure/best.pt")

# ── 맵 경로 ──────────────────────────────────────────────────────────
H_PATH        = os.getenv("VISION_H_PATH",        "/home/team1-ai/roscamp-repo-1/services/ai_server/robot_model/H.npy")
MAP_YAML_PATH = os.getenv("VISION_MAP_YAML_PATH", "/home/team1-ai/roscamp-repo-1/services/ai_server/robot_model/map/keepout_moosinsa.yaml")
MAP_PGM_PATH  = os.getenv("VISION_MAP_PGM_PATH",  "/home/team1-ai/roscamp-repo-1/services/ai_server/robot_model/map/keepout_moosinsa.pgm")

# ── 감지 설정 ──────────────────────────────────────────────────────────
CONF            = 0.40
SOLIDITY_THRESH = 0.85
REACH_THRESH    = 1.80
COOLDOWN_SEC    = 5.0

FIGURE_CLASS  = "figure"
TARGET_NAME   = "Main Target"
SCORE_THRESH  = 0.45
LOST_FRAMES   = 60
SEAT_SAVE     = Path("/home/team1-ai/roscamp-repo-1/services/ai_server/seats.json")
DEBOUNCE_SECS = 30
SEAT_SEND_INTERVAL = 1.0

# ── AI 동작 모드 ──────────────────────────────────────────────────────
MODE_SEAT   = 5   # 자리 점유 인식만
MODE_POSE   = 6   # hands_up / hands_down 인식만
MODE_TARGET = 7   # 타겟 ID 인식만
MODE_ALL    = 8   # 전체 기능 (기본값)

# ── 색상 ──────────────────────────────────────────────────────────────
POSE_COLORS = {
    "hands_up":   (80,  255, 120),
    "hands_down": (200, 180, 255),
}
COLOR_TARGET   = (255,  80, 200)
COLOR_OTHER    = (100, 100, 100)
COLOR_OCCUPIED = (0,    60, 255)
COLOR_EMPTY    = (0,   200,  80)
COLOR_GREEN    = (0,   255, 100)
COLOR_YELLOW   = (0,   230, 255)
COLOR_RED      = (0,    80, 255)

SEARCHING = "SEARCHING..."
TRACKING  = "TRACKING"
LOST_ST   = "LOST"
DISPLAY_SCALE = 1.0

# ── 좌석 ROI 전역 변수 ────────────────────────────────────────────────
seat_rois    = []
roi_size     = 80
mouse_pos    = (0, 0)
drag_idx     = -1
drag_offset  = (0, 0)
selected_roi = -1
locked_rois  = set()


# ══════════════════════════════════════════════════════════════════════
# TCP 서버
# ══════════════════════════════════════════════════════════════════════
_tcp_client_sock = None
_tcp_client_lock = threading.Lock()
_tcp_send_q      = queue.Queue(maxsize=2)


def _tcp_conn_monitor(conn, addr):
    """수락된 연결마다 띄우는 감시 스레드. recv=0이면 상대방이 연결 종료."""
    global _tcp_client_sock
    try:
        while conn.recv(1):
            pass
    except Exception:
        pass
    finally:
        with _tcp_client_lock:
            if _tcp_client_sock is conn:
                _tcp_client_sock = None
        try:
            conn.close()
        except Exception:
            pass
        print(f"[TCP] 연결 종료: {addr}")


def _enqueue_tcp(result_dict: dict):
    """최신 결과 2개만 유지. 큐가 가득 차면 오래된 결과를 버리고 최신을 넣는다."""
    try:
        _tcp_send_q.put_nowait(result_dict)
    except queue.Full:
        try:
            _tcp_send_q.get_nowait()
        except queue.Empty:
            pass
        _tcp_send_q.put_nowait(result_dict)


def _tcp_sender_worker():
    """단일 TCP 송신 스레드. 큐에서 직렬 소비 → 스레드 폭탄 방지."""
    while True:
        result_dict = _tcp_send_q.get()
        send_tcp_message(result_dict)


def tcp_server_thread():
    global _tcp_client_sock
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", AI_TCP_PORT))
    srv.listen(1)
    print(f"[TCP] Listening on 0.0.0.0:{AI_TCP_PORT}")
    while True:
        conn, addr = srv.accept()
        print(f"[TCP] Main server connected from {addr}")
        with _tcp_client_lock:
            if _tcp_client_sock:
                try:
                    _tcp_client_sock.close()
                except Exception:
                    pass
            _tcp_client_sock = conn
        threading.Thread(target=_tcp_conn_monitor, args=(conn, addr), daemon=True).start()


def send_tcp_message(result_dict: dict):
    global _tcp_client_sock
    payload = json.dumps(result_dict).encode("utf-8")
    header  = struct.pack("!I", len(payload))
    with _tcp_client_lock:
        sock = _tcp_client_sock
    if sock is None:
        print("[TCP] Main server not connected — dropped")
        return
    try:
        sock.sendall(header + payload)
        print(f"[TCP] → main server {len(payload)}B  type={result_dict.get('type')}")
    except Exception as e:
        print(f"[TCP] Send error: {e}")
        with _tcp_client_lock:
            _tcp_client_sock = None


# ══════════════════════════════════════════════════════════════════════
# ReID
# ══════════════════════════════════════════════════════════════════════
class ReIDExtractor:
    def __init__(self, device):
        self.device = device
        backbone = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
        self.model = nn.Sequential(*list(backbone.children())[:-1])
        self.model.eval().to(device)
        self.transform = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((128, 64)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406],
                                  [0.229, 0.224, 0.225]),
        ])

    @torch.no_grad()
    def extract(self, crop_bgr):
        if crop_bgr is None or crop_bgr.size == 0:
            return None
        rgb  = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        t    = self.transform(rgb).unsqueeze(0).to(self.device)
        feat = self.model(t).squeeze().cpu().numpy()
        return feat / (np.linalg.norm(feat) + 1e-6)


# ══════════════════════════════════════════════════════════════════════
# 유틸 함수
# ══════════════════════════════════════════════════════════════════════
def hsv_histogram(crop_bgr, bins=32):
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    hsv  = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h    = cv2.calcHist([hsv], [0], None, [bins], [0, 180])
    s    = cv2.calcHist([hsv], [1], None, [bins], [0, 256])
    hist = np.concatenate([h, s]).flatten().astype(np.float32)
    cv2.normalize(hist, hist)
    return hist


def combined_score(fa, fb, ha, hb):
    hist_score = float(cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL))
    if fa is None or fb is None:
        return hist_score
    # return (float(np.dot(fa, fb)) + hist_score) / 2.0
    return hist_score


def update_ref(ref_feat, ref_hist, new_feat, new_hist, alpha=0.05):
    h = (1 - alpha) * ref_hist + alpha * new_hist
    if ref_feat is None or new_feat is None:
        return None, h
    # f = (1 - alpha) * ref_feat + alpha * new_feat
    # return f / (np.linalg.norm(f) + 1e-6), h
    return None, h


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    return inter / ((ax2-ax1)*(ay2-ay1) + (bx2-bx1)*(by2-by1) - inter)


def nms_dets(dets, iou_thresh=0.3, rois=None):
    dets = sorted(dets, key=lambda d: d[4], reverse=True)
    kept = []
    for d in dets:
        suppress = False
        for k in kept:
            if iou(d[1], k[1]) > iou_thresh:
                if rois:
                    d_roi = next((i for i, r in enumerate(rois) if is_inside_roi(d[1], r)), -1)
                    k_roi = next((i for i, r in enumerate(rois) if is_inside_roi(k[1], r)), -1)
                    if d_roi >= 0 and k_roi >= 0 and d_roi != k_roi:
                        continue  # 서로 다른 ROI → suppress 하지 않음
                suppress = True
                break
        if not suppress:
            kept.append(d)
    return kept


def is_inside_roi(fig_box, seat_box):
    ix1 = max(fig_box[0], seat_box[0])
    iy1 = max(fig_box[1], seat_box[1])
    ix2 = min(fig_box[2], seat_box[2])
    iy2 = min(fig_box[3], seat_box[3])
    return ix1 < ix2 and iy1 < iy2


def resize_all_rois(rois, size):
    half = size // 2
    return [(cx - half, cy - half, cx + half, cy + half)
            for (x1, y1, x2, y2) in rois
            for cx, cy in [((x1+x2)//2, (y1+y2)//2)]]


def save_seats(seats):
    SEAT_SAVE.write_text(json.dumps(seats))


def load_seats():
    if SEAT_SAVE.exists():
        return [tuple(s) for s in json.loads(SEAT_SAVE.read_text())]
    return []


def classify_pose_geometric(frame, x1, y1, x2, y2, sol_thresh, reach_thresh):
    pad = 6
    h_img, w_img = frame.shape[:2]
    roi = frame[max(0,y1-pad):min(h_img,y2+pad), max(0,x1-pad):min(w_img,x2+pad)]
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
    fig_mask  = np.zeros_like(mask)
    cv2.drawContours(fig_mask, [cnt], -1, 255, -1)
    pts = np.argwhere(fig_mask > 0).astype(np.float32)
    if len(pts) < 30:
        return None, 0.0
    dists       = np.linalg.norm(pts - pts.mean(axis=0), axis=1)
    reach_ratio = np.percentile(dists, 95) / (dists.mean() + 1e-6)
    score = min((sol_thresh - solidity) / sol_thresh,
                (reach_ratio - reach_thresh) / (reach_thresh - 1.0))
    if score > 0.0:
        return "hands_up",   score
    elif score < 0.0:
        return "hands_down", -score
    return None, score


def load_map_metadata(yaml_path):
    with open(yaml_path) as f:
        info = yaml.safe_load(f)
    return float(info["resolution"]), info["origin"]


def get_map_height(pgm_path):
    return cv2.imread(pgm_path, cv2.IMREAD_GRAYSCALE).shape[0]


def pixel_to_map_metric(px, py, H, map_height, resolution, origin):
    pt = np.array([[[float(px), float(py)]]], dtype=np.float32)
    mp = cv2.perspectiveTransform(pt, H)[0][0]
    ox, oy, _ = origin
    return float(ox + mp[0] * resolution), float(oy + (map_height - mp[1]) * resolution)


# ══════════════════════════════════════════════════════════════════════
# 마우스 콜백
# ══════════════════════════════════════════════════════════════════════
def _roi_hit(x, y):
    for i, (x1, y1, x2, y2) in enumerate(seat_rois):
        if x1 <= x <= x2 and y1 <= y <= y2:
            return i
    return -1


def on_mouse(event, x, y, flags, param):
    global roi_size, mouse_pos, drag_idx, drag_offset
    mouse_pos = (x, y)

    if event == cv2.EVENT_MOUSEWHEEL:
        roi_size = max(20, min(400, roi_size + (10 if flags > 0 else -10)))
        seat_rois[:] = resize_all_rois(seat_rois, roi_size)
        save_seats(seat_rois)

    elif event == cv2.EVENT_LBUTTONDOWN:
        if selected_roi >= 0:
            if selected_roi < len(seat_rois) and selected_roi not in locked_rois:
                x1, y1, x2, y2 = seat_rois[selected_roi]
                drag_idx    = selected_roi
                drag_offset = (x - x1, y - y1)
        else:
            hit = _roi_hit(x, y)
            if hit >= 0 and hit not in locked_rois:
                drag_idx = hit
                x1, y1, x2, y2 = seat_rois[hit]
                drag_offset = (x - x1, y - y1)
            elif len(seat_rois) >= 4:
                print("[WARN] 좌석은 최대 4개까지만 등록 가능합니다.")
            else:
                half = roi_size // 2
                seat_rois.append((x - half, y - half, x + half, y + half))
                save_seats(seat_rois)
                print(f"[INFO] 좌석 {len(seat_rois)}/4 등록  크기: {roi_size}px")

    elif event == cv2.EVENT_MOUSEMOVE and drag_idx >= 0:
        ox, oy = drag_offset
        x1, y1, x2, y2 = seat_rois[drag_idx]
        w, h = x2 - x1, y2 - y1
        seat_rois[drag_idx] = (x - ox, y - oy, x - ox + w, y - oy + h)

    elif event == cv2.EVENT_LBUTTONUP and drag_idx >= 0:
        save_seats(seat_rois)
        print(f"[INFO] 좌석 #{drag_idx+1} 이동 완료: {seat_rois[drag_idx]}")
        drag_idx = -1


# ══════════════════════════════════════════════════════════════════════
# 메인 클래스
# ══════════════════════════════════════════════════════════════════════
class HandsSeatAI:
    def __init__(self):
        # 맵
        self.H          = np.load(H_PATH)
        self.resolution, self.origin = load_map_metadata(MAP_YAML_PATH)
        self.map_height = get_map_height(MAP_PGM_PATH)

        # 모델
        print("[INFO] 모델 로딩 중...")
        self.standing_model = YOLO(STANDING_MODEL)
        self.figure_model   = YOLO(FIGURE_MODEL)
        self.pinky_pose     = PinkyProPoseEstimator()
        print("  standing:", list(self.standing_model.names.values()))
        print("  figure  :", list(self.figure_model.names.values()))

        # ReID
        # device    = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        # self.reid = ReIDExtractor(device)
        # print(f"  Device  : {device}")
        self.reid = None

        # UDP
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RECV_BUF)
        self.udp_sock.bind((UDP_LISTEN_IP, AI_UDP_PORT))
        self.udp_sock.settimeout(0.03)
        print(f"[UDP] Listening on {UDP_LISTEN_IP}:{AI_UDP_PORT} (binary chunk mode)")

        # PNG 청크 재조합 버퍼
        self._frame_buffers = {}  # (addr, frame_id) -> {"chunks": {}, "total": int, "t": float}
        self._fb_lock       = threading.Lock()

        # 수신·추론 분리 큐 (maxsize=1: 항상 최신 프레임만 유지)
        self._frame_queue = queue.Queue(maxsize=1)
        threading.Thread(target=self._udp_receiver, daemon=True).start()

        # 좌석 ROI
        global seat_rois
        seat_rois = load_seats()
        if seat_rois:
            print(f"[INFO] 저장된 좌석 {len(seat_rois)}개 불러옴")

        # 포즈/추적 상태
        self._last_pub_t       = 0.0
        self._last_frame_id    = 0
        self._ref_feat         = None
        self._ref_hist         = None
        self._track_state      = SEARCHING
        self._target_id        = None
        self._target_ids       = []
        self._confirmed_fig_ids = set()
        self._target_enabled   = True
        self._auto_tracking    = True
        self._lost_count       = 0
        self._stable_fig_count = None
        self._cand_count       = 0
        self._cand_since       = 0.0
        self._frozen_frame     = None
        self._last_seat_send   = 0.0
        self._fig_boxes        = []   # figure 모델 bbox (이전 프레임, 포즈 필터링용)
        self._ai_mode          = MODE_ALL

        # FPS
        self._fps_count  = 0
        self._fps_last_t = time.time()
        self._udp_pkt_count = 0
        self._udp_chunk_count = 0
        self._udp_frame_count = 0
        self._udp_queue_drop_count = 0
        self._udp_stat_last_t = time.time()
        self._last_udp_frame_size = None
        self._model_input_count = 0
        self._model_input_last_t = time.time()
        self._last_model_frame_id = None

        self._running = True

        # 좌석 점유 분류기 (학습 후 활성화)
        cls_path = Path(__file__).parent / "robot_model" / "occupancy_cls" / "weights" / "best.pt"
        self._occ_cls = YOLO(str(cls_path)) if cls_path.exists() else None
        if self._occ_cls:
            print("[INFO] 좌석 점유 분류기 로드됨")
        else:
            print("[INFO] 좌석 점유 분류기 없음 → figure_model 사용")

        self.WIN = "Hands & Seat AI"
        self.viewer = MultiRobotViewer(self.WIN, main_robot_no=6)
        cv2.setMouseCallback(self.WIN, on_mouse)

        print("[INFO] 시작  [1~4] 좌석선택  [7] 타겟표시 ON/OFF  [A] 자동추적 ON/OFF  [R] 타겟초기화  [Z] 좌석삭제  [+/-] ROI크기  [5/6/8] 모드  [Q] 종료")

    # ── PNG 청크 버퍼 만료 정리 ───────────────────────────────────────
    def _cleanup_frames(self):
        now = time.time()
        expired = [fid for fid, b in self._frame_buffers.items()
                   if now - b["t"] > FRAME_TIMEOUT_SEC]
        for fid in expired:
            del self._frame_buffers[fid]

    def _print_udp_stats(self):
        now = time.time()
        elapsed = now - self._udp_stat_last_t
        if elapsed < 3.0:
            return

        recv_fps = self._udp_frame_count / elapsed
        avg_chunks = (self._udp_chunk_count / self._udp_frame_count
                      if self._udp_frame_count else 0.0)
        print(
            "[UDP] "
            f"packets={self._udp_pkt_count} "
            f"chunks={self._udp_chunk_count} "
            f"frames={self._udp_frame_count} "
            f"recv_fps={recv_fps:.1f} "
            f"last_size={self._last_udp_frame_size or 'N/A'} "
            f"avg_chunks/frame={avg_chunks:.1f} "
            f"queue_drops={self._udp_queue_drop_count}"
        )

        self._udp_pkt_count = 0
        self._udp_chunk_count = 0
        self._udp_frame_count = 0
        self._udp_queue_drop_count = 0
        self._udp_stat_last_t = now

    # ── UDP 수신 전담 스레드 ──────────────────────────────────────────
    def _udp_receiver(self):
        while True:
            try:
                data, addr = self.udp_sock.recvfrom(UDP_BUFFER_SIZE)
            except Exception:
                self._print_udp_stats()
                continue
            self._udp_pkt_count += 1

            if len(data) < HDR_SIZE:
                self._print_udp_stats()
                continue

            magic, frame_id, chunk_idx, total, pkt_type, _ = struct.unpack(
                HDR_FMT, data[:HDR_SIZE])

            if magic != MAGIC:
                self._print_udp_stats()
                continue
            if total == 0 or chunk_idx >= total:
                self._print_udp_stats()
                continue
            if pkt_type == PKT_HEARTBEAT:
                self._print_udp_stats()
                continue

            self._udp_chunk_count += 1
            chunk_data = data[HDR_SIZE:]
            buffer_key = (addr, frame_id)

            with self._fb_lock:
                self._cleanup_frames()

                # 같은 송신자의 오래된 frame_id 버퍼만 폐기
                old_keys = [key for key in self._frame_buffers
                            if key[0] == addr and key[1] < frame_id]
                for key in old_keys:
                    del self._frame_buffers[key]

                if buffer_key not in self._frame_buffers:
                    self._frame_buffers[buffer_key] = {
                        "chunks": {}, "total": total, "t": time.time()
                    }
                self._frame_buffers[buffer_key]["chunks"][chunk_idx] = chunk_data

                if len(self._frame_buffers[buffer_key]["chunks"]) == total:
                    full = b"".join(
                        self._frame_buffers[buffer_key]["chunks"][i] for i in range(total)
                    )
                    del self._frame_buffers[buffer_key]
                else:
                    continue

            try:
                meta_len  = struct.unpack('!H', full[:2])[0]
                meta      = json.loads(full[2:2 + meta_len].decode('utf-8'))
                png_bytes = full[2 + meta_len:]
                frame     = cv2.imdecode(
                    np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    self._print_udp_stats()
                    continue
            except Exception as e:
                print(f"[DECODE] 실패: {e}")
                self._print_udp_stats()
                continue

            # 오래된 프레임 버리고 최신만 유지
            try:
                self._frame_queue.get_nowait()
                self._udp_queue_drop_count += 1
            except queue.Empty:
                pass
            fh, fw = frame.shape[:2]
            self._last_udp_frame_size = f"{fw}x{fh}"
            meta["recv_ts"] = time.time()
            self._frame_queue.put((frame, meta))
            self._udp_frame_count += 1
            self._print_udp_stats()

    def run(self):
        while self._running:
            self._loop()

    def _loop(self):
        # ── 큐에서 최신 프레임 가져오기 ──────────────────────────────
        try:
            frame, meta = self._frame_queue.get(timeout=0.03)
        except queue.Empty:
            cv2.waitKey(1)
            return

        robot_id      = meta.get("robot_id", 0)
        frame_id_meta = meta.get("frame_id", 0)
        self._last_frame_id = frame_id_meta

        # AI 모델 투입 직전 프레임 측정
        self._fps_count += 1
        self._model_input_count += 1
        now_t = time.time()
        fh, fw = frame.shape[:2]
        input_wait_ms = (now_t - meta.get("recv_ts", now_t)) * 1000.0
        if now_t - self._fps_last_t >= 3.0:
            print(f"[FPS] {self._fps_count / (now_t - self._fps_last_t):.1f} fps")
            self._fps_count  = 0
            self._fps_last_t = now_t
        if now_t - self._model_input_last_t >= 3.0:
            elapsed = now_t - self._model_input_last_t
            frame_gap = (frame_id_meta - self._last_model_frame_id
                         if self._last_model_frame_id is not None else 0)
            print(
                "[MODEL_IN] "
                f"frames={self._model_input_count} "
                f"fps={self._model_input_count / elapsed:.1f} "
                f"last_frame_id={frame_id_meta} "
                f"frame_gap={frame_gap} "
                f"size={fw}x{fh} "
                f"queue_wait_ms={input_wait_ms:.1f}"
            )
            self._model_input_count = 0
            self._model_input_last_t = now_t
            self._last_model_frame_id = frame_id_meta

        annotated = frame.copy()
        t0        = time.time()
        pinky_pose_result = None

        run_pose = self._ai_mode in (MODE_POSE, MODE_ALL)
        run_seat = self._ai_mode in (MODE_SEAT, MODE_TARGET, MODE_ALL)
        run_target = self._target_enabled and self._ai_mode in (MODE_POSE, MODE_TARGET, MODE_ALL)

        # ── 1) 포즈 감지 ──────────────────────────────────────────────
        if run_pose:
            detections, goals = self._detect_pose(
                frame, annotated, fw, fh, robot_id, frame_id_meta)
        else:
            detections, goals = [], []

        if is_pinky_pro_robot(robot_id):
            pinky_pose_result = self.pinky_pose.process(annotated, robot_id)

        # ── 2) 피규어 + 좌석 감지 ────────────────────────────────────
        if run_seat or run_target:
            seat_status = self._detect_seat(
                frame, annotated, fw, fh, draw_seats=run_seat)
        else:
            seat_status = []
            self._target_ids = []

        # ── 모드 표시 ─────────────────────────────────────────────────
        _MODE_LABEL = {MODE_SEAT: "5:SEAT", MODE_POSE: "6:POSE",
                       MODE_TARGET: "7:TARGET", MODE_ALL: "8:ALL"}
        cv2.putText(annotated, f"[{_MODE_LABEL.get(self._ai_mode, '?')}]",
                    (fw - 160, fh - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 255), 2)

        # ── TCP 전송 (모드와 무관하게 데이터가 있으면 항상 송신) ──────
        if detections or pinky_pose_result is not None:
            _enqueue_tcp({
                "type":            "vision_result",
                "robot_id":        robot_id,
                "detection_count": len(detections),
                "detections":      detections,
                "goal_count":      len(goals),
                "goals":           goals,
                "pinky_pose":      pinky_pose_result,
                "target_id":       self._target_id,
                "target_ids":      self._target_ids,
                "target_count":    len(self._target_ids),
                "process_ms":      round((time.time() - t0) * 1000, 2),
                "timestamp":       time.time(),
            })

        if run_seat and time.time() - self._last_seat_send >= SEAT_SEND_INTERVAL:
            _enqueue_tcp({
                "type":        "seat_result",
                "robot_id":    robot_id,
                "seat_count":  len(seat_rois),
                "seat_status": [1 if occ else 0 for _, occ in seat_status],
                "target_id":   self._target_id,
                "target_ids":  self._target_ids,
                "target_count": len(self._target_ids),
                "timestamp":   time.time(),
            })
            self._last_seat_send = time.time()

        # ── 화면 표시는 별도 viewer 모듈에 위임 ───────────────────────
        if DISPLAY_SCALE != 1.0:
            dw = int(fw * DISPLAY_SCALE)
            dh = int(fh * DISPLAY_SCALE)
            display = cv2.resize(annotated, (dw, dh), interpolation=cv2.INTER_LINEAR)
        else:
            display = annotated
        self.viewer.update(robot_id, display)
        self._handle_key(self.viewer.show())

    # ── 포즈 감지 ──────────────────────────────────────────────────────
    def _detect_pose(self, frame, annotated, fw, fh, robot_id, frame_id):
        t0 = time.time()
        pose_res = self.standing_model.predict(frame, conf=CONF, verbose=False)
        result   = pose_res[0]

        detections, goals = [], []
        now = time.time()

        if result.boxes is None:
            return detections, goals

        for i, box in enumerate(result.boxes.xyxy):
            x1, y1, x2, y2 = map(int, box)
            model_pose = self.standing_model.names[int(result.boxes.cls[i])]
            pose_conf  = float(result.boxes.conf[i])

            # figure 모델 bbox와 겹치는 것만 유효한 감지로 인정
            if self._fig_boxes and not any(
                    iou((x1, y1, x2, y2), fb) > 0.05 for fb in self._fig_boxes):
                continue

            geo_pose, _ = classify_pose_geometric(
                frame, x1, y1, x2, y2, SOLIDITY_THRESH, REACH_THRESH)
            final_pose = geo_pose if geo_pose is not None else model_pose

            color = POSE_COLORS.get(final_pose, (200, 200, 200))

            if result.masks is not None and i < len(result.masks.data):
                mask_np = result.masks.data[i].cpu().numpy().astype(np.uint8)
                mask_np = cv2.resize(mask_np, (fw, fh))
                overlay = annotated.copy()
                overlay[mask_np > 0] = color
                cv2.addWeighted(overlay, 0.35, annotated, 0.65, 0, annotated)

            cv2.putText(annotated, f"{final_pose} {pose_conf:.2f}",
                        (x1, max(y1 - 6, 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

            # 좌석 번호 판정 (1-indexed, 없으면 None)
            seat_id = None
            for j, roi in enumerate(seat_rois):
                if is_inside_roi((x1, y1, x2, y2), roi):
                    seat_id = j + 1
                    break

            detections.append({
                "class_name":   final_pose,
                "pose":         final_pose,
                "model_pose":   model_pose,
                "confidence":   round(pose_conf, 4),
                "bbox":         [x1, y1, x2, y2],
                "center_pixel": {"x": cx, "y": cy},
                "seat_id":      seat_id,
            })

            if final_pose == "hands_up" and now - self._last_pub_t >= COOLDOWN_SEC:
                mx, my = pixel_to_map_metric(
                    cx, cy, self.H, self.map_height, self.resolution, self.origin)
                goal = {
                    "pixel":      {"x": cx, "y": cy},
                    "map":        {"x": round(mx, 4), "y": round(my, 4), "theta": 0.0},
                    "pose":       final_pose,
                    "confidence": round(pose_conf, 4),
                }
                goals.append(goal)
                self._last_pub_t = now
                cv2.circle(annotated, (cx, cy), 12, (255, 100, 0), 3)
                cv2.putText(annotated, f"GOAL ({mx:.2f},{my:.2f})",
                            (cx + 14, cy - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 100, 0), 2)
                print(f"[GOAL] pixel=({cx},{cy}) map=({mx:.3f},{my:.3f})")

        remain     = max(0.0, COOLDOWN_SEC - (time.time() - self._last_pub_t))
        cooldown_c = (0, 80, 255) if remain > 0 else (0, 220, 0)
        cv2.putText(annotated, f"goal cooldown: {remain:.1f}s",
                    (15, fh - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, cooldown_c, 2)

        return detections, goals

    # ── 피규어 + 좌석 감지 ────────────────────────────────────────────
    def _detect_seat(self, frame, annotated, fw, fh, draw_seats=True):
        if drag_idx >= 0:
            if self._frozen_frame is None:
                self._frozen_frame = frame.copy()
            base = self._frozen_frame
        else:
            self._frozen_frame = None
            base = frame

        fig_results = self.figure_model.track(
            base, persist=True, conf=0.3, verbose=False)
        fig_dets = []

        if (fig_results[0].boxes is not None
                and fig_results[0].boxes.id is not None):
            boxes = fig_results[0].boxes
            for i, (box, tid) in enumerate(zip(boxes.xyxy, boxes.id)):
                if self.figure_model.names[int(boxes.cls[i])] != FIGURE_CLASS:
                    continue
                x1, y1, x2, y2 = map(int, box)
                conf = float(boxes.conf[i])
                crop = base[max(0, y1):y2, max(0, x1):x2]
                # feat = self.reid.extract(crop)
                feat = None
                hist = hsv_histogram(crop)
                if hist is not None:
                    fig_dets.append((int(tid), (x1,y1,x2,y2), feat, hist, conf))

        fig_dets = nms_dets(fig_dets, rois=seat_rois)
        all_fig_dets = fig_dets[:]  # 좌석 점유/타겟 표시용 (stable 제한 전)

        now = time.time()
        raw_count = len(fig_dets)
        if self._stable_fig_count is None:
            self._stable_fig_count = raw_count
            self._cand_count = raw_count
            self._cand_since = now
        else:
            if raw_count != self._cand_count:
                self._cand_count = raw_count
                self._cand_since = now
            if (self._cand_count != self._stable_fig_count
                    and now - self._cand_since >= DEBOUNCE_SECS):
                self._stable_fig_count = self._cand_count

        target_det  = next((d for d in fig_dets if d[0] == self._target_id), None)
        others      = [d for d in fig_dets if d[0] != self._target_id]
        if self._stable_fig_count and self._stable_fig_count < raw_count:
            extra_slots = max(0, self._stable_fig_count - (1 if target_det else 0))
            fig_dets = ([target_det] if target_det else []) + others[:extra_slots]
        else:
            fig_dets = ([target_det] if target_det else []) + others

        # 새 figure 등장 시 타겟으로 추가 (고정)
        if self._target_enabled:
            for det in all_fig_dets:
                if det[0] not in self._confirmed_fig_ids:
                    self._confirmed_fig_ids.add(det[0])
                    print(f"[INFO] 새 피규어 #{det[0]} 감지 → 타겟 추가")
        self._target_ids = [d[0] for d in all_fig_dets
                            if d[0] in self._confirmed_fig_ids] if self._target_enabled else []

        if self._occ_cls and seat_rois:
            seat_status = []
            for roi in seat_rois:
                x1, y1, x2, y2 = roi
                crop = base[max(0,y1):y2, max(0,x1):x2]
                if crop.size == 0:
                    seat_status.append((roi, False))
                    continue
                res = self._occ_cls.predict(crop, verbose=False, imgsz=64)
                names = res[0].names
                probs = res[0].probs.data.tolist()
                occ_idx = next(i for i, n in names.items() if n == "occupied")
                occupied = probs[occ_idx] > 0.5
                seat_status.append((roi, occupied))
        else:
            occupy_boxes = [d[1] for d in all_fig_dets]
            seat_status = [(roi, any(is_inside_roi(fb, roi) for fb in occupy_boxes))
                           for roi in seat_rois]

        found = False
        if self._target_enabled and self._auto_tracking and self._ref_hist is None and fig_dets:
            det = max(fig_dets, key=lambda d: (d[1][2]-d[1][0])*(d[1][3]-d[1][1]))
            self._target_id = det[0]
            self._ref_feat = det[2]
            self._ref_hist = det[3]
            self._track_state = TRACKING
            self._lost_count = 0
            found = True

        if self._target_enabled and self._auto_tracking and self._ref_hist is not None and fig_dets:
            direct = next((d for d in fig_dets if d[0] == self._target_id), None)
            if direct:
                found = True
                self._track_state = TRACKING
                self._lost_count  = 0
                self._ref_feat, self._ref_hist = update_ref(
                    self._ref_feat, self._ref_hist, direct[2], direct[3])
            else:
                scores = [(combined_score(self._ref_feat, d[2], self._ref_hist, d[3]), d)
                          for d in fig_dets]
                best_s, best_det = max(scores, key=lambda x: x[0])
                if best_s >= SCORE_THRESH:
                    self._target_id   = best_det[0]
                    self._track_state = TRACKING
                    self._lost_count  = 0
                    found = True
                    self._ref_feat, self._ref_hist = update_ref(
                        self._ref_feat, self._ref_hist, best_det[2], best_det[3])
                else:
                    self._lost_count += 1
                    if self._lost_count > LOST_FRAMES:
                        self._track_state = LOST_ST

        if draw_seats:
            for i, ((sx1,sy1,sx2,sy2), is_occ) in enumerate(seat_status):
                color = COLOR_OCCUPIED if is_occ else COLOR_EMPTY
                label = f"#{i+1} OCCUPIED" if is_occ else f"#{i+1} EMPTY"
                overlay = annotated.copy()
                cv2.rectangle(overlay, (sx1,sy1), (sx2,sy2), color, -1)
                cv2.addWeighted(overlay, 0.25, annotated, 0.75, 0, annotated)
                border_color     = (255, 255, 255) if i == selected_roi else color
                border_thickness = 2              if i == selected_roi else 1
                cv2.rectangle(annotated, (sx1,sy1), (sx2,sy2), border_color, border_thickness)
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
                cv2.putText(annotated, label,
                            ((sx1+sx2)//2 - tw//2, (sy1+sy2)//2 + th//2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
                if i in locked_rois:
                    cv2.putText(annotated, "LOCK", (sx1 + 4, sy1 + 16),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 2)
                if i == selected_roi:
                    cv2.putText(annotated, "SEL", (sx2 - 40, sy1 + 16),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

            if len(seat_rois) < 4:
                mx_m, my_m = mouse_pos
                half = roi_size // 2
                cv2.rectangle(annotated,
                              (mx_m-half, my_m-half), (mx_m+half, my_m+half),
                              COLOR_YELLOW, 2)

        if self._target_enabled:
            for target_idx, det in enumerate(all_fig_dets, start=1):
                tid, (x1,y1,x2,y2), _, _, _ = det
                color     = COLOR_TARGET
                overlay   = annotated.copy()
                cv2.rectangle(overlay, (x1,y1), (x2,y2), color, -1)
                cv2.addWeighted(overlay, 0.35, annotated, 0.65, 0, annotated)
                cv2.rectangle(annotated, (x1,y1), (x2,y2), color, 2)
                label = f"{TARGET_NAME} {target_idx}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
                cv2.rectangle(annotated, (x1, y1-th-12), (x1+tw+6, y1), color, -1)
                cv2.putText(annotated, label, (x1+3, y1-5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255,255,255), 2)

        if draw_seats:
            occ_cnt = sum(1 for _, occ in seat_status if occ)
            cv2.putText(annotated, f"Seats: {occ_cnt}/{len(seat_rois)} occupied",
                        (fw - 290, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)

        if self._target_enabled:
            if not self._target_ids:
                stxt, scol = "Targets: 0", COLOR_GREEN
            elif self._track_state == LOST_ST:
                stxt, scol = f"Targets: {len(self._target_ids)} / Track: LOST", COLOR_RED
            else:
                stxt, scol = f"Targets: {len(self._target_ids)}", COLOR_YELLOW
            auto_txt = "AutoTrack: ON" if self._auto_tracking else "AutoTrack: OFF"
            cv2.putText(annotated, auto_txt, (15, 66),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                        COLOR_GREEN if self._auto_tracking else COLOR_RED, 2)
            cv2.putText(annotated, stxt, (15, 38),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, scol, 2)

        # 다음 프레임 포즈 필터링용으로 figure bbox 저장
        self._fig_boxes = [d[1] for d in fig_dets]

        return seat_status

    # ── 키 처리 ───────────────────────────────────────────────────────
    def _handle_key(self, key):
        global roi_size, seat_rois, selected_roi, locked_rois, DISPLAY_SCALE

        if key == ord('q'):
            self._running = False

        elif key in (ord('1'), ord('2'), ord('3'), ord('4')):
            seat_idx = key - ord('1')
            if seat_idx < len(seat_rois):
                selected_roi = -1 if selected_roi == seat_idx else seat_idx
                if selected_roi >= 0:
                    print(f"[INFO] 좌석 #{selected_roi+1} 선택")
                else:
                    print("[INFO] 좌석 선택 해제")
            else:
                print(f"[WARN] 좌석 #{seat_idx+1}이 아직 없습니다")

        elif key in (ord('a'), ord('A')):
            self._auto_tracking = not self._auto_tracking
            if not self._auto_tracking:
                self._track_state = SEARCHING
                self._target_id = None
                self._target_ids = []
                self._lost_count = 0
                self._ref_feat = None
                self._ref_hist = None
            print(f"[TRACK] 자동추적 {'ON' if self._auto_tracking else 'OFF'}")

        elif key in (ord('c'), ord('v')):
            label = "empty" if key == ord('c') else "occupied"
            self._save_roi_crops(label)

        elif key == ord('g'):
            if selected_roi >= 0 and selected_roi < len(seat_rois):
                if selected_roi in locked_rois:
                    locked_rois.discard(selected_roi)
                    print(f"[INFO] 좌석 #{selected_roi+1} 고정 해제")
                else:
                    locked_rois.add(selected_roi)
                    print(f"[INFO] 좌석 #{selected_roi+1} 고정됨")
            else:
                print("[WARN] 먼저 1~4 키로 좌석을 선택하세요")

        elif key == ord('r'):
            self._ref_feat          = None
            self._ref_hist          = None
            self._track_state       = SEARCHING
            self._target_id         = None
            self._target_ids        = []
            self._confirmed_fig_ids = set()
            self._lost_count        = 0
            print("[INFO] 타겟 초기화")
        elif key == ord('z') and seat_rois:
            removed = seat_rois.pop()
            locked_rois.discard(len(seat_rois))
            save_seats(seat_rois)
            print(f"[INFO] 좌석 삭제: {removed}  남은: {len(seat_rois)}개")
        elif key in (ord('+'), ord('=')):
            roi_size = min(roi_size + 10, 400)
            seat_rois[:] = resize_all_rois(seat_rois, roi_size)
            save_seats(seat_rois)
            print(f"[INFO] ROI 크기: {roi_size}px")
        elif key == ord('-'):
            roi_size = max(roi_size - 10, 20)
            seat_rois[:] = resize_all_rois(seat_rois, roi_size)
            save_seats(seat_rois)
            print(f"[INFO] ROI 크기: {roi_size}px")
        elif key == ord(']'):
            DISPLAY_SCALE = round(min(DISPLAY_SCALE + 0.1, 3.0), 1)
            print(f"[INFO] 화면 배율: {DISPLAY_SCALE:.1f}x")
        elif key == ord('['):
            DISPLAY_SCALE = round(max(DISPLAY_SCALE - 0.1, 0.3), 1)
            print(f"[INFO] 화면 배율: {DISPLAY_SCALE:.1f}x")
        elif key == ord('5'):
            self._ai_mode = MODE_SEAT
            print("[MODE] 5 — 자리 점유 인식만")
        elif key == ord('6'):
            self._ai_mode = MODE_POSE
            print("[MODE] 6 — hands_up / hands_down 인식만")
        elif key == ord('7'):
            self._target_enabled = not self._target_enabled
            if not self._target_enabled:
                self._ref_feat    = None
                self._ref_hist    = None
                self._track_state = SEARCHING
                self._target_id   = None
                self._target_ids  = []
                self._lost_count  = 0
            print(f"[TARGET] 타겟 표시 {'ON' if self._target_enabled else 'OFF'}")
        elif key == ord('8'):
            self._ai_mode = MODE_ALL
            print("[MODE] 8 — 전체 기능")

    def _save_roi_crops(self, label: str):
        if not seat_rois:
            print("[WARN] 저장된 좌석 ROI 없음")
            return
        frame_q = list(self._frame_queue.queue)
        if not frame_q:
            print("[WARN] 현재 프레임 없음")
            return
        frame, _ = frame_q[-1]
        save_dir = Path(__file__).parent / "roi_dataset" / label
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        for i, (x1, y1, x2, y2) in enumerate(seat_rois):
            crop = frame[max(0,y1):y2, max(0,x1):x2]
            if crop.size == 0:
                continue
            path = save_dir / f"seat{i+1}_{ts}.jpg"
            cv2.imwrite(str(path), crop)
        print(f"[DATA] {label} 이미지 {len(seat_rois)}장 저장 → roi_dataset/{label}/")

    def close(self):
        self.udp_sock.close()
        self.viewer.close()
        cv2.destroyAllWindows()


# ══════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════
def main():
    threading.Thread(target=tcp_server_thread,  daemon=True).start()
    threading.Thread(target=_tcp_sender_worker, daemon=True).start()
    ai = HandsSeatAI()
    try:
        ai.run()
    except KeyboardInterrupt:
        print("\n[INFO] 종료")
    finally:
        ai.close()


if __name__ == "__main__":
    main()
