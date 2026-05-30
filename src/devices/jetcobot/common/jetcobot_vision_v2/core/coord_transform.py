"""
core/coord_transform.py
=======================
카메라 픽셀 좌표 → base_link 3D 좌표 변환 (ROS2 의존성 없음).

수식 출처: jetcobot_vision/coord_transform_node.py (_pixel_to_base, _theta_to_yaw)
핵심 변경: update_pose()가 항상 flange 좌표를 받도록 통일.
           (호출 측에서 MotionController.get_flange_coords()를 사용할 것)
"""

import math
import numpy as np


def _euler_deg_to_R(rx: float, ry: float, rz: float) -> np.ndarray:
    """ZYX extrinsic Euler (deg) → 3×3 rotation matrix."""
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


def _make_T(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3,  3] = t
    return T


class CoordTransformer:
    """
    픽셀 좌표 → base_link 3D 좌표 변환기.

    파라미터:
        handeye_matrix  : T_ee2cam (4×4, row-major 16개 float)
        camera_intrinsics: [fx, fy, cx, cy]
        tcp_offset      : [x, y, z] mm — flange→TCP 오프셋.
                          update_pose()에 TCP 좌표가 들어올 경우 flange로 역산하는 데 사용.
                          None이면 역산 없이 입력값을 flange 좌표로 간주.
    """

    def __init__(self,
                 handeye_matrix: list,
                 camera_intrinsics: list,
                 tcp_offset: list | None = None):
        self._T_ee2cam = np.array(handeye_matrix).reshape(4, 4)
        fx, fy, cx, cy = camera_intrinsics
        self._K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=float)
        self._K_inv = np.linalg.inv(self._K)
        self._tcp_offset = np.array(tcp_offset) / 1000.0 if tcp_offset else None
        self._T_base2cam: np.ndarray | None = None

    def update_pose(self, flange_coords: list) -> None:
        """
        flange 6DoF 좌표 [x mm, y mm, z mm, rx deg, ry deg, rz deg] →
        T_base2cam 갱신.

        주의: 항상 flange 좌표를 넘길 것. TCP 좌표가 있다면
              MotionController.get_flange_coords()로 변환 후 전달.
        """
        t = np.array(flange_coords[:3]) / 1000.0
        R = _euler_deg_to_R(flange_coords[3], flange_coords[4], flange_coords[5])
        T_base2flange = _make_T(R, t)
        self._T_base2cam = T_base2flange @ self._T_ee2cam

    def pixel_to_base(self, cx: float, cy: float,
                      z_surface_mm: float) -> tuple[float, float, float] | None:
        """
        픽셀 (cx, cy) → base_link 3D 좌표 (mm). Ray-Plane 교점.

        z_surface_mm: base_link 기준 작업면 Z 높이 (상자 상단면).
        반환: (x_mm, y_mm, z_mm) 또는 None (교점 없음).
        """
        if self._T_base2cam is None:
            return None

        ray_c = self._K_inv @ np.array([cx, cy, 1.0])
        R = self._T_base2cam[:3, :3]
        origin = self._T_base2cam[:3, 3]
        ray_b = R @ ray_c

        if abs(ray_b[2]) < 1e-9:
            return None

        z_surface_m = z_surface_mm / 1000.0
        t = (z_surface_m - origin[2]) / ray_b[2]
        if t < 0.0:
            return None

        pt = origin + t * ray_b
        return pt[0] * 1000.0, pt[1] * 1000.0, pt[2] * 1000.0

    def theta_to_yaw(self, theta_cam: float) -> float | None:
        """
        카메라 OBB 장축 각도 (rad) → base_link yaw (deg).
        상자 180° 대칭은 호출 측에서 처리.
        """
        if self._T_base2cam is None:
            return None

        d_cam = np.array([math.cos(theta_cam), math.sin(theta_cam), 0.0])
        d_base = self._T_base2cam[:3, :3] @ d_cam
        return math.degrees(math.atan2(d_base[1], d_base[0]))

    @property
    def T_base2cam(self) -> np.ndarray | None:
        return self._T_base2cam

    @property
    def camera_z_mm(self) -> float | None:
        if self._T_base2cam is None:
            return None
        return self._T_base2cam[2, 3] * 1000.0
