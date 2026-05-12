"""
sshopylcd_home.py
==================
SShopy AMR LCD UI 진입점.

[기동 조건]
  손 든 고객을 backend 가 감지 → 유휴 AMR 배차 → AMR 도착 → LCD 가 이 UI 를 띄움.
  즉, 본 UI 는 항상 떠 있지 않고 일회성 안내 task 수명 동안만 동작한다.

[책임 분리]
  - LCD (이 UI): 고객 인터페이스 + 페이지/이벤트 보고
  - Backend     : AMR 제어 (domain_bridge), 고객 접근 감지, home pose 복귀

[페이지 흐름]
  Home ─┬─ Information ─(✕)─► Home
        └─ Search ─► SearchResult ─(상품 클릭)─► [안내 확인 팝업]
                                                  └─[안내 시작]─► Guide
                                                                   └─[도착]─[완료 팝업+안내 종료]
                                                                                          └─► Home

[환경변수]
  PINKYPRO_ROBOT_ID         (기본 "sshopy2")   ← backend 와 동일
  MOOSINSA_SERVICE_HOST     (기본 "localhost")
  MOOSINSA_SERVICE_PORT     (기본 "8000")
"""

import sys

from dotenv import load_dotenv

load_dotenv()

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QStackedWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer

from sshopylcd_common import (
    REF_W, REF_H, C_BG, C_DARK, C_FOREST, C_FOREST_T, C_SUB, C_SUB_DARK,
    SVG_USER, SVG_SEARCH, make_svg, TopBar, ConfirmOverlay
)
from sshopylcd_api_client import SshopyLcdApiClient, normalize_search_results
from sshopylcd_information import SshopyLcdInformationPage
from sshopylcd_search import SshopyLcdSearchPage
from sshopylcd_search_result import SshopyLcdSearchResultPage
from sshopylcd_guide import SshopyLcdGuidePage


# ── Press overlay colors (kiosk_ui 와 동일) ──────────────────
PRESS_COLOR = {
    C_BG:     "#D4CFC8",
    C_DARK:   "#383838",
    C_FOREST: "#3E5444",
}


# ══════════════════════════════════════════════════════════════
# MenuTile
# ══════════════════════════════════════════════════════════════
class MenuTile(QFrame):
    """
    홈 화면용 큰 타일. 터치 시 배경색 일시 변경으로 피드백.
    """
    def __init__(
        self,
        bg_color: str,
        icon_svg: str,
        icon_color: str,
        sub_text: str,
        sub_color: str,
        main_text: str,
        main_color: str,
        on_click=None,
        border: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._bg_color = bg_color
        self._press_color = PRESS_COLOR.get(bg_color, bg_color)
        self._border = border
        self._sub_color = sub_color
        self._main_color = main_color
        self._radius = 12
        self._is_pressed = False
        self._on_click = on_click or (lambda: None)

        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._restore_bg)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._outer = QVBoxLayout(self)
        self._outer.addStretch(1)

        self._icon_svg = make_svg(icon_svg, icon_color, 28)
        self._outer.addWidget(self._icon_svg, alignment=Qt.AlignCenter)

        self._icon_spacer = QWidget()
        self._icon_spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._outer.addWidget(self._icon_spacer)

        self._sub = QLabel(sub_text)
        self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._outer.addWidget(self._sub)

        self._mid_spacer = QWidget()
        self._mid_spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._outer.addWidget(self._mid_spacer)

        self._main = QLabel(main_text)
        self._main.setWordWrap(True)
        self._main.setAlignment(Qt.AlignCenter)
        self._main.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._outer.addWidget(self._main)

        self._outer.addStretch(1)

    def mousePressEvent(self, event):
        self._is_pressed = True
        self._restore_timer.stop()
        self._apply_bg(self._press_color)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_pressed:
            self._is_pressed = False
            self._restore_timer.start(120)
            if self.rect().contains(event.position().toPoint()):
                self._on_click()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_pressed and not self.rect().contains(event.position().toPoint()):
            self._is_pressed = False
            self._restore_timer.stop()
            self._restore_bg()
        super().mouseMoveEvent(event)

    def _apply_bg(self, color: str):
        border_style = (
            "border: 1.5px solid rgba(0,0,0,0.18);" if self._border else "border: none;"
        )
        self.setStyleSheet(
            f"MenuTile {{background-color:{color};"
            f"border-radius:{self._radius}px;{border_style}}}"
        )

    def _restore_bg(self):
        self._apply_bg(self._bg_color)

    def apply_scale(self, s: float):
        m = max(round(14 * s), 6)
        self._outer.setContentsMargins(m, 0, m, 0)
        self._outer.setSpacing(0)

        icon_sz = max(round(36 * s), 22)
        self._icon_svg.setFixedSize(icon_sz, icon_sz)
        self._icon_spacer.setFixedHeight(max(round(8 * s), 3))
        self._mid_spacer.setFixedHeight(max(round(4 * s), 2))

        self._sub.setStyleSheet(
            f"color:{self._sub_color};font-size:{max(round(8 * s), 7)}px;"
            f"font-family:'Helvetica Neue',Arial,sans-serif;font-weight:300;"
            f"letter-spacing:{max(round(1 * s), 1)}px;"
            f"background:transparent;border:none;"
        )
        self._main.setStyleSheet(
            f"color:{self._main_color};font-size:{max(round(18 * s), 12)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:600;letter-spacing:{max(round(1 * s), 1)}px;"
            f"background:transparent;border:none;"
        )

        self._radius = max(round(12 * s), 6)
        self._restore_bg()


# ══════════════════════════════════════════════════════════════
# HomePage (이용안내 + 신발찾기 두 타일)
# ══════════════════════════════════════════════════════════════
class SshopyLcdHomePage(QWidget):
    """
    홈 화면. 320x240 에서 2 타일을 좌우로 배치.
    """
    def __init__(self, on_information=None, on_search=None):
        super().__init__()
        _on_information = on_information or (lambda: None)
        _on_search = on_search or (lambda: None)

        self.setStyleSheet(f"background-color:{C_BG};")

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # 홈에서는 좌/우 버튼 모두 숨김 (브랜드만)
        self._topbar = TopBar(show_home=False, show_right=False)
        self._root.addWidget(self._topbar)

        self._body = QWidget()
        self._body.setStyleSheet(f"background-color:{C_BG};")
        self._body_lo = QVBoxLayout(self._body)
        self._body_lo.setAlignment(Qt.AlignCenter)

        self._grid = QGridLayout()
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)
        self._grid.setRowStretch(0, 1)

        # 좌: 이용 안내 (forest) / 우: 신발 찾기 (dark)
        self._info_tile = MenuTile(
            bg_color=C_FOREST, icon_svg=SVG_USER, icon_color=C_FOREST_T,
            sub_text="GUIDE", sub_color=C_SUB_DARK,
            main_text="이용\n안내", main_color=C_FOREST_T,
            on_click=_on_information, border=False,
        )
        self._search_tile = MenuTile(
            bg_color=C_DARK, icon_svg=SVG_SEARCH, icon_color=C_BG,
            sub_text="SEARCH", sub_color=C_SUB_DARK,
            main_text="신발\n찾기", main_color=C_BG,
            on_click=_on_search, border=False,
        )
        self._grid.addWidget(self._info_tile, 0, 0)
        self._grid.addWidget(self._search_tile, 0, 1)

        self._body_lo.addLayout(self._grid)
        self._root.addWidget(self._body, stretch=1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scale()

    def _apply_scale(self):
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._topbar.apply_scale(s)
        margin = max(round(12 * s), 4)
        self._body_lo.setContentsMargins(margin, margin, margin, margin)
        self._grid.setSpacing(max(round(8 * s), 3))
        self._info_tile.apply_scale(s)
        self._search_tile.apply_scale(s)


# ══════════════════════════════════════════════════════════════
# PageManager
# ══════════════════════════════════════════════════════════════
class PageManager:
    """
    QStackedWidget 기반 페이지 전환 매니저.

    페이지 식별자:
      "home" | "information" | "search" | "search_result" | "guide"

    페이지 전환 시 backend 에 page_event 를 fire-and-forget 으로 보고.
    """
    HOME = "home"
    INFORMATION = "information"
    SEARCH = "search"
    SEARCH_RESULT = "search_result"
    GUIDE = "guide"

    def __init__(self):
        self._api = SshopyLcdApiClient()
        self._cur_page: str | None = None
        self._search_query: str = ""

        self._window = QMainWindow()
        self._window.setWindowTitle(f"MOOSINSA SShopy LCD ({self._api.robot_id})")
        # 320x240 기본 (창 크기 변경 시 비율 유지)
        self._window.resize(REF_W, REF_H)

        self._stack = QStackedWidget()
        self._window.setCentralWidget(self._stack)

        # ── 페이지 인스턴스 ─────────────────────────────────
        self._home_page = SshopyLcdHomePage(
            on_information=lambda: self._go(self.INFORMATION),
            on_search=lambda: self._go(self.SEARCH),
        )
        self._info_page = SshopyLcdInformationPage(
            on_home=lambda: self._go(self.HOME),
            on_close=lambda: self._go(self.HOME),
        )
        self._search_page = SshopyLcdSearchPage(
            on_home=lambda: self._go(self.HOME),
            on_back=lambda: self._go(self.HOME),
            on_search=lambda q: self._go_search_result(q),
        )
        self._search_result_page = SshopyLcdSearchResultPage(
            on_home=lambda: self._go(self.HOME),
            on_back=lambda: self._go(self.SEARCH),
            on_retry_search=lambda: self._go(self.SEARCH),
            on_product_click=lambda p: self._ask_start_guide(p),
        )
        self._guide_page = SshopyLcdGuidePage(
            api_client=self._api,
            on_end=lambda: self._go(self.HOME),
        )

        self._pages: dict[str, QWidget] = {
            self.HOME:          self._home_page,
            self.INFORMATION:   self._info_page,
            self.SEARCH:        self._search_page,
            self.SEARCH_RESULT: self._search_result_page,
            self.GUIDE:         self._guide_page,
        }
        for page in self._pages.values():
            self._stack.addWidget(page)

        # 상품 선택 확인 팝업 — search_result 페이지 위에 띄움
        self._confirm_overlay = ConfirmOverlay(self._search_result_page)

    def start(self):
        self._window.show()
        self._go(self.HOME)

    # ── 페이지 전환 ──────────────────────────────────────────
    def _go(self, target: str):
        prev = self._cur_page

        # search 페이지에서 다른 곳으로 가면 입력 초기화
        if prev == self.SEARCH and target != self.SEARCH:
            self._search_page.reset()

        # [sshopylcd연동] guide 페이지를 떠날 때 polling 안전 정지.
        # 정상 종료 흐름에서는 _finish_guide 가 이미 stop 했으므로 idempotent.
        if prev == self.GUIDE and target != self.GUIDE:
            self._guide_page.stop_polling()

        self._stack.setCurrentWidget(self._pages[target])
        self._cur_page = target
        self._api.send_page_event(page=target, prev=prev)

    def _go_search_result(self, query: str):
        """
        검색 실행 → 즉시 결과 페이지로 (로딩 상태) → 응답 도착 시 결과 주입.
        검색 누적 태그는 미사용 (단발 검색).
        """
        self._search_query = query
        self._search_result_page.update_results(query=query, results=None)
        self._go(self.SEARCH_RESULT)

        def _on_result(data):
            # 사용자가 이미 다른 검색어로 갱신했으면 stale 응답 폐기
            if query != self._search_query:
                return
            results = normalize_search_results(data) if data else []
            self._search_result_page.update_results(query=query, results=results)

        self._api.search(
            keyword=query,
            accumulated_tags={},
            callback=_on_result,
        )

    def _ask_start_guide(self, product: dict):
        """
        상품 클릭 시 안내 시작 여부 확인 팝업.
        '안내 시작' 누르면 Guide 페이지로 전환.
        """
        name = (product or {}).get("name", "선택하신 상품")
        msg = f"'{name}' 위치로\n안내드릴까요?"
        self._confirm_overlay.setup(
            message=msg,
            primary_text="안내 시작",
            secondary_text="취소",
            on_primary=lambda: self._start_guide(product),
            on_secondary=lambda: None,
        )
        self._confirm_overlay.show_over(self._search_result_page)

    def _start_guide(self, product: dict):
        """Guide 페이지에 product 주입 후 전환. guide/start 호출은 페이지 내부에서."""
        self._guide_page.start(product)
        self._go(self.GUIDE)


# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    manager = PageManager()
    manager.start()

    sys.exit(app.exec())
