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

# Request shape reverse-engineered from a HAR file the user captured from
# their own browser session (2026-08-26). Unlike Tadawul and NSE, DFM does
# NOT block headless Chromium -- verified live: headless=True and
# headless=False both return 200 for both the page load and the API call,
# with real data (6 filings for ALEC, matching the HAR exactly). Simplest
# of the four exchanges implemented so far: the financial-reports API takes
# a plain ticker symbol directly (no fuzzy name search/resolution needed
# like NSE), and doesn't require navigating to the company's own profile
# page at all -- DFM's company-profile pages have a client-side routing bug
# that breaks direct deep-links (confirmed independently via both this
# sandbox's browser and the user's real Chrome, reproducing "Unable to load
# content" for a well-known, long-listed company), but the API itself is
# unaffected by that bug since it's called directly, bypassing that broken
# page entirely.
PROFILE_DIR = chrome_profile.profile_base() / "dfm"

MIN_DELAY_S = 1.5
MAX_DELAY_S = 3.5

WARMUP_URL = "https://www.dfm.ae/"
API_URL_TMPL = (
    "https://api2.dfm.ae/efsah/v1/prototype_efsah?types=financial_reports"
    "&announcement_type=Disclosure&symbol={symbol}&take=50&lang=en&cms_resources=true"
)
PDF_BASE = "https://feeds.dfm.ae/documents"

_FETCH_JSON_JS = """
(url) => fetch(url, { headers: { accept: 'application/json' } }).then(r => {
  if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
  return r.json();
})
"""


class DfmScraper(ExchangeScraper):
    exchange_name = "Dubai Financial Market (DFM)"

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
                url = API_URL_TMPL.format(symbol=quote(company.ticker))
                data = page.evaluate(_FETCH_JSON_JS, url)
                return self._parse_releases(company, data.get("root", []))
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
            item_id = item.get("id")
            headline = item.get("headline")
            if not item_id or not headline:
                continue
            resources = item.get("resources") or []
            detail_url = PDF_BASE + resources[0]["r_path"] if resources and resources[0].get("r_path") else ""
            out.append(
                Announcement(
                    id=item_id,
                    company=company,
                    title=headline,
                    date_text=item.get("publication_date") or "",
                    detail_url=detail_url,
                )
            )
        return out
