import re
import time

import cv2
import numpy as np


class MultiRobotViewer:
    def __init__(self, window_name, main_robot_no=6, canvas_size=(1280, 760)):
        self.window_name = window_name
        self.main_robot_no = main_robot_no
        self.canvas_w, self.canvas_h = canvas_size
        self.frames = {}
        self.updated_at = {}
        cv2.namedWindow(self.window_name)

    def update(self, robot_id, frame):
        key = str(robot_id)
        self.frames[key] = frame.copy()
        self.updated_at[key] = time.time()

    def show(self):
        canvas = np.zeros((self.canvas_h, self.canvas_w, 3), dtype=np.uint8)
        canvas[:] = (18, 18, 18)

        main_key = self._find_robot_key(self.main_robot_no)
        side_keys = [k for k in sorted(self.frames, key=self._sort_key) if k != main_key]

        side_w = 340
        gap = 10
        main_rect = (gap, gap, self.canvas_w - side_w - gap * 2, self.canvas_h - gap * 2)
        side_rect = (self.canvas_w - side_w, gap, side_w - gap, self.canvas_h - gap * 2)

        self._draw_panel(canvas, main_rect, main_key, f"ROBOT {self.main_robot_no}", large=True)
        self._draw_side_panels(canvas, side_rect, side_keys)

        cv2.imshow(self.window_name, canvas)
        return cv2.waitKey(1) & 0xFF

    def close(self):
        cv2.destroyWindow(self.window_name)

    def _find_robot_key(self, robot_no):
        for key in self.frames:
            if self._robot_no(key) == robot_no:
                return key
        return None

    def _draw_side_panels(self, canvas, rect, keys):
        x, y, w, h = rect
        if not keys:
            self._draw_empty(canvas, rect, "NO OTHER ROBOTS")
            return

        cols = 1 if len(keys) <= 3 else 2
        rows = int(np.ceil(len(keys) / cols))
        tile_w = (w - (cols - 1) * 8) // cols
        tile_h = (h - (rows - 1) * 8) // rows

        for idx, key in enumerate(keys):
            col = idx % cols
            row = idx // cols
            tx = x + col * (tile_w + 8)
            ty = y + row * (tile_h + 8)
            self._draw_panel(canvas, (tx, ty, tile_w, tile_h), key, key, large=False)

    def _draw_panel(self, canvas, rect, key, fallback_label, large):
        x, y, w, h = rect
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (60, 60, 60), 1)

        if key is None or key not in self.frames:
            self._draw_empty(canvas, rect, f"{fallback_label} WAITING")
            return

        frame = self.frames[key]
        fitted = self._fit(frame, w, h)
        fh, fw = fitted.shape[:2]
        ox = x + (w - fw) // 2
        oy = y + (h - fh) // 2
        canvas[oy:oy + fh, ox:ox + fw] = fitted

        age = time.time() - self.updated_at.get(key, time.time())
        label = f"{key}  {frame.shape[1]}x{frame.shape[0]}  {age:.1f}s"
        font_scale = 0.7 if large else 0.45
        thickness = 2 if large else 1
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        cv2.rectangle(canvas, (x, y), (x + tw + 12, y + th + 14), (0, 0, 0), -1)
        cv2.putText(canvas, label, (x + 6, y + th + 7),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness)

    def _draw_empty(self, canvas, rect, text):
        x, y, w, h = rect
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.putText(canvas, text, (x + max(8, (w - tw) // 2), y + (h + th) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 2)

    def _fit(self, frame, max_w, max_h):
        h, w = frame.shape[:2]
        scale = min(max_w / max(w, 1), max_h / max(h, 1))
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def _sort_key(self, key):
        robot_no = self._robot_no(key)
        return (robot_no is None, robot_no if robot_no is not None else key)

    def _robot_no(self, key):
        match = re.search(r"(\d+)$", str(key))
        return int(match.group(1)) if match else None
