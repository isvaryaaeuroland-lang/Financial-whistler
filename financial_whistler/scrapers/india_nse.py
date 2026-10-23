import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from .. import chrome_profile
from ..daily_status import IST
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

# Since 2025 listed companies file quarterly results through NSE's
# "Integrated Filing - Financials" system, and the legacy RESULTS_PATH feed
# above simply stopped growing: found 2026-10-23, when IHCL's Q2 FY27 result
# (filed 21-Oct-2026) never alerted -- its legacy feed still ended at the
# Q3 FY25 filing of 17-Jan-2025, so none of the NSE companies had produced a
# new result in ~21 months. This feed is the one that matters now; it accepts
# a per-company `symbol` filter and returns {"data": [...], "totalCount": N},
# newest first. The legacy feed is still read too, in case NSE uses it again.
INTEGRATED_PATH = "/api/integrated-filing-results"
INTEGRATED_TYPE = "Integrated Filing- Financials"
INTEGRATED_SOURCE = "integrated"  # Announcement.source for this feed's items
INTEGRATED_PAGE_SIZE = 100

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
    raise RuntimeError(f"unexpected {endpoint} response ({type(data).__name__}): {_snippet(data)}")


def _expect_page(data, endpoint: str) -> list[dict]:
    """The integrated-filing feed returns {"data": [...], "totalCount": N}."""
    rows = data.get("data") if isinstance(data, dict) else None
    if isinstance(rows, list) and all(isinstance(item, dict) for item in rows):
        return rows
    raise RuntimeError(f"unexpected {endpoint} response ({type(data).__name__}): {_snippet(data)}")


def _snippet(data) -> str:
    snippet = json.dumps(data, ensure_ascii=False, default=str)
    return snippet[:300] + "..." if len(snippet) > 300 else snippet


def _parse_nse_datetime(value: str | None) -> datetime | None:
    """NSE timestamps look like "21-Oct-2026 18:26:44" and are IST."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None


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

                resolved_symbol = quote(match["symbol"])
                resolved_name = quote(match["companyName"])

                # Step 2: the Integrated Filing feed, where results are
                # actually filed now. A failure here fails the company.
                integrated_url = (
                    f"https://www.nseindia.com{INTEGRATED_PATH}?index=equities&symbol={resolved_symbol}"
                    f"&type={quote(INTEGRATED_TYPE)}&page=1&size={INTEGRATED_PAGE_SIZE}"
                )
                integrated = _expect_page(page.evaluate(_FETCH_JSON_JS, integrated_url), "integrated-filing")
                if len(integrated) >= INTEGRATED_PAGE_SIZE:
                    # Newest first, so the latest filings are still covered.
                    log.warning(
                        "%s (%s): integrated-filing returned a full page of %d - older filings not fetched",
                        company.name,
                        company.ticker,
                        len(integrated),
                    )

                # Step 3: the legacy feed (frozen since Jan 2025). Optional --
                # if it errors, the integrated results above still count.
                legacy: list[dict] = []
                results_url = (
                    f"https://www.nseindia.com{RESULTS_PATH}?index=equities"
                    f"&symbol={resolved_symbol}&issuer={resolved_name}&period=Quarterly"
                )
                try:
                    legacy = _expect_list(page.evaluate(_FETCH_JSON_JS, results_url), "financial-results")
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s (%s): legacy financial-results feed failed, ignoring: %s",
                                company.name, company.ticker, exc)

                return self._parse_integrated(company, integrated) + self._parse_releases(company, legacy)
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
    def _parse_integrated(company: Company, rows: list[dict]) -> list[Announcement]:
        out = []
        for item in rows:
            seq = item.get("seq_Id")
            if not seq or (item.get("type") and item["type"] != INTEGRATED_TYPE):
                continue
            quarter = item.get("qe_Date") or "?"
            try:
                quarter = datetime.strptime(quarter, "%d-%b-%Y").strftime("%d %b %Y")
            except ValueError:
                pass
            kind = ", ".join(x for x in (item.get("consolidated"), item.get("audited")) if x)
            title = f"Results for quarter ended {quarter}" + (f" - {kind}" if kind else "")
            if item.get("type_Sub") and item["type_Sub"] != "Original":
                title += f" ({item['type_Sub']})"
            pdf = item.get("pdf_attach") or ""
            link = item.get("ixbrl") or item.get("xbrl") or ("" if pdf.endswith("/null") else pdf)
            out.append(
                Announcement(
                    # "if-" keeps these clear of the legacy feed's seqNumbers,
                    # which are a different numbering.
                    id=f"if-{seq}",
                    company=company,
                    title=title,
                    date_text=item.get("broadcast_Date") or "",
                    detail_url=link,
                    source=INTEGRATED_SOURCE,
                    published=_parse_nse_datetime(item.get("broadcast_Date")),
                )
            )
        return out

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
                    published=_parse_nse_datetime(item.get("broadCastDate")),
                )
            )
        return out

