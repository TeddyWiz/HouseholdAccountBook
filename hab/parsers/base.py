"""파서 공통 구조.

새 양식을 추가하려면:
  1) hab/parsers/ 아래에 Parser 를 상속한 클래스를 만들고
  2) detect() 에서 그 양식이면 점수(>0)를, parse() 에서 ParseResult 를 돌려준 뒤
  3) hab/parsers/__init__.py 의 PARSERS 목록에 추가하면 됩니다.
"""
import os


class NeedInput(Exception):
    """파일만으로 알 수 없는 정보(계좌번호, PDF 암호 등)가 필요할 때"""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field  # 'account' | 'password'
        self.message = message


class ParseResult:
    def __init__(self, parser_name):
        self.parser = parser_name
        self.accounts = {}   # key -> dict
        self.txns = []       # list of dict
        self.statements = [] # list of dict
        self.warnings = []

    def account(self, key, **info):
        acc = self.accounts.setdefault(key, {"key": key})
        for k, v in info.items():
            if v not in (None, ""):
                acc[k] = v
        return key

    def add_txn(self, **t):
        self.txns.append(t)
        return len(self.txns) - 1


def sniff(path):
    """파일 실제 형식: 'html' | 'xls' | 'xlsx' | 'pdf' | 'text'"""
    with open(path, "rb") as f:
        head = f.read(2048)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    if head.startswith(b"PK"):
        return "xlsx"
    low = head.lower()
    if b"<html" in low or b"<table" in low or b"<!doctype" in low or b"<meta" in low:
        return "html"
    return "text"


class Parser:
    name = "base"
    kind = "bank"          # 'bank' | 'card'
    version = 1            # 파서를 고치면 올림 → 같은 파일도 다시 읽어 기존 거래 정보를 바로잡음
    description = ""

    def detect(self, path, fmt, text):
        """fmt: sniff 결과, text: html/text 파일이면 내용(앞부분 일부 아님, 전체), 그 외 None"""
        return 0

    def parse(self, path, fmt, text, options):
        raise NotImplementedError

    @staticmethod
    def filename(path):
        return os.path.basename(path)
