"""
ui/management_screen.py
SCREEN 3 — 매장관리 (STORE MANAGEMENT)
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QPushButton, QLineEdit,
    QHeaderView, QGridLayout, QComboBox, QSizePolicy,
    # [실로봇연동] STOP 결과 / 연결 실패 메시지박스용
    QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui  import QColor


# [실로봇연동] 시착 요청 status 색 — TRYON_STAGE_LABELS / 완료 / 실패 / 취소
STATUS_STYLE_REQ = {
    "완료":  "#1f883d",   # 초록
    "취소":  "#57606a",   # 회색
    # '실패: ...' 도 빨강 — 시작어 매칭은 _req_status_color() 가 처리
}

# [white-theme] 좌석 (테두리, 배경) — active: 파랑/연파랑, idle: 회색/흰색
SEAT_COLORS = {
    "active": ("#0969da", "#ddf4ff"),
    "idle":   ("#d0d7de", "#ffffff"),
}


# [white-theme] 카드 — 연한 회색 배경 + 보더
def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    f.setStyleSheet(
        "QFrame#card { background:#f6f8fa; border:1px solid #d0d7de; border-radius:6px; }"
    )
    return f


class SeatStatusWidget(QLabel):
    """[실로봇연동] 좌석 점유 상태 표시 전용 위젯 (이전 SeatButton 대체).

    - QLabel 기반 — 클릭 / hover / 포커스 동작 없음 (display-only).
    - kiosk_tryon 과 동일한 /kiosk/seat/status 폴링 결과를 받아 active/idle 갱신.
    """
    def __init__(self, seat_id: int, parent=None):
        super().__init__(str(seat_id), parent)
        self.seat_id = seat_id
        self.setAlignment(Qt.AlignCenter)
        # 클릭/탭 인터랙션 차단
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        # [반응형] 고정 크기/폰트는 apply_scale 에서 갱신
        self._scale = 1.0
        self._status = "idle"
        self.set_status("idle")
        self.apply_scale(1.0)

    # [반응형] 외부에서 호출 — 크기/폰트 비례 갱신
    def apply_scale(self, s: float):
        self._scale = s
        self.setFixedSize(max(round(80 * s), 50), max(round(50 * s), 32))
        self.set_status(self._status)

    # [실로봇연동] active=점유 (빨강) / idle=빈자리 (초록 톤). hover/click 효과 제거.
    def set_status(self, status: str):
        self._status = status
        s = self._scale
        font_px = max(round(18 * s), 12)
        if status == "active":
            border_c, bg_c, text_c = "#cf222e", "#ffebe9", "#cf222e"
        else:
            border_c, bg_c, text_c = "#1f883d", "#dafbe1", "#1f883d"
        self.setStyleSheet(
            f"QLabel {{ background:{bg_c}; color:{text_c};"
            f"border:2px solid {border_c}; border-radius:4px;"
            f"font-family:'Courier New',monospace; font-size:{font_px}px; font-weight:bold; }}"
        )


# [실로봇연동] 기존 SeatButton 이름 호환 별칭 — 다른 파일이 import 하는 경우 대비.
SeatButton = SeatStatusWidget


class ManagementScreen(QWidget):
    def __init__(self, api_client, parent=None):
        super().__init__(parent)
        self.api = api_client
        self._seat_btns: dict[int, SeatButton] = {}
        # [반응형] apply_scale 에서 일괄 갱신할 컬렉션
        self._section_labels: list[QLabel] = []
        # KPI 카드: label 키 → (lbl_title, lbl_val, color, layout)
        self.kpi_cards: dict = {}
        # [실로봇연동] 시착 요청 UI 시퀀스 ID 매핑 — 프로세스 시작 시 1 부터 누적, 종료 시 종료.
        # 같은 백엔드 task_id (TRY-####) 가 매 폴링마다 다시 와도 동일한 UI 번호 유지.
        self._ui_req_id_map: dict[str, int] = {}
        self._next_ui_req_id: int = 1
        # [페이지네이션] 재고 표 페이지 상태 — 매 폴링마다 전체 행 재생성 비용 절감.
        self._inv_page_size: int = 25
        self._inv_page: int = 0
        self._inv_filtered: list = []   # 현재 검색 필터 통과한 항목 (페이지 슬라이스 전 전체)
        self._scale = 1.0
        self._build_ui()
        self.apply_scale(1.0)

    # [반응형] 섹션 라벨 — apply_scale 에서 일괄 갱신할 수 있게 모은다
    def _section(self, title: str) -> QLabel:
        lbl = QLabel(title)
        self._section_labels.append(lbl)
        return lbl

    def _build_ui(self):
        self._root = QVBoxLayout(self)
        # [화면꽉채우기] 가장자리 흰 여백 제거 — 루트 마진 0
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(10)

        # [화면꽉채우기] SCREEN 3 라벨 삭제 — 상단 여백 제거

        # ── KPI bar ───────────────────────────────────────────────────────
        # [실로봇연동] 5개 KPI — 시작 요청 / 회수 대기 / 회수 완료 / 판매 완료 / 매출액.
        # 모든 값은 백엔드 fleet 의 in-memory 카운터 — 서버 재시작 시 0 으로 리셋.
        # 판매 완료 / 매출액은 보류 상태이므로 항상 '—' 로 표시.
        self._kpi_row = QHBoxLayout()
        self._kpi_row.setSpacing(10)
        # 라벨 → (data key, 색상, hold). hold=True 이면 무조건 '—'.
        kpi_defs = [
            ("시작 요청", "requests_today",      "#0969da", False),
            ("회수 대기", "retrieval_pending",   "#bf8700", False),
            ("회수 완료", "retrieval_completed", "#1f883d", False),
            ("판매 완료", "sales_completed",     "#1f883d", True),
            ("매출액",    "revenue",             "#0a0c10", True),
        ]
        # [반응형] KPI 카드 레이아웃 핸들도 보존
        self._kpi_lays: list[QVBoxLayout] = []
        for label, key, color, hold in kpi_defs:
            card = _card()
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(16, 14, 16, 14)
            card_lay.setSpacing(4)
            self._kpi_lays.append(card_lay)
            lbl_title = QLabel(label)
            # 초기값 — '—' (데이터 도착 전)
            lbl_val = QLabel("—")
            card_lay.addWidget(lbl_title)
            card_lay.addWidget(lbl_val)
            self._kpi_row.addWidget(card, 1)
            # 4번째 튜플 요소: hold 플래그 (보류 KPI)
            self.kpi_cards[label] = (lbl_title, lbl_val, color, key, hold)

        # [실로봇연동] 서브 라벨 — '일별' 표현 제거 (서버 재시작까지 누적이라 '일' 단위 아님)
        self._sub_lbl = QLabel("누계 현황 · 서버 시작 후")
        # [실로봇연동] 우측에 연결 상태 배너 — 서버 unreachable 시 표시
        sub_row = QHBoxLayout()
        sub_row.addWidget(self._sub_lbl)
        sub_row.addStretch()
        self.lbl_dash_status = QLabel("")
        sub_row.addWidget(self.lbl_dash_status)
        self._root.addLayout(sub_row)
        self._root.addLayout(self._kpi_row)

        # ── Middle row ────────────────────────────────────────────────────
        self._mid_row = QHBoxLayout()
        self._mid_row.setSpacing(10)
        self._root.addLayout(self._mid_row, 1)

        # ── Inventory DB (left 55%) ────────────────────────────────────────
        inv_card = _card()
        self._inv_lay  = QVBoxLayout(inv_card)
        self._inv_lay.setContentsMargins(12, 12, 12, 12)
        self._inv_lay.setSpacing(8)

        # [실로봇연동] 헤더 — '재고 DB' 라벨 + 우측 'DB 연결 안됨' 배너
        inv_hdr = QHBoxLayout()
        inv_hdr.addWidget(self._section("재고 DB"))
        inv_hdr.addStretch()
        self.lbl_inv_status = QLabel("")
        inv_hdr.addWidget(self.lbl_inv_status)
        self._inv_lay.addLayout(inv_hdr)

        # Search row
        self._search_row = QHBoxLayout()
        self.input_search = QLineEdit()
        # [실로봇연동] 검색 형식: '상품명 사이즈' (스페이스 한 칸 구분).
        self.input_search.setPlaceholderText("상품명 사이즈  (예: Adidas Superstar 270)")
        self.input_search.textChanged.connect(self._filter_inventory)
        self._btn_search = QPushButton("검색")
        self._btn_search.setObjectName("btnSearch")
        self._btn_search.clicked.connect(
            lambda: self._filter_inventory(self.input_search.text())
        )
        self._search_row.addWidget(self.input_search)
        self._search_row.addWidget(self._btn_search)
        self._inv_lay.addLayout(self._search_row)

        # [실로봇연동] 검색 결과 안내 라벨 — '재고가 N개 있습니다' / '입고되지 않은 상품입니다'
        self.lbl_inv_msg = QLabel("")
        self._inv_lay.addWidget(self.lbl_inv_msg)

        self.tbl_inv = QTableWidget(0, 4)
        # [실로봇연동] 헤더 '랙' → '선반위치'
        self.tbl_inv.setHorizontalHeaderLabels(["상품명", "사이즈", "재고 ▲", "선반위치"])
        self.tbl_inv.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_inv.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_inv.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tbl_inv.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl_inv.verticalHeader().setVisible(False)
        self.tbl_inv.setShowGrid(False)
        self.tbl_inv.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_inv.setSelectionBehavior(QTableWidget.SelectRows)
        # [화면꽉채우기] 재고 표가 카드 내부에서 세로로 확장
        self.tbl_inv.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._inv_lay.addWidget(self.tbl_inv, 1)

        # [페이지네이션] 표 아래 페이지 네비게이션 행
        self._page_row = QHBoxLayout()
        self.btn_inv_prev = QPushButton("◀ 이전")
        self.btn_inv_next = QPushButton("다음 ▶")
        self.btn_inv_prev.setObjectName("btnSearch")   # 같은 톤 사용
        self.btn_inv_next.setObjectName("btnSearch")
        self.btn_inv_prev.clicked.connect(self._inv_prev_page)
        self.btn_inv_next.clicked.connect(self._inv_next_page)
        self.lbl_inv_page = QLabel("0 건")
        self.lbl_inv_page.setAlignment(Qt.AlignCenter)
        self.lbl_inv_page.setStyleSheet(
            "color:#57606a; font-family:'Courier New',monospace; font-size:11px;"
        )
        self._page_row.addWidget(self.btn_inv_prev)
        self._page_row.addWidget(self.lbl_inv_page, 1)
        self._page_row.addWidget(self.btn_inv_next)
        self._inv_lay.addLayout(self._page_row)

        self._mid_row.addWidget(inv_card, 55)

        # ── Right sub-column (45%) ────────────────────────────────────────
        self._right_col = QVBoxLayout()
        self._right_col.setSpacing(10)
        self._mid_row.addLayout(self._right_col, 45)

        # Seat grid card
        seat_card = _card()
        self._seat_lay  = QVBoxLayout(seat_card)
        self._seat_lay.setContentsMargins(12, 12, 12, 12)
        self._seat_lay.setSpacing(8)

        # [실로봇연동] 헤더 — 라벨(typo 수정) + 우측 cam/서버 연결 배너
        seat_hdr = QHBoxLayout()
        seat_hdr.addWidget(self._section("시착 좌석 현황"))
        seat_hdr.addStretch()
        self.lbl_seat_status = QLabel("")
        seat_hdr.addWidget(self.lbl_seat_status)
        self._seat_lay.addLayout(seat_hdr)

        self._grid = QGridLayout()
        self._grid.setSpacing(8)
        for seat_id in range(1, 5):
            col = (seat_id - 1) % 2
            row = (seat_id - 1) // 2
            # [실로봇연동] 표시 전용 위젯 — kiosk_tryon /kiosk/seat/status 폴링 결과 반영
            btn = SeatStatusWidget(seat_id)
            self._grid.addWidget(btn, row, col)
            self._seat_btns[seat_id] = btn
        self._seat_lay.addLayout(self._grid)
        self._right_col.addWidget(seat_card)

        # Requests card
        req_card = _card()
        self._req_lay  = QVBoxLayout(req_card)
        self._req_lay.setContentsMargins(12, 12, 12, 12)
        self._req_lay.setSpacing(8)

        # [실로봇연동] 헤더 — 라벨(typo 수정) + 우측 connection 배너
        req_hdr = QHBoxLayout()
        req_hdr.addWidget(self._section("실시간 시착 요청"))
        req_hdr.addStretch()
        self.lbl_req_status = QLabel("")
        req_hdr.addWidget(self.lbl_req_status)
        self._req_lay.addLayout(req_hdr)

        self.tbl_req = QTableWidget(0, 5)
        self.tbl_req.setHorizontalHeaderLabels(["요청번호", "좌석", "상품", "사이즈", "상태"])
        self.tbl_req.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_req.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tbl_req.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_req.verticalHeader().setVisible(False)
        self.tbl_req.setShowGrid(False)
        self.tbl_req.setEditTriggers(QTableWidget.NoEditTriggers)
        # [화면꽉채우기] 요청 표가 카드 내부에서 세로로 확장
        self.tbl_req.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._req_lay.addWidget(self.tbl_req, 1)
        # [화면꽉채우기] 우측 컬럼 — 요청 카드가 남는 세로 공간을 모두 차지
        self._right_col.addWidget(req_card, 1)

        # Robot control card
        robot_ctrl_card = _card()
        self._robot_ctrl_lay  = QVBoxLayout(robot_ctrl_card)
        self._robot_ctrl_lay.setContentsMargins(12, 12, 12, 12)
        self._robot_ctrl_lay.setSpacing(8)

        # [실로봇연동] 헤더 — 라벨 + 우측 connection 배너 (다른 섹션과 동일 패턴)
        ctrl_hdr = QHBoxLayout()
        ctrl_hdr.addWidget(self._section("로봇 제어"))
        ctrl_hdr.addStretch()
        self.lbl_ctrl_status = QLabel("")
        ctrl_hdr.addWidget(self.lbl_ctrl_status)
        self._robot_ctrl_lay.addLayout(ctrl_hdr)

        self.cmb_robot_mgmt = QComboBox()
        self.cmb_robot_mgmt.addItems(["Sshopy 1","Sshopy 2","Sshopy 3","FrontJet","WareJet"])
        # [실로봇연동] 로봇 변경 시 캐시된 robots 데이터로 즉시 라벨 갱신
        self.cmb_robot_mgmt.currentTextChanged.connect(self._refresh_ctrl_labels)
        self._robot_ctrl_lay.addWidget(self.cmb_robot_mgmt)

        # Robot status row
        rs_row = QHBoxLayout()
        # [실로봇연동] 초기값 — 데이터 도착 전엔 '—' (mock 라벨 제거)
        self.lbl_robot_name_mgmt = QLabel("—")
        self.lbl_robot_task_mgmt = QLabel("—")
        rs_row.addWidget(self.lbl_robot_name_mgmt)
        rs_row.addStretch()
        rs_row.addWidget(self.lbl_robot_task_mgmt)
        self._robot_ctrl_lay.addLayout(rs_row)

        self.btn_stop_big = QPushButton("STOP")
        self.btn_stop_big.setObjectName("btnStopBig")
        self.btn_stop_big.setToolTip(
            "선택한 로봇의 진행 중인 모든 시나리오(시착/배달/입고/회수)를 즉시 취소하고 정지"
        )
        self.btn_stop_big.clicked.connect(self._on_robot_stop)
        self._robot_ctrl_lay.addWidget(self.btn_stop_big)
        self._right_col.addWidget(robot_ctrl_card)
        # [화면꽉채우기] 우측 컬럼 하단 stretch 제거 — 카드들이 화면을 꽉 채움

        # [실로봇연동] 가장 최근 robots 응답 캐시 — 콤보 변경 시 즉시 라벨 갱신용
        self._robots_cache: list = []

        # ── Cache full inventory ───────────────────────────────────────────
        self._all_inventory = []

    # [반응형] 외부(MainWindow)에서 호출 — 모든 치수/폰트를 비례 갱신
    def apply_scale(self, s: float):
        self._scale = s

        # [화면꽉채우기] 루트 마진 0 유지 — spacing 만 스케일
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(max(round(10 * s), 4))

        # [화면꽉채우기] SCREEN 라벨 삭제됨 — 관련 styling 코드 제거

        # 섹션 라벨 일괄 갱신
        sec_px = max(round(10 * s), 8)
        sec_ls = max(round(2 * s), 1)
        for lbl in self._section_labels:
            lbl.setStyleSheet(
                f"color:#8c959f; font-size:{sec_px}px; letter-spacing:{sec_ls}px;"
                f"font-family:'Courier New',monospace; padding:0; background:transparent;"
            )

        # KPI 행
        self._kpi_row.setSpacing(max(round(10 * s), 4))
        kpi_pad_h = max(round(16 * s), 6)
        kpi_pad_v = max(round(14 * s), 5)
        kpi_title_px = max(round(10 * s), 8)
        kpi_title_ls = max(round(1 * s), 1)
        kpi_val_px = max(round(22 * s), 14)
        for lay in self._kpi_lays:
            lay.setContentsMargins(kpi_pad_h, kpi_pad_v, kpi_pad_h, kpi_pad_v)
            lay.setSpacing(max(round(4 * s), 2))
        # [실로봇연동] kpi_cards tuple shape: (lbl_title, lbl_val, color, key, hold)
        for label, (lbl_title, lbl_val, color, _key, _hold) in self.kpi_cards.items():
            lbl_title.setStyleSheet(
                f"color:#8c959f; font-size:{kpi_title_px}px; letter-spacing:{kpi_title_ls}px;"
                f"font-family:'Courier New',monospace; background:transparent;"
            )
            lbl_val.setStyleSheet(
                f"color:{color}; font-size:{kpi_val_px}px; font-weight:bold;"
                f"font-family:'Courier New',monospace; background:transparent;"
            )

        # 서브 라벨
        self._sub_lbl.setStyleSheet(
            f"color:#8c959f; font-size:{kpi_title_px}px; letter-spacing:{kpi_title_ls}px;"
            f"font-family:'Courier New',monospace;"
        )

        # 미들 행
        self._mid_row.setSpacing(max(round(10 * s), 4))

        # 재고 카드
        im = max(round(12 * s), 4)
        self._inv_lay.setContentsMargins(im, im, im, im)
        self._inv_lay.setSpacing(max(round(8 * s), 3))
        self._search_row.setSpacing(max(round(6 * s), 3))
        sh = max(round(32 * s), 22)
        self.input_search.setFixedHeight(sh)
        self._btn_search.setFixedHeight(sh)

        # 우측 컬럼
        self._right_col.setSpacing(max(round(10 * s), 4))

        # 좌석 카드
        sm = max(round(12 * s), 4)
        self._seat_lay.setContentsMargins(sm, sm, sm, sm)
        self._seat_lay.setSpacing(max(round(8 * s), 3))
        self._grid.setSpacing(max(round(8 * s), 3))
        for btn in self._seat_btns.values():
            btn.apply_scale(s)

        # 요청 카드
        rm2 = max(round(12 * s), 4)
        self._req_lay.setContentsMargins(rm2, rm2, rm2, rm2)
        self._req_lay.setSpacing(max(round(8 * s), 3))
        # [화면꽉채우기] 요청 표 고정 높이 제거 — 카드 stretch=1 로 자동 확장

        # 로봇 제어 카드
        rcm = max(round(12 * s), 4)
        self._robot_ctrl_lay.setContentsMargins(rcm, rcm, rcm, rcm)
        self._robot_ctrl_lay.setSpacing(max(round(8 * s), 3))

        rn_px = max(round(11 * s), 9)
        self.lbl_robot_name_mgmt.setStyleSheet(
            f"color:#1f2328; font-size:{rn_px}px; font-family:'Courier New',monospace; font-weight:bold;"
        )
        rt_px = max(round(11 * s), 9)
        rt_pad_v = max(round(2 * s), 1)
        rt_pad_h = max(round(8 * s), 3)
        self.lbl_robot_task_mgmt.setStyleSheet(
            f"color:#9a6700; background:#fff8c5; border:1px solid #d4a72c;"
            f"border-radius:3px; padding:{rt_pad_v}px {rt_pad_h}px;"
            f"font-size:{rt_px}px; font-weight:bold; font-family:'Courier New',monospace;"
        )

        self.btn_stop_big.setFixedHeight(max(round(42 * s), 28))

    # ── Slot handlers ─────────────────────────────────────────────────────

    def _on_robot_stop(self):
        # [실로봇연동] monitoring_screen 의 STOP 과 동일 백엔드(/api/robot/{name}/stop) 호출.
        # 백엔드는 진행 중 모든 시나리오(시착/배달/입고/회수)를 cancel + cmd_vel(0,0)
        # 까지 처리하므로, 어떤 task 가 진행 중이든 무관하게 깔끔하게 정리됨.
        # 진행 중 task 가 없는 경우에도 같은 endpoint 가 안전히 동작 (cancelled=[]).
        robot = self.cmb_robot_mgmt.currentText()
        try:
            resp = self.api.robot_stop(robot)
        except Exception as e:
            QMessageBox.warning(self, "STOP 실패", f"{robot}\n서버 연결/요청 오류:\n{e}")
            return

        cancelled = (resp or {}).get("cancelled") or []
        if cancelled:
            QMessageBox.information(
                self, "STOP",
                f"{robot} — 진행 중 시나리오 취소 + 정지 완료\n"
                f"취소된 시나리오: {', '.join(cancelled)}"
            )
        else:
            QMessageBox.information(
                self, "STOP",
                f"{robot} — 진행 중 시나리오 없음. cmd_vel(0,0) 만 발행 (정지 확인)"
            )

    def _filter_inventory(self, text: str, reset_page: bool = True):
        """[실로봇연동] 검색 형식 '상품명 사이즈' (스페이스 한 칸 구분).
        [페이지네이션]
          - 검색 결과 전체를 _inv_filtered 에 보관.
          - reset_page=True (기본, 사용자 입력 시) → 1 페이지부터 표시.
          - reset_page=False (폴링 갱신 시) → 보고 있던 페이지 유지
            (_render_inv_page 가 필요하면 페이지 인덱스 클램프).
        """
        text = (text or "").strip()
        if not text:
            self.lbl_inv_msg.setText("")
            self._inv_filtered = list(self._all_inventory)
            if reset_page:
                self._inv_page = 0
            self._render_inv_page()
            return

        parts = text.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            name_q = parts[0].strip().lower()
            size_q = int(parts[1])
            matched = [
                r for r in self._all_inventory
                if name_q in (r.get("name", "") or "").lower()
                and int(r.get("size") or 0) == size_q
            ]
            if matched:
                stock_total = sum(int(r.get("stock") or 0) for r in matched)
                self._set_inv_msg(f"재고가 {stock_total}개 있습니다", ok=True)
            else:
                self._set_inv_msg("입고되지 않은 상품입니다", ok=False)
            self._inv_filtered = matched
            if reset_page:
                self._inv_page = 0
            self._render_inv_page()
            return

        # 상품명만 입력 — 부분일치
        name_q = text.lower()
        matched = [
            r for r in self._all_inventory
            if name_q in (r.get("name", "") or "").lower()
        ]
        self.lbl_inv_msg.setText("")
        self._inv_filtered = matched
        if reset_page:
            self._inv_page = 0
        self._render_inv_page()

    # [페이지네이션] 현재 페이지만 _populate_inventory 에 넘김 + 라벨/버튼 갱신
    def _render_inv_page(self):
        total = len(self._inv_filtered)
        size  = self._inv_page_size
        pages = max(1, (total + size - 1) // size)
        # 페이지 인덱스 클램프 (필터 결과 줄어들면 페이지 인덱스 보정)
        self._inv_page = max(0, min(self._inv_page, pages - 1))

        start = self._inv_page * size
        end   = start + size
        self._populate_inventory(self._inv_filtered[start:end])

        self.lbl_inv_page.setText(
            f"{total} 건 · {self._inv_page + 1} / {pages} 페이지"
        )
        self.btn_inv_prev.setEnabled(self._inv_page > 0)
        self.btn_inv_next.setEnabled(self._inv_page < pages - 1)

    def _inv_prev_page(self):
        if self._inv_page > 0:
            self._inv_page -= 1
            self._render_inv_page()

    def _inv_next_page(self):
        total = len(self._inv_filtered)
        pages = max(1, (total + self._inv_page_size - 1) // self._inv_page_size)
        if self._inv_page < pages - 1:
            self._inv_page += 1
            self._render_inv_page()

    def _set_inv_msg(self, msg: str, ok: bool):
        """[실로봇연동] 검색 결과 안내 라벨 — 색상으로 in-stock / not-stocked 구분."""
        color = "#1f883d" if ok else "#cf222e"
        self.lbl_inv_msg.setText(msg)
        self.lbl_inv_msg.setStyleSheet(
            f"color:{color}; font-size:11px; font-family:'Courier New',monospace;"
            f"background:transparent; padding:2px 0;"
        )

    def _populate_inventory(self, items: list):
        self.tbl_inv.setRowCount(0)
        for item in items:
            row = self.tbl_inv.rowCount()
            self.tbl_inv.insertRow(row)
            # [white-theme] 재고 부족: 빨강 / 정상: 진한 회색
            stock = int(item.get("stock") or 0)
            name_item = QTableWidgetItem(item.get("name", ""))
            name_item.setForeground(QColor("#cf222e" if stock <= 1 else "#1f2328"))
            self.tbl_inv.setItem(row, 0, name_item)
            self.tbl_inv.setItem(row, 1, QTableWidgetItem(str(item.get("size", ""))))
            stock_item = QTableWidgetItem(str(stock))
            stock_item.setForeground(QColor("#cf222e" if stock <= 1 else "#1f2328"))
            self.tbl_inv.setItem(row, 2, stock_item)
            # [실로봇연동][컬럼명수정] DB 실제 컬럼명은 'warehouse_pos' (이전 잘못 추정한 ware_pos 아님)
            self.tbl_inv.setItem(row, 3, QTableWidgetItem(item.get("warehouse_pos", "—")))

    # ── Data update slots ─────────────────────────────────────────────────

    def on_dashboard_updated(self, data: dict):
        # [실로봇연동] /api/dashboard 새 응답 shape:
        #   {requests_today, retrieval_pending, retrieval_completed, sales_completed, revenue}
        # 각 KPI 카드의 4번째 튜플(key)·5번째(hold) 로 라벨→값 매핑.
        # 보류(hold=True) 항목은 무조건 '—', 데이터 없을 때(None)도 '—'.
        s = self._scale
        kpi_val_px = max(round(22 * s), 14)

        for label, (lbl_title, lbl_val, color, key, hold) in self.kpi_cards.items():
            if hold:
                text = "—"
            else:
                v = data.get(key)
                if v is None:
                    text = "—"
                elif key == "revenue":
                    text = f"₩ {int(v):,}"
                else:
                    text = str(v)
            lbl_val.setText(text)
            lbl_val.setStyleSheet(
                f"color:{color}; font-size:{kpi_val_px}px; font-weight:bold;"
                f"font-family:'Courier New',monospace; background:transparent;"
            )

    def on_dashboard_connection_changed(self, connected: bool):
        """[실로봇연동] 서버 unreachable 시 KPI 영역에 '● 연결 안됨' 배너 + 모든 값 '—'."""
        if connected:
            self.lbl_dash_status.setText("")
            self.lbl_dash_status.setStyleSheet("")
        else:
            self.lbl_dash_status.setText("● 연결 안됨")
            self.lbl_dash_status.setStyleSheet(
                "color:#cf222e; font-size:10px; letter-spacing:1px;"
                "font-family:'Courier New',monospace; background:transparent;"
            )
            # KPI 값들도 '—' 로 — 연결 끊긴 상태에서 직전 값을 잔류시키지 않음
            for label, (lbl_title, lbl_val, color, key, hold) in self.kpi_cards.items():
                lbl_val.setText("—")

    def on_inventory_updated(self, items: list):
        # [페이지네이션] 폴링 갱신 시엔 페이지 인덱스 보존 — 사용자가 보던 페이지가
        # 매 2초마다 1페이지로 튕기지 않도록.
        self._all_inventory = items
        self._filter_inventory(self.input_search.text(), reset_page=False)

    def on_inventory_connection_changed(self, connected: bool):
        """[실로봇연동] 서버/DB unreachable 시 'DB 연결 안됨' 배너 + 재고 캐시 초기화."""
        if connected:
            self.lbl_inv_status.setText("")
            self.lbl_inv_status.setStyleSheet("")
        else:
            self.lbl_inv_status.setText("● DB 연결 안됨")
            self.lbl_inv_status.setStyleSheet(
                "color:#cf222e; font-size:10px; letter-spacing:1px;"
                "font-family:'Courier New',monospace; background:transparent;"
            )
            # 직전 캐시는 _fetch_inventory 가 빈 리스트로 emit 하면서 자동 정리됨

    def on_kiosk_seats_updated(self, data: dict):
        """[실로봇연동] /kiosk/seat/status 응답 처리 — kiosk_tryon 과 동일 source.

        응답: {"seats": {"1": bool, "2": bool, ...}}  (True=점유, False=빈)
        """
        seats = (data or {}).get("seats", {}) or {}
        for sid_str, occupied in seats.items():
            try:
                sid = int(sid_str)
            except (TypeError, ValueError):
                continue
            if sid in self._seat_btns:
                self._seat_btns[sid].set_status("active" if occupied else "idle")

    def on_kiosk_seats_connection_changed(self, connected: bool):
        """[실로봇연동] kiosk_tryon source(/kiosk/seat/status) 연결 상태 표시.
        끊어졌을 때는 기존 점유 상태가 잔류하지 않도록 모든 좌석을 idle 로 리셋.
        """
        if connected:
            self.lbl_seat_status.setText("")
            self.lbl_seat_status.setStyleSheet("")
        else:
            self.lbl_seat_status.setText("● 연결 안됨")
            self.lbl_seat_status.setStyleSheet(
                "color:#cf222e; font-size:10px; letter-spacing:1px;"
                "font-family:'Courier New',monospace; background:transparent;"
            )
            for btn in self._seat_btns.values():
                btn.set_status("idle")

    # [실로봇연동] 백엔드 task_id (TRY-####) → UI 시퀀스 1, 2, 3...
    # 프로세스 실행 동안만 유지 — 재시작 시 1 부터 다시.
    def _ui_req_id(self, backend_id: str) -> int:
        if backend_id not in self._ui_req_id_map:
            self._ui_req_id_map[backend_id] = self._next_ui_req_id
            self._next_ui_req_id += 1
        return self._ui_req_id_map[backend_id]

    @staticmethod
    def _req_status_color(status: str) -> str:
        """[실로봇연동] 상태 → 색상.
        진행 중 stage_label (예: '시착존 이동 중') 은 파랑, 완료=초록, 실패=빨강, 취소=회색.
        """
        if not status:
            return "#1f2328"
        if status in STATUS_STYLE_REQ:
            return STATUS_STYLE_REQ[status]
        if status.startswith("실패"):
            return "#cf222e"
        # stage_label 진행 중 — 파랑 강조
        return "#0969da"

    def on_requests_updated(self, requests: list):
        # [실로봇연동] /api/requests 새 shape: [{id, seat, product, size, status, started_at}]
        # id (TRY-####) 는 UI 시퀀스 ID 로 변환해 표시 (1,2,3...).
        self.tbl_req.setRowCount(0)
        for req in requests:
            row = self.tbl_req.rowCount()
            self.tbl_req.insertRow(row)

            backend_id = req.get("id", "")
            ui_id      = self._ui_req_id(backend_id) if backend_id else "—"

            status = req.get("status", "") or ""
            color  = self._req_status_color(status)

            cells = [
                str(ui_id),
                str(req.get("seat") if req.get("seat") is not None else "—"),
                str(req.get("product") or "—"),
                str(req.get("size") if req.get("size") is not None else "—"),
                status or "—",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                if col == 4:
                    item.setForeground(QColor(color))
                self.tbl_req.setItem(row, col, item)

    def on_requests_connection_changed(self, connected: bool):
        """[실로봇연동] 서버 미연결 시 '연결 안됨' 배너 (표는 빈 상태로)."""
        if connected:
            self.lbl_req_status.setText("")
            self.lbl_req_status.setStyleSheet("")
        else:
            self.lbl_req_status.setText("● 연결 안됨")
            self.lbl_req_status.setStyleSheet(
                "color:#cf222e; font-size:10px; letter-spacing:1px;"
                "font-family:'Courier New',monospace; background:transparent;"
            )

    def on_robots_updated(self, robots: list):
        # [실로봇연동] 캐시 갱신 + 현재 선택된 로봇 라벨 즉시 갱신.
        self._robots_cache = robots or []
        self._refresh_ctrl_labels(self.cmb_robot_mgmt.currentText())

    def _refresh_ctrl_labels(self, name: str):
        """[실로봇연동] 콤보 변경 / 폴링 갱신 모두에서 호출.
        선택된 로봇이 캐시에 있으면 task 라벨 갱신, 없거나 데이터 없으면 '—'.
        """
        for r in self._robots_cache:
            if r.get("name") == name:
                self.lbl_robot_name_mgmt.setText(name or "—")
                self.lbl_robot_task_mgmt.setText(r.get("task") or "—")
                return
        # 일치 로봇 없음 — 이름은 콤보 선택값 유지하되 task 만 '—'
        self.lbl_robot_name_mgmt.setText(name or "—")
        self.lbl_robot_task_mgmt.setText("—")

    def on_robots_connection_changed(self, connected: bool):
        """[실로봇연동] /api/robots 폴링 연결 상태 → '연결 안됨' 배너.
        끊어졌을 때는 task 라벨도 '—' 로 리셋 (직전 값 잔류 방지).
        """
        if connected:
            self.lbl_ctrl_status.setText("")
            self.lbl_ctrl_status.setStyleSheet("")
        else:
            self.lbl_ctrl_status.setText("● 연결 안됨")
            self.lbl_ctrl_status.setStyleSheet(
                "color:#cf222e; font-size:10px; letter-spacing:1px;"
                "font-family:'Courier New',monospace; background:transparent;"
            )
            self.lbl_robot_task_mgmt.setText("—")
