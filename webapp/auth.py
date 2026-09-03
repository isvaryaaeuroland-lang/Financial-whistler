"""Login-based auth for the dashboard.

Required regardless of bind address (not just when exposed beyond
localhost) since the user asked for this specifically ahead of eventually
opening the dashboard to the network -- better to have it proven working
locally first than to bolt it on at the same moment as an exposure change.

Credentials come from .env (DASHBOARD_USERNAME / DASHBOARD_PASSWORD) --
plaintext there, consistent with how SMTP_PASSWORD already works in this
project; .env is this app's existing trust boundary for secrets, not a new
one. The app refuses to start if they're not set, rather than falling back
to something insecure silently.

The Flask session-signing secret key is generated once (a real random
value, not the placeholder string this file used to hardcode) and persisted
to .env so sessions survive restarts instead of invalidating every reload.
"""

import functools
import os
import secrets

from flask import redirect, request, session, url_for

ENV_FILE_MARKER_USERNAME = "DASHBOARD_USERNAME"
ENV_FILE_MARKER_PASSWORD = "DASHBOARD_PASSWORD"
ENV_FILE_MARKER_SECRET = "FLASK_SECRET_KEY"


def get_credentials() -> tuple[str, str] | None:
    username = os.environ.get(ENV_FILE_MARKER_USERNAME, "").strip()
    password = os.environ.get(ENV_FILE_MARKER_PASSWORD, "")
    if not username or not password:
        return None
    return username, password


def check_login(username: str, password: str) -> bool:
    creds = get_credentials()
    if not creds:
        return False
    expected_user, expected_pass = creds
    # Constant-time comparison so response timing can't leak how much of the
    # guess was correct.
    return secrets.compare_digest(username, expected_user) and secrets.compare_digest(
        password, expected_pass
    )


def get_or_create_secret_key(env_path) -> str:
    """Reads FLASK_SECRET_KEY from .env, generating and persisting a new
    random one on first run so it survives restarts."""
    existing = os.environ.get(ENV_FILE_MARKER_SECRET, "").strip()
    if existing:
        return existing

    new_key = secrets.token_hex(32)
    line = f"{ENV_FILE_MARKER_SECRET}={new_key}\n"
    if env_path.exists():
        with open(env_path, "a", encoding="utf-8") as f:
            f.write(("\n" if not env_path.read_text(encoding="utf-8").endswith("\n") else "") + line)
    else:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(line)
    os.environ[ENV_FILE_MARKER_SECRET] = new_key
    return new_key


def login_required_before_request():
    """Call from an app.before_request hook. Returns a redirect response if
    the request needs to be sent to the login page, or None to proceed."""
    if request.endpoint in ("login", "static"):
        return None
    if not session.get("logged_in"):
        return redirect(url_for("login", next=request.path))
    return None


def login_required(view):
    """Optional per-route decorator, for routes that need protecting even if
    the before_request hook is ever bypassed/refactored. Belt and suspenders,
    not the primary mechanism."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped
