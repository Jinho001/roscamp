# Plan: 노트북 반납 전 백업 및 정리 수행 계획

본 계획서는 노트북 반납 전에 로컬 데이터를 안전하게 보존하고, 개인 정보 유출을 차단하기 위한 단계별 백업 프로세스 실행 계획입니다. 사용자의 검토 및 승인 이후 실행됩니다.

---

## 1. 단계별 실행 계획

### [Step 1] Git 미커밋 변경 사항 및 Untracked 파일 백업
1. **메인 워크스페이스 (`/home/addinedu/roscamp-repo-1`)**
   * 브랜치: `feature/Jino-jetcobot-vision-pipeline`
   * 커밋되지 않은 YAML 설정 파일 2종 커밋 작성
   * AI 서버 내에 존재하는 추적되지 않는 파이썬 스크립트들(`services/ai_server/vision/...`)을 Git 스테이징 및 커밋
   * 원격 저장소(`origin` 및 `personal`)에 변경 내용 `git push` 진행
2. **개인 워크스페이스 (`/home/addinedu/Jino/roscamp-repo-1`)**
   * `claude/ros2-robot-migration` 브랜치 상태 점검 및 미커밋 수정 파일들의 임시 커밋 작성
   * 개인 원격 저장소로 `git push` 진행

---

### [Step 2] 로컬 프로젝트 폴더 선별 압축 (용량 최소화)
빌드 파일(`build`, `install`, `log`)을 제외하고 핵심 소스 코드(`src` 및 개별 스크립트)만 압축하여 백업 용량을 대폭 줄입니다.
* 백업 전용 임시 폴더 생성: `/home/addinedu/Backup_Jino`
* 각 디렉토리별 백업 스크립트 수행:
  * **`WS`**: `colcon_ws/src`, `reach_ws/src`, `ros2_ws/src`, `move.py` 등을 선별 복사 후 압축.
  * **`mycobot`**: `src` 및 `teach.py` 선별 압축.
  * **`mss_ws`**: `src` 선별 압축.
  * **`pinky_pro`**: `src`, `nav_jupyter_sim.ipynb`, 맵 파일 선별 압축.

---

### [Step 3] 환경 설정 및 바탕화면 문서 일괄 수집
* **환경 설정 파일 수집**:
  * `/home/addinedu/.bashrc` -> `Backup_Jino/setup/`
  * `/home/addinedu/.ssh/` 내 설정 및 키 파일 -> `Backup_Jino/setup/ssh/`
  * `/home/addinedu/.gitconfig` 및 `.git-credentials` -> `Backup_Jino/setup/git/`
  * `/home/addinedu/.rviz2/` -> `Backup_Jino/setup/rviz/`
* **바탕화면 및 개별 문서 수집**:
  * 바탕화면 메모 텍스트, 가이드 문서, URDF 모델 수집 -> `Backup_Jino/Desktop_Docs/`
  * 홈 디렉토리 내 PDF, GV, PGM 지도 파일 수집 -> `Backup_Jino/Desktop_Docs/`

---

### [Step 4] 통합 압축 및 최종 저장 매체로 이전
* `Backup_Jino` 폴더 전체를 하나의 아카이브 파일(`Backup_Jino_20260530.tar.gz`)로 압축.
* **이전 방식 (사용자 선택 필요)**:
  * 방법 A: 외부 USB 저장 장치 연결 후 복사.
  * 방법 B: 웹 브라우저(Google Drive 등 Cloud Storage)를 통한 업로드.
  * 방법 C: 깃허브 개인 비공개(Private) 저장소에 압축 파일 업로드.

---

### [Step 5] 노트북 반납용 보안 정리 (Sanitization)
백업이 정상적으로 완료되었음을 확인한 후, 다음 보안 조치를 수행합니다.
* `.ssh/id_ed25519` 및 개인 비밀 키 파일 삭제.
* `.git-credentials` 파일 삭제 (GitHub Access Token 정보 영구 제거).
* 터미널 히스토리(`.bash_history`) 및 웹 브라우저 로그인 정보/캐시 로그아웃 및 삭제.

---

## 2. 사용자 검토 및 요구사항 (피드백 요청)
> [!IMPORTANT]
> 본 계획서(plan.md)를 확인하신 후, 수정할 사항이나 추가로 백업하고 싶으신 폴더/파일이 있다면 메모를 남겨주시기 바랍니다.
> 승인(예: "승인", "계획대로 진행해줘" 등)을 해주시면 각 단계를 안전하게 수행할 수 있는 쉘 스크립트 작성 및 백업 실행에 들어가겠습니다.
