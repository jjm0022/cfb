"""Environment-sourced settings. Secrets never live in the repo."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DB = Path("data/pickem.duckdb")


def odds_api_key() -> str:
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("ODDS_API_KEY is not set")
    return key


def cfbd_api_key() -> str:
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        raise RuntimeError("CFBD_API_KEY is not set")
    return key
