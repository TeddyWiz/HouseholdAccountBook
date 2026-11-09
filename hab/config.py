"""경로 및 규칙 설정."""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULTS = {
    # 데이터베이스 파일 (SQLite)
    "db_path": os.path.join(BASE_DIR, "data", "household.db"),
    # 원본 자료 폴더 (Bank / Card 하위 폴더)
    "datasource_dir": os.path.join(os.path.dirname(BASE_DIR), "DataSource"),
    # 내 계좌 간 이체로 판단할 시간 차이(초)
    "transfer_window_sec": 600,
    # 웹 서버 포트
    "web_port": 5000,
}

# 통장 출금이 어떤 카드사의 결제인지 판단하는 규칙: (카드사, [키워드...])
CARD_PAYMENT_RULES = [
    ("현대카드", ["현대카드"]),
    ("삼성카드", ["삼성카드"]),
    ("IBK기업카드", ["비씨카드", "BC카드", "기업카드", "IBK카드"]),
]


# 카드대금 명세서가 없을 때 안내할 내려받기 위치
CARD_STATEMENT_HELP = {
    "IBK기업카드": "IBK기업은행 인터넷뱅킹 → 카드 → 이용내역/명세서 → 월별명세서조회에서 엑셀(거래용 또는 출력용)을 내려받아 넣어 주세요.",
    "현대카드": "현대카드 홈페이지 → 이용대금명세서에서 엑셀로 내려받아 넣어 주세요.",
    "삼성카드": "삼성카드 홈페이지 → 이용대금명세서(엑셀) 또는 카드이용내역조회(엑셀)를 내려받아 넣어 주세요.",
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    for k in ("db_path", "datasource_dir"):
        if not os.path.isabs(cfg[k]):
            cfg[k] = os.path.normpath(os.path.join(BASE_DIR, cfg[k]))
    return cfg
