"""IBK기업은행 BC카드 이용대금명세서(PDF) 파서

명세서 구성 (IBK기업은행_YYYYMMDD(문서열기암호_생년월일6자리).pdf):
  1쪽  '2026.01' 청구월, '대금결제일 2026년 01월 05일', 입금하실 금액
  2~3쪽 '카드이용내역' 표
        이용일자 | 가맹점명 | 소재지 | 이용금액(해외현지금액) | 할부개월 | 회차 | 원금(KRW) | 수수료 | 특별서비스 | 할인금액 | 결제 후 잔액
        카드 구분 줄: '(본인) 박병택 고객님 (신용)IBK K-패스(신용)(B170)'  → 카드번호 끝자리 170
        마지막: '소계' / '합계' 줄 (원금 합계 = 입금하실 금액)

- 일시불 행은 이용금액 칸이 비어 있고 원금이 곧 이용금액입니다.
- 할부 행은 이용금액·할부개월·회차가 함께 나옵니다.
- 원금 합계를 '합계' 줄과 비교해 읽기가 맞았는지 스스로 확인합니다(다르면 경고).
- 암호: 데이터 입력 화면의 'PDF 암호' 칸, 또는  import_all.bat <암호>
"""
import os
import re

from ..util import clean, to_int
from .base import NeedInput, ParseResult, Parser
from .cards import bill_month_from_filename, card_key

COMPANY = "IBK기업카드"
ITEM_RE = re.compile(r"^(\d{2})/(\d{2})\s+(.*)$")
NUM_TOKEN = re.compile(r"^-?\d{1,3}(?:,\d{3})*$|^-?\d+$")
CARD_LINE_RE = re.compile(r"\((본인|가족)\).*?\(B?(\d{3,4})\)")
# IBK 명세서의 한글은 'SBGC1', 'SBGB3' 같은 글꼴 수십 개로 나뉘어 있습니다.
# 글꼴 이름 끝 두 글자 = 한글 완성형(EUC-KR) 코드의 첫 바이트, 글자 자체 = 둘째 바이트(MacRoman으로 읽힘).
# 예) 글꼴 SBGC4 + 'Ì'(0xED) → 0xC4ED = '쿠'
SPLIT_FONT_RE = re.compile(r"SBG([0-9A-F]{2})")


def fix_char(c):
    m = SPLIT_FONT_RE.search(c.get("fontname", ""))
    if not m:
        return c
    try:
        raw = c["text"].encode("mac_roman")
        if len(raw) != 1:
            return c
        return dict(c, text=bytes([int(m.group(1), 16), raw[0]]).decode("cp949"))
    except (UnicodeError, ValueError):
        return c


def page_text(page):
    """한글이 나뉜 글꼴을 되살린 뒤 줄 단위 텍스트로"""
    from pdfplumber.utils import extract_text
    return extract_text([fix_char(c) for c in page.chars])


def open_pdf(path, password):
    import pdfplumber
    try:
        return pdfplumber.open(path, password=password or "")
    except Exception as e:
        name = type(e).__name__
        if "password" in str(e).lower() or name in ("PdfminerException", "PDFPasswordIncorrect"):
            if password:
                raise NeedInput("password", "PDF 암호가 맞지 않습니다. 생년월일 6자리 등 암호를 확인하세요.")
            raise NeedInput("password", "PDF 암호가 필요합니다 (예: 생년월일 6자리).")
        raise


def split_numbers(rest):
    """'가맹점 이름 ... 1,234 0 0 0' → ('가맹점 이름 ...', [1234, 0, 0, 0]) : 뒤쪽 숫자 칸들만 분리"""
    toks = rest.split()
    nums = []
    while toks and NUM_TOKEN.match(toks[-1]):
        nums.insert(0, to_int(toks.pop()))
    return " ".join(toks), nums


class IbkBcPdfStatement(Parser):
    name = "IBK기업은행 BC카드 명세서(PDF)"
    kind = "card"
    version = 2   # v2: 가맹점 한글 깨짐 수정 → 예전에 넣은 파일도 다시 읽어 가맹점명 교체

    def detect(self, path, fmt, text):
        if fmt != "pdf":
            return 0
        n = os.path.basename(path)
        return 70 if ("IBK" in n.upper() or "기업" in n or "BC" in n.upper()) else 0

    def parse(self, path, fmt, text, options):
        res = ParseResult(self.name)
        lines = []
        with open_pdf(path, options.get("password")) as pdf:
            for page in pdf.pages:
                lines += [clean(x) for x in (page_text(page) or "").splitlines()]
        full = "\n".join(lines)
        if not full.strip():
            raise ValueError("PDF에 글자가 없습니다(인쇄용 이미지 PDF). 카드사에서 받은 원본 PDF를 넣어 주세요.")

        # 청구월: '대금결제일 2026년 01월 05일' → '2026.01' → 파일명 순서로
        m = re.search(r"대금결제일\s*(20\d{2})\s*년\s*(\d{1,2})\s*월", full) or \
            re.search(r"^\s*(20\d{2})\.(\d{2})\s*$", full, re.M)
        bill_month = f"{m.group(1)}-{int(m.group(2)):02d}" if m else bill_month_from_filename(path)
        if not bill_month:
            raise ValueError("청구월을 찾을 수 없습니다")
        by, bm = int(bill_month[:4]), int(bill_month[5:])

        in_table, acct, items, total = False, None, [], None
        for ln in lines:
            if "카드이용내역" in ln.replace(" ", ""):
                in_table = True
                continue
            if not in_table:
                continue
            mc = CARD_LINE_RE.search(ln)
            if mc:
                acct = res.account(card_key(COMPANY, mc.group(2)), kind="card", institution=COMPANY,
                                   name="IBK기업카드", number=f"****{mc.group(2)}", holder="")
                continue
            compact = ln.replace(" ", "")
            if compact.startswith("합계") or compact.startswith("소계"):
                _, nums = split_numbers(ln)
                if compact.startswith("합계") and nums:
                    total = nums[0]
                continue
            mi = ITEM_RE.match(ln)
            if not mi:
                continue
            mo, dy = int(mi.group(1)), int(mi.group(2))
            merchant, nums = split_numbers(mi.group(3))
            if len(nums) < 4 or not merchant:
                continue
            # 숫자 칸: 일시불 = 원금, 수수료, (특별서비스), 할인, 결제후잔액
            #          할부   = 이용금액, 할부개월, 회차, 원금, 수수료, (특별서비스), 할인, 결제후잔액
            if len(nums) >= 7:
                amount, months, rnd, principal, fee = nums[0], nums[1], nums[2], nums[3], nums[4]
            elif len(nums) in (5, 6) and nums[0] == nums[1]:   # 이용금액 칸까지 찍힌 일시불
                amount, months, rnd, principal, fee = nums[1], 0, 0, nums[1], nums[2]
            else:
                principal, fee = nums[0], nums[1]
                amount, months, rnd = principal, 0, 0
            discount = nums[-2]
            year = by - 1 if mo > bm else by
            if acct is None:
                acct = res.account(card_key(COMPANY, name="PDF"), kind="card", institution=COMPANY,
                                   name="IBK기업카드", number="")
            idx = res.add_txn(kind="card", acct=acct, date=f"{year}-{mo:02d}-{dy:02d}", time="",
                              amount=amount, merchant=merchant,
                              inst_months=months if months > 1 else 0, inst_round=rnd,
                              inst_remain_amt=nums[-1] if months > 1 else 0,
                              txn_type="할부" if months > 1 else ("할인" if amount < 0 else "일시불"),
                              status="", detail=(f"할인 {discount:,}원" if discount and amount >= 0 else ""),
                              pay_month=bill_month)
            items.append((idx, principal + fee, rnd))

        if not items:
            raise ValueError("PDF에서 '카드이용내역' 표를 찾지 못했습니다.")
        parsed = sum(b for _, b, _ in items)
        if total is not None and parsed != total:
            res.warnings.append(f"읽은 금액 합계 {parsed:,}원이 명세서 합계 {total:,}원과 다릅니다. 일부 행을 놓쳤을 수 있습니다.")
        elif total is not None:
            res.warnings.append(f"명세서 합계 {total:,}원과 일치")
        res.statements.append(dict(company=COMPANY, bill_month=bill_month, total=total if total is not None else parsed,
                                   exact=total is not None and parsed == total, items=items))
        return res
