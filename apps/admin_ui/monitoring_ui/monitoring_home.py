import os
import sys
from pathlib import Path

# [원격배포] kiosk_ui 와 동일하게 같은 디렉터리의 .env 를 로드해서
# MOOSINSA_SERVICE_HOST 등을 ApiClient 가 읽을 수 있도록 한다.
# .env 미존재 시 조용히 패스 — 셸 export 만으로도 동작.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from ui.main_window import MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
