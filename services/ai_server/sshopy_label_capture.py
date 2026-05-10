import json
import re
import time
from pathlib import Path

import cv2


class SshopyLabelCapture:
    def __init__(
        self,
        root_dir="/home/team1-ai/roscamp-repo-1/services/ai_server/datasets/sshopy_pose",
        interval_sec=0.5,
        enabled=True,
    ):
        self.root_dir = Path(root_dir)
        self.images_dir = self.root_dir / "images"
        self.labels_dir = self.root_dir / "labels"
        self.meta_path = self.root_dir / "metadata.jsonl"
        self.interval_sec = interval_sec
        self.enabled = enabled
        self.last_saved_at = {}
        self.count = 0

        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.labels_dir.mkdir(parents=True, exist_ok=True)

    def toggle(self):
        self.enabled = not self.enabled
        return self.enabled

    def maybe_save(self, frame, meta):
        if not self.enabled:
            return False

        robot_id = str(meta.get("robot_id", "unknown"))
        if not robot_id.lower().startswith("sshopy"):
            return False

        now = time.time()
        if now - self.last_saved_at.get(robot_id, 0.0) < self.interval_sec:
            return False

        frame_id = meta.get("frame_id", 0)
        safe_robot_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", robot_id)
        stamp_ms = int(now * 1000)
        stem = f"{safe_robot_id}_frame{frame_id}_{stamp_ms}"

        image_path = self.images_dir / f"{stem}.jpg"
        label_path = self.labels_dir / f"{stem}.txt"

        ok = cv2.imwrite(str(image_path), frame)
        if not ok:
            print(f"[LABEL] image save failed: {image_path}")
            return False

        label_path.write_text("")

        record = {
            "image": str(image_path),
            "label": str(label_path),
            "robot_id": robot_id,
            "frame_id": frame_id,
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
            "timestamp": now,
        }
        with self.meta_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self.last_saved_at[robot_id] = now
        self.count += 1
        print(f"[LABEL] saved #{self.count}: {image_path.name}")
        return True
