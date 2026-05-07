"""
ui/widgets/floor_map.py
Live floor map — 실제 PGM 지도 위에 로봇 위치를 표시한다.

[실맵연동]
  - 배경: fms 서버(`/map/image`)에서 받은 PGM(PNG) 이미지를 종횡비 유지로 그림
  - 핑키 로봇(Sshopy 1/2/3): /api/robots 의 x,y (서버에서 0..1 정규화 완료된 값)
  - FrontJet/WareJet: 고정 워크포인트(world coords) → map_meta 로 정규화 변환
  - React admin_ui 의 MapView 와 동일한 ROS map 좌표 변환 규칙 적용
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore    import Qt, QRectF, QPointF, QByteArray, QTimer
from PySide6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont, QPixmap, QImage,
)

from api.client import ApiClient


# [실맵연동] 워크포인트 world 좌표 (App.jsx LOCATIONS 기준 — 2026-04-24 preset)
# React MapView 의 WAYPOINTS 와 동일: 홈(green) / 창고(orange) / 매장(blue).
# 미터 단위. map_meta 의 origin/resolution/width/height 로 정규화 [0..1] 로 변환.
WAYPOINTS_WORLD = [
    {"key": "home",     "x":  0.771, "y": -0.008, "label": "홈",   "color": "#16a34a"},
    {"key": "warejet",  "x": -0.003, "y":  0.160, "label": "창고", "color": "#ea580c"},
    {"key": "frontjet", "x":  0.720, "y":  0.477, "label": "매장", "color": "#2563eb"},
]


def _world_to_norm(wx: float, wy: float, meta: dict) -> tuple[float, float]:
    """[실맵연동] world(m) → [0..1] 정규화. ROS map 규약(좌하단 원점, y 위쪽이 양).

    화면 y 는 위가 0 이므로 1.0 - 정규 y 값 으로 반전.
    """
    res    = meta["resolution"]
    ox, oy = meta["origin"][0], meta["origin"][1]
    map_w_m = meta["width"]  * res
    map_h_m = meta["height"] * res
    nx = (wx - ox) / map_w_m if map_w_m else 0.5
    ny = 1.0 - (wy - oy) / map_h_m if map_h_m else 0.5
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    return nx, ny


class FloorMapWidget(QWidget):
    def __init__(self, api: ApiClient | None = None, parent=None):
        super().__init__(parent)
        # [반응형] minimumSize 는 apply_scale 에서 갱신
        self.setMinimumSize(260, 240)
        self._seats = []
        self._robots = {}   # name → {x, y, color, status, ...}
        # [반응형] 부모가 주입하는 스케일 — paintEvent 에서 폰트/도형 크기에 사용
        self._scale = 1.0
        # [실맵연동] 지도 이미지/메타 캐시
        self._api = api
        self._map_pixmap: QPixmap | None = None
        self._map_meta: dict | None = None
        # [실맵연동] 백그라운드 1회 로드 — 화면이 만들어진 직후 fetch.
        # 서버 미응답 시에는 빈 배경으로 폴백, 다음 retry 까지 일정 간격으로 재시도.
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._load_map_async)
        QTimer.singleShot(0, self._load_map_async)

    # ── Public API ────────────────────────────────────────────────────────

    def set_api(self, api: ApiClient):
        """[실맵연동] MainWindow 가 ApiClient 주입 시 호출."""
        self._api = api
        if self._map_pixmap is None:
            self._load_map_async()

    def update_seats(self, seats: list):
        self._seats = seats
        self.update()

    def update_robots(self, robots: dict):
        """robots: { name: {x, y, color, status, ...} } (x,y 0..1 정규화)."""
        self._robots = robots
        self.update()

    # [반응형] 외부에서 호출 — minimumSize 와 paintEvent 가 사용할 스케일 갱신
    def apply_scale(self, s: float):
        self._scale = s
        self.setMinimumSize(max(round(260 * s), 180), max(round(240 * s), 160))
        self.update()

    # ── [실맵연동] map 로딩 ──────────────────────────────────────────────

    def _load_map_async(self):
        """fms /map/image + /map/meta 를 동기 호출로 받아온 뒤 캐시.
        실패 시 5초 뒤 재시도.
        """
        if self._api is None:
            self._retry_timer.start(5000)
            return
        try:
            png_bytes = self._api.get_map_image_bytes()
            meta = self._api.get_map_meta()
        except Exception as e:
            print(f"[floor_map] map fetch 실패 (재시도 예약): {e}")
            self._retry_timer.start(5000)
            return

        img = QImage.fromData(QByteArray(png_bytes), "PNG")
        if img.isNull():
            print("[floor_map] 받은 PNG 디코드 실패")
            self._retry_timer.start(5000)
            return

        self._map_pixmap = QPixmap.fromImage(img)
        self._map_meta   = meta
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)  # 픽셀 선명하게

        w, h = self.width(), self.height()
        s = self._scale  # [반응형]

        # 배경: 흰색 + 보더
        p.fillRect(0, 0, w, h, QColor("#ffffff"))
        p.setPen(QPen(QColor("#d0d7de"), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # [실맵연동] 지도 이미지가 있으면 종횡비 유지로 fit 후 그 영역에 모든 좌표 매핑
        if self._map_pixmap is not None and not self._map_pixmap.isNull():
            img_rect = self._fit_rect(w, h, self._map_pixmap.width(), self._map_pixmap.height())
            p.drawPixmap(img_rect, self._map_pixmap, QRectF(self._map_pixmap.rect()))
        else:
            # 지도 미로딩 — 안내 텍스트
            p.setPen(QColor("#8c959f"))
            p.setFont(QFont("Courier New", max(round(10 * s), 8)))
            p.drawText(self.rect(), Qt.AlignCenter, "loading map…")
            img_rect = QRectF(0, 0, w, h)

        # ── [실맵연동] 워크포인트 (홈/창고/매장) — React MapView WAYPOINTS 와 동일
        # 로봇보다 먼저 그려야 핑키가 워크포인트 위로 지나갈 때 가려진다.
        if self._map_meta is not None:
            for wp in WAYPOINTS_WORLD:
                nx, ny = _world_to_norm(wp["x"], wp["y"], self._map_meta)
                cx = img_rect.x() + nx * img_rect.width()
                cy = img_rect.y() + ny * img_rect.height()
                self._draw_waypoint(p, cx, cy, wp["label"], wp["color"], s)

        # ── 로봇 표시 ─────────────────────────────────────────────────────
        # 핑키 (Sshopy 1/2/3) — 서버에서 받은 정규화 (x, y) 사용.
        # FrontJet/WareJet 은 React 와 동일하게 맵 위에는 별도 마커를 그리지 않는다
        # (창고/매장 워크포인트가 그 위치를 표시).
        for name, info in self._robots.items():
            if not name.startswith("Sshopy"):
                continue
            nx = info.get("x")
            ny = info.get("y")
            if nx is None or ny is None:
                continue
            cx = img_rect.x() + nx * img_rect.width()
            cy = img_rect.y() + ny * img_rect.height()
            short = "P" + name.split()[-1]   # React 와 동일: sshopy1 → P1
            self._draw_robot_circle(p, cx, cy, short,
                                    info.get("color", "#7c3aed"), s)

        # ── Legend ────────────────────────────────────────────────────────
        self._draw_legend(p, w, h, s)

        p.end()

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _fit_rect(widget_w: float, widget_h: float, img_w: int, img_h: int) -> QRectF:
        """[실맵연동] 위젯 안에 이미지를 종횡비 유지로 letterbox 배치."""
        if img_w <= 0 or img_h <= 0:
            return QRectF(0, 0, widget_w, widget_h)
        scale = min(widget_w / img_w, widget_h / img_h)
        out_w = img_w * scale
        out_h = img_h * scale
        ox = (widget_w - out_w) / 2.0
        oy = (widget_h - out_h) / 2.0
        return QRectF(ox, oy, out_w, out_h)

    # [반응형] 로봇 원형 마커 — 색상 채움 + 흰 텍스트
    @staticmethod
    def _draw_robot_circle(p: QPainter, cx: float, cy: float, label: str, color: str, s: float = 1.0):
        r = max(round(10 * s), 6)
        c = QColor(color)
        p.setPen(QPen(c.darker(130), 2))
        p.setBrush(QBrush(c))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Courier New", max(round(8 * s), 6), QFont.Bold))
        p.drawText(QRectF(cx - r, cy - r, r * 2, r * 2), Qt.AlignCenter, label)

    # [실맵연동] 워크포인트 마커 — React MapView 와 동일한 룩
    # (반투명 채움 + 채도 있는 외곽선 + 옆에 라벨 텍스트)
    @staticmethod
    def _draw_waypoint(p: QPainter, cx: float, cy: float, label: str, color: str, s: float = 1.0):
        r = max(round(5 * s), 3)
        c = QColor(color)
        # 반투명 채움 (alpha 0x55 ≈ 33% — React 의 color + '55' 와 동일)
        fill = QColor(c.red(), c.green(), c.blue(), 0x55)
        p.setPen(QPen(c, max(round(1.5 * s), 1)))
        p.setBrush(QBrush(fill))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        # 라벨 — 점 옆에 같은 색으로
        p.setPen(c)
        p.setFont(QFont("Courier New", max(round(9 * s), 7), QFont.Bold))
        p.drawText(QPointF(cx + r + max(round(2 * s), 1), cy + r * 0.8), label)

    # [실맵연동] 범례 — React 와 동일: 홈/창고/매장 + Pinky 로봇
    @staticmethod
    def _draw_legend(p: QPainter, w: float, h: float, s: float = 1.0):
        items = [
            ("#16a34a", "홈"),
            ("#ea580c", "창고"),
            ("#2563eb", "매장"),
            ("#7c3aed", "Pinky"),
        ]
        legend_pt = max(round(8 * s), 6)
        p.setFont(QFont("Courier New", legend_pt))
        x = max(round(6 * s), 3)
        y = h - max(round(14 * s), 8)
        dot_d = max(round(7 * s), 4)
        item_w = max(round(48 * s), 30)
        text_pad = max(round(10 * s), 5)
        for color, text in items:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(QRectF(x, y - 5, dot_d, dot_d))
            p.setPen(QColor("#57606a"))
            p.drawText(int(x + text_pad), int(y + 1), text)
            x += item_w
