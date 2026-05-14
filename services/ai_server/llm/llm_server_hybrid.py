import asyncio
import json
import struct
import re
import difflib
import pymysql
import random
import time
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig

# ─────────────────────────────────────────────
# MySQL 접속 설정
# ─────────────────────────────────────────────

DB_CONFIG = {
    "host": "192.168.1.121",
    "user": "admin",
    "password": "team1!",
    "database": "MSS_DB",
    "charset": "utf8mb4",
}

SHOES_TABLE_NAME = "llm_shoes"

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 9000
TOP_K = 3

MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"

_tokenizer = None
_llm_model = None

# ─────────────────────────────────────────────
# 태그 스키마
# ─────────────────────────────────────────────

TAG_SCHEMA = {
    "activity": ["러닝", "웨이트", "등산", "축구", "농구", "데이트", "출근", "일상", "격식", "캠핑", "물놀이"],
    "style": ["힙한", "무난한", "깔끔한", "화려한", "빈티지", "클래식", "귀여운", "레트로", "테크웨어", "고프코어", "스포티", "발레코어"],
    "feature": ["쿠션감", "발볼 넓음", "방수", "키높이", "가벼움", "통기성", "미끄럼 방지", "편안함", "내구성", "보온성"],
    "color": ["white", "black", "gray", "grey", "red", "orange", "yellow", "green", "blue", "purple", "brown", "beige", "silver", "navy", "pink"],
    "brand": ["나이키", "아디다스", "뉴발란스", "반스", "컨버스", "아식스", "살로몬", "오니츠카타이거", "푸마", "미즈노", "킨", "호카", "닥터마틴", "어그", "리복"],
    "season_weather": ["봄/가을용", "여름용", "겨울용", "사계절용", "우천용"],
    "price": ["가성비", "일반", "프리미엄"],
    "target": ["남성용", "여성용", "공용"],
}

WEIGHTS = {
    "activity": 5,
    "style": 3,
    "feature": 4,
    "color": 3,
    "brand": 5,
    "season_weather": 3,
    "price": 4,
    "target": 3,
}

MODEL_SYNONYMS = {
    "에어푸스": "에어 포스 1 07",
    "에어포스": "에어 포스 1 07",
    "에어포스1": "에어 포스 1 07",
    "에어포스107": "에어 포스 1 07",
    "삼바오쥐": "삼바 OG",
    "삼바오지": "삼바 OG",
    "젤카야노": "젤 카야노 14",
    "보메로": "줌 보메로 5",
    "카야노": "젤 카야노 14",
    "님버스": "젤 님버스 26",
    "샨티": "샨티 슬라이드",
    "멕시코66": "멕시코 66",
    "스피드캣": "스피드캣 OG",
}

COLOR_SYNONYMS = {
    "빨강": "red", "빨간색": "red", "빨간": "red", "붉은색": "red", "레드": "red", "버건디": "red",
    "주황": "orange", "주황색": "orange", "오렌지색": "orange", "오렌지": "orange",
    "노랑": "yellow", "노란색": "yellow", "노란": "yellow", "황색": "yellow", "옐로우": "yellow", "머스타드": "yellow",
    "초록": "green", "초록색": "green", "녹색": "green", "그린": "green", "카키": "green", "올리브": "green",
    "파랑": "blue", "파란색": "blue", "파란": "blue", "하늘색": "blue", "블루": "blue",
    "남색": "navy", "네이비": "navy", "곤색": "navy",
    "보라": "purple", "보라색": "purple", "퍼플": "purple",
    "핑크": "pink", "분홍": "pink", "분홍색": "pink", "핫핑크": "pink",
    "하얀색": "white", "흰색": "white", "하얀": "white", "올화이트": "white", "화이트": "white",
    "검정": "black", "검정색": "black", "검은색": "black", "검은": "black", "올블랙": "black", "블랙": "black",
    "회색": "gray", "그레이": "gray", "잿빛": "gray",
    "갈색": "brown", "브라운": "brown", "밤색": "brown", "고동색": "brown",
    "베이지": "beige", "살구색": "beige", "아이보리": "beige", "크림": "beige", "크림색": "beige",
    "은색": "silver", "실버": "silver", "메탈릭": "silver",
}

RULE_SYNONYMS = {
    "activity": {
        "데일리": "일상",
        "평소": "일상",
        "회사": "출근",
        "출근룩": "출근",
        "조깅": "러닝",
        "러닝화": "러닝",
        "헬스": "웨이트",
        "웨이트": "웨이트",
        "근력운동": "웨이트",
        "헬스장": "웨이트",
        "소개팅": "데이트",
        "면접": "격식",
        "결혼식": "격식",
        "풋살": "축구",
        "축구화": "축구",
        "농구화": "농구",
        "농구": "농구",
        "등산": "등산",
        "캠핑": "캠핑",
        "물놀이": "물놀이",
    },
    "style": {
        "힙한": "힙한",
        "무난한": "무난한",
        "깔끔한": "깔끔한",
        "화려한": "화려한",
        "빈티지": "빈티지",
        "클래식": "클래식",
        "귀여운": "귀여운",
        "레트로": "레트로",
        "테크웨어": "테크웨어",
        "고프코어": "고프코어",
        "스포티": "스포티",
        "발레코어": "발레코어",
        "심플한": "깔끔한",
        "베이직한": "무난한",
        "튀는": "화려한",
        "아웃도어": "고프코어",
    },
    "feature": {
        "푹신한": "쿠션감",
        "쿠션": "쿠션감",
        "발편한": "편안함",
        "발 편한": "편안함",
        "편하고": "편안함",
        "편한": "편안함",
        "넓은발볼": "발볼 넓음",
        "발볼큰": "발볼 넓음",
        "발볼 넓": "발볼 넓음",
        "비올때": "방수",
        "방수": "방수",
        "안미끄러운": "미끄럼 방지",
        "미끄럼": "미끄럼 방지",
        "가벼운": "가벼움",
        "따뜻한": "보온성",
        "털신": "보온성",
        "통기성": "통기성",
        "내구성": "내구성",
        "키높이": "키높이",
    },
    "season_weather": {
        "여름": "여름용",
        "겨울": "겨울용",
        "봄": "봄/가을용",
        "가을": "봄/가을용",
        "사계절": "사계절용",
        "우천": "우천용",
    },
    "price": {
        "저렴한": "가성비",
        "싼": "가성비",
        "가성비": "가성비",
        "일반": "일반",
        "고가": "프리미엄",
        "비싼": "프리미엄",
        "프리미엄": "프리미엄",
    },
    "target": {
        "남성": "남성용",
        "남자": "남성용",
        "여성": "여성용",
        "여자": "여성용",
        "공용": "공용",
        "남녀공용": "공용",
    },
}

TAG_EVIDENCE_KEYWORDS = {
    "activity": {
        "러닝": ["뛰", "달리", "조깅", "마라톤", "런닝", "러닝"],
        "웨이트": ["헬스", "웨이트", "근력", "리프팅"],
        "등산": ["등산", "산", "트레킹"],
        "축구": ["축구", "풋살"],
        "농구": ["농구"],
        "데이트": ["데이트", "소개팅"],
        "출근": ["출근", "회사", "직장"],
        "격식": ["격식", "정장", "면접", "결혼식"],
        "캠핑": ["캠핑"],
        "물놀이": ["물놀이", "바다", "계곡", "수영장"],
        "일상": ["일상", "평소", "데일리"],
    },
    "style": {
        "힙한": ["힙", "스트릿", "트렌디", "유행"],
        "무난한": ["무난", "어디에나", "코디쉬운", "튀지않는", "과하지않은"],
        "깔끔한": ["깔끔", "단정", "깨끗"],
        "화려한": ["화려", "눈에띄", "포인트"],
        "빈티지": ["빈티지", "오래된감성"],
        "클래식": ["클래식", "기본", "유행타지"],
        "귀여운": ["귀여", "아기자기"],
        "레트로": ["레트로", "복고"],
        "테크웨어": ["테크웨어", "미래적", "기능적"],
        "고프코어": ["고프코어", "아웃도어"],
        "스포티": ["스포티", "운동복"],
        "발레코어": ["발레코어"],
    },
    "feature": {
        "방수": ["비", "젖", "물", "장마", "스며들"],
        "편안함": ["편", "안아픈", "피곤", "오래걸", "오래서"],
        "쿠션감": ["푹신", "쿠션", "충격", "부담"],
        "미끄럼 방지": ["미끄", "접지"],
        "통기성": ["시원", "땀", "통풍", "답답"],
        "보온성": ["따뜻", "시린", "시리", "추운"],
        "키높이": ["키높", "키커", "키 커", "커보", "커 보"],
        "가벼움": ["가볍", "무겁지"],
        "내구성": ["튼튼", "오래신", "망가지", "내구"],
    },
    "season_weather": {
        "여름용": ["여름", "시원", "땀", "통풍"],
        "겨울용": ["겨울", "추운", "따뜻", "시린"],
        "우천용": ["비", "장마", "우천"],
    },
}


# ─────────────────────────────────────────────
# 기본 유틸
# ─────────────────────────────────────────────

def empty_tags():
    return {k: [] for k in TAG_SCHEMA.keys()}


def normalize_text(text):
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", str(text)).lower()


def normalize_model_text(text):
    text = str(text).lower()
    text = re.sub(r"[^a-z0-9가-힣\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_codes(text):
    text = str(text).lower().replace("-", "").replace(".", "")
    return re.findall(r"[a-z]*\d+[a-z]*", text)


def decompose_hangul(text):
    CHOSEONG = [
        "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
        "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"
    ]
    JUNGSEONG = [
        "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ",
        "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ"
    ]
    JONGSEONG = [
        "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ",
        "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ",
        "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"
    ]

    result = []
    for char in str(text):
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            base = code - 0xAC00
            cho = base // 588
            jung = (base % 588) // 28
            jong = base % 28
            result.append(CHOSEONG[cho])
            result.append(JUNGSEONG[jung])
            if JONGSEONG[jong]:
                result.append(JONGSEONG[jong])
        else:
            if char.strip():
                result.append(char.lower())
    return "".join(result)


def dedup_list(values):
    out = []
    seen = set()
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def normalize_accumulated_tags(tags):
    fixed = empty_tags()
    if not isinstance(tags, dict):
        return fixed

    if "colors" in tags and "color" not in tags:
        tags["color"] = tags.get("colors", [])

    for key in TAG_SCHEMA.keys():
        vals = tags.get(key, [])
        if vals is None:
            vals = []
        if isinstance(vals, str):
            vals = [vals]
        fixed[key] = dedup_list([str(v).strip() for v in vals if str(v).strip()])

    return fixed


def merge_tags(base, new):
    merged = normalize_accumulated_tags(base)
    new = normalize_accumulated_tags(new)

    for key, vals in new.items():
        for value in vals:
            if value not in merged[key]:
                merged[key].append(value)

    return merged


def parse_color_field(color_raw):
    if color_raw is None:
        return []

    if isinstance(color_raw, list):
        return [str(c).strip().lower() for c in color_raw if str(c).strip()]

    if isinstance(color_raw, str):
        color_raw = color_raw.strip()
        if not color_raw:
            return []

        try:
            parsed = json.loads(color_raw)
            if isinstance(parsed, list):
                return [str(c).strip().lower() for c in parsed if str(c).strip()]
        except Exception:
            pass

        return [color_raw.lower()]

    return [str(color_raw).strip().lower()]

def normalize_db_colors(colors):
    result = []
    for c in colors:
        c = str(c).strip().lower()
        result.append(COLOR_SYNONYMS.get(c, c))
    return result

def color_list_to_text(color_value):
    if isinstance(color_value, list):
        return " ".join(str(c) for c in color_value if str(c).strip())
    return str(color_value)


def get_table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    return [row["Field"] for row in cursor.fetchall()]


def pick_first_existing(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


# ─────────────────────────────────────────────
# 태그 추출
# ─────────────────────────────────────────────

def extract_tags_rule_based(user_text):
    tags = empty_tags()
    compact = str(user_text).replace(" ", "")

    for raw, norm in COLOR_SYNONYMS.items():
        if raw.replace(" ", "") in compact and norm not in tags["color"]:
            tags["color"].append(norm)

    for brand in TAG_SCHEMA["brand"]:
        if brand in user_text and brand not in tags["brand"]:
            tags["brand"].append(brand)

    for field, mapping in RULE_SYNONYMS.items():
        for raw, norm in mapping.items():
            if raw.replace(" ", "") in compact and norm not in tags[field]:
                tags[field].append(norm)

    for field, allowed_values in TAG_SCHEMA.items():
        if field == "color":
            continue
        for value in allowed_values:
            if value.replace(" ", "") in compact and value not in tags[field]:
                tags[field].append(value)

    return tags


def extract_tags_by_evidence(user_text):
    tags = empty_tags()
    compact = str(user_text).replace(" ", "")

    for field, tag_map in TAG_EVIDENCE_KEYWORDS.items():
        for tag, keywords in tag_map.items():
            if any(keyword.replace(" ", "") in compact for keyword in keywords):
                if tag not in tags[field]:
                    tags[field].append(tag)

    return tags

def load_llm_model():
    global _tokenizer, _llm_model

    if _tokenizer is not None and _llm_model is not None:
        return _tokenizer, _llm_model

    print("⏳ LLM 모델 로딩 중...")

    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_quant_type="nf4"
    )

    _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    _llm_model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        device_map="auto",
        quantization_config=quant_config
    )

    print("✅ LLM 모델 로딩 완료")
    return _tokenizer, _llm_model


def detect_unmatched_terms(user_text, rule_tags):
    compact = str(user_text).replace(" ", "")
    matched_words = []

    for field, vals in rule_tags.items():
        for v in vals:
            if str(v) in user_text or str(v).replace(" ", "") in compact:
                matched_words.append(str(v))

    # 색상 원문도 제거
    for raw in COLOR_SYNONYMS.keys():
        if raw.replace(" ", "") in compact:
            matched_words.append(raw)

    # 브랜드 원문도 제거
    for brand in TAG_SCHEMA["brand"]:
        if brand in user_text:
            matched_words.append(brand)

    # 룰 동의어 원문도 제거
    for mapping in RULE_SYNONYMS.values():
        for raw in mapping.keys():
            if raw.replace(" ", "") in compact:
                matched_words.append(raw)

    cleaned = user_text
    for w in sorted(set(matched_words), key=len, reverse=True):
        cleaned = cleaned.replace(w, " ")

    # 일반 불용어 제거
    stopwords = [
        "신발", "운동화", "추천", "찾아줘", "찾아", "주세요",
        "좀", "하나", "거", "것", "용", "신는", "신을"
    ]

    terms = []
    for token in cleaned.split():
        token = token.strip()
        if not token:
            continue
        if token in stopwords:
            continue
        if len(token) <= 1:
            continue
        terms.append(token)

    return terms


def extract_tags_with_llm(text):
    tokenizer, model = load_llm_model()

    system_prompt = f"""
너는 신발 검색 태그 추출기다.

사용자 표현에서 직접 의미가 드러나는 태그만 추출한다.
브랜드 이미지, 일반 상식, 제품 이미지, 추측으로 태그를 추가하지 마라.

허용되는 의미 기반 추론:
- "뛰는", "달리는", "조깅", "마라톤" → activity: ["러닝"]
- "비 오는 날", "젖는", "물 안 스며드는", "장마철" → feature: ["방수"]
- "편한", "오래 걸어도 안 아픈", "안 피곤한" → feature: ["편안함"]
- "푹신한", "충격 흡수", "발바닥 부담 적은" → feature: ["쿠션감"]
- "안 미끄러운", "접지력 좋은" → feature: ["미끄럼 방지"]
- "발 안 시린", "따뜻한" → feature: ["보온성"]
- "시원한", "땀 덜 차는", "통풍 잘 되는" → feature: ["통기성"]
- "키 커 보이는", "키높이" → feature: ["키높이"]
- "가볍게 신는", "무겁지 않은" → feature: ["가벼움"]

금지:
- "운동화" 단독으로 activity를 추론하지 마라.
- 브랜드명만 보고 style, feature를 추가하지 마라.
- "나이키"만 보고 "힙한"을 추가하지 마라.
- "러닝"만 보고 "쿠션감"을 자동 추가하지 마라.
- style 태그는 사용자가 직접 스타일 표현을 말한 경우에만 추가한다.

출력은 JSON만 한다.
스키마:
{json.dumps(TAG_SCHEMA, ensure_ascii=False)}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text}
    ]

    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True
    )

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=180,
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

    final = empty_tags()

    for field, allowed_values in TAG_SCHEMA.items():
        vals = parsed.get(field, [])
        if vals is None:
            vals = []
        if isinstance(vals, str):
            vals = [vals]

        for v in vals:
            if v in allowed_values and v not in final[field]:
                final[field].append(v)

    return final


def filter_llm_tags_by_evidence(llm_tags, evidence_text):
    filtered = empty_tags()
    compact = str(evidence_text).replace(" ", "")

    for field, tag_map in TAG_EVIDENCE_KEYWORDS.items():
        for tag, keywords in tag_map.items():
            if tag in llm_tags.get(field, []):
                if any(keyword.replace(" ", "") in compact for keyword in keywords):
                    filtered[field].append(tag)

    # color / brand는 LLM fallback에서는 보통 받지 않음
    # 필요하면 직접 언급된 경우만 허용
    for color in llm_tags.get("color", []):
        if color in compact:
            filtered["color"].append(color)

    for brand in llm_tags.get("brand", []):
        if brand in evidence_text:
            filtered["brand"].append(brand)

    return filtered

def extract_hybrid_tags_with_llm(user_text, accumulated_tags):
    # 1차: 규칙 기반
    rule_tags = extract_tags_rule_based(user_text)

    # 규칙 기반으로 매칭 안 된 표현 찾기
    unmatched_terms = detect_unmatched_terms(user_text, rule_tags)

    print(f"  규칙 기반 태그: {rule_tags}")
    print(f"  태그 미매칭 표현: {unmatched_terms}")

    # 2차: unmatched 있을 때만 LLM 호출
    if unmatched_terms:
        print("  → 미매칭 표현이 있어 LLM 태그 추출 실행")

        llm_tags = extract_tags_with_llm(" ".join(unmatched_terms))

        # hallucination 방지용 evidence 검증
        llm_tags = filter_llm_tags_by_evidence(
            llm_tags,
            " ".join(unmatched_terms)
        )

        print(f"  LLM 추출 태그: {llm_tags}")

        new_tags = merge_tags(rule_tags, llm_tags)

    else:
        llm_tags = empty_tags()
        new_tags = rule_tags

    final_tags = merge_tags(accumulated_tags, new_tags)

    print(f"  최종 누적 태그: {final_tags}")

    return final_tags


# ─────────────────────────────────────────────
# DB 로드: llm_shoes만 사용
# ─────────────────────────────────────────────

def load_inventory_from_db():
    """
    shoes_inventory JOIN 없이 llm_shoes 테이블만 사용한다.
    재고 정보는 사용하지 않고 stock=1로 고정한다.
    """
    inventory = []
    conn = pymysql.connect(**DB_CONFIG)

    total_count = 0

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            columns = get_table_columns(cursor, SHOES_TABLE_NAME)
            column_set = set(columns)

            id_col = pick_first_existing(column_set, ["id"])
            shoe_id_col = pick_first_existing(column_set, ["shoe_id", "ssid", "product_id", "item_id"])
            brand_col = pick_first_existing(column_set, ["brand"])
            model_col = pick_first_existing(column_set, ["model", "name", "shoe_name"])
            color_col = pick_first_existing(column_set, ["colors", "color"])
            image_col = pick_first_existing(column_set, ["image_url", "image", "img_url"])
            price_col = pick_first_existing(column_set, ["price"])
            tags_col = pick_first_existing(column_set, ["tags", "tag"])

            if not brand_col or not model_col:
                raise ValueError(
                    f"{SHOES_TABLE_NAME} 테이블에 brand/model 컬럼이 필요합니다. 현재 컬럼: {columns}"
                )

            select_parts = []

            if id_col:
                select_parts.append(f"`{id_col}` AS id")
            else:
                select_parts.append("NULL AS id")

            if shoe_id_col:
                select_parts.append(f"`{shoe_id_col}` AS shoe_id")
            elif id_col:
                select_parts.append(f"`{id_col}` AS shoe_id")
            else:
                select_parts.append("NULL AS shoe_id")

            select_parts.append(f"`{brand_col}` AS brand")
            select_parts.append(f"`{model_col}` AS model")

            if color_col:
                select_parts.append(f"`{color_col}` AS colors")
            else:
                select_parts.append("NULL AS colors")

            if image_col:
                select_parts.append(f"`{image_col}` AS image_url")
            else:
                select_parts.append("'' AS image_url")

            if price_col:
                select_parts.append(f"`{price_col}` AS price")
            else:
                select_parts.append("0 AS price")

            if tags_col:
                select_parts.append(f"`{tags_col}` AS tags")
            else:
                select_parts.append("'' AS tags")

            cursor.execute(f"SELECT COUNT(*) AS cnt FROM `{SHOES_TABLE_NAME}`")
            total_count = int(cursor.fetchone()["cnt"] or 0)

            sql = f"""
                SELECT
                    {", ".join(select_parts)}
                FROM `{SHOES_TABLE_NAME}`
            """

            cursor.execute(sql)
            rows = cursor.fetchall()

            for row in rows:
                color_raw = row.get("colors")
                color_parsed = parse_color_field(color_raw)
                color_parsed = normalize_db_colors(color_parsed)

                inventory.append({
                    "id": row.get("id"),
                    "shoe_id": row.get("shoe_id"),
                    "brand": row.get("brand") or "",
                    "model": row.get("model") or "",
                    "color": color_parsed,
                    "image_url": row.get("image_url") or "",
                    "price": int(row.get("price") or 0),
                    "tags": row.get("tags") or "",
                    "stock": 1,
                })

    finally:
        conn.close()

    return inventory, total_count


# ─────────────────────────────────────────────
# 모델명/추천 로직
# ─────────────────────────────────────────────

def find_best_model(user_text, inventory, threshold=0.62):
    clean_input = normalize_model_text(user_text)
    compact_input = normalize_text(user_text)

    for wrong, official in MODEL_SYNONYMS.items():
        if normalize_text(wrong) in compact_input:
            return official

    all_models = list(set([shoe["model"] for shoe in inventory if shoe.get("model")]))

    input_tokens = clean_input.split()
    input_codes = extract_codes(clean_input)
    input_jamo = decompose_hangul(compact_input)

    best_model = None
    best_score = 0.0

    weak_tokens = {
        "nike", "adidas", "new", "balance", "air", "zoom",
        "og", "low", "mid", "black", "white", "gel", "wave"
    }

    for model in all_models:
        model_norm = normalize_model_text(model)
        model_compact = normalize_text(model)
        model_tokens = model_norm.split()
        model_codes = extract_codes(model_norm)
        model_jamo = decompose_hangul(model_compact)

        score = 0.0

        for code in input_codes:
            if code in model_codes:
                score += 1.0

        for ut in input_tokens:
            for mt in model_tokens:
                if ut == mt:
                    score += 0.4 if ut not in weak_tokens else 0.1

        token_sim = 0.0
        for ut in input_tokens:
            best_token_score = 0.0
            for mt in model_tokens:
                sim = difflib.SequenceMatcher(None, ut, mt).ratio()
                best_token_score = max(best_token_score, sim)
            token_sim += best_token_score

        if input_tokens:
            token_sim /= len(input_tokens)

        score_full = difflib.SequenceMatcher(None, compact_input, model_compact).ratio()
        score_jamo = difflib.SequenceMatcher(None, input_jamo, model_jamo).ratio()

        score += max(token_sim, score_full, score_jamo)

        if compact_input and (compact_input in model_compact or model_compact in compact_input):
            score += 0.2

        if score > best_score:
            best_score = score
            best_model = model

    if best_score >= threshold:
        return best_model

    return None


def extract_brands(user_text):
    found = []
    for brand in TAG_SCHEMA["brand"]:
        if brand in user_text:
            found.append(brand)
    return dedup_list(found)


def build_db_string(shoe):
    color_str = color_list_to_text(shoe.get("color", []))
    return f"{shoe.get('brand', '')} {shoe.get('model', '')} {color_str} {shoe.get('tags', '')}".lower().replace(" ", "")


def match_all_filters(shoe, accumulated_tags):
    db_str = build_db_string(shoe)

    for field, vals in accumulated_tags.items():
        if not vals:
            continue

        if not any(str(v).lower().replace(" ", "") in db_str for v in vals):
            return False

    return True


def score_shoe(shoe, accumulated_tags, target_model, mentioned_brands, user_text):
    score = 0
    db_str = build_db_string(shoe)

    for field, vals in accumulated_tags.items():
        if not vals:
            continue

        weight = WEIGHTS.get(field, 3)
        for v in vals:
            if str(v).lower().replace(" ", "") in db_str:
                score += weight

    if target_model and normalize_text(target_model) == normalize_text(shoe.get("model", "")):
        score += 100

    for brand in mentioned_brands:
        if normalize_text(brand) in normalize_text(shoe.get("brand", "")):
            score += 30

    user_compact = normalize_text(user_text)
    if user_compact and user_compact in normalize_text(shoe.get("model", "")):
        score += 20
    if user_compact and user_compact in normalize_text(shoe.get("brand", "")):
        score += 15

    target_colors = accumulated_tags.get("color", [])
    for color in target_colors:
        if any(normalize_text(color) in normalize_text(col) for col in shoe.get("color", [])):
            score += 20

    return score


def get_recommendations(user_text, accumulated_tags, inventory):
    mentioned_brands = extract_brands(user_text)
    target_model = find_best_model(user_text, inventory, threshold=0.62)

    if target_model:
        candidates = [
            s for s in inventory
            if normalize_text(s.get("model", "")) == normalize_text(target_model)
        ]
    else:
        candidates = [s for s in inventory if match_all_filters(s, accumulated_tags)]
        if not candidates:
            print("  ⚠️ AND 필터 후보 없음 → 점수 기반 전체 후보로 fallback")
            candidates = inventory

    ranked = []
    for shoe in candidates:
        score = score_shoe(shoe, accumulated_tags, target_model, mentioned_brands, user_text)

        if score > 0 or target_model or (not any(accumulated_tags.values()) and not target_model):
            ranked.append({
                "id": shoe.get("id"),
                "shoe_id": shoe.get("shoe_id"),
                "brand": shoe.get("brand"),
                "model": shoe.get("model"),
                "colors": shoe.get("color"),
                "price": int(shoe.get("price") or 0),
                "stock": int(shoe.get("stock") or 1),
                "image_url": shoe.get("image_url"),
                "tags": shoe.get("tags"),
                "score": score,
            })

    ranked = sorted(
        ranked,
        key=lambda x: (-x["score"], x["price"] if isinstance(x["price"], int) else 999999999)
    )

    top_pool = ranked[:10]
    random_results = random.sample(top_pool, min(TOP_K, len(top_pool)))

    return random_results, target_model, mentioned_brands


# ─────────────────────────────────────────────
# TCP 서버 핸들러
# ─────────────────────────────────────────────

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    print(f"\n╔══ backend 연결: {addr} ══╗")

    try:
        header = await reader.readexactly(4)
        length = struct.unpack("!I", header)[0]
        raw = await reader.readexactly(length)
        data = json.loads(raw.decode("utf-8"))

        user_text = data.get("user_text", "")
        incoming_tags = data.get("accumulated_tags", {})
        accumulated_tags = normalize_accumulated_tags(incoming_tags)

        print(f'backend → m_llm  원문 입력: "{user_text}"')
        print(f"\n{'━' * 50}")
        print("  STEP 1 │ 입력 수신")
        print(f'  user_text       : "{user_text}"')
        print(f"  incoming_tags   : {incoming_tags}")

        accumulated_tags = extract_hybrid_tags_with_llm(user_text, accumulated_tags)

        db_start = time.time()
        inventory, total_count = load_inventory_from_db()
        print(f"\n  STEP 2 │ DB 로드")
        print(f"  전체 상품 수: {total_count}")
        print(f"  llm_shoes 로드 상품 수: {len(inventory)}")
        print(f"  ⏱ DB 로드 시간: {time.time() - db_start:.2f}초")

        print("\n[DEBUG] inventory brand/model 샘플")
        for s in inventory[:10]:
            print(
                f"brand={repr(s['brand'])}, "
                f"model={repr(s['model'])}, "
                f"color={repr(s['color'])}, "
                f"stock={repr(s.get('stock'))}"
            )

        rank_start = time.time()
        ranked, target_model, mentioned_brands = get_recommendations(
            user_text,
            accumulated_tags,
            inventory
        )
        print(f"  ⏱ 추천 계산 시간: {time.time() - rank_start:.2f}초")

        print(f"\n  STEP 3 │ 분석 결과")
        print(f"  누적 태그: {accumulated_tags}")
        print(f"  모델 직접 매칭: {target_model}")
        print(f"  브랜드 감지: {mentioned_brands}")

        print(f"\n  STEP 4 │ 추천 결과")
        if ranked:
            print(f"  최종 추천 개수: {len(ranked)}개")
            print("  ┌─────────────────────────────────────────")
            for idx, item in enumerate(ranked, start=1):
                print(f"  │ [{idx}] 브랜드 : {item['brand']}")
                print(f"  │     모델명 : {item['model']}")
                print(f"  │     색상   : {', '.join(item['colors']) if isinstance(item['colors'], list) else item['colors']}")
                print(f"  │     가격   : {item['price']:,}원")
                print(f"  │     재고   : {item.get('stock', 1)}")
                print(f"  │     점수   : {item['score']}")
            print("  └─────────────────────────────────────────")
        else:
            print("  추천 결과 없음")

        response = {
            "results": ranked,
            "count": len(ranked),
            "debug": {
                "accumulated_tags": accumulated_tags,
                "target_model": target_model,
                "mentioned_brands": mentioned_brands,
                "db_dump": {
                    "total_count": total_count,
                    "filtered_count": len(inventory),
                    "shoes_table": SHOES_TABLE_NAME,
                    "inventory_table": None,
                    "stock_mode": "ignored_llm_shoes_only",
                },
            },
        }

        if not ranked:
            response["message"] = "매칭 상품 없음"

        resp_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")

        writer.write(struct.pack("!I", len(resp_bytes)))
        writer.write(resp_bytes)
        await writer.drain()

        print(f"m_llm → backend  응답 전송 완료 ({len(resp_bytes)} bytes)")

    except asyncio.IncompleteReadError:
        print(f"연결 끊김: {addr}")
    except Exception as e:
        print(f"오류: {type(e).__name__}: {e}")
        err = json.dumps(
            {
                "error": f"{type(e).__name__}: {str(e)}"
            },
            ensure_ascii=False
        ).encode("utf-8")
        try:
            writer.write(struct.pack("!I", len(err)))
            writer.write(err)
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        await writer.wait_closed()
        print(f"╚══ backend 연결 종료: {addr} ══╝\n")


# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────

async def main():
    print("═══ M_LLM Hybrid 서버 시작 준비 ═══")
    print("※ shoes_inventory JOIN 없이 llm_shoes 테이블만 사용합니다.")

    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{SHOES_TABLE_NAME}`")
            shoes_count = cur.fetchone()[0]

            print(f" ✅ DB 연결 성공 ({DB_CONFIG['host']}/{DB_CONFIG['database']})")
            print(f" ✅ {SHOES_TABLE_NAME} 테이블 행 수: {shoes_count}")
            print(" ✅ shoes_inventory 테이블은 사용하지 않습니다.")
        conn.close()

    except Exception as e:
        print(f"DB 연결 실패: {type(e).__name__}: {e}")
        return

    server = await asyncio.start_server(handle_client, SERVER_HOST, SERVER_PORT)
    addr = server.sockets[0].getsockname()

    print(f"\nTCP 서버 대기 중: {addr[0]}:{addr[1]}")
    print("moosinsa_service.py에서 TCP 요청을 받을 준비 완료\n")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
