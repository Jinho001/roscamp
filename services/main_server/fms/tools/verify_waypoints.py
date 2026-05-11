"""
좌표 검증 CLI — zones.yaml 의 waypoint 를 실 로봇으로 한 곳씩 보내서 도착 여부 확인.

⚠️  실 로봇이 실제로 이동한다. CI/자동테스트로 돌리지 말 것.
    중단은 Ctrl+C — 그 후 별도로 cancel 명령 필요할 수 있음.

사용 예:
    # dry-run — 검증 대상 좌표만 출력 (이동 없음)
    python -m fms.tools.verify_waypoints --dry-run

    # 신규 waypoint 8개만 검증 (Phase 1.5 기본 set)
    python -m fms.tools.verify_waypoints --robot sshopy1

    # 특정 zone 만
    python -m fms.tools.verify_waypoints --robot sshopy1 --zone frontjet

    # interactive — 각 waypoint 직전 [Enter] 대기
    python -m fms.tools.verify_waypoints --robot sshopy1 --interactive

    # zones.yaml 의 모든 waypoint
    python -m fms.tools.verify_waypoints --robot sshopy1 --all

    # 명시 이름 리스트
    python -m fms.tools.verify_waypoints --robot sshopy1 \
        --waypoints frontjet_approach,warejet_exit
"""
import argparse
import math
import sys
import time
from datetime import datetime
from typing import Optional

import httpx

from fms.scenarios.zones import load_zones


DEFAULT_SERVER    = "http://localhost:8000"
DEFAULT_TIMEOUT   = 60.0
POLL_INTERVAL     = 0.5
ARRIVAL_FALLBACK  = 0.30

# Phase 1.5 검증 대상 — 어제 zones.yaml 에 추가한 신규 8개
DEFAULT_TARGETS = [
    "frontjet_approach", "frontjet_exit", "frontjet_hold_0",
    "warejet_approach",  "warejet_exit",  "warejet_hold_0",
    "subzone_approach",  "subzone_hold_0",
]


class C:
    """ANSI 컬러 — 터미널 출력용."""
    OK   = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    DIM  = "\033[90m"
    BOLD = "\033[1m"
    RST  = "\033[0m"


def _fmt_wp(wp: dict) -> str:
    return f"({wp['x']:+.3f}, {wp['y']:+.3f}, θ={wp['theta']:+.2f})"


def _flags(wp: dict) -> str:
    flags = []
    if wp.get("mutex"):
        flags.append(f"mutex={wp['mutex']}")
    if wp.get("is_holding_point"):
        flags.append("holding")
    if wp.get("is_parking_spot"):
        flags.append("parking")
    if wp.get("dock_name"):
        flags.append(f"dock={wp['dock_name']}")
    return f" [{', '.join(flags)}]" if flags else ""


def _get_pose(server: str, rid: str) -> Optional[dict]:
    try:
        r = httpx.get(f"{server}/robots", timeout=5.0)
        r.raise_for_status()
        for state in r.json():
            if state.get("robot_id") == rid:
                return state.get("pose")
    except Exception as e:
        print(f"{C.WARN}[warn] /robots fetch 실패: {e}{C.RST}")
    return None


def _send_goal(server: str, rid: str, wp: dict) -> tuple[bool, str]:
    try:
        r = httpx.post(
            f"{server}/robots/{rid}/goal_pose",
            json={"x": wp["x"], "y": wp["y"], "theta": wp["theta"]},
            timeout=5.0,
        )
        r.raise_for_status()
        body = r.json()
        ok = bool(body.get("ok", False))
        return ok, "ok" if ok else f"server: ok=False ({body})"
    except Exception as e:
        return False, f"REST 발행 실패: {e}"


def _wait_arrival(server: str, rid: str, target: dict,
                  timeout: float, threshold: float) -> tuple[bool, str, float]:
    """Returns: (success, message, elapsed_s)."""
    start = time.time()
    last_dist: Optional[float] = None
    deadline = start + timeout

    while time.time() < deadline:
        pose = _get_pose(server, rid)
        if pose is not None:
            dist = math.hypot(pose["x"] - target["x"], pose["y"] - target["y"])
            last_dist = dist
            if dist < threshold:
                elapsed = time.time() - start
                return True, f"도착 (거리 {dist:.3f}m, 소요 {elapsed:.1f}s)", elapsed
        time.sleep(POLL_INTERVAL)

    elapsed = time.time() - start
    msg = f"timeout {timeout:.0f}s"
    if last_dist is not None:
        msg += f" — 마지막 거리 {last_dist:.3f}m (threshold {threshold:.2f})"
    return False, msg, elapsed


def _verify_one(server: str, rid: str, name: str, wp: dict,
                timeout: float, threshold: float) -> dict:
    print(f"\n{C.DIM}── {name} {_fmt_wp(wp)}{_flags(wp)}{C.RST}")

    ok, send_msg = _send_goal(server, rid, wp)
    if not ok:
        print(f"  {C.FAIL}✗ goal_pose 발행 실패{C.RST} — {send_msg}")
        return {"name": name, "result": "send_fail", "msg": send_msg, "elapsed_s": 0.0}

    print(f"  goal_pose 발행 OK — 도착 폴링...")
    ok, msg, elapsed = _wait_arrival(server, rid, wp, timeout, threshold)
    if ok:
        print(f"  {C.OK}✓ {msg}{C.RST}")
        return {"name": name, "result": "pass", "msg": msg, "elapsed_s": elapsed}
    print(f"  {C.FAIL}✗ {msg}{C.RST}")
    return {"name": name, "result": "fail", "msg": msg, "elapsed_s": elapsed}


def _select_targets(zones: dict, args) -> list[tuple[str, dict]]:
    if args.waypoints:
        names = [n.strip() for n in args.waypoints.split(",") if n.strip()]
    elif args.all:
        names = list(zones["waypoints"].keys())
    elif args.zone:
        names = [n for n in zones["waypoints"]
                 if zones["waypoints"][n].get("mutex") == args.zone
                 or n.startswith(args.zone)]
    else:
        names = DEFAULT_TARGETS

    targets: list[tuple[str, dict]] = []
    for n in names:
        wp = zones["waypoints"].get(n)
        if wp is None:
            print(f"{C.WARN}[warn] {n} — zones.yaml 에 없음, 스킵{C.RST}")
            continue
        targets.append((n, wp))
    return targets


def _print_targets(targets: list[tuple[str, dict]]):
    print(f"검증 대상: {C.BOLD}{len(targets)}개{C.RST} waypoint")
    for n, wp in targets:
        print(f"  {n:24s} {_fmt_wp(wp)}{_flags(wp)}")


def _save_log(path: str, args, threshold: float, results: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 좌표 검증 로그\n")
        f.write(f"# 시각: {datetime.now().isoformat()}\n")
        f.write(f"# 로봇: {args.robot}, 서버: {args.server}\n")
        f.write(f"# timeout={args.timeout}s, threshold={threshold}m\n\n")
        for r in results:
            f.write(f"{r['result']:10s}  {r['name']:24s}  {r.get('msg', '')}\n")
    print(f"\n로그 저장: {path}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="좌표 검증 CLI — zones.yaml waypoint 를 실 로봇으로 검증",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("사용 예:")[1],
    )
    p.add_argument("--server", default=DEFAULT_SERVER,
                   help=f"FMS REST URL (기본: {DEFAULT_SERVER})")
    p.add_argument("--robot",
                   help="검증에 사용할 robot_id (예: sshopy1) — dry-run 외엔 필수")
    p.add_argument("--zone", help="zone 필터 (frontjet | warejet | subzone)")
    p.add_argument("--waypoints", help="명시 이름 리스트 (콤마 구분)")
    p.add_argument("--all", action="store_true",
                   help="zones.yaml 의 모든 waypoint (기본은 신규 8개)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                   help=f"각 waypoint 도착 timeout (기본: {DEFAULT_TIMEOUT}s)")
    p.add_argument("--threshold", type=float,
                   help="도착 판정 거리 m (기본: zones.yaml arrival.threshold)")
    p.add_argument("--interactive", action="store_true",
                   help="각 waypoint 진입 전 [Enter] 대기")
    p.add_argument("--dry-run", action="store_true",
                   help="좌표만 출력하고 종료 — 로봇 이동 없음")
    p.add_argument("--log-file",
                   help="결과 텍스트 로그 경로 (기본: zones_verification_<ts>.log)")
    p.add_argument("--no-confirm", action="store_true",
                   help="시작 시 y/N 확인 스킵 (interactive 모드에선 무시)")
    args = p.parse_args(argv)

    zones = load_zones()
    threshold = args.threshold or zones["arrival"].get("threshold", ARRIVAL_FALLBACK)
    targets = _select_targets(zones, args)

    if not targets:
        print(f"{C.FAIL}검증할 waypoint 없음{C.RST}")
        return 2

    _print_targets(targets)

    if args.dry_run:
        print(f"\n{C.DIM}--dry-run — 이동 안 함, 종료{C.RST}")
        return 0

    # ── 실 이동 모드 ────────────────────────────────────────────────
    if not args.robot:
        print(f"\n{C.FAIL}실 이동 모드는 --robot 필수 (예: --robot sshopy1){C.RST}")
        print(f"{C.DIM}좌표만 보고 싶으면 --dry-run 추가{C.RST}")
        return 2

    print(f"\n로봇:        {C.BOLD}{args.robot}{C.RST}")
    print(f"서버:        {args.server}")
    print(f"timeout:     {args.timeout}s")
    print(f"threshold:   {threshold}m")
    print(f"interactive: {args.interactive}")

    # 서버 reachability + 로봇 pose 확인
    try:
        httpx.get(f"{args.server}/health", timeout=3.0).raise_for_status()
    except Exception as e:
        print(f"{C.FAIL}서버 응답 없음: {e}{C.RST}")
        return 2

    pose = _get_pose(args.server, args.robot)
    if pose is None:
        print(f"{C.FAIL}{args.robot} pose 없음 — connected/localized 확인{C.RST}")
        return 2
    print(f"현재 pose:   ({pose['x']:+.3f}, {pose['y']:+.3f})")

    print(f"\n{C.WARN}⚠️  {args.robot} 가 실제로 이동합니다. "
          f"Ctrl+C 중단 + cancel 별도 필요.{C.RST}")
    if not args.no_confirm and not args.interactive:
        if input("계속? [y/N] ").strip().lower() != "y":
            print("취소.")
            return 0

    # ── 검증 루프 ───────────────────────────────────────────────────
    results: list[dict] = []
    try:
        for i, (name, wp) in enumerate(targets):
            if args.interactive:
                ans = input(f"\n[{i+1}/{len(targets)}] {name} "
                            f"[Enter=이동 / s=skip / q=중단] ").strip().lower()
                if ans == "q":
                    print("중단.")
                    break
                if ans == "s":
                    print(f"  {C.DIM}· skip{C.RST}")
                    results.append({"name": name, "result": "skipped", "msg": "user skip"})
                    continue
            results.append(_verify_one(args.server, args.robot, name, wp,
                                       args.timeout, threshold))
    except KeyboardInterrupt:
        print(f"\n{C.WARN}Ctrl+C — 중단됨. cancel 명령 필요할 수 있음.{C.RST}")

    # ── 요약 ───────────────────────────────────────────────────────
    print(f"\n{C.DIM}{'═' * 60}{C.RST}")
    print(f"{C.BOLD}검증 결과 요약{C.RST}")
    counts = {"pass": 0, "fail": 0, "send_fail": 0, "skipped": 0}
    for r in results:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
        sym = {"pass": "✓", "fail": "✗", "send_fail": "✗", "skipped": "·"}[r["result"]]
        col = {"pass": C.OK, "fail": C.FAIL, "send_fail": C.FAIL, "skipped": C.DIM}[r["result"]]
        print(f"  {col}{sym}{C.RST} {r['name']:24s} {r.get('msg', '')}")
    fail_total = counts["fail"] + counts["send_fail"]
    print(f"\n총 {len(results)}개 — "
          f"{C.OK}pass {counts['pass']}{C.RST} / "
          f"{C.FAIL}fail {fail_total}{C.RST} / "
          f"{C.DIM}skip {counts['skipped']}{C.RST}")

    log_path = args.log_file or f"zones_verification_{datetime.now():%Y%m%d_%H%M%S}.log"
    _save_log(log_path, args, threshold, results)

    return 0 if fail_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
