# Research: 노트북 반납 및 퇴실 대비 백업 대상 자료 분석

본 문서는 노트북 반납 및 퇴실에 대비하여, 로컬 환경에서 백업해야 할 중요한 개발 프로젝트, 설정 파일, 스크립트 및 개인 문서 목록을 정리한 연구 분석 보고서입니다.

---

## 1. 개요 및 목적
* **목적**: 교육 수료 또는 퇴실에 따른 노트북 반납 전, 그동안 수행했던 모든 개발 작업물 및 맞춤 설정 환경을 보존하여 추후 개발 및 인수인계에 활용할 수 있도록 함.
* **주요 목표**:
  * 커밋되지 않거나 원격 저장소(Git)에 푸시되지 않은 최신 코드 백업
  * ROS 2 개발 환경 및 SSH 설정 등 로컬 설정 정보 보존
  * 기타 개인 폴더 및 중요 텍스트 메모 유실 방지

---

## 2. 백업 대상 정밀 분석

### 2.1. Git 리포지토리 및 미커밋 코드
로컬에만 남아있고 원격(GitHub)에 반영되지 않은 변경 사항이 존재합니다.

1. **`roscamp-repo-1` (메인 워크스페이스)**
   * **위치**: `/home/addinedu/roscamp-repo-1`
   * **현재 브랜치**: `feature/Jino-jetcobot-vision-pipeline`
   * **원격 저장소**:
     * `origin`: `https://github.com/addinedu-roscamp-10th/roscamp-repo-1.git` (팀 공용)
     * `personal`: `https://github.com/Jinho001/roscamp.git` (개인 저장소, 인증 토큰 포함)
   * **미커밋 변경 파일 (Staged/Unstaged)**:
     * `src/devices/jetcobot/common/jetcobot_vision/config/vision_params.yaml` (수정됨)
     * `src/devices/jetcobot/common/jetcobot_vision/config/vision_params_ware_jet.yaml` (수정됨)
   * **추적되지 않는 파일 (Untracked)**:
     * `services/ai_server/vision/extract_presentation_images.py`
     * `services/ai_server/vision/test.png`
     * `services/ai_server/vision/view_udp_stream.py`

2. **`Jino/roscamp-repo-1` (개인 작업 폴더 내 리포지토리)**
   * **위치**: `/home/addinedu/Jino/roscamp-repo-1`
   * **현재 브랜치**: `claude/ros2-robot-migration`
   * **원격 저장소**: `origin`: `git@github.com:Jinho001/roscamp-repo-1.git`
   * **변경 사항**: 다수의 파일 삭제(`src/jetcobot_vision/*` 등) 및 수정본 존재.

---

### 2.2. 기타 로컬 프로젝트 및 학습 워크스페이스
홈 디렉토리 아래에 존재하는 개별 ROS 2 워크스페이스 및 로봇 제어 관련 폴더들입니다. 빌드 산출물(`build`, `install`, `log`)로 인해 용량이 크므로, **소스 코드(`src`) 위주로 백업**하는 것이 좋습니다.

| 디렉토리 경로 | 크기 | 주요 내용 및 특징 |
| :--- | :--- | :--- |
| `/home/addinedu/WS` | **5.3G** | `colcon_ws`, `ros2_ws`, `reach_ws` 등 다양한 ROS2 워크스페이스 및 `move.py` 테스트 코드 포함. 소스 코드만 선별 백업 필요. |
| `/home/addinedu/mycobot` | **2.6G** | Mycobot 제어 라이브러리 및 테스트 스크립트(`teach.py`), 소스 코드 포함. |
| `/home/addinedu/mss_ws` | **418M** | MSS 프로젝트 ROS2 워크스페이스 및 백업 압축 파일(`src_04211506.zip`). |
| `/home/addinedu/pinky_pro` | **323M** | 네비게이션 시뮬레이션 코드, 주피터 노트북(`nav_jupyter_sim.ipynb`), 맵 파일(`sim_map.pgm`, `.yaml`). |
| `/home/addinedu/ros2_study` | **608K** | ROS 2 기초 학습 코드 및 스크립트. |

---

### 2.3. 환경 설정 및 유틸리티
새로운 장비에서 현재와 동일하게 터미널, 로봇 SSH 접속, 깃 설정을 하기 위해 필수적인 시스템 설정 파일입니다.

1. **터미널 및 ROS 환경 설정 (`.bashrc`)**
   * **위치**: `/home/addinedu/.bashrc`
   * **핵심 커스텀 설정**:
     ```bash
     source /opt/ros/jazzy/setup.bash 
     export ROS_DOMAIN_ID=14
     
     alias sb='source ~/.bashrc; echo "bashrc is reloaded!"'
     alias sis='source install/setup.bash'
     alias sshfj="ssh -X jetcobot@192.168.1.114"
     alias sshwj="ssh -X jetcobot@192.168.1.115"
     alias sshai="ssh -X team1-ai@192.168.1.121"
     ```
2. **보안 및 인증 자격 증명**
   * **`/home/addinedu/.ssh`**: GitHub SSH 키 (`id_ed25519`, `id_ed25519.pub`), 호스트 접속 설정(`config`), 알려진 호스트(`known_hosts`).
   * **`.gitconfig` 및 `.git-credentials`**: 깃 자격 정보 설정 파일.
3. **시각화 레이아웃 설정**
   * **`/home/addinedu/.rviz2`**: RViz2의 커스텀 레이아웃 및 환경 설정.

---

### 2.4. 바탕화면 및 기타 중요 문서
* **바탕화면 문서 (`/home/addinedu/Desktop`)**:
  * `command 뭉치.md` (자주 사용하는 명령어 리스트)
  * `ip list.txt` (네트워크 및 장비 IP 정보)
  * `실행가이드.txt` (구동 안내서)
  * `젯코봇 초기 실행.txt` (장비 초기 가이드)
  * `mycobot_280_pi_adaptive_gripper.urdf` (로봇 URDF 3D 모델)
  * 카카오톡 전송 이미지 다수 (`KakaoTalk_*.jpg`)
* **홈 디렉토리 내 단일 문서**:
  * `frames_2026-04-16_*.pdf` & `.gv` (TF 트리 구조 다이어그램 PDF 및 Graphviz 소스)
  * `0320.pgm` (지도 이미지)

---

## 3. 백업 및 노트북 반납 시 주의사항 (Security Guidelines)
1. **개인 정보 및 자격 증명 제거**:
   * 노트북을 반납하기 전에 `.ssh` 내 개인 키, `.gitconfig` 및 `.git-credentials` 등에 남아있는 자격 증명(Personal Access Token 등)을 삭제해야 타인의 무단 도용을 방지할 수 있습니다.
2. **원격 저장소 Push**:
   * 로컬 미커밋 변경 사항 및 개발 브랜치는 최신 상태로 GitHub 개인/팀 저장소에 Push해 두는 것이 가장 안전한 백업 방식입니다.
3. **용량 압축**:
   * ROS 2 워크스페이스는 빌드 디렉토리(`build`, `install`, `log`)를 제외하고 소스 폴더(`src`)만 압축하여 백업 용량을 최소화(5.3G -> 수백 MB 단위)해야 합니다.
