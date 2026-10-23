import os
from pathlib import Path

from .models import Company

BASE_DIR = Path(__file__).resolve().parent.parent

_PLACEHOLDER_VALUES = ("-", "tbd", "n/a", "nil", "")


def load_companies(path: str | Path) -> list[Company]:
    """Loads the client list: company_name, ticker_code, announcement_url.

    Supports .xlsx (first sheet, header row + data rows, same 3 columns)
    and the older .md pipe-table format, chosen by file extension.
    """
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        return _load_from_xlsx(path)
    return _load_from_markdown(path)


def match_columns(header_row: tuple) -> dict[str, int]:
    """Finds the name/ticker/url/market columns by header text rather than
    fixed position, so the sheet tolerates column reordering or an added
    column (e.g. a "Market" column) without silently misreading data."""
    result: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        text = str(cell).strip().lower() if cell is not None else ""
        if not text:
            continue
        if "name" not in result and ("company" in text or text == "name"):
            result["name"] = i
        elif "ticker" not in result and ("ticker" in text or "symbol" in text):
            result["ticker"] = i
        elif "url" not in result and ("url" in text or "announcement" in text):
            result["url"] = i
        elif "market" not in result and ("market" in text or "exchange" in text or "country" in text):
            result["market"] = i
    return result


def _load_from_xlsx(path: Path) -> list[Company]:
    import openpyxl

    companies: list[Company] = []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header_row = next(rows, None)
    if not header_row:
        wb.close()
        return companies

    cols = match_columns(header_row)
    if "name" not in cols or "ticker" not in cols or "url" not in cols:
        wb.close()
        raise ValueError(
            f"companies.xlsx header row must have name/ticker/url columns "
            f"(found: {header_row}) - could not identify all three"
        )

    def cell_at(row: tuple, key: str) -> str:
        idx = cols.get(key)
        if idx is None or idx >= len(row) or row[idx] is None:
            return ""
        return str(row[idx]).strip()

    for row in rows:
        if not row:
            continue
        name = cell_at(row, "name")
        ticker = cell_at(row, "ticker")
        url = cell_at(row, "url")
        market = cell_at(row, "market")
        if not name or not ticker:
            continue
        if not url or url.lower() in _PLACEHOLDER_VALUES:
            continue
        companies.append(Company(name=name, ticker=ticker, profile_url=url, market=market))
    wb.close()
    return companies


def _load_from_markdown(path: Path) -> list[Company]:
    """Parse the `| company_name | ticker_code | announcement_url |` markdown table."""
    companies: list[Company] = []
    with open(path, encoding="utf-8") as f:
        rows = [line.strip() for line in f if line.strip().startswith("|")]

    for row in rows[2:]:  # skip header + separator rows
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 3:
            continue
        name, ticker, url = cells[0], cells[1], cells[2]
        if not url or url.lower() in _PLACEHOLDER_VALUES:
            continue
        companies.append(Company(name=name, ticker=ticker, profile_url=url))
    return companies


def group_by_exchange_host(companies: list[Company]) -> dict[str, list[Company]]:
    grouped: dict[str, list[Company]] = {}
    for c in companies:
        grouped.setdefault(c.exchange_host, []).append(c)
    return grouped


class Settings:
    def __init__(self):
        self.companies_file = BASE_DIR / os.environ.get("COMPANIES_FILE", "companies.xlsx")
        self.state_file = BASE_DIR / os.environ.get("STATE_FILE", "state/seen.json")
        self.log_file = BASE_DIR / os.environ.get("LOG_FILE", "logs/whistler.log")

        self.smtp_host = os.environ.get("SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_password = os.environ.get("SMTP_PASSWORD", "")
        self.from_email = os.environ.get("FROM_EMAIL", "") or self.smtp_user

        recipients = os.environ.get("ALERT_RECIPIENTS", "")
        self.alert_recipients = [r.strip() for r in recipients.split(",") if r.strip()]

        # Daily status email (see daily_status.py) -- off unless recipients are set.
        status = os.environ.get("STATUS_RECIPIENTS", "")
        self.status_recipients = [r.strip() for r in status.split(",") if r.strip()]
        self.status_send_after = os.environ.get("STATUS_SEND_AFTER_IST", "18:00").strip() or "18:00"
        self.expected_runs_per_day = int(os.environ.get("EXPECTED_RUNS_PER_DAY", "3").strip() or "3")
        # A company with nothing new for longer than this is flagged in the
        # daily status -- listed companies publish every quarter, so a silent
        # one usually means its feed has stopped updating.
        self.stale_after_days = int(os.environ.get("STALE_AFTER_DAYS", "200").strip() or "200")
