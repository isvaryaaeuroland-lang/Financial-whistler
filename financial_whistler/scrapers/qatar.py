import logging
import random
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from .. import chrome_profile
from ..models import Announcement, Company
from .base import ExchangeScraper

log = logging.getLogger(__name__)

# Unlike Tadawul, QSE (qe.com.qa) does NOT block headless Chromium -- verified
# live 2026-08-25: headless=True returned 200 and found real filing links.
# No off-screen-window / non-headless workaround needed here, and (unlike
# Tadawul) this scraper doesn't require an active logged-in GUI session.
PROFILE_DIR = chrome_profile.profile_base() / "qatar"

MIN_DELAY_S = 2.0
MAX_DELAY_S = 4.5

# The company profile page has a "Financials" tab (#tab-financials) whose
# "Detailed report" table (#DetailedreportData) is a single, simple grid:
# one header row (blank label cell + years 2026..2022), then one row per
# period (Q1-Q4). Each populated cell has <a class="link"
# href=".../qdisclosure/api/XBRL/GetFSAttachmentAPI?symCode=X&reportEndDate=
# YYYY-MM-DD&..." aria-label="{year} {period} Pdf">. Verified live against
# GISS (Gulf International Services) 2026-08-25.
_EXTRACT_JS = """
() => {
  const table = document.querySelector('#DetailedreportData');
  if (!table) return { error: 'DetailedreportData table not found' };
  const theadRow = table.querySelector('thead tr');
  if (!theadRow) return { error: 'no thead row' };
  const yearHeaders = Array.from(theadRow.querySelectorAll('th, td')).map(c => c.textContent.trim());

  const rows = Array.from(table.querySelectorAll('tbody tr'));
  const out = [];
  for (const tr of rows) {
    const tds = Array.from(tr.querySelectorAll('td'));
    if (!tds.length) continue;
    const period = tds[0].textContent.trim();
    for (let i = 1; i < tds.length; i++) {
      const a = tds[i].querySelector('a[href*="GetFSAttachmentAPI"]');
      if (!a) continue;
      out.push({
        period,
        year: yearHeaders[i] || null,
        href: a.getAttribute('href'),
        label: a.getAttribute('aria-label') || '',
      });
    }
  }
  return { releases: out };
}
"""


class QatarScraper(ExchangeScraper):
    exchange_name = "Qatar Stock Exchange (QSE)"

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
                for i, company in enumerate(companies):
                    if i > 0:
                        time.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))
                    results[company.ticker] = self._scrape_one(context, company)
            finally:
                context.close()
        return results

    def _scrape_one(self, context, company: Company) -> list[Announcement] | None:
        for attempt in range(1, self.retries + 2):
            page = context.new_page()
            try:
                resp = page.goto(
                    company.profile_url, wait_until="domcontentloaded", timeout=self.timeout_ms
                )
                if resp is not None and resp.status >= 400:
                    log.error(
                        "%s (%s): HTTP %d on attempt %d/%d",
                        company.name,
                        company.ticker,
                        resp.status,
                        attempt,
                        self.retries + 1,
                    )
                    continue

                page.click("#tab-financials", timeout=self.timeout_ms)
                page.wait_for_selector('a[href*="GetFSAttachmentAPI"]', timeout=self.timeout_ms)

                result = page.evaluate(_EXTRACT_JS)
                if result.get("error"):
                    raise RuntimeError(result["error"])
                return self._parse_releases(company, result.get("releases", []))
            except Exception as exc:  # noqa: BLE001 - isolate failures per company
                log.warning(
                    "%s (%s): scrape attempt %d/%d failed: %s",
                    company.name,
                    company.ticker,
                    attempt,
                    self.retries + 1,
                    exc,
                )
            finally:
                page.close()
        log.error("%s (%s): giving up after %d attempts", company.name, company.ticker, self.retries + 1)
        return None

    @staticmethod
    def _parse_releases(company: Company, releases: list[dict]) -> list[Announcement]:
        out = []
        for item in releases:
            href = item.get("href") or ""
            if not href:
                continue
            period = item.get("period") or "?"
            year = item.get("year") or "?"
            out.append(
                Announcement(
                    id=href,  # the full query string (symCode+reportEndDate+attachmentType) is unique per filing
                    company=company,
                    title=f"{period} {year} Financial Statements",
                    date_text=item.get("label") or "",
                    detail_url=href,
                )
            )
        return out
