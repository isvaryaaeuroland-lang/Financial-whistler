# Deploying Financial Whistler

The scheduled scrape now runs on **GitHub Actions** — free, no server to
maintain, nothing that depends on a laptop being awake. The always-on server
setup (systemd + Xvfb on a Linux VM) is still documented further down as an
alternative, mainly for if you want the dashboard running 24/7 too.

## GitHub Actions (current setup)

### What runs, and when

[`.github/workflows/whistler.yml`](../.github/workflows/whistler.yml) runs
`run_whistler.py` on a GitHub-hosted Ubuntu runner at **07:30, 10:00 and
13:00 UTC** (13:00 / 15:30 / 18:30 IST) every day — the same times as the
old launchd schedule and the systemd timer below. GitHub may start scheduled
runs a few minutes late.

Three exchanges (Tadawul, Boursa Kuwait, NSE) block headless Chromium, so
each run starts real Google Chrome (preinstalled on the runner) in a
non-headless window inside **Xvfb**, a virtual display. Verified with a dry
run on 2026-10-08: **41/41 companies across all 6 exchanges checked OK** from
GitHub's runners, no 403s. A full run takes about 6–7 minutes, so 3 runs/day
uses roughly 600–700 of the 2,000 free Actions minutes a private repo gets
each month.

### Where the data lives

`companies.xlsx` and `state/` are gitignored on `main` (client data, and
state that changes every run). They live on a separate **`whistler-data`**
branch instead:

| File on `whistler-data` | What it is |
|---|---|
| `companies.xlsx` | The client list the scheduled runs use |
| `state/seen.json` | Announcements already alerted on, plus each company's current failure streak |

Each run checks that branch out beside the code, runs, and — whenever
`seen.json` changed — commits it back. That commit is how the next run
remembers this one. **Keep the repository private**: both files are client data.

### Email settings

One repository secret, **`ALERTS`** (Settings → Secrets and variables →
Actions), holds the full contents of the local `.env` file (see
[`.env.example`](../.env.example)). Each run writes it out as `.env`, which
`run.py` already loads. To change the SMTP account or the alert recipients,
edit that secret — there's nothing to change in the code.

### Running it by hand

Repo → **Actions** → **Financial Whistler** → **Run workflow**, then pick a mode:

| Mode | What it does |
|---|---|
| `dry-run` (default) | Scrapes everything, sends no email, changes no state. Use it to check the exchanges still load. |
| `test-alert` | Sends one fabricated "TEST COMPANY" alert, scrapes nothing. Use it to check email delivery. |
| `normal` | A real run, exactly like the scheduled ones. |

Each run's `logs/whistler.log` is attached to the run as an artifact for 14
days (open the run → **Artifacts**).

### How you find out something is wrong

| Problem | How you hear about it |
|---|---|
| A company can't be checked (blocked, timeout, page layout changed) | A failure email, but only once the **same company fails 2 runs in a row** — a one-off timeout is retried on the next run and never emails. Sent once per failure streak, not on every run while it stays down. |
| The whole run crashes (Chrome won't start, SMTP login rejected, …) | GitHub's own "Run failed" email to the repo owner's GitHub account |
| Scheduled runs stop being started at all | Not covered — rare, but if no emails arrive for days, check the Actions tab. |

### Changing the company list

Replace `companies.xlsx` on the **`whistler-data`** branch: on GitHub,
switch the branch selector to `whistler-data` → **Add file** → **Upload
files**, drop in the new `companies.xlsx`, commit. The next run picks it up.

Editing companies in the local dashboard only changes the copy on your own
machine — it does **not** update the one the scheduled runs use.

### Limitations

- **No dashboard.** The Flask dashboard needs an always-on machine. Run it
  locally, or use the VM setup below.
- **Fresh Chrome profile every run.** The scrapers keep a persistent Chrome
  profile so repeat visits look like a returning visitor; on GitHub each run
  starts with an empty one. It hasn't caused blocks so far — if an exchange
  starts failing, this is one of the first things to look at.
- **GitHub's IPs are datacenter IPs.** All six exchanges accept them today;
  that could change, which is what the failure email is for.

## Alternative: always-on Linux VM (systemd + Xvfb)

Use this if you want the dashboard running 24/7 alongside the scheduled
runs. Any Ubuntu 24.04 VM works; `setup_server.sh` detects ARM64 vs x86_64
automatically.

**Free options tried so far:**
- **Oracle Cloud Always Free, Ampere A1** (best free spec). The account's
  home region is India South (Hyderabad), fixed for the life of the account,
  with a free A1 limit of 2 OCPUs / 12 GB. Every create attempt on
  2026-10-08 (2 OCPU / 12 GB and 1 OCPU / 6 GB) failed with "Out of capacity"
  — Hyderabad has a single availability domain, so the only fix is retrying
  later. Use the existing VCN (`vcn-20260902-1229`) — the account is at its
  VCN limit, so the wizard's "create new VCN" option fails.
- **Google Cloud `e2-micro`** (free in `us-west1`/`us-central1`/`us-east1`
  only) — 1 GB RAM, tight for Chrome; `setup_server.sh` adds a 2 GB swap
  file for that reason.

Paid fallback: any ~$5/month VPS with 2 GB+ RAM.

### Setup, once you have SSH access

1. `sudo bash deploy/setup_server.sh` — installs Xvfb, real Google Chrome,
   Python and a swap file, creates `/opt/financial-whistler`, enables the
   firewall (SSH only).
2. Copy the project to `/opt/financial-whistler`, excluding `.venv` and
   `chrome-profile` (rebuilt fresh on the server — Chrome profiles aren't
   portable across machines).
3. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. Copy `.env` over **separately** — never bundle it into the general file
   copy.
5. `bash deploy/install_services.sh` — wires up systemd:
   - `financial-whistler-webapp.service` — the dashboard, always running,
     restarted if it crashes
   - `financial-whistler-run.timer` — the 3×/day run, times written in UTC
     so they're correct whatever the server's timezone
6. Verify: a manual `--dry-run`, a `--force-test-alert`, and the dashboard
   loading.

**If you move the scheduled runs to the VM, disable the GitHub Actions
schedule** (Actions → Financial Whistler → ⋯ → Disable workflow) and copy
`state/seen.json` from the `whistler-data` branch to the server first —
otherwise both would alert on the same filings.

**Dashboard access:** bound to `127.0.0.1` by default. Reach it through an
SSH tunnel: `ssh -L 5057:127.0.0.1:5057 ubuntu@<server-ip>`, then open
`http://127.0.0.1:5057`. Exposing it publicly (HTTPS via Caddy/certbot plus a
firewall rule) is a deliberate opt-in, not a default.

## Files in this directory

- `setup_server.sh` — one-time OS package install for the VM option
- `install_services.sh` — wires up the systemd units below
- `financial-whistler-webapp.service` — the dashboard
- `financial-whistler-run.service` + `.timer` — the 3×/day scheduled run on a VM

The GitHub Actions workflow itself is `.github/workflows/whistler.yml` at the
repo root.
