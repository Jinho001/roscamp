import argparse
import json
import math
import socket
import struct
import time

import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node


# =========================
# back_test.py 포트 기준
# =========================
MAIN_SERVER_IP = "192.168.1.120"
AI_SERVER_IP = "192.168.1.121"
AI_TCP_PORT = 7019
ROBOT_IP = "192.168.1.113"
TOPIC_NAME = "/hand_raise_goal"

HEADER_SIZE = 4
MAX_JSON_SIZE = 20 * 1024 * 1024
RECONNECT_SEC = 3.0
DEFAULT_COOLDOWN_SEC = 1.0


def recv_exact(conn, size):
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            return None
        data += chunk
    return data


class TcpGoalBridge(Node):
    def __init__(self, topic_name, frame_id, cooldown_sec):
        super().__init__("tcp_result_to_hand_raise_goal")
        self.publisher = self.create_publisher(PoseStamped, topic_name, 10)
        self.topic_name = topic_name
        self.frame_id = frame_id
        self.cooldown_sec = cooldown_sec
        self._last_publish_time = 0.0

        self.get_logger().info(f"ROS2 publish topic: {self.topic_name}")
        self.get_logger().info(f"target robot IP: {ROBOT_IP}")

    def publish_goal(self, goal, robot_id=None, frame_id=None):
        now = time.time()
        if now - self._last_publish_time < self.cooldown_sec:
            return False

        map_goal = goal.get("map", {})
        try:
            x = float(map_goal["x"])
            y = float(map_goal["y"])
            theta = float(map_goal.get("theta", 0.0))
        except (KeyError, TypeError, ValueError):
            self.get_logger().warning(f"invalid goal map data: {goal}")
            return False

        msg = PoseStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.pose.position.x = x
        msg.pose.position.y = y
        msg.pose.position.z = 0.0
        msg.pose.orientation.z = math.sin(theta / 2.0)
        msg.pose.orientation.w = math.cos(theta / 2.0)

        self.publisher.publish(msg)
        self._last_publish_time = now

        self.get_logger().info(
            f"published goal robot={robot_id} frame={frame_id} "
            f"x={x:.3f} y={y:.3f} theta={theta:.3f}"
        )
        return True


def print_result(result, raw_size):
    sent_ts = result.get("timestamp", 0)
    latency_ms = (time.time() - sent_ts) * 1000.0 if sent_ts else 0.0
    detections = result.get("detections", [])
    goals = result.get("goals", [])

    print(
        f"[TCP OK] "
        f"type={result.get('type')} "
        f"robot={result.get('robot_id')} "
        f"frame={result.get('frame_id')} "
        f"detections={len(detections)} "
        f"goals={len(goals)} "
        f"process={result.get('process_ms')}ms "
        f"latency={latency_ms:.1f}ms "
        f"bytes={raw_size}"
    )

    for i, det in enumerate(detections):
        print(
            f"  [{i}] {det.get('class_name')} "
            f"conf={det.get('confidence')} "
            f"bbox={det.get('bbox')}"
        )

    for i, goal in enumerate(goals):
        print(
            f"  [goal {i}] pose={goal.get('pose')} "
            f"conf={goal.get('confidence')} "
            f"pixel={goal.get('pixel')} "
            f"map={goal.get('map')}"
        )


def publish_goals_from_result(bridge, result):
    goals = result.get("goals", [])
    if not goals:
        return

    robot_id = result.get("robot_id")
    frame_id = result.get("frame_id")

    for goal in goals:
        if goal.get("pose") == "hands_up":
            bridge.publish_goal(goal, robot_id=robot_id, frame_id=frame_id)


def handle_json_bytes(body, bridge):
    result = json.loads(body.decode("utf-8"))
    print_result(result, len(body))
    publish_goals_from_result(bridge, result)


def receive_length_prefixed_stream(sock, bridge):
    while True:
        header = recv_exact(sock, HEADER_SIZE)
        if header is None:
            raise ConnectionError("AI server disconnected")

        msg_len = struct.unpack("!I", header)[0]
        if msg_len <= 0 or msg_len > MAX_JSON_SIZE:
            raise ValueError(f"invalid TCP JSON size: {msg_len}")

        body = recv_exact(sock, msg_len)
        if body is None:
            raise ConnectionError("AI server disconnected while reading JSON body")

        handle_json_bytes(body, bridge)


def connect_to_ai(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect((host, port))
    sock.settimeout(None)
    return sock


def main():
    parser = argparse.ArgumentParser(description="Receive AI server TCP JSON results.")
    parser.add_argument(
        "--host",
        default=AI_SERVER_IP,
        help="AI server IP to connect to",
    )
    parser.add_argument("--port", type=int, default=AI_TCP_PORT)
    parser.add_argument("--topic", default=TOPIC_NAME)
    parser.add_argument("--frame-id", default="map")
    parser.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN_SEC)
    args = parser.parse_args()

    rclpy.init()
    bridge = TcpGoalBridge(
        topic_name=args.topic,
        frame_id=args.frame_id,
        cooldown_sec=args.cooldown,
    )

    print("=" * 60)
    print("[AI TCP RESULT TO ROS2 GOAL BRIDGE]")
    print(f"connect target: {args.host}:{args.port}")
    print("protocol: [4-byte length][JSON]")
    print(f"ros2 topic: {args.topic}")
    print(f"robot ip: {ROBOT_IP}")
    print("=" * 60)

    try:
        while True:
            rclpy.spin_once(bridge, timeout_sec=0.0)
            sock = None
            try:
                sock = connect_to_ai(args.host, args.port)
                print(f"[TCP CONNECTED] AI server {args.host}:{args.port}")
                receive_length_prefixed_stream(sock, bridge)
            except OSError as e:
                print(f"[TCP WAIT] connect/read failed: {e} - retry in {RECONNECT_SEC}s")
            except Exception as e:
                print(f"[TCP ERR] {e} - retry in {RECONNECT_SEC}s")
            finally:
                if sock is not None:
                    sock.close()
                time.sleep(RECONNECT_SEC)
    except KeyboardInterrupt:
        print("\n[STOP] receiver stopped")
    finally:
        bridge.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
