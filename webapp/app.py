"""Local web dashboard for Financial Whistler.

Binds to 127.0.0.1 by default (not exposed to the network) since it
triggers a local scraper that needs this machine's GUI session, and manages
this machine's files (companies.xlsx, .env, launchd schedule). Login is
required regardless of bind address -- see auth.py. Set DASHBOARD_HOST in
.env to 0.0.0.0 to make it reachable from other devices on your network,
once you're ready for that; the credential requirement is what makes that
safe to do.
"""

import datetime
import io
import os
import plistlib
import re
import subprocess
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")

from financial_whistler.config import load_companies, match_columns  # noqa: E402
from financial_whistler import lookup as tadawul_lookup  # noqa: E402
from financial_whistler import lookup_qatar, lookup_india  # noqa: E402
from financial_whistler import lookup_oman, lookup_kuwait, lookup_dfm  # noqa: E402
import log_export  # noqa: E402
import auth  # noqa: E402

VENV_PYTHON = BASE_DIR / ".venv" / "bin" / "python3"
RUN_SCRIPT = BASE_DIR / "run_whistler.py"
COMPANIES_FILE = BASE_DIR / "companies.xlsx"
LOG_FILE = BASE_DIR / "logs" / "whistler.log"
WEBAPP_LOG_FILE = BASE_DIR / "logs" / "webapp.log"

PLIST_LABEL = "com.euroland.financialwhistler"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"

DEFAULT_HEADERS = ["company_name", "Market", "ticker_code / Symbol", "announcement_url"]

if not auth.get_credentials():
    sys.exit(
        "DASHBOARD_USERNAME and DASHBOARD_PASSWORD must both be set in .env before "
        "the dashboard will start. Add them (any values you choose), then restart. "
        "See .env.example."
    )

app = Flask(__name__)
app.secret_key = auth.get_or_create_secret_key(BASE_DIR / ".env")


@app.before_request
def _require_login():
    return auth.login_required_before_request()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if auth.check_login(username, password):
            session["logged_in"] = True
            session["username"] = username
            next_url = request.form.get("next") or url_for("index")
            return redirect(next_url)
        flash("Incorrect username or password.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


_run_lock = threading.Lock()
_run_state = {"running": False, "started_at": None, "mode": None}


def _uid() -> str:
    import os

    return str(os.getuid())


# ---------- run control ----------


def _do_run(dry_run: bool) -> None:
    try:
        args = [str(VENV_PYTHON), str(RUN_SCRIPT)]
        if dry_run:
            args.append("--dry-run")
        subprocess.run(args, cwd=str(BASE_DIR), timeout=900)
    finally:
        with _run_lock:
            _run_state["running"] = False
            _run_state["started_at"] = None
            _run_state["mode"] = None


def trigger_run(dry_run: bool = False) -> bool:
    with _run_lock:
        if _run_state["running"]:
            return False
        _run_state["running"] = True
        _run_state["started_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        _run_state["mode"] = "dry-run" if dry_run else "live"
    threading.Thread(target=_do_run, args=(dry_run,), daemon=True).start()
    return True


# ---------- log parsing ----------

_SUMMARY_RE = re.compile(
    r"^(?P<ts>[\d-]+ [\d:,]+) INFO financial_whistler: Run summary: "
    r"(?P<ok>\d+)/(?P<total>\d+) companies checked successfully \((?P<failed>\d+) failed\), "
    r"(?P<new>\d+) new financial-result announcement"
)


def get_last_run_summary() -> dict | None:
    if not LOG_FILE.exists():
        return None
    last_match = None
    # Log can grow large over time; only scan a bounded tail for speed.
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-2000:]
    for line in lines:
        m = _SUMMARY_RE.match(line)
        if m:
            last_match = m
    if not last_match:
        return None
    d = last_match.groupdict()
    return {
        "timestamp": d["ts"],
        "checked_ok": int(d["ok"]),
        "total": int(d["total"]),
        "failed": int(d["failed"]),
        "new_alerts": int(d["new"]),
    }


def get_recent_log_lines(n: int = 60) -> list[str]:
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return [line.rstrip("\n") for line in lines[-n:]]


# ---------- schedule control ----------
#
# Two backends: launchd on macOS (the original), systemd --user on Linux
# (added when this got deployed to a server). Dispatched by sys.platform so
# the same dashboard code works unmodified on either. systemd --user rather
# than a system-level unit deliberately -- it lets the dashboard's own
# process (running as an unprivileged user) toggle the timer with plain
# `systemctl --user`, no sudo needed for every click. That only works
# headless (no one logged in) because deploy/install_services.sh enables
# "lingering" for the user once at setup time; without that, user units
# stop when the SSH session that started them ends.

LINUX_TIMER_UNIT = "financial-whistler-run.timer"


def get_schedule_info() -> dict:
    if sys.platform == "darwin":
        return _get_schedule_info_launchd()
    return _get_schedule_info_systemd()


def set_schedule_enabled(enable: bool) -> tuple[bool, str]:
    if sys.platform == "darwin":
        return _set_schedule_enabled_launchd(enable)
    return _set_schedule_enabled_systemd(enable)


def _get_schedule_info_launchd() -> dict:
    installed = PLIST_PATH.exists()
    times = []
    if installed:
        try:
            with open(PLIST_PATH, "rb") as f:
                plist = plistlib.load(f)
            for entry in plist.get("StartCalendarInterval", []):
                times.append(f"{entry.get('Hour', 0):02d}:{entry.get('Minute', 0):02d}")
        except Exception:
            pass

    enabled = False
    if installed:
        try:
            result = subprocess.run(
                ["launchctl", "print", f"gui/{_uid()}/{PLIST_LABEL}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            enabled = result.returncode == 0
        except Exception:
            enabled = False

    return {"installed": installed, "enabled": enabled, "times": times}


def _set_schedule_enabled_launchd(enable: bool) -> tuple[bool, str]:
    if not PLIST_PATH.exists():
        return False, "Schedule file not found -- it needs to be set up first (contact your admin)."
    uid = _uid()
    cmd = (
        ["launchctl", "bootstrap", f"gui/{uid}", str(PLIST_PATH)]
        if enable
        else ["launchctl", "bootout", f"gui/{uid}/{PLIST_LABEL}"]
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0 and "already" not in (result.stderr or "").lower():
        # bootout on an already-unloaded job (or bootstrap on an already-loaded
        # one) returns non-zero but isn't really a failure worth surfacing.
        if "3: No such process" not in (result.stderr or "") and "already bootstrapped" not in (
            result.stderr or ""
        ):
            return False, result.stderr.strip() or "Unknown error"
    return True, ""


def _get_schedule_info_systemd() -> dict:
    times = []
    installed = False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "cat", LINUX_TIMER_UNIT],
            capture_output=True,
            text=True,
            timeout=10,
        )
        installed = result.returncode == 0
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("OnCalendar="):
                # "OnCalendar=*-*-* 07:30:00 UTC" -> "07:30 UTC"
                parts = line.split("=", 1)[1].split()
                if len(parts) >= 2:
                    label = parts[1][:5]
                    if len(parts) >= 3:
                        label += f" {parts[2]}"
                    times.append(label)
    except Exception:
        installed = False

    enabled = False
    if installed:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", LINUX_TIMER_UNIT],
                capture_output=True,
                text=True,
                timeout=10,
            )
            enabled = result.stdout.strip() == "active"
        except Exception:
            enabled = False

    return {"installed": installed, "enabled": enabled, "times": times}


def _set_schedule_enabled_systemd(enable: bool) -> tuple[bool, str]:
    cmd = (
        ["systemctl", "--user", "enable", "--now", LINUX_TIMER_UNIT]
        if enable
        else ["systemctl", "--user", "disable", "--now", LINUX_TIMER_UNIT]
    )
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or "Unknown error").strip()
    return True, ""


# ---------- companies management ----------


def read_companies_rows() -> list[dict]:
    companies = load_companies(COMPANIES_FILE)
    return [
        {"name": c.name, "ticker": c.ticker, "url": c.profile_url, "market": c.market}
        for c in companies
    ]


def _load_workbook():
    import openpyxl

    return openpyxl.load_workbook(COMPANIES_FILE)


def _header_row(ws) -> tuple:
    return next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())


def add_company_row(name: str, ticker: str, url: str, market: str = "") -> None:
    import openpyxl
    from openpyxl.styles import Font

    if COMPANIES_FILE.exists():
        wb = _load_workbook()
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "companies"
        ws.append(DEFAULT_HEADERS)
        for cell in ws[1]:
            cell.font = Font(bold=True)

    cols = match_columns(_header_row(ws))
    width = ws.max_column
    row = [""] * width
    for key, value in (("name", name), ("ticker", ticker), ("url", url), ("market", market)):
        idx = cols.get(key)
        if idx is not None and idx < width:
            row[idx] = value
    ws.append(row)
    wb.save(COMPANIES_FILE)


def delete_company_row(ticker: str) -> bool:
    wb = _load_workbook()
    ws = wb.active
    cols = match_columns(_header_row(ws))
    ticker_idx = cols.get("ticker")
    if ticker_idx is None:
        return False
    for row in list(ws.iter_rows(min_row=2)):
        if ticker_idx < len(row) and str(row[ticker_idx].value).strip() == ticker:
            ws.delete_rows(row[0].row, 1)
            wb.save(COMPANIES_FILE)
            return True
    return False


def replace_companies_file(uploaded_stream) -> tuple[bool, str]:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(uploaded_stream, read_only=True, data_only=True)
    except Exception as exc:
        return False, f"That doesn't look like a valid Excel (.xlsx) file: {exc}"

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return False, "The uploaded file is empty."

    cols = match_columns(rows[0])
    if "name" not in cols or "ticker" not in cols or "url" not in cols:
        return False, (
            "Couldn't find company name / ticker / URL columns in the first row. "
            f"Found headers: {', '.join(str(c) for c in rows[0] if c) or '(empty)'}"
        )

    ticker_idx = cols["ticker"]
    data_rows = [r for r in rows[1:] if r and r[0] and ticker_idx < len(r) and r[ticker_idx]]
    if not data_rows:
        return False, "No company rows found under the header row."

    uploaded_stream.seek(0)
    if COMPANIES_FILE.exists():
        backup = COMPANIES_FILE.with_name(
            f"companies.xlsx.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}"
        )
        COMPANIES_FILE.replace(backup)
    with open(COMPANIES_FILE, "wb") as f:
        f.write(uploaded_stream.read())
    return True, f"Uploaded successfully: {len(data_rows)} companies loaded."


# ---------- routes ----------


@app.route("/")
def index():
    return render_template(
        "index.html",
        run_state=_run_state,
        last_summary=get_last_run_summary(),
        schedule=get_schedule_info(),
        companies_count=len(read_companies_rows()),
        recent_log=get_recent_log_lines(40),
    )


@app.route("/run", methods=["POST"])
def run_now():
    dry_run = request.form.get("mode") == "dry-run"
    started = trigger_run(dry_run=dry_run)
    if started:
        flash(f"Run started ({'test / dry-run' if dry_run else 'live'}). This takes a couple of minutes.", "info")
    else:
        flash("A run is already in progress.", "warning")
    return redirect(url_for("index"))


@app.route("/run/status")
def run_status():
    return {
        "running": _run_state["running"],
        "started_at": _run_state["started_at"],
        "mode": _run_state["mode"],
        "last_summary": get_last_run_summary(),
    }


@app.route("/schedule/enable", methods=["POST"])
def schedule_enable():
    ok, err = set_schedule_enabled(True)
    flash("Schedule enabled." if ok else f"Couldn't enable schedule: {err}", "info" if ok else "error")
    return redirect(url_for("index"))


@app.route("/schedule/disable", methods=["POST"])
def schedule_disable():
    ok, err = set_schedule_enabled(False)
    flash("Schedule disabled." if ok else f"Couldn't disable schedule: {err}", "info" if ok else "error")
    return redirect(url_for("index"))


@app.route("/companies")
def companies():
    return render_template("companies.html", companies=read_companies_rows())


_LOOKUP_SOURCES = {
    "ksa": (tadawul_lookup, "KSA", "Tadawul"),
    "qatar": (lookup_qatar, "Qatar", "Qatar Exchange"),
    "india": (lookup_india, "INDIA", "NSE"),
    "oman": (lookup_oman, "Oman", "MSX"),
    "kuwait": (lookup_kuwait, "KUWAIT", "Boursa Kuwait"),
    "uae": (lookup_dfm, "UAE", "DFM"),
}


@app.route("/companies/lookup")
def companies_lookup():
    query = request.args.get("q", "")
    market_key = request.args.get("market", "ksa")
    if len(query.strip()) < 2:
        return {"results": [], "error": None}

    source = _LOOKUP_SOURCES.get(market_key)
    if not source:
        return {"results": [], "error": f"Unknown market {market_key!r}"}
    module, market_value, display_name = source

    try:
        results = module.search_companies(query)
        already_added = {c["ticker"] for c in read_companies_rows()}
        for r in results:
            r["already_added"] = r["ticker"] in already_added
            r["market"] = market_value
        return {"results": results, "error": None}
    except Exception as exc:
        app.logger.exception("%s lookup failed", display_name)
        return {
            "results": [],
            "error": (
                f"Couldn't reach {display_name} to search right now "
                f"({exc}). You can still add the company manually below."
            ),
        }


@app.route("/companies/add", methods=["POST"])
def companies_add():
    name = request.form.get("name", "").strip()
    ticker = request.form.get("ticker", "").strip()
    url = request.form.get("url", "").strip()
    market = request.form.get("market", "").strip()
    if not name or not ticker or not url or not market:
        flash("Name, market, ticker, and announcement URL are all required.", "error")
    elif ticker in {c["ticker"] for c in read_companies_rows()}:
        flash(f"{name} ({ticker}) is already on the list.", "warning")
    else:
        add_company_row(name, ticker, url, market)
        flash(f"Added {name} ({ticker}). It will be checked starting next run.", "info")
    return redirect(url_for("companies"))


@app.route("/companies/delete/<ticker>", methods=["POST"])
def companies_delete(ticker):
    if delete_company_row(ticker):
        flash(f"Removed ticker {ticker}.", "info")
    else:
        flash(f"Couldn't find ticker {ticker}.", "error")
    return redirect(url_for("companies"))


@app.route("/companies/upload", methods=["POST"])
def companies_upload():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for("companies"))
    if not secure_filename(file.filename).endswith(".xlsx"):
        flash("Please upload a .xlsx Excel file.", "error")
        return redirect(url_for("companies"))
    ok, message = replace_companies_file(io.BytesIO(file.read()))
    flash(message, "info" if ok else "error")
    return redirect(url_for("companies"))


@app.route("/companies/download")
def companies_download():
    return send_file(COMPANIES_FILE, as_attachment=True, download_name="companies.xlsx")


@app.route("/logs")
def logs():
    return render_template("logs.html", lines=get_recent_log_lines(300), log_exists=LOG_FILE.exists())


@app.route("/logs/export")
def logs_export():
    if not LOG_FILE.exists():
        flash("No activity log file exists yet - nothing to export.", "error")
        return redirect(url_for("logs"))
    download_name = f"whistler-activity-log-{datetime.datetime.now():%Y%m%d-%H%M%S}.log"
    return send_file(LOG_FILE, as_attachment=True, download_name=download_name, mimetype="text/plain")


@app.route("/logs/export/xlsx")
def logs_export_xlsx():
    if not LOG_FILE.exists():
        flash("No activity log file exists yet - nothing to export.", "error")
        return redirect(url_for("logs"))
    lines = get_recent_log_lines(5000)
    buf = log_export.build_log_xlsx(lines)
    download_name = f"whistler-activity-log-{datetime.datetime.now():%Y%m%d-%H%M%S}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/logs/export/pdf")
def logs_export_pdf():
    if not LOG_FILE.exists():
        flash("No activity log file exists yet - nothing to export.", "error")
        return redirect(url_for("logs"))
    lines = get_recent_log_lines(5000)
    buf = log_export.build_log_pdf(lines)
    download_name = f"whistler-activity-log-{datetime.datetime.now():%Y%m%d-%H%M%S}.pdf"
    return send_file(buf, as_attachment=True, download_name=download_name, mimetype="application/pdf")


if __name__ == "__main__":
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("DASHBOARD_PORT", "5057").strip() or "5057")
    app.run(host=host, port=port, debug=False, threaded=True)
