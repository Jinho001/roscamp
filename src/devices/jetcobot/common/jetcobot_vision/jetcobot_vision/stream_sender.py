#!/usr/bin/env python3
"""
제어 PC용 영상 스트리머 (Sender)
===============================
젯코봇 제어 PC에서 실행하여 AI 서버로 영상을 전송합니다.
카메라 포트가 하나뿐이므로 --targets 로 다중 목적지에 동시 전송 가능.

사용법:
  # 단일 목적지 (하위 호환)
  python3 stream_sender.py --host 192.168.1.121 --port 5000

  # 다중 목적지 (cv_detect + qr_server 동시)
  python3 stream_sender.py --targets 192.168.1.121:5000 192.168.1.121:5010
"""
import argparse
import socket
import time

import cv2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host",    default=None, help="단일 목적지 IP (하위 호환)")
    parser.add_argument("--port",    type=int, default=5000, help="단일 목적지 포트 (하위 호환)")
    parser.add_argument("--targets", nargs="+", default=None,
                        metavar="HOST:PORT",
                        help="다중 전송 목적지 (예: 192.168.1.121:5000 192.168.1.121:5010)")
    parser.add_argument("--device",  default="/dev/jetcocam0", help="카메라 장치")
    parser.add_argument("--width",   type=int, default=640)
    parser.add_argument("--height",  type=int, default=480)
    args = parser.parse_args()

    # 목적지 파싱: --targets 우선, 없으면 --host/--port 사용
    destinations = []
    if args.targets:
        for t in args.targets:
            host, port = t.rsplit(":", 1)
            destinations.append((host, int(port)))
    elif args.host:
        destinations.append((args.host, args.port))
    else:
        parser.error("--targets 또는 --host 중 하나는 필수입니다.")

    cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    if not cap.isOpened():
        print(f"[ERROR] 카메라를 열 수 없습니다: {args.device}")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    dest_str = ", ".join(f"{h}:{p}" for h, p in destinations)
    print(f"[STREAM] 전송 시작 → {dest_str}")

    try:
        while True:
            if not cap.isOpened():
                print("[WARN] 카메라가 닫혀있습니다. 재연결 시도...")
                time.sleep(1.0)
                cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  args.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
                continue

            ret, frame = cap.read()
            if not ret:
                print("[WARN] 프레임 획득 실패.")
                time.sleep(0.5)
                cap.release()
                continue

            # JPEG 압축 (품질을 동적으로 낮춰 UDP 64KB 제한 초과 방지)
            quality = 50
            while True:
                _, img_encoded = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
                )
                data = img_encoded.tobytes()
                if len(data) <= 65000 or quality <= 10:
                    break
                quality -= 10

            if len(data) > 65000:
                print(f"[WARN] 프레임 크기 초과 ({len(data)} bytes) - 전송 포기")
                continue

            # 모든 목적지에 동일 프레임 전송
            for dest in destinations:
                sock.sendto(data, dest)

            time.sleep(0.03)  # 약 30 FPS

    except KeyboardInterrupt:
        print("[STOP] 스트리밍 중단")
    finally:
        cap.release()
        sock.close()


if __name__ == "__main__":
    main()
