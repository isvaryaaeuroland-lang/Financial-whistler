import argparse
import logging
import sys

from dotenv import load_dotenv

from .config import Settings, group_by_exchange_host, load_companies
from .models import Announcement, Company
from .scrapers import SCRAPER_REGISTRY
from . import daily_status
from .alerts.email_alert import EmailAlerter, github_run_url
from .state import StateStore


def setup_logging(log_file) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Financial Whistler - MENA financial-results monitor")
    parser.add_argument(
        "--dry-run", action="store_true", help="Scrape but do not send email or update state"
    )
    parser.add_argument(
        "--force-test-alert",
        action="store_true",
        help="Send one fabricated test alert through the real email path, "
        "without scraping anything. For verifying SMTP delivery works.",
    )
    args = parser.parse_args()

    load_dotenv()
    settings = Settings()
    setup_logging(settings.log_file)
    log = logging.getLogger("financial_whistler")

    if args.force_test_alert:
        fake_company = Company(
            name="TEST COMPANY (not real)",
            ticker="0000",
            profile_url="https://example.invalid/test",
        )
        fake_announcement = Announcement(
            id="test-0",
            company=fake_company,
            title="TEST ALERT - Financial Whistler email delivery check",
            date_text="(this is a fabricated test announcement, not a real disclosure)",
            detail_url="https://example.invalid/test",
        )
        log.info("--force-test-alert: sending one fabricated test alert to verify SMTP delivery")
        EmailAlerter(settings).send_digest([fake_announcement])
        return 0

    companies = load_companies(settings.companies_file)
    if not companies:
        log.error("No companies loaded from %s - nothing to do", settings.companies_file)
        return 1
    log.info("Loaded %d companies", len(companies))

    started = daily_status.now_ist()
    grouped = group_by_exchange_host(companies)
    state = StateStore(settings.state_file)
    new_alerts: list[Announcement] = []
    failed_now: list[str] = []
    repeat_failures: list[tuple[str, Company]] = []
    checked_ok = 0
    checked_failed = 0

    for host, comp_list in grouped.items():
        scraper_cls = SCRAPER_REGISTRY.get(host)
        if not scraper_cls:
            log.warning("No scraper registered for host %r - skipping %d companies", host, len(comp_list))
            continue

        scraper = scraper_cls()
        log.info("Scraping %s: %d companies via %s", scraper.exchange_name, len(comp_list), host)
        try:
            results = scraper.run(comp_list)
        except Exception:
            # A whole-exchange failure (e.g. the browser itself couldn't
            # launch) shouldn't stop other exchanges from being checked --
            # treat every company in this group as failed and move on.
            log.exception(
                "%s: scraper.run() raised - treating all %d companies as failed this run",
                scraper.exchange_name,
                len(comp_list),
            )
            results = {c.ticker: None for c in comp_list}

        for company in comp_list:
            announcements = results.get(company.ticker)
            if announcements is None:
                checked_failed += 1
                failed_now.append(f"{company.name} ({company.ticker})")
                streak = state.record_failure(company.ticker)
                log.warning(
                    "%s (%s): fetch failed (%d run(s) in a row), skipping this run",
                    company.name,
                    company.ticker,
                    streak,
                )
                # One failure is usually a transient timeout and gets retried
                # next run anyway; only the second in a row is worth an email.
                # Exactly 2 (not >= 2) so a company that stays blocked is
                # reported once, not on every run until it recovers.
                if streak == 2:
                    repeat_failures.append((scraper.exchange_name, company))
                continue

            checked_ok += 1
            is_first_run = not state.is_seeded(company.ticker)
            seen = state.seen_ids(company.ticker)
            current_ids = [a.id for a in announcements]

            if is_first_run:
                log.info(
                    "%s (%s): checked OK - first run, seeding baseline with %d financial statement(s), no alerts",
                    company.name,
                    company.ticker,
                    len(announcements),
                )
            else:
                new_here = [a for a in announcements if a.id not in seen]
                if not new_here:
                    log.info(
                        "%s (%s): checked OK - %d financial statement(s) on file, none new",
                        company.name,
                        company.ticker,
                        len(announcements),
                    )
                for ann in new_here:
                    # Every item here already came from the Financial Statements
                    # table specifically (see scrapers/tadawul.py), so no keyword
                    # classification is needed -- it's a financial result by
                    # construction, not by guessing from a title.
                    new_alerts.append(ann)
                    log.info("%s (%s): checked OK - NEW financial statement: %s", company.name, company.ticker, ann.title)

            if not args.dry_run:
                state.record(company.ticker, current_ids)

    if not args.dry_run:
        state.save()

    log.info(
        "Run summary: %d/%d companies checked successfully (%d failed), %d new financial-result announcement(s)",
        checked_ok,
        len(companies),
        checked_failed,
        len(new_alerts),
    )

    if new_alerts:
        if args.dry_run:
            log.info("[dry-run] Would send alert for %d new financial result(s):", len(new_alerts))
            for a in new_alerts:
                log.info("  - %s (%s): %s", a.company.name, a.company.ticker, a.title)
        else:
            EmailAlerter(settings).send_digest(new_alerts)

    if repeat_failures:
        if args.dry_run:
            log.info("[dry-run] Would send failure report for %d company(ies)", len(repeat_failures))
        else:
            EmailAlerter(settings).send_failure_report(repeat_failures, len(companies))

    if not args.dry_run:
        daily_status.record_run(
            state,
            started,
            checked_ok,
            len(companies),
            failed_now,
            [f"{a.company.name} ({a.company.ticker}): {a.title}" for a in new_alerts],
        )
        state.save()
        if daily_status.is_due(state, settings, started):
            subject, body = daily_status.build_email(state, settings, github_run_url())
            EmailAlerter(settings).send_daily_status(subject, body)
            daily_status.mark_sent(state)
            state.save()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
