"""
sshopylcd_api_client.py
========================
SShopy LCD 전용 HTTP 클라이언트.

[설계]
  - sshopylcd_ui 는 backend 미가동 상태에서도 UI 핵심 동작이 유지되어야 함.
  - 페이지 이벤트는 fire-and-forget. guide/start 는 task_id 가 필요하여 동기 응답
    (실패 시 콜백에 None 전달).
  - guide/status 는 2초 주기 polling — backend 의 도착 감지를 LCD 가 수신.
  - 결과가 필요한 호출(검색·start·status)은 Qt 시그널로 메인 스레드 콜백 전달.

[환경변수]
  - MOOSINSA_SERVICE_HOST (기본 "localhost")
  - MOOSINSA_SERVICE_PORT (기본 "8000")
  - PINKYPRO_ROBOT_ID     (기본 "sshopy2")  ← backend 와 동일 컨벤션 사용

[엔드포인트]
  - POST /sshopylcd/page_event  body: {robot_id, page, prev}       (fire-and-forget)
  - POST /sshopylcd/guide/start body: {robot_id, shoe_id, shoe_name} (동기, task_id 응답)
  - POST /sshopylcd/guide/end   body: {robot_id}                    (fire-and-forget)
  - GET  /sshopylcd/guide/status?robot_id=...                       (polling 2s)
  - POST /search                body: {keyword, accumulated_tags}   (kiosk_ui 와 동일)
"""

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Callable, Optional

from dotenv import load_dotenv
from PySide6.QtCore import QObject, Signal

load_dotenv()

# ── 설정 ──────────────────────────────────────────────────────
_HOST = os.environ.get("MOOSINSA_SERVICE_HOST", "localhost")
_PORT = os.environ.get("MOOSINSA_SERVICE_PORT", "8000")
BASE_URL = f"http://{_HOST}:{_PORT}"

ROBOT_ID = os.environ.get("PINKYPRO_ROBOT_ID", "sshopy2")

_TIMEOUT = 5.0
_SEARCH_TIMEOUT = 60.0


# ── Qt 시그널 브리지 ─────────────────────────────────────────
class _SignalBridge(QObject):
    """백그라운드 스레드 → 메인 스레드 결과 전달용 Qt 시그널."""
    done = Signal(object, object)  # (callback, result)


# ── API 클라이언트 ────────────────────────────────────────────
class SshopyLcdApiClient:
    """
    SShopy LCD 전용 클라이언트.

    `robot_id` 는 인스턴스 생성 시 PINKYPRO_ROBOT_ID 환경변수에서 자동 주입.
    모든 페이로드에 robot_id 자동 포함.
    """

    def __init__(self):
        self.robot_id = ROBOT_ID
        self._bridge = _SignalBridge()
        self._bridge.done.connect(self._dispatch)

    # ════════════════════════════════════════════════════════
    # Public API
    # ════════════════════════════════════════════════════════

    def send_page_event(self, page: str, prev: Optional[str] = None):
        """페이지 전환 이벤트 (fire-and-forget, 결과 무시)."""
        payload = {"robot_id": self.robot_id, "page": page, "prev": prev}
        self._fire_forget("/sshopylcd/page_event", payload)

    def send_guide_start(
        self,
        shoe_id: str,
        shoe_name: str = "",
        callback: Optional[Callable[[Optional[dict]], None]] = None,
    ):
        """
        안내 시작. Backend 는 이 호출을 받으면 SShopy 를 진열대로 이동시킨다.
        응답으로 task_id 를 받아 callback 으로 전달 (polling 에 필요).
        실패 시 callback 인자는 None.
        """
        payload = {
            "robot_id":  self.robot_id,
            "shoe_id":   shoe_id,
            "shoe_name": shoe_name,
        }
        self._run_with_callback(
            self._req_guide_start, args=(payload,),
            callback=callback or (lambda _r: None),
        )

    def send_guide_end(self):
        """
        안내 종료 — backend 가 SShopy 를 home pose 로 복귀시킨다 (fire-and-forget).
        """
        payload = {"robot_id": self.robot_id}
        self._fire_forget("/sshopylcd/guide/end", payload)

    def poll_guide_status(
        self,
        callback: Callable[[Optional[dict]], None],
    ):
        """
        안내 진행 상태 조회 — backend 의 도착 감지를 LCD 가 수신하기 위한 단발 호출.
        QTimer 가 2초 주기로 반복 호출하는 식으로 사용.

        callback 인자:
            dict — {robot_id, task_id, stage, stage_label, arrived, completed}
            None — 통신 실패
        """
        self._run_with_callback(
            self._req_guide_status, args=(self.robot_id,), callback=callback,
        )

    def search(
        self,
        keyword: str,
        accumulated_tags: dict,
        callback: Callable[[Optional[dict]], None],
    ):
        """
        키워드 검색. 결과 dict 또는 None 을 메인 스레드 콜백으로 전달.
        """
        self._run_with_callback(
            self._req_search, args=(keyword, accumulated_tags), callback=callback,
        )

    # ════════════════════════════════════════════════════════
    # 내부: 비동기 실행
    # ════════════════════════════════════════════════════════

    def _fire_forget(self, path: str, body: dict):
        """결과를 기다리지 않는 POST. 실패는 로그로만."""
        url = f"{BASE_URL}{path}"

        def _worker():
            try:
                self._post_json(url, body, timeout=_TIMEOUT)
            except urllib.error.URLError as e:
                print(f"[SshopyLcdApi] {path} 전송 실패 (무시): {e.reason}")
            except Exception as e:
                print(f"[SshopyLcdApi] {path} 예외 (무시): {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _run_with_callback(self, func, args: tuple, callback: Callable):
        """func 실행 후 Qt 시그널로 메인 스레드 콜백 호출."""
        def _worker():
            try:
                result = func(*args)
            except Exception as e:
                print(f"[SshopyLcdApi] 예외 ({func.__name__}): {e}")
                result = None
            self._bridge.done.emit(callback, result)
        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _dispatch(callback: Callable, result):
        try:
            callback(result)
        except Exception as e:
            print(f"[SshopyLcdApi] 콜백 예외: {e}")

    # ════════════════════════════════════════════════════════
    # 내부: HTTP 호출
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _post_json(url: str, body: dict, timeout: float) -> dict:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data) if data else {}

    def _req_search(self, keyword: str, accumulated_tags: dict) -> Optional[dict]:
        """POST /search → {results, count, ...}"""
        url = f"{BASE_URL}/search"
        return self._post_json(
            url,
            {"keyword": keyword, "accumulated_tags": accumulated_tags},
            _SEARCH_TIMEOUT,
        )

    def _req_guide_start(self, payload: dict) -> Optional[dict]:
        """POST /sshopylcd/guide/start → {success, robot_id, task_id} 또는 None."""
        url = f"{BASE_URL}/sshopylcd/guide/start"
        try:
            return self._post_json(url, payload, _TIMEOUT)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8"))
                detail = err.get("detail", "안내 시작 실패")
            except Exception:
                detail = "안내 시작 실패"
            print(f"[SshopyLcdApi] guide/start 거부 ({e.code}): {detail}")
            return {"success": False, "detail": detail}

    def _req_guide_status(self, robot_id: str) -> Optional[dict]:
        """GET /sshopylcd/guide/status?robot_id=... → status dict."""
        import urllib.parse
        qs = urllib.parse.urlencode({"robot_id": robot_id})
        url = f"{BASE_URL}/sshopylcd/guide/status?{qs}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data) if data else {}


# ════════════════════════════════════════════════════════════
# 검색 결과 정규화 (kiosk_ui 와 동일 로직, 자체 보유)
# ════════════════════════════════════════════════════════════
def normalize_search_results(raw: dict) -> list:
    """
    /search 응답 → SshopyLcdSearchResultPage 가 쓰는 dict 리스트로 변환.

    반환:
      [{"rank", "name", "brand", "price", "tag", "shoe_id", "image_url"}, ...]
    """
    if not raw:
        return []
    results = raw.get("results", [])
    normalized = []
    for i, item in enumerate(results):
        normalized.append({
            "rank":      i + 1,
            "name":      item.get("model", ""),
            "brand":     item.get("brand", ""),
            "price":     int(item.get("price", 0)),
            "tag":       item.get("tags", ""),
            "shoe_id":   item.get("shoe_id", ""),
            "image_url": item.get("image_url", None),
        })
    return normalized
