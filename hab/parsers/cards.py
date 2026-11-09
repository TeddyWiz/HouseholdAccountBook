"""신용카드 파서: 현대카드 명세서(xls-html), 삼성카드 명세서/이용내역(xlsx), IBK기업카드 명세서(xls/html-xls)"""
import os
import re
from collections import OrderedDict

from ..util import cell, clean, col_index, digits, find_header_table, html_tables, parse_date, parse_time, to_int
from .base import ParseResult, Parser


def card_key(company, number="", name=""):
    d = digits(number)
    if d:
        return f"card:{company}:{d[-3:]}"
    return f"card:{company}:{clean(name)}"


def norm_card_name(s):
    s = clean(s)
    s = re.sub(r"^(본\s*인|가\s*족)[A-Z]?\s*", "", s)
    return s.strip()


def installment_estimate(amount, months, rnd=1):
    """할부 회차별 청구 원금 추정: 월 원금은 100원 단위 내림, 남는 금액은 1회차에 더함
    (예: 1,333,700원 5개월 → 1회차 266,900원, 2~5회차 266,700원 / 실제 카드사 명세서와 일치)"""
    if not months or months <= 1:
        return amount
    base = (amount // months) // 100 * 100
    return amount - base * (months - 1) if rnd == 1 else base


def bill_month_from_filename(path):
    name = os.path.basename(path)
    m = re.search(r"(20\d{2})(\d{2})(\d{2})?", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


# ---------------------------------------------------------------- 현대카드
class HyundaiCardStatement(Parser):
    name = "현대카드 이용대금명세서(xls)"
    kind = "card"
    company = "현대카드"

    def detect(self, path, fmt, text):
        if fmt == "html" and text and "이용대금명세서" in text and "이용가맹점" in text and "할부/회차" in text:
            return 90
        return 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        tables, full = html_tables(text)
        m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*이용대금명세서", full)
        bill_month = f"{m.group(1)}-{int(m.group(2)):02d}" if m else bill_month_from_filename(path)
        header, rows = find_header_table(tables, ["이용일", "이용카드", "이용가맹점", "이용금액"])
        i_d, i_card = col_index(header, "이용일"), col_index(header, "이용카드")
        i_m, i_amt = col_index(header, "이용가맹점"), col_index(header, "이용금액")
        i_inst = col_index(header, "할부/회차")
        i_bill, i_rest = col_index(header, "결제원금"), col_index(header, "결제후잔액")
        i_fee = col_index(header, "수수료(이자)", "수수료")
        items, total = [], None
        for r in rows:
            merchant = cell(r, i_m)
            d = parse_date(cell(r, i_d))
            if not d:
                if "총" in merchant and "합계" in merchant.replace(" ", ""):
                    total = to_int(cell(r, i_bill))
                continue
            cname = norm_card_name(cell(r, i_card))
            acct = res.account(card_key(self.company, name=cname), kind="card", institution=self.company,
                               name=f"현대카드 {cname}", number=cname)
            months = rnd = 0
            mi = re.match(r"(\d+)\s*/\s*(\d+)", cell(r, i_inst))
            if mi:
                months, rnd = int(mi.group(1)), int(mi.group(2))
            amt = to_int(cell(r, i_amt))
            fee = to_int(cell(r, i_fee))
            if amt == 0 and fee:
                amt = fee
            billed = to_int(cell(r, i_bill))
            idx = res.add_txn(kind="card", acct=acct, date=d, time="", amount=amt, merchant=merchant,
                              inst_months=months, inst_round=rnd,
                              inst_remain_amt=to_int(cell(r, i_rest)) if months else 0,
                              txn_type="할부" if months > 1 else ("연회비" if "연회비" in merchant else "일시불"),
                              status="", detail="", pay_month=bill_month)
            items.append((idx, billed, rnd))
        if total is None:
            total = sum(b for _, b, _ in items)
        res.statements.append(dict(company=self.company, bill_month=bill_month, total=total,
                                   exact=True, items=items))
        return res


# ---------------------------------------------------------------- 삼성카드
def _xlsx_sheets(path):
    import openpyxl
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [[clean(v) for v in r] for r in ws.iter_rows(values_only=True)]
        out.append((ws.title, [r for r in rows if any(r)]))
    return out


class SamsungCardStatement(Parser):
    name = "삼성카드 이용대금명세서(xlsx)"
    kind = "card"
    company = "삼성카드"

    def detect(self, path, fmt, text):
        if fmt != "xlsx":
            return 0
        try:
            for _, rows in _xlsx_sheets(path):
                h, _ = find_header_table([rows], ["이용일", "이용구분", "가맹점", "원금"])
                if h:
                    return 90
        except Exception:
            return 0
        return 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        bill_month = bill_month_from_filename(path)
        items, total = [], 0
        for title, rows in _xlsx_sheets(path):
            h, data = find_header_table([rows], ["이용일", "이용구분", "가맹점", "원금"])
            if not h:
                continue
            i_d, i_card, i_m = col_index(h, "이용일"), col_index(h, "이용구분"), col_index(h, "가맹점")
            i_amt, i_mon, i_rnd = col_index(h, "이용금액"), col_index(h, "개월"), col_index(h, "회차")
            i_prin, i_fee = col_index(h, "원금"), col_index(h, "이자/수수료")
            i_ben, i_bena = col_index(h, "이용혜택"), col_index(h, "혜택금액")
            i_rest = col_index(h, "입금후잔액")
            for r in data:
                if "합계" in "".join(r):
                    total += to_int(cell(r, i_prin))
                    continue
                d = parse_date(cell(r, i_d))
                if not d:
                    continue
                cardno = digits(cell(r, i_card))
                acct = res.account(card_key(self.company, cardno), kind="card", institution=self.company,
                                   name="삼성카드", number=("****" + cardno) if cardno else "")
                months, rnd = to_int(cell(r, i_mon)), to_int(cell(r, i_rnd))
                ben = clean(cell(r, i_ben))
                detail = f"{ben} {cell(r, i_bena)}".strip() if ben else ""
                idx = res.add_txn(kind="card", acct=acct, date=d, time="", amount=to_int(cell(r, i_amt)),
                                  merchant=cell(r, i_m), inst_months=months, inst_round=rnd,
                                  inst_remain_amt=to_int(cell(r, i_rest)) if months else 0,
                                  txn_type="할부" if months > 1 else "일시불", status="", detail=detail,
                                  pay_month=bill_month)
                items.append((idx, to_int(cell(r, i_prin)) + to_int(cell(r, i_fee)), rnd))
        if not total:
            total = sum(b for _, b, _ in items)
        if bill_month:
            res.statements.append(dict(company=self.company, bill_month=bill_month, total=total,
                                       exact=True, items=items))
        return res


class SamsungCardApprovals(Parser):
    name = "삼성카드 카드이용내역조회(xlsx)"
    kind = "card"
    company = "삼성카드"
    version = 2   # v2: 할부 1회차 청구액을 추정해 채움

    def detect(self, path, fmt, text):
        if fmt != "xlsx":
            return 0
        try:
            for _, rows in _xlsx_sheets(path):
                h, _ = find_header_table([rows], ["카드번호", "승인일자", "가맹점명", "승인금액"])
                if h:
                    return 90
        except Exception:
            return 0
        return 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        by_pay = OrderedDict()
        for title, rows in _xlsx_sheets(path):
            h, data = find_header_table([rows], ["카드번호", "승인일자", "가맹점명", "승인금액"])
            if not h:
                continue
            i_no, i_who = col_index(h, "카드번호"), col_index(h, "본인가족구분")
            i_d, i_t = col_index(h, "승인일자"), col_index(h, "승인시각")
            i_m, i_amt = col_index(h, "가맹점명"), col_index(h, "승인금액(원)", "승인금액")
            i_kind, i_mon = col_index(h, "일시불할부구분"), col_index(h, "할부개월")
            i_appr, i_cancel = col_index(h, "승인번호"), col_index(h, "취소여부")
            i_pay = col_index(h, "결제일")
            for r in data:
                d = parse_date(cell(r, i_d))
                if not d:
                    continue
                no = cell(r, i_no)
                acct = res.account(card_key(self.company, no), kind="card", institution=self.company,
                                   name="삼성카드", number=no)
                cancel = cell(r, i_cancel)
                status = "" if cancel in ("", "-") else "취소"
                pay = parse_date(cell(r, i_pay))
                months = to_int(cell(r, i_mon))
                amt = to_int(cell(r, i_amt))
                idx = res.add_txn(kind="card", acct=acct, date=d, time=parse_time(cell(r, i_t)), amount=amt,
                                  merchant=cell(r, i_m), inst_months=months, inst_round=0, inst_remain_amt=0,
                                  txn_type=cell(r, i_kind) or ("할부" if months > 1 else "일시불"),
                                  status=status, detail=cell(r, i_who), approval_no=cell(r, i_appr),
                                  pay_month=pay[:7] if pay else "")
                if pay and not status:
                    billed = amt if months <= 1 else installment_estimate(amt, months, 1)
                    by_pay.setdefault(pay[:7], []).append((idx, billed, 1 if months > 1 else 0))
        for month, items in by_pay.items():
            res.statements.append(dict(company=self.company, bill_month=month,
                                       total=sum(b or 0 for _, b, _ in items), exact=False, items=items))
        return res


# ---------------------------------------------------------------- IBK 기업카드(BC)
IBK_CARD_HEADER = ["청구월", "카드번호", "거래일", "매입일", "상품구분", "가맹점명", "할부기간/도시명",
                   "납입회차/해외현지금액", "잔여회차/매출외화금액", "할부금액/적용환율", "이용금액", "청구금액", "수수료"]


class IbkCardStatement(Parser):
    name = "IBK기업카드 월별명세서(엑셀)"
    kind = "card"
    company = "IBK기업카드"

    def _rows(self, path, fmt, text):
        if fmt == "html":
            tables, full = html_tables(text)
            h, rows = find_header_table(tables, ["청구월", "카드번호", "거래일", "가맹점명"])
            return h, rows
        import xlrd
        sh = xlrd.open_workbook(path).sheet_by_index(0)
        rows = [[clean(v) for v in sh.row_values(i)] for i in range(sh.nrows)]
        if rows and "청구월" in rows[0]:
            return rows[0], rows[1:]
        if rows and len(rows[0]) == 13 and re.match(r"^\d{4}-\d{2}$", rows[0][0]):
            return IBK_CARD_HEADER, rows
        return None, []

    def detect(self, path, fmt, text):
        if fmt not in ("html", "xls"):
            return 0
        if fmt == "html" and (not text or "월별명세서" not in text):
            return 0
        try:
            h, _ = self._rows(path, fmt, text)
        except Exception:
            return 0
        return 90 if h else 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        h, rows = self._rows(path, fmt, text)
        i_bm, i_no = col_index(h, "청구월"), col_index(h, "카드번호")
        i_d, i_kind, i_m = col_index(h, "거래일"), col_index(h, "상품구분"), col_index(h, "가맹점명")
        i_mon, i_rnd = col_index(h, "할부기간"), col_index(h, "납입회차")
        i_rem = col_index(h, "잔여회차")
        i_amt, i_bill, i_fee = col_index(h, "이용금액"), col_index(h, "청구금액"), col_index(h, "수수료")
        by_month = OrderedDict()
        for r in rows:
            bm = cell(r, i_bm)
            d = parse_date(cell(r, i_d))
            if not d or not re.match(r"^\d{4}-\d{2}$", bm):
                continue
            no = cell(r, i_no)
            acct = res.account(card_key(self.company, no), kind="card", institution=self.company,
                               name="IBK기업카드", number=no)
            kind = cell(r, i_kind)
            months = rnd = 0
            if "할부" in kind:
                months, rnd = to_int(cell(r, i_mon)), to_int(cell(r, i_rnd))
            billed = to_int(cell(r, i_bill)) + to_int(cell(r, i_fee))
            idx = res.add_txn(kind="card", acct=acct, date=d, time="", amount=to_int(cell(r, i_amt)),
                              merchant=cell(r, i_m), inst_months=months, inst_round=rnd,
                              inst_remain_amt=0, txn_type=kind, status="",
                              detail=(f"잔여 {to_int(cell(r, i_rem))}회" if months else ""), pay_month=bm)
            by_month.setdefault(bm, []).append((idx, billed, rnd))
        for month, items in by_month.items():
            res.statements.append(dict(company=self.company, bill_month=month,
                                       total=sum(b for _, b, _ in items), exact=True, items=items))
        return res
