import math
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO


MODEL_PATH = Path(__file__).resolve().parent / "pose_estimation" / "pose_estimation" / "yolo26n-pose.pt"

CONF_THRES = 0.5
IMG_SIZE = 640
HAND_MODE = "both"
DIST_THRESHOLD_PX = 90
TRIGGER_FRAMES = 5

LEFT_SHOULDER = 5
RIGHT_SHOULDER = 6
LEFT_HIP = 11
RIGHT_HIP = 12
LEFT_WRIST = 9
RIGHT_WRIST = 10
LEFT_ANKLE = 15
RIGHT_ANKLE = 16


def is_pinky_pro_robot(robot_id):
    rid = str(robot_id).lower().replace("-", "_")
    return (
        rid.startswith("sshopy")
        or rid.startswith("pinkypro")
        or rid.startswith("pinky_pro")
    )


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
    if not candidates:
        return None

    pts = np.array(candidates, dtype=np.float32)
    cx, cy = np.mean(pts, axis=0)
    return float(cx), float(cy)


def get_hand_points(kpts, mode="both"):
    hands = []
    if mode in ("left", "both"):
        p = kpts[LEFT_WRIST]
        if valid_point(p):
            hands.append(("left", float(p[0]), float(p[1])))
    if mode in ("right", "both"):
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
    if ankles:
        pts = np.array(ankles, dtype=np.float32)
        fx, fy = np.mean(pts, axis=0)
        return float(fx), float(fy)
    return fallback_xy


class PinkyProPoseEstimator:
    def __init__(self, model_path=MODEL_PATH):
        self.model = YOLO(str(model_path))
        self.trigger_counts = {}

    def process(self, frame, robot_id):
        annotated = frame
        h, w = frame.shape[:2]

        status = "NO POSE"
        max_dist = 0.0
        target_pixel = None
        trigger_count = self.trigger_counts.get(str(robot_id), 0)

        if frame.size > 0:
            results = self.model.predict(
                source=frame,
                conf=CONF_THRES,
                imgsz=IMG_SIZE,
                verbose=False,
            )
            result = results[0]
            kpts = get_best_person_keypoints(result)

            if kpts is not None:
                body_center = get_body_center(kpts)
                hands = get_hand_points(kpts, HAND_MODE)

                if body_center is not None and hands:
                    bx, by = body_center
                    cv2.circle(annotated, (int(bx), int(by)), 7, (255, 255, 0), -1)

                    far_hand = None
                    for hand_name, hx, hy in hands:
                        dist = math.hypot(bx - hx, by - hy)
                        if dist > max_dist:
                            max_dist = dist
                            far_hand = (hand_name, hx, hy)

                        cv2.circle(annotated, (int(hx), int(hy)), 7, (0, 255, 255), -1)
                        cv2.line(annotated,
                                 (int(bx), int(by)),
                                 (int(hx), int(hy)),
                                 (0, 255, 255), 2)

                    if far_hand is not None and max_dist >= DIST_THRESHOLD_PX:
                        status = f"HAND FAR {max_dist:.1f}px"
                        trigger_count += 1
                        fx, fy = get_foot_point(kpts, (w / 2, h))
                        target_pixel = {"x": int(fx), "y": int(fy)}
                        cv2.circle(annotated, (target_pixel["x"], target_pixel["y"]),
                                   9, (0, 0, 255), -1)
                    else:
                        status = f"HAND CLOSE {max_dist:.1f}px"
                        trigger_count = 0
                else:
                    trigger_count = 0
            else:
                trigger_count = 0

        self.trigger_counts[str(robot_id)] = trigger_count

        cv2.rectangle(annotated, (0, 0), (w - 1, h - 1), (0, 255, 0), 2)
        cv2.putText(annotated, f"Pinky Pose: {status}", (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.putText(annotated, f"Trigger: {trigger_count}/{TRIGGER_FRAMES}", (20, 68),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)

        return {
            "status": status,
            "max_distance_px": round(max_dist, 2),
            "trigger_count": trigger_count,
            "trigger_frames": TRIGGER_FRAMES,
            "target_pixel": target_pixel,
        }
