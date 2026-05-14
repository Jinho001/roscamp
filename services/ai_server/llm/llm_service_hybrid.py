import json
import re
import torch
import asyncio
import struct
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9000
MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"

IS_FIRST_RUN = True

TAG_SCHEMA = {
    "activity": ["러닝", "웨이트", "등산", "축구", "농구", "데이트", "출근", "일상", "격식", "캠핑", "물놀이"],
    "style": ["힙한", "무난한", "깔끔한", "화려한", "빈티지", "클래식", "귀여운", "레트로", "테크웨어", "고프코어", "스포티", "발레코어"],
    "feature": ["쿠션감", "발볼 넓음", "방수", "키높이", "가벼움", "통기성", "미끄럼 방지", "편안함", "내구성", "보온성"],
    "color": ["화이트", "블랙", "그레이", "레드", "오렌지", "옐로우", "그린", "블루", "퍼플", "브라운", "베이지", "실버", "네이비", "핑크"],
    "brand": ["나이키", "아디다스", "뉴발란스", "반스", "컨버스", "아식스", "살로몬", "오니츠카타이거", "푸마", "미즈노", "킨", "호카", "닥터마틴", "어그", "리복"],
    "season_weather": ["봄/가을용", "여름용", "겨울용", "사계절용", "우천용"],
    "price": ["가성비", "일반", "프리미엄"],
    "target": ["남성용", "여성용", "공용"]
}

SYNONYMS = {
    "activity": {
        "데일리": "일상", "평소": "일상", "회사": "출근", "출근룩": "출근",
        "조깅": "러닝", "러닝화": "러닝", "헬스": "웨이트", "운동": "웨이트",
        "소개팅": "데이트", "면접": "격식", "결혼식": "격식", "풋살": "축구",
        "농구화": "농구", "농구": "농구",
    },
    "style": {
        "심플한": "깔끔한", "베이직한": "무난한", "튀는": "화려한", "아웃도어": "고프코어",
    },
    "feature": {
        "푹신한": "쿠션감", "발편한": "편안함", "발 편한": "편안함", "편하고": "편안함",
        "넓은발볼": "발볼 넓음", "발볼큰": "발볼 넓음", "비올때": "방수",
        "안미끄러운": "미끄럼 방지", "가벼운": "가벼움", "따뜻한": "보온성", "털신": "보온성"
    },
    "color": {
        "빨강": "레드", "빨간색": "레드", "빨간": "레드", "붉은색": "레드", "레드": "레드", "버건디": "레드",
        "주황": "오렌지", "주황색": "오렌지", "오렌지색": "오렌지", "오렌지": "오렌지",
        "노랑": "옐로우", "노란색": "옐로우", "노란": "옐로우", "황색": "옐로우", "옐로우": "옐로우", "머스타드": "옐로우",
        "초록": "그린", "초록색": "그린", "녹색": "그린", "그린": "그린", "카키": "그린", "올리브": "그린", "올리브그린": "그린",
        "파랑": "블루", "파란색": "블루", "파란": "블루", "하늘색": "블루", "블루": "블루",
        "남색": "네이비", "네이비": "네이비", "곤색": "네이비",
        "보라": "퍼플", "보라색": "퍼플", "퍼플": "퍼플",
        "핑크": "핑크", "분홍": "핑크", "분홍색": "핑크", "핫핑크": "핑크",
        "하얀색": "화이트", "흰색": "화이트", "하얀": "화이트", "올화이트": "화이트", "화이트": "화이트", "프화이트": "화이트",
        "검정": "블랙", "검정색": "블랙", "검은색": "블랙", "검은": "블랙", "올블랙": "블랙", "블랙": "블랙",
        "회색": "그레이", "그레이": "그레이", "잿빛": "그레이",
        "갈색": "브라운", "브라운": "브라운", "밤색": "브라운", "고동색": "브라운",
        "베이지": "베이지", "살구색": "베이지", "아이보리": "베이지", "크림": "베이지", "크림색": "베이지",
        "은색": "실버", "실버": "실버", "메탈릭": "실버"
    },
    "price": {
        "저렴한": "가성비", "싼": "가성비", "고가": "프리미엄", "비싼": "프리미엄"
    }
}

KOR_TO_ENG_COLOR = {
    "화이트": "white",
    "블랙": "black",
    "그레이": "gray",
    "레드": "red",
    "오렌지": "orange",
    "옐로우": "yellow",
    "그린": "green",
    "블루": "blue",
    "퍼플": "purple",
    "브라운": "brown",
    "베이지": "beige",
    "실버": "silver",
    "네이비": "navy",
    "핑크": "pink",
}

def load_model():
    print("⏳ LLM 모델 로딩 중...")
    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_quant_type="nf4"
    )

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        device_map="auto",
        quantization_config=quant_config
    )

    print("✅ LLM 모델 로딩 완료")
    return tokenizer, model


tokenizer = None
model = None


def get_model():
    global tokenizer, model
    if tokenizer is None or model is None:
        tokenizer, model = load_model()
    return tokenizer, model


def empty_tag_result():
    return {k: [] for k in TAG_SCHEMA.keys()}


def add_tag(tags: dict, field: str, value: str):
    if field not in tags:
        tags[field] = []
    if value not in tags[field]:
        tags[field].append(value)


def normalize_for_match(text: str) -> str:
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", str(text)).lower()


def extract_tags_by_rules(user_text: str):
    """
    1차 태그 추출: 기존 TAG_SCHEMA + SYNONYMS 기반으로만 처리한다.
    반환값:
      - tags: 규칙 기반으로 잡힌 태그
      - unmatched_keywords: 태그로 변환되지 않은 의미 있는 입력 조각
      - matched_words: 실제 매칭된 원문/동의어 표현
    """
    tags = empty_tag_result()
    text_no_space = user_text.replace(" ", "")
    text_norm = normalize_for_match(user_text)
    matched_words = []

    # 스키마에 있는 값을 직접 언급한 경우
    for field, allowed_values in TAG_SCHEMA.items():
        for value in allowed_values:
            value_norm = normalize_for_match(value)
            if value_norm and value_norm in text_norm:
                add_tag(tags, field, value)
                matched_words.append(value)

    # 동의어/표현 매핑
    for field, mapping in SYNONYMS.items():
        for raw_word, normalized_word in mapping.items():
            raw_compact = raw_word.replace(" ", "")
            if raw_compact and raw_compact in text_no_space:
                add_tag(tags, field, normalized_word)
                matched_words.append(raw_word)

    # 태그 추출 여부 판단에서 제외할 일반 표현
    stopwords = {
        "추천", "신발", "운동화", "신어", "신을", "신는", "거", "것",
        "좀", "해줘", "해주세요", "찾아줘", "찾아주세요", "보여줘",
        "원해", "원합니다", "싶어", "같은", "느낌", "스타일",
        "용", "용도", "때", "할", "하는", "으로", "로", "에", "에서",
        "나", "나는", "저", "저는", "있으면", "있는", "없는", "말고"
    }

    cleaned = user_text
    for word in sorted(matched_words, key=len, reverse=True):
        cleaned = re.sub(re.escape(word), " ", cleaned, flags=re.IGNORECASE)

    raw_tokens = re.findall(r"[가-힣a-zA-Z0-9]+", cleaned)
    unmatched_keywords = []
    for token in raw_tokens:
        token_norm = normalize_for_match(token)
        if not token_norm or token_norm in stopwords:
            continue
        # 한 글자 조사/감탄류 제거. 단, 숫자나 영문 모델명 가능성은 유지
        if len(token_norm) == 1 and not token_norm.isdigit():
            continue
        unmatched_keywords.append(token)

    return tags, unmatched_keywords, matched_words


def extract_tags_with_llm(user_text: str):
    tokenizer, model = get_model()

    system_prompt = f"""
    너는 신발 검색 태그 추출기다.

    반드시 사용자 문장에 직접 드러난 의미만 태그로 추출한다.
    브랜드 이미지, 일반 상식, 제품 카테고리 특성, 추측 기반으로 태그를 추가하지 마라.

    매우 중요:
    - 사용자가 직접 언급하지 않은 스타일(style) 태그는 절대 추가하지 마라.
    - 사용자가 직접 언급하지 않은 브랜드(brand)는 절대 추가하지 마라.
    - 사용자가 직접 언급하지 않은 색상(color)은 절대 추가하지 마라.
    - "운동화", "신발" 같은 일반 표현만 보고 activity를 추론하지 마라.
    - 문맥상 의미 추론이 가능한 경우에만 feature / season_weather / 일부 activity를 제한적으로 추론할 수 있다.
    - style은 절대 추론하지 말고, 사용자가 직접 말한 경우에만 넣어라.

    허용되는 의미 기반 추론 예시:
    - "뛰는", "달리는", "조깅", "마라톤" → activity: ["러닝"]
    - "비 오는 날", "젖는", "물 안 스며드는", "장마철" → feature: ["방수"]
    - "편한", "오래 걸어도 안 아픈", "안 피곤한" → feature: ["편안함"]
    - "푹신한", "충격 흡수", "발바닥 부담 적은" → feature: ["쿠션감"]
    - "안 미끄러운", "접지력 좋은" → feature: ["미끄럼 방지"]
    - "발 안 시린", "따뜻한" → feature: ["보온성"]
    - "시원한", "땀 덜 차는", "통풍 잘 되는" → feature: ["통기성"]
    - "키 커 보이는" → feature: ["키높이"]
    - "가볍게 신는", "무겁지 않은" → feature: ["가벼움"]

    추론 금지 예시:
    - "운동화" → activity 추가 금지
    - "나이키" → 힙한 추가 금지
    - "러닝" → 쿠션감 자동 추가 금지
    - "검정 신발" → 무난한 추가 금지
    - "비싼 브랜드" → 프리미엄 자동 추가 금지
    - "등산" → 고프코어 자동 추가 금지

    style 태그 규칙:
    아래 표현이 직접 등장한 경우에만 style 태그를 넣는다.

    - "힙한" → style: ["힙한"]
    - "무난한" → style: ["무난한"]
    - "깔끔한" → style: ["깔끔한"]
    - "화려한" → style: ["화려한"]
    - "빈티지" → style: ["빈티지"]
    - "클래식" → style: ["클래식"]
    - "귀여운" → style: ["귀여운"]
    - "레트로" → style: ["레트로"]
    - "테크웨어" → style: ["테크웨어"]
    - "고프코어" → style: ["고프코어"]
    - "스포티" → style: ["스포티"]
    - "발레코어" → style: ["발레코어"]

    출력 규칙:
    - 반드시 JSON만 출력한다.
    - 존재하지 않는 태그는 빈 리스트로 출력한다.
    - 스키마 외 태그는 절대 생성하지 않는다.

    스키마:
    {json.dumps(TAG_SCHEMA, ensure_ascii=False)}
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ]

    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True
    )

    inputs = tokenizer(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=150,
            do_sample=False
        )

    generated = tokenizer.decode(
        outputs[0][inputs["input_ids"].shape[1]:],
        skip_special_tokens=True
    ).strip()

    match = re.search(r"\{[\s\S]*\}", generated)
    parsed = {}

    if match:
        try:
            parsed = json.loads(match.group())
        except Exception:
            parsed = {}

    final_parsed = {k: [] for k in TAG_SCHEMA.keys()}

    for k, allowed_values in TAG_SCHEMA.items():
        vals = parsed.get(k, [])
        if vals is None:
            vals = []
        if isinstance(vals, str):
            vals = [vals]

        for v in vals:
            if v in allowed_values:
                final_parsed[k].append(v)

    normalized_text = user_text.replace(" ", "")
    for field, mapping in SYNONYMS.items():
        for raw_word, normalized_word in mapping.items():
            if raw_word in normalized_text and normalized_word not in final_parsed[field]:
                final_parsed[field].append(normalized_word)

    return final_parsed


def convert_color_tags_to_english(tags: dict) -> dict:
    converted = {k: list(v) for k, v in tags.items()}

    eng_colors = []
    for color in converted.get("color", []):
        eng_colors.append(KOR_TO_ENG_COLOR.get(color, color.lower()))

    converted["color"] = eng_colors
    return converted


def empty_accumulated_tags():
    return {k: [] for k in TAG_SCHEMA.keys()}


def is_reset_command(user_text: str) -> bool:
    reset_words = ["초기화", "다시", "리셋", "처음부터", "조건 초기화"]
    return any(word in user_text for word in reset_words)


def merge_tags(accumulated_tags: dict, new_tags: dict) -> dict:
    merged = {k: list(v) for k, v in accumulated_tags.items()}

    for key, values in new_tags.items():
        for value in values:
            if value not in merged[key]:
                merged[key].append(value)

    return merged


async def request_mllm(user_text: str, accumulated_tags: dict, host=SERVER_HOST, port=SERVER_PORT):
    reader, writer = await asyncio.open_connection(host, port)

    payload = json.dumps(
        {
            "user_text": user_text,
            "accumulated_tags": accumulated_tags
        },
        ensure_ascii=False
    ).encode("utf-8")

    writer.write(struct.pack("!I", len(payload)))
    writer.write(payload)
    await writer.drain()

    header = await reader.readexactly(4)
    resp_len = struct.unpack("!I", header)[0]
    resp_data = await reader.readexactly(resp_len)
    result = json.loads(resp_data.decode("utf-8"))

    writer.close()
    await writer.wait_closed()
    return result


def format_colors(colors_value):
    if not colors_value:
        return "-"
    if isinstance(colors_value, list):
        return ", ".join(map(str, colors_value))
    if isinstance(colors_value, str):
        try:
            parsed = json.loads(colors_value)
            if isinstance(parsed, list):
                return ", ".join(map(str, parsed))
        except Exception:
            return colors_value
    return str(colors_value)


def print_pretty_results(result: dict):
    global IS_FIRST_RUN

    debug = result.get("debug", {})
    db_info = debug.get("db_dump", {})

    if IS_FIRST_RUN and db_info:
        print("\n[DB LOAD INFO]")
        print(f"전체 상품 수 : {db_info.get('total_count', 0)}개")
        print(f"재고 기준 상품 수 : {db_info.get('filtered_count', 0)}개\n")
        IS_FIRST_RUN = False
        
    print("\n" + "═" * 60)

    if result.get("error"):
        print("서버 오류")
        print(f"  {result['error']}")
        print("═" * 60)
        return

    items = result.get("results", [])

    if not items:
        print("조건에 맞는 상품을 찾지 못했습니다. 검색어를 다시 입력해주세요.")
        message = result.get("message")
        if message:
            print(f"  {message}")
        print("═" * 60)
        return

    print(f"추천 신발 결과 ({len(items)}개)")
    print("═" * 60)

    for idx, item in enumerate(items, start=1):
        print(f"  [{idx}]")
        print(f"  브랜드 : {item.get('brand', '-')}")
        print(f"  모델명 : {item.get('model', '-')}")
        print(f"  색상   : {format_colors(item.get('colors'))}")

        price = item.get("price")
        if isinstance(price, int):
            print(f"  가격   : {price:,}원")
        else:
            print(f"  가격   : {price}")

        score = item.get("score")
        if isinstance(score, (int, float)):
            print(f"  점수   : {score}")

        print(f"  태그   : {item.get('tags', '-')}")
        print(f"  이미지 : {item.get('image_url', '-')}")
        print("-" * 60)

    debug = result.get("debug", {})
    if debug:
        print("  [디버그]")
        print(f"  accumulated_tags : {debug.get('accumulated_tags')}")
        print(f"  target_model     : {debug.get('target_model')}")
        print(f"  mentioned_brands : {debug.get('mentioned_brands')}")

        db_dump = debug.get("db_dump", {})
        if db_dump:
            print("  [DB DUMP 수신 정보]")
            print(f"  shoes 테이블              : {db_dump.get('shoes_table')}")
            print(f"  inventory 테이블          : {db_dump.get('inventory_table')}")
            print(f"  JOIN 조건                 : {db_dump.get('join_condition')}")
            print(f"  shoes 전체 상품 수         : {db_dump.get('shoes_total_count')}")
            print(f"  shoes_inventory 전체 row 수: {db_dump.get('inventory_total_count')}")
            print(f"  stock 0 이하 제외 상품 수  : {db_dump.get('excluded_zero_stock_count')}")
            print(f"  추천 후보 로드 상품 수     : {db_dump.get('loaded_available_count')}")

            samples = db_dump.get("sample_loaded_products", [])
            if samples:
                print("  [DB에서 받아온 추천 후보 샘플]")
                for sample in samples[:5]:
                    print(
                        f"    - id={sample.get('id')}, "
                        f"brand={sample.get('brand')}, "
                        f"model={sample.get('model')}, "
                        f"stock={sample.get('stock')}"
                    )
        print("═" * 60)


async def send_request(user_text: str, accumulated_tags: dict):
    print(f"\n{'═' * 60}")
    print(f"  [User → LLM] 원문 입력: '{user_text}'")
    print(f"{'═' * 60}")

    if is_reset_command(user_text):
        accumulated_tags = empty_accumulated_tags()
        print("  ✅ 검색 조건이 초기화되었습니다.")
        return accumulated_tags

    # 1) 기존 방식: TAG_SCHEMA + SYNONYMS 기반 규칙 추출 먼저 수행
    rule_tags, unmatched_keywords, matched_words = extract_tags_by_rules(user_text)

    # 2) 태그로 변환되지 않은 의미 있는 단어가 있을 때만 LLM 사용
    if unmatched_keywords:
        print(f"  규칙 기반 매칭 단어: {matched_words}")
        print(f"  태그 미매칭 표현: {unmatched_keywords}")
        print("  → 미매칭 표현이 있어 LLM 태그 추출을 실행합니다.")

        llm_tags = extract_tags_with_llm(user_text)
        new_tags = merge_tags(rule_tags, llm_tags)
    else:
        print(f"  규칙 기반 매칭 단어: {matched_words}")
        print("  → 모든 의미 있는 표현이 태그로 처리되어 LLM 호출을 생략합니다.")
        llm_tags = empty_tag_result()
        new_tags = rule_tags

    new_tags = convert_color_tags_to_english(new_tags)
    accumulated_tags = merge_tags(accumulated_tags, new_tags)

    print(f"  규칙 기반 태그: {convert_color_tags_to_english(rule_tags)}")
    print(f"  LLM 추출 태그: {convert_color_tags_to_english(llm_tags)}")
    print(f"  최종 신규 태그(영문 색상 반영): {new_tags}")
    print(f"  누적 태그: {accumulated_tags}")

    result = await request_mllm(user_text, accumulated_tags)
    print_pretty_results(result)

    return accumulated_tags


async def main():
    print("═══ LLM + M_LLM 연동 테스트 클라이언트 ═══")
    print(f"서버: {SERVER_HOST}:{SERVER_PORT}")
    print("종료: q 또는 Ctrl+C")
    print("초기화: '초기화' 입력\n")

    accumulated_tags = empty_accumulated_tags()

    while True:
        try:
            user_text = input("검색 문장 입력 (예: 검정 나이키 러닝화 추천): ").strip()
            if user_text.lower() == "q":
                print("종료합니다.")
                break
            if not user_text:
                continue

            accumulated_tags = await send_request(user_text, accumulated_tags)

        except (KeyboardInterrupt, EOFError):
            print("\n종료합니다.")
            break


if __name__ == "__main__":
    asyncio.run(main())