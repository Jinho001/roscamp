import socket
import struct
import json

LLM_SERVER_IP   = "localhost"
LLM_SERVER_PORT = 9000


def test_llm(user_text: str, accumulated_tags: dict = None):
    if accumulated_tags is None:
        accumulated_tags = {
            "activity": [], "style": [], "feature": [], "color": [],
            "brand": [], "season_weather": [], "price": [], "target": []
        }

    payload = json.dumps({
        "user_text": user_text,
        "accumulated_tags": accumulated_tags
    }).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)

    try:
        sock.connect((LLM_SERVER_IP, LLM_SERVER_PORT))
        sock.send(struct.pack("!I", len(payload)) + payload)

        resp_len = struct.unpack("!I", sock.recv(4))[0]
        resp_data = b""
        while len(resp_data) < resp_len:
            resp_data += sock.recv(resp_len - len(resp_data))

        resp = json.loads(resp_data.decode("utf-8"))
        return resp

    except ConnectionRefusedError:
        print(f"[오류] LLM 서버({LLM_SERVER_IP}:{LLM_SERVER_PORT})에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
        return None
    except socket.timeout:
        print("[오류] 응답 타임아웃")
        return None
    finally:
        sock.close()


if __name__ == "__main__":
    print("=" * 50)
    print("  LLM 서버 테스트")
    print(f"  대상: {LLM_SERVER_IP}:{LLM_SERVER_PORT}")
    print("=" * 50)

    result = test_llm("러닝화 추천해줘")

    if result:
        print(f"\n추천 결과 수: {result.get('count', 0)}개")
        for item in result.get("results", []):
            print(f"  - [{item.get('brand')}] {item.get('model')} / {item.get('price')}원 / score={item.get('score')}")
        print("\n전체 응답:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
