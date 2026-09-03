#!/usr/bin/env bash
# Installs both systemd units (replaces the two macOS launchd .plist files):
#
# - financial-whistler-webapp.service: a normal SYSTEM unit (needs root to
#   manage, but nothing in the app ever needs to toggle it at runtime, so
#   that's fine).
# - financial-whistler-run.timer/.service: a USER unit instead, deliberately.
#   The dashboard's "Turn schedule on/off" button needs to toggle this timer
#   from the webapp's own (unprivileged) process -- a system unit would need
#   sudo for every single click, which the running webapp doesn't have. A
#   user unit lets it just run `systemctl --user enable/disable --now`
#   directly, no privilege escalation needed at runtime.
#
#   The catch: user units normally stop when the owning user's last login
#   session ends -- fine on a desktop, wrong for a headless server where
#   nobody's ever logged in. `loginctl enable-linger` (root-only, done once
#   below) makes the user's systemd instance persist regardless, exactly
#   like a system service would.
#
# Run as: sudo bash deploy/install_services.sh

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo: sudo bash deploy/install_services.sh" >&2
  exit 1
fi

APP_USER="${SUDO_USER:?Run with sudo as the app user, e.g.: sudo bash deploy/install_services.sh}"
APP_DIR="/opt/financial-whistler"
APP_UID="$(id -u "$APP_USER")"

echo "==> Installing financial-whistler-webapp.service (system unit)..."
sed -e "s/%i/${APP_USER}/g" -e "s/__APP_UID__/${APP_UID}/g" \
  "$APP_DIR/deploy/financial-whistler-webapp.service" > /etc/systemd/system/financial-whistler-webapp.service
systemctl daemon-reload
systemctl enable --now financial-whistler-webapp.service

echo "==> Enabling lingering for $APP_USER (lets their user services run with nobody logged in)..."
loginctl enable-linger "$APP_USER"

echo "==> Installing financial-whistler-run.service/.timer (user units, run as $APP_USER)..."
USER_UNIT_DIR="/home/$APP_USER/.config/systemd/user"
sudo -u "$APP_USER" mkdir -p "$USER_UNIT_DIR"
cp "$APP_DIR/deploy/financial-whistler-run.service" "$USER_UNIT_DIR/"
cp "$APP_DIR/deploy/financial-whistler-run.timer" "$USER_UNIT_DIR/"
chown "$APP_USER":"$APP_USER" "$USER_UNIT_DIR"/financial-whistler-run.{service,timer}

sudo -u "$APP_USER" XDG_RUNTIME_DIR="/run/user/$APP_UID" systemctl --user daemon-reload
sudo -u "$APP_USER" XDG_RUNTIME_DIR="/run/user/$APP_UID" systemctl --user enable --now financial-whistler-run.timer

echo
echo "==> Installed and started. Check status with:"
echo "    systemctl status financial-whistler-webapp"
echo "    sudo -u $APP_USER XDG_RUNTIME_DIR=/run/user/$APP_UID systemctl --user list-timers financial-whistler-run.timer"
echo "    journalctl -u financial-whistler-webapp -f"
echo
echo "The dashboard's schedule on/off toggle now works too (it calls"
echo "'systemctl --user' directly as the webapp's own user, no sudo needed)."
