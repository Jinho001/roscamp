"""
ui/login_screen.py
Login screen — SCREEN 1
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class LoginScreen(QWidget):
    login_requested = Signal(str, str)   # (user_id, password)

    def __init__(self, parent=None):
        super().__init__(parent)
        # [반응형] 부모(MainWindow)가 apply_scale(s) 호출 시 갱신할 위젯/스페이서
        # 인스턴스 변수로 저장. 초기에는 1.0 스케일 기준 더미 값으로 둔다.
        self._scale = 1.0
        self._build_ui()
        self.apply_scale(1.0)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ──────────────────────────────────────────────────────
        # [white-theme] 상단바 흰 배경 + 진한 회색 텍스트
        # [반응형] 높이/폰트는 apply_scale 에서 갱신
        self._topbar = QFrame()
        self._topbar.setObjectName("card")
        self._topbar.setStyleSheet(
            "QFrame#card { background:#ffffff; border:none; border-bottom:1px solid #d0d7de; border-radius:0; }"
        )
        self._tb_lay = QHBoxLayout(self._topbar)
        self._tb_lay.setContentsMargins(20, 0, 20, 0)
        self._tb_brand = QLabel("MOOSINSA  ·  ADMIN SYSTEM")
        self._tb_lay.addWidget(self._tb_brand)
        self._tb_lay.addStretch()
        root.addWidget(self._topbar)

        # ── Center card ───────────────────────────────────────────────────
        center = QWidget()
        center.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        c_lay = QVBoxLayout(center)
        c_lay.setAlignment(Qt.AlignCenter)

        # [white-theme] 로그인 카드 — 부드러운 회색 배경 + 진한 테두리
        # [반응형] 카드 너비 + 내부 마진/스페이싱 apply_scale 에서 갱신
        self._card = QFrame()
        self._card.setObjectName("card")
        self._card.setStyleSheet(
            "QFrame#card { background:#f6f8fa; border:1px solid #d0d7de; border-radius:6px; }"
        )
        self._card_lay = QVBoxLayout(self._card)
        self._card_lay.setContentsMargins(36, 40, 36, 40)
        self._card_lay.setSpacing(0)

        # Title
        title_row = QHBoxLayout()
        title_row.setAlignment(Qt.AlignCenter)
        # [white-theme] 타이틀 3종 — 보조/강조/보조
        self._t1 = QLabel("ADMIN ")
        self._t2 = QLabel("CONSOLE")
        self._t3 = QLabel(" — MOOSINSA")
        title_row.addWidget(self._t1)
        title_row.addWidget(self._t2)
        title_row.addWidget(self._t3)
        self._card_lay.addLayout(title_row)

        # [반응형] addSpacing 대신 _spacer QWidget 사용 — apply_scale 에서 변경 가능
        self._sp_after_title = self._make_v_spacer()
        self._card_lay.addWidget(self._sp_after_title)

        # USER ID
        # [white-theme] 입력 라벨 — muted gray
        self._uid_lbl = QLabel("USER ID")
        self._card_lay.addWidget(self._uid_lbl)
        self._sp_after_uid_lbl = self._make_v_spacer()
        self._card_lay.addWidget(self._sp_after_uid_lbl)
        self.input_uid = QLineEdit()
        self.input_uid.setText("admin")
        self._card_lay.addWidget(self.input_uid)
        self._sp_after_uid = self._make_v_spacer()
        self._card_lay.addWidget(self._sp_after_uid)

        # PASSWORD
        # [white-theme] 패스워드 라벨 — muted gray
        self._pw_lbl = QLabel("PASSWORD")
        self._card_lay.addWidget(self._pw_lbl)
        self._sp_after_pw_lbl = self._make_v_spacer()
        self._card_lay.addWidget(self._sp_after_pw_lbl)
        self.input_pw = QLineEdit()
        self.input_pw.setEchoMode(QLineEdit.Password)
        self.input_pw.setText("password")
        self.input_pw.returnPressed.connect(self._on_login)
        self._card_lay.addWidget(self.input_pw)
        self._sp_after_pw = self._make_v_spacer()
        self._card_lay.addWidget(self._sp_after_pw)

        # LOGIN button
        self._btn_login = QPushButton("LOGIN")
        self._btn_login.setObjectName("btnLogin")
        self._btn_login.clicked.connect(self._on_login)
        self._card_lay.addWidget(self._btn_login)

        # Error label
        self.lbl_error = QLabel("")
        self.lbl_error.setAlignment(Qt.AlignCenter)
        self._sp_before_err = self._make_v_spacer()
        self._card_lay.addWidget(self._sp_before_err)
        self._card_lay.addWidget(self.lbl_error)

        c_lay.addWidget(self._card)
        root.addWidget(center)

    # [반응형] 수직 스페이서 헬퍼 — addSpacing(N) 대신 사용해 동적 높이 변경 가능
    @staticmethod
    def _make_v_spacer() -> QWidget:
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return w

    # [반응형] 외부(MainWindow)에서 호출 — 모든 치수/폰트를 비례 갱신
    def apply_scale(self, s: float):
        self._scale = s

        # 상단바
        self._topbar.setFixedHeight(max(round(48 * s), 32))
        m_h = max(round(20 * s), 8)
        self._tb_lay.setContentsMargins(m_h, 0, m_h, 0)
        brand_px = max(round(13 * s), 10)
        brand_ls = max(round(2 * s), 1)
        self._tb_brand.setStyleSheet(
            f"color:#0a0c10; font-family:'Courier New',monospace;"
            f"font-size:{brand_px}px; letter-spacing:{brand_ls}px; font-weight:bold;"
        )

        # 로그인 카드 너비 + 내부 마진
        self._card.setFixedWidth(max(round(380 * s), 260))
        cm_h = max(round(36 * s), 16)
        cm_v = max(round(40 * s), 18)
        self._card_lay.setContentsMargins(cm_h, cm_v, cm_h, cm_v)

        # 카드 내 스페이서 높이
        self._sp_after_title.setFixedHeight(max(round(36 * s), 14))
        self._sp_after_uid_lbl.setFixedHeight(max(round(6 * s), 3))
        self._sp_after_uid.setFixedHeight(max(round(20 * s), 8))
        self._sp_after_pw_lbl.setFixedHeight(max(round(6 * s), 3))
        self._sp_after_pw.setFixedHeight(max(round(28 * s), 10))
        self._sp_before_err.setFixedHeight(max(round(10 * s), 4))

        # 타이틀 3종 폰트
        title_px = max(round(15 * s), 11)
        title_ls = max(round(3 * s), 1)
        self._t1.setStyleSheet(
            f"color:#57606a; font-size:{title_px}px; font-family:'Courier New',monospace;"
            f"letter-spacing:{title_ls}px; background:transparent; border:none;"
        )
        self._t2.setStyleSheet(
            f"color:#0969da; font-size:{title_px}px; font-family:'Courier New',monospace;"
            f"letter-spacing:{title_ls}px; font-weight:bold; background:transparent; border:none;"
        )
        self._t3.setStyleSheet(
            f"color:#57606a; font-size:{title_px}px; font-family:'Courier New',monospace;"
            f"letter-spacing:{title_ls}px; background:transparent; border:none;"
        )

        # 입력 라벨
        lbl_px = max(round(10 * s), 8)
        lbl_ls = max(round(2 * s), 1)
        for lbl in (self._uid_lbl, self._pw_lbl):
            lbl.setStyleSheet(
                f"color:#8c959f; font-size:{lbl_px}px; letter-spacing:{lbl_ls}px;"
                f"font-family:'Courier New',monospace; background:transparent; border:none;"
            )

        # 입력 필드
        input_h = max(round(38 * s), 26)
        self.input_uid.setFixedHeight(input_h)
        self.input_pw.setFixedHeight(input_h)

        # 로그인 버튼
        self._btn_login.setFixedHeight(max(round(42 * s), 28))

        # 에러 라벨 폰트
        err_px = max(round(11 * s), 9)
        self.lbl_error.setStyleSheet(
            f"color:#cf222e; font-size:{err_px}px; font-family:'Courier New',monospace;"
            f"background:transparent; border:none;"
        )

    def _on_login(self):
        uid = self.input_uid.text().strip()
        pw  = self.input_pw.text()
        if not uid or not pw:
            self.lbl_error.setText("USER ID 또는 PASSWORD를 입력하세요.")
            return
        self.lbl_error.setText("")
        self.login_requested.emit(uid, pw)

    def show_error(self, msg: str):
        self.lbl_error.setText(msg)
