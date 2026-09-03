"""Excel and PDF export helpers for the activity log page.

The log format is "%(asctime)s %(levelname)s %(name)s: %(message)s" (see
financial_whistler/run.py's setup_logging). Lines that don't match that
shape (e.g. a multi-line Playwright traceback) are kept as their own row
with blank Timestamp/Level/Logger columns rather than being dropped, so
nothing from the log is silently lost in the export.
"""

import io
import re

_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<level>[A-Z]+) (?P<logger>[\w.]+): (?P<msg>.*)$"
)


def parse_log_lines(lines: list[str]) -> list[dict]:
    rows = []
    for line in lines:
        m = _LINE_RE.match(line)
        if m:
            rows.append(m.groupdict())
        else:
            rows.append({"ts": "", "level": "", "logger": "", "msg": line})
    return rows


def build_log_xlsx(lines: list[str]) -> io.BytesIO:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "activity log"
    headers = ["Timestamp", "Level", "Logger", "Message"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row in parse_log_lines(lines):
        ws.append([row["ts"], row["level"], row["logger"], row["msg"]])

    widths = [22, 9, 28, 100]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_log_pdf(lines: list[str]) -> io.BytesIO:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    page_size = landscape(A4)
    c = canvas.Canvas(buf, pagesize=page_size)
    width, height = page_size

    left_margin = 12 * mm
    top_margin = height - 15 * mm
    line_height = 10
    font_name, font_size = "Courier", 7

    c.setFont(font_name, 10)
    c.drawString(left_margin, height - 10 * mm, "Financial Whistler - Activity Log Export")
    c.setFont(font_name, font_size)

    y = top_margin
    max_chars = 190  # fits landscape A4 at this font size/margins

    for line in lines:
        wrapped = line if len(line) <= max_chars else line[:max_chars] + " ..."
        if y < 12 * mm:
            c.showPage()
            c.setFont(font_name, font_size)
            y = top_margin
        c.drawString(left_margin, y, wrapped)
        y -= line_height

    c.save()
    buf.seek(0)
    return buf
