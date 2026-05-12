"""
sshopylcd_guide.py
===================
SShopy LCD 안내 중 페이지.

[흐름]
  1. PageManager 가 start(product) 호출
     → POST /sshopylcd/guide/start (동기) 로 backend 에 안내 시작 요청
     → 응답의 task_id 보관 (이후 polling 시 참고만; backend 는 robot_id 기준 조회)
     → 화면 = "따라오세요!"
     → QTimer 2초 주기 polling 시작 (GET /sshopylcd/guide/status)

  2. backend 가 진열대 도착을 감지하면 status.arrived=True 응답
     → polling 콜백이 _on_customer_arrived() 호출
     → polling 중단, 완료 팝업 표시

  3. 사용자가 '안내 종료' 클릭
     → POST /sshopylcd/guide/end (fire-and-forget) — backend 가 홈 복귀 명령
     → on_end 콜백 → PageManager 홈 페이지로 복귀

[연동 자리]
  - start_guide() 실패 시 (HTTP 409 등) 사용자에게 알리고 홈으로 복귀
  - 도착 감지 트리거는 backend 의 _check_arrival() 이 담당하므로 LCD 측 시뮬레이션
    버튼은 사용하지 않는다 (실로봇 동작 가정).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QFrame
)
from PySide6.QtCore import Qt, QTimer

from sshopylcd_common import (
    REF_W, REF_H, C_BG, C_FOREST, C_FOREST_T,
    TopBar, ConfirmOverlay
)


# polling 주기 — monitoring_ui 와 동일
GUIDE_POLL_INTERVAL_MS = 2000


class SshopyLcdGuidePage(QWidget):
    """안내 중 화면. 안내 시작 직전에 start(product) 로 진입 정보 주입."""

    GUIDE_MSG = (
        "선택하신 상품이 진열되어\n있는 위치로 안내해드리겠습니다!\n"
        "저를 따라오세요!"
    )
    ARRIVED_MSG = (
        "선택하신 상품이 위치한 진열대로\n안내해드렸습니다.\n\n"
        "시착 요청은 스마트폰으로 진열대의\n"
        "QR코드를 인식하거나 시착 좌석의\n"
        "키오스크를 이용해주세요!"
    )
    ERROR_MSG = (
        "안내를 시작할 수 없습니다.\n잠시 후 다시 시도해주세요."
    )

    def __init__(self, api_client, on_end=None):
        """
        api_client: SshopyLcdApiClient — guide/start, guide/end, guide/status 호출에 사용
        on_end: 안내 종료(완료 또는 시작 실패) 후 PageManager 가 Home 으로 돌아가도록 호출
        """
        super().__init__()
        self._api = api_client
        self._on_end = on_end or (lambda: None)
        self._product: dict | None = None
        self._task_id: str | None = None
        self._arrived: bool = False
        self._s = 0.5

        self.setStyleSheet(f"background:{C_FOREST};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 안내 중에는 home/back 버튼 모두 숨김 (조작 차단)
        self._topbar = TopBar(show_home=False, show_right=False)
        root.addWidget(self._topbar)

        # 본문
        self._body = QFrame()
        self._body.setStyleSheet(f"background:{C_FOREST};border:none;")
        body_lo = QVBoxLayout(self._body)
        body_lo.setAlignment(Qt.AlignCenter)
        self._body_lo = body_lo

        self._title = QLabel("안내 중")
        self._title.setAlignment(Qt.AlignCenter)
        body_lo.addWidget(self._title)

        self._msg = QLabel(self.GUIDE_MSG)
        self._msg.setAlignment(Qt.AlignCenter)
        self._msg.setWordWrap(True)
        body_lo.addWidget(self._msg)

        root.addWidget(self._body, stretch=1)

        # 완료/에러 팝업 오버레이
        self._overlay = ConfirmOverlay(self)

        # 2초 주기 polling 타이머 — start() 에서 활성, 도착 시 정지
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(GUIDE_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_once)

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self, product: dict):
        """
        PageManager 가 이 페이지로 전환하기 직전에 호출.
        backend 에 guide/start 동기 호출 후 polling 시작.
        """
        self._product = product or {}
        shoe_id = self._product.get("shoe_id", "")
        shoe_name = self._product.get("name", "")
        self._arrived = False
        self._task_id = None
        self._overlay.hide()

        def _on_start_result(resp: dict | None):
            # 통신 실패 또는 backend 거부
            if resp is None or not resp.get("success"):
                detail = (resp or {}).get("detail") if resp else None
                self._show_error(detail)
                return
            self._task_id = resp.get("task_id")
            # 도착 감지를 위해 polling 시작
            self._poll_timer.start()

        self._api.send_guide_start(
            shoe_id=shoe_id, shoe_name=shoe_name,
            callback=_on_start_result,
        )

    def stop_polling(self):
        """페이지 떠날 때 polling 종료 (안전망)."""
        self._poll_timer.stop()

    # ── polling ───────────────────────────────────────────────────────────
    def _poll_once(self):
        """단발 status 조회 — 응답에 따라 도착 감지 → 완료 팝업 전환."""
        self._api.poll_guide_status(callback=self._on_status)

    def _on_status(self, status: dict | None):
        if status is None:
            # 통신 실패 — 다음 폴링까지 무시 (fire-and-forget 정책)
            return
        if self._arrived:
            return
        if status.get("arrived"):
            self._arrived = True
            self._poll_timer.stop()
            self._on_customer_arrived()

    def _on_customer_arrived(self):
        """진열대 도착 감지 — 완료 팝업 + '안내 종료' 버튼 표시."""
        self._overlay.setup(
            message=self.ARRIVED_MSG,
            primary_text="안내 종료",
            secondary_text=None,
            on_primary=self._finish_guide,
        )
        self._overlay.show_over(self)

    def _show_error(self, detail: str | None):
        """guide/start 실패 시 안내 후 홈으로 복귀."""
        msg = self.ERROR_MSG + (f"\n\n({detail})" if detail else "")
        self._overlay.setup(
            message=msg,
            primary_text="확인",
            secondary_text=None,
            on_primary=self._on_end,
        )
        self._overlay.show_over(self)

    def _finish_guide(self):
        """안내 종료 — backend 가 SShopy 를 홈으로 복귀시킴 (fire-and-forget)."""
        self._poll_timer.stop()
        self._api.send_guide_end()
        self._on_end()

    # ── scale ─────────────────────────────────────────────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)

        m = max(round(16 * s), 6)
        self._body_lo.setContentsMargins(m, m, m, m)
        self._body_lo.setSpacing(max(round(10 * s), 4))

        self._title.setStyleSheet(
            f"color:{C_FOREST_T};font-size:{max(round(12 * s), 9)}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:300;"
            f"letter-spacing:{max(round(3 * s), 1)}px;background:transparent;"
        )
        self._msg.setStyleSheet(
            f"color:{C_BG};font-size:{max(round(15 * s), 11)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:500;line-height:1.5;background:transparent;"
        )

        if self._overlay.isVisible():
            self._overlay.setGeometry(0, 0, self.width(), self.height())
