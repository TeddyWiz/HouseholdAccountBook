"""양식 파서 등록부. 새 양식은 여기 PARSERS 에 추가합니다."""
from ..util import read_text
from .banks import GenericBankTable, IbkBankHtml, IbkBankXls, KakaoBankXlsx, NhBankHtml, WooriBankHtml
from .base import NeedInput, ParseResult, Parser, sniff
from .cards import HyundaiCardStatement, IbkCardStatement, SamsungCardApprovals, SamsungCardStatement
from .hyundai_pdf import HyundaiCardPdf
from .ibk_pdf import IbkBcPdfStatement
from .pays import NaverPayMoneyHtml
from .pdf_cards import PdfCardStatement

PARSERS = [
    IbkBankHtml(), IbkBankXls(), NhBankHtml(), WooriBankHtml(), KakaoBankXlsx(), NaverPayMoneyHtml(),
    HyundaiCardStatement(), SamsungCardStatement(), SamsungCardApprovals(), IbkCardStatement(),
    HyundaiCardPdf(), IbkBcPdfStatement(), PdfCardStatement(),
    GenericBankTable(),
]


def choose_parser(path):
    import warnings
    warnings.filterwarnings("ignore", module="openpyxl")
    fmt = sniff(path)
    text = read_text(path) if fmt in ("html", "text") else None
    best, score = None, 0
    for p in PARSERS:
        try:
            s = p.detect(path, fmt, text)
        except Exception:
            s = 0
        if s > score:
            best, score = p, s
    return best, fmt, text


def parser_label(parser):
    return parser.name if getattr(parser, "version", 1) <= 1 else f"{parser.name} v{parser.version}"


def parse_file(path, options=None):
    options = options or {}
    parser, fmt, text = choose_parser(path)
    if not parser:
        raise ValueError("지원하지 않는 양식입니다.")
    res = parser.parse(path, fmt, text, options)
    res.kind = parser.kind
    res.parser = parser_label(parser)
    return res


__all__ = ["PARSERS", "choose_parser", "parse_file", "parser_label", "NeedInput", "ParseResult", "Parser"]
