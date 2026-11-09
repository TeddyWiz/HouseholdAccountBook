"""가계부 웹페이지 서버

실행:  python web_app.py   →  브라우저에서 http://localhost:5000
같은 공유기(와이파이)의 휴대폰에서 보려면  python web_app.py --lan  후  http://<PC IP>:5000
"""
import os
import sys
import tempfile

from flask import Flask, jsonify, request, send_from_directory

from hab.config import load_config
from hab.service import get_service

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE, "web"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
app.json.ensure_ascii = False


def svc():
    return get_service()


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/overview")
def overview():
    s = svc()
    months = s.months()
    month = request.args.get("month") or (months[0] if months else "")
    bals = s.balances()
    return jsonify(months=months, month=month, summary=s.summary(month) if month else None,
                   balances=bals, balance_total=s.balance_total(bals),
                   trend=s.trend(12), bank_accounts=s.bank_accounts())


@app.get("/report")
def report_page():
    return send_from_directory(app.static_folder, "report.html")


@app.get("/api/report")
def report():
    s = svc()
    month = request.args.get("month") or (s.months() or [""])[0]
    return jsonify(s.report(month, show_internal=request.args.get("internal") == "1"))


@app.get("/api/transactions")
def transactions():
    a = request.args
    rows = svc().transactions(month=a.get("month") or None, view=a.get("view", "bank"),
                              show_internal=a.get("internal") == "1", account_id=a.get("account") or None,
                              q=a.get("q", "").strip())
    return jsonify(rows=rows)


@app.get("/api/statement")
def statement():
    return jsonify(svc().statement_detail(txn_id=request.args.get("txn_id")))


@app.post("/api/note")
def set_note():
    d = request.get_json(force=True)
    svc().set_note(d["id"], d.get("note", ""))
    return jsonify(ok=True)


@app.get("/api/sources")
def sources():
    return jsonify(svc().sources())


@app.post("/api/sources/owned")
def set_owned():
    d = request.get_json(force=True)
    svc().set_owned(d["id"], d.get("owned", True))
    return jsonify(ok=True)


@app.post("/api/sources/account")
def add_account():
    d = request.get_json(force=True)
    try:
        svc().add_account(d.get("number", ""), d.get("institution", ""), d.get("name", ""))
    except ValueError as e:
        return jsonify(ok=False, message=str(e)), 400
    return jsonify(ok=True)


@app.post("/api/import")
def do_import():
    options = {}
    if request.form.get("account"):
        options["account"] = request.form["account"]
    if request.form.get("password"):
        options["password"] = request.form["password"]
    results = []
    for f in request.files.getlist("files"):
        name = os.path.basename(f.filename or "upload")
        tmpdir = tempfile.mkdtemp(prefix="hab_")
        path = os.path.join(tmpdir, name)
        f.save(path)
        try:
            results.append(svc().import_file(path, options, original_name=name))
        finally:
            try:
                os.remove(path)
                os.rmdir(tmpdir)
            except OSError:
                pass
    return jsonify(results=results)


def run(host=None, port=None, open_browser=False):
    cfg = load_config()
    port = port or cfg["web_port"]
    host = host or "127.0.0.1"
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run(host="0.0.0.0" if "--lan" in sys.argv else "127.0.0.1", open_browser="--no-browser" not in sys.argv)
