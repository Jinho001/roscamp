"""
ui/monitoring_screen.py
SCREEN 2 — 관제시스템 (MONITORING)
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QPushButton, QComboBox,
    QHeaderView, QSizePolicy, QScrollArea,
    # [실로봇연동] 로그확인 다이얼로그 / 결과 메시지박스용
    QDialog, QPlainTextEdit, QMessageBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui  import QColor, QFont

from ui.widgets.floor_map  import FloorMapWidget
from ui.widgets.robot_panel import RobotStatusRow
# [실맵연동] MOCK_ROBOT_POSITIONS 제거 — 실제 pose 좌표 사용


# [실로봇연동] React admin_ui 가 표시하는 status 값만 색상 매핑 — 진행중/완료/실패
STATUS_STYLE = {
    "진행중": ("color:#0969da;", "#0969da"),
    "완료":   ("color:#1f883d;", "#1f883d"),
    "실패":   ("color:#cf222e;", "#cf222e"),
}

ROBOT_NAMES = ["Sshopy 1", "Sshopy 2", "Sshopy 3", "FrontJet", "WareJet"]

# [실로봇연동][다중로봇dispatcher] 입고 시작 버튼은 fleet.inbound_demo.start() 호출 —
# 별도 입력 폼 없이 클릭만으로 다중 sshopy 입고 시나리오 dispatch.
# (이전엔 InboundStartDialog 로 robot/product/size/color/quantity 받았으나,
#  React admin_ui 의 '입고 시나리오 시작' 패턴 따라 단순화)


# [실로봇연동] 로봇별 선택 가능한 task 리스트 — React admin_ui 가 트리거 가능한 task 와 동치.
# 각 항목은 (사용자 표시 라벨, backend task_name 키워드, seat_id|None) 튜플.
# backend `/api/robot/{name}/start` 가 task_name 키워드(delivery/inbound/retrieval/tryon/arm_*)
# 로 분기하므로 키워드를 그대로 전달.
_SSHOPY_TASKS: list[tuple[str, str, int | None]] = [
    ("배달 (창고 → 매장 → 홈)",   "delivery",   None),
    ("입고",                      "inbound",    None),
    ("회수",                      "retrieval",  None),
    ("시착 — 좌석 1",             "tryon",      1),
    ("시착 — 좌석 2",             "tryon",      2),
    ("시착 — 좌석 3",             "tryon",      3),
    ("시착 — 좌석 4",             "tryon",      4),
    ("팔 동작 테스트",            "arm_test",   None),
    ("팔 초기화",                 "arm_reset",  None),
]
_JETCOBOT_TASKS: list[tuple[str, str, int | None]] = [
    ("팔 동작 테스트", "arm_test",  None),
    ("팔 초기화",      "arm_reset", None),
]
ROBOT_TASKS: dict[str, list[tuple[str, str, int | None]]] = {
    "Sshopy 1": _SSHOPY_TASKS,
    "Sshopy 2": _SSHOPY_TASKS,
    "Sshopy 3": _SSHOPY_TASKS,
    "FrontJet": _JETCOBOT_TASKS,
    "WareJet":  _JETCOBOT_TASKS,
}


# [white-theme] 카드 — 연한 회색 배경 + 보더
def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    f.setStyleSheet(
        "QFrame#card { background:#f6f8fa; border:1px solid #d0d7de; border-radius:6px; }"
    )
    return f


class MonitoringScreen(QWidget):
    def __init__(self, api_client, parent=None):
        super().__init__(parent)
        self.api = api_client
        self._robot_rows: dict[str, RobotStatusRow] = {}
        # [반응형] apply_scale 에서 일괄 스타일 갱신할 위젯/레이아웃 컬렉션
        self._section_labels: list[QLabel] = []
        self._header_labels: list[tuple[QLabel, int]] = []  # (label, base_width or -1)
        self._scale = 1.0
        # [실로봇연동] schedule UI 시퀀스 ID 매핑 — 백엔드 task_id (INB-####/RTR-####)
        # 를 화면용 1, 2, 3... 으로 변환. 프로그램 재시작 시 자동 리셋.
        self._ui_task_id_map: dict[str, int] = {}
        self._next_ui_task_id: int = 1
        # [실로봇연동] schedule 백엔드 연결 상태 — UI 배너 토글
        self._schedule_connected: bool = True
        self._build_ui()
        self.apply_scale(1.0)

    # [반응형] 섹션 라벨 — apply_scale 에서 일괄 갱신할 수 있게 모은다
    def _section(self, title: str) -> QLabel:
        lbl = QLabel(title)
        self._section_labels.append(lbl)
        return lbl

    def _build_ui(self):
        self._root = QHBoxLayout(self)
        # [화면꽉채우기] 좌우 섹션 위아래 흰 여백 제거 — 루트 마진 0
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(12)

        # ── LEFT COLUMN ───────────────────────────────────────────────────
        self._left = QVBoxLayout()
        self._left.setSpacing(10)
        self._root.addLayout(self._left, 50)

        # [화면꽉채우기] SCREEN 라벨 삭제 — 상단 여백 제거

        # Floor map card
        self._map_card = _card()
        self._map_lay  = QVBoxLayout(self._map_card)
        self._map_lay.setContentsMargins(10, 10, 10, 10)
        self._map_lay.setSpacing(6)
        map_hdr = QHBoxLayout()
        map_hdr.addWidget(self._section("LIVE MAP  ·  STORE FLOOR"))
        map_hdr.addStretch()
        self._map_lay.addLayout(map_hdr)
        # [실맵연동] ApiClient 주입 — FloorMap 이 /map/image, /map/meta 를 직접 fetch
        self.floor_map = FloorMapWidget(api=self.api)
        # [화면꽉채우기] 지도가 남는 세로 공간을 모두 차지
        self.floor_map.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._map_lay.addWidget(self.floor_map, 1)
        # [화면꽉채우기] 좌측 컬럼 — 지도 카드가 stretch 1로 확장, robot status 는 preferred
        self._left.addWidget(self._map_card, 1)

        # Robot status table card
        self._rs_card = _card()
        self._rs_lay  = QVBoxLayout(self._rs_card)
        self._rs_lay.setContentsMargins(0, 10, 0, 10)
        self._rs_lay.setSpacing(4)

        self._rs_hdr = QHBoxLayout()
        self._rs_hdr.setContentsMargins(12, 0, 12, 0)
        # [white-theme] 테이블 헤더 — muted gray
        # [반응형] (라벨, 기준 너비) 튜플로 저장 — apply_scale 에서 너비/폰트 갱신
        for text, width in [("ROBOT", 90), ("CONNECTION", 120), ("CURRENT TASK STATE", -1)]:
            l = QLabel(text)
            self._header_labels.append((l, width))
            self._rs_hdr.addWidget(l)
        self._rs_hdr.addStretch()
        self._rs_lay.addLayout(self._rs_hdr)

        # [white-theme] 굵은 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background:#d0d7de; max-height:1px; border:none;")
        self._rs_lay.addWidget(sep)

        for name in ROBOT_NAMES:
            row = RobotStatusRow(name)
            self._rs_lay.addWidget(row)
            self._robot_rows[name] = row
            if name != ROBOT_NAMES[-1]:
                # [white-theme] 행간 얇은 구분선 — 연한 회색
                s = QFrame()
                s.setFrameShape(QFrame.HLine)
                s.setStyleSheet("background:#eaeef2; max-height:1px; border:none;")
                self._rs_lay.addWidget(s)

        self._left.addWidget(self._rs_card)

        # ── RIGHT COLUMN ──────────────────────────────────────────────────
        self._right = QVBoxLayout()
        self._right.setSpacing(10)
        self._root.addLayout(self._right, 50)

        # [화면꽉채우기] SCREEN 라벨 제거에 맞춰 우측 top spacer 도 제거 — 상단 여백 없어짐

        # Schedule DB card
        self._sched_card = _card()
        self._sched_lay  = QVBoxLayout(self._sched_card)
        self._sched_lay.setContentsMargins(12, 12, 12, 12)
        self._sched_lay.setSpacing(8)

        # [실로봇연동] 섹션 헤더 — 라벨 + 우측 연결상태 배너
        sched_hdr = QHBoxLayout()
        sched_hdr.addWidget(self._section("SCHEDULE DB"))
        sched_hdr.addStretch()
        self.lbl_sched_status = QLabel("")
        sched_hdr.addWidget(self.lbl_sched_status)
        self._sched_lay.addLayout(sched_hdr)

        self.tbl_schedule = QTableWidget(0, 6)
        self.tbl_schedule.setHorizontalHeaderLabels(
            ["TASK ID", "ROBOT_NAME", "TASK NAME", "STATUS", "START TIME", "END TIME"]
        )
        # [실로봇연동] TASK NAME 만 stretch, 나머지는 contents 폭에 맞춤
        self.tbl_schedule.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tbl_schedule.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_schedule.verticalHeader().setVisible(False)
        self.tbl_schedule.setShowGrid(False)
        self.tbl_schedule.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_schedule.setSelectionBehavior(QTableWidget.SelectRows)
        # [화면꽉채우기] 스케줄 표가 카드 내부에서 세로로 확장
        self.tbl_schedule.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._sched_lay.addWidget(self.tbl_schedule, 1)
        # [화면꽉채우기] 우측 컬럼 — 스케줄 카드가 남는 세로 공간을 모두 차지
        self._right.addWidget(self._sched_card, 1)

        # Robot control card
        self._ctrl_card = _card()
        self._ctrl_lay  = QVBoxLayout(self._ctrl_card)
        self._ctrl_lay.setContentsMargins(12, 12, 12, 12)
        self._ctrl_lay.setSpacing(10)
        self._ctrl_lay.addWidget(self._section("로봇 제어"))

        # Robot select
        self.cmb_robot = QComboBox()
        self.cmb_robot.addItems(ROBOT_NAMES)
        self.cmb_robot.currentTextChanged.connect(self._on_robot_changed)
        self._ctrl_lay.addWidget(self.cmb_robot)

        # Status row
        status_row = QHBoxLayout()
        # [white-theme] 선택된 로봇명 — secondary
        self.lbl_selected_robot = QLabel("Sshopy 1")
        # [white-theme] 작업 상태 — 파랑 배지
        self.lbl_robot_task_status = QLabel("rack A-3 → 좌석 2 이동중")
        status_row.addWidget(self.lbl_selected_robot)
        status_row.addStretch()
        status_row.addWidget(self.lbl_robot_task_status)
        self._ctrl_lay.addLayout(status_row)

        # Task select
        # [실로봇연동] 항목은 _on_robot_changed() 가 ROBOT_TASKS 에서 채움.
        # itemData 에 (task_keyword, seat_id) 튜플 저장 — START 시 그대로 사용.
        self.cmb_task = QComboBox()
        self._ctrl_lay.addWidget(self.cmb_task)
        self._populate_task_combo(self.cmb_robot.currentText())

        # Control buttons
        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(8)
        self.btn_start  = QPushButton("START");  self.btn_start.setObjectName("btnStart")
        self.btn_stop   = QPushButton("STOP");   self.btn_stop.setObjectName("btnStop")
        self.btn_log    = QPushButton("로그확인"); self.btn_log.setObjectName("btnLog")
        self.btn_manual = QPushButton("수동조작"); self.btn_manual.setObjectName("btnManual")
        for b in (self.btn_start, self.btn_stop, self.btn_log, self.btn_manual):
            self._btn_row.addWidget(b)
        self._ctrl_lay.addLayout(self._btn_row)

        # Wire buttons
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        # [실로봇연동] 로그확인 — 선택 로봇 로그 다이얼로그
        self.btn_log.clicked.connect(self._on_log)
        # [실로봇연동] 수동조작 — 보류. 클릭 시 안내 메시지만 띄우고 비활성화 톤으로 표시.
        self.btn_manual.clicked.connect(self._on_manual)
        self.btn_manual.setEnabled(False)
        self.btn_manual.setToolTip("수동조작 기능은 보류 상태")

        self._right.addWidget(self._ctrl_card)

        # Inbound card
        self._inbound_card = _card()
        self._inbound_lay  = QVBoxLayout(self._inbound_card)
        self._inbound_lay.setContentsMargins(12, 12, 12, 12)
        self.btn_inbound = QPushButton("입고 시작")
        self.btn_inbound.setObjectName("btnInbound")
        self.btn_inbound.clicked.connect(self._on_inbound)
        self._inbound_lay.addWidget(self.btn_inbound)
        self._right.addWidget(self._inbound_card)

        # [화면꽉채우기] 우측 컬럼 하단 stretch 제거 — 카드가 화면을 꽉 채움

    # [반응형] 외부(MainWindow)에서 호출 — 모든 치수/폰트를 비례 갱신
    def apply_scale(self, s: float):
        self._scale = s

        # [화면꽉채우기] 루트 마진은 0 유지 (가로 간격만 좌우 spacing 으로 확보)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(max(round(12 * s), 4))
        self._left.setSpacing(max(round(10 * s), 4))
        self._right.setSpacing(max(round(10 * s), 4))

        # [화면꽉채우기] SCREEN 라벨 / 우측 top spacer 삭제됨 — 관련 styling 코드 제거

        # 섹션 라벨 일괄 갱신
        sec_px = max(round(10 * s), 8)
        sec_ls = max(round(2 * s), 1)
        for lbl in self._section_labels:
            lbl.setStyleSheet(
                f"color:#8c959f; font-size:{sec_px}px; letter-spacing:{sec_ls}px;"
                f"font-family:'Courier New',monospace; padding:0; background:transparent;"
            )

        # Map 카드 내부
        mm = max(round(10 * s), 4)
        self._map_lay.setContentsMargins(mm, mm, mm, mm)
        self._map_lay.setSpacing(max(round(6 * s), 2))
        self.floor_map.setMinimumHeight(max(round(200 * s), 140))
        self.floor_map.apply_scale(s)

        # Robot status 카드 내부
        rs_v = max(round(10 * s), 4)
        self._rs_lay.setContentsMargins(0, rs_v, 0, rs_v)
        self._rs_lay.setSpacing(max(round(4 * s), 2))
        rs_h = max(round(12 * s), 4)
        self._rs_hdr.setContentsMargins(rs_h, 0, rs_h, 0)

        hdr_px = max(round(10 * s), 8)
        hdr_ls = max(round(1 * s), 1)
        for lbl, base_w in self._header_labels:
            lbl.setStyleSheet(
                f"color:#8c959f; font-size:{hdr_px}px; letter-spacing:{hdr_ls}px;"
                f"font-family:'Courier New',monospace;"
            )
            if base_w > 0:
                lbl.setFixedWidth(max(round(base_w * s), int(base_w * 0.6)))

        # 로봇 행 스케일 전파
        for row in self._robot_rows.values():
            row.apply_scale(s)

        # Schedule 카드
        sm = max(round(12 * s), 4)
        self._sched_lay.setContentsMargins(sm, sm, sm, sm)
        self._sched_lay.setSpacing(max(round(8 * s), 3))
        # [화면꽉채우기] 스케줄 표 고정 높이 제거 — 카드 stretch=1 로 자동 확장

        # Robot control 카드
        cm = max(round(12 * s), 4)
        self._ctrl_lay.setContentsMargins(cm, cm, cm, cm)
        self._ctrl_lay.setSpacing(max(round(10 * s), 4))

        # 선택 로봇명 / 상태 배지 폰트
        sel_px = max(round(11 * s), 9)
        self.lbl_selected_robot.setStyleSheet(
            f"color:#1f2328; font-size:{sel_px}px; font-family:'Courier New',monospace; font-weight:bold;"
        )
        st_px = max(round(11 * s), 9)
        st_pad_v = max(round(2 * s), 1)
        st_pad_h = max(round(8 * s), 3)
        self.lbl_robot_task_status.setStyleSheet(
            f"color:#0969da; background:#ddf4ff; border:1px solid #0969da;"
            f"border-radius:3px; padding:{st_pad_v}px {st_pad_h}px;"
            f"font-size:{st_px}px; font-weight:bold; font-family:'Courier New',monospace;"
        )

        # 제어 버튼 행
        self._btn_row.setSpacing(max(round(8 * s), 3))
        btn_h = max(round(36 * s), 24)
        for b in (self.btn_start, self.btn_stop, self.btn_log, self.btn_manual):
            b.setFixedHeight(btn_h)

        # 입고 카드
        im = max(round(12 * s), 4)
        self._inbound_lay.setContentsMargins(im, im, im, im)
        self.btn_inbound.setFixedHeight(max(round(46 * s), 30))

    # ── Slot handlers ─────────────────────────────────────────────────────

    # [실로봇연동] 로봇 변경 시 task ComboBox 도 해당 로봇에서 가능한 task 로 갱신
    def _populate_task_combo(self, robot_name: str):
        self.cmb_task.clear()
        for label, keyword, seat_id in ROBOT_TASKS.get(robot_name, []):
            self.cmb_task.addItem(label, userData=(keyword, seat_id))

    def _on_robot_changed(self, name: str):
        self.lbl_selected_robot.setText(name)
        # [실로봇연동] 로봇별 task 리스트 재구성
        self._populate_task_combo(name)

    def _on_start(self):
        # [실로봇연동] cmb_task.itemData() 의 (keyword, seat_id) 그대로 백엔드로 전달
        robot = self.cmb_robot.currentText()
        data  = self.cmb_task.currentData()
        if not data:
            return
        keyword, seat_id = data
        try:
            self.api.robot_start(robot, keyword, seat_id=seat_id)
        except Exception as e:
            QMessageBox.warning(self, "START 실패", f"{robot} {keyword}\n{e}")

    def _on_stop(self):
        # [실로봇연동] 백엔드 /api/robot/{name}/stop 가 진행 중인 모든 시나리오를
        # cancel + cmd_vel(0,0) 까지 처리하므로 이대로 호출 → 다음 task start 안전.
        robot = self.cmb_robot.currentText()
        try:
            self.api.robot_stop(robot)
        except Exception as e:
            QMessageBox.warning(self, "STOP 실패", f"{robot}\n{e}")

    def _on_manual(self):
        # [실로봇연동] 수동조작 기능은 보류 — 백엔드 fleet.is_manual_mode/set_manual_mode 미구현.
        QMessageBox.information(
            self, "수동조작 보류",
            "수동조작 기능은 현재 보류 상태입니다.\n(백엔드 미구현)"
        )

    def _on_log(self):
        # [실로봇연동] 선택된 로봇의 최근 이벤트 로그를 다이얼로그로 표시
        robot = self.cmb_robot.currentText()
        try:
            data = self.api.robot_log(robot, limit=100)
        except Exception as e:
            QMessageBox.warning(self, "로그 조회 실패", f"{robot}\n{e}")
            return

        entries = (data or {}).get("entries", []) or []
        import time as _t
        if entries:
            text = "\n".join(
                f"{_t.strftime('%H:%M:%S', _t.localtime(e['ts']))}  {e['msg']}"
                for e in entries
            )
        else:
            text = "(로그 없음)"

        dlg = QDialog(self)
        dlg.setWindowTitle(f"로봇 로그 — {robot}")
        dlg.resize(640, 420)
        lay = QVBoxLayout(dlg)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setStyleSheet(
            "QPlainTextEdit { font-family:'Courier New',monospace; font-size:11px;"
            "background:#0a0c10; color:#d0d7de; border:1px solid #d0d7de; }"
        )
        view.setPlainText(text)
        lay.addWidget(view)

        btn_close = QPushButton("닫기")
        btn_close.clicked.connect(dlg.accept)
        lay.addWidget(btn_close)
        dlg.exec()

    def _on_inbound(self):
        # [실로봇연동][다중로봇dispatcher] React admin_ui '입고 시나리오 시작' 버튼과 동일 동작.
        # 별도 입력 없이 즉시 fleet.inbound_demo.start(['sshopy2','sshopy1','sshopy3']) 호출.
        # FrontJet/창고존/서브존 mutex 로 직렬화되어 각 sshopy worker 가 입고 task 진행.
        try:
            resp = self.api.inbound_start()   # 기본 robot_ids 사용 (백엔드 기본값)
        except Exception as e:
            QMessageBox.warning(
                self, "입고 시작 실패",
                f"서버 연결/요청 오류:\n{e}"
            )
            return

        # 응답 형식: {ok: bool, message: str, robot_ids: list[str]}
        ok        = bool(resp.get("ok"))
        msg       = resp.get("message") or ""
        robot_ids = resp.get("robot_ids") or []
        if ok:
            QMessageBox.information(
                self, "입고 시작",
                f"다중-sshopy 입고 시나리오 dispatch\n"
                f"우선순위: {', '.join(robot_ids) if robot_ids else '(기본값)'}\n"
                f"메시지: {msg or 'ok'}"
            )
        else:
            QMessageBox.warning(
                self, "입고 시작 실패",
                f"메시지: {msg or '알 수 없는 오류'}"
            )

    # ── Data update slots (called by MainWindow) ──────────────────────────

    def on_robots_updated(self, robots: list):
        # ════════════════════════════════════════════════════════════════
        # [실맵연동] /api/robots 의 정규화 (x, y) 를 그대로 floor_map 에 전달.
        # FloorMap 위젯이 실제 PGM 지도 이미지 위에 좌표를 매핑해 그린다.
        # 핑키 색상은 React UI 와 동일한 robot ordering 기준 팔레트 사용.
        # FJ/WJ 색상은 type 기반 고정 (추후 task 별 분기 가능).
        # ════════════════════════════════════════════════════════════════
        pinky_palette = ["#7c3aed", "#db2777", "#0891b2"]
        pinky_idx = 0
        robot_map: dict = {}
        for r in robots:
            name = r["name"]
            if name in self._robot_rows:
                self._robot_rows[name].update_data(r)

            entry = {**r}
            if name.startswith("Sshopy"):
                entry["color"] = pinky_palette[pinky_idx % len(pinky_palette)]
                pinky_idx += 1
            elif name == "FrontJet":
                entry["color"] = "#0969da"
            elif name == "WareJet":
                entry["color"] = "#57606a"
            robot_map[name] = entry

        self.floor_map.update_robots(robot_map)

        # Update task status label for selected robot
        selected = self.cmb_robot.currentText()
        for r in robots:
            if r["name"] == selected:
                task = r.get("task", "—")
                self.lbl_robot_task_status.setText(task)

    # [실로봇연동] epoch → "HH:MM:SS" 포맷, None/0 은 "—"
    @staticmethod
    def _fmt_time(epoch: float | None) -> str:
        if not epoch:
            return "—"
        import time
        return time.strftime("%H:%M:%S", time.localtime(epoch))

    def _ui_task_id(self, backend_id: str) -> int:
        """[실로봇연동] 백엔드 task_id (INB-0001 등) → UI 시퀀스 (1, 2, ...).
        프로그램 실행 동안만 유지, 재시작 시 1 부터 다시.
        """
        if backend_id not in self._ui_task_id_map:
            self._ui_task_id_map[backend_id] = self._next_ui_task_id
            self._next_ui_task_id += 1
        return self._ui_task_id_map[backend_id]

    def on_schedule_connection_changed(self, connected: bool):
        """[실로봇연동] PollingWorker.schedule_connection_changed 슬롯.
        서버 unreachable 시 우측 상단에 '● 연결 안됨' 배너 표시.
        """
        self._schedule_connected = connected
        if connected:
            self.lbl_sched_status.setText("")
            self.lbl_sched_status.setStyleSheet("")
        else:
            self.lbl_sched_status.setText("● 연결 안됨")
            self.lbl_sched_status.setStyleSheet(
                "color:#cf222e; font-size:10px; letter-spacing:1px;"
                "font-family:'Courier New',monospace; background:transparent;"
            )

    def on_schedule_updated(self, schedule: list):
        # [실로봇연동] /api/schedule 의 새 응답 shape 에 맞춰 row 빌드:
        #   {task_id, robot, task_name, status, started_at, completed_at, stage_label}
        # task_id 는 UI 시퀀스로 변환, 시각은 HH:MM:SS 로 포맷.
        self.tbl_schedule.setRowCount(0)
        for row_data in schedule:
            row = self.tbl_schedule.rowCount()
            self.tbl_schedule.insertRow(row)

            backend_id = row_data.get("task_id", "")
            ui_id      = self._ui_task_id(backend_id) if backend_id else "—"

            status = row_data.get("status", "")
            _, color = STATUS_STYLE.get(status, ("color:#1f2328;", "#1f2328"))

            cells = [
                str(ui_id),
                row_data.get("robot", ""),
                row_data.get("task_name", ""),
                status,
                self._fmt_time(row_data.get("started_at")),
                self._fmt_time(row_data.get("completed_at")),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                if col == 3:
                    item.setForeground(QColor(color))
                self.tbl_schedule.setItem(row, col, item)

    def on_seats_updated(self, seats: dict):
        self.floor_map.update_seats(seats.get("seats", []))
