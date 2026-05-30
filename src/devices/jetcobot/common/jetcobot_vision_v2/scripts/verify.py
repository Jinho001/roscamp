#!/usr/bin/env python3
"""
scripts/verify.py
=================
CV 파이프라인 단계별 검증 도구 (ROS2 불필요, 순수 Python).
core 모듈(CoordTransformer, Detector)을 직접 사용해 단계별 오차를 측정.

Step A — 픽셀 스케일: OBB w/h 픽셀 → mm 역산, 실제 상자 크기 대비 오차
Step B — 중심 좌표 절대 오차: (cx, cy) → base_link (x, y) 변환 후 실측 위치와 비교
Step C — Yaw 방향: 상자를 직접 회전시켜 측정 yaw vs 입력 expected yaw 오차

사용 예:
  python3 scripts/verify.py --config config/params_front_jet.yaml \\
      --server http://192.168.1.4:8081 --box-w 33 --box-h 25

  # Step B 추가 (상자를 base_link 기준 x=150mm, y=0mm에 놓고)
  python3 scripts/verify.py ... --real-x 150 --real-y 0

  # Step C 추가 (상자를 45도 회전시켜 놓고)
  python3 scripts/verify.py ... --expected-yaw 45
"""

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from core.coord_transform import CoordTransformer
from core.detector import Detector

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def _load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


# ── Step A ───────────────────────────────────────────────────────────────────

def verify_pixel_scale(obb: dict, transformer: CoordTransformer,
                       z_surface_mm: float,
                       box_w: float, box_h: float) -> dict:
    """OBB w/h 픽셀 → mm 역산, 실제 크기 대비 오차."""
    w_px, h_px = obb['w'], obb['h']

    z_cam = transformer.camera_z_mm
    if z_cam is None:
        return {"error": "T_base2cam 미갱신 — update_pose() 먼저 호출 필요"}

    d_z = z_cam - z_surface_mm
    if d_z <= 0:
        return {"error": f"d_z={d_z:.1f}mm ≤ 0 — z_surface_mm 설정 오류"}

    fx = transformer._K[0, 0]
    fy = transformer._K[1, 1]

    w_est = w_px * d_z / fx
    h_est = h_px * d_z / fy

    # 90도 회전 매칭
    if abs(w_est - box_h) + abs(h_est - box_w) < abs(w_est - box_w) + abs(h_est - box_h):
        w_est, h_est = h_est, w_est

    err_w = w_est - box_w
    err_h = h_est - box_h

    d_z_opt = (box_w * fx / max(w_px, 1) + box_h * fy / max(h_px, 1)) / 2.0
    z_surf_opt = z_cam - d_z_opt

    return {
        "w_px": w_px, "h_px": h_px,
        "z_cam_mm": round(z_cam, 1),
        "d_z_mm": round(d_z, 1),
        "w_estimated_mm": round(w_est, 2),
        "h_estimated_mm": round(h_est, 2),
        "target_w_mm": box_w, "target_h_mm": box_h,
        "error_w_mm": round(err_w, 2),
        "error_h_mm": round(err_h, 2),
        "z_surface_current_mm": z_surface_mm,
        "z_surface_optimal_mm": round(z_surf_opt, 1),
        "pass": abs(err_w) < 3.0 and abs(err_h) < 3.0,
    }


# ── Step B ───────────────────────────────────────────────────────────────────

def verify_center_position(obb: dict, transformer: CoordTransformer,
                           z_surface_mm: float,
                           real_x_mm: float, real_y_mm: float) -> dict:
    """(cx, cy) → base_link (x, y) 변환 후 실측 위치와 비교."""
    pt = transformer.pixel_to_base(obb['cx'], obb['cy'], z_surface_mm)
    if pt is None:
        return {"error": "Ray-Plane 교점 계산 실패"}

    x_est, y_est, _ = pt
    err_x = x_est - real_x_mm
    err_y = y_est - real_y_mm
    total = math.sqrt(err_x**2 + err_y**2)

    return {
        "cx_px": obb['cx'], "cy_px": obb['cy'],
        "x_estimated_mm": round(x_est, 2),
        "y_estimated_mm": round(y_est, 2),
        "x_real_mm": real_x_mm,
        "y_real_mm": real_y_mm,
        "error_x_mm": round(err_x, 2),
        "error_y_mm": round(err_y, 2),
        "total_error_mm": round(total, 2),
        "pass": total <= 5.0,
    }


# ── Step C ───────────────────────────────────────────────────────────────────

def verify_yaw(obb: dict, transformer: CoordTransformer,
               expected_yaw_deg: float) -> dict:
    """OBB theta → base_link yaw 변환 결과를 실제 각도와 비교 (180° 대칭 처리)."""
    measured = transformer.theta_to_yaw(obb['theta'])
    if measured is None:
        return {"error": "T_base2cam 미갱신"}

    err = measured - expected_yaw_deg
    while err > 90.0:
        err -= 180.0
    while err < -90.0:
        err += 180.0

    return {
        "theta_cam_rad": round(obb['theta'], 4),
        "measured_yaw_deg": round(measured, 2),
        "expected_yaw_deg": expected_yaw_deg,
        "error_deg": round(err, 2),
        "pass": abs(err) <= 5.0,
    }


# ── 출력 헬퍼 ────────────────────────────────────────────────────────────────

def _badge(passed: bool) -> str:
    return PASS if passed else FAIL


def print_step_a(r: dict) -> None:
    print("\n╔══ Step A: 픽셀 스케일 검증 ══════════════════════════════════╗")
    if "error" in r:
        print(f"  {FAIL} {r['error']}")
        return
    print(f"  OBB 검출:    w={r['w_px']:.0f}px  h={r['h_px']:.0f}px")
    print(f"  카메라 높이: {r['z_cam_mm']}mm  |  d_z: {r['d_z_mm']}mm")
    print(f"  추정 크기:   {r['w_estimated_mm']}mm × {r['h_estimated_mm']}mm")
    print(f"  실제 크기:   {r['target_w_mm']}mm × {r['target_h_mm']}mm")
    print(f"  오차:        가로 {r['error_w_mm']:+.2f}mm / 세로 {r['error_h_mm']:+.2f}mm  {_badge(r['pass'])}")
    if not r['pass'] or abs(r['z_surface_optimal_mm'] - r['z_surface_current_mm']) > 1.0:
        diff = r['z_surface_optimal_mm'] - r['z_surface_current_mm']
        print(f"  {WARN} 권장 z_surface_mm: {r['z_surface_optimal_mm']}mm  "
              f"(현재 {r['z_surface_current_mm']}mm, 조정 {diff:+.1f}mm)")
    print("╚═══════════════════════════════════════════════════════════════╝")


def print_step_b(r: dict) -> None:
    print("\n╔══ Step B: 중심 좌표 절대 오차 ═══════════════════════════════╗")
    if "error" in r:
        print(f"  {FAIL} {r['error']}")
        return
    print(f"  OBB 중심:  cx={r['cx_px']:.1f}px  cy={r['cy_px']:.1f}px")
    print(f"  변환 결과: x={r['x_estimated_mm']}mm  y={r['y_estimated_mm']}mm")
    print(f"  실측 위치: x={r['x_real_mm']}mm  y={r['y_real_mm']}mm")
    print(f"  오차:      Δx={r['error_x_mm']:+.2f}mm  Δy={r['error_y_mm']:+.2f}mm"
          f"  총={r['total_error_mm']:.2f}mm (기준 ≤5mm)  {_badge(r['pass'])}")
    print("╚═══════════════════════════════════════════════════════════════╝")


def print_step_c(r: dict) -> None:
    print("\n╔══ Step C: Yaw 방향 검증 ══════════════════════════════════════╗")
    if "error" in r:
        print(f"  {FAIL} {r['error']}")
        return
    print(f"  theta_cam: {math.degrees(r['theta_cam_rad']):.1f}° (카메라 픽셀 좌표계)")
    print(f"  측정 yaw:  {r['measured_yaw_deg']}°")
    print(f"  실제 yaw:  {r['expected_yaw_deg']}°")
    print(f"  오차:      {r['error_deg']:+.2f}° (기준 ≤5°)  {_badge(r['pass'])}")
    print("╚═══════════════════════════════════════════════════════════════╝")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CV 파이프라인 단계별 검증")
    parser.add_argument("--config",   required=True, help="params_*.yaml 경로")
    parser.add_argument("--profile",  default="receiving_zone", help="프로파일 이름")
    parser.add_argument("--server",   default=None,  help="cv_detect_server URL (기본: config에서 읽음)")
    parser.add_argument("--box-w",    type=float, required=True, help="상자 실제 가로 (mm)")
    parser.add_argument("--box-h",    type=float, required=True, help="상자 실제 세로 (mm)")
    parser.add_argument("--real-x",   type=float, default=None,  help="[Step B] 상자 실측 x (mm, base_link 기준)")
    parser.add_argument("--real-y",   type=float, default=None,  help="[Step B] 상자 실측 y (mm, base_link 기준)")
    parser.add_argument("--expected-yaw", type=float, default=None, help="[Step C] 상자 실제 yaw (deg)")
    parser.add_argument("--save-json", default=None, help="결과 저장 경로 (예: results/verify.json)")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    ct_cfg  = cfg['coord_transform']
    det_cfg = cfg['detector']
    profile = cfg['profiles'].get(args.profile, {})
    z_surface_mm = profile.get('z_surface_mm', 0.0)

    transformer = CoordTransformer(
        handeye_matrix    = ct_cfg['handeye_matrix'],
        camera_intrinsics = ct_cfg['camera_intrinsics'],
    )

    server_url = args.server or det_cfg['server_url']
    detector   = Detector(server_url)

    print("=" * 65)
    print("  CV 파이프라인 단계별 검증 (jetcobot_vision_v2)")
    print(f"  config  : {Path(args.config).name}  profile={args.profile}")
    print(f"  서버    : {server_url}")
    print(f"  상자    : {args.box_w}×{args.box_h}mm  z_surface={z_surface_mm}mm")
    print(f"  시각    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # observe_pose 기준 T_base2cam 설정 (실제 로봇 없을 때 YAML observe_pose 사용)
    obs = profile.get('observe_pose')
    if obs:
        transformer.update_pose(obs)
        print(f"\n[*] observe_pose 기준 T_base2cam 계산 (카메라 Z: {transformer.camera_z_mm:.1f}mm)")
    else:
        print(f"\n{FAIL} profile에 observe_pose 없음")
        sys.exit(1)

    print(f"\n[*] cv_detect_server 검출 대기 중... ({server_url})")
    obb = detector.wait_for_detection(timeout_sec=10.0)
    if obb is None:
        print(f"{FAIL} 상자가 감지되지 않음. cv_detect_server 실행 여부 확인.")
        sys.exit(1)
    print(f"[+] 검출됨  cx={obb['cx']:.1f} cy={obb['cy']:.1f} "
          f"w={obb['w']:.1f} h={obb['h']:.1f} conf={obb['confidence']:.2f}")

    results = {
        "timestamp": datetime.now().isoformat(),
        "config": args.config,
        "profile": args.profile,
        "box_w_mm": args.box_w,
        "box_h_mm": args.box_h,
        "detection": obb,
    }

    # Step A
    r_a = verify_pixel_scale(obb, transformer, z_surface_mm, args.box_w, args.box_h)
    print_step_a(r_a)
    results["step_a"] = r_a

    # Step B
    if args.real_x is not None and args.real_y is not None:
        r_b = verify_center_position(obb, transformer, z_surface_mm, args.real_x, args.real_y)
        print_step_b(r_b)
        results["step_b"] = r_b
    else:
        print(f"\n  [Step B 스킵]  --real-x / --real-y 입력 시 중심 좌표 오차 측정")

    # Step C
    if args.expected_yaw is not None:
        r_c = verify_yaw(obb, transformer, args.expected_yaw)
        print_step_c(r_c)
        results["step_c"] = r_c
    else:
        print(f"  [Step C 스킵]  --expected-yaw 입력 시 Yaw 방향 오차 측정")

    all_pass = all([
        results.get("step_a", {}).get("pass", True),
        results.get("step_b", {}).get("pass", True),
        results.get("step_c", {}).get("pass", True),
    ])
    print(f"\n{'='*65}")
    print(f"  종합 판정: {'PASS ' + PASS if all_pass else 'FAIL ' + FAIL}")
    print(f"{'='*65}\n")

    if args.save_json:
        out = Path(args.save_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"[저장] {out}")


if __name__ == "__main__":
    main()
