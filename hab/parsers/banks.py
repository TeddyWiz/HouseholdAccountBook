"""은행 거래내역 파서: IBK기업은행(출력용 html-xls / 거래용 xls), 농협(html 메일), 우리은행(html 메일), 카카오뱅크(xlsx)"""
import re

from ..util import (cell, clean, col_index, digits, find_header_table, html_tables,
                    parse_date, parse_datetime, parse_time, to_int)
from .base import NeedInput, ParseResult, Parser


def _bank_key(number):
    return "bank:" + digits(number)


def _header_balance_at(text, label):
    m = re.search(label + r"\s*:?\s*(\d{4}[-.]\d{2}[-.]\d{2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)", text)
    if not m:
        return None
    d, t = parse_datetime(m.group(1))
    return f"{d} {t or '23:59:59'}"


# ---------------------------------------------------------------- IBK 기업은행
class IbkBankHtml(Parser):
    name = "IBK기업은행 거래내역(출력용)"
    kind = "bank"

    def detect(self, path, fmt, text):
        if fmt == "html" and text and "거래내역조회_입출식" in text and "상대계좌예금주명" in text:
            return 90
        return 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        tables, full = html_tables(text)
        m = re.search(r"계좌번호\s*:\s*([\d-]+)", full)
        if not m:
            raise ValueError("계좌번호를 찾을 수 없습니다")
        number = m.group(1)
        holder = re.search(r"예금주명\s*:\s*(\S+)", full)
        bal = re.search(r"현재잔액\s*:\s*(-?[\d,]+)원", full)
        kind = re.search(r"예금종류\s*:\s*(.+?)\s+(출금가능|조회)", full)
        acct = res.account(_bank_key(number), kind="bank", number=number, institution="기업은행",
                           name=("기업 " + kind.group(1)) if kind else "기업은행",
                           holder=holder.group(1) if holder else "",
                           balance=to_int(bal.group(1)) if bal else None,
                           balance_at=_header_balance_at(full, "조회기준일"))
        header, rows = find_header_table(tables, ["거래일시", "출금", "입금"])
        _ibk_rows(res, acct, header, rows)
        return res


def _ibk_rows(res, acct, header, rows):
    i_dt = col_index(header, "거래일시")
    i_out = col_index(header, "출금")
    i_in = col_index(header, "입금")
    i_bal = col_index(header, "거래후 잔액", "잔액")
    i_desc = col_index(header, "거래내용")
    i_memo = col_index(header, "송금메시지")
    i_cacc = col_index(header, "상대계좌번호")
    i_cbank = col_index(header, "상대은행")
    i_type = col_index(header, "거래구분")
    i_cname = col_index(header, "상대계좌예금주명")
    for r in rows:
        d, t = parse_datetime(cell(r, i_dt))
        if not d:
            continue
        res.add_txn(kind="bank", acct=acct, date=d, time=t,
                    out_amt=to_int(cell(r, i_out)), in_amt=to_int(cell(r, i_in)),
                    balance=to_int(cell(r, i_bal)),
                    description=clean(cell(r, i_desc)), memo=clean(cell(r, i_memo)),
                    counter_account=clean(cell(r, i_cacc)), counter_bank=clean(cell(r, i_cbank)),
                    counter_name=clean(cell(r, i_cname)), txn_type=clean(cell(r, i_type)))


IBK_XLS_HEADER = ["거래일시", "출금", "입금", "거래후 잔액", "거래내용", "송금메시지", "상대계좌번호",
                  "상대은행", "거래구분", "수표어음금액", "CMS코드", "상대계좌예금주명"]


class IbkBankXls(Parser):
    """기업은행 '거래용' 엑셀: 머리글/계좌번호가 없는 12열 엑셀"""
    name = "IBK기업은행 거래내역(거래용 엑셀)"
    kind = "bank"

    def _rows(self, path):
        import xlrd
        sh = xlrd.open_workbook(path).sheet_by_index(0)
        return [[clean(v) for v in sh.row_values(i)] for i in range(sh.nrows)]

    def detect(self, path, fmt, text):
        if fmt != "xls":
            return 0
        try:
            rows = self._rows(path)
        except Exception:
            return 0
        if rows and len(rows[0]) == 12 and re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", rows[0][0]):
            return 80
        if rows and "거래일시" in rows[0] and "상대계좌예금주명" in "|".join(rows[0]):
            return 80
        return 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        rows = self._rows(path)
        header = IBK_XLS_HEADER
        if rows and "거래일시" in rows[0]:
            header, rows = rows[0], rows[1:]
        account = options.get("account")
        if account:
            acct = res.account(_bank_key(account), kind="bank", number=account, institution="기업은행")
        else:
            acct = None  # importer 가 기존 DB 와 대조하여 계좌를 추정
            res.needs_account = True
        _ibk_rows(res, acct, header, rows)
        return res


# ---------------------------------------------------------------- 농협
class NhBankHtml(Parser):
    name = "농협 거래내역(이메일 html)"
    kind = "bank"

    def detect(self, path, fmt, text):
        if fmt == "html" and text and "거래기록사항" in text and "거래후잔액" in text:
            return 90
        return 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        tables, full = html_tables(text)
        number = None
        for rows in tables:
            for r in rows:
                if len(r) >= 2 and r[0] == "계좌번호":
                    number = r[1]
        if not number:
            m = re.search(r"계좌번호\s*([\d-]+)", full)
            number = m.group(1) if m else None
        if not number:
            raise ValueError("계좌번호를 찾을 수 없습니다")
        bal = re.search(r"통장잔액\s*(-?[\d,]+)원", full)
        holder = re.search(r"고객명\s*:\s*(\S+)", full)
        sent = re.search(r"발송일자\s*:\s*(\d{4}\.\d{2}\.\d{2})", full)
        acct = res.account(_bank_key(number), kind="bank", number=number, institution="농협",
                           name="농협 " + number, holder=holder.group(1) if holder else "",
                           balance=to_int(bal.group(1)) if bal else None,
                           balance_at=(parse_date(sent.group(1)) + " 23:59:59") if sent else None)
        header, rows = find_header_table(tables, ["거래일시", "구분", "금액", "거래후잔액"])
        i_dt, i_div = col_index(header, "거래일시"), col_index(header, "구분")
        i_amt, i_bal = col_index(header, "금액"), col_index(header, "거래후잔액")
        i_desc, i_rec = col_index(header, "거래내용"), col_index(header, "거래기록사항")
        i_br, i_memo = col_index(header, "거래점"), col_index(header, "이체메모")
        for r in rows:
            d, t = parse_datetime(cell(r, i_dt))
            if not d:
                continue
            amt = to_int(cell(r, i_amt))
            is_out = "출금" in cell(r, i_div)
            desc = cell(r, i_desc)
            rec = cell(r, i_rec)
            transfer_like = any(k in desc for k in ("송금", "이체", "은행", "타행"))
            res.add_txn(kind="bank", acct=acct, date=d, time=t,
                        out_amt=amt if is_out else 0, in_amt=0 if is_out else amt,
                        balance=to_int(cell(r, i_bal)),
                        description=clean(f"{desc} {rec}"), memo=cell(r, i_memo),
                        counter_account="", counter_bank=cell(r, i_br) if transfer_like else "",
                        counter_name=rec if transfer_like else "", txn_type=desc,
                        branch=cell(r, i_br))
        return res


# ---------------------------------------------------------------- 우리은행
class WooriBankHtml(Parser):
    name = "우리은행 거래내역(이메일 html)"
    kind = "bank"

    def detect(self, path, fmt, text):
        if fmt == "html" and text and "기재내용" in text and ("WOORI" in text or "우리은행" in text):
            return 90
        return 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        tables, full = html_tables(text)
        number = holder = None
        bal = None
        for rows in tables:
            for r in rows:
                if len(r) >= 2 and r[0] == "계좌번호":
                    number = r[1]
                    if len(r) >= 4 and r[2] == "예금주":
                        holder = r[3]
                if len(r) >= 2 and r[0] == "현재잔액":
                    bal = to_int(r[1])
        if not number:
            raise ValueError("계좌번호를 찾을 수 없습니다")
        issued = re.search(r"발급 기준일\s*:?\s*(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})", full)
        bat = None
        if issued:
            d, t = parse_datetime(issued.group(1))
            bat = f"{d} {t}"
        acct = res.account(_bank_key(number), kind="bank", number=number, institution="우리은행",
                           name="우리 " + number, holder=holder or "", balance=bal, balance_at=bat)
        header, rows = find_header_table(tables, ["거래일시", "기재내용", "출금금액", "입금금액"])
        i_dt, i_type = col_index(header, "거래일시"), col_index(header, "거래구분")
        i_desc = col_index(header, "기재내용")
        i_out, i_in = col_index(header, "출금금액"), col_index(header, "입금금액")
        i_bal, i_br = col_index(header, "잔액"), col_index(header, "취급점")
        for r in rows:
            d, t = parse_datetime(cell(r, i_dt))
            if not d:
                continue
            res.add_txn(kind="bank", acct=acct, date=d, time=t,
                        out_amt=to_int(cell(r, i_out)), in_amt=to_int(cell(r, i_in)),
                        balance=to_int(cell(r, i_bal)), description=cell(r, i_desc), memo="",
                        counter_account="", counter_bank="", counter_name="",
                        txn_type=cell(r, i_type), branch=cell(r, i_br))
        return res


# ---------------------------------------------------------------- 카카오뱅크
def _xlsx_rows(path):
    import warnings

    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        out.extend([clean(v) for v in r] for r in ws.iter_rows(values_only=True))
    return [r for r in out if any(r)]


class KakaoBankXlsx(Parser):
    """카카오뱅크 앱/웹 '거래내역' 엑셀. 계좌번호가 가려져 있어(****-**-***4141)
    끝자리로 기존 계좌나 다른 통장의 상대계좌번호와 연결합니다."""
    name = "카카오뱅크 거래내역(xlsx)"
    kind = "bank"

    def detect(self, path, fmt, text):
        if fmt != "xlsx":
            return 0
        try:
            rows = _xlsx_rows(path)[:12]
        except Exception:
            return 0
        joined = "|".join("|".join(r) for r in rows)
        if "카카오뱅크" in joined and "거래 후 잔액" in joined and "거래금액" in joined:
            return 90
        return 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        rows = _xlsx_rows(path)
        info = {}
        for r in rows[:10]:
            cells = [c for c in r if c]
            for i, c in enumerate(cells[:-1]):
                if c in ("성명", "계좌번호", "조회기간", "요청일시"):
                    info[c] = cells[i + 1]
        number = options.get("account") or info.get("계좌번호")
        if not number:
            raise NeedInput("account", "계좌번호를 찾을 수 없습니다. 계좌를 지정하세요.")
        acct = res.account("bank:카카오뱅크:" + digits(number)[-4:], kind="bank", number=number,
                           institution="카카오뱅크", name="카카오뱅크 " + number, holder=info.get("성명", ""))
        header, data = find_header_table([rows], ["거래일시", "구분", "거래금액", "거래 후 잔액"])
        i_dt, i_div = col_index(header, "거래일시"), col_index(header, "구분")
        i_amt, i_bal = col_index(header, "거래금액"), col_index(header, "거래 후 잔액")
        i_type, i_desc, i_memo = col_index(header, "거래구분"), col_index(header, "내용"), col_index(header, "메모")
        for r in data:
            d, t = parse_datetime(cell(r, i_dt))
            if not d:
                continue
            amt = to_int(cell(r, i_amt))
            is_out = amt < 0 or "출금" in cell(r, i_div)
            typ, desc = cell(r, i_type), cell(r, i_desc)
            transfer = "이체" in typ and "자동이체(기타)" not in typ
            res.add_txn(kind="bank", acct=acct, date=d, time=t,
                        out_amt=abs(amt) if is_out else 0, in_amt=0 if is_out else abs(amt),
                        balance=to_int(cell(r, i_bal)), description=desc, memo=cell(r, i_memo),
                        counter_account="", counter_bank="", counter_name=desc if transfer else "",
                        txn_type=typ)
        return res


# ---------------------------------------------------------------- 범용(머리글 기반)
class GenericBankTable(Parser):
    """알려지지 않은 은행 양식이라도 '거래일시/출금/입금/잔액' 머리글이 있으면 읽어 봄 (계좌 지정 필요)"""
    name = "범용 은행 표(머리글 인식)"
    kind = "bank"

    def _table(self, path, fmt, text):
        if fmt == "html":
            tables, _ = html_tables(text)
        elif fmt == "xls":
            import xlrd
            b = xlrd.open_workbook(path)
            tables = [[[clean(v) for v in s.row_values(i)] for i in range(s.nrows)] for s in b.sheets()]
        elif fmt == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            tables = [[[clean(v) for v in r] for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets]
        else:
            return None, []
        for must in (["거래일", "출금", "입금", "잔액"], ["일시", "출금", "입금"]):
            h, rows = find_header_table(tables, must)
            if h:
                return h, rows
        return None, []

    def detect(self, path, fmt, text):
        try:
            h, _ = self._table(path, fmt, text)
        except Exception:
            return 0
        return 10 if h else 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        account = options.get("account")
        if not account:
            raise NeedInput("account", "이 양식에는 계좌번호가 없어 계좌를 지정해야 합니다.")
        acct = res.account(_bank_key(account), kind="bank", number=account, institution="")
        h, rows = self._table(path, fmt, text)
        i_dt = col_index(h, "거래일시", "거래일자", "거래일")
        i_tm = col_index(h, "거래시간", "시간")
        i_out, i_in = col_index(h, "출금금액", "출금"), col_index(h, "입금금액", "입금")
        i_bal = col_index(h, "거래후 잔액", "잔액")
        i_desc = col_index(h, "거래내용", "기재내용", "적요", "내용")
        i_type = col_index(h, "거래구분", "구분")
        i_memo = col_index(h, "송금메시지", "메모", "이체메모")
        i_cacc, i_cname = col_index(h, "상대계좌번호"), col_index(h, "상대계좌예금주명", "예금주")
        for r in rows:
            d, t = parse_datetime(cell(r, i_dt))
            if not d:
                continue
            if i_tm is not None:
                t = parse_time(cell(r, i_tm)) or t
            res.add_txn(kind="bank", acct=acct, date=d, time=t,
                        out_amt=to_int(cell(r, i_out)), in_amt=to_int(cell(r, i_in)),
                        balance=to_int(cell(r, i_bal)), description=cell(r, i_desc),
                        memo=cell(r, i_memo), counter_account=cell(r, i_cacc), counter_bank="",
                        counter_name=cell(r, i_cname), txn_type=cell(r, i_type))
        return res
