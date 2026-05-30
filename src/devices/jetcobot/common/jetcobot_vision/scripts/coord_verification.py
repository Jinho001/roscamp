#!/usr/bin/env python3
"""
로봇 좌표계 정밀도 및 상자 물리 크기 검증 유틸리티 (coord_verification.py)
========================================================================
이 스크립트는 실물 로봇 환경에서 작업 영역의 z_surface_mm를 정밀 측정하고,
카메라 픽셀 검출값(OBB Width, Height)이 물리적 상자 크기와 일치하는지 검증합니다.

주요 기능:
1. YAML 파일로부터 카메라 intrinsics, handeye matrix, observe_pose 로드.
2. 현재 로봇의 flange pose를 기준으로 camera_link의 Base 좌표계 기준 Z 높이 계산.
3. CV Detect Server로부터 최신 OBB 픽셀 정보(cx, cy, w, h) 취득.
4. 핀홀 카메라 역투영 모델을 이용하여 픽셀 크기를 물리적 mm 크기로 변환.
5. 오차 분석 및 이상적인 z_surface_mm 자동 권장 값 제안.

사용법:
    python3 coord_verification.py --role front_jet --box-w 100 --box-h 60 --box-z 40
"""

import argparse
import sys
import math
import yaml
import requests
import numpy as np
from pathlib import Path

# Euler angle to rotation matrix (ZYX extrinsic)
def euler_deg_to_rotation(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    rx, ry, rz = map(math.radians, [rx_deg, ry_deg, rz_deg])
    Rz = np.array([[math.cos(rz), -math.sin(rz), 0],
                   [math.sin(rz),  math.cos(rz), 0],
                   [0,             0,             1]])
    Ry = np.array([[ math.cos(ry), 0, math.sin(ry)],
                   [0,             1, 0            ],
                   [-math.sin(ry), 0, math.cos(ry)]])
    Rx = np.array([[1, 0,            0           ],
                   [0, math.cos(rx), -math.sin(rx)],
                   [0, math.sin(rx),  math.cos(rx)]])
    return Rz @ Ry @ Rx

def make_T(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3,  3] = t
    return T

def main():
    parser = argparse.ArgumentParser(description="Jetcobot Vision Coordinate & Metric Size Verification Tool")
    parser.add_argument("--role", default="front_jet", choices=["front_jet", "ware_jet"],
                        help="Jetcobot node role (default: front_jet)")
    parser.add_argument("--box-w", type=float, required=True, help="물리적인 상자 가로 크기 (mm)")
    parser.add_argument("--box-h", type=float, required=True, help="물리적인 상자 세로 크기 (mm)")
    parser.add_argument("--box-z", type=float, default=0.0, help="물리적인 상자 높이/두께 (mm)")
    parser.add_argument("--yaml", type=str, default="", help="커스텀 yaml 파일 경로")
    
    args = parser.parse_args()

    # 1. YAML 파일 로드
    script_dir = Path(__file__).parent
    if args.yaml:
        yaml_path = Path(args.yaml)
    else:
        yaml_path = script_dir.parent / "config" / f"vision_params_{args.role}.yaml"

    if not yaml_path.exists():
        print(f"[ERR] YAML 파일을 찾을 수 없습니다: {yaml_path}")
        sys.exit(1)

    print(f"[*] YAML 파라미터 로드 중: {yaml_path.name}")
    with open(yaml_path) as f:
        config = yaml.safe_load(f)

    # 파라미터 파싱
    try:
        ct_params = config["coord_transform_node"]["ros__parameters"]
        handeye_flat = ct_params["handeye_matrix"]
        intrinsics = ct_params["camera_intrinsics"]
        obs_pose = ct_params["observe_pose"]
        z_surface_mm = ct_params["z_surface_mm"]
        
        vpp_params = config.get("vision_pick_place_node", {}).get("ros__parameters", {})
        server_url = vpp_params.get("cv_detect_server_url", "http://192.168.1.121:8081")
    except KeyError as exc:
        print(f"[ERR] YAML에서 필수 파라미터를 읽는 데 실패했습니다: {exc}")
        sys.exit(1)

    T_ee2cam = np.array(handeye_flat).reshape(4, 4)
    fx, fy = intrinsics[0], intrinsics[1]
    cx, cy = intrinsics[2], intrinsics[3]

    print("-" * 60)
    print(f"카메라 내부 파라미터 (Intrinsics): fx={fx:.2f}, fy={fy:.2f}, cx={cx:.2f}, cy={cy:.2f}")
    print(f"작업면 설정 (z_surface_mm): {z_surface_mm} mm")
    print(f"로봇 감시 자세 (observe_pose): {obs_pose}")
    print(f"CV 검출 서버 주소: {server_url}")
    print("-" * 60)

    # 2. 카메라 높이 계산 (Observe Pose 기준)
    # obs_pose = [x, y, z, rx, ry, rz] (mm, deg)
    rx_ee, ry_ee, rz_ee = obs_pose[3], obs_pose[4], obs_pose[5]
    t_ee = np.array(obs_pose[:3]) / 1000.0  # m
    R_ee = euler_deg_to_rotation(rx_ee, ry_ee, rz_ee)
    T_base2ee = make_T(R_ee, t_ee)
    
    # Base to Camera Transform
    T_base2cam = T_base2ee @ T_ee2cam
    z_cam_mm = T_base2cam[2, 3] * 1000.0
    print(f"계산된 camera_link Z 높이: {z_cam_mm:.2f} mm")

    # 3. CV Detect Server로부터 검출 결과 수신
    url = server_url.rstrip("/") + "/latest"
    print(f"[*] CV 검출 서버에서 데이터를 받아오는 중... ({url})")
    try:
        resp = requests.get(url, timeout=3.0)
        if resp.status_code != 200:
            print(f"[ERR] HTTP 에러코드: {resp.status_code}")
            sys.exit(1)
        data = resp.json()
    except Exception as exc:
        print(f"[ERR] CV 검출 서버 연결 실패: {exc}")
        sys.exit(1)

    if not data.get("detected"):
        print("[WARN] 현재 카메라 영상에서 상자가 감지되지 않았습니다.")
        print("  - 조명 및 HSV 설정이 올바른지 확인하세요.")
        sys.exit(1)

    detections = data.get("detections", [])
    print(f"[+] 총 {len(detections)}개의 상자가 검출되었습니다.")

    # 각 검출 상자에 대해 물리 치수 검증 및 피드백 제공
    for idx, d in enumerate(detections):
        w_px, h_px = d["w"], d["h"]
        cx_px, cy_px = d["cx"], d["cy"]
        theta = d["theta"]
        conf = d["confidence"]
        
        # 4. 물리 치수 역투영 계산
        # Z_distance = z_cam_mm - (z_surface_mm + box_z)
        d_z = z_cam_mm - (z_surface_mm + args.box_z)
        
        if d_z <= 0:
            print(f"  [ERR] 박스 상단이 카메라 높이보다 위에 있거나 작업면 설정이 잘못되었습니다. (d_z={d_z:.1f}mm)")
            continue
            
        w_est = w_px * (d_z / fx)
        h_est = h_px * (d_z / fy)
        
        # 가로/세로 매칭 보정 (검출된 w, h 중 물리 가로/세로에 가까운 값으로 비교)
        err_w_opt1 = abs(w_est - args.box_w) + abs(h_est - args.box_h)
        err_w_opt2 = abs(w_est - args.box_h) + abs(h_est - args.box_w)
        
        target_w, target_h = args.box_w, args.box_h
        if err_w_opt2 < err_w_opt1:
            # 90도 회전 매칭
            w_est, h_est = h_est, w_est
            
        err_w = w_est - target_w
        err_h = h_est - target_h
        err_w_pct = (err_w / target_w) * 100
        err_h_pct = (err_h / target_h) * 100

        # 5. 오차 해결을 위한 이상적인 z_surface_mm 추천
        # w_px = target_w * fx / d_z_opt => d_z_opt = target_w * fx / w_px
        d_z_opt_w = target_w * fx / max(w_px, 1.0)
        d_z_opt_h = target_h * fy / max(h_px, 1.0)
        d_z_opt = (d_z_opt_w + d_z_opt_h) / 2.0  # 가로/세로 평균
        
        # z_surface_opt = z_cam_mm - d_z_opt - box_z
        z_surface_opt = z_cam_mm - d_z_opt - args.box_z

        print(f"\n--- Box [{idx}] (conf={conf:.2f}) ---")
        print(f"  픽셀 좌표: cx={cx_px:.1f}, cy={cy_px:.1f} (px)")
        print(f"  픽셀 크기: w={w_px:.1f}, h={h_px:.1f} (px)")
        print(f"  실제 거리(카메라-박스 상단): {d_z:.1f} mm")
        print(f"  추정 물리 크기: {w_est:.2f} x {h_est:.2f} mm")
        print(f"  실제 물리 크기: {target_w:.1f} x {target_h:.1f} mm")
        print(f"  크기 오차:")
        print(f"    가로 오차: {err_w:+.2f} mm ({err_w_pct:+.1f}%)")
        print(f"    세로 오차: {err_h:+.2f} mm ({err_h_pct:+.1f}%)")
        print(f"  [권장 피드백]")
        print(f"    -> 이 상자 기준으로 오차를 0%로 만드는 최적의 작업면 설정:")
        print(f"       z_surface_mm: {z_surface_opt:.1f} mm (현재 설정보다 {z_surface_opt - z_surface_mm:+.1f}mm 조정 필요)")
        print("-" * 50)

if __name__ == "__main__":
    main()
