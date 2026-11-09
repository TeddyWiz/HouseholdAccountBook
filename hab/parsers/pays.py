"""간편결제 머니 파서: 네이버페이 머니 이용내역 (pay.naver.com '포인트·머니 > 머니' 화면을 저장한 html)

- 네이버페이 머니를 하나의 '계좌'로 다룹니다 → 계좌별 잔액에 남은 머니가 나옵니다.
- 충전: 연결 계좌(예: 카카오뱅크)에서 들어온 돈 → 그 계좌의 '네이버페이충전' 출금과 짝지어 내 계좌 간 이체로 분류
- 결제: 실제 지출(상품명이 상세내역) / 결제 취소: 환불(입금)
- 화면에는 연도와 거래 후 잔액이 없어서
  연도 = 주문번호의 날짜 또는 파일 날짜에서 거꾸로 추정, 잔액 = 현재 머니 잔액에서 거꾸로 계산합니다.
"""
import os
import re
from datetime import date

from ..util import clean, parse_time, to_int
from .base import ParseResult, Parser

ACCOUNT_KEY = "bank:네이버페이머니"


def _file_date(path, text):
    m = re.search(r"(\d{2})(\d{2})(\d{2})(?!\d)", os.path.basename(path))
    if m:
        try:
            return date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return date.today()


class NaverPayMoneyHtml(Parser):
    name = "네이버페이 머니 이용내역(html)"
    kind = "bank"

    def detect(self, path, fmt, text):
        if fmt == "html" and text and "네이버페이" in text and "계좌잔액" in text and "_history" in text:
            return 90
        return 0

    def parse(self, path, fmt, text, options):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "lxml")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        full = clean(soup.get_text(" ", strip=True))
        m = re.search(r"계좌잔액\s*([\d,]+)\s*원", full)
        current = to_int(m.group(1)) if m else 0

        # 1) 화면 순서(최신 → 과거)대로 항목 수집, 날짜가 비어 있으면 위 항목과 같은 날
        fd = _file_date(path, text)
        year, prev_month, cur = fd.year, fd.month, None
        items = []
        for li in soup.select('li[class*="item-date"]'):
            d = li.select_one('[class*="_date"]')
            dtxt = clean(d.get_text(" ", strip=True)).replace("날짜", "").strip() if d else ""
            for h in li.select('div[class*="_history"]'):
                md = re.match(r"(\d{1,2})\.(\d{1,2})", dtxt)
                if md:
                    mo, dy = int(md.group(1)), int(md.group(2))
                    if mo > prev_month:
                        year -= 1
                    prev_month = mo
                    cur = (year, mo, dy)
                    dtxt = ""  # 같은 li 안의 다음 항목은 같은 날
                a = h.select_one("a[href]")
                href = a["href"] if a else ""
                mo_id = re.search(r"(20\d{2})(\d{2})(\d{2})(?:NP|\d)", href)
                if mo_id and cur and int(mo_id.group(2)) == cur[1] and int(mo_id.group(3)) == cur[2]:
                    cur = (int(mo_id.group(1)), cur[1], cur[2])  # 주문번호의 연도로 보정
                    year = cur[0]
                title = h.select_one('[class*="_title"]')
                subs = [clean(x.get_text(" ", strip=True)).replace("시간", "").strip()
                        for x in h.select('[class*="_sub-text"]')]
                price = h.select_one('[class*="_price"]')
                if not cur or not price:
                    continue
                tm = next((parse_time(x) for x in subs if parse_time(x)), "")
                typ = next((x for x in subs if not parse_time(x)), "")
                items.append(dict(date=f"{cur[0]}-{cur[1]:02d}-{cur[2]:02d}", time=tm, type=typ,
                                  title=clean(title.get_text(" ", strip=True)) if title else "",
                                  amount=to_int(clean(price.get_text(" ", strip=True)).replace("금액", "")),
                                  href=href))

        res = ParseResult(self.name)
        latest = f"{items[0]['date']} {items[0]['time']}" if items else None
        acct = res.account(ACCOUNT_KEY, kind="bank", number="네이버페이 머니", institution="네이버페이",
                           name="네이버페이 머니", balance=current, balance_at=latest)
        # 2) 잔액: 최신 항목의 거래 후 잔액 = 현재 잔액, 과거로 갈수록 금액을 거꾸로 빼며 계산
        bal = current
        for it in items:
            amt = it["amount"]
            is_charge = it["type"] == "충전"
            src = re.match(r"(.+?)\(([\d*\-]+)\)", it["title"]) if is_charge else None
            res.add_txn(kind="bank", acct=acct, date=it["date"], time=it["time"],
                        out_amt=-amt if amt < 0 else 0, in_amt=amt if amt > 0 else 0, balance=bal,
                        description=(f"충전 ← {it['title']}" if is_charge else it["title"]), memo="",
                        counter_account=src.group(2) if src else "", counter_bank=src.group(1) if src else "",
                        counter_name="", txn_type=it["type"] or ("입금" if amt > 0 else "결제"),
                        branch="")
            bal -= amt
        return res
