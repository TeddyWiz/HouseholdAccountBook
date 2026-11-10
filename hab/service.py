"""화면(웹/앱)에서 공통으로 쓰는 조회 기능"""
import os
import shutil

from .config import CARD_STATEMENT_HELP, load_config
from .db import DB
from .importer import Importer
from .parsers import NeedInput
from .recurring import Recurring, base_key
from .util import digits

_service = None


def get_service():
    global _service
    if _service is None:
        _service = Service(load_config()["db_path"])
    return _service


class Service:
    def __init__(self, db_path):
        self.db = DB(db_path)
        self.importer = Importer(self.db)

    # ------------------------------------------------------------ 기간
    def months(self):
        return [r["m"] for r in self.db.q("SELECT DISTINCT substr(date,1,7) m FROM txns ORDER BY m DESC")]

    # ------------------------------------------------------------ 요약
    def summary(self, month):
        db = self.db
        b = db.one("SELECT COALESCE(SUM(in_amt),0) income, COALESCE(SUM(out_amt),0) expense, "
                   "COALESCE(SUM(CASE WHEN card_company<>'' THEN out_amt ELSE 0 END),0) card_paid "
                   "FROM txns WHERE kind='bank' AND is_internal=0 AND substr(date,1,7)=?", (month,))
        it = db.one("SELECT COALESCE(SUM(out_amt),0) s, COUNT(*) n FROM txns "
                    "WHERE kind='bank' AND is_internal=1 AND substr(date,1,7)=?", (month,))
        c = db.one("SELECT COALESCE(SUM(amount),0) s, COUNT(*) n FROM txns "
                   "WHERE kind='card' AND status='' AND substr(date,1,7)=?", (month,))
        return {"month": month, "income": b["income"], "expense": b["expense"],
                "net": b["income"] - b["expense"], "card_paid": b["card_paid"],
                "card_usage": c["s"], "card_count": c["n"],
                "internal_amount": it["s"], "internal_count": it["n"]}

    def trend(self, last=12):
        rows = self.db.q("SELECT substr(date,1,7) m, SUM(in_amt) income, SUM(out_amt) expense FROM txns "
                         "WHERE kind='bank' AND is_internal=0 GROUP BY m ORDER BY m DESC LIMIT ?", (last,))
        return list(reversed(rows))

    def balances(self, as_of=None):
        """계좌별 잔액. as_of('YYYY-MM-DD')를 주면 그날 마지막 거래 기준(월말 잔액 등)"""
        out = []
        for a in self.db.q("SELECT * FROM accounts WHERE kind='bank' ORDER BY institution, number"):
            last = self.db.one("SELECT date, time, balance FROM txns WHERE account_id=? AND date<=? "
                               "ORDER BY date DESC, time DESC, id ASC LIMIT 1", (a["id"], as_of or "9999-12-31"))
            bal, at = a["balance"], a["balance_at"]
            if as_of and at and at[:10] > as_of:
                bal, at = None, None
            if last and (not at or f"{last['date']} {last['time']}" > at):
                bal, at = last["balance"], f"{last['date']} {last['time']}"
            if bal is None:
                continue
            out.append({"id": a["id"], "number": a["number"], "institution": a["institution"],
                        "name": a["name"], "balance": bal, "as_of": (at or "")[:16]})
        return out

    def month_end_balances(self, month):
        """선택한 달의 마지막 잔액: {계좌id: {balance, as_of}}, 총합, 월말 날짜"""
        import calendar
        if not month:
            return {}, None, None
        y, m = int(month[:4]), int(month[5:7])
        end = f"{month}-{calendar.monthrange(y, m)[1]:02d}"
        bals = self.balances(as_of=end)
        return {b["id"]: {"balance": b["balance"], "as_of": b["as_of"]} for b in bals}, self.balance_total(bals), end

    def interest_summary(self, month):
        """선택한 달의 이자: 통장 거래 중 거래구분 또는 상세내역에 '이자'가 있는 거래
        낸 이자(출금: 대출이자·대월결산이자 등)와 받은 이자(입금: 예금이자 등)를 나눠 합계, 계좌별 합계"""
        if not month:
            return None
        rows = self.db.q("SELECT t.account_id, t.date, t.time, t.out_amt, t.in_amt, t.txn_type, t.description, "
                         "a.number, a.name, a.institution FROM txns t JOIN accounts a ON a.id=t.account_id "
                         "WHERE t.kind='bank' AND substr(t.date,1,7)=? AND (t.txn_type LIKE '%이자%' OR t.description LIKE '%이자%') "
                         "ORDER BY a.institution, a.number, t.date", (month,))
        by = {}
        for r in rows:
            b = by.setdefault(r["account_id"], {"id": r["account_id"], "number": r["number"], "name": r["name"],
                                                "institution": r["institution"], "paid": 0, "received": 0, "count": 0})
            b["paid"] += r["out_amt"] or 0
            b["received"] += r["in_amt"] or 0
            b["count"] += 1
        paid = sum(b["paid"] for b in by.values())
        received = sum(b["received"] for b in by.values())
        return {"month": month, "paid": paid, "received": received, "net": received - paid, "count": len(rows),
                "by_account": list(by.values()),
                "items": [{"date": r["date"], "time": r["time"], "account": r["number"], "out": r["out_amt"],
                           "in": r["in_amt"], "txn_type": r["txn_type"], "description": r["description"]} for r in rows]}

    @staticmethod
    def balance_total(balances):
        return sum(b["balance"] for b in balances)

    # ------------------------------------------------------------ 월 보고서(인쇄/PDF)
    def report(self, month, show_internal=False):
        import calendar
        y, m = int(month[:4]), int(month[5:7])
        month_end = f"{month}-{calendar.monthrange(y, m)[1]:02d}"
        bals = self.balances(as_of=month_end)
        cards = {}
        card_rows = self.transactions(month=month, view="card")
        # 각 사용 건의 청구 내역(어느 달 명세서에 얼마가 청구됐는지). 할부는 여러 달
        billed = {}
        if card_rows:
            ids = ",".join(str(r["id"]) for r in card_rows)
            for b in self.db.q("SELECT si.txn_id, s.bill_month, s.company, si.billed, si.round FROM statement_items si "
                               f"JOIN statements s ON s.id=si.statement_id WHERE si.txn_id IN ({ids}) "
                               "ORDER BY s.bill_month"):
                billed.setdefault(b["txn_id"], []).append(
                    {"month": b["bill_month"], "amount": b["billed"], "round": b["round"]})
        for r in card_rows:
            r["billed"] = billed.get(r["id"], [])
            cards.setdefault(r["account_name"] or r["account"], []).append(r)
        card_groups = []
        for k, v in sorted(cards.items()):
            first = [r["billed"][0]["amount"] for r in v if r["billed"] and r["billed"][0]["amount"] is not None]
            card_groups.append({"card": k, "rows": sorted(v, key=lambda r: (r["date"], r["time"])),
                                "total": sum(r["out"] for r in v), "billed_total": sum(first)})
        # 이번 달에 청구(통장에서 결제)되는 카드 명세서
        bills = self.db.q("SELECT s.company, s.bill_month, s.total, s.exact, "
                          "(SELECT COUNT(*) FROM statement_items si WHERE si.statement_id=s.id) n, "
                          "(SELECT t.out_amt FROM txns t WHERE t.statement_id=s.id AND t.kind='bank' LIMIT 1) paid "
                          "FROM statements s WHERE s.bill_month=? ORDER BY s.company", (month,))
        bank = sorted(self.transactions(month=month, view="bank", show_internal=show_internal),
                      key=lambda r: (r["date"], r["time"], r["id"]))
        return {"month": month, "month_end": month_end, "summary": self.summary(month),
                "balances": bals, "balance_total": self.balance_total(bals),
                "bank": bank, "cards": card_groups, "bills": bills, "interest": self.interest_summary(month)}

    # ------------------------------------------------------------ 거래 목록
    def transactions(self, month=None, view="bank", show_internal=False, account_id=None, q=""):
        sql = ("SELECT t.*, a.number acc_number, a.name acc_name, a.institution FROM txns t "
               "JOIN accounts a ON a.id=t.account_id WHERE 1=1")
        args = []
        if month:
            sql += " AND substr(t.date,1,7)=?"
            args.append(month)
        if view in ("bank", "card"):
            sql += " AND t.kind=?"
            args.append(view)
        if not show_internal:
            sql += " AND t.is_internal=0"
        if account_id:
            sql += " AND t.account_id=?"
            args.append(int(account_id))
        if q:
            sql += (" AND (t.description LIKE ? OR t.merchant LIKE ? OR t.memo LIKE ? OR t.counter_name LIKE ?"
                    " OR t.counter_account LIKE ? OR t.txn_type LIKE ? OR t.user_note LIKE ?)")
            args += [f"%{q}%"] * 7
        sql += " ORDER BY t.date DESC, t.time DESC, t.id ASC"
        rc = Recurring(self.db)
        out = []
        for r in self.db.q(sql, args):
            row = self._row(r)
            rec, same, mode, _ = rc.judge(r)
            row["recurring"] = rec          # 반복 거래(메모 공유) — 자동 판단 또는 직접 지정
            row["same_prev"] = same         # 반복 거래이면서 전달에도 같은 거래가 있음
            row["recurring_mode"] = mode    # 'on' 직접 지정 / 'off' 직접 제외 / None 자동
            out.append(row)
        return out

    @staticmethod
    def _row(r):
        bank = r["kind"] == "bank"
        months = r["inst_months"] or 0
        rnd = r["inst_round"] or 0
        if months > 1 and not rnd and r.get("pay_month"):
            # 회차 정보가 없으면(승인내역) 첫 결제월부터 지난 달 수로 추정
            from datetime import date
            t = date.today()
            y, m = int(r["pay_month"][:4]), int(r["pay_month"][5:7])
            rnd = max(0, min(months, (t.year - y) * 12 + (t.month - m) + 1))
        if bank:
            detail = r["description"]
            if r["branch"]:
                detail = f"{detail} ({r['branch']})" if detail else r["branch"]
        else:
            detail = " ".join(x for x in (r["detail"], "취소" if r["status"] else "") if x)
        return {
            "id": r["id"], "kind": r["kind"], "date": r["date"], "time": (r["time"] or "")[:8],
            "account": r["acc_number"] or r["acc_name"], "account_name": r["acc_name"],
            "out": (r["out_amt"] if bank else (r["amount"] if not r["status"] else 0)) or 0,
            "in": (r["in_amt"] if bank else 0) or 0,
            "counter_account": r["counter_account"] if bank else "",
            "counter_name": r["counter_name"] if bank else "",
            "counter_bank": r["counter_bank"] if bank else "",
            "memo": r["memo"] if bank else "",
            "txn_type": r["txn_type"],
            "merchant": "" if bank else r["merchant"],
            "inst_months": months if months > 1 else 0,
            "inst_remain": (months - rnd) if (months > 1 and rnd) else (None if months > 1 else 0),
            "inst_round": rnd,
            "detail": detail,
            "balance": r["balance"] if bank else None,
            "internal": bool(r["is_internal"]),
            "card_company": r["card_company"] if bank else "",
            "statement_id": r["statement_id"] if bank else None,
            "status": r["status"],
            "note": r.get("user_note") or "",
        }

    # ------------------------------------------------------------ 사용자 메모
    def _group_ids(self, t, k):
        return [x["id"] for x in self.db.q("SELECT * FROM txns WHERE account_id=? AND kind=?", (t["account_id"], t["kind"]))
                if base_key(x) == k]

    def set_note(self, txn_id, note):
        """메모 저장. 반복 거래(자동 판단 또는 직접 지정)면 묶음 전체와 앞으로 들어올 같은 거래에 공유.
        돌려주는 값: 메모가 적용된 거래 수"""
        note = (note or "").strip()
        t = self.db.one("SELECT * FROM txns WHERE id=?", (int(txn_id),))
        if not t:
            return 0
        rec, _, _, k = Recurring(self.db).judge(t)
        if rec:
            ids = self._group_ids(t, k)
            self.db.x("INSERT INTO shared_notes(rkey, note) VALUES (?,?) ON CONFLICT(rkey) DO UPDATE SET note=excluded.note",
                      (k, note))
            self.db.x("UPDATE txns SET user_note=? WHERE id IN (%s)" % ",".join(map(str, ids)), (note,))
            n = len(ids)
        else:
            self.db.x("UPDATE txns SET user_note=? WHERE id=?", (note, int(txn_id)))
            n = 1
        self.db.commit()
        return n

    # ------------------------------------------------------------ 반복 거래 직접 지정
    def set_recurring(self, txn_id, mode):
        """mode: 'on' 반복 거래로 지정 / 'off' 반복 거래에서 제외 / 'auto' 자동 판단으로 되돌리기
        돌려주는 값: 같은 묶음의 거래 수"""
        t = self.db.one("SELECT * FROM txns WHERE id=?", (int(txn_id),))
        if not t:
            return 0
        k = base_key(t)
        ids = self._group_ids(t, k)
        if mode == "auto":
            self.db.x("DELETE FROM recurring_overrides WHERE rkey=?", (k,))
        else:
            acc = self.db.one("SELECT number, name FROM accounts WHERE id=?", (t["account_id"],)) or {}
            amt = t["amount"] if t["kind"] == "card" else (t["out_amt"] or t["in_amt"])
            what = t["merchant"] if t["kind"] == "card" else t["description"]
            label = f"{acc.get('number') or acc.get('name', '')} · {amt:,}원 · {what}"
            self.db.x("INSERT INTO recurring_overrides(rkey, mode, label, updated) VALUES (?,?,?,datetime('now','localtime')) "
                      "ON CONFLICT(rkey) DO UPDATE SET mode=excluded.mode, label=excluded.label, updated=excluded.updated",
                      (k, mode, label))
        if mode == "off":
            self.db.x("DELETE FROM shared_notes WHERE rkey=?", (k,))   # 앞으로 들어올 거래에 메모를 붙이지 않음
        self.db.commit()
        if mode == "on" and (t["user_note"] or ""):
            self.set_note(t["id"], t["user_note"])   # 지정한 거래의 메모를 묶음 전체에 공유
        return len(ids)

    def recurring_overrides(self):
        return self.db.q("SELECT rkey, mode, label, updated FROM recurring_overrides ORDER BY updated DESC")

    def clear_override(self, rkey_):
        self.db.x("DELETE FROM recurring_overrides WHERE rkey=?", (rkey_,))
        self.db.commit()

    # ------------------------------------------------------------ 카드 상세
    def statement_detail(self, txn_id=None, statement_id=None):
        db = self.db
        pay = None
        if txn_id:
            pay = db.one("SELECT * FROM txns WHERE id=?", (int(txn_id),))
            statement_id = pay and pay["statement_id"]
        st = db.one("SELECT * FROM statements WHERE id=?", (statement_id,)) if statement_id else None
        items = []
        if st:
            items = db.q("SELECT t.*, a.name acc_name, a.number acc_number, si.billed, si.round "
                         "FROM statement_items si JOIN txns t ON t.id=si.txn_id JOIN accounts a ON a.id=t.account_id "
                         "WHERE si.statement_id=? ORDER BY t.date, t.time, t.id", (st["id"],))
        elif pay and pay["card_company"]:
            # 명세서 자료가 없으면: 해당 카드사의 전월 사용분을 참고로 보여줌
            y, m = int(pay["date"][:4]), int(pay["date"][5:7])
            pm = f"{y - 1 if m == 1 else y}-{12 if m == 1 else m - 1:02d}"
            items = db.q("SELECT t.*, a.name acc_name, a.number acc_number, NULL billed, 0 round FROM txns t "
                         "JOIN accounts a ON a.id=t.account_id WHERE t.kind='card' AND a.institution=? "
                         "AND substr(t.date,1,7)=? ORDER BY t.date, t.time", (pay["card_company"], pm))
        rows = []
        for t in items:
            months = t["inst_months"] or 0
            rnd = t["round"] or t["inst_round"] or 0
            rows.append({"id": t["id"], "note": t.get("user_note") or "", "date": t["date"], "time": t["time"], "card": t["acc_name"], "card_number": t["acc_number"],
                         "merchant": t["merchant"], "amount": t["amount"], "billed": t["billed"],
                         "inst_months": months if months > 1 else 0, "inst_round": rnd,
                         "inst_remain": (months - rnd) if months > 1 and rnd else None,
                         "txn_type": t["txn_type"], "status": t["status"], "detail": t["detail"]})
        return {
            "payment": {"date": pay["date"], "time": pay["time"], "amount": pay["out_amt"],
                        "company": pay["card_company"]} if pay else None,
            "statement": {"company": st["company"], "bill_month": st["bill_month"], "total": st["total"],
                          "exact": bool(st["exact"])} if st else None,
            "estimated": st is None,
            # 명세서가 없을 때: 어느 카드사의 몇 월 청구분이 필요한지와 내려받는 곳 안내
            "missing": None if st or not pay else {
                "company": pay["card_company"], "bill_month": pay["date"][:7],
                "last_month": (db.one("SELECT MAX(bill_month) m FROM statements WHERE company=?",
                                      (pay["card_company"],)) or {}).get("m"),
                "help": CARD_STATEMENT_HELP.get(pay["card_company"], "해당 카드사의 이용대금명세서를 내려받아 넣어 주세요.")},
            "items": rows,
            "items_total": sum((r["billed"] if r["billed"] is not None else r["amount"]) or 0 for r in rows
                               if not r["status"]),
        }

    # ------------------------------------------------------------ 소스
    def sources(self):
        accs = self.db.q("SELECT a.*, COUNT(t.id) n, MIN(t.date) first, MAX(t.date) last FROM accounts a "
                         "LEFT JOIN txns t ON t.account_id=a.id GROUP BY a.id ORDER BY a.kind, a.institution, a.number")
        files = self.db.q("SELECT filename, parser, imported_at, n_rows, n_new FROM sources ORDER BY imported_at DESC, id DESC")
        return {"accounts": accs, "files": files, "candidates": self.self_transfer_candidates(),
                "overrides": self.recurring_overrides()}

    def self_transfer_candidates(self):
        """예금주가 본인(내 계좌의 예금주)인데 아직 등록되지 않은 상대계좌 → '내 계좌 추가' 후보"""
        holders = {r["holder"] for r in self.db.q("SELECT holder FROM accounts WHERE kind='bank' AND holder<>''")}
        if not holders:
            return []
        known = {digits(a["number"]) for a in self.db.q("SELECT number FROM accounts")}
        rows = self.db.q("SELECT counter_account, counter_bank, counter_name, COUNT(*) n, "
                         "SUM(out_amt) out_sum, SUM(in_amt) in_sum FROM txns WHERE kind='bank' AND counter_account<>'' "
                         "AND counter_name IN (%s) GROUP BY counter_account ORDER BY n DESC" % ",".join("?" * len(holders)),
                         tuple(holders))
        return [r for r in rows if digits(r["counter_account"]) not in known and len(digits(r["counter_account"])) >= 8]

    def set_owned(self, account_id, owned):
        self.db.x("UPDATE accounts SET owned=? WHERE id=?", (1 if owned else 0, int(account_id)))
        self.importer.postprocess()
        self.db.commit()

    def add_account(self, number, institution="", name="", owned=True):
        key = "bank:" + digits(number)
        if not digits(number):
            raise ValueError("계좌번호를 입력하세요")
        self.db.x("INSERT OR IGNORE INTO accounts(key, kind, number, institution, name, owned) VALUES (?,?,?,?,?,?)",
                  (key, "bank", number, institution, name or f"{institution} {number}".strip(), 1 if owned else 0))
        self.importer.postprocess()
        self.db.commit()

    def bank_accounts(self):
        return self.db.q("SELECT id, number, institution, name FROM accounts WHERE kind='bank' ORDER BY institution, number")

    def import_file(self, path, options=None, original_name=None):
        """파일을 DB에 넣고, DataSource 폴더 밖의 파일이면 DataSource/Bank 또는 Card 에 사본 보관"""
        name = original_name or os.path.basename(path)
        try:
            r = self.importer.import_file(path, options or {}, original_name=name)
        except NeedInput as e:
            return {"file": name, "status": "need_" + e.field, "message": e.message}
        except Exception as e:
            return {"file": name, "status": "error", "message": f"{type(e).__name__}: {e}"}
        if r["status"] == "ok":
            ds = load_config()["datasource_dir"]
            try:
                inside = os.path.commonpath([os.path.abspath(path), os.path.abspath(ds)]) == os.path.abspath(ds)
            except ValueError:
                inside = False
            if not inside:
                folder = os.path.join(ds, "Card" if r.get("kind") == "card" else "Bank")
                os.makedirs(folder, exist_ok=True)
                base, ext = os.path.splitext(name)
                dest, n = os.path.join(folder, name), 1
                while os.path.exists(dest):
                    dest = os.path.join(folder, f"{base}_{n}{ext}")
                    n += 1
                shutil.copyfile(path, dest)
                r["saved_to"] = dest
        return r
