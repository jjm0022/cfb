"""Environment-sourced settings. Secrets never live in the repo.

A `.env` at the project root is loaded once, on import, so the CLI picks up
keys without the caller having to export them. Real environment variables win:
`load_dotenv` is called with `override=False`, so an exported key — or a value
a test sets with `monkeypatch.setenv` — is never clobbered by the file.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Saved CBS pages and the reports built from them live on the NAS, so a page
# saved from any machine is readable here without an rsync. The live database
# stays local on purpose: the share is CIFS, which does not enforce DuckDB's
# lock — two writers can open it at once and corrupt it with no error.
NAS_MOUNT = Path("/mnt/nas")
NAS_DIR = NAS_MOUNT / "Betting" / "pickem"
DEFAULT_DB = Path("data/pickem.duckdb")
DEFAULT_RESULTS_DIR = NAS_DIR / "results"
DEFAULT_WEEKS_DIR = NAS_DIR / "weeks"
# The owner's display name on the CBS standings page.
DEFAULT_ENTRY_NAME = "Jota"

# usecwd so the search starts where the command was run, not where this module
# happens to be installed.
load_dotenv(find_dotenv(usecwd=True), override=False)

# The pool's CBS page, and the Chrome profile logged in to it. The profile is
# dedicated to the scheduled fetch: Chrome locks a profile in use and refuses
# remote debugging on the everyday default one. Log in again with
# scripts/cbs-login.sh.
CBS_POOL_URL = os.environ.get(
    "PICKEM_CBS_POOL_URL",
    "https://picks.cbssports.com/football/pickem/pools/kbxw63b2ge3dkobtge2tq===",
)
CBS_CHROME_PROFILE = Path(
    os.environ.get("PICKEM_CBS_CHROME_PROFILE", "~/.local/share/pickem/cbs-chrome")
).expanduser()
CHROME_BINARY = Path(os.environ.get("PICKEM_CHROME_BINARY", "/opt/google/chrome/chrome"))


def nas_unavailable(path: Path) -> str | None:
    """Say why a path on the share cannot be used, or ``None`` if it can.

    A missing file on a mounted share is an ordinary missing file and returns
    ``None`` — the caller's own message is the useful one. This exists for the
    other case, where the share itself is gone: `mkdir -p` under a cold mount
    can otherwise build a local tree that shadows the real one, and a report
    written there disappears from view the moment the share comes back.

    Reads the module globals on each call so a caller can repoint them.
    """
    nas_dir = NAS_DIR
    if path != nas_dir and nas_dir not in path.parents:
        return None
    try:
        if nas_dir.is_dir():
            return None
    except OSError:
        pass  # A stalled share raises rather than answering; treat it as gone.
    return (
        f"{nas_dir} is not available — the NAS is offline or not mounted, so "
        f"reading or writing there would not reach it. Check the mount with "
        f"`mountpoint {NAS_MOUNT}`, then retry; pass an explicit path to work "
        f"somewhere else in the meantime."
    )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; export it or add it to a .env file")
    return value


def odds_api_key() -> str:
    return _required("ODDS_API_KEY")


def cfbd_api_key() -> str:
    return _required("CFBD_API_KEY")


def discord_bot_token() -> str:
    """Return the Discord token using the shared required-value policy."""
    return _required("DISCORD_BOT_TOKEN")


def discord_owner_id() -> int:
    """Return the configured Discord owner ID."""
    return int(_required("DISCORD_OWNER_ID"))


def dashboard_dir() -> Path:
    """Where the results dashboard pages are written.

    Local, not on the NAS, so the page stays reachable when the share is
    offline. Read on each call so tests and one-off runs can repoint it.
    """
    value = os.environ.get("PICKEM_DASHBOARD_DIR") or "~/.local/share/pickem/dashboard"
    return Path(value).expanduser()


def dashboard_url() -> str | None:
    """The dashboard's base address for the results DM, or ``None`` to omit the link."""
    value = (os.environ.get("PICKEM_DASHBOARD_URL") or "").strip()
    if not value:
        return None
    return value if value.endswith("/") else value + "/"
