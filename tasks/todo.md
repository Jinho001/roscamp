# sshopylcd_ui 구현 TODO

## 목표
SShopy AMR에 부착된 320x240 터치 LCD용 PySide6 UI. 손 든 고객을 backend가 감지·배차하면 시작되는 일회성 안내 task.

## 책임 분리
- LCD: 고객 인터페이스 + 상태 보고 (fire-and-forget)
- Backend: AMR 제어 (domain_bridge), 고객 접근 감지, 배차

## 환경변수
- `PINKYPRO_ROBOT_ID` (기본 `"sshopy2"`) — backend와 동일
- `MOOSINSA_SERVICE_HOST` / `MOOSINSA_SERVICE_PORT` (기본 `localhost:8000`)

## 파일 구성
- [x] `apps/sshopylcd_ui/sshopylcd_common.py` — 상수/SVG/TopBar/ConfirmOverlay
- [x] `apps/sshopylcd_ui/sshopylcd_hangul.py` — 두벌식 입력기 + 키보드 레이아웃 (kiosk_search 복제)
- [x] `apps/sshopylcd_ui/sshopylcd_api_client.py` — fire-and-forget HTTP 클라이언트
- [x] `apps/sshopylcd_ui/sshopylcd_home.py` — 엔트리 + PageManager + HomePage
- [x] `apps/sshopylcd_ui/sshopylcd_information.py` — 이용안내
- [x] `apps/sshopylcd_ui/sshopylcd_search.py` — 검색 입력 (한글 키보드)
- [x] `apps/sshopylcd_ui/sshopylcd_search_result.py` — 검색 결과 (안내 시작 팝업은 home의 PageManager가 띄움)
- [x] `apps/sshopylcd_ui/sshopylcd_guide.py` — 안내 중 + 도착 시뮬레이션 + 안내 완료 팝업
- [x] `apps/sshopylcd_ui/requirements.txt`

## 리뷰
- 모든 모듈 임포트 정상 확인
- PageManager 기동 + HOME 페이지 렌더 정상 확인
- backend 미가동 상태에서 `page_event` 전송 실패가 fire-and-forget으로 흡수되어 UI 동작에 영향 없음 확인
- 환경변수 컨벤션 `PINKYPRO_ROBOT_ID` 재사용 (기본 `"sshopy2"`)

## Backend 연동 완료 (2026-05-12)

### 추가된 파일/변경
- [x] `services/main_server/fms/scenarios/constants.py`: GUIDE stage/label/DEMO_TARGET 추가
- [x] `services/main_server/fms/robot_manager.py`:
  - `GuideTask` dataclass, `_guide_tasks` dict, `_guide_counter`, `_guide_lock`
  - `_RobotState.guide_*` 필드 + `to_dict()` 갱신
  - `start_guide()`, `end_guide()`, `cancel_guide()`, `_guide_target()`, `_on_guide_arrived()`
  - `_set_guide_stage()`, `_finish_guide_task()` helper
  - `_check_arrival()` 에 guide 분기 추가
  - `_is_robot_idle()` 에 guide_stage 포함
  - `get_active_guide()`, `get_all_guide_tasks()`, `get_guide_status()`
- [x] `services/main_server/api_server/moosinsa_service.py`:
  - `POST /sshopylcd/page_event` (fire-and-forget 로깅)
  - `POST /sshopylcd/guide/start` (동기 응답으로 task_id 반환)
  - `POST /sshopylcd/guide/end` (홈 복귀 명령)
  - `GET  /sshopylcd/guide/status` (LCD polling 용)
  - `/api/schedule` 에 "안내" task 소스 추가 → monitoring_ui 자동 표시
- [x] `apps/sshopylcd_ui/sshopylcd_api_client.py`: 동기 start + polling 메서드
- [x] `apps/sshopylcd_ui/sshopylcd_guide.py`: 시뮬레이션 버튼 제거, 2초 polling 도입
- [x] `apps/sshopylcd_ui/sshopylcd_home.py`: 페이지 이탈 시 polling 안전 정지

### 검증
- e2e 흐름 통과 (STUB 모드): start → goal_pose 발행 → 도착 → AT_SHELF → end → 홈 복귀 → 완료
- monitoring_ui `/api/schedule` 에 GUIDE 작업이 "안내" task_name 으로 자동 노출
- 거부 케이스 (이미 작업 중 / 도착 전 종료) 정상 처리
- LCD UI fire-and-forget 폴리시: backend 미가동에도 폴링 실패만 로그로 흡수, UI 정상

### 추후 (실로봇 테스트)
- `robot_manager.GUIDE_DEMO_TARGET` 를 shoe_id 별 진열대 좌표 매핑으로 교체
  (TODO 주석 위치: `start_guide()` 내부, `_guide_target()` 내부)
- domain_bridge 측 실로봇 동작 검증

## 페이지 흐름
```
Home ──┬──► Information ─(✕)──► Home
       └──► Search ──► SearchResult ──(상품선택)──► [확인팝업: 안내드릴까요? + 안내시작]
                                                     └──► GuideInProgress (따라오세요!)
                                                            └─[도착시뮬레이션]─► [완료팝업 + 안내종료]
                                                                                 └──► Home
```

## 엔드포인트 (신규, fire-and-forget)
- `POST /sshopylcd/page_event` body: `{robot_id, page, prev}`
- `POST /sshopylcd/guide/start` body: `{robot_id, shoe_id, shoe_name}`
- `POST /sshopylcd/guide/end` body: `{robot_id}`
- `POST /search` (기존 재사용)

## 검증
- 320x240 모드 표시 확인
- 창 크기 변경 시 비율 유지 확장 확인
- 모든 페이지 전환 동작 확인
- backend 미가동 상태에서도 UI는 fire-and-forget으로 정상 동작해야 함

---

# 키오스크 좌석 환경변수 전환 (2026-05-13)

플랜 문서: `docs/kiosk_seat_env_var_20260513.md`
브랜치: `fix/seunggyu-kiosk-seat`

## 작업 항목
- [x] `apps/kiosk_ui/.env.example` 신규 작성 (`MOOSINSA_SEAT_ID` 포함)
- [x] `apps/kiosk_ui/kiosk_home.py` — 진입점에 좌석 환경변수 fail-fast 추가 (`_validate_seat_id`)
- [x] `apps/kiosk_ui/kiosk_tryon.py` — `SeatMap` 제거, `SeatInfoLabel` 도입, 섹션 제목 변경
- [x] `apps/kiosk_ui/kiosk_tryon.py` — `_on_request_clicked` 좌석 검증/재조회 단계 제거, seat=환경변수 값
- [x] `apps/kiosk_ui/kiosk_api_client.py` — `fetch_seat_status`/`_req_seat_status`/`normalize_seat_status` 제거
- [x] 자동화 검증 (AST parse / import / 환경변수 단위 테스트 / 코드 흐름 grep)
- [ ] 사용자 환경에서 UI 시각 검증 (좌석 라벨 표시, ErrorDialog, tryon_another/delivery 좌석값)
- [ ] 서버 연동 시나리오 검증 (request_tryon에 seat_id=N 전송 확인)

## 결정 요약
- 환경변수: `MOOSINSA_SEAT_ID` (정수 1~4)
- 환경변수 누락 시: 앱 시작 차단 (fail-fast)
- 서버측 변경: 없음 (`fleet._seat_occupied`는 그대로 유지 — 정당한 방어)
- 배포: `.env.example` + 기기별 `.env`

## 자동화 검증 결과
- AST parse: 3개 파일 모두 OK
- import 테스트: `MOOSINSA_SEAT_ID=2` 환경에서 kiosk_tryon import 성공, `SeatInfoLabel` 존재, `SeatMap`/`SEAT_STATUS` 제거 확인
- `_validate_seat_id` 단위 테스트: 누락/빈문자열/비정수/범위밖(0,5)/정상(1~4)/공백 포함 모두 기대대로 동작
- 죽은 참조 grep: 잔여 문자열 5개 모두 설명 주석 (실제 호출 없음)
- seat_id 데이터 흐름: `self._seat_id` = env 정수 → `SeatInfoLabel` 표시 + `selection["seat"]=str(...)` + `request_tryon(seat_id=int(...))`

## 리뷰 (작업 후 작성)
- TBD (사용자 UI 검증 완료 후)
