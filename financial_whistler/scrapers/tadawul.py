import logging
import random
import shutil
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from .. import chrome_profile
from ..config import BASE_DIR
from ..models import Announcement, Company
from .base import ExchangeScraper

log = logging.getLogger(__name__)

# Persisted real Chrome profile (cookies, local storage, history) so repeated
# runs look like a recurring visitor returning to the site rather than a
# fresh, cookie-less session every single time -- one signal among several
# that bot-management scoring weighs alongside the headless check.
#
# Deliberately kept OUTSIDE the project folder (which lives in OneDrive) --
# a live Chrome profile's constantly-churning SQLite/lock files inside a
# cloud-sync folder caused severe intermittent hangs (one run took 11+
# minutes just to launch Chrome before a single page even loaded, while the
# identical code against a profile outside OneDrive was consistently fast).
# See README "Known issue: Chrome profile location" for the full story.
PROFILE_DIR = chrome_profile.profile_base() / "tadawul"

# Random pause between company page loads so requests aren't machine-uniform
# back-to-back bursts -- 26 human-plausible individual page visits spread
# over roughly a minute or two, rather than as fast as the network allows.
MIN_DELAY_S = 2.5
MAX_DELAY_S = 6.0

# Consecutive company failures that trigger one restart with a fresh session.
RESTART_AFTER_CONSECUTIVE_FAILURES = 3

# Screenshots of failed pages go next to the log, so the GitHub Actions run
# uploads them with it (the workflow uploads the whole logs/ folder).
SCREENSHOT_DIR = BASE_DIR / "logs" / "screenshots"

PDF_BASE = "https://www.saudiexchange.sa"

# The company profile page's "Financials" section has a "FINANCIAL
# STATEMENTS AND REPORTS" sub-tab (clicked below) whose table combines FOUR
# separate report types as repeated <thead>/<tbody> sibling pairs within one
# <table>: Financial Statements, XBRL, Board Report, ESG Report (verified
# live 2026-08-24 against Advanced Petrochemical's profile page). Only
# "Financial Statements" is what this tool alerts on -- found by matching
# the <thead> whose title row is exactly "Financial Statements" and reading
# its immediately-following sibling <tbody>. Each populated cell has a
# <a href=".../Resources/fsPdf/{internalId}_0_{timestamp}_En.pdf"> and a
# <p>display date</p>; the filename itself is a stable, unique, permanent
# id for that specific filing (used as the dedup key).
_EXTRACT_JS = """
() => {
  const financials = document.querySelector('.financials');
  if (!financials) return { error: 'no .financials section on page' };
  const theads = Array.from(financials.querySelectorAll('table thead'));
  let targetThead = null;
  for (const th of theads) {
    const titleRow = th.querySelector('tr');
    if (titleRow && titleRow.textContent.trim().replace(/\\s+/g, ' ') === 'Financial Statements') {
      targetThead = th;
      break;
    }
  }
  if (!targetThead) return { error: 'Financial Statements table not found' };
  const tbody = targetThead.nextElementSibling;
  if (!tbody || tbody.tagName !== 'TBODY') return { error: 'Financial Statements tbody not found' };

  const theadRows = Array.from(targetThead.querySelectorAll('tr'));
  const yearRow = theadRows[1];
  const yearHeaders = yearRow ? Array.from(yearRow.querySelectorAll('th, td')).map(c => c.textContent.trim()) : [];

  const rows = Array.from(tbody.querySelectorAll('tr'));
  const out = [];
  for (const tr of rows) {
    const tds = Array.from(tr.querySelectorAll('td'));
    if (!tds.length) continue;
    const period = tds[0].textContent.trim();
    for (let i = 1; i < tds.length; i++) {
      const a = tds[i].querySelector('a[href*="fsPdf"]');
      if (!a) continue;
      const p = tds[i].querySelector('p');
      out.push({
        period,
        year: yearHeaders[i] || null,
        href: a.getAttribute('href'),
        dateText: p ? p.textContent.trim() : '',
      });
    }
  }
  return { releases: out };
}
"""


class TadawulScraper(ExchangeScraper):
    exchange_name = "Tadawul (Saudi Exchange)"

    def __init__(self, timeout_ms: int = 45000, retries: int = 2):
        self.timeout_ms = timeout_ms
        self.retries = retries

    def run(self, companies: list[Company]) -> dict[str, list[Announcement] | None]:
        results: dict[str, list[Announcement] | None] = {}
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        chrome_profile.clear_stale_lock(PROFILE_DIR)
        with sync_playwright() as p:
            context = self._launch(p, PROFILE_DIR)
            fresh_dir: str | None = None
            try:
                pending = list(companies)
                streak: list[Company] = []
                restarted = False
                i = 0
                while i < len(pending):
                    company = pending[i]
                    if i > 0:
                        time.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))
                    result = self._scrape_one(context, company)
                    results[company.ticker] = result
                    i += 1
                    if result is not None:
                        streak = []
                        continue
                    streak.append(company)
                    if len(streak) < RESTART_AFTER_CONSECUTIVE_FAILURES:
                        continue
                    # Several companies failing back to back mid-run means the
                    # session itself has gone bad, not those companies: on
                    # 2026-10-20 the first 11 companies worked and then all 17
                    # remaining ones timed out in the same session, costing 30
                    # minutes. Restart once in a brand-new browser session and
                    # retry the streak; if that fails the same way too, the
                    # site is down for us and the rest are skipped.
                    if not restarted:
                        log.warning(
                            "%d Tadawul companies failed in a row - restarting Chrome with a fresh "
                            "session and retrying them",
                            len(streak),
                        )
                        context.close()
                        # A throwaway profile, not a wipe of PROFILE_DIR: the
                        # persistent "returning visitor" profile stays intact
                        # for future runs.
                        fresh_dir = tempfile.mkdtemp(prefix="tadawul-fresh-")
                        try:
                            context = self._launch(p, Path(fresh_dir))
                        except Exception:  # noqa: BLE001 - keep the companies already checked
                            log.exception("Could not restart Chrome - skipping the remaining Tadawul companies")
                            for c in pending[i:]:
                                results[c.ticker] = None
                            break
                        restarted = True
                        pending[i:i] = streak
                        streak = []
                        continue
                    remaining = pending[i:]
                    log.error(
                        "%d Tadawul companies failed in a row even after a fresh session - skipping "
                        "the remaining %d this run",
                        len(streak),
                        len(remaining),
                    )
                    for c in remaining:
                        results[c.ticker] = None
                    break
            finally:
                context.close()
                if fresh_dir:
                    shutil.rmtree(fresh_dir, ignore_errors=True)
        return results

    @staticmethod
    def _launch(p, profile_dir: Path):
        # Akamai (in front of saudiexchange.sa) returns a 403 on every
        # request when Chromium runs in headless mode, even with a real
        # Chrome channel and a normal user agent -- confirmed by testing
        # headless=True vs headless=False side by side (403 vs 200) with
        # everything else identical. A non-headless real Chrome window,
        # moved off-screen, passes. Requires the machine running this to
        # have Google Chrome installed and an active logged-in session
        # (cron/launchd need window-server access) -- see README.
        #
        # launch_persistent_context (rather than launch + new_context)
        # keeps cookies/storage across runs in PROFILE_DIR, so this looks
        # like the same returning visitor each time instead of a fresh
        # session 3x/day.
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            args=["--window-position=-2400,-2400", "--window-size=1280,900"],
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        chrome_profile.hide_window(context, context.new_page())
        return context

    def _scrape_one(self, context, company: Company) -> list[Announcement] | None:
        for attempt in range(1, self.retries + 2):
            page = context.new_page()
            try:
                resp = page.goto(
                    company.profile_url, wait_until="domcontentloaded", timeout=self.timeout_ms
                )
                if resp is not None and resp.status == 403:
                    log.error(
                        "%s (%s): blocked with HTTP 403 (likely Akamai bot protection) "
                        "on attempt %d/%d",
                        company.name,
                        company.ticker,
                        attempt,
                        self.retries + 1,
                    )
                    continue

                page.locator(".financials_Tab").get_by_text(
                    "FINANCIAL STATEMENTS AND REPORTS", exact=False
                ).first.click()
                # Deliberately waiting for an actual fsPdf link, not just any
                # table in .financials_Tab_Dtl_box -- that container already
                # has tables from the default "FINANCIAL INFORMATION" tab
                # before this click does anything, which made an earlier,
                # broader selector here time out for real (confirmed live:
                # it kept resolving to those pre-existing tables without ever
                # satisfying Playwright's visibility check, for the full 45s,
                # on every company). Waiting for the specific evidence this
                # scraper actually needs is what verified reliably in testing.
                page.wait_for_selector('a[href*="fsPdf"]', timeout=self.timeout_ms)

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
                if attempt == self.retries + 1:
                    self._record_failed_page(page, company)
            finally:
                page.close()
        log.error("%s (%s): giving up after %d attempts", company.name, company.ticker, self.retries + 1)
        return None

    @staticmethod
    def _record_failed_page(page, company: Company) -> None:
        """Logs what the page actually showed when a company finally fails,
        and saves a screenshot next to the log. A "tab never appeared"
        timeout alone can't tell an Akamai challenge page from maintenance
        or a real layout change -- the 2026-10-20 failures had nothing else
        to go on. Best-effort: never raises."""
        try:
            title = page.title()
            text = page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 600)")
            text = " ".join(text.split())[:300]
            log.error(
                "%s (%s): page at failure - title=%r url=%s text=%r",
                company.name,
                company.ticker,
                title,
                page.url,
                text,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("%s (%s): could not read the failed page: %s", company.name, company.ticker, exc)
        try:
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = SCREENSHOT_DIR / f"tadawul-{company.ticker}-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
            page.screenshot(path=str(path), type="jpeg", quality=60)
            log.error("%s (%s): screenshot of the failed page saved: %s", company.name, company.ticker, path.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s (%s): could not save a screenshot: %s", company.name, company.ticker, exc)

    @staticmethod
    def _parse_releases(company: Company, releases: list[dict]) -> list[Announcement]:
        out = []
        for item in releases:
            href = item.get("href") or ""
            if not href:
                continue
            filename = href.rsplit("/", 1)[-1]  # e.g. "413_0_2026-02-23_13-42-14_En.pdf" -- unique per filing
            period = item.get("period") or "?"
            year = item.get("year") or "?"
            out.append(
                Announcement(
                    id=filename,
                    company=company,
                    title=f"{period} {year} Financial Statements",
                    date_text=item.get("dateText") or "",
                    detail_url=PDF_BASE + href,
                )
            )
        return out
