#!/usr/bin/env python3
"""
CV 파이프라인 단계별 검증 도구 (pipeline_verify.py)
====================================================
파이프라인 각 단계를 독립적으로 측정하여 포트폴리오 수치 근거를 확보한다.

검증 단계:
  Step A — 픽셀 스케일: OBB w/h 픽셀 → mm 역산, 실제 상자 크기 대비 오차
  Step B — 중심 좌표 절대 오차: (cx, cy) → base_link (x, y) 변환 후 실측 위치와 비교
  Step C — Yaw 방향: 상자를 직접 회전시켜 측정 yaw vs 입력 expected yaw 오차

사전 조건:
  - cv_detect_server.py 실행 중
  - Step B: --real-x, --real-y 로 상자의 실측 위치(base_link 기준 mm) 입력
  - Step C: --expected-yaw 로 현재 상자의 실제 회전각(deg) 입력

사용 예:
  # Step A + B (상자를 base_link 기준 x=150mm, y=0mm 위치에 놓고)
  python3 pipeline_verify.py --yaml ../config/vision_params_front_jet.yaml \\
      --server http://192.168.1.4:8081 --box-w 30 --box-h 20 \\
      --real-x 150 --real-y 0

  # Step C (상자를 45도 회전시켜 놓고)
  python3 pipeline_verify.py --yaml ../config/vision_params_front_jet.yaml \\
      --server http://192.168.1.4:8081 --box-w 30 --box-h 20 \\
      --expected-yaw 45

  # 결과 JSON 저장
  python3 pipeline_verify.py ... --save-json results/verify_20260528.json
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
import yaml

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


# ── 좌표 변환 유틸 (coord_transform_node.py와 동일 수식, 독립 구현) ───────────

def euler_deg_to_R(rx: float, ry: float, rz: float) -> np.ndarray:
    """ZYX extrinsic Euler → 3×3 rotation matrix."""
    rx, ry, rz = map(math.radians, [rx, ry, rz])
    Rz = np.array([[math.cos(rz), -math.sin(rz), 0],
                   [math.sin(rz),  math.cos(rz), 0],
                   [0,             0,             1]])
    Ry = np.array([[ math.cos(ry), 0, math.sin(ry)],
                   [0,             1, 0            ],
                   [-math.sin(ry), 0, math.cos(ry)]])
    Rx = np.array([[1, 0,            0            ],
                   [0, math.cos(rx), -math.sin(rx)],
                   [0, math.sin(rx),  math.cos(rx)]])
    return Rz @ Ry @ Rx


def make_T(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3,  3] = t
    return T


def build_T_base2cam(obs_pose: list, T_ee2cam: np.ndarray,
                     tcp_offset_mm: list | None = None) -> np.ndarray:
    """observe_pose [x_mm, y_mm, z_mm, rx, ry, rz] + T_ee2cam → T_base2cam.

    obs_pose가 TCP 좌표일 경우 tcp_offset_mm([x,y,z] mm)을 넘기면
    T_base2tcp → T_base2flange로 역변환 후 T_ee2cam을 적용한다.
    T_ee2cam은 flange 기준 핸드아이 캘리브 결과이므로 반드시 flange 기준으로 곱해야 한다.
    """
    t_tcp = np.array(obs_pose[:3]) / 1000.0
    R_tcp = euler_deg_to_R(obs_pose[3], obs_pose[4], obs_pose[5])
    T_base2tcp = make_T(R_tcp, t_tcp)

    if tcp_offset_mm:
        # T_flange2tcp: 순수 평행이동 (그리퍼 TCP는 회전 없이 Z축 방향으로 돌출)
        t_offset = np.array(tcp_offset_mm) / 1000.0
        T_flange2tcp = make_T(np.eye(3), t_offset)
        T_tcp2flange = np.linalg.inv(T_flange2tcp)
        T_base2flange = T_base2tcp @ T_tcp2flange
    else:
        T_base2flange = T_base2tcp

    return T_base2flange @ T_ee2cam


def pixel_to_base(px: float, py: float, K_inv: np.ndarray,
                  T_base2cam: np.ndarray, z_surface_m: float) -> np.ndarray | None:
    """픽셀 (px, py) → base_link 3D 좌표 (m). Ray-Plane 교점."""
    ray_c  = K_inv @ np.array([px, py, 1.0])
    R      = T_base2cam[:3, :3]
    origin = T_base2cam[:3, 3]
    ray_b  = R @ ray_c

    if abs(ray_b[2]) < 1e-9:
        return None
    t = (z_surface_m - origin[2]) / ray_b[2]
    if t < 0.0:
        return None
    return origin + t * ray_b


def theta_to_yaw(theta_cam: float, T_base2cam: np.ndarray) -> float:
    """카메라 OBB 장축 각도(rad) → base_link yaw (deg)."""
    d_cam  = np.array([math.cos(theta_cam), math.sin(theta_cam), 0.0])
    d_base = T_base2cam[:3, :3] @ d_cam
    return math.degrees(math.atan2(d_base[1], d_base[0]))


# ── 파라미터 로드 ─────────────────────────────────────────────────────────────

def load_params(yaml_path: Path) -> dict:
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)
    ct = cfg["coord_transform_node"]["ros__parameters"]
    vpp = cfg.get("vision_pick_place_node", {}).get("ros__parameters", {})
    return {
        "handeye_matrix":    ct["handeye_matrix"],
        "camera_intrinsics": ct["camera_intrinsics"],
        "observe_pose":      ct["observe_pose"],
        "z_surface_mm":      ct["z_surface_mm"],
        "tcp_offset":        vpp.get("tcp_offset", None),  # observe_pose가 TCP 좌표일 때 flange 역산
        "server_url":        vpp.get("cv_detect_server_url", "http://192.168.1.4:8081"),
    }


def fetch_detection(server_url: str, retries: int = 3) -> dict | None:
    url = server_url.rstrip("/") + "/latest"
    for i in range(retries):
        try:
            r = requests.get(url, timeout=3.0)
            data = r.json()
            if data.get("detected"):
                return data
        except Exception as e:
            print(f"  [재시도 {i+1}/{retries}] 서버 응답 실패: {e}")
        time.sleep(0.5)
    return None


# ── Step A: 픽셀 스케일 검증 ─────────────────────────────────────────────────

def verify_pixel_scale(det: dict, params: dict, box_w: float, box_h: float,
                       box_z: float = 0.0) -> dict:
    """
    OBB w/h 픽셀값을 물리 mm로 역산해 실제 상자 크기와 비교.
    수식: W_real = w_px * d_z / fx  (핀홀 투영 역산)
    """
    fx, fy = params["camera_intrinsics"][0], params["camera_intrinsics"][1]
    T_ee2cam   = np.array(params["handeye_matrix"]).reshape(4, 4)
    T_base2cam = build_T_base2cam(params["observe_pose"], T_ee2cam, params.get("tcp_offset"))
    z_cam_mm   = T_base2cam[2, 3] * 1000.0
    z_surf_mm  = params["z_surface_mm"]

    w_px = det["w"]
    h_px = det["h"]
    d_z  = z_cam_mm - (z_surf_mm + box_z)

    if d_z <= 0:
        return {"error": f"d_z={d_z:.1f}mm — z_surface_mm 설정 오류"}

    w_est = w_px * d_z / fx
    h_est = h_px * d_z / fy

    # 90도 회전 매칭 (w/h 중 어느 쪽이 실제 가로인지)
    if abs(w_est - box_h) + abs(h_est - box_w) < abs(w_est - box_w) + abs(h_est - box_h):
        w_est, h_est = h_est, w_est

    err_w = w_est - box_w
    err_h = h_est - box_h

    # 오차 0이 되는 최적 z_surface_mm 역산
    d_z_opt = (box_w * fx / max(w_px, 1) + box_h * fy / max(h_px, 1)) / 2.0
    z_surf_opt = z_cam_mm - d_z_opt - box_z

    return {
        "w_px": w_px, "h_px": h_px,
        "d_z_mm": d_z,
        "w_estimated_mm": round(w_est, 2),
        "h_estimated_mm": round(h_est, 2),
        "target_w_mm": box_w, "target_h_mm": box_h,
        "error_w_mm": round(err_w, 2),
        "error_h_mm": round(err_h, 2),
        "z_cam_mm": round(z_cam_mm, 2),
        "z_surface_current_mm": z_surf_mm,
        "z_surface_optimal_mm": round(z_surf_opt, 1),
        "pass": abs(err_w) < 3.0 and abs(err_h) < 3.0,  # 기준: ±3mm
    }


# ── Step B: 중심 좌표 절대 오차 검증 ─────────────────────────────────────────

def verify_center_position(det: dict, params: dict,
                           real_x_mm: float, real_y_mm: float) -> dict:
    """
    (cx, cy) → base_link (x, y) 변환 결과를 줄자 실측 위치와 비교.
    수식: Ray-Plane 교점 (coord_transform_node._pixel_to_base 동일)
    """
    fx, fy = params["camera_intrinsics"][0], params["camera_intrinsics"][1]
    icx, icy = params["camera_intrinsics"][2], params["camera_intrinsics"][3]
    K_inv = np.linalg.inv(np.array([[fx, 0, icx], [0, fy, icy], [0, 0, 1]]))

    T_ee2cam   = np.array(params["handeye_matrix"]).reshape(4, 4)
    T_base2cam = build_T_base2cam(params["observe_pose"], T_ee2cam, params.get("tcp_offset"))
    z_surf_m   = params["z_surface_mm"] / 1000.0

    pt = pixel_to_base(det["cx"], det["cy"], K_inv, T_base2cam, z_surf_m)
    if pt is None:
        return {"error": "Ray-Plane 교점 계산 실패 (ray_b.z ≈ 0 또는 교점이 카메라 뒤)"}

    x_est_mm = pt[0] * 1000.0
    y_est_mm = pt[1] * 1000.0
    err_x    = x_est_mm - real_x_mm
    err_y    = y_est_mm - real_y_mm
    total    = math.sqrt(err_x**2 + err_y**2)

    return {
        "cx_px": det["cx"], "cy_px": det["cy"],
        "x_estimated_mm": round(x_est_mm, 2),
        "y_estimated_mm": round(y_est_mm, 2),
        "x_real_mm": real_x_mm,
        "y_real_mm": real_y_mm,
        "error_x_mm": round(err_x, 2),
        "error_y_mm": round(err_y, 2),
        "total_error_mm": round(total, 2),
        "pass": total <= 5.0,  # 오차 예산: ±5mm (그리퍼 여유 10mm의 절반)
    }


# ── Step C: Yaw 방향 검증 ────────────────────────────────────────────────────

def verify_yaw(det: dict, params: dict, expected_yaw_deg: float) -> dict:
    """
    OBB theta → base_link yaw 변환 결과를 사용자가 입력한 실제 각도와 비교.
    수식: d_base = R @ [cosθ, sinθ, 0]; yaw = atan2(d_y, d_x)
    상자는 180° 대칭이므로 ±180° 차이는 동일한 파지 방향으로 처리.
    """
    T_ee2cam   = np.array(params["handeye_matrix"]).reshape(4, 4)
    T_base2cam = build_T_base2cam(params["observe_pose"], T_ee2cam, params.get("tcp_offset"))

    measured_yaw = theta_to_yaw(det["theta"], T_base2cam)

    # 180° 대칭 정규화: 오차가 ±90° 이하인 등가 각도 선택
    err = measured_yaw - expected_yaw_deg
    while err > 90.0:
        err -= 180.0
    while err < -90.0:
        err += 180.0

    return {
        "theta_cam_rad": round(det["theta"], 4),
        "measured_yaw_deg": round(measured_yaw, 2),
        "expected_yaw_deg": expected_yaw_deg,
        "error_deg": round(err, 2),
        "pass": abs(err) <= 5.0,  # 기준: ±5도
    }


# ── 출력 헬퍼 ────────────────────────────────────────────────────────────────

def _badge(passed: bool) -> str:
    return PASS if passed else FAIL


def print_step_a(r: dict) -> None:
    print("\n╔══ Step A: 픽셀 스케일 검증 ══════════════════════════════════╗")
    if "error" in r:
        print(f"  {FAIL} 오류: {r['error']}")
        return
    print(f"  OBB 검출:    w={r['w_px']:.0f}px  h={r['h_px']:.0f}px")
    print(f"  카메라 높이: {r['z_cam_mm']:.1f}mm  |  d_z(카메라→상자 상단): {r['d_z_mm']:.1f}mm")
    print(f"  추정 크기:   {r['w_estimated_mm']}mm × {r['h_estimated_mm']}mm")
    print(f"  실제 크기:   {r['target_w_mm']}mm × {r['target_h_mm']}mm")
    print(f"  오차:        가로 {r['error_w_mm']:+.2f}mm  /  세로 {r['error_h_mm']:+.2f}mm"
          f"  {_badge(r['pass'])}")
    if not r["pass"] or abs(r["z_surface_optimal_mm"] - r["z_surface_current_mm"]) > 1.0:
        print(f"  {WARN}  권장 z_surface_mm: {r['z_surface_optimal_mm']}mm"
              f"  (현재 {r['z_surface_current_mm']}mm, 조정 {r['z_surface_optimal_mm']-r['z_surface_current_mm']:+.1f}mm)")
    print("╚═══════════════════════════════════════════════════════════════╝")


def print_step_b(r: dict) -> None:
    print("\n╔══ Step B: 중심 좌표 절대 오차 ═══════════════════════════════╗")
    if "error" in r:
        print(f"  {FAIL} 오류: {r['error']}")
        return
    print(f"  OBB 중심:    cx={r['cx_px']:.1f}px  cy={r['cy_px']:.1f}px")
    print(f"  변환 결과:   x={r['x_estimated_mm']}mm  y={r['y_estimated_mm']}mm")
    print(f"  실측 위치:   x={r['x_real_mm']}mm  y={r['y_real_mm']}mm")
    print(f"  오차:        Δx={r['error_x_mm']:+.2f}mm  Δy={r['error_y_mm']:+.2f}mm"
          f"  총={r['total_error_mm']:.2f}mm  (기준 ≤5mm)  {_badge(r['pass'])}")
    print("╚═══════════════════════════════════════════════════════════════╝")


def print_step_c(r: dict) -> None:
    print("\n╔══ Step C: Yaw 방향 검증 ══════════════════════════════════════╗")
    if "error" in r:
        print(f"  {FAIL} 오류: {r['error']}")
        return
    print(f"  theta_cam:   {math.degrees(r['theta_cam_rad']):.1f}°  (카메라 픽셀 좌표계)")
    print(f"  측정 yaw:    {r['measured_yaw_deg']}°  (base_link 변환 후)")
    print(f"  실제 yaw:    {r['expected_yaw_deg']}°")
    print(f"  오차:        {r['error_deg']:+.2f}°  (기준 ≤5°)  {_badge(r['pass'])}")
    print("╚═══════════════════════════════════════════════════════════════╝")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CV 파이프라인 단계별 검증 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--yaml", required=True,
                        help="vision_params_*.yaml 경로 (예: ../config/vision_params_front_jet.yaml)")
    parser.add_argument("--server", default="http://192.168.1.4:8081",
                        help="cv_detect_server URL (기본: http://192.168.1.4:8081)")
    parser.add_argument("--box-w",  type=float, required=True, help="상자 실제 가로 (mm)")
    parser.add_argument("--box-h",  type=float, required=True, help="상자 실제 세로 (mm)")
    parser.add_argument("--box-z",  type=float, default=0.0,   help="상자 두께 (mm, 기본 0)")
    parser.add_argument("--real-x", type=float, default=None,
                        help="[Step B] 상자 실측 x 위치 — base_link 기준 mm")
    parser.add_argument("--real-y", type=float, default=None,
                        help="[Step B] 상자 실측 y 위치 — base_link 기준 mm")
    parser.add_argument("--expected-yaw", type=float, default=None,
                        help="[Step C] 상자 실제 회전각 (deg, 예: 0 / 45 / 90 / 180)")
    parser.add_argument("--save-json", type=str, default=None,
                        help="결과를 JSON 파일로 저장 (예: results/verify_20260528.json)")
    args = parser.parse_args()

    # 파라미터 로드
    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        print(f"{FAIL} YAML 파일 없음: {yaml_path}")
        sys.exit(1)

    try:
        params = load_params(yaml_path)
    except KeyError as e:
        print(f"{FAIL} YAML 파싱 오류 — 필수 키 없음: {e}")
        sys.exit(1)

    server_url = args.server or params["server_url"]

    print("=" * 65)
    print("  CV 파이프라인 단계별 검증")
    print(f"  YAML   : {yaml_path.name}")
    print(f"  서버   : {server_url}")
    print(f"  상자   : {args.box_w}×{args.box_h}mm  두께={args.box_z}mm")
    print(f"  시각   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # 검출 데이터 수신
    print(f"\n[*] cv_detect_server 검출 대기 중... ({server_url})")
    data = fetch_detection(server_url)
    if data is None:
        print(f"{FAIL} 상자가 감지되지 않았습니다. cv_detect_server가 실행 중인지 확인하세요.")
        sys.exit(1)

    detections = data.get("detections", [])
    print(f"[+] {len(detections)}개 상자 검출됨 (confidence 기준 첫 번째 사용)\n")

    # confidence 최고 상자 선택
    best = max(detections, key=lambda d: d.get("confidence", 0))

    results = {
        "timestamp": datetime.now().isoformat(),
        "yaml": str(yaml_path),
        "box_w_mm": args.box_w,
        "box_h_mm": args.box_h,
        "detection": best,
    }

    # Step A: 항상 실행
    r_a = verify_pixel_scale(best, params, args.box_w, args.box_h, args.box_z)
    print_step_a(r_a)
    results["step_a"] = r_a

    # Step B: --real-x / --real-y 입력 시 실행
    if args.real_x is not None and args.real_y is not None:
        r_b = verify_center_position(best, params, args.real_x, args.real_y)
        print_step_b(r_b)
        results["step_b"] = r_b
    else:
        print(f"\n  [Step B 스킵]  --real-x / --real-y 를 입력하면 중심 좌표 절대 오차를 측정합니다.")

    # Step C: --expected-yaw 입력 시 실행
    if args.expected_yaw is not None:
        r_c = verify_yaw(best, params, args.expected_yaw)
        print_step_c(r_c)
        results["step_c"] = r_c
    else:
        print(f"  [Step C 스킵]  --expected-yaw 를 입력하면 Yaw 방향 오차를 측정합니다.")

    # 종합 판정
    all_pass = all([
        results.get("step_a", {}).get("pass", True),
        results.get("step_b", {}).get("pass", True),
        results.get("step_c", {}).get("pass", True),
    ])
    print(f"\n{'='*65}")
    print(f"  종합 판정: {'PASS ' + PASS if all_pass else 'FAIL ' + FAIL}")
    print(f"{'='*65}\n")

    # JSON 저장
    if args.save_json:
        out = Path(args.save_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"[저장] {out}")


if __name__ == "__main__":
    main()
