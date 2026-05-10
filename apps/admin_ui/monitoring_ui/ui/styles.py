# [white-theme] 다크 네이비 테마 → 흰색 배경 + 직관 색상 팔레트로 전면 교체

# [반응형] kiosk_ui 와 동일한 스케일링 패턴 — 디자인 기준 해상도.
# MainWindow.resizeEvent 가 s = min(w/REF_W, h/REF_H) 를 계산해
# 자식 위젯의 apply_scale(s) 에 전달한다. 각 위젯은
# max(round(N * s), MIN) 형태로 폰트/마진/고정크기를 환산한다.
REF_W = 1280
REF_H = 800


GLOBAL_STYLE = """
QWidget {
    background-color: #ffffff;
    color: #1f2328;
    font-family: 'Courier New', 'Consolas', monospace;
    font-size: 12px;
}

/* ── Scrollbars ── */
QScrollBar:vertical {
    background: #f6f8fa;
    width: 6px;
    border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: #d0d7de;
    border-radius: 3px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #afb8c1;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 0; }

/* ── Tab Bar ── */
QTabWidget::pane {
    border: none;
    background: #ffffff;
}
QTabBar::tab {
    background: #f6f8fa;
    color: #57606a;
    padding: 8px 24px;
    border: none;
    font-family: 'Courier New', monospace;
    font-size: 12px;
    letter-spacing: 1px;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #0a0c10;
    border-bottom: 2px solid #0969da;
}
QTabBar::tab:hover:!selected {
    color: #1f2328;
    background: #eaeef2;
}

/* ── Buttons ── */
QPushButton {
    background: #f6f8fa;
    color: #1f2328;
    border: 1px solid #d0d7de;
    border-radius: 4px;
    padding: 6px 16px;
    font-family: 'Courier New', monospace;
    font-size: 11px;
    letter-spacing: 1px;
}
QPushButton:hover {
    background: #eaeef2;
    border-color: #afb8c1;
}
QPushButton:pressed {
    background: #d0d7de;
}

QPushButton#btnEmergency {
    background: #cf222e;
    color: #ffffff;
    border: 1px solid #cf222e;
    letter-spacing: 2px;
    font-weight: bold;
}
QPushButton#btnEmergency:hover {
    background: #a40e26;
    border-color: #a40e26;
}

QPushButton#btnLogout {
    background: #ffffff;
    color: #57606a;
    border: 1px solid #d0d7de;
    letter-spacing: 1px;
}
QPushButton#btnLogout:hover {
    background: #f6f8fa;
    color: #1f2328;
    border-color: #afb8c1;
}

QPushButton#btnStart {
    background: #1f883d;
    color: #ffffff;
    border: none;
    letter-spacing: 2px;
    font-weight: bold;
}
QPushButton#btnStart:hover { background: #1a7f37; }

QPushButton#btnStop {
    background: #ffebe9;
    color: #cf222e;
    border: 1px solid #cf222e;
    letter-spacing: 2px;
    font-weight: bold;
}
QPushButton#btnStop:hover { background: #ffcecb; }

QPushButton#btnStopBig {
    background: #ffebe9;
    color: #cf222e;
    border: 1px solid #cf222e;
    padding: 10px;
    font-size: 13px;
    letter-spacing: 4px;
    font-weight: bold;
}
QPushButton#btnStopBig:hover { background: #ffcecb; }

QPushButton#btnLogin {
    background: #0969da;
    color: #ffffff;
    border: none;
    padding: 10px;
    font-size: 13px;
    letter-spacing: 3px;
    font-weight: bold;
    border-radius: 4px;
}
QPushButton#btnLogin:hover { background: #0550ae; }

QPushButton#btnSearch {
    background: #f6f8fa;
    color: #57606a;
    border: 1px solid #d0d7de;
    padding: 6px 14px;
    letter-spacing: 1px;
}
QPushButton#btnSearch:hover {
    background: #eaeef2;
    color: #1f2328;
    border-color: #afb8c1;
}

QPushButton#btnInbound {
    background: #ddf4ff;
    color: #0969da;
    border: 1px solid #0969da;
    padding: 12px;
    font-size: 12px;
    letter-spacing: 2px;
    font-weight: bold;
}
QPushButton#btnInbound:hover { background: #b6e3ff; }

QPushButton#btnLog {
    background: #f6f8fa;
    border: 1px solid #d0d7de;
    color: #57606a;
    letter-spacing: 1px;
}
QPushButton#btnLog:hover { background: #eaeef2; color: #1f2328; }

QPushButton#btnManual {
    background: #f6f8fa;
    border: 1px solid #d0d7de;
    color: #57606a;
    letter-spacing: 1px;
}
QPushButton#btnManual:hover { background: #eaeef2; color: #1f2328; }

/* ── LineEdit ── */
QLineEdit {
    background: #ffffff;
    color: #1f2328;
    border: 1px solid #d0d7de;
    border-radius: 4px;
    padding: 6px 10px;
    font-family: 'Courier New', monospace;
    selection-background-color: #0969da;
    selection-color: #ffffff;
}
QLineEdit:focus {
    border-color: #0969da;
}
QLineEdit::placeholder {
    color: #8c959f;
}

/* ── ComboBox ── */
QComboBox {
    background: #ffffff;
    color: #1f2328;
    border: 1px solid #d0d7de;
    border-radius: 4px;
    padding: 6px 10px;
    font-family: 'Courier New', monospace;
}
QComboBox:hover { border-color: #afb8c1; }
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #57606a;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #d0d7de;
    color: #1f2328;
    selection-background-color: #ddf4ff;
    selection-color: #0a0c10;
    outline: none;
}

/* ── Table ── */
QTableWidget {
    background: transparent;
    border: none;
    gridline-color: #eaeef2;
    color: #1f2328;
    font-family: 'Courier New', monospace;
    font-size: 11px;
}
QTableWidget::item {
    padding: 4px 8px;
    border-bottom: 1px solid #eaeef2;
}
QTableWidget::item:selected {
    background: #ddf4ff;
    color: #0a0c10;
}
QHeaderView::section {
    background: #f6f8fa;
    color: #57606a;
    border: none;
    border-bottom: 1px solid #d0d7de;
    padding: 4px 8px;
    font-family: 'Courier New', monospace;
    font-size: 10px;
    letter-spacing: 1px;
    text-transform: uppercase;
}
QTableWidget QTableCornerButton::section {
    background: #f6f8fa;
    border: none;
}

/* ── Labels ── */
QLabel#labelTitle {
    color: #0a0c10;
    font-size: 13px;
    letter-spacing: 2px;
    font-weight: bold;
}
QLabel#labelSection {
    color: #8c959f;
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
}
QLabel#labelScreenId {
    color: #8c959f;
    font-size: 10px;
    letter-spacing: 1px;
}

/* ── Frames / Cards ── */
QFrame#card {
    background: #f6f8fa;
    border: 1px solid #d0d7de;
    border-radius: 6px;
}
QFrame#separator {
    background: #d0d7de;
    max-height: 1px;
}
"""
