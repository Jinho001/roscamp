import cv2
import socket
import numpy as np

def main():
    UDP_PORT = 5000
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # 다른 포트 충돌 방지를 위해 SO_REUSEADDR 설정
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", UDP_PORT))
    sock.settimeout(1.0)
    
    print(f"==================================================")
    print(f"[INFO] UDP {UDP_PORT} 포트에서 카메라 영상을 대기 중입니다...")
    print(f"[INFO] 's' 키를 누르면 현재 화면을 'test_box.jpg'로 캡처(저장)합니다.")
    print(f"[INFO] 'q' 키를 누르면 뷰어를 종료합니다.")
    print(f"==================================================")
    
    cv2.namedWindow("UDP Raw Stream Viewer", cv2.WINDOW_NORMAL)
    
    while True:
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
            continue
            
        nparr = np.frombuffer(data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is not None:
            cv2.imshow("UDP Raw Stream Viewer", img)
            
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('s'):
                cv2.imwrite("test_box.jpg", img)
                print("\n[SUCCESS] 'test_box.jpg' 저장 완료! 📸")
                print("이제 이 터미널을 종료(q)하고 아래 명령어를 실행하세요:")
                print("python3 extract_presentation_images.py test_box.jpg\n")

    cv2.destroyAllWindows()
    sock.close()

if __name__ == "__main__":
    main()
