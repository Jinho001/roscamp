# import cv2
# import numpy as np
# from ultralytics import YOLO

# MODEL_PATH = "yolo26n-pose.pt"
# CONF_THRES = 0.5
# CAMERA_ID = "/dev/video0" #"/dev/video4"   # USB 카메라 사용

# LEFT_WRIST = 9
# RIGHT_WRIST = 10

# MIN_MOVE_PX = 18
# CENTER_MARGIN_PX = 12
# SMOOTH_WINDOW = 5

# HAND_MODE = "right"


# class WaveCounter:
#     def __init__(self):
#         self.wave_count = 0
#         self.prev_x = None
#         self.x_history = []
#         self.last_side = None
#         self.crossed_once = False
#         self.center_x = None

#     def smooth_x(self, x):
#         self.x_history.append(x)
#         if len(self.x_history) > SMOOTH_WINDOW:
#             self.x_history.pop(0)
#         return float(np.mean(self.x_history))

#     def update(self, wrist_x):
#         if wrist_x is None:
#             return self.wave_count, "손목 미검출"

#         wrist_x = self.smooth_x(wrist_x)

#         if self.center_x is None:
#             self.center_x = wrist_x
#             self.prev_x = wrist_x
#             return self.wave_count, "초기화"

#         move = wrist_x - self.prev_x
#         self.prev_x = wrist_x

#         if abs(move) < MIN_MOVE_PX:
#             return self.wave_count, "미세 움직임"

#         if wrist_x < self.center_x - CENTER_MARGIN_PX:
#             current_side = "left"
#         elif wrist_x > self.center_x + CENTER_MARGIN_PX:
#             current_side = "right"
#         else:
#             return self.wave_count, "중앙 영역"

#         debug_text = f"side={current_side}"

#         if self.last_side is None:
#             self.last_side = current_side
#             return self.wave_count, debug_text + " / 시작"

#         if current_side != self.last_side:
#             if not self.crossed_once:
#                 self.crossed_once = True
#                 self.last_side = current_side
#                 return self.wave_count, debug_text + " / 반쪽 흔들기"
#             else:
#                 self.wave_count += 1
#                 self.crossed_once = False
#                 self.last_side = current_side
#                 return self.wave_count, debug_text + " / 1회 카운트"

#         return self.wave_count, debug_text


# def get_best_person_keypoints(result):
#     if result.boxes is None or result.keypoints is None:
#         return None

#     boxes = result.boxes
#     kpts = result.keypoints

#     if boxes.conf is None or len(boxes.conf) == 0:
#         return None

#     confs = boxes.conf.cpu().numpy()
#     best_idx = int(np.argmax(confs))

#     xy = kpts.xy.cpu().numpy()
#     if best_idx >= len(xy):
#         return None

#     return xy[best_idx]


# def select_wrist_xy(person_kpts, hand_mode="right"):
#     if person_kpts is None:
#         return None, None

#     left_xy = person_kpts[LEFT_WRIST]
#     right_xy = person_kpts[RIGHT_WRIST]

#     lx, ly = float(left_xy[0]), float(left_xy[1])
#     rx, ry = float(right_xy[0]), float(right_xy[1])

#     left_valid = not (lx < 1 and ly < 1)
#     right_valid = not (rx < 1 and ry < 1)

#     if hand_mode == "left":
#         return (lx, ly) if left_valid else (None, None)

#     if hand_mode == "right":
#         return (rx, ry) if right_valid else (None, None)

#     if right_valid:
#         return rx, ry
#     if left_valid:
#         return lx, ly
#     return None, None


# def main():
#     model = YOLO(MODEL_PATH)
#     counter = WaveCounter()

#     cap = cv2.VideoCapture(CAMERA_ID, cv2.CAP_V4L2)
#     if not cap.isOpened():
#         raise RuntimeError(f"카메라를 열 수 없습니다: {CAMERA_ID}")

#     cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
#     cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
#     cap.set(cv2.CAP_PROP_FPS, 30)

#     while True:
#         ret, frame = cap.read()
#         if not ret:
#             print("프레임을 읽지 못했습니다.")
#             break

#         results = model.predict(
#             source=frame,
#             conf=CONF_THRES,
#             verbose=False
#         )

#         annotated = frame.copy()
#         wrist_x, wrist_y = None, None
#         debug_text = "사람 미검출"

#         if len(results) > 0:
#             result = results[0]
#             annotated = result.plot()

#             person_kpts = get_best_person_keypoints(result)
#             wrist_x, wrist_y = select_wrist_xy(person_kpts, HAND_MODE)

#             if wrist_x is not None:
#                 _, debug_text = counter.update(wrist_x)

#                 cv2.circle(annotated, (int(wrist_x), int(wrist_y)), 8, (0, 255, 255), -1)
#                 cv2.putText(
#                     annotated,
#                     f"Wrist: ({int(wrist_x)}, {int(wrist_y)})",
#                     (10, 90),
#                     cv2.FONT_HERSHEY_SIMPLEX,
#                     0.7,
#                     (0, 255, 255),
#                     2
#                 )
#             else:
#                 debug_text = "손목 미검출"

#         cv2.putText(
#             annotated,
#             f"Wave Count: {counter.wave_count}",
#             (10, 35),
#             cv2.FONT_HERSHEY_SIMPLEX,
#             1.0,
#             (0, 255, 0),
#             2
#         )

#         cv2.putText(
#             annotated,
#             f"Status: {debug_text}",
#             (10, 65),
#             cv2.FONT_HERSHEY_SIMPLEX,
#             0.7,
#             (255, 255, 0),
#             2
#         )

#         cv2.imshow("Hand Wave Counter", annotated)

#         key = cv2.waitKey(1) & 0xFF
#         if key == ord("q"):
#             break
#         elif key == ord("r"):
#             counter = WaveCounter()
#             print("카운터 리셋")

#     cap.release()
#     cv2.destroyAllWindows()


# if __name__ == "__main__":
#     main()
import cv2
import os
import json
import time
import math
import numpy as np
from ultralytics import YOLO

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped

# =========================
# 설정
# =========================
CAMERA_ID = "/dev/video4"# "/dev/video4"
MODEL_PATH = "yolo26n-pose.pt"

ROI_SAVE_PATH = "/home/addinedu/detection/pose_detection/roi_pose_config.json"

CONF_THRES = 0.5
IMG_SIZE = 640

ROI_SIZE = 260
ROI_CENTER = [640, 360]

WINDOW_NAME = "ROI Pose Goal"

# COCO keypoint index
NOSE = 0
LEFT_SHOULDER = 5
RIGHT_SHOULDER = 6
LEFT_HIP = 11
RIGHT_HIP = 12
LEFT_WRIST = 9
RIGHT_WRIST = 10
LEFT_ANKLE = 15
RIGHT_ANKLE = 16

HAND_MODE = "both"          # "left", "right", "both"
DIST_THRESHOLD_PX = 90      # 몸 중심과 손 거리 기준
TRIGGER_FRAMES = 5          # 연속 5프레임 이상이면 goal 전송
GOAL_COOLDOWN_SEC = 3.0

# perspective 결과 사용 여부
USE_PERSPECTIVE = False
H_PATH = "/home/addinedu/perspective/result/H.npy"
MAP_YAML_PATH = "/home/addinedu/perspective/map/mapgood.yaml"


# =========================
# ROS2 Goal Publisher
# =========================
class GoalPublisher(Node):
    def __init__(self):
        super().__init__("roi_pose_goal_publisher")
        self.pub = self.create_publisher(PoseStamped, "/goal_pose", 10)

    def publish_goal(self, x, y):
        msg = PoseStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()

        msg.pose.position.x = float(x)
        msg.pose.position.y = float(y)
        msg.pose.position.z = 0.0

        msg.pose.orientation.w = 1.0

        self.pub.publish(msg)
        self.get_logger().info(f"Goal published: x={x:.3f}, y={y:.3f}")


# =========================
# ROI 유틸
# =========================
def make_square_roi_from_center(cx, cy, size, frame_w, frame_h):
    side = min(size, frame_w, frame_h)

    x = int(cx - side / 2)
    y = int(cy - side / 2)

    x = max(0, min(x, frame_w - side))
    y = max(0, min(y, frame_h - side))

    return x, y, side, side


def save_roi(path, center, size):
    data = {
        "roi_center": center,
        "roi_size": size,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_roi(path):
    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("roi_center", None), data.get("roi_size", None)


def make_mouse_callback(state):
    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            state["roi_center"] = [x, y]
            print(f"ROI 중심 지정: {state['roi_center']}")
    return on_mouse


# =========================
# Pose 유틸
# =========================
def valid_point(p):
    if p is None:
        return False
    x, y = float(p[0]), float(p[1])
    return not (x < 1 and y < 1)


def get_best_person_keypoints(result):
    if result.boxes is None or result.keypoints is None:
        return None

    if result.boxes.conf is None or len(result.boxes.conf) == 0:
        return None

    confs = result.boxes.conf.cpu().numpy()
    best_idx = int(np.argmax(confs))

    xy = result.keypoints.xy.cpu().numpy()
    if best_idx >= len(xy):
        return None

    return xy[best_idx]


def get_body_center(kpts):
    candidates = []

    for idx in [LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_HIP, RIGHT_HIP]:
        p = kpts[idx]
        if valid_point(p):
            candidates.append(p)

    if len(candidates) == 0:
        return None

    pts = np.array(candidates, dtype=np.float32)
    cx, cy = np.mean(pts, axis=0)
    return float(cx), float(cy)


def get_hand_points(kpts, mode="both"):
    hands = []

    if mode in ["left", "both"]:
        p = kpts[LEFT_WRIST]
        if valid_point(p):
            hands.append(("left", float(p[0]), float(p[1])))

    if mode in ["right", "both"]:
        p = kpts[RIGHT_WRIST]
        if valid_point(p):
            hands.append(("right", float(p[0]), float(p[1])))

    return hands


def get_foot_point(kpts, fallback_xy):
    ankles = []

    for idx in [LEFT_ANKLE, RIGHT_ANKLE]:
        p = kpts[idx]
        if valid_point(p):
            ankles.append(p)

    if len(ankles) > 0:
        pts = np.array(ankles, dtype=np.float32)
        fx, fy = np.mean(pts, axis=0)
        return float(fx), float(fy)

    return fallback_xy


def calc_distance(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


# =========================
# 좌표 변환
# =========================
def image_point_to_map(x_img, y_img):
    """
    지금은 기본적으로 이미지 좌표를 그대로 반환.
    USE_PERSPECTIVE=True일 때 H.npy + map 변환을 연결해서 사용.
    """
    if not USE_PERSPECTIVE:
        return x_img, y_img

    H = np.load(H_PATH)

    src = np.array([[[x_img, y_img]]], dtype=np.float32)
    dst = cv2.perspectiveTransform(src, H)

    map_px = float(dst[0][0][0])
    map_py = float(dst[0][0][1])

    # 여기서 map.yaml의 resolution/origin을 적용해야 함.
    # 사용자의 map yaml 구조에 맞춰 추가 필요.
    return map_px, map_py


# =========================
# 메인
# =========================
def main():
    rclpy.init()
    goal_node = GoalPublisher()

    model = YOLO(MODEL_PATH)

    cap = cv2.VideoCapture(CAMERA_ID, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"카메라를 열 수 없습니다: {CAMERA_ID}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)

    state = {
        "roi_center": ROI_CENTER,
        "roi_size": ROI_SIZE,
    }

    loaded = load_roi(ROI_SAVE_PATH)
    if loaded is not None:
        loaded_center, loaded_size = loaded
        if loaded_center is not None:
            state["roi_center"] = loaded_center
        if loaded_size is not None:
            state["roi_size"] = loaded_size

    cv2.namedWindow(WINDOW_NAME)
    cv2.setMouseCallback(WINDOW_NAME, make_mouse_callback(state))

    trigger_count = 0
    last_goal_time = 0.0

    print("ROI Pose Goal 시작")
    print("마우스 왼쪽 클릭: ROI 중심 지정")
    print("s: ROI 저장")
    print("+: ROI 크기 증가")
    print("-: ROI 크기 감소")
    print("q: 종료")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("프레임 읽기 실패")
            break

        display = frame.copy()
        h, w = frame.shape[:2]

        cx, cy = state["roi_center"]
        roi_size = state["roi_size"]

        x, y, rw, rh = make_square_roi_from_center(cx, cy, roi_size, w, h)
        crop = frame[y:y + rh, x:x + rw]

        status_text = "NO POSE"
        max_dist = 0.0
        target_global = None

        if crop.size > 0:
            results = model.predict(
                source=crop,
                conf=CONF_THRES,
                imgsz=IMG_SIZE,
                verbose=False
            )

            result = results[0]
            kpts = get_best_person_keypoints(result)

            if kpts is not None:
                body_center = get_body_center(kpts)
                hands = get_hand_points(kpts, HAND_MODE)

                if body_center is not None and len(hands) > 0:
                    bx, by = body_center
                    cv2.circle(display, (int(x + bx), int(y + by)), 7, (255, 255, 0), -1)

                    far_hand = None
                    for hand_name, hx, hy in hands:
                        dist = calc_distance((bx, by), (hx, hy))

                        if dist > max_dist:
                            max_dist = dist
                            far_hand = (hand_name, hx, hy)

                        cv2.circle(display, (int(x + hx), int(y + hy)), 7, (0, 255, 255), -1)
                        cv2.line(
                            display,
                            (int(x + bx), int(y + by)),
                            (int(x + hx), int(y + hy)),
                            (0, 255, 255),
                            2
                        )

                    if far_hand is not None and max_dist >= DIST_THRESHOLD_PX:
                        status_text = f"HAND FAR {max_dist:.1f}px"
                        trigger_count += 1

                        # 이동 좌표는 손이 아니라 사람/피규어 발 위치 기준
                        fallback = (rw / 2, rh)
                        fx, fy = get_foot_point(kpts, fallback)

                        global_fx = x + fx
                        global_fy = y + fy
                        target_global = (global_fx, global_fy)

                        cv2.circle(display, (int(global_fx), int(global_fy)), 9, (0, 0, 255), -1)

                    else:
                        status_text = f"HAND CLOSE {max_dist:.1f}px"
                        trigger_count = 0
                else:
                    trigger_count = 0
            else:
                trigger_count = 0

        now = time.time()

        if target_global is not None:
            if trigger_count >= TRIGGER_FRAMES and now - last_goal_time > GOAL_COOLDOWN_SEC:
                gx, gy = image_point_to_map(target_global[0], target_global[1])

                goal_node.publish_goal(gx, gy)

                last_goal_time = now
                trigger_count = 0

        # ROI 표시
        cv2.rectangle(display, (x, y), (x + rw, y + rh), (0, 255, 0), 2)
        cv2.circle(display, (cx, cy), 4, (0, 255, 0), -1)

        cv2.putText(display, f"Status: {status_text}", (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.putText(display, f"Dist: {max_dist:.1f}px / Threshold: {DIST_THRESHOLD_PX}px", (20, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        cv2.putText(display, f"Trigger: {trigger_count}/{TRIGGER_FRAMES}", (20, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        cv2.putText(display, "click: move ROI | s: save | +/-: ROI size | q: quit", (20, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        cv2.imshow(WINDOW_NAME, display)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        elif key == ord("s"):
            save_roi(ROI_SAVE_PATH, state["roi_center"], state["roi_size"])
            print(f"ROI 저장 완료: {ROI_SAVE_PATH}")
        elif key == ord("+") or key == ord("="):
            state["roi_size"] += 20
            print(f"ROI_SIZE: {state['roi_size']}")
        elif key == ord("-"):
            state["roi_size"] = max(80, state["roi_size"] - 20)
            print(f"ROI_SIZE: {state['roi_size']}")

        rclpy.spin_once(goal_node, timeout_sec=0.001)

    cap.release()
    cv2.destroyAllWindows()
    goal_node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()