import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import Settings
from ..models import Announcement, Company
from .base import Alerter

log = logging.getLogger(__name__)


def github_run_url() -> str:
    """Link to the current GitHub Actions run, or "" when running elsewhere."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


class EmailAlerter(Alerter):
    def __init__(self, settings: Settings):
        self.settings = settings

    def send_digest(self, announcements: list[Announcement]) -> None:
        if not announcements:
            return
        s = self.settings
        if not s.smtp_host or not s.alert_recipients or not s.smtp_user or not s.smtp_password:
            log.error(
                "Email not fully configured (need SMTP_HOST, SMTP_USER, SMTP_PASSWORD and "
                "ALERT_RECIPIENTS all set in .env) - %d new financial-result announcement(s) "
                "were found but NOT sent:",
                len(announcements),
            )
            for a in announcements:
                log.error("  - %s (%s): %s", a.company.name, a.company.ticker, a.title)
            return

        self._send(self._subject(announcements), self._body_text(announcements))
        log.info("Sent alert email for %d announcement(s) to %s", len(announcements), s.alert_recipients)

    def send_failure_report(self, failed: list[tuple[str, Company]], total: int) -> None:
        """Emails the companies that have just failed their second check in a
        row, so a site starting to block us (or changing its layout) doesn't
        go unnoticed -- a failed fetch is otherwise just a log line, and the
        run as a whole still exits successfully."""
        if not failed:
            return
        s = self.settings
        if not s.smtp_host or not s.alert_recipients or not s.smtp_user or not s.smtp_password:
            log.error("Email not fully configured - failure report for %d company(ies) NOT sent", len(failed))
            return

        subject = f"[Financial Whistler] {len(failed)} of {total} companies failed 2 checks in a row"
        lines = [
            f"{len(failed)} of {total} companies could not be checked on the last 2 runs in a row, "
            "so any new financial results from them are NOT being detected. Checks keep retrying "
            "every run; you won't be emailed again about these companies unless they recover "
            "and then fail twice in a row again.\n",
        ]
        by_exchange: dict[str, list[Company]] = {}
        for exchange, company in failed:
            by_exchange.setdefault(exchange, []).append(company)
        for exchange, companies in by_exchange.items():
            lines.append(f"{exchange}:")
            for c in companies:
                lines.append(f"  - {c.name} ({c.ticker})")
            lines.append("")
        run_url = github_run_url()
        if run_url:
            lines.append(f"Run log: {run_url}")
        self._send(subject, "\n".join(lines))
        log.info("Sent failure report for %d company(ies) to %s", len(failed), s.alert_recipients)

    def send_daily_status(self, subject: str, body: str) -> None:
        """Goes to STATUS_RECIPIENTS only -- a short list of whoever looks
        after the tool -- never to the full alert list."""
        s = self.settings
        if not s.smtp_host or not s.status_recipients or not s.smtp_user or not s.smtp_password:
            log.error("Email not fully configured - daily status NOT sent")
            return
        self._send(subject, body, s.status_recipients)
        log.info("Sent daily status to %s", s.status_recipients)

    def _send(self, subject: str, body: str, recipients: list[str] | None = None) -> None:
        s = self.settings
        recipients = recipients or s.alert_recipients
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = s.from_email
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as server:
            if s.smtp_use_tls:
                server.starttls()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.sendmail(s.from_email, recipients, msg.as_string())

    @staticmethod
    def _subject(announcements: list[Announcement]) -> str:
        if len(announcements) == 1:
            a = announcements[0]
            return f"[Financial Whistler] {a.company.name} ({a.company.ticker}): new financial result"
        return f"[Financial Whistler] {len(announcements)} new financial results"

    @staticmethod
    def _body_text(announcements: list[Announcement]) -> str:
        lines = ["New financial-result disclosures detected:\n"]
        for a in announcements:
            lines.append(f"- {a.company.name} ({a.company.ticker})")
            lines.append(f"  {a.title}")
            lines.append(f"  Published: {a.date_text}")
            lines.append(f"  {a.detail_url}")
            lines.append("")
        return "\n".join(lines)
