"""
sshopylcd_search_result.py
===========================
SShopy LCD 검색 결과 페이지.

[흐름]
  1. PageManager 가 검색 실행 후 update_results(query, None) 로 로딩 상태 진입
  2. /search 응답 도착 시 update_results(query, [...]) 로 결과 주입
  3. 상품 클릭 → on_product_click 콜백 → 안내 시작 확인 팝업 (PageManager 책임)

[이미지 로딩]
  비동기로 image_url 을 받아와 캐시. 실패해도 'IMG' 플레이스홀더 유지.
"""

import os
import threading
import urllib.request
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, QByteArray, QEvent, QObject, Signal, QTimer
from PySide6.QtGui import QPixmap

from sshopylcd_common import (
    REF_W, REF_H, C_BG, C_DARK, C_FOREST, C_BROWN, C_BROWN_H, C_BORDER, C_SUB,
    TopBar, make_svg
)

# [이미지URL해결] DB의 image_url 은 파일명만 저장돼 있어 메인 서버 경로로 prepend 필요
_SHOE_BASE_URL = "http://{}:{}".format(
    os.environ.get("MOOSINSA_SERVICE_HOST", "localhost"),
    os.environ.get("MOOSINSA_SERVICE_PORT", "8000"),
)

SVG_SEARCH_AGAIN = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round"
  xmlns="http://www.w3.org/2000/svg">
  <circle cx="14" cy="14" r="8"/>
  <line x1="20" y1="20" x2="27" y2="27"/>
</svg>"""

SVG_ARROW_RIGHT = """<svg viewBox="0 0 20 20" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M5 10h10M11 6l4 4-4 4"/>
</svg>"""


# ════════════════════════════════════════════════════════════
#  비동기 이미지 로더 (kiosk_category_brand._load_image_async 와 동일 컨셉,
#  sshopylcd_ui 가 별도 기기라 자체 보유)
# ════════════════════════════════════════════════════════════
_img_cache: dict[str, bytes] = {}


class _ImageBridge(QObject):
    """워커 스레드 → 메인 스레드 이미지 데이터 전달."""
    received = Signal(object, bytes, int)


_img_bridge: Optional[_ImageBridge] = None


def _get_img_bridge() -> _ImageBridge:
    global _img_bridge
    if _img_bridge is None:
        _img_bridge = _ImageBridge()
        _img_bridge.received.connect(_apply_image)
    return _img_bridge


def _apply_image(label: QLabel, data: bytes, size: int):
    pix = QPixmap()
    pix.loadFromData(QByteArray(data))
    if not pix.isNull():
        pix = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.setPixmap(pix)


# [이미지URL해결] kiosk_category_brand._resolve_image_url 와 동일 규칙:
#   - 절대 URL(http/https) 은 그대로
#   - "/" 시작은 base URL prepend
#   - 그 외는 /shoes_images/{파일명}
def _resolve_image_url(image_url: str) -> str:
    if not image_url:
        return ""
    if image_url.startswith(("http://", "https://")):
        return image_url
    if image_url.startswith("/"):
        return f"{_SHOE_BASE_URL}{image_url}"
    return f"{_SHOE_BASE_URL}/shoes_images/{image_url}"


def _load_image_async(image_url: str, label: QLabel, size: int):
    url = _resolve_image_url(image_url)  # [이미지URL해결]
    if not url:
        return
    if url in _img_cache:
        data = _img_cache[url]
        QTimer.singleShot(0, lambda: _apply_image(label, data, size))
        return

    bridge = _get_img_bridge()

    def _worker():
        try:
            data = urllib.request.urlopen(url, timeout=5).read()
            _img_cache[url] = data
            bridge.received.emit(label, data, size)
        except Exception as e:
            print(f"[sshopylcd_search_result] 이미지 로드 실패 ({url}): {e}")

    threading.Thread(target=_worker, daemon=True).start()


# ════════════════════════════════════════════════════════════
#  Touch scroll
# ════════════════════════════════════════════════════════════
class _TouchScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_y = None
        self._scroll_y0 = None
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.viewport():
            t = event.type()
            if t == QEvent.MouseButtonPress:
                self._drag_y = event.pos().y()
                self._scroll_y0 = self.verticalScrollBar().value()
                return True
            elif t == QEvent.MouseMove and self._drag_y is not None:
                self.verticalScrollBar().setValue(
                    self._scroll_y0 - (event.pos().y() - self._drag_y)
                )
                return True
            elif t == QEvent.MouseButtonRelease:
                self._drag_y = None
                return True
        return super().eventFilter(obj, event)


# ════════════════════════════════════════════════════════════
#  Product row
# ════════════════════════════════════════════════════════════
class _ProductRow(QFrame):
    def __init__(self, product: dict, s: float, on_click, parent=None):
        super().__init__(parent)
        self._product = product
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._build(product, s)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click(self._product)
        super().mousePressEvent(event)

    def _build(self, p: dict, s: float):
        outer = QHBoxLayout(self)
        pad_h = max(round(10 * s), 4)
        pad_v = max(round(6 * s), 3)
        outer.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        outer.setSpacing(max(round(8 * s), 4))

        # 순위
        rank_lbl = QLabel(str(p.get("rank", "")))
        rank_lbl.setFixedWidth(max(round(14 * s), 10))
        rank_lbl.setAlignment(Qt.AlignCenter)
        rank_lbl.setStyleSheet(
            f"color:{C_SUB};font-size:{max(round(10 * s), 8)}px;"
            f"font-family:'Georgia',serif;font-weight:400;background:transparent;"
        )
        outer.addWidget(rank_lbl, alignment=Qt.AlignVCenter)

        # 이미지
        img_sz = max(round(40 * s), 28)
        img_frame = QFrame()
        img_frame.setFixedSize(img_sz, img_sz)
        img_frame.setStyleSheet(
            f"QFrame{{background:{C_BG};border:1px solid {C_BORDER};"
            f"border-radius:{max(round(4 * s), 2)}px;}}"
        )
        img_lbl = QLabel("IMG", img_frame)
        img_lbl.setAlignment(Qt.AlignCenter)
        img_lbl.setGeometry(0, 0, img_sz, img_sz)
        img_lbl.setStyleSheet(
            f"color:{C_BORDER};font-size:{max(round(7 * s), 6)}px;"
            f"font-family:'Helvetica Neue',Arial;background:transparent;border:none;"
        )
        if p.get("image_url"):
            _load_image_async(p["image_url"], img_lbl, img_sz)
        outer.addWidget(img_frame, alignment=Qt.AlignVCenter)

        # 텍스트
        text_block = QWidget()
        text_block.setStyleSheet("background:transparent;")
        text_lo = QVBoxLayout(text_block)
        text_lo.setContentsMargins(0, 0, 0, 0)
        text_lo.setSpacing(max(round(2 * s), 1))

        if p.get("tag"):
            tag_lbl = QLabel(p["tag"])
            tag_lbl.setStyleSheet(
                f"color:{C_FOREST};font-size:{max(round(8 * s), 7)}px;"
                f"font-family:'Helvetica Neue',Arial;font-weight:400;background:transparent;"
            )
            text_lo.addWidget(tag_lbl)

        name_lbl = QLabel(p.get("name", ""))
        name_lbl.setWordWrap(True)
        name_lbl.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(11 * s), 9)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:500;background:transparent;"
        )
        text_lo.addWidget(name_lbl)

        brand_lbl = QLabel(p.get("brand", ""))
        brand_lbl.setStyleSheet(
            f"color:{C_SUB};font-size:{max(round(9 * s), 7)}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:300;background:transparent;"
        )
        text_lo.addWidget(brand_lbl)

        outer.addWidget(text_block, stretch=1, alignment=Qt.AlignVCenter)

        # 화살표
        arrow = make_svg(SVG_ARROW_RIGHT, C_BORDER, max(round(10 * s), 8))
        outer.addWidget(arrow, alignment=Qt.AlignVCenter)

        self.setObjectName("product_row")
        self.setStyleSheet(
            f"QFrame#product_row{{background:{C_BG};border:none;"
            f"border-bottom:1px solid {C_BORDER};}}"
            f"QFrame#product_row:hover{{background:#E8E3DC;}}"
        )


class _ResultList(QWidget):
    def __init__(self, results: list, s: float, on_click, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{C_BG};")
        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)
        for item in results:
            lo.addWidget(_ProductRow(item, s, on_click))
        lo.addStretch()


# ════════════════════════════════════════════════════════════
#  SearchResultPage
# ════════════════════════════════════════════════════════════
class SshopyLcdSearchResultPage(QWidget):
    def __init__(
        self,
        on_home=None,
        on_back=None,
        on_retry_search=None,
        on_product_click=None,
    ):
        super().__init__()
        self._query = ""
        self._results = None  # None=로딩, []=결과 없음
        self._on_home = on_home or (lambda: None)
        self._on_back = on_back or (lambda: None)
        self._on_retry = on_retry_search or (lambda: None)
        self._on_product = on_product_click or (lambda p: None)
        self._s = 0.5

        self.setStyleSheet(f"background:{C_BG};")

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._topbar = TopBar(
            on_home=self._on_home,
            on_right=self._on_back,
            right_kind="back",
        )
        self._root.addWidget(self._topbar)

        # 안내 영역
        self._info_frame = QFrame()
        self._info_frame.setStyleSheet(
            f"QFrame{{background:{C_BG};border-bottom:1px solid {C_BORDER};}}"
        )
        self._info_lo = QVBoxLayout(self._info_frame)
        self._query_lbl = QLabel("")
        self._query_lbl.setAlignment(Qt.AlignLeft)
        self._desc_lbl = QLabel("")
        self._desc_lbl.setAlignment(Qt.AlignLeft)
        self._desc_lbl.setWordWrap(True)
        self._info_lo.addWidget(self._query_lbl)
        self._info_lo.addWidget(self._desc_lbl)
        self._root.addWidget(self._info_frame)

        # 결과 스크롤
        self._scroll = _TouchScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{C_BG};}}"
            f"QScrollBar:vertical{{width:0px;background:transparent;}}"
        )
        self._root.addWidget(self._scroll, stretch=1)
        self._load_results()

        # 다시 검색 버튼
        self._retry_btn = QPushButton()
        self._retry_btn.setCursor(Qt.PointingHandCursor)
        self._retry_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._retry_btn.clicked.connect(self._retry_search)
        self._retry_icon = make_svg(SVG_SEARCH_AGAIN, C_BG, 12)
        self._retry_lbl = QLabel("다시 검색하기")
        retry_lo = QHBoxLayout(self._retry_btn)
        retry_lo.setContentsMargins(0, 0, 0, 0)
        retry_lo.setSpacing(max(round(6 * self._s), 3))
        retry_lo.addStretch()
        retry_lo.addWidget(self._retry_icon)
        retry_lo.addWidget(self._retry_lbl)
        retry_lo.addStretch()
        self._retry_lo = retry_lo
        self._root.addWidget(self._retry_btn)

    def update_results(self, query: str, results):
        self._query = query
        self._results = results
        self._load_results()
        self._apply_scale(self._s)

    def _retry_search(self):
        self._on_retry()

    def _make_msg_widget(self, msg: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background:{C_BG};")
        lo = QVBoxLayout(w)
        lo.setAlignment(Qt.AlignCenter)
        lbl = QLabel(msg)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"color:{C_SUB};font-size:{max(round(12 * self._s), 9)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;background:transparent;"
        )
        lo.addWidget(lbl)
        return w

    def _load_results(self):
        if self._results is None:
            widget = self._make_msg_widget("검색 중...")
        elif not self._results:
            widget = self._make_msg_widget("검색 결과가 없습니다.")
        else:
            widget = _ResultList(self._results, self._s, self._on_product)
        self._scroll.setWidget(widget)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_scale(s)
        self._load_results()

    def _apply_scale(self, s: float):
        hm = max(round(16 * s), 6)
        vm = max(round(8 * s), 4)
        self._info_lo.setContentsMargins(hm, vm, hm, vm)
        self._info_lo.setSpacing(max(round(4 * s), 2))

        q_text = f"'{self._query}' 검색 결과" if self._query else "검색 결과"
        self._query_lbl.setText(q_text)
        self._query_lbl.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(13 * s), 10)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:600;background:transparent;"
        )

        if self._results is None:
            desc_text = "잠시만 기다려 주세요."
        elif not self._results:
            desc_text = "다른 검색어로 다시 시도해 보세요."
        else:
            desc_text = "상품을 선택하시면 해당 위치로 안내해 드립니다."
        self._desc_lbl.setText(desc_text)
        self._desc_lbl.setStyleSheet(
            f"color:{C_SUB};font-size:{max(round(10 * s), 8)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;background:transparent;"
        )

        btn_h = max(round(34 * s), 24)
        fs_btn = max(round(12 * s), 9)
        icon_sz = max(round(12 * s), 9)
        self._retry_btn.setFixedHeight(btn_h)
        self._retry_icon.setFixedSize(icon_sz, icon_sz)
        self._retry_lbl.setStyleSheet(
            f"color:{C_BG};font-size:{fs_btn}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:400;background:transparent;"
        )
        self._retry_lo.setSpacing(max(round(6 * s), 3))
        self._retry_btn.setStyleSheet(
            f"QPushButton{{background:{C_BROWN};border:none;}}"
            f"QPushButton:hover{{background:{C_BROWN_H};}}"
        )
