"""
top-view 카메라 → 좌석 점유 감지 → 점유 좌석 footprint → ROS2 topic publish
수신 대상: 핑키프로  /occupied_seat_footprints

실행 전 양쪽 머신에서 동일하게 설정:
  export ROS_DOMAIN_ID=30

실행:
  python3 ros2_position.py
"""

import os
import sys
import json

import cv2
import numpy as np
import yaml

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseArray, Pose

from ultralytics import YOLO

# =========================
# 설정
# =========================
BASE          = "/home/addinedu/perspective"
MAP_YAML_PATH = os.path.join(BASE, "map/mapgood.yaml")
MAP_PGM_PATH  = os.path.join(BASE, "map/mapgood.pgm")
H_PATH        = os.path.join(BASE, "result/H.npy")

MODEL_PATH    = "/home/addinedu/detection/pose_detection/seat_detection/runs/detect/train/weights/best.pt"
ROI_SAVE_PATH = "/home/addinedu/detection/pose_detection/seat_detection/seat_roi_config.json"

CAMERA_ID     = "/dev/video2"
CONF_THRES    = 0.5
IMG_SIZE      = 640
ROI_SIZE      = 50
PUBLISH_HZ    = 5

TOPIC_NAME    = "/occupied_seat_footprints"

DEFAULT_ROI_CENTERS = {
    1: [250, 180],
    2: [500, 180],
    3: [250, 380],
    4: [500, 380],
}


# =========================
# 유틸
# =========================
def load_map_metadata(yaml_path):
    with open(yaml_path, "r") as f:
        info = yaml.safe_load(f)
    return float(info["resolution"]), info["origin"]


def get_map_height(pgm_path):
    img = cv2.imread(pgm_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"map pgm 읽기 실패: {pgm_path}")
    return img.shape[0]


def pixel_to_map(px, py, H, map_img_height, resolution, origin):
    pt = np.array([[[px, py]]], dtype=np.float32)
    mp = cv2.perspectiveTransform(pt, H)[0][0]
    ox, oy, _ = origin
    x = ox + mp[0] * resolution
    y = oy + (map_img_height - mp[1]) * resolution
    return float(x), float(y)


def load_roi_centers(path):
    if not os.path.exists(path):
        return DEFAULT_ROI_CENTERS.copy()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        centers = data.get("roi_centers", None)
        if centers:
            return {int(k): v for k, v in centers.items()}
    except Exception:
        pass
    return DEFAULT_ROI_CENTERS.copy()


def make_square_roi(cx, cy, size, frame_w, frame_h):
    side = min(size, frame_w, frame_h)
    x = max(0, int(cx - side / 2))
    y = max(0, int(cy - side / 2))
    if x + side > frame_w:
        x = frame_w - side
    if y + side > frame_h:
        y = frame_h - side
    return int(x), int(y), int(side), int(side)


def normalize_name(name):
    return name.lower().strip().replace("_", " ").replace("-", " ")


def is_occupied(name):
    n = normalize_name(name)
    return any(k in n for k in ["occupy", "occupied", "sit", "sitting"])


def predict_seat(model, crop):
    if crop.size == 0:
        return False

    results = model.predict(source=crop, conf=CONF_THRES, imgsz=IMG_SIZE, verbose=False)
    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return False

    best_conf, best_name = -1.0, ""
    for box in boxes:
        conf = float(box.conf[0].item())
        if conf > best_conf:
            best_conf = conf
            best_name = model.names[int(box.cls[0].item())]

    return is_occupied(best_name)


# =========================
# ROS2 노드
# =========================
class OccupiedSeatPublisher(Node):
    def __init__(self):
        super().__init__("occupied_seat_publisher")

        for path, label in [
            (MAP_YAML_PATH, "map yaml"),
            (MAP_PGM_PATH,  "map pgm"),
            (H_PATH,        "H.npy"),
            (MODEL_PATH,    "YOLO model"),
        ]:
            if not os.path.exists(path):
                self.get_logger().error(f"{label} 파일 없음: {path}")
                sys.exit(1)

        self.H              = np.load(H_PATH)
        self.resolution, self.origin = load_map_metadata(MAP_YAML_PATH)
        self.map_img_height = get_map_height(MAP_PGM_PATH)

        self.model = YOLO(MODEL_PATH)

        self.cap = cv2.VideoCapture(CAMERA_ID, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.get_logger().error(f"카메라 열기 실패: {CAMERA_ID}")
            sys.exit(1)

        self.roi_centers = load_roi_centers(ROI_SAVE_PATH)

        # 이전 상태 추적 (상태 변화 시에만 publish)
        self.prev_occupied = {1: False, 2: False, 3: False, 4: False}

        self.pub = self.create_publisher(PoseArray, TOPIC_NAME, 10)
        self.create_timer(1.0 / PUBLISH_HZ, self.timer_callback)

        self.get_logger().info("occupied_seat_publisher 시작")
        self.get_logger().info(f"topic   : {TOPIC_NAME}  ({PUBLISH_HZ} Hz)")
        self.get_logger().info("좌석이 점유될 때만 publish됩니다.")

    def timer_callback(self):
        ret, frame = self.cap.read()
        if not ret:
            self.get_logger().warn("프레임 읽기 실패")
            return

        h, w = frame.shape[:2]
        newly_occupied = []

        for seat_id in [1, 2, 3, 4]:
            cx, cy = self.roi_centers[seat_id]
            x, y, rw, rh = make_square_roi(cx, cy, ROI_SIZE, w, h)
            crop = frame[y:y+rh, x:x+rw]

            occupied_now = predict_seat(self.model, crop)

            # 빈 자리 → 점유 로 바뀐 경우
            if occupied_now and not self.prev_occupied[seat_id]:
                # footprint = ROI bbox 하단 중앙
                foot_px = cx
                foot_py = y + rh
                map_x, map_y = pixel_to_map(
                    foot_px, foot_py,
                    self.H, self.map_img_height,
                    self.resolution, self.origin
                )
                newly_occupied.append((seat_id, map_x, map_y))
                self.get_logger().info(
                    f"Seat {seat_id} 점유 감지 → footprint ({map_x:.3f}, {map_y:.3f})"
                )

            elif not occupied_now and self.prev_occupied[seat_id]:
                self.get_logger().info(f"Seat {seat_id} 비어있음")

            self.prev_occupied[seat_id] = occupied_now

        if newly_occupied:
            msg = PoseArray()
            msg.header.stamp    = self.get_clock().now().to_msg()
            msg.header.frame_id = "map"

            for seat_id, mx, my in newly_occupied:
                pose = Pose()
                pose.position.x = mx
                pose.position.y = my
                pose.position.z = 0.0
                # z에 seat_id 인코딩 (필요 시 구독자에서 활용)
                pose.position.z = float(seat_id)
                msg.poses.append(pose)

            self.pub.publish(msg)
            self.get_logger().info(f"{len(newly_occupied)}개 좌석 footprint publish")

    def destroy_node(self):
        self.cap.release()
        super().destroy_node()


# =========================
# 메인
# =========================
def main():
    rclpy.init()
    node = OccupiedSeatPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
