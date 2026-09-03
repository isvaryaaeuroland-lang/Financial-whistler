"""Chrome persistent-profile launch helpers: stale-lock cleanup and hiding
the window.

Chrome refuses to open a persistent profile if it sees a SingletonLock left
by a previous instance -- normally harmless, since Chrome checks whether
that process is still alive and cleans up automatically. But if the previous
process was killed hard enough (SIGKILL, or the machine losing power/sleep
mid-launch) that cleanup doesn't happen reliably, and the next launch can
fail outright with Playwright reporting a confusing
"Target page, context or browser has been closed" rather than a clear
lock-related error.

`clear_stale_lock` checks the lock's referenced PID before every
persistent-profile launch and removes the lock files if that process isn't
actually running, so a crashed prior run doesn't take out the next
search/scrape too.

`hide_window` covers a second, unrelated problem: several of these launches
use `--window-position=-2400,-2400` (an off-screen coordinate) to keep the
required real, non-headless Chrome window out of the user's way. That flag
turns out not to be honored at all on macOS -- verified live 2026-08-26 by
launching with the exact same args and reading the resulting window bounds
back via CDP: Chrome placed the window at (0, 34), a fully visible, focused
position at the top-left of the screen, not off-screen. That's a real
Chrome window opening on top of whatever the user is doing every time a
search or scrape runs -- confusing and disruptive, and the actual cause
behind reports of the dashboard "redirecting" to an exchange site.
`Browser.setWindowBounds` via CDP does work, unlike the command-line flag,
so this minimizes the window right after the page is created instead of
relying on positioning -- verified the page still navigates and evaluates
JS normally while minimized. Harmless to leave running on Linux (a headless
server has no window to steal focus in the first place -- verified live
against Tadawul from an Xvfb + real Chrome container before this ever ran
on a real server), so there's no platform check needed for that part.

`profile_base()` covers the one genuinely platform-specific thing: where
these Chrome profiles live. macOS deliberately keeps them outside the
project folder, in ~/Library/Application Support -- see the scrapers' own
comments for why (a live Chrome profile inside a OneDrive-synced folder
caused severe multi-minute hangs). That's a macOS/OneDrive-specific
problem; a Linux server has no such cloud-sync folder, so on Linux the
profiles just live under the project directory like everything else.
"""

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")


def profile_base() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FinancialWhistler" / "chrome-profile"
    return Path(__file__).resolve().parent.parent / "chrome-profile"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clear_stale_lock(profile_dir: Path) -> None:
    lock_path = profile_dir / "SingletonLock"
    if not lock_path.is_symlink():
        return

    target = os.readlink(lock_path)
    pid_str = target.rsplit("-", 1)[-1]
    if pid_str.isdigit() and _pid_alive(int(pid_str)):
        return  # a real, running Chrome owns this profile -- leave it alone

    log.warning(
        "Removing stale Chrome lock in %s (owner PID %s not running)", profile_dir, pid_str
    )
    for name in _LOCK_FILES:
        (profile_dir / name).unlink(missing_ok=True)


def hide_window(context, page) -> None:
    """Minimizes the real Chrome window a persistent context just opened.
    Call once, right after the first page is created. Best-effort: if CDP
    fails for any reason, log and continue rather than blocking the caller
    on a purely cosmetic step."""
    try:
        cdp = context.new_cdp_session(page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "minimized"}})
    except Exception:
        log.warning("Could not minimize the Chrome window -- it may be visible on screen", exc_info=True)
