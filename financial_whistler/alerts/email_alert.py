import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import Settings
from ..models import Announcement
from .base import Alerter

log = logging.getLogger(__name__)


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

        subject = self._subject(announcements)
        body = self._body_text(announcements)

        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = s.from_email
        msg["To"] = ", ".join(s.alert_recipients)
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as server:
            if s.smtp_use_tls:
                server.starttls()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.sendmail(s.from_email, s.alert_recipients, msg.as_string())

        log.info("Sent alert email for %d announcement(s) to %s", len(announcements), s.alert_recipients)

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
