import json
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

# Request shapes reverse-engineered from a HAR file the user captured from
# their own browser session (nseindia.com is blocked by safety policy in
# every browser tool available here, so this couldn't be researched
# directly). Verified live 2026-08-25 against RATEGAIN: 26 filings back to
# 2022, matching the HAR exactly. Like Tadawul, NSE blocks headless
# Chromium specifically -- confirmed by testing headless=True (immediate
# net::ERR_HTTP2_PROTOCOL_ERROR on the very first navigation) vs
# headless=False (works) with everything else identical. Not yet verified:
# whether smart-search-by-name reliably resolves the other 4 companies the
# same way it did for Rategain (their HAR entries weren't captured).

PROFILE_DIR = chrome_profile.profile_base() / "india_nse"

MIN_DELAY_S = 3.0
MAX_DELAY_S = 6.0

WARMUP_URL = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
SEARCH_PATH = "/api/smart-search/eqEtf"
RESULTS_PATH = "/api/corporates-financial-results"

_FETCH_JSON_JS = """
(url) => fetch(url, { headers: { accept: '*/*' } }).then(r => {
  if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
  return r.json();
})
"""


def _expect_list(data, endpoint: str) -> list[dict]:
    """Both NSE endpoints normally return a JSON list of objects. On
    2026-10-10 (Saturday evening, ~18:30-19:05 IST) every NSE request instead
    came back HTTP 200 with something else, which surfaced only as a
    confusing "'str' object has no attribute 'get'" further down. Fail here
    instead, with the start of what NSE actually sent, so the log shows
    whether it was a maintenance notice, a rate-limit message, or a real
    format change."""
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return data
    snippet = json.dumps(data, ensure_ascii=False, default=str)
    if len(snippet) > 300:
        snippet = snippet[:300] + "..."
    raise RuntimeError(f"unexpected {endpoint} response ({type(data).__name__}): {snippet}")


class NseIndiaScraper(ExchangeScraper):
    exchange_name = "National Stock Exchange of India (NSE)"

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
                # Warm-up: land on the actual page the API calls are meant to
                # be made from (matches the "referer" header seen in the HAR)
                # before firing any API requests from within its context.
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
                # Step 1: resolve the exact NSE-registered company name (the
                # financial-results endpoint requires an exact "issuer"
                # match). Searching by our own stored name -- same as a user
                # typing into the search box -- rather than hardcoding legal
                # names we'd have to guess and keep in sync ourselves.
                search_url = f"https://www.nseindia.com{SEARCH_PATH}?q={quote(company.name)}"
                matches = _expect_list(page.evaluate(_FETCH_JSON_JS, search_url), "smart-search")
                match = self._pick_match(matches, company)
                if not match:
                    raise RuntimeError(f"no smart-search match for {company.name!r} (got {matches})")

                # Step 2: fetch the actual financial-results filings for that
                # resolved company.
                resolved_symbol = quote(match["symbol"])
                resolved_name = quote(match["companyName"])
                results_url = (
                    f"https://www.nseindia.com{RESULTS_PATH}?index=equities"
                    f"&symbol={resolved_symbol}&issuer={resolved_name}&period=Quarterly"
                )
                filings = _expect_list(page.evaluate(_FETCH_JSON_JS, results_url), "financial-results")
                return self._parse_releases(company, filings)
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
    def _pick_match(matches: list[dict], company: Company) -> dict | None:
        if not matches:
            return None
        # Prefer an exact symbol match (our stored ticker) if present, since
        # a name search can return multiple similarly-named companies.
        for m in matches:
            if m.get("symbol", "").upper() == company.ticker.upper():
                return m
        return matches[0]

    @staticmethod
    def _parse_releases(company: Company, filings: list[dict]) -> list[Announcement]:
        out = []
        for item in filings:
            seq = item.get("seqNumber")
            if not seq:
                continue
            relating_to = item.get("relatingTo") or "?"
            fy = item.get("financialYear") or ""
            consolidated = item.get("consolidated") or ""
            title = f"{relating_to} ({fy}) - {consolidated} Financial Results".strip()
            out.append(
                Announcement(
                    id=str(seq),
                    company=company,
                    title=title,
                    date_text=item.get("broadCastDate") or "",
                    detail_url=item.get("xbrl") or "",
                )
            )
        return out

