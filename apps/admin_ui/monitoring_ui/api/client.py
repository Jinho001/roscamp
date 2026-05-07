"""
api/client.py
─────────────────────────────────────────────────────────────────────────────
HTTP 클라이언트 (requests) + 폴링용 QThread 워커.

엔드포인트 규칙 (FastAPI 서버 기준):
  GET  /api/dashboard          → DashboardData
  GET  /api/robots             → List[RobotStatus]
  GET  /api/schedule           → List[TaskRecord]
  GET  /api/inventory          → List[InventoryItem]
  GET  /api/seats              → SeatGrid
  GET  /api/requests           → List[RequestItem]
  POST /api/robot/{name}/start → { task_name }
  POST /api/robot/{name}/stop  → {}
  POST /api/robot/{name}/manual→ {}
  POST /api/inbound/start      → {}
  POST /api/auth/login         → { token } | 401
"""

import json
import os
import requests
from PySide6.QtCore import QThread, Signal, QTimer, QObject


# [원격배포] kiosk_ui 와 동일한 env var 패턴 — 다른 PC 에서 모니터링 UI 실행 시 사용:
#   MOOSINSA_SERVICE_HOST=192.168.x.120 python3 monitoring_home.py
# 미설정 시 'localhost' (같은 PC 에서 서버 실행 시).
_SERVICE_HOST = os.environ.get("MOOSINSA_SERVICE_HOST", "localhost")
_SERVICE_PORT = os.environ.get("MOOSINSA_SERVICE_PORT", "8000")
BASE_URL = os.environ.get(
    "MOOSINSA_BASE_URL",
    f"http://{_SERVICE_HOST}:{_SERVICE_PORT}",
).rstrip("/")
POLL_INTERVAL_MS = 2000  # 2 seconds

# [실맵연동][원격배포] React admin_ui 와 동일하게 fms (port 8002) 의 /map/image, /map/meta 사용.
# 기본 host 는 _SERVICE_HOST 와 같음 (보통 같은 서버), 별도 host 가 필요하면 MOOSINSA_MAP_HOST 로 override.
_MAP_HOST = os.environ.get("MOOSINSA_MAP_HOST", _SERVICE_HOST)
_MAP_PORT = os.environ.get("MOOSINSA_MAP_PORT", "8002")
MAP_BASE_URL = os.environ.get(
    "MOOSINSA_MAP_BASE_URL",
    f"http://{_MAP_HOST}:{_MAP_PORT}",
).rstrip("/")


class ApiClient(QObject):
    """Thin synchronous wrapper around the REST API."""

    def __init__(self, base_url: str = BASE_URL):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ── Auth ──────────────────────────────────────────────────────────────

    def login(self, user_id: str, password: str) -> dict:
        """Returns {"token": "..."} or raises on failure."""
        resp = self._session.post(
            f"{self.base_url}/api/auth/login",
            json={"user_id": user_id, "password": password},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data.get("token")
        if self._token:
            self._session.headers["Authorization"] = f"Bearer {self._token}"
        return data

    def logout(self):
        self._token = None
        self._session.headers.pop("Authorization", None)

    # ── GET helpers ───────────────────────────────────────────────────────

    def _get(self, path: str) -> dict | list:
        resp = self._session.get(f"{self.base_url}{path}", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict | None = None) -> dict:
        resp = self._session.post(
            f"{self.base_url}{path}",
            json=payload or {},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Domain methods ────────────────────────────────────────────────────

    def get_dashboard(self) -> dict:
        return self._get("/api/dashboard")

    def get_robots(self) -> list:
        return self._get("/api/robots")

    def get_schedule(self) -> list:
        return self._get("/api/schedule")

    def get_inventory(self) -> list:
        return self._get("/api/inventory")

    def get_seats(self) -> dict:
        return self._get("/api/seats")

    # [실로봇연동] kiosk_tryon 페이지가 폴링하는 좌석 점유 상태 (top-view cam YOLO 기반).
    # 응답: {"seats": {"1": bool, "2": bool, ...}}
    # /api/seats (in-memory tryon 시나리오 점유) 와는 다른 데이터 소스.
    def get_kiosk_seat_status(self) -> dict:
        return self._get("/kiosk/seat/status")

    def get_requests(self) -> list:
        return self._get("/api/requests")

    def robot_start(self, robot_name: str, task_name: str, seat_id: int | None = None) -> dict:
        # [실로봇연동] tryon 일 때 seat_id 동봉 (1~4); 다른 task 에서는 무시.
        payload: dict = {"task_name": task_name}
        if seat_id is not None:
            payload["seat_id"] = seat_id
        return self._post(f"/api/robot/{robot_name}/start", payload)

    def robot_stop(self, robot_name: str) -> dict:
        return self._post(f"/api/robot/{robot_name}/stop")

    def robot_manual(self, robot_name: str) -> dict:
        return self._post(f"/api/robot/{robot_name}/manual")

    # [실로봇연동] 로봇 이벤트 로그 — 로그확인 버튼이 사용
    def robot_log(self, robot_name: str, limit: int = 50) -> dict:
        return self._get(f"/api/robot/{robot_name}/log?limit={int(limit)}")

    def inbound_start(
        self,
        robot_id: str | None = None,
        items: list[dict] | None = None,
    ) -> dict:
        # [실로봇연동] React /inbound/start 와 동일한 페이로드 ({robot_id, items}).
        # robot_id=None 이면 fleet 가 idle 핑키 자동 배정.
        # items 각 원소: {product_id: str, size: int, color: str, quantity: int}.
        payload: dict = {}
        if robot_id:
            payload["robot_id"] = robot_id
        if items:
            payload["items"] = items
        return self._post("/api/inbound/start", payload)

    def emergency_stop(self) -> dict:
        return self._post("/api/emergency/stop")

    # ── [실맵연동] 지도 이미지/메타 ─────────────────────────────────────────
    # fms 서버(port 8002)의 /map/image (PNG bytes) 와 /map/meta (해상도/원점/크기) 사용.
    def get_map_image_bytes(self) -> bytes:
        resp = self._session.get(f"{MAP_BASE_URL}/map/image", timeout=5)
        resp.raise_for_status()
        return resp.content

    def get_map_meta(self) -> dict:
        resp = self._session.get(f"{MAP_BASE_URL}/map/meta", timeout=5)
        resp.raise_for_status()
        return resp.json()


# ── Mock data used when server is unreachable ─────────────────────────────

# [실로봇연동] MOCK_DASHBOARD 삭제 — 서버 미연결 시 가짜 KPI 가 표시되던 원인.
# 대신 PollingWorker._fetch_dashboard 가 실패하면 dashboard_connection_changed(False)
# 시그널을 emit 하고 management_screen 이 KPI 값을 '—' 로 표시한다.

# [실로봇연동] MOCK_ROBOTS 삭제 — 서버 미연결 시 가짜 배터리/task 가 그대로 표시되던 원인.
# 대신 서버 호출 실패 시 _disconnected_robots() 가 모든 로봇을 disconnected 로 마킹해 emit.
_ROBOT_DEFINITIONS = [
    # (display_name, is_battery: 핑키=True/jetcobot=False)
    ("Sshopy 1", True),
    ("Sshopy 2", True),
    ("Sshopy 3", True),
    ("FrontJet", False),
    ("WareJet",  False),
]


def _disconnected_robots() -> list:
    """[실로봇연동] 서버 unreachable 시 표시할 disconnected 상태.

    Sshopy: power=None → robot_panel 의 _battery_color(0) → 빨강 0%, 'connected:False'.
    FJ/WJ : connected=False → robot_panel 이 'disconnect' 배지 표시.
    task : '—' (대기 라벨도 아님 — 데이터 없음을 명시).
    """
    return [
        {
            "name":       name,
            "power":      None,
            "is_battery": is_battery,
            "connected":  False,
            "task":       "—",
        }
        for name, is_battery in _ROBOT_DEFINITIONS
    ]

# [실로봇연동] MOCK_SCHEDULE 삭제 — 서버 미연결 시 가짜 task 행이 표시되던 원인.
# 대신 PollingWorker._fetch_schedule 가 실패하면 schedule_connection_changed(False) 시그널을 emit.

# [실로봇연동] MOCK_INVENTORY 삭제 — 서버/DB 미연결 시 가짜 재고가 표시되던 원인.
# 대신 _fetch_inventory 실패 시 inventory_connection_changed(False) 시그널 emit.

# [실로봇연동] MOCK_SEATS 삭제 — 서버 미연결 시 가짜 좌석 점유 표시되던 원인.
# 대신 _fetch_seats / _fetch_kiosk_seats 가 실패 시 빈 데이터 + connection_changed 시그널.

# [실로봇연동] MOCK_REQUESTS 삭제 — 서버 미연결 시 가짜 시착 요청이 표시되던 원인.
# 대신 _fetch_requests 실패 시 requests_connection_changed(False) 시그널 emit.

# [실맵연동] MOCK_ROBOT_POSITIONS 삭제 — 실제 pose(/api/robots 의 x,y 정규화 좌표) 사용.


# ── Polling worker ────────────────────────────────────────────────────────

class PollingWorker(QObject):
    """
    QTimer-based periodic polling.  Emits signals with fresh data.
    Falls back to mock data when the server is unreachable.
    """
    dashboard_updated = Signal(dict)
    robots_updated    = Signal(list)
    schedule_updated  = Signal(list)
    inventory_updated = Signal(list)
    seats_updated     = Signal(dict)
    requests_updated  = Signal(list)
    error_occurred    = Signal(str)
    # [실로봇연동] 섹션별 연결 상태 — True=정상, False=서버 unreachable.
    # UI 에서 "연결 안됨" 배너 표시 여부 결정.
    schedule_connection_changed     = Signal(bool)
    dashboard_connection_changed    = Signal(bool)
    inventory_connection_changed    = Signal(bool)
    kiosk_seats_connection_changed  = Signal(bool)
    requests_connection_changed     = Signal(bool)
    robots_connection_changed       = Signal(bool)
    # [실로봇연동] kiosk_tryon 과 동일한 source(/kiosk/seat/status) 의 좌석 점유 상태.
    # 신호 형식: {"seats": {"1": bool, "2": bool, ...}}
    kiosk_seats_updated             = Signal(dict)

    def __init__(self, client: ApiClient, interval_ms: int = POLL_INTERVAL_MS):
        super().__init__()
        self.client = client
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)
        self._use_mock = False

    def start(self):
        self._poll()
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _poll(self):
        self._fetch_dashboard()
        self._fetch_robots()
        self._fetch_schedule()
        self._fetch_inventory()
        self._fetch_seats()
        # [실로봇연동] kiosk_tryon 과 동일 source — management_screen 좌석 점유 표시용
        self._fetch_kiosk_seats()
        self._fetch_requests()

    # ── individual fetchers ──────────────────────────────────────────────

    def _fetch_dashboard(self):
        # [실로봇연동] 서버 unreachable 시 mock 대신 빈 dict + disconnected 시그널.
        # UI 가 "연결 안됨" 배너 + KPI 값을 '—' 로 표시한다.
        try:
            data = self.client.get_dashboard()
            self.dashboard_connection_changed.emit(True)
            self.dashboard_updated.emit(data)
        except Exception:
            self.dashboard_connection_changed.emit(False)
            self.dashboard_updated.emit({})

    def _fetch_robots(self):
        # [실로봇연동] 서버 연결 실패 시 mock 대신 disconnected 상태를 emit —
        # 가짜 배터리 % 와 가짜 task 가 화면에 표시되던 문제 제거.
        # 추가로 robots_connection_changed 시그널로 UI 가 '연결 안됨' 배너 표시 가능.
        try:
            data = self.client.get_robots()
            self.robots_connection_changed.emit(True)
            self.robots_updated.emit(data)
        except Exception:
            self.robots_connection_changed.emit(False)
            self.robots_updated.emit(_disconnected_robots())

    def _fetch_schedule(self):
        # [실로봇연동] 서버 unreachable 시 mock 대신 빈 리스트 + disconnected 시그널.
        # UI 가 "연결 안됨" 배너를 표시하도록 한다.
        try:
            data = self.client.get_schedule()
            self.schedule_connection_changed.emit(True)
            self.schedule_updated.emit(data)
        except Exception:
            self.schedule_connection_changed.emit(False)
            self.schedule_updated.emit([])

    def _fetch_inventory(self):
        # [실로봇연동] DB 미연결 (서버 503 또는 네트워크 실패) 시 mock 대신
        # 빈 리스트 + disconnected 시그널 — UI 가 'DB 연결 안됨' 배너 표시.
        try:
            data = self.client.get_inventory()
            self.inventory_connection_changed.emit(True)
            self.inventory_updated.emit(data)
        except Exception:
            self.inventory_connection_changed.emit(False)
            self.inventory_updated.emit([])

    def _fetch_seats(self):
        # [실로봇연동] /api/seats — fleet in-memory 시나리오 점유 (monitoring_screen FloorMap 용).
        # 실패 시 mock 대신 빈 리스트.
        try:
            self.seats_updated.emit(self.client.get_seats())
        except Exception:
            self.seats_updated.emit({"seats": []})

    def _fetch_kiosk_seats(self):
        # [실로봇연동] /kiosk/seat/status — top-view cam YOLO 기반 좌석 점유.
        # kiosk_tryon 과 동일 source. management_screen 의 시착 좌석 현황 표시용.
        try:
            data = self.client.get_kiosk_seat_status()
            self.kiosk_seats_connection_changed.emit(True)
            self.kiosk_seats_updated.emit(data or {})
        except Exception:
            self.kiosk_seats_connection_changed.emit(False)
            self.kiosk_seats_updated.emit({"seats": {}})

    def _fetch_requests(self):
        # [실로봇연동] 서버 unreachable 시 mock 대신 빈 리스트 + disconnected 시그널.
        try:
            data = self.client.get_requests()
            self.requests_connection_changed.emit(True)
            self.requests_updated.emit(data)
        except Exception:
            self.requests_connection_changed.emit(False)
            self.requests_updated.emit([])


# ══════════════════════════════════════════════════════════════════════════════
# [monitoring_ui] /ws/admin 실시간 push 구독 클라이언트
# ──────────────────────────────────────────────────────────────────────────────
# moosinsa_service.py 의 /ws/admin 엔드포인트를 QWebSocket 으로 구독하여
# fleet 상태(로봇/좌석/요청)를 1초 주기로 받아 같은 시그널로 emit 한다.
# PollingWorker(2초 폴링)와 병렬로 동작 — WebSocket 단절 시 폴링이 폴백 역할.
# 끊기면 RECONNECT_INTERVAL_MS 마다 자동 재연결.
# ══════════════════════════════════════════════════════════════════════════════

from PySide6.QtCore import QUrl
from PySide6.QtWebSockets import QWebSocket

WS_RECONNECT_INTERVAL_MS = 3000


class AdminWebSocketClient(QObject):
    """[monitoring_ui] /ws/admin 실시간 push 구독."""

    robots_updated   = Signal(list)
    seats_updated    = Signal(dict)
    requests_updated = Signal(list)
    connection_changed = Signal(bool)

    def __init__(self, base_url: str = BASE_URL, parent=None):
        super().__init__(parent)
        # http(s):// → ws(s)://, /ws/admin 경로 부착
        scheme_url = base_url.rstrip("/")
        if scheme_url.startswith("http://"):
            ws_url = "ws://" + scheme_url[len("http://"):]
        elif scheme_url.startswith("https://"):
            ws_url = "wss://" + scheme_url[len("https://"):]
        else:
            ws_url = scheme_url
        self._url = ws_url + "/ws/admin"

        self._ws = QWebSocket()
        self._ws.connected.connect(self._on_connected)
        self._ws.disconnected.connect(self._on_disconnected)
        self._ws.textMessageReceived.connect(self._on_message)
        self._ws.errorOccurred.connect(self._on_error)

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(WS_RECONNECT_INTERVAL_MS)
        self._reconnect_timer.timeout.connect(self._try_open)

        self._wanted = False

    def start(self):
        self._wanted = True
        self._try_open()

    def stop(self):
        self._wanted = False
        self._reconnect_timer.stop()
        try:
            self._ws.close()
        except Exception:
            pass

    def _try_open(self):
        if not self._wanted:
            return
        # QWebSocket 은 이미 연결되어 있거나 연결 중이면 open() 호출을 내부에서 무시한다
        try:
            self._ws.open(QUrl(self._url))
        except Exception as e:
            print(f"[ws/admin] open failed: {e}")

    def _on_connected(self):
        self._reconnect_timer.stop()
        print(f"[ws/admin] connected → {self._url}")
        self.connection_changed.emit(True)

    def _on_disconnected(self):
        print("[ws/admin] disconnected")
        self.connection_changed.emit(False)
        if self._wanted:
            self._reconnect_timer.start()

    def _on_error(self, _err):
        # 자동 재연결에 맡기고 자세한 로깅만
        print(f"[ws/admin] error: {self._ws.errorString()}")

    def _on_message(self, raw: str):
        try:
            data = json.loads(raw)
        except Exception:
            return
        if data.get("type") != "fleet_status":
            return
        if "robots" in data and isinstance(data["robots"], list):
            self.robots_updated.emit(data["robots"])
        if "seats" in data and isinstance(data["seats"], dict):
            self.seats_updated.emit(data["seats"])
        if "requests" in data and isinstance(data["requests"], list):
            self.requests_updated.emit(data["requests"])
