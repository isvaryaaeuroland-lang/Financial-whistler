"""Search-by-name company lookup for Boursa Kuwait.

Fetches the full "Listed Companies" table once (cached in-process, refreshed
daily) from the public directory page -- rendered server-side into the DOM,
not a JSON API -- then matches locally against the cached list.

Boursa Kuwait blocks headless Chromium (see scrapers/kuwait.py), so this
uses the same real, non-headless, persistent-profile Chrome approach.

Each row's "Sec. Code" column is the opaque internal numeric company ID that
scrapers/kuwait.py needs (verified live: Jazeera Airways' Sec. Code 654
matches the #654 fragment already used in companies.xlsx), so the built
profile_url embeds it as a URL fragment the same way.
"""

import logging
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import chrome_profile, lookup_cache

log = logging.getLogger(__name__)

CACHE_NAME = "kuwait"

# See lookup_india.py's docstring for why this matters.
_launch_lock = threading.Lock()

PROFILE_DIR = chrome_profile.profile_base() / "lookup-kuwait"

DIRECTORY_URL = "https://www.boursakuwait.com.kw/en/participants/participants/listed-companies"
PROFILE_URL_TMPL = "https://www.boursakuwait.com.kw/en/stock/financial-statement#{company_id}"

_ROWS_JS = """
() => Array.from(document.querySelectorAll('table tbody tr')).map(
  tr => Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim())
)
"""

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
            viewport={"width": 1280, "height": 900},
        )
        try:
            page = context.new_page()
            chrome_profile.hide_window(context, page)
            page.goto(DIRECTORY_URL, wait_until="domcontentloaded", timeout=45000)
            # The table renders an empty placeholder row before data loads,
            # so wait for real rows rather than just the selector matching.
            page.wait_for_function(
                "document.querySelectorAll('table tbody tr').length > 5", timeout=20000
            )
            rows = page.evaluate(_ROWS_JS)
        finally:
            context.close()

    entries = []
    for row in rows:
        # Columns: #No, Sec. Code, Ticker, (logo), Name, Sector, Market Segment, Date of Listing
        if len(row) < 5:
            continue
        company_id, ticker, name = row[1], row[2], row[4]
        if not company_id or not ticker:
            continue
        entries.append({"company_id": company_id, "ticker": ticker, "name": name})
    return entries


def get_directory(force_refresh: bool = False) -> list[dict]:
    if _cache["entries"] is None:
        disk = lookup_cache.load(CACHE_NAME)
        if disk:
            _cache["entries"] = disk.get("entries")
            _cache["fetched_at"] = disk.get("fetched_at", 0.0)
            log.info("Loaded %d Boursa Kuwait companies from disk cache", len(_cache["entries"]))

    now = time.time()
    stale = (now - _cache["fetched_at"]) > _CACHE_TTL_SECONDS
    if force_refresh or _cache["entries"] is None or stale:
        try:
            log.info("Fetching Boursa Kuwait company directory (real browser, ~10-20s)...")
            fresh = _fetch_directory()
            _cache["entries"] = fresh
            _cache["fetched_at"] = now
            lookup_cache.save(CACHE_NAME, {"fetched_at": now, "entries": fresh})
            log.info("Cached %d listed companies (Boursa Kuwait)", len(fresh))
        except Exception:
            if _cache["entries"] is None:
                raise
            log.warning(
                "Boursa Kuwait directory refresh failed -- serving %d cached companies from disk instead",
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
        name_l = entry["name"].lower()
        ticker_l = entry["ticker"].lower()
        if name_l.startswith(query) or ticker_l.startswith(query):
            starts.append(entry)
        elif query in name_l:
            contains.append(entry)

    seen = set()
    out = []
    for e in starts + contains:
        if e["ticker"] in seen:
            continue
        seen.add(e["ticker"])
        out.append(
            {
                "name": e["name"],
                "ticker": e["ticker"],
                "market_label": "Kuwait (Boursa Kuwait)",
                "profile_url": PROFILE_URL_TMPL.format(company_id=e["company_id"]),
            }
        )
        if len(out) >= limit:
            break
    return out
