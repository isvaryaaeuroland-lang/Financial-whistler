"""Search-by-name company lookup for Muscat Stock Exchange (MSX).

Fetches the full MSX company directory once (cached in-process, refreshed
daily) from companies.aspx/List -- the same JSON endpoint that backs the
site's own "Companies" page -- then matches locally against the cached list.
No headless block on MSX (verified live in scrapers/oman.py), so this uses a
plain headless fetch, unlike the Tadawul/NSE lookups.

Rights-issue subscriptions are excluded, matched two ways since neither
alone is complete -- verified live 2026-08-26 against the full directory:
Market codes 4/5 catch most of them (e.g. ticker "RISB" / "BANK SOHAR RI
2022", no "RIGHT" substring in the name), while a name substring match
("RIGHT"/"RIGTH", covering a recurring site typo) catches a handful of
older ones still sitting under a regular Market code (e.g. "BSRI" / "BANK
SOHAR RIGHT ISSUE", Market="3"). No real company name collided with either
check.
"""

import logging
import time

from playwright.sync_api import sync_playwright

from . import lookup_cache

log = logging.getLogger(__name__)

CACHE_NAME = "oman"

WARMUP_URL = "https://www.msx.om/"
LIST_ENDPOINT = "https://www.msx.om/companies.aspx/List"
PROFILE_URL_TMPL = "https://www.msx.om/snapshot.aspx?s={ticker}"

# See module docstring: two independent signals, either one excludes.
_RIGHTS_ISSUE_MARKETS = {"4", "5"}
_RIGHTS_ISSUE_NAME_MARKERS = ("RIGHT", "RIGTH")


def _is_rights_issue(entry: dict) -> bool:
    if entry.get("Market") in _RIGHTS_ISSUE_MARKETS:
        return True
    name = (entry.get("LongNameEn") or "").upper()
    return any(marker in name for marker in _RIGHTS_ISSUE_NAME_MARKERS)

_FETCH_JS = """
() => fetch('/companies.aspx/List', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json; charset=utf-8' },
  body: '{}',
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
    return [d for d in data.get("d", []) if not _is_rights_issue(d)]


def get_directory(force_refresh: bool = False) -> list[dict]:
    if _cache["entries"] is None:
        disk = lookup_cache.load(CACHE_NAME)
        if disk:
            _cache["entries"] = disk.get("entries")
            _cache["fetched_at"] = disk.get("fetched_at", 0.0)
            log.info("Loaded %d MSX companies from disk cache", len(_cache["entries"]))

    now = time.time()
    stale = (now - _cache["fetched_at"]) > _CACHE_TTL_SECONDS
    if force_refresh or _cache["entries"] is None or stale:
        try:
            log.info("Fetching MSX company directory...")
            fresh = _fetch_directory()
            _cache["entries"] = fresh
            _cache["fetched_at"] = now
            lookup_cache.save(CACHE_NAME, {"fetched_at": now, "entries": fresh})
            log.info("Cached %d listed companies (MSX)", len(fresh))
        except Exception:
            if _cache["entries"] is None:
                raise
            log.warning(
                "MSX directory refresh failed -- serving %d cached companies from disk instead",
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
        name = (entry.get("LongNameEn") or "").lower()
        symbol = (entry.get("Symbol") or "").lower()
        if name.startswith(query) or symbol.startswith(query):
            starts.append(entry)
        elif query in name:
            contains.append(entry)

    seen = set()
    out = []
    for e in starts + contains:
        symbol = e.get("Symbol")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(
            {
                "name": e.get("LongNameEn"),
                "ticker": symbol,
                "market_label": "Oman (MSX)",
                "profile_url": PROFILE_URL_TMPL.format(ticker=symbol),
            }
        )
        if len(out) >= limit:
            break
    return out
