"""공통 유틸: 금액/날짜 파싱, HTML 표 추출."""
import re
import unicodedata
from datetime import datetime


def to_int(v):
    """'1,234원', '-5,000', 1234.0, '' -> int"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(round(v))
    s = str(v).strip().replace(",", "").replace("원", "").replace(" ", "")
    s = s.replace(" ", "")
    if s in ("", "-", "."):
        return 0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = int(round(float(s)))
    except ValueError:
        m = re.search(r"-?\d+(\.\d+)?", s)
        n = int(round(float(m.group()))) if m else 0
    return -n if neg else n


def digits(s):
    return re.sub(r"\D", "", str(s or ""))


def clean(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))  # 전각 문자(ＰＨＡＮ) → 반각, nbsp → 공백
    return re.sub(r"\s+", " ", s).strip()


_DATE_PATTERNS = [
    (re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})"), None),
    (re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"), None),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), None),
    (re.compile(r"^(\d{2})[-./](\d{1,2})[-./](\d{1,2})$"), "yy"),
]
_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def parse_date(s):
    """여러 형식의 날짜 -> 'YYYY-MM-DD' (실패 시 None)"""
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.strftime("%Y-%m-%d")
    s = clean(s)
    for pat, kind in _DATE_PATTERNS:
        m = pat.search(s)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            if kind == "yy":
                y += 2000
            try:
                return datetime(y, mo, d).strftime("%Y-%m-%d")
            except ValueError:
                return None
    return None


def parse_time(s):
    if s is None:
        return ""
    m = _TIME.search(str(s))
    if not m:
        return ""
    h, mi, se = m.group(1), m.group(2), m.group(3) or "00"
    return f"{int(h):02d}:{mi}:{se}"


def parse_datetime(s):
    """'2026-10-20 10:50:36' / '2026.11.02 07:07:13' -> (date, time)"""
    d = parse_date(s)
    rest = str(s)
    m = re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}(.*)$", rest)
    t = parse_time(m.group(1)) if m else parse_time(rest if d is None else "")
    return d, t


def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def html_tables(html):
    """HTML 안의 '가장 안쪽' 표들을 [[셀텍스트...], ...] 목록으로 반환"""
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    out = []
    for t in soup.find_all("table"):
        if t.find("table"):
            continue
        rows = []
        for tr in t.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        if rows:
            out.append(rows)
    return out, clean(soup.get_text(" ", strip=True))


def find_header_table(tables, must_have):
    """must_have 단어를 모두 포함하는 헤더 행을 가진 표 -> (헤더, 데이터행들)"""
    for rows in tables:
        for i, r in enumerate(rows):
            joined = "|".join(r)
            if all(k in joined for k in must_have):
                return r, rows[i + 1:]
    return None, []


def col_index(header, *names):
    """헤더에서 이름 후보 중 첫 번째로 일치하는 열 번호"""
    for n in names:
        for i, h in enumerate(header):
            if h.replace(" ", "") == n.replace(" ", ""):
                return i
    for n in names:
        for i, h in enumerate(header):
            if n.replace(" ", "") in h.replace(" ", ""):
                return i
    return None


def cell(row, idx):
    if idx is None or idx >= len(row):
        return ""
    return row[idx]
