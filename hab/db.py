"""SQLite 데이터베이스 (data/household.db)"""
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,          -- bank:<숫자> / card:<카드사>:<끝3자리 또는 이름>
    kind TEXT NOT NULL,                -- bank | card
    number TEXT DEFAULT '',
    institution TEXT DEFAULT '',
    name TEXT DEFAULT '',
    holder TEXT DEFAULT '',
    owned INTEGER DEFAULT 1,           -- 내 계좌 여부(내부이체 판단에 사용)
    balance INTEGER,
    balance_at TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    filename TEXT, path TEXT, sha1 TEXT UNIQUE, parser TEXT,
    imported_at TEXT, n_rows INTEGER, n_new INTEGER
);
CREATE TABLE IF NOT EXISTS txns (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                -- bank | card
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    date TEXT NOT NULL, time TEXT DEFAULT '',
    -- 통장
    out_amt INTEGER DEFAULT 0, in_amt INTEGER DEFAULT 0, balance INTEGER,
    description TEXT DEFAULT '', memo TEXT DEFAULT '',
    counter_account TEXT DEFAULT '', counter_bank TEXT DEFAULT '', counter_name TEXT DEFAULT '',
    txn_type TEXT DEFAULT '', branch TEXT DEFAULT '',
    -- 카드
    amount INTEGER DEFAULT 0, merchant TEXT DEFAULT '',
    inst_months INTEGER DEFAULT 0, inst_round INTEGER DEFAULT 0, inst_remain_amt INTEGER DEFAULT 0,
    status TEXT DEFAULT '', detail TEXT DEFAULT '', approval_no TEXT DEFAULT '', pay_month TEXT DEFAULT '',
    -- 관리
    dkey TEXT NOT NULL,
    is_internal INTEGER DEFAULT 0, pair_id INTEGER,
    card_company TEXT DEFAULT '', statement_id INTEGER,
    source_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_txn_key ON txns(account_id, dkey);
CREATE INDEX IF NOT EXISTS ix_txn_date ON txns(date);
CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY,
    company TEXT NOT NULL, bill_month TEXT NOT NULL,
    total INTEGER, exact INTEGER DEFAULT 0,
    UNIQUE(company, bill_month)
);
CREATE TABLE IF NOT EXISTS statement_items (
    statement_id INTEGER NOT NULL, txn_id INTEGER NOT NULL,
    billed INTEGER, round INTEGER DEFAULT 0,
    PRIMARY KEY(statement_id, txn_id)
);
"""

_lock = threading.RLock()


class DB:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        self.lock = _lock

    def _migrate(self):
        """이전 버전 DB에 새 열 추가 (기존 데이터는 그대로)"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(txns)").fetchall()}
        if "user_note" not in cols:
            # 사용자가 직접 적는 메모. 파일 가져오기(중복 처리)는 이 열을 건드리지 않음
            self.conn.execute("ALTER TABLE txns ADD COLUMN user_note TEXT DEFAULT ''")

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    def commit(self):
        with self.lock:
            self.conn.commit()
