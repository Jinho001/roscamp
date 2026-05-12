"""
sshopylcd_hangul.py
====================
두벌식 한글 입력기 + 가상 키보드 컴포넌트.

[참고] 동일 알고리즘이 apps/kiosk_ui/kiosk_search.py 에도 존재하나,
sshopylcd_ui 는 별도 기기(로봇 LCD)에서 실행되므로 kiosk_ui 코드를
import 할 수 없어 필요한 부분을 복제·축소 이식했다.

[제공]
  - HangulComposer: 자모 입력 상태머신
  - validate_query: 검색어 유효성 검사
  - VirtualKeyboard: 320x240 LCD에 맞춰 압축된 가상 키보드
"""

from PySide6.QtWidgets import (
    QWidget, QFrame, QPushButton, QHBoxLayout, QVBoxLayout, QSizePolicy
)
from PySide6.QtCore import Qt, QByteArray
from PySide6.QtSvgWidgets import QSvgWidget

from sshopylcd_common import (
    C_BG, C_DARK, C_BORDER, C_SUB, C_BROWN, C_BROWN_H, SVG_BACK
)

# ── Key surface palette ──────────────────────────────────────
C_KEY_BG  = "#F5F1EC"
C_KEY_SP  = "#D9D4CD"
C_KEY_HOV = "#E2DDD6"


# ════════════════════════════════════════════════════════════
#  두벌식 자모 정의
# ════════════════════════════════════════════════════════════
CHOSEONG = [
    'ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ',
    'ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'
]
JUNGSEONG = [
    'ㅏ','ㅐ','ㅑ','ㅒ','ㅓ','ㅔ','ㅕ','ㅖ','ㅗ','ㅘ','ㅙ','ㅚ',
    'ㅛ','ㅜ','ㅝ','ㅞ','ㅟ','ㅠ','ㅡ','ㅢ','ㅣ'
]
JONGSEONG = [
    '','ㄱ','ㄲ','ㄳ','ㄴ','ㄵ','ㄶ','ㄷ','ㄹ','ㄺ','ㄻ','ㄼ','ㄽ','ㄾ','ㄿ','ㅀ',
    'ㅁ','ㅂ','ㅄ','ㅅ','ㅆ','ㅇ','ㅈ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'
]

CONSONANTS = set('ㄱㄲㄳㄴㄵㄶㄷㄸㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅃㅄㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ')
VOWELS = set('ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ')

VOWEL_COMBINE = {
    ('ㅗ','ㅏ'):'ㅘ', ('ㅗ','ㅐ'):'ㅙ', ('ㅗ','ㅣ'):'ㅚ',
    ('ㅜ','ㅓ'):'ㅝ', ('ㅜ','ㅔ'):'ㅞ', ('ㅜ','ㅣ'):'ㅟ',
    ('ㅡ','ㅣ'):'ㅢ',
}
CONS_COMBINE = {
    ('ㄱ','ㅅ'):'ㄳ', ('ㄴ','ㅈ'):'ㄵ', ('ㄴ','ㅎ'):'ㄶ',
    ('ㄹ','ㄱ'):'ㄺ', ('ㄹ','ㅁ'):'ㄻ', ('ㄹ','ㅂ'):'ㄼ',
    ('ㄹ','ㅅ'):'ㄽ', ('ㄹ','ㅌ'):'ㄾ', ('ㄹ','ㅍ'):'ㄿ',
    ('ㄹ','ㅎ'):'ㅀ', ('ㅂ','ㅅ'):'ㅄ',
}
CONS_SPLIT = {v: k for k, v in CONS_COMBINE.items()}


def _cho_idx(c):  return CHOSEONG.index(c) if c in CHOSEONG else -1
def _jung_idx(v): return JUNGSEONG.index(v) if v in JUNGSEONG else -1
def _jong_idx(c): return JONGSEONG.index(c) if c in JONGSEONG else -1


def _compose(cho, jung, jong=''):
    ci, vi, ji = _cho_idx(cho), _jung_idx(jung), _jong_idx(jong)
    if ci < 0 or vi < 0 or ji < 0:
        return cho + jung + jong
    return chr(0xAC00 + ci * 21 * 28 + vi * 28 + ji)


# ── 검색어 유효성 검사 ────────────────────────────────────────
MIN_QUERY_LEN = 2
_HAN_START, _HAN_END = 0xAC00, 0xD7A3
_JAMO_RANGES = [(0x1100, 0x11FF), (0x3130, 0x318F)]


def _is_complete_hangul(ch: str) -> bool:
    return _HAN_START <= ord(ch) <= _HAN_END


def _is_jamo_only(ch: str) -> bool:
    cp = ord(ch)
    return any(s <= cp <= e for s, e in _JAMO_RANGES)


def _is_alphanumeric(ch: str) -> bool:
    return ch.isascii() and (ch.isalpha() or ch.isdigit())


def validate_query(query: str) -> str | None:
    """유효 시 None, 오류 시 사용자에게 보여줄 메시지."""
    q = query.strip()
    if not q:
        return "검색어를 입력해 주세요."
    if all(_is_jamo_only(ch) for ch in q if not ch.isspace()):
        return "완성된 글자로 입력해 주세요."
    has_meaningful = any(
        _is_complete_hangul(ch) or _is_jamo_only(ch) or _is_alphanumeric(ch)
        for ch in q
    )
    if not has_meaningful:
        return "한글 또는 영문·숫자를 포함해 주세요."
    meaningful_chars = [ch for ch in q if not ch.isspace()]
    if len(meaningful_chars) < MIN_QUERY_LEN:
        return "두 글자 이상 입력해 주세요."
    return None


# ════════════════════════════════════════════════════════════
#  HangulComposer
# ════════════════════════════════════════════════════════════
class HangulComposer:
    """두벌식 입력기 상태머신. push(key)/backspace()/flush()/text()/reset()."""
    def __init__(self):
        self._committed = ""
        self._cho = ""
        self._jung = ""
        self._jong = ""
        self._state = 0   # 0=비어있음 1=초 2=초+중 3=초+중+종

    def reset(self):
        self._committed = ""
        self._cho = self._jung = self._jong = ""
        self._state = 0

    def push(self, key: str) -> str:
        if key in VOWELS:
            self._push_vowel(key)
        elif key in CONSONANTS:
            self._push_consonant(key)
        else:
            self._committed += self._current_char() + key
            self._reset_state()
        return self.text()

    def backspace(self) -> str:
        if self._state == 3:
            if self._jong in CONS_SPLIT:
                self._jong = CONS_SPLIT[self._jong][0]
            else:
                self._jong = ""
                self._state = 2
        elif self._state == 2:
            if self._jung in {v for k, v in VOWEL_COMBINE.items()}:
                for (a, b), c in VOWEL_COMBINE.items():
                    if c == self._jung:
                        self._jung = a
                        break
            else:
                self._jung = ""
                self._state = 1
        elif self._state == 1:
            self._cho = ""
            self._state = 0
        elif self._committed:
            last = self._committed[-1]
            self._committed = self._committed[:-1]
            code = ord(last)
            if 0xAC00 <= code <= 0xD7A3:
                code -= 0xAC00
                ji = code % 28; code //= 28
                vi = code % 21; ci = code // 21
                self._cho = CHOSEONG[ci]
                self._jung = JUNGSEONG[vi]
                self._jong = JONGSEONG[ji]
                self._state = 3 if ji > 0 else 2
        return self.text()

    def flush(self) -> str:
        self._committed += self._current_char()
        self._reset_state()
        return self.text()

    def text(self) -> str:
        return self._committed + self._current_char()

    def _current_char(self) -> str:
        if self._state == 0: return ""
        if self._state == 1: return self._cho
        if self._state == 2: return _compose(self._cho, self._jung)
        if self._state == 3: return _compose(self._cho, self._jung, self._jong)
        return ""

    def _reset_state(self):
        self._cho = self._jung = self._jong = ""
        self._state = 0

    def _push_vowel(self, v: str):
        if self._state == 0:
            self._committed += v
        elif self._state == 1:
            self._jung = v; self._state = 2
        elif self._state == 2:
            combined = VOWEL_COMBINE.get((self._jung, v))
            if combined:
                self._jung = combined
            else:
                self._committed += _compose(self._cho, self._jung)
                self._reset_state()
                self._committed += v
        elif self._state == 3:
            if self._jong in CONS_SPLIT:
                j1, j2 = CONS_SPLIT[self._jong]
                self._committed += _compose(self._cho, self._jung, j1)
                self._cho = j2; self._jung = v; self._jong = ""; self._state = 2
            else:
                next_cho = self._jong
                self._committed += _compose(self._cho, self._jung)
                self._cho = next_cho; self._jung = v; self._jong = ""; self._state = 2

    def _push_consonant(self, c: str):
        if self._state == 0:
            self._cho = c; self._state = 1
        elif self._state == 1:
            self._committed += self._cho
            self._cho = c
        elif self._state == 2:
            self._jong = c; self._state = 3
        elif self._state == 3:
            combined = CONS_COMBINE.get((self._jong, c))
            if combined:
                self._jong = combined
            else:
                self._committed += _compose(self._cho, self._jung, self._jong)
                self._reset_state()
                self._cho = c; self._state = 1


# ════════════════════════════════════════════════════════════
#  Keyboard layout
# ════════════════════════════════════════════════════════════
KO_ROWS_NORMAL = [
    ["ㅂ","ㅈ","ㄷ","ㄱ","ㅅ","ㅛ","ㅕ","ㅑ","ㅐ","ㅔ"],
    ["ㅁ","ㄴ","ㅇ","ㄹ","ㅎ","ㅗ","ㅓ","ㅏ","ㅣ"],
    ["SHIFT","ㅋ","ㅌ","ㅊ","ㅍ","ㅠ","ㅜ","ㅡ","⌫"],
    ["!#1"," ","한/영"],
]
KO_ROWS_SHIFT = [
    ["ㅃ","ㅉ","ㄸ","ㄲ","ㅆ","ㅛ","ㅕ","ㅑ","ㅒ","ㅖ"],
    ["ㅁ","ㄴ","ㅇ","ㄹ","ㅎ","ㅗ","ㅓ","ㅏ","ㅣ"],
    ["SHIFT","ㅋ","ㅌ","ㅊ","ㅍ","ㅠ","ㅜ","ㅡ","⌫"],
    ["!#1"," ","한/영"],
]
NUM_ROWS = [
    ["1","2","3","4","5","6","7","8","9","0"],
    ["-","/",":",";","(",")","₩","&","@","\""],
    ["#+=",".",",","?","!","'","~","<",">","⌫"],
    ["한글"," ","한/영"],
]


def _make_back_icon(color: str, w: int, h: int) -> QSvgWidget:
    wgt = QSvgWidget()
    wgt.load(QByteArray(SVG_BACK.format(color=color).encode()))
    wgt.setFixedSize(w, h)
    wgt.setStyleSheet("background: transparent;")
    return wgt


# ════════════════════════════════════════════════════════════
#  VirtualKeyboard
# ════════════════════════════════════════════════════════════
class VirtualKeyboard(QFrame):
    """
    320x240 LCD 용 가상 키보드.
    on_key(key:str) 콜백으로 입력 전달. 특수키도 그대로 문자열로 전달.
    """
    def __init__(self, on_key, parent=None):
        super().__init__(parent)
        self._on_key = on_key
        self._shifted = False
        self._nummode = False
        self._s = 0.5
        self.setStyleSheet(f"background-color:{C_KEY_SP};border:none;")
        self._outer = QVBoxLayout(self)
        self._outer.setSpacing(2)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._row_widgets: list[QWidget] = []
        self._build()

    def _rows(self):
        if self._nummode: return NUM_ROWS
        if self._shifted: return KO_ROWS_SHIFT
        return KO_ROWS_NORMAL

    def _build(self):
        for w in self._row_widgets:
            w.deleteLater()
        self._row_widgets.clear()

        s = self._s
        # 320x240 LCD: 키 한 칸 ~28-32px 높이가 한계
        key_h = max(round(34 * s), 26)
        h_gap = max(round(3 * s), 1)
        h_pad = max(round(4 * s), 2)
        v_pad = max(round(3 * s), 2)
        radius = max(round(6 * s), 3)
        fs = max(round(14 * s), 11)
        fs_sm = max(round(11 * s), 9)

        self._outer.setContentsMargins(h_pad, v_pad, h_pad, v_pad)
        self._outer.setSpacing(max(round(2 * s), 1))

        for row_keys in self._rows():
            row_w = QWidget()
            row_w.setStyleSheet("background:transparent;")
            row_lo = QHBoxLayout(row_w)
            row_lo.setContentsMargins(0, 0, 0, 0)
            row_lo.setSpacing(h_gap)

            for key in row_keys:
                btn = QPushButton()
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedHeight(key_h)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

                if key == " ":
                    btn.setText("space")
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_KEY_BG};color:{C_SUB};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{fs_sm}px;font-family:'Helvetica Neue',Arial;"
                        f"font-weight:300;}}"
                        f"QPushButton:hover{{background:{C_KEY_HOV};}}"
                    )
                    btn.clicked.connect(lambda: self._on_key(" "))

                elif key == "⌫":
                    back_lo = QHBoxLayout(btn)
                    back_lo.setContentsMargins(0, 0, 0, 0)
                    bw = max(round(14 * s), 10); bh = max(round(10 * s), 7)
                    back_icon = _make_back_icon(C_DARK, bw, bh)
                    back_lo.addStretch(); back_lo.addWidget(back_icon); back_lo.addStretch()
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_KEY_SP};border:none;"
                        f"border-radius:{radius}px;}}"
                        f"QPushButton:hover{{background:{C_BORDER};}}"
                    )
                    btn.clicked.connect(lambda: self._on_key("⌫"))

                elif key == "SHIFT":
                    lbl = "⇩" if self._shifted else "⇧"
                    btn.setText(lbl)
                    bg = C_DARK if self._shifted else C_KEY_SP
                    fg = C_BG if self._shifted else C_DARK
                    btn.setStyleSheet(
                        f"QPushButton{{background:{bg};color:{fg};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{max(round(13 * s), 10)}px;}}"
                        f"QPushButton:hover{{background:{C_BORDER};}}"
                    )
                    btn.clicked.connect(self._toggle_shift)

                elif key in ("!#1", "#+=", "한글"):
                    btn.setText(key)
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_KEY_SP};color:{C_DARK};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{fs_sm}px;font-family:'Helvetica Neue',Arial;"
                        f"font-weight:400;}}"
                        f"QPushButton:hover{{background:{C_BORDER};}}"
                    )
                    btn.clicked.connect(self._toggle_num)

                elif key == "한/영":
                    btn.setText("한/영")
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_BROWN};color:{C_BG};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{fs_sm}px;font-family:'Helvetica Neue',Arial;"
                        f"font-weight:400;}}"
                        f"QPushButton:hover{{background:{C_BROWN_H};}}"
                    )
                    btn.clicked.connect(lambda: self._on_key("한/영"))

                else:
                    btn.setText(key)
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_KEY_BG};color:{C_DARK};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{fs}px;"
                        f"font-family:'Apple SD Gothic Neo','Noto Sans KR',"
                        f"'Malgun Gothic',sans-serif;font-weight:400;}}"
                        f"QPushButton:hover{{background:{C_KEY_HOV};}}"
                        f"QPushButton:pressed{{background:{C_BORDER};}}"
                    )
                    k = key
                    btn.clicked.connect(lambda chk=False, c=k: self._on_key(c))

                row_lo.addWidget(btn)
            self._outer.addWidget(row_w)
            self._row_widgets.append(row_w)

    def _toggle_shift(self):
        self._shifted = not self._shifted
        self._build()

    def _toggle_num(self):
        self._nummode = not self._nummode
        self._shifted = False
        self._build()

    def apply_scale(self, s: float):
        self._s = s
        self._build()
