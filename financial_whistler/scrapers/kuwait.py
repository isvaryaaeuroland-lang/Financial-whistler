import logging
import random
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from .. import chrome_profile
from ..models import Announcement, Company
from .base import ExchangeScraper

log = logging.getLogger(__name__)

# Boursa Kuwait blocks headless Chromium -- verified live 2026-08-26:
# headless=True got 403 on both the page load and the API call; headless=
# False got 200 with real JSON, everything else identical. Same off-screen
# window approach as Tadawul/NSE.
PROFILE_DIR = chrome_profile.profile_base() / "kuwait"

MIN_DELAY_S = 2.0
MAX_DELAY_S = 4.5

WARMUP_URL = "https://www.boursakuwait.com.kw/en/"

# Found live 2026-08-26 via network inspection of the "Financial Statement"
# tab on a company's stock page (which uses an opaque internal numeric ID in
# the URL fragment, e.g. #654 for Jazeera Airways -- resolved from the
# public listed-companies directory's <a href=".../stock/profile#654">
# links, not from the ticker symbol). RT=3502 is specifically the Financial
# Statement history; other RT codes seen alongside it (3517, 3558) serve
# other tabs (Corporate Profit, Disclosures) not needed here.
API_URL_TMPL = "https://www.boursakuwait.com.kw/data-api/client-services?RT=3502&SYMC={company_id}&L=E"

# "period" is a numeric code with no label in the API response itself;
# decoded by matching each code's activatedDate month against the site's
# own rendered "First Quarter / Half Yearly / Third Quarter / Yearly"
# columns for the same rows (e.g. period=11 filed in August matches the
# "Half Yearly" column's August filing date for the same year).
PERIOD_LABELS = {
    "12": "First Quarter",
    "11": "Half Yearly",
    "10": "Third Quarter",
    "9": "Yearly",
}

_FETCH_JSON_JS = """
(url) => fetch(url, { headers: { accept: 'application/json' } }).then(r => {
  if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
  return r.json();
})
"""


class KuwaitScraper(ExchangeScraper):
    exchange_name = "Boursa Kuwait"

    def __init__(self, timeout_ms: int = 30000, retries: int = 2):
        self.timeout_ms = timeout_ms
        self.retries = retries

    def run(self, companies: list[Company]) -> dict[str, list[Announcement] | None]:
        results: dict[str, list[Announcement] | None] = {}
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        chrome_profile.clear_stale_lock(PROFILE_DIR)
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="chrome",
                headless=False,
                args=["--window-position=-2400,-2400", "--window-size=1280,900"],
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            try:
                page = context.new_page()
                chrome_profile.hide_window(context, page)
                page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=self.timeout_ms)
                for i, company in enumerate(companies):
                    if i > 0:
                        time.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))
                    results[company.ticker] = self._scrape_one(page, company)
            finally:
                context.close()
        return results

    def _scrape_one(self, page, company: Company) -> list[Announcement] | None:
        # Boursa Kuwait's API keys on an opaque internal numeric company ID
        # (e.g. "654" for Jazeera Airways), not the trading symbol -- there's
        # no direct symbol-to-API mapping found. That ID lives in the
        # profile_url's fragment (.../financial-statement#654), resolved
        # from the public listed-companies directory's own profile links,
        # so `ticker` can stay the human-readable trading symbol for the
        # spreadsheet while the scraper reads the ID from the URL.
        company_id = urlparse(company.profile_url).fragment
        if not company_id:
            log.error(
                "%s (%s): profile_url has no #<id> fragment (Boursa Kuwait "
                "needs the internal numeric company ID there, e.g. .../financial-statement#654)",
                company.name,
                company.ticker,
            )
            return None
        for attempt in range(1, self.retries + 2):
            try:
                url = API_URL_TMPL.format(company_id=company_id)
                data = page.evaluate(_FETCH_JSON_JS, url)
                items = data.get("dataFields", [])
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
            row_id = item.get("rowId")
            if not row_id:
                continue
            period = PERIOD_LABELS.get(str(item.get("period")), f"Period {item.get('period')}")
            year = item.get("year") or "?"
            files = item.get("fileNames") or []
            pdf = next((f["fileName"] for f in files if f.get("type") == "PDF"), None)
            detail_url = pdf or (files[0]["fileName"] if files else "")
            raw_date = str(item.get("activatedDate") or "")
            date_text = (
                f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else raw_date
            )
            out.append(
                Announcement(
                    id=str(row_id),
                    company=company,
                    title=f"{period} {year} Financial Statement",
                    date_text=date_text,
                    detail_url=detail_url,
                )
            )
        return out
