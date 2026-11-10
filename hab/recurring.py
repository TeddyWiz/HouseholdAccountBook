"""반복 거래: 같은 계좌 + 같은 금액 + 같은 상세내역(카드는 가맹점)인 거래 묶음

- 자동 판단: 같은 묶음이 2개월 이상, 대략 한 달에 한 번(평균 1.5회 이하) 나타나면 반복 거래.
  사람 이름 이체(예: 박병택, 김미나)와 자주 사는 같은 금액 물건(커피 등)은 제외.
- 직접 지정: 사용자가 묶음을 '반복 거래로 지정'(on) 하거나 '반복 거래에서 제외'(off) 할 수 있음
  → recurring_overrides 표. 지정은 자동 판단보다 우선합니다(사람 이름 이체도 지정 가능, 1개월만 있어도 지정 가능).
- '전달 동일' 표시: 반복 거래인 묶음이 바로 전달에도 있으면 표시
- 메모 공유: 반복 거래에 메모를 적으면 묶음 전체에 적용되고, 이후 새로 들어오는 같은 거래에도 자동으로 붙음
  (shared_notes 표)
"""
import re


def norm_text(s):
    """공백·기호와 숫자를 뺀 비교용 문자열.
    숫자를 빼는 이유: '한화생10000' / '한화생09000', '현대카드10' / '현대카드09'처럼
    달마다 바뀌는 회차·월 번호가 붙는 반복 거래도 같은 거래로 보기 위해 (금액·계좌는 그대로 비교)"""
    return re.sub(r"[\s\-_·.,()（）\[\]\d]+", "", (s or "")).lower()


BUSINESS_WORDS = ("주식회사", "(주)", "㈜", "은행", "카드", "보험", "증권", "페이", "회사", "공단", "공사",
                  "센터", "협회", "조합", "재단", "병원", "학교", "스토어", "마트", "모임", "회비")


def is_person(t):
    """사람 이름으로 주고받은 이체인지: 상대 예금주명이 상세내역과 같거나(끝에 붙은 경우 포함)
    상세내역이 한글 2~4글자 이름이면서 상대 예금주가 있는 경우. 회사·모임 이름은 제외"""
    if t.get("kind") != "bank":
        return False
    desc = re.sub(r"\s+", "", t.get("description") or "")
    cname = re.sub(r"\s+", "", t.get("counter_name") or "")
    if not desc or any(w in desc or w in cname for w in BUSINESS_WORDS):
        return False
    if cname and (cname == desc or (desc.endswith(cname) and re.fullmatch(r"[가-힣]{2,4}", cname))):
        return True
    return bool(cname) and re.fullmatch(r"[가-힣]{2,4}", desc) is not None


MAX_PER_MONTH = 1.5   # 자동 판단 시 한 달 평균 횟수 상한


def base_key(t):
    """금액·계좌·상세내역으로 만든 묶음 키 (사람 이름 여부와 무관)"""
    if t["kind"] == "card":
        return f"{t['account_id']}|card|{t.get('amount') or 0}|{norm_text(t.get('merchant'))}"
    return (f"{t['account_id']}|bank|{t.get('out_amt') or 0}|{t.get('in_amt') or 0}|"
            f"{norm_text(t.get('description'))}")


def prev_month(month):
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


class Recurring:
    """한 번 불러서 여러 거래를 판단할 때 쓰는 도우미 (DB를 한 번만 읽음)"""

    def __init__(self, db):
        self.overrides = {r["rkey"]: r["mode"] for r in db.q("SELECT rkey, mode FROM recurring_overrides")}
        self.months, self.counts = {}, {}
        for t in db.q("SELECT account_id, kind, out_amt, in_amt, amount, description, merchant, "
                      "substr(date,1,7) m FROM txns"):
            k = base_key(t)
            self.months.setdefault(k, set()).add(t["m"])
            self.counts[k] = self.counts.get(k, 0) + 1

    def judge(self, t):
        """→ (반복 거래 여부, 전달 동일 여부, 지정 상태 'on'|'off'|None, 키)"""
        k = base_key(t)
        mode = self.overrides.get(k)
        months = self.months.get(k, set())
        if mode == "off":
            rec = False
        elif mode == "on":
            rec = True
        else:
            # 자동 판단: 2개월 이상 + 대략 한 달에 한 번 (같은 가게 커피처럼 한 달에 여러 번이면 반복 거래 아님)
            rec = (not is_person(t) and len(months) >= 2
                   and self.counts.get(k, 0) <= len(months) * MAX_PER_MONTH)
        same = rec and prev_month(t["date"][:7]) in months
        return rec, same, mode, k


def rkey(t, overrides=None):
    """메모를 공유할 키: 반복 거래로 볼 수 있으면 키, 아니면 None (새로 들어오는 거래에 공유 메모를 붙일 때 사용)"""
    k = base_key(t)
    mode = (overrides or {}).get(k)
    if mode == "off" or (mode != "on" and is_person(t)):
        return None
    return k
