#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, PoseStamped
from apriltag_msgs.msg import AprilTagDetectionArray
import numpy as np
from enum import Enum

class ServoState(Enum):
    IDLE = 0
    APPROACHING = 1
    DOCKED = 2

class VisualServoingNode(Node):
    def __init__(self):
        super().__init__('visual_servoing_node')
        
        # Parameters
        self.declare_parameter('target_tag_id', 0)
        self.declare_parameter('enable_servoing', False)
        self.declare_parameter('tag_size', 0.04)  # 4cm
        
        self.target_tag_id = self.get_parameter('target_tag_id').value
        self.tag_size = self.get_parameter('tag_size').value
        
        # State
        self.state = ServoState.IDLE
        self.tag_detected = False
        self.last_detection = None
        
        # Target thresholds
        self.distance_threshold = 0.05  # 5cm
        
        # Publishers
        self.cmd_vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.dock_complete_pub = self.create_publisher(
            PoseStamped,
            '/dock_complete',
            10
        )
        
        # Subscribers
        self.tag_sub = self.create_subscription(
            AprilTagDetectionArray,
            '/detections',
            self.tag_callback,
            10
        )
        
        # Control loop (20Hz)
        self.timer = self.create_timer(0.05, self.control_loop)
        
        self.get_logger().info(
            f'Visual Servoing Node started (target tag: {self.target_tag_id})'
        )
    
    def tag_callback(self, msg):
        """AprilTag 감지 콜백"""
        self.tag_detected = False
        
        for detection in msg.detections:
            if detection.id == self.target_tag_id:
                self.tag_detected = True
                self.last_detection = detection
                break
    
    def pixel_to_camera_coords(self, detection):
        """
        픽셀 좌표 → 카메라 좌표계 변환
        간단한 방법: 중심점과 코너로 거리 추정
        """
        # 태그의 픽셀 크기 계산
        corners = detection.corners
        
        # 대각선 길이로 픽셀 크기 추정
        width_px = np.sqrt(
            (corners[1].x - corners[0].x)**2 + 
            (corners[1].y - corners[0].y)**2
        )
        
        # 실제 크기 / 픽셀 크기 = 거리 (단순 추정)
        focal_length = 500  # 픽셀 (대략적인 값)
        
        distance = (self.tag_size * focal_length) / width_px
        
        # 중심점의 픽셀 좌표
        cx = detection.centre.x
        cy = detection.centre.y
        
        # 이미지 중심 (320, 240 for 640x480)
        img_center_x = 320
        img_center_y = 240
        
        # 각도 계산 (라디안)
        angle_x = np.arctan2(cx - img_center_x, focal_length)
        angle_y = np.arctan2(cy - img_center_y, focal_length)
        
        # 카메라 좌표계
        x = distance * np.sin(angle_x)
        y = distance * np.sin(angle_y)
        z = distance * np.cos(angle_x) * np.cos(angle_y)
        
        return x, y, z
    
    def compute_control(self):
        """제어 명령 계산 (단순 버전)"""
        if not self.tag_detected or self.last_detection is None:
            return None, None, False
        
        # 픽셀 → 3D 좌표
        x, y, z = self.pixel_to_camera_coords(self.last_detection)
        
        # 태그가 화면 중앙에 오도록
        cx = self.last_detection.centre.x
        screen_error = (cx - 320) / 320.0  # -1 ~ 1 정규화
        
        # 목표: 태그 정면 5cm, 화면 중앙
        distance_error = z - self.distance_threshold
        
        # 제어 계산
        linear_x = distance_error * 0.5
        angular_z = -screen_error * 1.5
        
        # 속도 제한
        linear_x = np.clip(linear_x, -0.1, 0.1)
        angular_z = np.clip(angular_z, -0.3, 0.3)
        
        # 도킹 완료 확인
        if abs(distance_error) < 0.03 and abs(screen_error) < 0.1:
            return 0.0, 0.0, True
        
        return linear_x, angular_z, False
    
    def control_loop(self):
        """메인 제어 루프"""
        if not self.get_parameter('enable_servoing').value:
            return
        
        if self.state == ServoState.IDLE:
            self.state = ServoState.APPROACHING
            self.get_logger().info('Starting visual servoing...')
        
        elif self.state == ServoState.APPROACHING:
            if not self.tag_detected:
                # 태그 미감지 시 제자리 회전
                twist = Twist()
                twist.angular.z = 0.2
                self.cmd_vel_pub.publish(twist)
                return
            
            linear_x, angular_z, docked = self.compute_control()
            
            if docked:
                self.state = ServoState.DOCKED
                self.get_logger().info('Docking complete!')
                
                # 정지
                self.cmd_vel_pub.publish(Twist())
                
                # 완료 신호
                msg = PoseStamped()
                msg.header.stamp = self.get_clock().now().to_msg()
                self.dock_complete_pub.publish(msg)
                
                # Visual Servoing 비활성화
                self.set_parameters([
                    rclpy.parameter.Parameter(
                        'enable_servoing',
                        rclpy.Parameter.Type.BOOL,
                        False
                    )
                ])
            else:
                twist = Twist()
                twist.linear.x = linear_x
                twist.angular.z = angular_z
                self.cmd_vel_pub.publish(twist)
                
                # 디버그 로그
                if self.tag_detected:
                    x, y, z = self.pixel_to_camera_coords(self.last_detection)
                    cx = self.last_detection.centre.x
                    screen_error = (cx - 320) / 320.0
                    self.get_logger().info(
                        f'Tag: z={z:.2f}m, screen_err={screen_error:.2f} | '
                        f'cmd: v={linear_x:.2f}, w={angular_z:.2f}',
                        throttle_duration_sec=1.0
                    )

def main(args=None):
    rclpy.init(args=args)
    node = VisualServoingNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        # Ctrl+C 시 정지 명령 발행
        node.get_logger().info('Stopping robot...')
        node.cmd_vel_pub.publish(Twist())
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
