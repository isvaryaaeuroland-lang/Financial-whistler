import logging
import random
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from .. import chrome_profile
from ..models import Announcement, Company
from .base import ExchangeScraper

log = logging.getLogger(__name__)

# Request shape found live 2026-08-26 by reading msx.om's own site.js: the
# "FINANCIALS REPORTS" tab on a company snapshot page calls
# POST /snapshot.aspx/SnapFinancial with a JSON-as-string body
# "{ 'Symbol': '<ticker>' }" -- note the capital "Symbol" key, which matters
# (a lowercase "symbol" silently falls through to the page's own HTML
# instead of the JSON API, which looks confusingly like a bot-block at
# first glance but isn't one). No headless block: verified live,
# headless=True and headless=False both return 200 with real JSON.
PROFILE_DIR = chrome_profile.profile_base() / "oman"

MIN_DELAY_S = 1.5
MAX_DELAY_S = 3.5

WARMUP_URL = "https://www.msx.om/"
DOWNLOAD_BASE = "https://www.msx.om/MSMDOCS/FinancialReports/"

_FETCH_JS = """
(symbol) => fetch('/snapshot.aspx/SnapFinancial', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json; charset=utf-8' },
  body: "{ 'Symbol': '" + symbol + "' }",
}).then(r => {
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
})
"""


class OmanScraper(ExchangeScraper):
    exchange_name = "Muscat Stock Exchange (MSX)"

    def __init__(self, timeout_ms: int = 30000, retries: int = 2):
        self.timeout_ms = timeout_ms
        self.retries = retries

    def run(self, companies: list[Company]) -> dict[str, list[Announcement] | None]:
        results: dict[str, list[Announcement] | None] = {}
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="chrome",
                headless=True,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            try:
                page = context.new_page()
                page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=self.timeout_ms)
                for i, company in enumerate(companies):
                    if i > 0:
                        time.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))
                    results[company.ticker] = self._scrape_one(page, company)
            finally:
                context.close()
        return results

    def _scrape_one(self, page, company: Company) -> list[Announcement] | None:
        for attempt in range(1, self.retries + 2):
            try:
                data = page.evaluate(_FETCH_JS, company.ticker)
                items = data.get("d", [])
                return self._parse_releases(company, items)
            except Exception as exc:  # noqa: BLE001 - isolate failures per company
                log.warning(
                    "%s (%s): scrape attempt %d/%d failed: %s",
                    company.name,
                    company.ticker,
                    attempt,
                    self.retries + 1,
                    exc,
                )
        log.error("%s (%s): giving up after %d attempts", company.name, company.ticker, self.retries + 1)
        return None

    @staticmethod
    def _parse_releases(company: Company, items: list[dict]) -> list[Announcement]:
        out = []
        for item in items:
            report_id = item.get("ReportID")
            if not report_id:
                continue
            period = item.get("NameEn") or "?"
            year = item.get("ReportYear") or "?"
            title = item.get("TitleEn") or f"{period} {year} Financial Statements"
            filename = item.get("FileNameEn") or ""
            detail_url = DOWNLOAD_BASE + quote(filename) if filename else ""
            out.append(
                Announcement(
                    id=str(report_id),
                    company=company,
                    title=title,
                    date_text=item.get("UploadDate") or "",
                    detail_url=detail_url,
                )
            )
        return out
