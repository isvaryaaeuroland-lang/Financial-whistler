"""Search-by-name company lookup for NSE India.

Unlike lookup.py (Tadawul) and lookup_qatar.py, there's no bulk directory to
cache here -- NSE's smart-search is a live per-query endpoint, so every
search needs a real (non-headless) Chrome window, same as the scraper and
for the same reason: NSE blocks headless Chromium, including real Chrome's
newer headless mode -- verified live 2026-08-27 (net::ERR_HTTP2_PROTOCOL_ERROR
rather than a clean 403, suggesting the CDN resets the connection outright
when it detects headless characteristics).

Keeps that Chrome window alive and reuses it across searches, rather than
launching and closing a fresh one per query. Reason: opening a new,
non-headless Chrome window briefly steals OS focus before it can be
minimized (~0.6-1s: Chrome's own startup time plus the OS's window-minimize
animation, neither of which responds to any Playwright/CDP command any
faster) -- confirmed live by timing each step. `--window-position` and
`--start-minimized` are both silently ignored on macOS (verified: the
window still opens at (0, 34), fully visible), so minimizing via CDP right
after the page is created is the only thing that actually works, and even
that can't outrun the OS's own animation. Since the Tadawul/Kuwait/Oman/DFM
lookups cache their whole directory to disk (see lookup_cache.py) and
rarely launch a browser at all after the first search, they rarely show
this; NSE has no such directory to fall back on, so without reuse it would
show this jerk on *every single search* instead of just the first one in a
while.

Reusing the browser has one hard constraint: Playwright's sync API is
thread-bound -- a page created on one thread cannot be touched from another
(confirmed live: doing so raises "cannot switch to a different thread" from
the underlying greenlet). Flask runs each request on whatever thread the
server hands it, so the browser can't just live in a module-level variable
touched directly by request threads. Instead a single dedicated background
thread owns Playwright/the browser/the page for its entire life; request
threads only ever talk to it through a thread-safe queue.
"""

import atexit
import logging
import queue
import threading
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from . import chrome_profile, lookup_cache

log = logging.getLogger(__name__)

CACHE_NAME = "india_queries"

PROFILE_DIR = chrome_profile.profile_base() / "india_nse_lookup"

WARMUP_URL = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
SEARCH_PATH = "/api/smart-search/eqEtf"

# If no search comes in for this long, the worker closes its browser rather
# than sitting minimized in the background indefinitely on a dashboard
# nobody's using. Well above how long anyone plausibly pauses mid-session.
_IDLE_TIMEOUT_SECONDS = 20 * 60
_REQUEST_TIMEOUT_SECONDS = 60

# A daemon thread's `finally` block does not run when the interpreter
# exits -- Python just kills daemon threads outright. Left alone, that
# would orphan the Chrome subprocess on any abnormal-ish exit (a LaunchAgent
# restart, `launchctl kickstart -k`, a crash), and the orphan would still
# legitimately hold the profile's SingletonLock, so the *next* launch would
# hit the exact "second instance can't take over a live lock" failure that
# chrome_profile.clear_stale_lock() exists to prevent -- clear_stale_lock
# correctly leaves a lock alone when its owning PID is still alive, orphan
# or not. The atexit hook below asks the worker to shut down and waits
# briefly for it to actually close the browser first.
_SHUTDOWN = object()

_work_queue: "queue.Queue" = queue.Queue()
_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None


def _ensure_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker_loop, name="nse-lookup-worker", daemon=True)
            _worker_thread.start()


@atexit.register
def _shutdown_worker() -> None:
    with _worker_lock:
        thread = _worker_thread
    if thread is not None and thread.is_alive():
        _work_queue.put(_SHUTDOWN)
        thread.join(timeout=10)


def _worker_loop() -> None:
    playwright = None
    context = None
    page = None
    try:
        while True:
            try:
                item = _work_queue.get(timeout=_IDLE_TIMEOUT_SECONDS)
            except queue.Empty:
                log.info("NSE lookup session idle for %ds -- closing it", _IDLE_TIMEOUT_SECONDS)
                break

            if item is _SHUTDOWN:
                log.info("NSE lookup session shutting down")
                break
            query, response_q = item

            try:
                if page is None or page.is_closed():
                    playwright, context, page = _launch_session(playwright, context)
                data = _run_search(page, query)
                response_q.put(("ok", data))
            except Exception:
                log.warning("NSE search failed on the existing session -- retrying with a fresh one", exc_info=True)
                _close_session(playwright, context)
                playwright = context = page = None
                try:
                    playwright, context, page = _launch_session(None, None)
                    data = _run_search(page, query)
                    response_q.put(("ok", data))
                except Exception as retry_exc:
                    response_q.put(("error", retry_exc))
    finally:
        _close_session(playwright, context)


def _launch_session(old_playwright, old_context):
    _close_session(old_playwright, old_context)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome_profile.clear_stale_lock(PROFILE_DIR)
    log.info("Launching NSE lookup Chrome session (real browser, briefly visible while it opens)...")
    p = sync_playwright().start()
    try:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            args=["--window-position=-2400,-2400", "--window-size=1280,900"],
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = context.new_page()
        chrome_profile.hide_window(context, page)
        page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        p.stop()
        raise
    return p, context, page


def _close_session(playwright, context) -> None:
    try:
        if context is not None:
            context.close()
    except Exception:
        pass
    try:
        if playwright is not None:
            playwright.stop()
    except Exception:
        pass


def _run_search(page, query: str):
    url = f"https://www.nseindia.com{SEARCH_PATH}?q={quote(query)}"
    return page.evaluate("(u) => fetch(u).then(r => r.json())", url)


def search_companies(query: str, limit: int = 8) -> list[dict]:
    query = (query or "").strip()
    if len(query) < 2:
        return []

    cache = _load_query_cache()
    cache_key = query.lower()

    try:
        data = _search_live(query)
    except Exception:
        if cache_key in cache:
            log.warning(
                "NSE live search for %r failed -- serving %d cached result(s) from last time",
                query,
                len(cache[cache_key]),
                exc_info=True,
            )
            return cache[cache_key]
        raise

    results = _parse_results(data, limit)
    cache[cache_key] = results
    lookup_cache.save(CACHE_NAME, cache)
    return results


def _search_live(query: str):
    _ensure_worker()
    response_q: "queue.Queue" = queue.Queue()
    _work_queue.put((query, response_q))
    status, payload = response_q.get(timeout=_REQUEST_TIMEOUT_SECONDS)
    if status == "error":
        raise payload
    return payload


# NSE has no bulk directory export, so unlike the other lookups this can
# only cache per-query results rather than a whole exchange. Still useful:
# a failed live search falls back to whatever this exact query returned last
# time instead of erroring outright, same idea as the other lookups'
# disk-backed directory cache.
_query_cache: dict | None = None


def _load_query_cache() -> dict:
    global _query_cache
    if _query_cache is None:
        _query_cache = lookup_cache.load(CACHE_NAME) or {}
    return _query_cache


def _parse_results(data, limit: int) -> list[dict]:
    out = []
    for item in data or []:
        # Filter to the actual tradeable equity (series "EQ") -- excludes
        # bonds variants of the same company under the same name. NOT
        # sufficient on its own: Rights Entitlement variants are *also*
        # tagged segment="in equity"/series="EQ" (confirmed live searching
        # "IHCL", which only matches the RE variant, not the real equity --
        # searching the fuller name "Indian Hotels" finds the right one
        # instead). RE instruments are temporary, don't file their own
        # quarterly results, and follow a consistent "-RE" symbol suffix,
        # so exclude those explicitly too.
        if item.get("segment") != "in equity" or item.get("series") != "EQ":
            continue
        symbol = item.get("symbol")
        name = item.get("companyName")
        if not symbol or not name:
            continue
        if symbol.upper().endswith("-RE") or "rights entitlement" in name.lower():
            continue
        out.append(
            {
                "name": name,
                "ticker": symbol,
                "market_label": "India (NSE)",
                "profile_url": f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}",
            }
        )
        if len(out) >= limit:
            break
    return out
