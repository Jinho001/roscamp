#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from geometry_msgs.msg import Twist

from ir import IR


class IRLineTraceNode(Node):

    def __init__(self):
        super().__init__("ir_line_trace_node")

        # 흰색 테이프 감지 (값이 낮으면 흰색)
        self.WHITE_MAX = 1800

        self.FORWARD_SPEED = 0.045
        self.SEARCH_SPEED = 0.035

        self.CORRECT_TURN_SPEED = 0.22
        self.TURN_IN_PLACE_SPEED = 0.28

        # ✅ 상태별 센서 읽기 주기
        self.IDLE_LOOP_DT = 0.5      # IDLE: 0.5초마다 (부하 적음)
        self.ACTIVE_LOOP_DT = 0.04   # ACTIVE: 0.04초마다 (빠른 반응)

        self.STOP_IGNORE_TIME = 3.0
        self.AUTO_START_CONFIRM_COUNT = 5
        self.LOST_LIMIT = 750

        self.STOP_CONFIRM_COUNT = 3

        self.ir = IR()

        self.cmd_sub = self.create_subscription(
            String,
            "/warejet/precision_parking/cmd",
            self.cmd_callback,
            10
        )

        self.state_pub = self.create_publisher(
            String,
            "/warejet/precision_parking/state",
            10
        )

        self.cmd_vel_pub = self.create_publisher(
            Twist,
            "/cmd_vel",
            10
        )

        self.active = False
        self.phase = "IDLE"
        self.completed = False  # ✅ 완료 플래그

        self.start_time = 0.0
        self.follow_start_time = 0.0

        self.stop_count = 0
        self.lost_count = 0
        self.auto_start_count = 0

        self.last_turn = "right"

        # ✅ 타이머는 IDLE 주기로 시작
        self.timer = self.create_timer(
            self.IDLE_LOOP_DT,
            self.control_loop
        )

        self.publish_state("IDLE")

        self.get_logger().info("IR 정밀주차 노드 준비 완료 (자동 시작 모드)")
        self.get_logger().info(f"WHITE_MAX={self.WHITE_MAX}")
        self.get_logger().info(f"IDLE 주기: {self.IDLE_LOOP_DT}s, ACTIVE 주기: {self.ACTIVE_LOOP_DT}s")

    def cmd_callback(self, msg):

        cmd = msg.data.strip().upper()

        if cmd == "START":
            self.start()

        elif cmd == "STOP":
            self.finish("STOPPED")

    def start(self):

        if self.completed:
            self.get_logger().warn("이미 완료됨 - 재시작 불가")
            return

        if self.active:
            self.get_logger().warn("이미 동작 중")
            return

        self.active = True
        self.phase = "FOLLOW_LINE"

        now = time.time()

        self.start_time = now
        self.follow_start_time = now

        self.stop_count = 0
        self.lost_count = 0
        self.auto_start_count = 0

        self.last_turn = "right"

        # ✅ ACTIVE 상태로 전환 → 타이머 주기 변경!
        self.timer.cancel()
        self.timer = self.create_timer(
            self.ACTIVE_LOOP_DT,
            self.control_loop
        )

        self.stop_robot()

        self.publish_state("FOLLOW_LINE")

        self.get_logger().info("IR 정밀주차 시작 (라인 추적)")

    def finish(self, state):

        self.stop_robot()

        self.active = False
        self.phase = "DONE"  # ✅ IDLE이 아닌 DONE으로!

        # ✅ SUCCESS면 완료 플래그 설정
        if state == "SUCCESS":
            self.completed = True
            self.get_logger().info("SUCCESS 완료 → 노드 종료 (재시작 불가)")

        # ✅ 타이머 완전 취소 (더 이상 실행 안 함!)
        self.timer.cancel()

        self.publish_state(state)

        self.get_logger().info(f"종료: {state}")

    def is_white(self, value):
        """값이 WHITE_MAX보다 낮으면 흰색"""
        return value <= self.WHITE_MAX

    def read_white_pattern(self):
        """흰색 테이프 패턴 읽기"""

        l, c, r = self.ir.read_ir()

        l = int(l)
        c = int(c)
        r = int(r)

        L = 1 if self.is_white(l) else 0
        C = 1 if self.is_white(c) else 0
        R = 1 if self.is_white(r) else 0

        white_count = L + C + R

        return (l, c, r), L, C, R, white_count

    def publish_cmd_vel(self, linear_x, angular_z):

        msg = Twist()

        msg.linear.x = float(linear_x)
        msg.angular.z = float(angular_z)

        self.cmd_vel_pub.publish(msg)

    def stop_robot(self):
        self.publish_cmd_vel(0.0, 0.0)

    def go_forward(self, speed=None):

        if speed is None:
            speed = self.FORWARD_SPEED

        self.publish_cmd_vel(speed, 0.0)

    def correct_left_while_forward(self):

        self.publish_cmd_vel(
            self.FORWARD_SPEED,
            self.CORRECT_TURN_SPEED
        )

    def correct_right_while_forward(self):

        self.publish_cmd_vel(
            self.FORWARD_SPEED,
            -self.CORRECT_TURN_SPEED
        )

    def turn_left_in_place(self):

        self.publish_cmd_vel(
            0.0,
            self.TURN_IN_PLACE_SPEED
        )

    def turn_right_in_place(self):

        self.publish_cmd_vel(
            0.0,
            -self.TURN_IN_PLACE_SPEED
        )

    def control_loop(self):

        # ✅ DONE 상태면 아무것도 안 함
        if self.phase == "DONE":
            return

        try:
            raw, L, C, R, white_count = self.read_white_pattern()

        except Exception as e:
            self.get_logger().error(f"IR 읽기 실패: {e}")
            if self.active:
                self.finish("FAIL_SENSOR")
            return

        now = time.time()

        # ──── [자동 시작] IDLE 상태에서 테이프 감지 시 자동 시작 ────
        if self.phase == "IDLE":

            # ✅ 완료되지 않았을 때만 자동 시작
            if not self.completed and white_count >= 1:
                self.auto_start_count += 1

                if self.auto_start_count >= self.AUTO_START_CONFIRM_COUNT:
                    self.get_logger().info(f"테이프 자동 감지! ({self.auto_start_count}회) → 라인 추적 시작")
                    self.start()
                    return

            else:
                self.auto_start_count = 0

            return

        # ──── [라인 추적] FOLLOW_LINE 상태 ────
        if self.phase == "FOLLOW_LINE":

            self.publish_state("FOLLOW_LINE")

            elapsed = now - self.follow_start_time
            stop_check_allowed = elapsed >= self.STOP_IGNORE_TIME

            is_stop_line = (L == 1 and C == 1 and R == 1)

            if stop_check_allowed and is_stop_line:

                self.stop_count += 1
                self.stop_robot()

                self.get_logger().info(f"정지선 감지! stop_count={self.stop_count}/{self.STOP_CONFIRM_COUNT}")

                if self.stop_count >= self.STOP_CONFIRM_COUNT:
                    self.get_logger().info("정지선 확정 → SUCCESS")
                    self.finish("SUCCESS")
                    return

                return

            else:
                if self.stop_count > 0:
                    self.get_logger().info("정지선 벗어남, stop_count 리셋")
                self.stop_count = 0

            self.follow_line(L, C, R, white_count)

            return

    def follow_line(self, L, C, R, white_count):

        if white_count == 0:

            self.lost_count += 1

            if self.lost_count >= self.LOST_LIMIT:
                self.finish("FAIL_LINE_LOST")
                return

            self.go_forward(self.SEARCH_SPEED)
            return

        self.lost_count = 0

        if C and not L and not R:
            self.go_forward()
            return

        if L and not R:
            self.last_turn = "left"
            self.correct_left_while_forward()
            return

        if R and not L:
            self.last_turn = "right"
            self.correct_right_while_forward()
            return

        self.go_forward(self.SEARCH_SPEED)

    def publish_state(self, state):

        msg = String()
        msg.data = state
        self.state_pub.publish(msg)

    def destroy_node(self):

        try:
            self.stop_robot()
            self.ir.close()

        except Exception:
            pass

        super().destroy_node()


def main(args=None):

    rclpy.init(args=args)

    node = IRLineTraceNode()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()