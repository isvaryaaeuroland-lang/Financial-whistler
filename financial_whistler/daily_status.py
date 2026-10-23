"""Once-a-day status email: "all clear" or "check needed".

Result alerts and failure reports only fire when something happens, so on a
quiet day nobody hears anything -- and that silence looks the same whether
the runs are fine or have stopped altogether (e.g. the cron-job.org trigger's
GitHub token expired). This email is the heartbeat: each real run records a
one-line summary in state/seen.json, and the first run at or after
STATUS_SEND_AFTER_IST (normally the 18:37 IST run) mails a summary of the
whole day to STATUS_RECIPIENTS. If it doesn't arrive, the evening run didn't
happen.

Deliberately a separate, short recipient list: an "all clear" mail to the
whole alert list three times a day would train people to ignore the sender,
real alerts included.
"""

import datetime as dt
import logging

from .config import Settings
from .state import StateStore

log = logging.getLogger(__name__)

# India has no daylight saving, so a fixed offset is exact -- and avoids
# needing tzdata, which Windows Python doesn't ship.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def record_run(
    state: StateStore,
    started: dt.datetime,
    checked_ok: int,
    total: int,
    failed: list[str],
    new: list[str],
) -> None:
    """Appends this run to today's log, starting a fresh log on a new day."""
    day = state.daily_log()
    today = started.date().isoformat()
    if day.get("date") != today:
        day.clear()
        day.update({"date": today, "runs": [], "sent": False})
    day["runs"].append(
        {"time": started.strftime("%H:%M"), "ok": checked_ok, "total": total, "failed": failed, "new": new}
    )


def is_due(state: StateStore, settings: Settings, started: dt.datetime) -> bool:
    if not settings.status_recipients:
        return False
    day = state.daily_log()
    if day.get("date") != started.date().isoformat() or day.get("sent"):
        return False
    return started.time() >= _parse_hhmm(settings.status_send_after)


def mark_sent(state: StateStore) -> None:
    state.daily_log()["sent"] = True


def build_email(
    state: StateStore, settings: Settings, run_url: str = "", stale: list[str] | None = None
) -> tuple[str, str]:
    stale = stale or []
    day = state.daily_log()
    runs = day.get("runs", [])
    date = dt.date.fromisoformat(day["date"])
    expected = settings.expected_runs_per_day
    any_failed = any(r["failed"] for r in runs)
    latest_failed = runs[-1]["failed"] if runs else []
    # A failure that recovered by the last run is the same one-off noise the
    # 2-in-a-row failure email ignores, so it doesn't spoil "all clear" -- it
    # is still listed in the body.
    all_clear = len(runs) >= expected and not latest_failed and not stale
    new_today = [n for r in runs for n in r["new"]]

    verdict = "all clear" if all_clear else "check needed"
    subject = f"[Financial Whistler] Daily status {date:%a %d %b}: {verdict}"
    if new_today:
        subject += f" ({len(new_today)} new result{'s' if len(new_today) != 1 else ''} alerted)"

    lines = [f"Daily status for {date:%A %d %B %Y} (times in IST)", ""]

    lines.append(f"Runs today: {len(runs)} of {expected} expected")
    if len(runs) < expected:
        lines.append("  ! Fewer runs than expected - check the cron-job.org job's history.")
    for r in runs:
        line = f"  {r['time']}  {r['ok']}/{r['total']} companies checked"
        if r["failed"]:
            line += f" - {len(r['failed'])} failed: {', '.join(r['failed'])}"
        line += f", {len(r['new'])} new"
        lines.append(line)
    lines.append("")

    lines.append("New financial results alerted today:")
    lines.extend(_bullets(new_today))
    lines.append("")

    lines.append("Companies that failed on the latest run:")
    lines.extend(_bullets(latest_failed))
    if any_failed and not latest_failed:
        lines.append("  (earlier failures today recovered on a later run)")
    lines.append("")

    if stale:
        lines.append(f"Companies with nothing new in over {settings.stale_after_days} days:")
        lines.extend(_bullets(stale))
        lines.append(
            "  Listed companies publish results every quarter, so this usually means the "
            "exchange changed where it publishes them and the scraper is reading a feed "
            "that no longer updates (as NSE did in 2025). Check the company's page on the "
            "exchange's website."
        )
        lines.append("")

    lines.append(
        "This email is sent once a day by the first run after "
        f"{settings.status_send_after} IST. If it stops arriving, the scheduled runs "
        "aren't happening: check the GitHub Actions tab and the cron-job.org job "
        "(its GitHub token may have expired)."
    )
    if run_url:
        lines.append("")
        lines.append(f"Latest run log: {run_url}")
    return subject, "\n".join(lines)


def _bullets(items: list[str]) -> list[str]:
    return [f"  - {item}" for item in items] if items else ["  none"]


def _parse_hhmm(value: str) -> dt.time:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return dt.time(hour, minute)
    except ValueError:
        log.warning("STATUS_SEND_AFTER_IST=%r is not HH:MM - using 18:00", value)
        return dt.time(18, 0)
