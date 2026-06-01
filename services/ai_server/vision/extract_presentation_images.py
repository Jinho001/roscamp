import cv2
import numpy as np
import sys
import os

# 실제 운영 중인 서버 모듈을 직접 불러옵니다.
import cv_detect_server

def extract_pipeline_images(img_path):
    print(f"[{img_path}] 이미지를 불러옵니다...")
    img = cv2.imread(img_path)
    if img is None:
        print("오류: 이미지를 불러올 수 없습니다. 경로를 확인해주세요.")
        return

    out_dir = "presentation_images"
    os.makedirs(out_dir, exist_ok=True)

    # 1. 원본 이미지 저장
    cv2.imwrite(f"{out_dir}/01_raw.jpg", img)

    # 2. CLAHE 조명 평활화
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    clahe_img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    cv2.imwrite(f"{out_dir}/02_clahe.jpg", clahe_img)
    cv2.imwrite(f"{out_dir}/02_clahe_L_channel.jpg", lab[:, :, 0])

    # 3. 마스크 (실제 서버의 전역 변수 HSV_LOWER/UPPER 그대로 사용)
    blurred = cv2.GaussianBlur(clahe_img, (5, 5), 0)
    hsv     = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    mask_before = cv2.inRange(hsv, cv_detect_server.HSV_LOWER, cv_detect_server.HSV_UPPER)
    cv2.imwrite(f"{out_dir}/03_mask_before_morph.jpg", mask_before)

    # 4. 모폴로지 (실제 서버의 MORPH_K 그대로 사용)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (cv_detect_server.MORPH_K, cv_detect_server.MORPH_K))
    mask_after = cv2.morphologyEx(mask_before, cv2.MORPH_CLOSE, kernel)
    mask_after = cv2.morphologyEx(mask_after, cv2.MORPH_OPEN,  kernel)
    cv2.imwrite(f"{out_dir}/04_mask_after_morph.jpg", mask_after)

    # 5. 최종 OBB 결과 (실제 서버의 함수를 직접 호출!)
    result = cv_detect_server.detect_box_cv(img)
    display = cv_detect_server._draw_overlay(img, result)
    
    cv2.imwrite(f"{out_dir}/05_final_obb.jpg", display)
    
    print("\n✓ 추출 성공! 다음과 같은 실제 서버 파라미터가 적용되었습니다:")
    print(f"  - HSV 범위: {cv_detect_server.HSV_LOWER.tolist()} ~ {cv_detect_server.HSV_UPPER.tolist()}")
    print(f"  - Area 제한: {cv_detect_server.MIN_AREA} ~ {cv_detect_server.MAX_AREA}")
    print(f"  - 크기 제한: {cv_detect_server.MIN_W}~{cv_detect_server.MAX_W} x {cv_detect_server.MIN_H}~{cv_detect_server.MAX_H}")
    print(f"\n모든 이미지 추출이 완료되었습니다! './{out_dir}' 폴더를 확인하세요.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 extract_presentation_images.py <테스트이미지_경로>")
    else:
        extract_pipeline_images(sys.argv[1])
