"""가계부 데스크톱 앱 (tkinter, 파이썬 기본 포함)

실행:  python desktop_app.py
"""
import os
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk

from hab.config import load_config
from hab.service import get_service

COLS = [("date", "날짜", 86), ("time", "시간", 66), ("account", "계좌/카드번호", 140), ("out", "지출금액", 95),
        ("in", "입금금액", 95), ("counter_account", "상대계좌번호", 130), ("counter_name", "상대계좌예금주명", 120),
        ("memo", "송금메세지", 110), ("txn_type", "거래구분", 80),
        ("detail", "상세내역", 260), ("rec", "반복", 46), ("note", "메모(반복 거래는 공유)", 170),
        ("balance", "잔액", 105)]
NUM_COLS = {"out", "in", "balance"}


HERE = os.path.dirname(os.path.abspath(__file__))
ICON_ICO = os.path.join(HERE, "assets", "app.ico")
ICON_PNG = os.path.join(HERE, "assets", "app.png")


def set_app_id():
    """작업 표시줄에 파이썬 아이콘 대신 가계부 아이콘이 보이도록 (Windows)"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HouseholdAccountBook.Desktop")
        except Exception:
            pass


def won(n):
    return "" if n in (None, "") else f"{int(n):,}"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("가계부")
        self.geometry("1500x880")
        self._set_icon()
        self.svc = get_service()
        self.month = tk.StringVar()
        self.view = tk.StringVar(value="bank")
        self.show_internal = tk.BooleanVar(value=False)
        self.fixed_only = tk.BooleanVar(value=False)
        self.account = None
        self.q = tk.StringVar()
        self.row_map = {}
        self._style()
        self._build()
        self.refresh()

    def _set_icon(self):
        try:
            if os.name == "nt" and os.path.exists(ICON_ICO):
                self.iconbitmap(default=ICON_ICO)      # 모든 창(상세, 소스 창 포함)에 적용
            elif os.path.exists(ICON_PNG):
                self._icon_img = tk.PhotoImage(file=ICON_PNG)
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        base = ("Malgun Gothic", 10)
        st.configure(".", font=base)
        st.configure("Treeview", rowheight=24)
        st.configure("Treeview.Heading", font=("Malgun Gothic", 10, "bold"))
        st.configure("Big.TLabel", font=("Malgun Gothic", 17, "bold"))
        st.configure("Cap.TLabel", foreground="#52514e")
        st.configure("Tile.TFrame", relief="solid", borderwidth=1)

    # ------------------------------------------------------------ 화면 구성
    def _build(self):
        top = ttk.Frame(self, padding=(12, 8))
        top.pack(fill="x")
        ttk.Label(top, text="가계부", font=("Malgun Gothic", 15, "bold")).pack(side="left", padx=(0, 16))
        ttk.Button(top, text="◀", width=3, command=lambda: self.shift(1)).pack(side="left")
        self.cb_month = ttk.Combobox(top, textvariable=self.month, width=10, state="readonly")
        self.cb_month.pack(side="left", padx=4)
        self.cb_month.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(top, text="▶", width=3, command=lambda: self.shift(-1)).pack(side="left")
        ttk.Button(top, text="웹페이지 열기", command=self.open_web).pack(side="right", padx=4)
        ttk.Button(top, text="월 보고서 인쇄/PDF", command=self.open_report).pack(side="right", padx=4)
        ttk.Button(top, text="데이터 입력", command=self.import_files).pack(side="right", padx=4)
        ttk.Button(top, text="소스", command=self.show_sources).pack(side="right", padx=4)

        # 월 수입/지출 (맨 위)
        tiles = ttk.Frame(self, padding=(12, 0))
        tiles.pack(fill="x")
        self.tiles = {}
        for key, cap, color in (("income", "이번 달 수입", "#2a78d6"), ("expense", "이번 달 지출", "#eb6834"),
                                ("net", "수입 − 지출", None), ("card", "카드 사용액(사용일 기준)", None)):
            f = ttk.Frame(tiles, style="Tile.TFrame", padding=10)
            f.pack(side="left", fill="x", expand=True, padx=4, pady=4)
            ttk.Label(f, text=cap, style="Cap.TLabel").pack(anchor="w")
            v = ttk.Label(f, text="-", style="Big.TLabel")
            if color:
                v.configure(foreground=color)
            v.pack(anchor="w")
            s = ttk.Label(f, text="", style="Cap.TLabel")
            s.pack(anchor="w")
            self.tiles[key] = (v, s)

        # 계좌별 잔액
        ttk.Label(self, text="  계좌별 잔액 (클릭하면 해당 계좌만 보기)", style="Cap.TLabel").pack(anchor="w", padx=12, pady=(6, 0))
        self.bal_frame = ttk.Frame(self, padding=(12, 2))
        self.bal_frame.pack(fill="x")
        self.int_frame = ttk.Frame(self, padding=(12, 2))   # 선택한 달의 이자 총합
        self.int_frame.pack(fill="x")

        # 필터
        flt = ttk.Frame(self, padding=(12, 8))
        flt.pack(fill="x")
        for v, t in (("bank", "통장"), ("card", "카드"), ("all", "전체")):
            ttk.Radiobutton(flt, text=t, value=v, variable=self.view, command=self.load_rows).pack(side="left")
        ttk.Checkbutton(flt, text="내 계좌 간 이체 표시", variable=self.show_internal,
                        command=self.load_rows).pack(side="left", padx=12)
        ttk.Checkbutton(flt, text="고정지출만", variable=self.fixed_only,
                        command=self.on_fixed_toggle).pack(side="left", padx=(0, 12))
        ttk.Label(flt, text="검색").pack(side="left")
        e = ttk.Entry(flt, textvariable=self.q, width=26)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.load_rows())
        ttk.Button(flt, text="전체 계좌", command=self.clear_account).pack(side="left", padx=6)
        self.lbl_count = ttk.Label(flt, text="", style="Cap.TLabel")
        self.lbl_count.pack(side="left", padx=8)
        ttk.Label(flt, text="※ 메모 칸 더블클릭 = 메모 입력 · 카드결제 행 더블클릭 = 카드 사용내역 · 오른쪽 클릭 = 반복 거래 지정",
                  style="Cap.TLabel").pack(side="right")

        # 거래 표
        wrap = ttk.Frame(self, padding=(12, 0, 12, 12))
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=[c[0] for c in COLS], show="headings")
        for key, title, w in COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=w, anchor="e" if key in NUM_COLS else "w", stretch=key in ("detail", "merchant"))
        ys = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.tree.tag_configure("internal", foreground="#8a8984")
        self.tree.tag_configure("cardpay", foreground="#2a78d6")
        self.tree.tag_configure("card", background="#f3f2ee")
        self.tree.tag_configure("income", foreground="#1c5cab")
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<Button-3>", self.on_right_click)   # 오른쪽 클릭: 반복 거래 지정/제외

    # ------------------------------------------------------------ 데이터 표시
    def refresh(self):
        months = self.svc.months()
        self.cb_month["values"] = months
        if months and self.month.get() not in months:
            self.month.set(months[0])
        m = self.month.get()
        if m:
            s = self.svc.summary(m)
            self.tiles["income"][0].configure(text=f"{won(s['income'])}원")
            self.tiles["income"][1].configure(text="내 계좌 간 이체 제외")
            self.tiles["expense"][0].configure(text=f"{won(s['expense'])}원")
            self.tiles["expense"][1].configure(text=f"카드대금 결제 {won(s['card_paid'])}원 포함")
            self.tiles["net"][0].configure(text=f"{'+' if s['net'] > 0 else ''}{won(s['net'])}원",
                                           foreground="#d03b3b" if s["net"] < 0 else "")
            self.tiles["net"][1].configure(text=f"내부이체 {s['internal_count']}건 ({won(s['internal_amount'])}원) 제외")
            self.tiles["card"][0].configure(text=f"{won(s['card_usage'])}원")
            self.tiles["card"][1].configure(text=f"{s['card_count']}건 · 참고용")
        for w in self.bal_frame.winfo_children():
            w.destroy()
        month_bal, month_total, _ = self.svc.month_end_balances(self.month.get())
        mlabel = f"{int(self.month.get()[5:7])}월 말" if self.month.get() else ""
        for b in self.svc.balances():
            f = tk.Frame(self.bal_frame, bd=1, relief="solid", padx=10, pady=6, cursor="hand2",
                         bg="#e8f0fb" if self.account == b["id"] else "#fcfcfb")
            f.pack(side="left", padx=4)
            l1 = tk.Label(f, text=f"{b['institution']} {b['number']}", fg="#52514e", bg=f["bg"], font=("Malgun Gothic", 9))
            l2 = tk.Label(f, text=f"{won(b['balance'])}원", bg=f["bg"], font=("Malgun Gothic", 13, "bold"),
                          fg="#d03b3b" if b["balance"] < 0 else "#0b0b0b")
            l3 = tk.Label(f, text=f"{b['as_of']} 기준", fg="#8a8984", bg=f["bg"], font=("Malgun Gothic", 8))
            mb = month_bal.get(b["id"])
            l4 = tk.Label(f, text=f"{mlabel} {won(mb['balance'])}원" if mb else f"{mlabel} 자료 없음", bg=f["bg"],
                          font=("Malgun Gothic", 10, "bold"),
                          fg="#d03b3b" if mb and mb["balance"] < 0 else ("#52514e" if mb else "#8a8984"))
            for w in (f, l1, l2, l3, l4):
                w.bind("<Button-1>", lambda e, i=b["id"]: self.pick_account(i))
            l1.pack(anchor="w"), l2.pack(anchor="w"), l3.pack(anchor="w"), l4.pack(anchor="w", pady=(4, 0))
        bals = self.svc.balances()
        if bals:
            total = self.svc.balance_total(bals)
            f = tk.Frame(self.bal_frame, bd=1, relief="solid", padx=12, pady=6, bg="#efeeea")
            f.pack(side="right", padx=4)
            tk.Label(f, text=f"계좌별 잔액 총합 · {len(bals)}개", fg="#52514e", bg=f["bg"],
                     font=("Malgun Gothic", 9)).pack(anchor="w")
            tk.Label(f, text=f"{won(total)}원", bg=f["bg"], font=("Malgun Gothic", 14, "bold"),
                     fg="#d03b3b" if total < 0 else "#0b0b0b").pack(anchor="w")
            tk.Label(f, text="마이너스 통장 포함", fg="#8a8984", bg=f["bg"], font=("Malgun Gothic", 8)).pack(anchor="w")
            if month_total is not None:
                tk.Label(f, text=f"{mlabel} 총합 {won(month_total)}원", bg=f["bg"], font=("Malgun Gothic", 10, "bold"),
                         fg="#d03b3b" if month_total < 0 else "#52514e").pack(anchor="w", pady=(4, 0))
        self.show_interest()
        self.load_rows()

    def show_interest(self):
        """계좌별 잔액 아래: 선택한 달의 이자 총합(낸 이자 / 받은 이자)과 계좌별 금액. 누르면 이자 거래만 보기"""
        for w in self.int_frame.winfo_children():
            w.destroy()
        it = self.svc.interest_summary(self.month.get())
        if not it:
            return
        m = int(self.month.get()[5:7])
        f = tk.Frame(self.int_frame, bd=1, relief="solid", padx=10, pady=4, bg="#efeeea", cursor="hand2")
        f.pack(side="left", padx=4)
        lab = tk.Label(f, bg=f["bg"], font=("Malgun Gothic", 10), justify="left",
                       text=f"{m}월 이자 총합 ({it['count']}건)\n낸 이자 {won(it['paid'])}원 · 받은 이자 {won(it['received'])}원 · "
                            f"합계 {'+' if it['net'] > 0 else ''}{won(it['net'])}원")
        lab.pack(anchor="w")

        def only_interest(_=None):
            self.q.set("이자")
            self.fixed_only.set(False)
            self.view.set("bank")
            self.load_rows()
        for w in (f, lab):
            w.bind("<Button-1>", only_interest)

        # 고정지출 (반복 거래 중 나간 돈) — 이자 총합 바로 옆
        fx = self.svc.fixed_expense_summary(self.month.get())
        if fx:
            g = tk.Frame(self.int_frame, bd=1, relief="solid", padx=10, pady=4, bg="#efeeea", cursor="hand2")
            g.pack(side="left", padx=4)
            gl = tk.Label(g, bg=g["bg"], font=("Malgun Gothic", 10), justify="left",
                          text=f"{m}월 고정지출 ({fx['count']}건)\n합계 {won(fx['total'])}원 · 통장 {won(fx['bank'])}원 · "
                               f"카드 {won(fx['card'])}원")
            gl.pack(anchor="w")

            def only_fixed(_=None):
                self.fixed_only.set(True)
                self.q.set("")
                self.view.set("all")
                self.load_rows()
            for w in (g, gl):
                w.bind("<Button-1>", only_fixed)
        for a in it["by_account"]:
            if not (a["paid"] or a["received"]):
                continue
            parts = []
            if a["paid"]:
                parts.append(f"낸 이자 {won(a['paid'])}원")
            if a["received"]:
                parts.append(f"받은 이자 {won(a['received'])}원")
            g = tk.Frame(self.int_frame, bd=1, relief="solid", padx=8, pady=4, bg="#fcfcfb")
            g.pack(side="left", padx=4)
            tk.Label(g, bg=g["bg"], font=("Malgun Gothic", 9), justify="left", fg="#52514e",
                     text=f"{a['institution']} {a['number']}\n" + " · ".join(parts)).pack(anchor="w")

    def load_rows(self):
        self.tree.delete(*self.tree.get_children())
        self.row_map.clear()
        rows = self.svc.transactions(month=self.month.get() or None, view=self.view.get(),
                                     show_internal=self.show_internal.get(), account_id=self.account,
                                     q=self.q.get().strip(), fixed_only=self.fixed_only.get())
        for r in rows:
            inst = ""
            if r["inst_months"]:
                inst = f"{r['inst_months']}개월 / " + ("?" if r["inst_remain"] is None else f"{r['inst_remain']}개월 남음")
            detail = r["detail"]
            if r["kind"] == "card":
                # 메인 표에는 이용가맹점·할부 칸이 없으므로 카드 행은 상세내역에 함께 표시
                detail = " · ".join(x for x in (r["merchant"], inst, r["detail"]) if x)
            if r.get("same_prev"):
                detail += " [전달 동일]"
            if r["internal"]:
                detail += " [내부이체]"
            if r["card_company"]:
                detail += f" [{r['card_company']} 결제 ▶ 더블클릭]"
            acc = ("💳 " if r["kind"] == "card" else "") + (r["account"] or "")
            vals = [r["date"], r["time"], acc, won(r["out"]) if r["out"] else "", won(r["in"]) if r["in"] else ("0" if not r["out"] and r["kind"] == "bank" else ""),
                    r["counter_account"], r["counter_name"], r["memo"], r["txn_type"],
                    detail, ("★" if r.get("recurring") else "") + ("지정" if r.get("recurring_mode") == "on" else ("제외" if r.get("recurring_mode") == "off" else "")),
                    r["note"], "" if r["balance"] is None else won(r["balance"])]
            tags = []
            if r["internal"]:
                tags.append("internal")
            elif r["card_company"]:
                tags.append("cardpay")
            elif r["in"]:
                tags.append("income")
            if r["kind"] == "card":
                tags.append("card")
            iid = self.tree.insert("", "end", values=vals, tags=tags)
            self.row_map[iid] = r
        self.lbl_count.configure(text=f"{len(rows)}건")

    def shift(self, d):
        months = list(self.cb_month["values"])
        if self.month.get() in months:
            i = months.index(self.month.get()) + d
            if 0 <= i < len(months):
                self.month.set(months[i])
                self.refresh()

    def pick_account(self, aid):
        self.account = None if self.account == aid else aid
        if self.account and self.view.get() == "card":
            self.view.set("bank")
        self.refresh()

    def on_fixed_toggle(self):
        if self.fixed_only.get():
            self.view.set("all")
        self.load_rows()

    def clear_account(self):
        self.account = None
        self.refresh()

    # ------------------------------------------------------------ 카드 상세
    # ------------------------------------------------------------ 사용자 메모 (표 안에서 바로 입력)
    def edit_note(self, tree, iid, col, txn_id, current, on_saved=None):
        """col: '#번호' 형식의 열. 셀 위에 입력칸을 띄우고 Enter/포커스 이동 시 저장, Esc 취소"""
        tree.see(iid)
        box = tree.bbox(iid, col)
        if not box:
            return
        x, y, w, h = box
        ent = ttk.Entry(tree)
        ent.insert(0, current or "")
        ent.place(x=x, y=y, width=max(w, 220), height=h)
        ent.focus_set()
        ent.select_range(0, "end")
        done = {"v": False}

        def save(_=None):
            if done["v"]:
                return
            done["v"] = True
            val = ent.get().strip()
            ent.destroy()
            if val != (current or ""):
                n = self.svc.set_note(txn_id, val)
                tree.set(iid, col, val)
                if on_saved:
                    on_saved()
                if n > 1:  # 반복 거래 묶음 전체에 적용됨 → 목록 새로 그림
                    self.load_rows()
                    self.title(f"가계부 — 반복 거래 {n}건에 메모를 함께 적용했습니다")
                    self.after(3000, lambda: self.title("가계부"))

        def cancel(_=None):
            done["v"] = True
            ent.destroy()
        ent.bind("<Return>", save)
        ent.bind("<FocusOut>", save)
        ent.bind("<Escape>", cancel)

    def on_right_click(self, ev):
        iid = self.tree.identify_row(ev.y)
        r = self.row_map.get(iid)
        if not r:
            return
        self.tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0)
        state = ("직접 지정한 반복 거래" if r.get("recurring_mode") == "on" else
                 "직접 제외한 거래" if r.get("recurring_mode") == "off" else
                 "자동으로 찾은 반복 거래" if r.get("recurring") else "반복 거래 아님")
        menu.add_command(label=f"현재: {state}", state="disabled")
        menu.add_separator()
        menu.add_command(label="★ 반복 거래로 지정 (같은 계좌·금액·상세내역, 메모 공유)",
                         command=lambda: self.set_recurring(r, "on"))
        menu.add_command(label="☆ 반복 거래에서 제외", command=lambda: self.set_recurring(r, "off"))
        menu.add_command(label="자동 판단으로 되돌리기", command=lambda: self.set_recurring(r, "auto"))
        menu.tk_popup(ev.x_root, ev.y_root)

    def set_recurring(self, r, mode):
        n = self.svc.set_recurring(r["id"], mode)
        self.load_rows()
        msg = {"on": "반복 거래로 지정", "off": "반복 거래에서 제외", "auto": "자동 판단으로 되돌림"}[mode]
        self.title(f"가계부 — {msg} (같은 거래 {n}건)")
        self.after(3000, lambda: self.title("가계부"))

    def on_double(self, ev):
        iid = self.tree.identify_row(ev.y)
        r = self.row_map.get(iid)
        if not r:
            return
        col = self.tree.identify_column(ev.x)
        note_col = f"#{[c[0] for c in COLS].index('note') + 1}"
        if col == note_col or not r["card_company"]:
            # 메모 칸, 또는 카드결제가 아닌 행 → 메모 입력
            def saved():
                r["note"] = self.tree.set(iid, note_col)
            self.edit_note(self.tree, iid, note_col, r["id"], r["note"], saved)
            return
        self.show_card_detail(r)

    def show_card_detail(self, r):
        d = self.svc.statement_detail(txn_id=r["id"])
        win = tk.Toplevel(self)
        win.title(f"{r['card_company']} 결제 상세")
        win.geometry("1220x580")
        p, s = d["payment"], d["statement"]
        info = f"출금일 {p['date']} {p['time'] or ''}   출금액 {won(p['amount'])}원"
        if s:
            info += f"   명세서 {s['bill_month']} ({'명세서 자료' if s['exact'] else '승인내역으로 추정'})   명세서 합계 {won(s['total'])}원"
            if s["total"] != p["amount"]:
                info += f"   (차이 {won(p['amount'] - s['total'])}원)"
        else:
            ms = d.get("missing") or {}
            info += (f"\n※ {ms.get('company')} {ms.get('bill_month')} 청구분 명세서가 없습니다 "
                     f"(입력된 마지막 명세서: {ms.get('last_month') or '없음'}).\n{ms.get('help', '')}")
            if d["items"]:
                info += "\n아래는 전월 사용분 참고용입니다."
        ttk.Label(win, text=info, padding=10).pack(anchor="w")
        cols = [("date", "이용일", 90), ("time", "시간", 70), ("card", "카드", 140), ("merchant", "이용가맹점", 260),
                ("amount", "이용금액", 100), ("inst", "할부(개월/회차/남은)", 150), ("billed", "이번 청구액", 100),
                ("txn_type", "구분", 80), ("etc", "비고", 120), ("note", "메모(직접 입력)", 170)]
        tv = ttk.Treeview(win, columns=[c[0] for c in cols], show="headings")
        for k, t, w in cols:
            tv.heading(k, text=t)
            tv.column(k, width=w, anchor="e" if k in ("amount", "billed") else "w")
        item_of = {}
        for i in d["items"]:
            inst = ""
            if i["inst_months"]:
                inst = f"{i['inst_months']}개월 / {i['inst_round'] or '?'}회차 / " + \
                       ("?" if i["inst_remain"] is None else f"{i['inst_remain']}개월 남음")
            iid = tv.insert("", "end", values=[i["date"], i["time"] or "", i["card"], i["merchant"], won(i["amount"]),
                                               inst, "" if i["billed"] is None else won(i["billed"]), i["txn_type"],
                                               f"{i['status']} {i['detail']}".strip(), i["note"]])
            item_of[iid] = i
        tv.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        ttk.Label(win, text="행을 더블클릭하면 '메모' 칸에 직접 입력할 수 있습니다 (Enter 저장, Esc 취소).",
                  style="Cap.TLabel").pack(anchor="w", padx=10, pady=(0, 8))
        note_col = f"#{len(cols)}"

        def on_item_double(ev):
            iid = tv.identify_row(ev.y)
            it = item_of.get(iid)
            if not it:
                return

            def saved():
                it["note"] = tv.set(iid, note_col)
                self.load_rows()
            self.edit_note(tv, iid, note_col, it["id"], it["note"], saved)
        tv.bind("<Double-1>", on_item_double)
        if not d["items"]:
            ttk.Label(win, text="사용내역 자료가 없습니다. 해당 카드 명세서를 입력해 주세요.").pack()

    # ------------------------------------------------------------ 데이터 입력
    def import_files(self):
        paths = filedialog.askopenfilenames(
            title="은행 거래내역 / 카드 명세서 선택", initialdir=load_config()["datasource_dir"],
            filetypes=[("지원 파일", "*.xls *.xlsx *.html *.htm *.pdf"), ("모든 파일", "*.*")])
        if not paths:
            return
        lines = []
        for p in paths:
            opts = {}
            while True:
                r = self.svc.import_file(p, opts)
                if r["status"] == "need_password":
                    pw = simpledialog.askstring("PDF 암호", f"{os.path.basename(p)}\n{r['message']}", show="*", parent=self)
                    if not pw:
                        break
                    opts["password"] = pw
                    continue
                if r["status"] == "need_account":
                    acc = self.ask_account(os.path.basename(p))
                    if not acc:
                        break
                    opts["account"] = acc
                    continue
                break
            lines.append(f"[{r['status']}] {r['file']}: {r.get('message', '')}")
        self.refresh()
        messagebox.showinfo("데이터 입력 결과", "\n".join(lines), parent=self)

    def ask_account(self, fname):
        accs = self.svc.bank_accounts()
        win = tk.Toplevel(self)
        win.title("계좌 선택")
        win.grab_set()
        ttk.Label(win, text=f"{fname}\n이 파일에는 계좌번호가 없습니다. 어느 계좌의 내역인가요?", padding=10).pack()
        var = tk.StringVar()
        cb = ttk.Combobox(win, textvariable=var, width=40,
                          values=[f"{a['number']}  ({a['institution']} {a['name']})" for a in accs])
        cb.pack(padx=10, pady=4)
        ttk.Label(win, text="(목록에 없으면 계좌번호를 직접 입력)", style="Cap.TLabel").pack()
        out = {}

        def ok():
            out["v"] = var.get().split("  (")[0].strip()
            win.destroy()
        ttk.Button(win, text="확인", command=ok).pack(pady=8)
        self.wait_window(win)
        return out.get("v")

    # ------------------------------------------------------------ 소스
    def show_sources(self):
        d = self.svc.sources()
        win = tk.Toplevel(self)
        win.title("소스 · 보유 계좌와 카드")
        win.geometry("980x640")
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        def table(parent, cols, rows):
            tv = ttk.Treeview(parent, columns=[c[0] for c in cols], show="headings", height=8)
            for k, t, w in cols:
                tv.heading(k, text=t)
                tv.column(k, width=w, anchor="e" if k == "n" else "w")
            for r in rows:
                tv.insert("", "end", values=[r.get(k, "") for k, _, _ in cols])
            tv.pack(fill="both", expand=True, padx=6, pady=6)
            return tv

        f1 = ttk.Frame(nb)
        nb.add(f1, text="보유 계좌")
        banks = [dict(a, own="✔ 내 계좌" if a["owned"] else "", period=f"{a['first'] or ''} ~ {a['last'] or ''}")
                 for a in d["accounts"] if a["kind"] == "bank"]
        tv = table(f1, [("institution", "은행", 90), ("number", "계좌번호", 170), ("name", "이름", 200),
                        ("holder", "예금주", 80), ("n", "거래수", 70), ("period", "기간", 190), ("own", "내 계좌", 90)], banks)
        bar = ttk.Frame(f1)
        bar.pack(fill="x", padx=6, pady=4)

        def toggle():
            for iid in tv.selection():
                a = banks[tv.index(iid)]
                self.svc.set_owned(a["id"], not a["owned"])
            win.destroy()
            self.refresh()
            self.show_sources()
        ttk.Button(bar, text="선택 계좌 '내 계좌' 켜기/끄기", command=toggle).pack(side="left")
        no, inst = tk.StringVar(), tk.StringVar()
        ttk.Label(bar, text="   계좌번호").pack(side="left")
        ttk.Entry(bar, textvariable=no, width=20).pack(side="left", padx=2)
        ttk.Label(bar, text="기관").pack(side="left")
        ttk.Entry(bar, textvariable=inst, width=12).pack(side="left", padx=2)

        def add():
            try:
                self.svc.add_account(no.get(), inst.get())
            except ValueError as e:
                messagebox.showerror("오류", str(e), parent=win)
                return
            win.destroy()
            self.refresh()
            self.show_sources()
        ttk.Button(bar, text="내 계좌 추가", command=add).pack(side="left", padx=4)

        if d["candidates"]:
            ttk.Label(f1, text="본인 명의로 보이는 상대계좌 (선택 후 추가하면 그 계좌와의 이체를 지출/수입에서 제외)",
                      style="Cap.TLabel").pack(anchor="w", padx=6)
            tvc = table(f1, [("counter_bank", "은행", 120), ("counter_account", "계좌번호", 180),
                             ("counter_name", "예금주", 120), ("n", "거래수", 70)], d["candidates"])

            def add_c():
                for iid in tvc.selection():
                    c = d["candidates"][tvc.index(iid)]
                    self.svc.add_account(c["counter_account"], c["counter_bank"])
                win.destroy()
                self.refresh()
                self.show_sources()
            ttk.Button(f1, text="선택한 상대계좌를 내 계좌로 추가", command=add_c).pack(anchor="w", padx=6, pady=4)

        f2 = ttk.Frame(nb)
        nb.add(f2, text="보유 카드")
        cards = [dict(a, period=f"{a['first'] or ''} ~ {a['last'] or ''}") for a in d["accounts"] if a["kind"] == "card"]
        table(f2, [("institution", "카드사", 110), ("number", "카드번호", 200), ("name", "이름", 200),
                   ("n", "거래수", 70), ("period", "기간", 200)], cards)

        f4 = ttk.Frame(nb)
        nb.add(f4, text="반복 거래 지정")
        ovs = [dict(o, state="★ 반복 거래로 지정" if o["mode"] == "on" else "☆ 반복 거래에서 제외") for o in d["overrides"]]
        tvo = table(f4, [("state", "상태", 150), ("label", "거래 (계좌 · 금액 · 상세내역)", 480), ("updated", "지정 시각", 150)], ovs)

        def unov():
            for iid in tvo.selection():
                self.svc.clear_override(ovs[tvo.index(iid)]["rkey"])
            win.destroy()
            self.refresh()
            self.show_sources()
        ttk.Button(f4, text="선택 항목을 자동 판단으로 되돌리기", command=unov).pack(anchor="w", padx=6, pady=4)
        ttk.Label(f4, text="거래 목록에서 줄을 오른쪽 클릭하면 반복 거래로 지정하거나 제외할 수 있습니다.",
                  style="Cap.TLabel").pack(anchor="w", padx=6)

        f3 = ttk.Frame(nb)
        nb.add(f3, text="입력된 파일")
        table(f3, [("filename", "파일", 380), ("parser", "양식", 230), ("imported_at", "입력 시각", 150),
                   ("n_rows", "행", 60), ("n_new", "추가", 60)], d["files"])

    # ------------------------------------------------------------ 웹
    def _start_web(self):
        port = load_config()["web_port"]
        if not getattr(self, "_web_started", False):
            import web_app
            threading.Thread(target=lambda: web_app.run(port=port), daemon=True).start()
            self._web_started = True
        return port

    def open_web(self):
        port = self._start_web()
        self.after(1200, lambda: webbrowser.open(f"http://localhost:{port}"))

    def open_report(self):
        """선택한 달의 보고서를 브라우저로 열기 → 보고서의 '인쇄 / PDF 저장' 버튼으로 출력"""
        port = self._start_web()
        q = f"?month={self.month.get()}" + ("&internal=1" if self.show_internal.get() else "")
        self.after(1200, lambda: webbrowser.open(f"http://localhost:{port}/report{q}"))


if __name__ == "__main__":
    set_app_id()
    App().mainloop()
