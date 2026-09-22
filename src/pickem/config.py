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
NAS_DIR = Path("/mnt/nas/Betting/pickem")
DEFAULT_DB = Path("data/pickem.duckdb")
DEFAULT_RESULTS_DIR = NAS_DIR / "results"
DEFAULT_WEEKS_DIR = NAS_DIR / "weeks"
# The owner's display name on the CBS standings page.
DEFAULT_ENTRY_NAME = "Jota"

# usecwd so the search starts where the command was run, not where this module
# happens to be installed.
load_dotenv(find_dotenv(usecwd=True), override=False)


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
