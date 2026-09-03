# Deploying to a server

This replaces "runs on my Mac via launchd, only while it's on" with
"runs on a server 24/7." The whole plan hinges on one thing that's already
been verified: three exchanges (Tadawul, Boursa Kuwait, NSE India) block
headless Chromium, so the server needs a real, non-headless Chrome window
to open into -- **Xvfb** (a virtual display) provides that without needing
an actual monitor. This was tested live against Tadawul's bot-protected
endpoint from an ARM64 Ubuntu container running Xvfb + real Chrome and got
a clean 200 with real data, not a 403 -- so the core assumption holds.

## Part 1 -- things only you can do

I can't create a cloud account or enter payment/identity details on your
behalf, even for a free tier. This part is on you.

**Current path: Google Cloud's `e2-micro`** (switched to this after Oracle
Cloud sign-up didn't go through -- also free forever, not a trial):

1. **Sign up for Google Cloud** at [cloud.google.com/free](https://cloud.google.com/free)
   (needs card verification too, same as any provider's free tier -- you're
   not charged unless you explicitly upgrade off the free tier later).
2. **Create a Compute Engine VM instance**: machine type `e2-micro`,
   region **must** be one of `us-west1`, `us-central1`, or `us-east1` --
   that's the "always free" eligibility rule, other regions get billed.
   Ubuntu 24.04 LTS image. Check the "Free tier eligible" indicator the
   console shows you before creating it, to confirm you're in the free
   configuration. Allow HTTP/SSH firewall rules if offered (SSH at least).
3. Note the instance's external IP address.
4. Either use GCP's built-in "SSH in browser" once to confirm it boots, or
   add your own SSH public key to the instance metadata so I can connect.

Once you have that (a running VM + SSH access), the rest is mine to drive.

One real constraint worth knowing up front: `e2-micro` only has **1GB RAM**
(vs. the 12GB Oracle's ARM tier offers), and Chrome is the heaviest thing
this tool runs. `setup_server.sh` now provisions a 2GB swap file as a
safety net against that, and the scrapers already run exchanges
sequentially (never multiple Chrome instances at once), which helps. If it
still turns out too tight in practice, the fallback is either resizing to
a small paid tier (a couple dollars/month) or revisiting Oracle once
whatever blocked sign-up there is sorted out -- not a dead end either way.

## Part 2 -- what happens once you have SSH access

Give me the server's IP and confirm SSH access works
(`ssh ubuntu@<ip>` or whichever user the image uses), and I'll:

1. Run `deploy/setup_server.sh` to install Xvfb, real Google Chrome, and Python
2. Copy the project over (excluding your local `.venv` and `chrome-profile`,
   which get rebuilt fresh on the server -- Chrome profiles aren't portable
   across machines/OSes anyway, and macOS's app-store Chrome vs Linux's
   apt Chrome are different binaries)
3. Set up the Python venv and install dependencies
4. Copy your `.env` over **separately and directly** (SMTP credentials,
   dashboard login, `FLASK_SECRET_KEY`) -- deliberately never bundled into
   the general file copy, so it doesn't end up in any staging step by
   accident
5. Run `deploy/install_services.sh` to wire up systemd (the Linux
   equivalent of your two launchd `.plist` files):
   - `financial-whistler-webapp.service` -- the dashboard, always running,
     restarts itself if it crashes (like `KeepAlive` in the old plist)
   - `financial-whistler-run.timer` -- the 3x/day scheduled run, same
     wall-clock times as your current schedule (07:30 / 10:00 / 13:00 UTC,
     which is 13:00 / 15:30 / 18:30 IST -- see the `.timer` file's comment
     for the conversion)
6. Verify: trigger a manual test run, confirm an alert email sends, confirm
   the dashboard loads

## What's different from the Mac setup

- **No LaunchAgent GUI-session requirement.** launchd needed
  `LimitLoadToSessionType: Aqua` because the off-screen Chrome trick still
  needs a real window-server session on macOS. Xvfb *is* that "window
  server" on Linux, running as part of each service's own startup (via
  `xvfb-run`), so the server doesn't need anyone logged in at all --
  it just needs to be powered on.
- **No visible-window disruption to worry about.** All of today's work on
  hiding/minimizing the Chrome window existed because a human was sitting
  at that Mac and could see it flash. Nobody's looking at this server's
  display, ever -- it's a headless box with a virtual framebuffer, so that
  whole class of problem doesn't apply here. The minimize-via-CDP code
  stays in (harmless either way), but there's nothing left for it to fix.
- **Dashboard access.** Deployed bound to `127.0.0.1` only, same safe
  default as your Mac. Reach it via an SSH tunnel:
  `ssh -L 5057:127.0.0.1:5057 ubuntu@<server-ip>`, then open
  `http://127.0.0.1:5057` in your own browser. If you'd rather have it
  reachable directly (e.g. from your phone without SSH), say so and I'll
  add HTTPS (via Caddy or certbot) and a firewall rule for it -- that's a
  deliberate opt-in, not a default, since it widens what's exposed to the
  internet.
- **Timezone-explicit schedule.** The systemd timer specifies UTC times
  directly rather than relying on the server's configured local timezone,
  so the schedule is correct regardless of how the Ubuntu image has
  `timedatectl` set.

## Files in this directory

- `setup_server.sh` -- one-time OS package install (Xvfb, Chrome, Python)
- `install_services.sh` -- wires up the two systemd units below
- `financial-whistler-webapp.service` -- the dashboard
- `financial-whistler-run.service` + `.timer` -- the 3x/day scheduled run
