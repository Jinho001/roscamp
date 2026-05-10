# Moosinsa Admin GUI

PySide6 기반 관제 시스템 관리자 GUI.

## 구조

```
monitoring_ui/
├── monitoring_home.py          # 진입점
├── requirements.txt
├── api/
│   └── client.py               # HTTP API 클라이언트 + PollingWorker + Mock data
└── ui/
    ├── styles.py               # 전역 QSS 스타일시트
    ├── main_window.py          # QMainWindow — 화면 스택 관리
    ├── login_screen.py         # SCREEN 1: 로그인
    ├── monitoring_screen.py    # SCREEN 2: 관제시스템
    ├── management_screen.py    # SCREEN 3: 매장관리
    └── widgets/
        ├── topbar.py           # 공유 상단 바 (탭 + 로그아웃 + 비상정지)
        ├── floor_map.py        # 실시간 매장 지도 (QPainter)
        └── robot_panel.py      # 로봇 상태 행 위젯
```

## 실행

```bash
pip install -r requirements.txt
python monitoring_home.py
```

## 서버 API 엔드포인트 (FastAPI 기준)

| Method | Path | 설명 |
|--------|------|------|
| POST | /api/auth/login | 로그인 |
| GET  | /api/dashboard  | KPI 집계 |
| GET  | /api/robots     | 로봇 상태 목록 |
| GET  | /api/schedule   | 태스크 스케줄 |
| GET  | /api/inventory  | 재고 목록 |
| GET  | /api/seats      | 좌석 상태 |
| GET  | /api/requests   | 진행중 요청 목록 |
| POST | /api/robot/{name}/start  | 로봇 태스크 시작 |
| POST | /api/robot/{name}/stop   | 로봇 정지 |
| POST | /api/robot/{name}/manual | 수동 조작 전환 |
| POST | /api/inbound/start       | 입고 시작 |
| POST | /api/emergency/stop      | 비상정지 |

## 오프라인 / Mock 모드

서버가 응답하지 않으면 `api/client.py`의 `MOCK_*` 데이터로 자동 폴백되어
UI 확인이 가능합니다. 서버 없이 실행 시 로그인은 ID `admin`, 비밀번호 임의입력으로 가능합니다.

## 폴링 주기

`api/client.py`의 `POLL_INTERVAL_MS` (기본 2000ms) 로 조정합니다.
