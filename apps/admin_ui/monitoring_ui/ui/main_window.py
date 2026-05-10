"""
ui/main_window.py
Root window — owns all screens and wires data flow.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QStackedWidget,
    QMessageBox,
)
from PySide6.QtCore import Qt

from ui.styles            import GLOBAL_STYLE, REF_W, REF_H  # [반응형] 기준 해상도
from ui.login_screen      import LoginScreen
from ui.widgets.topbar    import TopBar
from ui.monitoring_screen import MonitoringScreen
from ui.management_screen import ManagementScreen
from api.client           import ApiClient, PollingWorker, AdminWebSocketClient  # [monitoring_ui] WS 추가


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MOOSINSA · ADMIN SYSTEM")
        # [반응형] 초기 크기 = 기준 해상도. 최소 크기는 너무 작아지지 않도록 보호.
        self.resize(REF_W, REF_H)
        self.setMinimumSize(720, 480)
        self.setStyleSheet(GLOBAL_STYLE)

        self.api    = ApiClient()
        self.poller = PollingWorker(self.api)
        # ════════════════════════════════════════════════════════════════
        # [monitoring_ui] /ws/admin 실시간 push 구독 (폴링과 병행)
        # ────────────────────────────────────────────────────────────────
        # 로봇/좌석/요청 데이터는 1초 주기 WebSocket push 로 갱신하고,
        # dashboard/schedule/inventory 는 PollingWorker(2초) 가 담당한다.
        # WebSocket 단절 시에도 PollingWorker 가 폴백 역할을 수행한다.
        # ════════════════════════════════════════════════════════════════
        self.ws_admin = AdminWebSocketClient()

        self._build_ui()
        self._connect_signals()

    # ── UI Construction ───────────────────────────────────────────────────

    def _build_ui(self):
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # ── Page 0: Login ─────────────────────────────────────────────────
        self.login_screen = LoginScreen()
        self._stack.addWidget(self.login_screen)

        # ── Page 1: Admin (TopBar + sub-screens) ──────────────────────────
        admin_page = QWidget()
        admin_lay  = QVBoxLayout(admin_page)
        admin_lay.setContentsMargins(0, 0, 0, 0)
        admin_lay.setSpacing(0)

        self.topbar = TopBar()
        admin_lay.addWidget(self.topbar)

        self.sub_stack = QStackedWidget()
        self.monitoring  = MonitoringScreen(self.api)
        self.management  = ManagementScreen(self.api)
        self.sub_stack.addWidget(self.monitoring)   # idx 0
        self.sub_stack.addWidget(self.management)   # idx 1
        admin_lay.addWidget(self.sub_stack)

        self._stack.addWidget(admin_page)

    # ── Signal Wiring ─────────────────────────────────────────────────────

    def _connect_signals(self):
        # Login
        self.login_screen.login_requested.connect(self._on_login)

        # TopBar
        self.topbar.tab_changed.connect(self.sub_stack.setCurrentIndex)
        self.topbar.logout_clicked.connect(self._on_logout)
        self.topbar.emergency_clicked.connect(self._on_emergency)

        # Poller → Monitoring
        self.poller.robots_updated.connect(self.monitoring.on_robots_updated)
        self.poller.schedule_updated.connect(self.monitoring.on_schedule_updated)
        self.poller.seats_updated.connect(self.monitoring.on_seats_updated)
        # [실로봇연동] schedule 백엔드 연결 상태 → '연결 안됨' 배너
        self.poller.schedule_connection_changed.connect(
            self.monitoring.on_schedule_connection_changed
        )

        # Poller → Management
        self.poller.dashboard_updated.connect(self.management.on_dashboard_updated)
        # [실로봇연동] dashboard 백엔드 연결 상태 → '연결 안됨' 배너
        self.poller.dashboard_connection_changed.connect(
            self.management.on_dashboard_connection_changed
        )
        # [실로봇연동] inventory(MSS_DB) 연결 상태 → 'DB 연결 안됨' 배너
        self.poller.inventory_connection_changed.connect(
            self.management.on_inventory_connection_changed
        )
        self.poller.inventory_updated.connect(self.management.on_inventory_updated)
        # [실로봇연동] management 좌석 표시는 kiosk_tryon 과 동일 source(/kiosk/seat/status) 로 변경.
        # /api/seats (in-memory 시나리오 점유) 는 monitoring FloorMap 만 사용.
        self.poller.kiosk_seats_updated.connect(self.management.on_kiosk_seats_updated)
        self.poller.kiosk_seats_connection_changed.connect(
            self.management.on_kiosk_seats_connection_changed
        )
        self.poller.requests_updated.connect(self.management.on_requests_updated)
        # [실로봇연동] /api/requests 연결 상태 → '연결 안됨' 배너
        self.poller.requests_connection_changed.connect(
            self.management.on_requests_connection_changed
        )
        # [실로봇연동] /api/robots 폴링 연결 상태 → 로봇 제어 카드 '연결 안됨' 배너
        self.poller.robots_connection_changed.connect(
            self.management.on_robots_connection_changed
        )
        self.poller.robots_updated.connect(self.management.on_robots_updated)

        # ════════════════════════════════════════════════════════════════
        # [monitoring_ui] /ws/admin push → 같은 슬롯에 시그널 연결
        # ────────────────────────────────────────────────────────────────
        # 폴링 핸들러와 동일 슬롯이라 idempotent — 중복 emit 시 단순 갱신만 발생.
        # ════════════════════════════════════════════════════════════════
        self.ws_admin.robots_updated.connect(self.monitoring.on_robots_updated)
        self.ws_admin.seats_updated.connect(self.monitoring.on_seats_updated)
        self.ws_admin.robots_updated.connect(self.management.on_robots_updated)
        # [실로봇연동] /ws/admin 의 seats 는 fleet 시나리오 점유 — management 시착 좌석 표시는
        # 별도 /kiosk/seat/status 폴링으로 대체했으므로 여기에는 연결하지 않는다.
        self.ws_admin.requests_updated.connect(self.management.on_requests_updated)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_login(self, user_id: str, password: str):
        try:
            self.api.login(user_id, password)
        except Exception:
            # Allow entry even when server is unreachable (demo / offline mode)
            pass

        # Simple local credential check for demo
        if user_id == "admin" and password:
            self._enter_admin()
        else:
            self.login_screen.show_error("로그인 실패: 아이디 또는 비밀번호를 확인하세요.")

    def _enter_admin(self):
        self._stack.setCurrentIndex(1)
        self.topbar.set_tab(0)
        self.poller.start()
        self.ws_admin.start()  # [monitoring_ui] WebSocket 실시간 push 구독 시작

    def _on_logout(self):
        self.poller.stop()
        self.ws_admin.stop()   # [monitoring_ui] WebSocket 종료
        self.api.logout()
        self._stack.setCurrentIndex(0)

    def _on_emergency(self):
        reply = QMessageBox.warning(
            self, "비상정지",
            "모든 로봇을 즉시 정지합니다.\n계속하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                self.api.emergency_stop()
            except Exception as e:
                QMessageBox.critical(self, "오류", f"비상정지 명령 실패:\n{e}")

    def closeEvent(self, event):
        self.poller.stop()
        self.ws_admin.stop()   # [monitoring_ui] WebSocket 종료
        super().closeEvent(event)

    # [반응형] 윈도우 크기 변경 시 모든 자식 위젯에 동일 스케일 전파.
    # s = min(w/REF_W, h/REF_H) — 작은 비율 기준으로 비례 축소/확대해
    # 가로/세로 비율을 유지한다.
    def resizeEvent(self, event):
        super().resizeEvent(event)
        s = min(self.width() / REF_W, self.height() / REF_H)
        self.login_screen.apply_scale(s)
        self.topbar.apply_scale(s)
        self.monitoring.apply_scale(s)
        self.management.apply_scale(s)
