"""
Zone / waypoint 로더 — Open-RMF 어휘 (mutex group, holding point, parking spot).

Phase 1 — 데이터 로더만 추가. 기존 시나리오는 이 모듈을 사용하지 않는다.
Phase 2 부터 MutexGroupManager / 새 입고 시나리오에서 사용 예정.

좌표 단일 소스: scenarios/zones.yaml.
quaternion: {oz, ow} 가 주어지면 theta = 2 * atan2(oz, ow) 로 변환.
theta: <float> 가 직접 주어지면 그대로 사용.

API:
    load_zones()                     → 전체 정규화 dict (캐시됨)
    get_waypoint(name)               → 단일 waypoint dict
    get_waypoints_in_mutex(group)    → 같은 mutex 그룹 waypoint 리스트
    get_parking_spot(robot_id)       → 해당 로봇의 홈 (parking_spot) dict
"""
import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

_YAML_PATH = Path(__file__).parent / "zones.yaml"


def _q_to_theta(oz: float, ow: float) -> float:
    return 2.0 * math.atan2(oz, ow)


def _normalize_waypoint(name: str, raw: dict) -> dict:
    """yaml waypoint entry → 표준 dict."""
    if "quaternion" in raw:
        q = raw["quaternion"]
        theta = _q_to_theta(float(q["oz"]), float(q["ow"]))
    elif "theta" in raw:
        theta = float(raw["theta"])
    else:
        raise ValueError(f"waypoint '{name}': quaternion 또는 theta 중 하나는 필수")

    return {
        "name":             name,
        "x":                float(raw["x"]),
        "y":                float(raw["y"]),
        "theta":            theta,
        "mutex":            raw.get("mutex"),
        "dock_name":        raw.get("dock_name"),
        "is_parking_spot":  bool(raw.get("is_parking_spot", False)),
        "is_holding_point": bool(raw.get("is_holding_point", False)),
        "owner":            raw.get("owner"),
        "description":      raw.get("description"),
    }


@lru_cache(maxsize=1)
def load_zones(path: Optional[str] = None) -> dict:
    """
    zones.yaml 을 파싱해서 정규화된 dict 반환 (모듈 단위 캐시).

    반환 구조:
        {
          "waypoints":        {name: {x, y, theta, mutex, ...}},
          "by_mutex":         {group: [wp, ...]},
          "parking_by_owner": {robot_id: wp},
          "arrival":          {"threshold", "cooldown"},
          "priorities":       {"customer_call", "tryon", "inbound", "retrieval"},
          "version":          1,
        }
    """
    yaml_path = Path(path) if path else _YAML_PATH
    with yaml_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    waypoints: dict[str, dict] = {}
    by_mutex: dict[str, list[dict]] = {}
    parking_by_owner: dict[str, dict] = {}

    for name, entry in (raw.get("waypoints") or {}).items():
        wp = _normalize_waypoint(name, entry)
        waypoints[name] = wp
        if wp["mutex"]:
            by_mutex.setdefault(wp["mutex"], []).append(wp)
        if wp["is_parking_spot"] and wp["owner"]:
            parking_by_owner[wp["owner"]] = wp

    return {
        "waypoints":        waypoints,
        "by_mutex":         by_mutex,
        "parking_by_owner": parking_by_owner,
        "arrival":          raw.get("arrival", {"threshold": 0.30, "cooldown": 5.0}),
        "priorities":       raw.get("priorities", {}),
        "version":          raw.get("version", 1),
    }


def get_waypoint(name: str) -> dict:
    """이름으로 waypoint 조회. 없으면 KeyError."""
    return load_zones()["waypoints"][name]


def get_waypoints_in_mutex(group: str) -> list[dict]:
    """특정 mutex 그룹 waypoint 리스트. 없으면 빈 리스트."""
    return load_zones()["by_mutex"].get(group, [])


def get_parking_spot(robot_id: str) -> Optional[dict]:
    """해당 로봇의 parking_spot (홈) 조회. 없으면 None."""
    return load_zones()["parking_by_owner"].get(robot_id)


if __name__ == "__main__":
    import json
    print(json.dumps(load_zones(), indent=2, ensure_ascii=False))
