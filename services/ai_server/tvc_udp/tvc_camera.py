#!/usr/bin/env python3
"""
탑뷰 카메라 캡처 도구.
캘리브레이션 적용된 실시간 영상을 보여주고,
각도를 맞출 때마다 Space / s 키로 사진을 저장한다.

저장 경로: /home/addinedu/perspective/image/capture_YYYYMMDD_HHMMSS.jpg

Keys:
    Space / s : 현재 프레임 캡처 저장
    r         : 최근 저장 사진 다시 보기
    q         : 종료
"""

import os
import time

import cv2
import numpy as np

# ── 설정 ───────────────────────────────────────────────────────────────────────
CAMERA_ID  = "/dev/video0"
CALIB_PATH = "/home/addinedu/detection/pose_detection/camera_calibration/camera_calibration_data.npz"
SAVE_DIR   = "/home/addinedu/perspective/image"

FRAME_W = 1280
FRAME_H =  720
FPS     =  30


# ── 캘리브레이션 ───────────────────────────────────────────────────────────────
def load_calibration(path):
    if not os.path.exists(path):
        print(f"[경고] 캘리브레이션 파일 없음: {path}")
        print("[경고] 왜곡 보정 없이 원본 영상을 사용합니다.")
        return None, None
    data = np.load(path)
    print("[캘리브레이션] 로드 완료")
    return data["camera_matrix"], data["dist_coeffs"]


def build_undistort_maps(K, D, w, h):
    new_K, _ = cv2.getOptimalNewCameraMatrix(K, D, (w, h), 0, (w, h))
    m1, m2   = cv2.initUndistortRectifyMap(K, D, None, new_K, (w, h), cv2.CV_16SC2)
    return m1, m2


# ── 저장 ───────────────────────────────────────────────────────────────────────
def save_frame(frame):
    os.makedirs(SAVE_DIR, exist_ok=True)
    ts       = time.strftime("%Y%m%d_%H%M%S")
    filename = f"capture_{ts}.jpg"
    path     = os.path.join(SAVE_DIR, filename)
    cv2.imwrite(path, frame)
    print(f"[저장] {path}")
    return path


# ── 메인 ───────────────────────────────────────────────────────────────────────
def main():
    # 카메라 열기
    cam_arg = CAMERA_ID
    if CAMERA_ID.startswith("/dev/video"):
        try:
            cam_arg = int(CAMERA_ID.replace("/dev/video", ""))
        except ValueError:
            pass

    cap = cv2.VideoCapture(cam_arg, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(cam_arg)
    if not cap.isOpened():
        print(f"[오류] 카메라를 열 수 없습니다: {CAMERA_ID}")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    cap.set(cv2.CAP_PROP_FPS, FPS)

    # 캘리브레이션 로드
    K, D = load_calibration(CALIB_PATH)
    map1 = map2 = None
    calib_ready = False

    # 상태
    capture_count = 0
    last_saved    = None       # 마지막 저장 경로
    show_saved    = False      # 저장 미리보기 표시 중
    saved_img     = None
    notice_until  = 0          # 화면 알림 표시 종료 시각

    print("\n[시작]  Space/s : 캡처 저장 | r : 마지막 사진 보기 | q : 종료\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[경고] 프레임 읽기 실패")
            continue

        fh, fw = frame.shape[:2]

        # 왜곡 보정 맵 최초 1회 생성
        if K is not None and map1 is None:
            map1, map2   = build_undistort_maps(K, D, fw, fh)
            calib_ready  = True

        if map1 is not None:
            frame = cv2.remap(frame, map1, map2, cv2.INTER_LINEAR)

        frame = cv2.flip(frame, 0)

        display = frame.copy()

        # ── HUD ──────────────────────────────────────────────────────────────
        calib_str = "캘리브레이션 적용 중" if calib_ready else "캘리브레이션 없음 (원본)"
        cv2.putText(display, calib_str,
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 220, 0) if calib_ready else (0, 80, 220), 2)

        cv2.putText(display, f"저장 횟수: {capture_count}",
                    (10, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 200, 200), 2)

        cv2.putText(display,
                    "Space/s: 캡처  |  r: 마지막 사진  |  q: 종료",
                    (10, fh - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 160, 160), 1)

        # 캡처 직후 알림 표시
        if time.time() < notice_until:
            cv2.putText(display, "SAVED!",
                        (fw // 2 - 60, fh // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 255, 0), 4)

        cv2.imshow("Top View Camera", display)

        # ── 키 입력 ──────────────────────────────────────────────────────────
        key = cv2.waitKey(1) & 0xFF

        if key in (ord(" "), ord("s")):
            # 캡처 저장
            last_saved = save_frame(frame)
            saved_img  = frame.copy()
            capture_count += 1
            notice_until  = time.time() + 1.5   # 1.5초 동안 SAVED 표시

        elif key == ord("r"):
            # 마지막 저장 사진 토글
            if saved_img is not None:
                show_saved = not show_saved
                if show_saved:
                    cv2.imshow("마지막 캡처", saved_img)
                    print(f"[미리보기] {last_saved}")
                else:
                    cv2.destroyWindow("마지막 캡처")
            else:
                print("[안내] 아직 저장된 사진이 없습니다.")

        elif key == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"\n[종료] 총 {capture_count}장 저장됨  →  {SAVE_DIR}")


if __name__ == "__main__":
    main()
