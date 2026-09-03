"""Search-by-name company lookup for Dubai Financial Market (DFM).

Fetches the full DFM securities list once (cached in-process, refreshed
daily) from api2.dfm.ae's widget-data endpoint -- found by reading the HAR
capture of dfm.ae's own company-profile widget, which calls it with
Command=LiteSecuritiesLists to populate its own search box -- then matches
locally against the cached list.

DFM does not block headless Chromium (verified live in scrapers/dfm.py), so
this uses a plain headless fetch.

The endpoint also returns Nasdaq Dubai dual-listings and DFM rights-issue
subscriptions, neither of which is the underlying tradeable DFM company, so
both are excluded. Rights issues are matched by "Rights Issue" appearing in
FullName rather than by ticker shape -- verified live 2026-08-26 against
every rights-issue row in the directory (16 of them, spanning tickers like
"DIB-RI", "DIB-RI18", and "DUBAIPARKSRI" with no hyphen at all) -- FullName
was the one field consistent across all of them.
"""

import logging
import time

from playwright.sync_api import sync_playwright

from . import lookup_cache

log = logging.getLogger(__name__)

CACHE_NAME = "dfm"

WARMUP_URL = "https://www.dfm.ae/"
PROFILE_URL_TMPL = "https://www.dfm.ae/the-exchange/market-information/company/{ticker}/reports/financial-reports"

_FETCH_JS = """
() => fetch('https://api2.dfm.ae/web/widgets/v1/data', {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json' },
  body: 'Command=LiteSecuritiesLists&Language=en&lang=en&securitytype=equities',
}).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
"""

_CACHE_TTL_SECONDS = 24 * 3600
_cache: dict = {"entries": None, "fetched_at": 0.0}


def _fetch_directory() -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=45000)
            data = page.evaluate(_FETCH_JS)
        finally:
            browser.close()
    return [
        d
        for d in data
        if d.get("Exchange") == "DFM" and "rights issue" not in (d.get("FullName") or "").lower()
    ]


def get_directory(force_refresh: bool = False) -> list[dict]:
    if _cache["entries"] is None:
        disk = lookup_cache.load(CACHE_NAME)
        if disk:
            _cache["entries"] = disk.get("entries")
            _cache["fetched_at"] = disk.get("fetched_at", 0.0)
            log.info("Loaded %d DFM companies from disk cache", len(_cache["entries"]))

    now = time.time()
    stale = (now - _cache["fetched_at"]) > _CACHE_TTL_SECONDS
    if force_refresh or _cache["entries"] is None or stale:
        try:
            log.info("Fetching DFM company directory...")
            fresh = _fetch_directory()
            _cache["entries"] = fresh
            _cache["fetched_at"] = now
            lookup_cache.save(CACHE_NAME, {"fetched_at": now, "entries": fresh})
            log.info("Cached %d listed companies (DFM)", len(fresh))
        except Exception:
            if _cache["entries"] is None:
                raise
            log.warning(
                "DFM directory refresh failed -- serving %d cached companies from disk instead",
                len(_cache["entries"]),
                exc_info=True,
            )
    return _cache["entries"]


def search_companies(query: str, limit: int = 8) -> list[dict]:
    query = (query or "").strip().lower()
    if len(query) < 2:
        return []

    directory = get_directory()
    starts, contains = [], []
    for entry in directory:
        name = (entry.get("FullName") or "").lower()
        symbol = (entry.get("SecuritySymbol") or "").lower()
        if name.startswith(query) or symbol.startswith(query):
            starts.append(entry)
        elif query in name:
            contains.append(entry)

    seen = set()
    out = []
    for e in starts + contains:
        symbol = e.get("SecuritySymbol")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(
            {
                "name": e.get("FullName"),
                "ticker": symbol,
                "market_label": "UAE (DFM)",
                "profile_url": PROFILE_URL_TMPL.format(ticker=symbol),
            }
        )
        if len(out) >= limit:
            break
    return out
