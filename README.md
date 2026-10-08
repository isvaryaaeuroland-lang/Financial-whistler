# Financial Whistler

Watches the investor-relations disclosure pages of listed MENA and Indian
companies and **emails an alert when a company publishes new financial
statements**. It runs automatically three times a day on GitHub Actions — no
server or laptop needs to be left on.

Exchanges currently covered:

| Exchange | Website |
|---|---|
| Saudi Exchange (Tadawul) | saudiexchange.sa |
| Qatar Stock Exchange | qe.com.qa |
| National Stock Exchange of India | nseindia.com |
| Dubai Financial Market | dfm.ae |
| Muscat Stock Exchange | msx.om |
| Boursa Kuwait | boursakuwait.com.kw |

## How it works

1. At **12:30, 15:30 and 18:30 IST** every day, GitHub starts a run.
2. The run opens each company's page on its exchange's website in real
   Google Chrome and reads the list of published financial statements.
3. Anything not seen on an earlier run goes into one digest email to the
   alert recipients.
4. What it has seen is saved, so each filing is alerted on exactly once.

A company is only alerted on from its *second* run onwards — the first run
for a newly added company just records what's already published, so you
don't get its whole history at once.

If the **same company can't be checked two runs in a row** (the exchange is
blocking us, or changed its page layout), you get a separate failure email
listing it.

## Everyday tasks

You'll need access to the GitHub repo
([isvaryaaeuroland-lang/Financial-whistler](https://github.com/isvaryaaeuroland-lang/Financial-whistler))
as a collaborator. Everything below is done on GitHub's website unless it
says otherwise.

### Add or remove a company

The list the scheduled runs use is `companies.xlsx` on the **`whistler-data`**
branch — not on `main`, and not anyone's local copy.

1. In the repo, switch the branch selector (top left, says `main`) to
   **`whistler-data`**.
2. Click `companies.xlsx` → **Download** (⋯ menu or download icon).
3. Edit it in Excel. Columns are found by their header, so keep the header
   row: a **company name** column, a **ticker** (or symbol) column, and the
   **announcement URL** — the company's page on its exchange's website. An
   optional **market** column is allowed.
4. Back on GitHub, still on `whistler-data`: **Add file → Upload files**,
   drop in the edited `companies.xlsx`, **Commit changes**.

The next run picks it up. Rows with an empty URL, or a URL on an exchange
not listed above, are skipped (the log says so).

### Change who gets the alerts, or the sending mailbox

These are in the repository secret **`ALERTS`**, which holds the contents of
a `.env` file (see [`.env.example`](.env.example) for every setting).
`ALERT_RECIPIENTS` is a comma-separated list of addresses.

Only the **repo owner** can edit secrets (Settings → Secrets and variables →
Actions → `ALERTS` → ✏️). GitHub never shows the current value, so paste the
complete `.env` contents again, not only the line you're changing.

### Run it now, stop a run, or pause the schedule

Repo → **Actions** → **Financial Whistler** (left sidebar):

| To… | Do this |
|---|---|
| Start a run now | **Run workflow** → choose a mode → **Run workflow** |
| Stop a run in progress | open the run → **Cancel workflow** |
| Pause / resume the daily schedule | **⋯** (top right) → **Disable workflow** / **Enable workflow** |

The modes:

- **`dry-run`** — checks every company but sends no email and saves nothing.
  Use it to see whether all exchanges still load.
- **`test-alert`** — sends one fake "TEST COMPANY" alert, checks nothing.
  Use it to confirm email delivery.
- **`normal`** — a real run, same as the scheduled ones.

Cancelling is safe: results are only saved at the very end of a run.

### Read the logs

Repo → **Actions** → click a run → the **run** job → expand **Run whistler**.
This shows live progress while a run is going, and every company's result
afterwards. The run's summary page also has the full `whistler.log` under
**Artifacts**, kept for 14 days.

### Change the schedule

Edit the `cron:` lines in
[`.github/workflows/whistler.yml`](.github/workflows/whistler.yml) on `main`
(open the file on GitHub → ✏️ → **Commit changes**).

Times are **UTC**: subtract 5h30 from IST. Format is
`minute hour day month weekday`:

```yaml
    - cron: "30 7 * * *"     # 07:30 UTC = 13:00 IST, every day
    - cron: "30 3 * * 1-5"   # 03:30 UTC = 09:00 IST, Monday-Friday only
```

## Changing the code

Code changes take effect on the next run once they're **on `main`**. Small
edits can be made on GitHub directly (✏️ on any file); for anything bigger,
clone the repo and work locally (below).

| To change… | Look in |
|---|---|
| How a particular exchange is read | `financial_whistler/scrapers/<exchange>.py` |
| Adding a new exchange | a new scraper in `financial_whistler/scrapers/`, registered by website hostname in [`scrapers/__init__.py`](financial_whistler/scrapers/__init__.py) |
| The run itself (which companies, alert vs. failure logic) | [`financial_whistler/run.py`](financial_whistler/run.py) |
| What "seen" means / failure streaks | [`financial_whistler/state.py`](financial_whistler/state.py) |
| Email subject and wording | [`financial_whistler/alerts/email_alert.py`](financial_whistler/alerts/email_alert.py) |
| Reading `companies.xlsx` | [`financial_whistler/config.py`](financial_whistler/config.py) |
| The scheduled run's setup (Chrome, Python version, timeouts, log retention) | [`.github/workflows/whistler.yml`](.github/workflows/whistler.yml) |
| Python packages | `requirements.txt` |

**Before merging a scraper change, start a `dry-run`** from the Actions tab
and check every company still says "checked OK".

### Why real Chrome, not a headless browser

Tadawul, Boursa Kuwait and NSE reject headless (invisible) Chromium. Every
scraper therefore launches the installed **Google Chrome** in a normal
window — on GitHub's servers that window opens inside a virtual display
(Xvfb). Don't switch a scraper to `headless=True` without a dry run proving
the exchange still answers.

## Running it on your own computer

Needed: Python 3.12+ and Google Chrome installed.

```bash
python -m venv .venv
```

Then activate it (`.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` on macOS/Linux) and:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill it in. For local runs you also need
your own `companies.xlsx` in the project folder — download it from the
`whistler-data` branch. Both files are gitignored, so they won't be committed
by accident.

```bash
python run_whistler.py --dry-run
```

`--dry-run` checks everything without emailing or saving. Leave the flag off
for a real run, or use `--force-test-alert` to test email. A local Chrome
window will flash open while it runs.

**A real local run keeps its own `state/seen.json`, separate from the
scheduled runs' state** — so it can email alerts the team already got. Stick
to `--dry-run` locally unless you mean to.

### The dashboard

`python webapp/app.py` starts a web dashboard at http://127.0.0.1:5057
(login set by `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` in `.env`). It can
look up companies on each exchange, edit the company list, and show logs.

Today it only works with **local** files: its company edits, logs and "Run
now" don't reach the scheduled GitHub runs. Connecting it to GitHub is in
progress. Its "Run now" and schedule buttons also assume macOS or Linux.

## More detail

[`deploy/README.md`](deploy/README.md) covers the deployment in depth: the
`whistler-data` branch, Actions minutes, monitoring, and the alternative of
running everything (dashboard included) on an always-on Linux server.

## Access and ownership

The repo is **private**. Collaborators can change code, the company list and
the schedule, and start or stop runs. Only the **owner** (currently
`isvaryaaeuroland-lang`) can change secrets — the alert recipients and SMTP
login — or repo settings and access. Ask the owner for access or for changes
to those.
