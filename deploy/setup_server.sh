#!/usr/bin/env bash
# One-time bootstrap for a fresh Ubuntu 24.04 server -- works on either of
# the two free-forever options this was evaluated against: Oracle Cloud's
# Ampere A1 (ARM64) or Google Cloud's e2-micro (x86_64). Architecture is
# detected automatically (dpkg --print-architecture), so this script itself
# doesn't change between them.
#
# Installs everything Financial Whistler needs to run headless: Xvfb (a
# virtual display so the three exchanges that block headless Chromium --
# Tadawul, Boursa Kuwait, NSE -- see a real non-headless Chrome, same as
# verified live against Tadawul from an ARM64 Ubuntu container before this
# ever ran on a real VM), real Google Chrome (not just Chromium -- that's
# specifically what was verified to get past the bot protection), a swap
# file (see below), and the Python venv.
#
# Run as: sudo bash setup_server.sh
# Idempotent -- safe to re-run.

set -euo pipefail

APP_USER="${SUDO_USER:-$(whoami)}"
APP_DIR="/opt/financial-whistler"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo: sudo bash setup_server.sh" >&2
  exit 1
fi

echo "==> Setting up a swap file (matters most on GCP's e2-micro, which"
echo "    only has 1GB RAM -- Chrome plus Python plus the OS baseline can"
echo "    get close to that on its own; 2GB of swap is cheap insurance"
echo "    against an OOM kill mid-scrape. Harmless on a bigger box too.)"
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
free -h

echo "==> Updating apt and installing base packages..."
apt-get update
apt-get install -y \
  wget gnupg curl xvfb ufw \
  python3 python3-venv python3-pip \
  fonts-liberation libnss3 libxss1

echo "==> Installing real Google Chrome (not Chromium -- required, see README)..."
if ! command -v google-chrome-stable >/dev/null 2>&1; then
  ARCH="$(dpkg --print-architecture)"
  if [ "$ARCH" = "arm64" ]; then
    CHROME_URL="https://dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb"
  else
    CHROME_URL="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
  fi
  wget -q -O /tmp/chrome.deb "$CHROME_URL"
  apt-get install -y /tmp/chrome.deb
  rm -f /tmp/chrome.deb
fi
google-chrome-stable --version

echo "==> Creating $APP_DIR (owned by $APP_USER)..."
mkdir -p "$APP_DIR"
chown "$APP_USER":"$APP_USER" "$APP_DIR"

echo "==> Firewall: allow SSH, keep everything else closed by default..."
ufw allow OpenSSH || true
ufw --force enable || true
echo "    (Dashboard stays on 127.0.0.1 -- reach it via an SSH tunnel, not"
echo "    a public port. See deploy/README.md if you want it exposed instead.)"

cat <<EOF

==> System packages installed. Next steps (run as $APP_USER, not root):

1. Copy the project into $APP_DIR (from your Mac, e.g.):
     rsync -avz --exclude='.venv' --exclude='chrome-profile' \\
       "/path/to/Financial whistler/" $APP_USER@<server-ip>:$APP_DIR/

2. cd $APP_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
3. .venv/bin/playwright install-deps chromium   # system libs Chrome needs
4. Copy your .env over (it has your SMTP + dashboard credentials) --
   NOT included in the rsync above on purpose, copy it separately.
5. Run deploy/install_services.sh to wire up systemd (replaces launchd).

See deploy/README.md for the full walkthrough.
EOF
