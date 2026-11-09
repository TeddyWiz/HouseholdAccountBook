"""PDF 카드 명세서 파서 (IBK기업은행 카드 명세서 등).

PDF 명세서는 카드사마다 배치가 달라, 표 추출 → 줄 단위 정규식 순서로 시도하는 범용 방식입니다.
'이용일 가맹점 이용금액 ... 청구금액' 순서의 행을 찾아 읽습니다.
"""
import os
import re

from ..util import clean, digits, parse_date, to_int
from .base import NeedInput, ParseResult, Parser
from .cards import bill_month_from_filename, card_key

DATE_RE = re.compile(r"^(\d{4}[./-]\d{1,2}[./-]\d{1,2}|\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d{2}/\d{2})\b")
NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+|-?\d+")
CARDNO_RE = re.compile(r"\d{4}[- ]?[\d*]{4}[- ]?[\d*]{4}[- ]?\d{3,4}")
SKIP_WORDS = ("합계", "소계", "합 계", "결제금액", "청구금액 합")

COMPANY_HINTS = [("IBK기업카드", ["IBK", "기업은행", "BC"]), ("현대카드", ["현대카드"]),
                 ("삼성카드", ["삼성카드"]), ("KB국민카드", ["국민카드", "KB"]), ("신한카드", ["신한카드"])]


class PdfCardStatement(Parser):
    name = "카드 명세서(PDF)"
    kind = "card"

    def detect(self, path, fmt, text):
        return 50 if fmt == "pdf" else 0

    def _open(self, path, password):
        from .ibk_pdf import open_pdf
        return open_pdf(path, password)

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        pdf = self._open(path, options.get("password"))
        lines = []
        with pdf:
            for page in pdf.pages:
                for tb in page.extract_tables() or []:
                    for row in tb:
                        lines.append(" ".join(clean(c) for c in row if c))
                for ln in (page.extract_text() or "").splitlines():
                    lines.append(clean(ln))
        full = "\n".join(lines)
        fname = os.path.basename(path)
        company = "IBK기업카드"
        for comp, hints in COMPANY_HINTS:
            if any(h in fname or h in full[:3000] for h in hints):
                company = comp
                break
        m = CARDNO_RE.search(full)
        number = m.group().replace(" ", "-") if m else ""
        acct = res.account(card_key(company, number, "PDF"), kind="card", institution=company,
                           name=company, number=number)
        bill_month = bill_month_from_filename(path)
        year = int(bill_month[:4]) if bill_month else None
        seen, items = set(), []
        for ln in lines:
            md = DATE_RE.match(ln)
            if not md or any(w in ln for w in SKIP_WORDS):
                continue
            ds = md.group(1)
            if re.match(r"^\d{2}/\d{2}$", ds) and year:
                mo = int(ds[:2])
                y = year - 1 if bill_month and mo > int(bill_month[5:7]) else year
                d = parse_date(f"{y}-{ds[:2]}-{ds[3:]}")
            else:
                d = parse_date(ds)
            if not d:
                continue
            rest = ln[md.end():].strip()
            nums = list(NUM_RE.finditer(rest))
            money = [n for n in nums if "," in n.group() or len(n.group().lstrip("-")) >= 3]
            if not money:
                continue
            merchant = clean(rest[:money[0].start()])
            merchant = re.sub(r"^\d{2}[./-]\d{2}\s*", "", merchant)  # 매입일 제거
            if not merchant:
                continue
            amount = to_int(money[0].group())
            billed = to_int(money[-1].group()) if len(money) > 1 else amount
            inst = re.search(r"(\d{1,2})\s*개월|(\d{1,2})\s*/\s*(\d{1,2})", rest)
            months = rnd = 0
            if inst:
                if inst.group(1):
                    months = int(inst.group(1))
                else:
                    rnd, months = int(inst.group(2)), int(inst.group(3))
            key = (d, merchant, amount, billed)
            if key in seen:          # 표/텍스트 두 경로에서 같은 행이 나오면 1건만
                continue
            seen.add(key)
            idx = res.add_txn(kind="card", acct=acct, date=d, time="", amount=amount, merchant=merchant,
                              inst_months=months if months > 1 else 0, inst_round=rnd, inst_remain_amt=0,
                              txn_type="할부" if months > 1 else "일시불", status="", detail="PDF",
                              pay_month=bill_month or "")
            items.append((idx, billed, rnd))
        if not items:
            res.warnings.append("PDF에서 이용내역 행을 찾지 못했습니다. 엑셀 명세서를 사용하거나 파서를 조정해야 합니다.")
        if bill_month and items:
            res.statements.append(dict(company=company, bill_month=bill_month,
                                       total=sum(b for _, b, _ in items), exact=False, items=items))
        return res
