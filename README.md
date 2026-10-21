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

1. At **12:37, 15:37 and 18:37 IST** every day, a scheduled job on
   [cron-job.org](https://cron-job.org) asks GitHub to start a run, and the
   run itself happens on GitHub Actions.
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

Once a day, the evening run also sends a short **daily status** email ("all
clear" or "check needed") to the person looking after the tool — not to the
whole alert list. If that email doesn't arrive, the runs have stopped.

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
`ALERT_RECIPIENTS` is a comma-separated list of addresses. The daily status
email goes to `STATUS_RECIPIENTS` instead, a separate and normally much
shorter list. Leave it empty to turn the status email off.

Only the **repo owner** can edit secrets (Settings → Secrets and variables →
Actions → `ALERTS` → ✏️). GitHub never shows the current value, so paste the
complete `.env` contents again, not only the line you're changing.

### Run it now, stop a run, or pause the schedule

Repo → **Actions** → **Financial Whistler** (left sidebar):

| To… | Do this |
|---|---|
| Start a run now | **Run workflow** → choose a mode → **Run workflow** |
| Stop a run in progress | open the run → **Cancel workflow** |
| Pause / resume the daily schedule | On **cron-job.org** (not GitHub): switch the *Financial Whistler* job off / on in the job list. See [Change the schedule](#change-the-schedule). |

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
**Artifacts**, kept for 14 days. If a Tadawul company failed, the same
download also has a `screenshots/` folder showing what its page looked like
at that moment, and the log line before it quotes the page's title and text.

### Change the schedule

The run times are set on **cron-job.org**, not in this repo. GitHub's own
scheduler was tried first and proved unreliable: it skipped runs and started
others up to 6 hours late. So the workflow no longer has a `schedule:`
trigger, and editing `.github/workflows/whistler.yml` won't change the times.

To change the times, sign in to [cron-job.org](https://console.cron-job.org)
with the account that owns the job, open the **Financial Whistler** job, and
in the **Common** tab → **Execution schedule** → **Custom**, pick the
**Hours** and **Minutes**. Ctrl+click selects several values. The job's time
zone is **Asia/Kolkata** (**Advanced** tab), so pick IST times directly.
Check **Next executions** shows what you expect, then **Save**.

Avoid minute `0`: everything else on the internet runs on the hour too.

The job works by sending this request, which you only need if you ever have
to rebuild it:

| Setting | Value |
|---|---|
| URL | `https://api.github.com/repos/isvaryaaeuroland-lang/Financial-whistler/actions/workflows/whistler.yml/dispatches` |
| Method | `POST` |
| Headers | `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `Content-Type: application/json` |
| Body | `{"ref":"main","inputs":{"mode":"normal"}}` |

`<token>` is a fine-grained GitHub token limited to this repo with
**Actions: Read and write** only. **It expires**, and when it does
cron-job.org's calls fail and no runs happen. Create a new token and paste it
into the job's `Authorization` header (keep the `Bearer ` prefix).

**Don't use GitHub's "Disable workflow" to pause runs.** It also rejects
cron-job.org's calls, which then show up there as failures. Switch the
cron-job.org job off instead.

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
| Daily status email (when it's sent, "all clear" rule, wording) | [`financial_whistler/daily_status.py`](financial_whistler/daily_status.py) |
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

The repo is **private**. Collaborators can change code and the company list,
and start or stop runs. Only the **owner** (currently
`isvaryaaeuroland-lang`) can change secrets — the alert recipients and SMTP
login — or repo settings and access. The **run schedule** is changed only by
whoever holds the cron-job.org login, and that person also renews the GitHub
token the job uses. Ask the owner for access or for changes to those.
