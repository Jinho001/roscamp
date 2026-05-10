"""
ui/widgets/robot_panel.py
Robot status row widget: name | power/connection | current task
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QProgressBar,
)
from PySide6.QtCore import Qt
from PySide6.QtGui  import QColor


# [white-theme] 흰 배경에서 한 눈에 잘 보이는 배터리 상태색
def _battery_color(pct: int) -> str:
    if pct >= 60:
        return "#1f883d"   # green
    if pct >= 30:
        return "#bf8700"   # amber
    return "#cf222e"       # red


class RobotStatusRow(QWidget):
    """One row in the robot status table."""

    def __init__(self, robot_name: str, parent=None):
        super().__init__(parent)
        self.robot_name = robot_name
        # [반응형] 마지막으로 들어온 데이터 + 현재 스케일을 보존해
        # apply_scale / update_data 어느 쪽이 먼저 와도 일관된 스타일을 적용
        self._scale = 1.0
        self._last_data: dict = {}
        self._build_ui()
        self.apply_scale(1.0)

    def _build_ui(self):
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(12, 6, 12, 6)
        self._lay.setSpacing(0)

        # [white-theme] Name — 진한 텍스트
        # [반응형] 너비/폰트 apply_scale 에서 갱신
        self.lbl_name = QLabel(self.robot_name)
        self._lay.addWidget(self.lbl_name)

        # Power / connection
        self.pw_container = QWidget()
        self._pw_lay = QHBoxLayout(self.pw_container)
        self._pw_lay.setContentsMargins(0, 0, 0, 0)
        self._pw_lay.setSpacing(6)

        # [white-theme] 진행바 — 연한 회색 트랙 + 초록 채움
        # [반응형] 크기 apply_scale 에서 갱신
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)

        # [white-theme] 보조 텍스트 — secondary gray
        self.lbl_pw = QLabel("—")

        self._pw_lay.addWidget(self.bar)
        self._pw_lay.addWidget(self.lbl_pw)
        self._lay.addWidget(self.pw_container)

        # [white-theme] 작업 상태 — 흰 배경에 회색 보더
        self.lbl_task = QLabel("—")
        self._lay.addWidget(self.lbl_task)
        self._lay.addStretch()

    # [반응형] 외부에서 호출 — 너비/폰트/마진 비례 갱신
    def apply_scale(self, s: float):
        self._scale = s

        m_h = max(round(12 * s), 4)
        m_v = max(round(6 * s), 2)
        self._lay.setContentsMargins(m_h, m_v, m_h, m_v)

        self.lbl_name.setFixedWidth(max(round(90 * s), 60))
        name_px = max(round(11 * s), 9)
        self.lbl_name.setStyleSheet(
            f"color:#1f2328; font-family:'Courier New',monospace;"
            f"font-size:{name_px}px; font-weight:bold;"
        )

        self.pw_container.setFixedWidth(max(round(120 * s), 80))
        self._pw_lay.setSpacing(max(round(6 * s), 3))
        self.bar.setFixedSize(max(round(60 * s), 36), max(round(10 * s), 6))

        # 마지막 데이터로 스타일/텍스트 재적용 (없으면 placeholder)
        if self._last_data:
            self.update_data(self._last_data)
        else:
            self._restyle_placeholder()

    def _restyle_placeholder(self):
        s = self._scale
        pw_px = max(round(11 * s), 9)
        self.bar.setStyleSheet(
            "QProgressBar { background:#eaeef2; border:none; border-radius:5px; }"
            "QProgressBar::chunk { background:#1f883d; border-radius:5px; }"
        )
        self.lbl_pw.setStyleSheet(
            f"color:#57606a; font-family:'Courier New',monospace; font-size:{pw_px}px;"
        )
        task_px = max(round(11 * s), 9)
        task_pad_v = max(round(2 * s), 1)
        task_pad_h = max(round(8 * s), 3)
        self.lbl_task.setStyleSheet(
            f"color:#8c959f; font-family:'Courier New',monospace; font-size:{task_px}px;"
            f"background:#ffffff; border:1px solid #d0d7de; border-radius:3px;"
            f"padding:{task_pad_v}px {task_pad_h}px;"
        )

    def update_data(self, data: dict):
        # [반응형] apply_scale 호출 시 동일 데이터로 재스타일 가능하도록 보존
        self._last_data = dict(data)

        s = self._scale
        pw_px = max(round(11 * s), 9)
        is_battery = data.get("is_battery", True)

        if is_battery:
            # [실로봇연동] power=None (서버 미연결 / 배터리 미수신) 은 0% 가 아니라
            # '—' 로 표시 — 실제 데이터 없음을 명시.
            raw_power = data.get("power")
            if raw_power is None:
                self.bar.hide()
                self.lbl_pw.setText("—")
                self.lbl_pw.setStyleSheet(
                    f"color:#8c959f; font-family:'Courier New',monospace; font-size:{pw_px}px;"
                )
            else:
                pct = int(raw_power)
                color = _battery_color(pct)
                self.bar.setValue(pct)
                # [white-theme] 진행바 색을 배터리 잔량에 맞춰 동적 변경
                self.bar.setStyleSheet(
                    "QProgressBar { background:#eaeef2; border:none; border-radius:5px; }"
                    f"QProgressBar::chunk {{ background:{color}; border-radius:5px; }}"
                )
                self.bar.show()
                self.lbl_pw.setText(f"{pct}%")
                self.lbl_pw.setStyleSheet(
                    f"color:{color}; font-family:'Courier New',monospace; font-size:{pw_px}px; font-weight:bold;"
                )
        else:
            self.bar.hide()
            connected = data.get("connected", False)
            text  = "connect" if connected else "disconnect"
            # [white-theme] 연결 상태 — 연결: 초록 / 미연결: 회색
            color = "#1f883d" if connected else "#8c959f"
            bg    = "#dafbe1" if connected else "#eaeef2"
            pad_v = max(round(2 * s), 1)
            pad_h = max(round(6 * s), 2)
            self.lbl_pw.setText(text)
            self.lbl_pw.setStyleSheet(
                f"color:{color}; font-family:'Courier New',monospace; font-size:{pw_px}px; font-weight:bold;"
                f"background:{bg}; border:1px solid {color}; border-radius:3px;"
                f"padding:{pad_v}px {pad_h}px;"
            )

        task = data.get("task", "—")
        task_px = max(round(11 * s), 9)
        task_pad_v = max(round(2 * s), 1)
        task_pad_h = max(round(8 * s), 3)
        self.lbl_task.setText(task)
        if task == "대기중" or task == "—":
            # [white-theme] 대기 — muted
            self.lbl_task.setStyleSheet(
                f"color:#8c959f; font-family:'Courier New',monospace; font-size:{task_px}px;"
                f"background:#ffffff; border:1px solid #d0d7de; border-radius:3px;"
                f"padding:{task_pad_v}px {task_pad_h}px;"
            )
        else:
            # [white-theme] 작업중 — 파란색 강조
            self.lbl_task.setStyleSheet(
                f"color:#0969da; font-family:'Courier New',monospace; font-size:{task_px}px; font-weight:bold;"
                f"background:#ddf4ff; border:1px solid #0969da; border-radius:3px;"
                f"padding:{task_pad_v}px {task_pad_h}px;"
            )
