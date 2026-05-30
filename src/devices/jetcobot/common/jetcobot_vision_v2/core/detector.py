"""
core/detector.py
================
cv_detect_server HTTP 클라이언트 (ROS2 의존성 없음).

cv_detect_server GET /latest 응답 구조:
  {
    "detected": bool,
    "detections": [
      {"cx": float, "cy": float, "w": float, "h": float,
       "theta": float, "confidence": float, "id": int},
      ...
    ]
  }
"""

import time
import requests


class Detector:
    """
    cv_detect_server HTTP 클라이언트.

    Args:
        server_url: cv_detect_server 주소 (예: "http://192.168.1.4:8081")
    """

    def __init__(self, server_url: str):
        self._url = server_url.rstrip("/") + "/latest"

    def fetch(self, timeout: float = 3.0) -> list[dict]:
        """
        GET /latest → OBB 검출 결과 리스트 반환.
        미검출 또는 연결 실패 시 [] 반환.
        """
        try:
            resp = requests.get(self._url, timeout=timeout)
            data = resp.json()
            if data.get("detected"):
                return data.get("detections", [])
        except Exception:
            pass
        return []

    def fetch_best(self, timeout: float = 3.0) -> dict | None:
        """confidence 최고 OBB 1개 반환. 없으면 None."""
        detections = self.fetch(timeout)
        if not detections:
            return None
        return max(detections, key=lambda d: d.get("confidence", 0.0))

    def wait_for_detection(self, timeout_sec: float = 10.0,
                           poll_interval: float = 0.1) -> dict | None:
        """
        검출될 때까지 폴링. timeout_sec 초과 시 None 반환.
        """
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            best = self.fetch_best()
            if best is not None:
                return best
            time.sleep(poll_interval)
        return None
