"""파일 가져오기: 파싱 → 계좌 연결 → 중복 제거 저장 → 내부이체/카드결제 연결"""
import hashlib
import os
import re
from collections import defaultdict
from datetime import datetime

from .config import CARD_PAYMENT_RULES, load_config
from .parsers import NeedInput, choose_parser, parse_file, parser_label
from .util import digits

SUPPORTED_EXT = (".xls", ".xlsx", ".html", ".htm", ".pdf")


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_garbled(s):
    """한글이 MacRoman 등으로 깨진 문자열인지 (예: 'ÌŒ-ÌŒ ÷ƒ∏Á')"""
    if not s or re.search(r"[가-힣]", s):
        return False
    odd = sum(1 for ch in s if ord(ch) > 127)
    return odd >= 2


def dkey_of(t):
    if t["kind"] == "bank":
        return f"{t['date']} {t.get('time', '')}|{t.get('out_amt', 0)}|{t.get('in_amt', 0)}|{t.get('balance', 0)}"
    return f"{t['date']}|{t.get('amount', 0)}"


class Importer:
    def __init__(self, db):
        self.db = db
        self.cfg = load_config()

    # ------------------------------------------------------------ accounts
    def _resolve_masked(self, info):
        """'****-**-***4141' 처럼 가려진 계좌번호 → 이미 아는 전체 번호로 연결
        1) 같은 끝자리의 등록 계좌  2) 다른 통장 거래의 상대계좌번호(같은 은행)  3) 없으면 '은행:끝자리' 키"""
        number = info.get("number") or ""
        tail = digits(number)
        if info.get("kind", "bank") != "bank" or "*" not in number or len(tail) < 4:
            return info
        inst = info.get("institution") or ""
        info = dict(info)
        for a in self.db.q("SELECT key, number FROM accounts WHERE kind='bank'"):
            if digits(a["number"]).endswith(tail) and "*" not in a["number"]:
                info["key"], info["number"] = a["key"], a["number"]
                return info
        for r in self.db.q("SELECT DISTINCT counter_account FROM txns WHERE kind='bank' AND counter_account<>'' "
                           "AND counter_bank LIKE ?", (f"%{inst[:2]}%",)):
            if digits(r["counter_account"]).endswith(tail):
                info["key"], info["number"] = "bank:" + digits(r["counter_account"]), r["counter_account"]
                return info
        info["key"] = f"bank:{inst}:{tail}"
        return info

    def _account_id(self, info):
        db = self.db
        info = self._resolve_masked(info)
        row = db.one("SELECT * FROM accounts WHERE key=?", (info["key"],))
        if not row:
            aid = db.x("INSERT INTO accounts(key, kind, number, institution, name, holder) VALUES (?,?,?,?,?,?)",
                       (info["key"], info.get("kind", "bank"), info.get("number", ""),
                        info.get("institution", ""), info.get("name", ""), info.get("holder", "")))
            row = db.one("SELECT * FROM accounts WHERE id=?", (aid,))
        else:
            aid = row["id"]
            # 더 자세한 정보(긴 번호, 이름 등)가 오면 보강
            for col in ("number", "institution", "name", "holder"):
                v = info.get(col)
                if v and (not row[col] or (col == "number" and len(digits(v)) > len(digits(row[col])))):
                    db.x(f"UPDATE accounts SET {col}=? WHERE id=?", (v, aid))
        if info.get("balance") is not None and info.get("balance_at"):
            if not row.get("balance_at") or info["balance_at"] >= row["balance_at"]:
                db.x("UPDATE accounts SET balance=?, balance_at=? WHERE id=?",
                     (info["balance"], info["balance_at"], aid))
        return aid

    def _guess_account(self, res):
        """계좌번호가 없는 파일: 이미 저장된 거래와 가장 많이 겹치는 계좌를 찾음"""
        keys = {dkey_of(t) for t in res.txns}
        best, best_n = None, 0
        for a in self.db.q("SELECT id FROM accounts WHERE kind='bank'"):
            have = {r["dkey"] for r in self.db.q("SELECT dkey FROM txns WHERE account_id=?", (a["id"],))}
            n = len(keys & have)
            if n > best_n:
                best, best_n = a["id"], n
        return best

    # ------------------------------------------------------------ import
    def import_file(self, path, options=None, original_name=None, force=False):
        options = options or {}
        db = self.db
        name = original_name or os.path.basename(path)
        sha = sha1_of(path)
        done = db.one("SELECT parser FROM sources WHERE sha1=?", (sha,))
        if done and not force:
            parser = choose_parser(path)[0]
            # 같은 파일이라도 파서가 새 버전이면 다시 읽어 기존 거래를 바로잡음(중복 추가는 없음)
            if not parser or done["parser"] == parser_label(parser):
                return {"file": name, "status": "skip", "message": "이미 입력된 파일입니다(동일 내용).", "new": 0, "rows": 0}
        res = parse_file(path, options)
        with db.lock:
            acct_ids = {k: self._account_id(info) for k, info in res.accounts.items()}
            fixed_acct = None
            if any(t.get("acct") is None for t in res.txns):
                if options.get("no_guess"):
                    raise NeedInput("account", "계좌번호가 없는 파일 (나중에 다시 시도)")
                fixed_acct = self._guess_account(res)
                if fixed_acct is None:
                    raise NeedInput("account", "이 파일에는 계좌번호가 없습니다. 어느 계좌의 내역인지 선택하세요.")
            src_id = db.x("INSERT OR REPLACE INTO sources(filename, path, sha1, parser, imported_at, n_rows, n_new) "
                          "VALUES (?,?,?,?,?,?,?)",
                          (name, path, sha, res.parser, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           len(res.txns), 0))
            # (계좌, 키)별로 묶어서 '파일 안 건수 - DB 건수' 만큼만 추가 → 같은 날 같은 금액 2건도 보존
            groups = defaultdict(list)
            for i, t in enumerate(res.txns):
                aid = acct_ids.get(t.get("acct")) if t.get("acct") else fixed_acct
                t["_aid"] = aid
                t["_dkey"] = dkey_of(t)
                groups[(aid, t["_dkey"])].append(i)
            idx_to_id, new = {}, 0
            for (aid, key), idxs in groups.items():
                existing = db.q("SELECT * FROM txns WHERE account_id=? AND dkey=? ORDER BY id", (aid, key))
                for n, i in enumerate(idxs):
                    t = res.txns[i]
                    if n < len(existing):
                        idx_to_id[i] = existing[n]["id"]
                        self._merge(existing[n], t)
                    else:
                        idx_to_id[i] = self._insert(t, aid, key, src_id)
                        new += 1
            for st in res.statements:
                self._statement(st, idx_to_id)
            db.x("UPDATE sources SET n_new=? WHERE id=?", (new, src_id))
            self.postprocess()
            db.commit()
        msg = f"{len(res.txns)}건 중 {new}건 추가"
        if res.warnings:
            msg += " / " + " ".join(res.warnings)
        return {"file": name, "status": "ok", "parser": res.parser, "kind": getattr(res, "kind", "bank"),
                "rows": len(res.txns), "new": new, "message": msg}

    def _insert(self, t, aid, key, src_id):
        cols = ["kind", "account_id", "date", "time", "out_amt", "in_amt", "balance", "description", "memo",
                "counter_account", "counter_bank", "counter_name", "txn_type", "branch", "amount", "merchant",
                "inst_months", "inst_round", "inst_remain_amt", "status", "detail", "approval_no", "pay_month",
                "dkey", "source_id"]
        vals = dict(t, account_id=aid, dkey=key, source_id=src_id)
        row = [vals.get(c, "" if c not in ("out_amt", "in_amt", "amount", "inst_months", "inst_round",
                                            "inst_remain_amt", "balance") else 0) for c in cols]
        return self.db.x(f"INSERT INTO txns({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", row)

    def _merge(self, old, t):
        """이미 있는 거래: 비어 있는 정보만 보강 (예: 승인내역의 시간, 할부 최신 회차)"""
        upd = {}
        for c in ("time", "approval_no", "pay_month", "memo", "counter_account", "counter_name", "detail"):
            if t.get(c) and not old.get(c):
                upd[c] = t[c]
        if t.get("inst_months") and not old.get("inst_months"):
            upd["inst_months"] = t["inst_months"]
        if (t.get("inst_round") or 0) > (old.get("inst_round") or 0):
            upd["inst_round"] = t["inst_round"]
            upd["inst_remain_amt"] = t.get("inst_remain_amt", 0)
        if t.get("status") and not old.get("status"):
            upd["status"] = t["status"]
        om, nm = old.get("merchant") or "", t.get("merchant") or ""
        if nm and ((len(nm) > len(om) and om in nm) or (is_garbled(om) and not is_garbled(nm))):
            upd["merchant"] = nm
        if upd:
            sets = ",".join(f"{k}=?" for k in upd)
            self.db.x(f"UPDATE txns SET {sets} WHERE id=?", (*upd.values(), old["id"]))

    def _statement(self, st, idx_to_id):
        db = self.db
        row = db.one("SELECT * FROM statements WHERE company=? AND bill_month=?", (st["company"], st["bill_month"]))
        if not row:
            sid = db.x("INSERT INTO statements(company, bill_month, total, exact) VALUES (?,?,?,?)",
                       (st["company"], st["bill_month"], st["total"], 1 if st["exact"] else 0))
        else:
            sid = row["id"]
            if st["exact"] or not row["exact"]:
                if st["exact"] and not row["exact"]:
                    # 정확한 명세서가 들어오면 추정 항목을 지우고 명세서 기준으로
                    db.x("DELETE FROM statement_items WHERE statement_id=?", (sid,))
                db.x("UPDATE statements SET total=?, exact=? WHERE id=?",
                     (st["total"], 1 if (st["exact"] or row["exact"]) else 0, sid))
            elif row["exact"] and not st["exact"]:
                return  # 정확한 명세서가 이미 있으면 추정 자료는 무시
        for idx, billed, rnd in st["items"]:
            tid = idx_to_id.get(idx)
            if tid is None:
                continue
            db.x("INSERT INTO statement_items(statement_id, txn_id, billed, round) VALUES (?,?,?,?) "
                 "ON CONFLICT(statement_id, txn_id) DO UPDATE SET billed=excluded.billed, round=excluded.round",
                 (sid, tid, billed, rnd or 0))
        if not st["exact"]:
            tot = db.one("SELECT COALESCE(SUM(billed),0) s FROM statement_items WHERE statement_id=?", (sid,))["s"]
            db.x("UPDATE statements SET total=? WHERE id=? AND exact=0", (tot, sid))

    # ------------------------------------------------------------ 후처리
    def postprocess(self):
        self.mark_internal()
        self.link_card_payments()

    def mark_internal(self):
        db = self.db
        window = int(self.cfg.get("transfer_window_sec", 600))
        db.x("UPDATE txns SET is_internal=0, pair_id=NULL WHERE kind='bank'")
        owned = {a["id"]: digits(a["number"]) for a in db.q("SELECT id, number FROM accounts WHERE kind='bank' AND owned=1")}
        owned_nums = {v: k for k, v in owned.items() if v}
        rows = db.q("SELECT id, account_id, date, time, out_amt, in_amt, counter_account FROM txns "
                    "WHERE kind='bank' AND account_id IN (%s) ORDER BY date, time" % (",".join(map(str, owned)) or "0"))
        # 1) 상대계좌가 내 계좌
        def owner_of(counter):
            ca = digits(counter)
            if not ca:
                return None
            if "*" in counter:  # '333******4141' 처럼 가려진 번호: 앞·뒤 숫자로 비교
                parts = [p for p in re.split(r"\*+", re.sub(r"[^\d*]", "", counter)) if p]
                if len(parts) >= 2:
                    for num, aid in owned_nums.items():
                        if num.startswith(parts[0]) and num.endswith(parts[-1]):
                            return aid
                return None
            return owned_nums.get(ca)

        for r in rows:
            owner = owner_of(r["counter_account"])
            if owner and owner != r["account_id"]:
                db.x("UPDATE txns SET is_internal=1 WHERE id=?", (r["id"],))

        # 2) 같은 시간대(±window초)에 내 계좌 A 출금 = 내 계좌 B 입금
        def ts(r):
            try:
                return datetime.strptime(f"{r['date']} {r['time'] or '00:00:00'}", "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                return 0
        ins = defaultdict(list)
        for r in rows:
            if r["in_amt"] > 0:
                ins[r["in_amt"]].append(r)
        used = set()
        for r in rows:
            if r["out_amt"] <= 0:
                continue
            t0, best = ts(r), None
            for c in ins.get(r["out_amt"], []):
                if c["id"] in used or c["account_id"] == r["account_id"]:
                    continue
                dt = abs(ts(c) - t0)
                if dt <= window and (best is None or dt < best[0]):
                    best = (dt, c)
            if best:
                c = best[1]
                used.add(c["id"])
                db.x("UPDATE txns SET is_internal=1, pair_id=? WHERE id=?", (c["id"], r["id"]))
                db.x("UPDATE txns SET is_internal=1, pair_id=? WHERE id=?", (r["id"], c["id"]))

    @staticmethod
    def card_company_of(t):
        text = " ".join([t.get("description") or "", t.get("counter_name") or "", t.get("txn_type") or ""])
        for company, words in CARD_PAYMENT_RULES:
            if any(w in text for w in words):
                return company
        if (t.get("txn_type") or "").upper() == "BC":
            return "IBK기업카드"
        return ""

    def link_card_payments(self):
        db = self.db
        db.x("UPDATE txns SET card_company='', statement_id=NULL WHERE kind='bank'")
        stmts = defaultdict(dict)
        for s in db.q("SELECT * FROM statements"):
            stmts[s["company"]][s["bill_month"]] = s
        for t in db.q("SELECT * FROM txns WHERE kind='bank' AND out_amt>0 AND is_internal=0"):
            comp = self.card_company_of(t)
            if not comp:
                continue
            month = t["date"][:7]
            cands = stmts.get(comp, {})
            pick = cands.get(month)
            if not pick or pick["total"] != t["out_amt"]:
                # 금액이 정확히 맞는 앞뒤 달 명세서가 있으면 그쪽으로
                y, m = int(month[:4]), int(month[5:])
                near = [f"{y + (m + d - 1) // 12}-{(m + d - 1) % 12 + 1:02d}" for d in (-1, 1)]
                for nm in near:
                    if nm in cands and cands[nm]["total"] == t["out_amt"]:
                        pick = cands[nm]
                        break
            db.x("UPDATE txns SET card_company=?, statement_id=? WHERE id=?",
                 (comp, pick["id"] if pick else None, t["id"]))

    # ------------------------------------------------------------ 폴더 일괄
    def import_folder(self, folder, options=None, log=print):
        files = []
        for root, _, names in os.walk(folder):
            if root.endswith("_files"):
                continue
            for n in sorted(names):
                if n.lower().endswith(SUPPORTED_EXT):
                    files.append(os.path.join(root, n))
        results, retry = [], []
        for f in files:
            try:
                r = self.import_file(f, dict(options or {}, no_guess=True))
            except NeedInput as e:
                retry.append((f, e))
                continue
            except Exception as e:
                r = {"file": os.path.basename(f), "status": "error", "message": f"{type(e).__name__}: {e}"}
            results.append(r)
            log(f"[{r['status']}] {r['file']}: {r.get('message', '')}")
        for f, e in retry:  # 계좌번호 없는 파일은 다른 파일을 다 넣은 뒤 다시 시도
            try:
                r = self.import_file(f, options)
            except NeedInput as e2:
                r = {"file": os.path.basename(f), "status": "need_" + e2.field, "message": e2.message}
            except Exception as e2:
                r = {"file": os.path.basename(f), "status": "error", "message": str(e2)}
            results.append(r)
            log(f"[{r['status']}] {r['file']}: {r.get('message', '')}")
        return results
