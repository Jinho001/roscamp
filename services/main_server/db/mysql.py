import mysql.connector
import json
from mysql.connector import Error
from fastapi import HTTPException
from dotenv import load_dotenv
import os

load_dotenv()

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4"
    )

# 로봇 조회
def get_robot_by_domain_id(domain_id: int):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
        SELECT id, name, status, domain_id
        FROM robot
        WHERE domain_id = %s
        LIMIT 1
        """
        cursor.execute(sql, (domain_id,))
        return cursor.fetchone()

    except Error as e:
        raise RuntimeError(f"로봇 조회 실패: {e}")

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# 상태 업데이트
def update_robot_status(robot_id: int, status: int):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        sql = """
        UPDATE robot
        SET status = %s
        WHERE id = %s
        """
        cursor.execute(sql, (status, robot_id))
        conn.commit()

    except Error as e:
        raise RuntimeError(f"상태 업데이트 실패: {e}")

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

#신발 조회

# def get_shoe_all_information 등록된 전체 신발 조회 
def get_shoe_all_information():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT *
            FROM shoes
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        return rows

    except mysql.connector.Error as e:
        print("MySQL 오류:", e)
        raise HTTPException(status_code=500, detail=f"MySQL 오류: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print("서버 오류:", e)
        raise HTTPException(status_code=500, detail=f"서버 오류: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# def get_shoe_information_by_shoe_id 등록한 신발 중 shoe_id 로 검색
def get_shoe_information_by_shoe_id(shoe_id: str):   
    
    conn = None
    cursor = None

    try:      
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT *
            FROM shoes
            WHERE shoe_id = %s
        """
        cursor.execute(sql, (shoe_id,))
        rows = cursor.fetchone()

        if not rows:
            raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")
        return rows

    except mysql.connector.Error as e:
        print("MySQL 오류:", e)
        raise HTTPException(status_code=500, detail=f"MySQL 오류: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print("서버 오류:", e)
        raise HTTPException(status_code=500, detail=f"서버 오류: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

#창고 신발 조회
def get_shoe_information_by_shoe_id_from_inventory(shoe_id: str):   
    
    conn = None
    cursor = None

    try:      
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT *
            FROM shoes_inventory
            WHERE shoe_id = %s
        """
        cursor.execute(sql, (shoe_id,))
        rows = cursor.fetchall()   # 결과 전체 읽기

        if not rows:
            raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")

        return rows

    except mysql.connector.Error as e:
        print("MySQL 오류:", e)
        raise HTTPException(status_code=500, detail=f"MySQL 오류: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print("서버 오류:", e)
        raise HTTPException(status_code=500, detail=f"서버 오류: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────────
# [요청] 시착 요청 시 shoes_inventory stock=0 처리 (variant 단위)
# ─────────────────────────────────────────────────────────────
def set_variant_stock_zero(shoe_id: str, color: str, size) -> int:
    """
    [요청] shoes_inventory 의 (shoe_id + color + size) variant row 의 stock 을 0 으로 set.

    /tryon/request 직후 호출되어 동일 variant 의 중복 시착 요청을 막는 용도.
    size 컬럼이 DECIMAL/INT 이고 클라이언트는 문자열을 보내므로
    SQL 내에서 DECIMAL 캐스팅하여 비교한다.

    Returns:
        업데이트된 row 개수 (매칭 없으면 0).
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        sql = """
            UPDATE shoes_inventory
               SET stock = 0
             WHERE shoe_id = %s
               AND TRIM(color) = TRIM(%s)
               AND CAST(size AS DECIMAL(10,4)) = CAST(%s AS DECIMAL(10,4))
        """
        cursor.execute(sql, (shoe_id, color, size))
        conn.commit()
        return cursor.rowcount

    except mysql.connector.Error as e:
        print("MySQL 오류:", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"MySQL 오류: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print("서버 오류:", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"서버 오류: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────────
# [요청] QR 입고 시 shoes_inventory.id 기준 stock += 1
# ─────────────────────────────────────────────────────────────
def increment_inventory_stock_by_id(inventory_id) -> int:
    """
    [요청] shoes_inventory.id 가 일치하는 row 의 stock 을 +1 한다.

    /qr_product_info 웹훅에서 호출되며, raw_payload(JSON) 의 'id' 키 값을
    그대로 받아 PK 로 사용한다.

    Returns:
        업데이트된 row 개수 (매칭 없으면 0).
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        sql = """
            UPDATE shoes_inventory
               SET stock = stock + 1
             WHERE id = %s
        """
        cursor.execute(sql, (inventory_id,))
        conn.commit()
        return cursor.rowcount

    except mysql.connector.Error as e:
        print("MySQL 오류:", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"MySQL 오류: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print("서버 오류:", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"서버 오류: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()