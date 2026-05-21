#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from sensor_msgs.srv import SetCameraInfo
from cv_bridge import CvBridge
import numpy as np
import yaml

try:
    from picamera2 import Picamera2
except ImportError:
    print("ERROR: picamera2 not installed.")
    exit(1)

class RPiCameraNode(Node):
    def __init__(self):
        super().__init__('rpi_camera_node')
        
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('framerate', 30.0)
        
        width = self.get_parameter('width').value
        height = self.get_parameter('height').value
        framerate = self.get_parameter('framerate').value
        
        self.bridge = CvBridge()
        
        # 캘리브레이션 파일 로드
        self.camera_info_msg = None
        try:
            with open('/home/pinky/camera_calibration.yaml', 'r') as f:
                calib = yaml.safe_load(f)
                self.camera_info_msg = CameraInfo()
                self.camera_info_msg.width = calib['image_width']
                self.camera_info_msg.height = calib['image_height']
                self.camera_info_msg.distortion_model = calib['distortion_model']
                self.camera_info_msg.d = calib['distortion_coefficients']['data']
                self.camera_info_msg.k = calib['camera_matrix']['data']
                self.camera_info_msg.r = calib['rectification_matrix']['data']
                self.camera_info_msg.p = calib['projection_matrix']['data']
                self.get_logger().info('Calibration file loaded successfully!')
        except Exception as e:
            self.get_logger().warn(f'Could not load calibration: {e}')
        
        self.image_pub = self.create_publisher(Image, '/image_raw', 10)
        self.info_pub = self.create_publisher(CameraInfo, '/camera_info', 10)
        
        self.set_info_srv = self.create_service(
            SetCameraInfo,
            '/camera/set_camera_info',
            self.set_camera_info_callback
        )
        
        self.picam2 = Picamera2()
        config = self.picam2.create_preview_configuration(
            main={"size": (width, height), "format": "RGB888"}
        )
        self.picam2.configure(config)
        self.picam2.start()
        
        self.timer = self.create_timer(1.0 / framerate, self.capture_and_publish)
        self.get_logger().info('Camera node ready with calibration service')
    
    def capture_and_publish(self):
        try:
            frame = self.picam2.capture_array()
            frame = np.rot90(frame, 2)
            
            msg = self.bridge.cv2_to_imgmsg(frame, encoding='rgb8')
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = 'camera_link'
            self.image_pub.publish(msg)
            
            info_msg = CameraInfo()
            info_msg.header = msg.header
            info_msg.height = frame.shape[0]
            info_msg.width = frame.shape[1]
            info_msg.distortion_model = "plumb_bob"
            
            if self.camera_info_msg is not None:
                info_msg = self.camera_info_msg
                info_msg.header = msg.header
            
            self.info_pub.publish(info_msg)
        except Exception as e:
            self.get_logger().error(f'Error: {e}')
    
    def set_camera_info_callback(self, request, response):
        self.camera_info_msg = request.camera_info
        response.success = True
        response.status_message = "OK"
        self.get_logger().info('Calibration received')
        return response
    
    def destroy_node(self):
        if hasattr(self, 'picam2'):
            self.picam2.stop()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = RPiCameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
