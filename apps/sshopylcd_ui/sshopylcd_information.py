"""
sshopylcd_information.py
=========================
SShopy LCD '이용안내' 페이지.

[표시 내용 — kiosk_ui/kiosk_information.py 와 동일]
  - 이용방법 4단계
  - 영업시간
  - 휴무일
  - 고객 문의

320x240 해상도에 맞춰 스크롤 영역으로 구성.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QFrame, QScrollArea
)
from PySide6.QtCore import Qt

from sshopylcd_common import (
    REF_W, REF_H, C_BG, C_DARK, C_FOREST, C_BORDER, C_SUB, TopBar
)


class _SectionTitle(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)

    def apply_scale(self, s):
        self.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(15 * s), 11)}px;"
            f"font-family:'Georgia',serif;font-weight:600;background:transparent;"
        )


class _StepRow(QLabel):
    def __init__(self, number: str, text: str, parent=None):
        super().__init__(parent)
        self._number = number
        self._text = text
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)

    def apply_scale(self, s):
        fs = max(round(11 * s), 9)
        self.setStyleSheet(
            f"color:{C_DARK};font-size:{fs}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;background:transparent;"
        )
        self.setText(
            f'<span style="color:{C_FOREST};font-family:Georgia,serif;font-weight:600;">'
            f'{self._number}</span>{self._text}'
        )


class _InfoRow(QLabel):
    _ICON_MAP = {"phone": "📞", "mail": "✉", "insta": "📷"}

    def __init__(self, icon_key: str, text: str, parent=None):
        super().__init__(parent)
        self._icon_key = icon_key
        self._text = text
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)

    def apply_scale(self, s):
        fs = max(round(11 * s), 9)
        icon = self._ICON_MAP.get(self._icon_key, "")
        self.setStyleSheet(
            f"color:{C_DARK};font-size:{fs}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;background:transparent;"
        )
        self.setText(f"{icon}  {self._text}")


class _TextRow(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)

    def apply_scale(self, s):
        self.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(11 * s), 9)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;background:transparent;"
        )


class _Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)

    def apply_scale(self, s):
        self.setFixedHeight(max(round(1 * s), 1))
        self.setStyleSheet(f"background:{C_BORDER};color:{C_BORDER};border:none;")


# ════════════════════════════════════════════════════════════
# InformationPage
# ════════════════════════════════════════════════════════════
class SshopyLcdInformationPage(QWidget):
    def __init__(self, on_home=None, on_close=None):
        super().__init__()
        self._on_home = on_home or (lambda: None)
        self._on_close = on_close or (lambda: None)
        self._s = 0.5

        self.setStyleSheet(f"background:{C_BG};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._topbar = TopBar(
            on_home=self._on_home,
            on_right=self._on_close,
            right_kind="close",
        )
        root.addWidget(self._topbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{C_BG};}}"
            f"QScrollBar:vertical{{width:0px;}}"
        )
        root.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet(f"background:{C_BG};")
        self._content_lo = QVBoxLayout(content)
        # [텍스트잘림수정] AlignHCenter 를 걸면 자식 QLabel 이 sizeHint 너비만 받아
        # setWordWrap(True) 가 동작하지 못해 한 줄로 늘어나며 화면 우측이 잘림.
        # 레이아웃 정렬은 기본값(전체 너비 stretch)으로 두고, 텍스트 자체는
        # 각 QLabel 의 setAlignment(Qt.AlignCenter) 로 가운데 정렬한다.
        scroll.setWidget(content)

        self._scalables: list = []

        def add(w):
            self._content_lo.addWidget(w)
            self._scalables.append(w)
            return w

        def gap(ref_px: int):
            sp = QWidget()
            sp.setStyleSheet("background:transparent;")
            sp._ref = ref_px
            self._content_lo.addWidget(sp)
            self._scalables.append(sp)

        # ── 이용방법 ──────────────────────────────────────────
        gap(12)
        add(_SectionTitle("이용방법"))
        gap(8)
        for num, txt in [
            ("①", "  진열대의 QR을 스마트폰으로 인식, 또는 키오스크에서 상품 찾기"),
            ("②", "  시착 요청 후 선택한 좌석에서 로봇 배달 대기"),
            ("③", "  상품 수령 및 '수령 완료' 누른 후 시착"),
            ("④", "  좌석 옆 키오스크로 구매 결제, 또는 회수함에 반납"),
        ]:
            add(_StepRow(num, txt))
            gap(6)

        gap(8)
        add(_Divider())
        gap(8)

        # ── 영업시간 ──────────────────────────────────────────
        add(_SectionTitle("영업시간"))
        gap(6)
        for txt in ["평일  10:00 – 21:00", "주말  10:00 – 22:00"]:
            add(_TextRow(txt))
            gap(3)

        gap(8)

        # ── 휴무일 ───────────────────────────────────────────
        add(_SectionTitle("휴무일"))
        gap(6)
        for txt in ["매월 첫째 월요일 정기 휴무", "공휴일 정상 영업"]:
            add(_TextRow(txt))
            gap(3)

        gap(8)

        # ── 고객 문의 ─────────────────────────────────────────
        add(_SectionTitle("고객 문의"))
        gap(6)
        for icon_key, txt in [
            ("phone", "02-1234-5678"),
            ("mail",  "moosinsa@store.com"),
            ("insta", "@MoosinsaStore"),
        ]:
            add(_InfoRow(icon_key, txt))
            gap(3)

        gap(16)
        self._content_lo.addStretch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        # [텍스트잘림수정] 좌우 마진을 스케일로 부여 — 화면 끝에 텍스트가 붙어
        # 가독성이 떨어지는 것을 방지. 마진 안쪽에서 QLabel 의 wordWrap 이 정상 동작.
        hm = max(round(10 * s), 4)
        self._content_lo.setContentsMargins(hm, 0, hm, 0)
        self._content_lo.setSpacing(0)
        for w in self._scalables:
            if isinstance(w, (_SectionTitle, _TextRow, _StepRow, _InfoRow, _Divider)):
                w.apply_scale(s)
            elif hasattr(w, "_ref"):
                w.setFixedHeight(max(round(w._ref * s), 2))
