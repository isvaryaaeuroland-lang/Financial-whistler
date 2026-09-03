"""Look up a Tadawul-listed company by name to auto-fill ticker + profile URL.

Fetches the full Tadawul company directory once (cached in-process, refreshed
daily) from ThemeSearchUtilityServlet -- the same endpoint that backs the
site's own search box -- rather than hitting a live search endpoint per
query. Matching is done locally against the cached list.

Uses the same real, non-headless, persistent-profile Chrome approach as the
announcement scraper (see scrapers/tadawul.py) since this endpoint sits
behind the same Akamai bot protection.
"""

import logging
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import chrome_profile, lookup_cache

log = logging.getLogger(__name__)

CACHE_NAME = "ksa"

# Guards against two directory refreshes racing on the same Chrome profile
# within this process -- see lookup_india.py's docstring for why that
# matters (Chrome's singleton handling doesn't queue a second launch
# gracefully, it just fails the second one confusingly).
_launch_lock = threading.Lock()

# Deliberately outside the project's OneDrive-synced folder -- see
# scrapers/tadawul.py's PROFILE_DIR comment for why a live Chrome profile
# inside a cloud-sync folder causes severe intermittent hangs.
PROFILE_DIR = chrome_profile.profile_base() / "lookup"

WARMUP_URL = "https://www.saudiexchange.sa/wps/portal/saudiexchange/ourmarkets/main-market-watch"
SEARCH_ENDPOINT_PATH = "/tadawul.eportal.theme.helper/ThemeSearchUtilityServlet"

# market_type values observed in the directory: M=Main Market, S=Nomu
# Parallel Market, plus B/C/D/E/F/O for bonds/derivatives/ETFs/futures/
# options -- none of which are companies that publish financial results, so
# only M and S are kept.
PROFILE_URL_TEMPLATES = {
    "M": (
        "https://www.saudiexchange.sa/wps/portal/saudiexchange/hidden/company-profile-main/"
        "!ut/p/z1/04_Sj9CPykssy0xPLMnMz0vMAfIjo8ziTR3NDIw8LAz83d2MXA0C3SydAl1c3Q0NvE30I4EKzBEKDMKcTQzMDPxN3H19"
        "LAzdTU31w8syU8v1wwkpK8hOMgUA-oskdg!!/?companySymbol={ticker}"
    ),
    "S": (
        "https://www.saudiexchange.sa/wps/portal/saudiexchange/hidden/company-profile-nomu-parallel/"
        "!ut/p/z1/04_Sj9CPykssy0xPLMnMz0vMAfIjo8ziTR3NDIw8LAz8_R0tzQ0C3byc3D19HI1dA030I4EKzBEKDMKcTQzMDPxN3H19"
        "LAzdTUz1wwkpKMhO8wQA9KBkIg!!/?companySymbol={ticker}"
    ),
}
MARKET_TYPE_LABEL = {"M": "Main Market", "S": "Nomu Parallel Market"}

_CACHE_TTL_SECONDS = 24 * 3600
_cache: dict = {"entries": None, "fetched_at": 0.0}


def _fetch_directory() -> list[dict]:
    with _launch_lock:
        return _fetch_directory_locked()


def _fetch_directory_locked() -> list[dict]:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome_profile.clear_stale_lock(PROFILE_DIR)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            args=["--window-position=-2400,-2400", "--window-size=1280,900"],
        )
        try:
            page = context.new_page()
            chrome_profile.hide_window(context, page)
            page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=45000)
            data = page.evaluate(
                "(path) => fetch(path).then(r => r.json())", SEARCH_ENDPOINT_PATH
            )
        finally:
            context.close()
    return [d for d in data if d.get("market_type") in PROFILE_URL_TEMPLATES]


def get_directory(force_refresh: bool = False) -> list[dict]:
    if _cache["entries"] is None:
        disk = lookup_cache.load(CACHE_NAME)
        if disk:
            _cache["entries"] = disk.get("entries")
            _cache["fetched_at"] = disk.get("fetched_at", 0.0)
            log.info("Loaded %d Tadawul companies from disk cache", len(_cache["entries"]))

    now = time.time()
    stale = (now - _cache["fetched_at"]) > _CACHE_TTL_SECONDS
    if force_refresh or _cache["entries"] is None or stale:
        try:
            log.info("Fetching Tadawul company directory (real browser, ~10-20s)...")
            fresh = _fetch_directory()
            _cache["entries"] = fresh
            _cache["fetched_at"] = now
            lookup_cache.save(CACHE_NAME, {"fetched_at": now, "entries": fresh})
            log.info("Cached %d listed companies (Main + Nomu)", len(fresh))
        except Exception:
            if _cache["entries"] is None:
                raise
            log.warning(
                "Tadawul directory refresh failed -- serving %d cached companies from disk instead",
                len(_cache["entries"]),
                exc_info=True,
            )
    return _cache["entries"]


def build_profile_url(ticker: str, market_type: str) -> str | None:
    tmpl = PROFILE_URL_TEMPLATES.get(market_type)
    return tmpl.format(ticker=ticker) if tmpl else None


def search_companies(query: str, limit: int = 8) -> list[dict]:
    """Local name search over the cached directory. Prefix matches on the
    trading/short name rank first (e.g. 'SABIC' before a company whose full
    legal name merely contains the word), then any substring match."""
    query = (query or "").strip().lower()
    if len(query) < 2:
        return []

    directory = get_directory()
    starts, contains = [], []
    for entry in directory:
        trading = (entry.get("tradingNameEn") or "").lower()
        full_name = (entry.get("companyNameEN") or "").lower()
        if trading.startswith(query) or full_name.startswith(query):
            starts.append(entry)
        elif query in trading or query in full_name:
            contains.append(entry)

    seen_symbols = set()
    out = []
    for e in starts + contains:
        symbol = e.get("symbol")
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        market_type = e.get("market_type")
        out.append(
            {
                "name": e.get("companyNameEN"),
                "trading_name": e.get("tradingNameEn"),
                "ticker": symbol,
                "isin": e.get("isin"),
                "market_type": market_type,
                "market_label": MARKET_TYPE_LABEL.get(market_type, market_type),
                "profile_url": build_profile_url(symbol, market_type),
            }
        )
        if len(out) >= limit:
            break
    return out
