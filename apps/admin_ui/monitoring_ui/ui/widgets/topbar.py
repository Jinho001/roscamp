"""
ui/widgets/topbar.py
Shared top bar: title + tab switcher + logout + emergency stop
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QTabBar,
    QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class TopBar(QWidget):
    tab_changed      = Signal(int)   # 0 = 관제시스템, 1 = 매장관리
    logout_clicked   = Signal()
    emergency_clicked= Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # [white-theme] 상단바 — 밝은 회색 배경 + 진한 하단 보더
        # [반응형] setFixedHeight 는 apply_scale 에서 동적으로 변경
        self.setStyleSheet(
            "background:#ffffff; border-bottom:1px solid #d0d7de;"
        )
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(20, 0, 20, 0)
        self._lay.setSpacing(0)

        # ── Brand ─────────────────────────────────────────────────────────
        # [white-theme] 브랜드 — 거의 검정 텍스트
        # [반응형] 폰트/letter-spacing 은 apply_scale 에서 갱신
        self._brand = QLabel("MOOSINSA  ·  ADMIN SYSTEM")
        self._lay.addWidget(self._brand)

        # [반응형] 고정 spacer → setFixedWidth 로 스케일 가능하게
        self._brand_spacer = QWidget()
        self._brand_spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._lay.addWidget(self._brand_spacer)

        # ── Fake tab buttons ──────────────────────────────────────────────
        # [반응형] 탭 스타일은 active 여부 + 스케일 모두에 의존하므로
        # apply_scale 에서 _tab_style(active, s) 를 호출해 재적용한다.
        self._tabs = []
        self._tab_active_idx = 0
        for i, label in enumerate(["관제시스템", "매장관리"]):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, idx=i: self._select_tab(idx))
            self._lay.addWidget(btn)
            self._tabs.append(btn)

        self._lay.addStretch()

        # ── Logout ────────────────────────────────────────────────────────
        # [반응형] 버튼 setFixedHeight 는 apply_scale 에서 갱신
        self._btn_logout = QPushButton("로그아웃")
        self._btn_logout.setObjectName("btnLogout")
        self._btn_logout.clicked.connect(self.logout_clicked)
        self._lay.addWidget(self._btn_logout)

        # [반응형] 고정 spacer
        self._btn_spacer = QWidget()
        self._btn_spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._lay.addWidget(self._btn_spacer)

        # ── Emergency ─────────────────────────────────────────────────────
        self._btn_em = QPushButton("비상정지")
        self._btn_em.setObjectName("btnEmergency")
        self._btn_em.clicked.connect(self.emergency_clicked)
        self._lay.addWidget(self._btn_em)

        # 초기 탭 선택 + 기본 스케일 적용
        self._scale = 1.0
        self.apply_scale(1.0)
        self._select_tab(0)

    # ── helpers ───────────────────────────────────────────────────────────

    # [white-theme] 탭 스타일 — 활성: 진한 텍스트 + 파랑 밑줄, 비활성: 회색 텍스트
    # [반응형] 폰트/패딩/letter-spacing 을 스케일에 비례시킴
    @staticmethod
    def _tab_style(active: bool, s: float) -> str:
        font_px = max(round(12 * s), 9)
        pad_v   = max(round(8 * s), 4)
        pad_h   = max(round(20 * s), 8)
        ls      = max(round(1 * s), 1)
        if active:
            return (
                f"QPushButton {{ background:transparent; color:#0a0c10;"
                f"border:none; border-bottom:2px solid #0969da;"
                f"padding:{pad_v}px {pad_h}px; font-family:'Courier New',monospace;"
                f"font-size:{font_px}px; letter-spacing:{ls}px; font-weight:bold; }}"
            )
        return (
            f"QPushButton {{ background:transparent; color:#57606a;"
            f"border:none; border-bottom:2px solid transparent;"
            f"padding:{pad_v}px {pad_h}px; font-family:'Courier New',monospace;"
            f"font-size:{font_px}px; letter-spacing:{ls}px; }}"
            f"QPushButton:hover {{ color:#1f2328; }}"
        )

    def _select_tab(self, idx: int):
        self._tab_active_idx = idx
        for i, btn in enumerate(self._tabs):
            btn.setStyleSheet(self._tab_style(i == idx, self._scale))
            btn.setChecked(i == idx)
        self.tab_changed.emit(idx)

    def set_tab(self, idx: int):
        self._select_tab(idx)

    # [반응형] 외부(MainWindow)에서 호출 — 모든 치수/폰트를 비례 갱신
    def apply_scale(self, s: float):
        self._scale = s

        # 상단바 자체 높이
        self.setFixedHeight(max(round(48 * s), 32))

        # 레이아웃 마진/스페이서
        m_h = max(round(20 * s), 8)
        self._lay.setContentsMargins(m_h, 0, m_h, 0)
        self._brand_spacer.setFixedWidth(max(round(24 * s), 8))
        self._btn_spacer.setFixedWidth(max(round(10 * s), 4))

        # 브랜드 라벨 폰트
        brand_px = max(round(13 * s), 10)
        brand_ls = max(round(2 * s), 1)
        self._brand.setStyleSheet(
            f"color:#0a0c10; font-family:'Courier New',monospace;"
            f"font-size:{brand_px}px; letter-spacing:{brand_ls}px; font-weight:bold;"
        )

        # 탭 버튼 — 현재 active 상태 유지하며 스타일만 재적용
        for i, btn in enumerate(self._tabs):
            btn.setStyleSheet(self._tab_style(i == self._tab_active_idx, s))

        # 로그아웃/비상정지 버튼 높이
        btn_h = max(round(32 * s), 22)
        self._btn_logout.setFixedHeight(btn_h)
        self._btn_em.setFixedHeight(btn_h)
