"""
sshopylcd_search.py
====================
SShopy LCD 검색 입력 페이지.

[레이아웃 — 320x240]
  TopBar (~28px) + Prompt (~16px) + Input row (~22px) + Keyboard (~165px)

  키보드 영역에 비중을 두고 입력창과 프롬프트는 압축한다.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QLineEdit, QSizePolicy
)
from PySide6.QtCore import Qt, QByteArray, QTimer
from PySide6.QtSvgWidgets import QSvgWidget

from sshopylcd_common import (
    REF_W, REF_H, C_BG, C_DARK, C_BORDER, TopBar, make_svg
)
from sshopylcd_hangul import HangulComposer, VirtualKeyboard, validate_query

SVG_ENTER = """<svg viewBox="0 0 28 20" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M24 4v6a2 2 0 01-2 2H5"/>
  <polyline points="9,7 4,12 9,17"/>
</svg>"""


class SshopyLcdSearchPage(QWidget):
    def __init__(self, on_home=None, on_back=None, on_search=None):
        super().__init__()
        self._on_home = on_home or (lambda: None)
        self._on_back = on_back or (lambda: None)
        self._on_search = on_search or (lambda q: None)
        self._s = 0.5
        self._composer = HangulComposer()

        self.setStyleSheet(f"background-color:{C_BG};")

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # ── Top bar (back) ───────────────────────────────────
        self._topbar = TopBar(
            on_home=self._on_home,
            on_right=self._on_back,
            right_kind="back",
        )
        self._root.addWidget(self._topbar)

        # ── 중앙 영역 ────────────────────────────────────────
        self._mid = QWidget()
        self._mid.setStyleSheet(f"background:{C_BG};")
        self._mid_lo = QVBoxLayout(self._mid)
        self._mid_lo.setContentsMargins(0, 0, 0, 0)
        self._mid_lo.setSpacing(0)

        self._mid_lo.addStretch(1)

        self._prompt = QLabel("찾으시는 신발 이름이나 브랜드를 입력해 주세요")
        self._prompt.setAlignment(Qt.AlignCenter)
        self._prompt.setWordWrap(True)
        self._mid_lo.addWidget(self._prompt)

        self._mid_lo.addSpacing(6)

        # ── Input row ────────────────────────────────────────
        self._input_row = QFrame()
        self._input_row.setStyleSheet("background:transparent;border:none;")
        self._input_lo = QHBoxLayout(self._input_row)
        self._input_lo.setContentsMargins(0, 0, 0, 0)
        self._input_lo.setSpacing(0)

        self._input = QLineEdit()
        self._input.setReadOnly(True)
        self._input.setPlaceholderText("검색어")

        self._enter_btn = QPushButton()
        self._enter_btn.setCursor(Qt.PointingHandCursor)
        self._enter_btn.clicked.connect(self._do_search)
        self._enter_lo = QHBoxLayout(self._enter_btn)
        self._enter_lo.setContentsMargins(0, 0, 0, 0)
        self._enter_lo.setSpacing(0)
        self._enter_icon = make_svg(SVG_ENTER, C_BG, 12, 9)
        self._enter_lbl = QLabel("입력")
        self._enter_lo.addStretch()
        self._enter_lo.addWidget(self._enter_icon)
        self._enter_lo.addSpacing(3)
        self._enter_lo.addWidget(self._enter_lbl)
        self._enter_lo.addStretch()

        self._input_lo.addWidget(self._input, stretch=1)
        self._input_lo.addWidget(self._enter_btn)
        self._mid_lo.addWidget(self._input_row)

        # ── 에러 메시지 ──────────────────────────────────────
        self._error_lbl = QLabel("")
        self._error_lbl.setAlignment(Qt.AlignCenter)
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setVisible(False)
        self._mid_lo.addWidget(self._error_lbl)

        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self._clear_error)

        self._mid_lo.addStretch(1)
        self._root.addWidget(self._mid, stretch=1)

        # ── Virtual keyboard ─────────────────────────────────
        self._keyboard = VirtualKeyboard(on_key=self._key_pressed)
        self._keyboard.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._root.addWidget(self._keyboard)

    # ── actions ───────────────────────────────────────────────
    def reset(self):
        """페이지 떠날 때 입력 상태 초기화."""
        self._composer.reset()
        self._input.clear()
        self._clear_error()

    def _key_pressed(self, key: str):
        if key == "⌫":
            self._composer.backspace()
        elif key == "한/영":
            pass
        else:
            self._composer.push(key)
        self._input.setText(self._composer.text())

    def _do_search(self):
        self._composer.flush()
        self._input.setText(self._composer.text())
        query = self._input.text().strip()

        error = validate_query(query)
        if error:
            self._show_error(error)
            return

        self._clear_error()
        self._on_search(query)

    def _show_error(self, message: str):
        s = self._s
        self._apply_input_style(s, error=True)
        fs_err = max(round(10 * s), 8)
        self._error_lbl.setStyleSheet(
            f"color:#C0392B;font-size:{fs_err}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:400;background:transparent;"
        )
        self._error_lbl.setText(message)
        self._error_lbl.setVisible(True)
        self._error_timer.stop()
        self._error_timer.start(1500)

    def _clear_error(self):
        self._error_lbl.setVisible(False)
        self._error_lbl.setText("")
        self._apply_input_style(self._s, error=False)

    # ── resize / scale ────────────────────────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_content_scale(s)
        self._keyboard.apply_scale(s)

    def _apply_content_scale(self, s: float):
        hm = max(round(14 * s), 6)
        self._mid_lo.setContentsMargins(hm, 0, hm, 0)

        fs_p = max(round(12 * s), 9)
        self._prompt.setStyleSheet(
            f"color:{C_DARK};font-size:{fs_p}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:400;background:transparent;"
        )

        self._apply_input_style(s, error=False)

    def _apply_input_style(self, s: float, error: bool):
        input_h = max(round(26 * s), 20)
        enter_w = max(round(50 * s), 36)
        fs_input = max(round(12 * s), 10)
        r = max(round(6 * s), 3)

        border_color = "#C0392B" if error else C_BORDER
        self._input.setFixedHeight(input_h)
        self._input.setStyleSheet(
            f"QLineEdit{{background:{C_BG};color:{C_DARK};"
            f"border:1.5px solid {border_color};border-right:none;"
            f"border-top-left-radius:{r}px;border-bottom-left-radius:{r}px;"
            f"font-size:{fs_input}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"padding:0px {max(round(8 * s), 4)}px;}}"
            f"QLineEdit:focus{{border:1.5px solid {border_color};border-right:none;}}"
        )

        self._enter_btn.setFixedSize(enter_w, input_h)
        fs_e = max(round(11 * s), 9)
        iw = max(round(12 * s), 8); ih = max(round(9 * s), 6)
        self._enter_icon.setFixedSize(iw, ih)
        self._enter_lbl.setStyleSheet(
            f"color:{C_BG};font-size:{fs_e}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:500;background:transparent;"
        )
        self._enter_btn.setStyleSheet(
            f"QPushButton{{background:{C_DARK};color:{C_BG};border:none;"
            f"border-top-right-radius:{r}px;border-bottom-right-radius:{r}px;}}"
            f"QPushButton:hover{{background:#2E2E2E;}}"
        )
