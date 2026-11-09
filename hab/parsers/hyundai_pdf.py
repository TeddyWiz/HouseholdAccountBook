"""현대카드 이용대금명세서 PDF 파서 (현대카드 이메일 명세서를 브라우저에서 PDF로 저장한 것)

표 구성: 이용일 | 이용카드 | 이용가맹점 | 이용금액 | 할부/회차 | 적립/할인율(%) | 예상적립/할인 | 결제원금 | 결제 후 잔액 | 수수료(이자)
- 가맹점 이름이 길면 칸 안에서 여러 줄로 나뉘어(날짜 줄 위·아래) 나오므로 다시 이어 붙입니다.
- 이용일은 'MM/DD' 만 있어 '결제일 : 2026년 11월 05일'의 연도로 채웁니다(결제월보다 뒤 달이면 전년도).
- '총합계' 결제원금과 읽은 결제원금 합계를 비교해 스스로 확인합니다.
- 카드 이름(본인 M BOOST)은 엑셀 명세서와 같은 카드로 연결됩니다.
"""
import re

from ..util import clean, to_int
from .base import ParseResult, Parser
from .cards import bill_month_from_filename, card_key, norm_card_name
from .ibk_pdf import open_pdf

COMPANY = "현대카드"
DATE_LINE = re.compile(r"^(\d{2})/(\d{2})\s+(.*)$")
NUM = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
INST = re.compile(r"^(\d+)/(\d+)$")


def parse_item_line(rest):
    """'본인 M BOOST 더서울치과 3,900 0/0 0.7 22 3,900 0 0'
       → (카드, 가맹점 일부, [이용금액, 할부/회차, 적립율, 예상적립, 결제원금, 결제후잔액, 수수료])"""
    toks = rest.split()
    tail = []
    while toks and (NUM.match(toks[-1]) or INST.match(toks[-1])) and len(tail) < 7:
        tail.insert(0, toks.pop())
    if len(tail) < 7:
        return None
    # 이용카드: '본인'/'가족' + 카드 이름 두 단어 (예: M BOOST, M 하이패스)
    card = []
    if toks and re.match(r"^(본인|가족)", toks[0]):
        card = toks[:3]
        toks = toks[3:]
    return " ".join(card), " ".join(toks), tail


class HyundaiCardPdf(Parser):
    name = "현대카드 이용대금명세서(PDF)"
    kind = "card"

    def detect(self, path, fmt, text):
        if fmt != "pdf":
            return 0
        try:
            with open_pdf(path, "") as pdf:
                title = (pdf.metadata or {}).get("Title", "") or ""
                first = pdf.pages[0].extract_text() or ""
        except Exception:
            return 0
        if "현대카드" in title or "현대카드" in first or ("1577-6000" in first and "이용대금명세서" in first):
            return 85
        if "이용가맹점" in first and "할부" in first and "결제" in first and "본인" in first:
            return 60
        return 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        cells, full = [], []
        with open_pdf(path, options.get("password")) as pdf:
            for page in pdf.pages:
                full.append(page.extract_text() or "")
                for tb in page.extract_tables() or []:
                    for row in tb:
                        cells.append("\n".join(c for c in row if c))
        full = "\n".join(full)
        m = re.search(r"결제일\s*:?\s*(20\d{2})\s*년\s*(\d{1,2})\s*월", full)
        bill_month = f"{m.group(1)}-{int(m.group(2)):02d}" if m else bill_month_from_filename(path)
        if not bill_month:
            raise ValueError("결제일(청구월)을 찾을 수 없습니다")
        by, bm = int(bill_month[:4]), int(bill_month[5:])

        items, total = [], None
        for cell in cells:
            lines = [clean(x) for x in cell.split("\n") if clean(x)]
            joined = "".join(lines).replace(" ", "")
            if joined.startswith("총합계"):
                nums = [to_int(x) for x in re.findall(r"-?[\d,]{2,}", " ".join(lines[0].split()[1:]))]
                if nums:
                    total = nums[0]
                continue
            di = next((i for i, ln in enumerate(lines) if DATE_LINE.match(ln)), None)
            if di is None:
                continue
            md = DATE_LINE.match(lines[di])
            parsed = parse_item_line(md.group(3))
            if not parsed:
                continue
            card, mid, tail = parsed
            merchant = "".join(lines[:di]) + mid + "".join(lines[di + 1:])
            amount = to_int(tail[0])
            months, rnd = (int(x) for x in INST.match(tail[1]).groups()) if INST.match(tail[1]) else (0, 0)
            principal, remain, fee = to_int(tail[4]), to_int(tail[5]), to_int(tail[6])
            mo, dy = int(md.group(1)), int(md.group(2))
            year = by - 1 if mo > bm else by
            cname = norm_card_name(card) or "현대카드"
            acct = res.account(card_key(COMPANY, name=cname), kind="card", institution=COMPANY,
                               name=f"현대카드 {cname}", number=cname)
            idx = res.add_txn(kind="card", acct=acct, date=f"{year}-{mo:02d}-{dy:02d}", time="", amount=amount,
                              merchant=merchant, inst_months=months if months > 1 else 0, inst_round=rnd,
                              inst_remain_amt=remain if months > 1 else 0,
                              txn_type="할부" if months > 1 else ("연회비" if "연회비" in merchant else "일시불"),
                              status="", detail="", pay_month=bill_month)
            items.append((idx, principal + fee, rnd))
        if not items:
            raise ValueError("PDF에서 '결제상세내역' 표를 찾지 못했습니다.")
        got = sum(b for _, b, _ in items)
        if total is not None and got != total:
            res.warnings.append(f"읽은 결제원금 합계 {got:,}원이 명세서 총합계 {total:,}원과 다릅니다.")
        elif total is not None:
            res.warnings.append(f"명세서 총합계 {total:,}원과 일치")
        res.statements.append(dict(company=COMPANY, bill_month=bill_month, total=total if total is not None else got,
                                   exact=total is not None and got == total, items=items))
        return res
