"""
sshopylcd_common.py
====================
SShopy AMR 부착 320x240 LCD UI 공통 모듈.

[기준 해상도]
  REF_W = 320, REF_H = 240
  실제 창 크기에 따라 min(w/REF_W, h/REF_H) 로 스케일 계산하여 비율 유지.

[공통 컴포넌트]
  - 색상 팔레트 / SVG 템플릿
  - make_svg(): SVG → QSvgWidget 헬퍼
  - TopBar: kiosk_ui와 동일한 다크 바(브랜드 가운데)
  - ConfirmOverlay: 페이지 위에 띄우는 모달 카드 (QMessageBox 대체)
"""

from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout
)
from PySide6.QtCore import Qt, QByteArray
from PySide6.QtSvgWidgets import QSvgWidget

# ── Design reference resolution ──────────────────────────────
REF_W = 320
REF_H = 240

# ── Color palette ────────────────────────────────────────────
C_BG       = "#EDE9E3"
C_DARK     = "#1C1C1C"
C_FOREST   = "#2C3D30"
C_FOREST_T = "#C8DDB8"
C_BROWN    = "#5C4A3A"
C_BROWN_H  = "#6E5A48"
C_BORDER   = "#D6D1C9"
C_SUB      = "#999999"
C_SUB_DARK = "rgba(255,255,255,0.38)"

# ── SVG templates ────────────────────────────────────────────
SVG_HOME = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M4 14L16 4l12 10"/>
  <path d="M6 12v14h7v-7h6v7h7V12"/>
</svg>"""

SVG_CLOSE = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round"
  xmlns="http://www.w3.org/2000/svg">
  <line x1="8" y1="8" x2="24" y2="24"/>
  <line x1="24" y1="8" x2="8" y2="24"/>
</svg>"""

SVG_BACK = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M20 6L8 16l12 10"/>
</svg>"""

SVG_SEARCH = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.5" stroke-linecap="round"
  xmlns="http://www.w3.org/2000/svg">
  <circle cx="14" cy="14" r="8"/>
  <line x1="20" y1="20" x2="27" y2="27"/>
</svg>"""

SVG_USER = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <circle cx="16" cy="10" r="5"/>
  <path d="M6 27c0-5.523 4.477-10 10-10s10 4.477 10 10"/>
</svg>"""


def make_svg(tpl: str, color: str, w: int, h: int = 0) -> QSvgWidget:
    """SVG 템플릿(`{color}`) → QSvgWidget. 배경 투명, 고정 크기."""
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background: transparent;")
    return wgt


# ══════════════════════════════════════════════════════════════
# TopBar
# ══════════════════════════════════════════════════════════════
class TopBar(QFrame):
    """
    상단바. kiosk_ui와 동일한 다크 배경 + 가운데 MOOSINSA 브랜드.

    좌측/우측 버튼은 옵션:
      - `on_home`: 좌측 home 아이콘 버튼 (None 이면 자리만 차지)
      - `on_right`: 우측 버튼 (None 이면 자리만 차지)
      - `right_kind`: "close" | "back" — 우측 아이콘 종류
      - `show_home`: 좌측 home 버튼 숨김 여부 (Home 페이지에서 사용)
      - `show_right`: 우측 버튼 숨김 여부 (안내 중 페이지에서 사용)
    """
    def __init__(
        self,
        on_home=None,
        on_right=None,
        right_kind: str = "close",
        show_home: bool = True,
        show_right: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C_DARK}; border: none;")

        self._lo = QHBoxLayout(self)

        # ── 좌측: home 버튼 ───────────────────────────────────
        self._home_btn = QPushButton()
        self._home_btn.setCursor(Qt.PointingHandCursor)
        self._home_btn.setStyleSheet("QPushButton{background:transparent;border:none;}")
        self._home_icon = make_svg(SVG_HOME, C_BG, 16)
        hl = QHBoxLayout(self._home_btn)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self._home_icon, alignment=Qt.AlignCenter)
        if on_home is not None:
            self._home_btn.clicked.connect(on_home)
        self._home_btn.setVisible(show_home)

        # ── 가운데: 브랜드 ────────────────────────────────────
        self._brand = QLabel("MOOSINSA")
        self._brand.setAlignment(Qt.AlignCenter)

        # ── 우측: close 또는 back ─────────────────────────────
        self._right_btn = QPushButton()
        self._right_btn.setCursor(Qt.PointingHandCursor)
        self._right_btn.setStyleSheet("QPushButton{background:transparent;border:none;}")
        right_svg = SVG_BACK if right_kind == "back" else SVG_CLOSE
        self._right_icon = make_svg(right_svg, C_BG, 14)
        rl = QHBoxLayout(self._right_btn)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self._right_icon, alignment=Qt.AlignCenter)
        if on_right is not None:
            self._right_btn.clicked.connect(on_right)
        self._right_btn.setVisible(show_right)

        self._lo.addWidget(self._home_btn)
        self._lo.addStretch()
        self._lo.addWidget(self._brand)
        self._lo.addStretch()
        self._lo.addWidget(self._right_btn)

    def apply_scale(self, s: float):
        self.setFixedHeight(max(round(32 * s), 22))
        hm = max(round(10 * s), 4)
        self._lo.setContentsMargins(hm, 0, hm, 0)

        icon_sz = max(round(16 * s), 12)
        btn_sz = max(round(26 * s), 18)
        self._home_icon.setFixedSize(icon_sz, icon_sz)
        self._home_btn.setFixedSize(btn_sz, btn_sz)
        right_icon_sz = max(round(14 * s), 10)
        self._right_icon.setFixedSize(right_icon_sz, right_icon_sz)
        self._right_btn.setFixedSize(btn_sz, btn_sz)

        self._brand.setStyleSheet(
            f"color:{C_BG};font-size:{max(round(13 * s), 10)}px;"
            f"font-family:'Georgia',serif;font-weight:500;"
            f"letter-spacing:{max(round(3 * s), 1)}px;background:transparent;"
        )


# ══════════════════════════════════════════════════════════════
# ConfirmOverlay
# ══════════════════════════════════════════════════════════════
class ConfirmOverlay(QFrame):
    """
    페이지 위에 띄우는 인라인 모달 카드.

    `setup(message, primary_text, secondary_text, on_primary, on_secondary)` 로
    내용을 갱신한 뒤 `show_over(parent)` 로 표시.

    secondary_text=None 이면 버튼 1개만 표시 (정보형).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.55);")
        self.hide()

        self._s = 0.5

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setAlignment(Qt.AlignCenter)

        self._card = QFrame()
        self._card.setObjectName("modal_card")
        outer.addWidget(self._card, alignment=Qt.AlignCenter)

        card_lo = QVBoxLayout(self._card)
        card_lo.setAlignment(Qt.AlignCenter)

        self._msg = QLabel("")
        self._msg.setAlignment(Qt.AlignCenter)
        self._msg.setWordWrap(True)
        card_lo.addWidget(self._msg)

        self._btn_row = QHBoxLayout()
        self._btn_row.setAlignment(Qt.AlignCenter)
        card_lo.addLayout(self._btn_row)
        self._card_lo = card_lo

        self._secondary_btn = QPushButton("")
        self._secondary_btn.setCursor(Qt.PointingHandCursor)
        self._primary_btn = QPushButton("")
        self._primary_btn.setCursor(Qt.PointingHandCursor)

        self._btn_row.addWidget(self._secondary_btn)
        self._btn_row.addWidget(self._primary_btn)

        self._on_primary = lambda: None
        self._on_secondary = lambda: None
        self._primary_btn.clicked.connect(lambda: self._fire("primary"))
        self._secondary_btn.clicked.connect(lambda: self._fire("secondary"))

    def setup(
        self,
        message: str,
        primary_text: str,
        secondary_text: str | None = None,
        on_primary=None,
        on_secondary=None,
    ):
        self._msg.setText(message)
        self._primary_btn.setText(primary_text)
        self._on_primary = on_primary or (lambda: None)

        if secondary_text is None:
            self._secondary_btn.setVisible(False)
            self._on_secondary = lambda: None
        else:
            self._secondary_btn.setText(secondary_text)
            self._secondary_btn.setVisible(True)
            self._on_secondary = on_secondary or (lambda: None)

    def show_over(self, parent: QWidget):
        """parent 페이지 위에 덮어씌워 표시. 부모 크기에 맞춰 자동 리사이즈."""
        self.setParent(parent)
        self.setGeometry(0, 0, parent.width(), parent.height())
        self.raise_()
        self.show()

    def _fire(self, which: str):
        if which == "primary":
            cb = self._on_primary
        else:
            cb = self._on_secondary
        self.hide()
        cb()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scale()

    def _apply_scale(self):
        if self.parent() is None:
            return
        pw, ph = self.parent().width(), self.parent().height()
        s = min(pw / REF_W, ph / REF_H)
        self._s = s

        card_w = max(round(260 * s), 180)
        card_h = max(round(160 * s), 110)
        radius = max(round(12 * s), 6)
        pad = max(round(16 * s), 8)
        spacing = max(round(12 * s), 6)

        self._card.setFixedSize(card_w, card_h)
        self._card.setStyleSheet(
            f"QFrame#modal_card{{background:{C_BG};border-radius:{radius}px;}}"
        )
        self._card_lo.setContentsMargins(pad, pad, pad, pad)
        self._card_lo.setSpacing(spacing)

        fs_msg = max(round(13 * s), 10)
        self._msg.setStyleSheet(
            f"color:{C_DARK};font-size:{fs_msg}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:400;line-height:1.5;background:transparent;"
        )

        btn_h = max(round(32 * s), 22)
        btn_radius = max(round(8 * s), 4)
        fs_btn = max(round(12 * s), 9)
        self._btn_row.setSpacing(max(round(8 * s), 4))

        self._primary_btn.setFixedHeight(btn_h)
        self._primary_btn.setMinimumWidth(max(round(90 * s), 60))
        self._primary_btn.setStyleSheet(
            f"QPushButton{{background:{C_FOREST};color:{C_BG};border:none;"
            f"border-radius:{btn_radius}px;font-size:{fs_btn}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:500;padding:0 {max(round(12 * s), 6)}px;}}"
            f"QPushButton:hover{{background:#3E5444;}}"
        )

        self._secondary_btn.setFixedHeight(btn_h)
        self._secondary_btn.setMinimumWidth(max(round(80 * s), 50))
        self._secondary_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C_DARK};"
            f"border:1.5px solid {C_BORDER};border-radius:{btn_radius}px;"
            f"font-size:{fs_btn}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:400;padding:0 {max(round(10 * s), 4)}px;}}"
            f"QPushButton:hover{{background:#E2DDD6;}}"
        )
