"""Team logos for the dashboard, downloaded once and served beside the page.

College logos come from CFBD's team list, which also has versions drawn for
dark backgrounds. NFL logos come from ESPN's logo address. The page never
fetches a logo from outside: it only names files listed here as saved.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from pickem.atomic import write_atomic
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

LOGO_DIR = "logos"
CFBD_SIZE = "96"  # pixels; sharp at the page's 28-44px on a phone screen
ESPN_NFL = "https://a.espncdn.com/i/teamlogos/nfl/500/{code}.png"
# Every other NFL id is ESPN's code in lower case.
ESPN_CODES = {"WAS": "wsh"}
PNG_SIGNATURE = b"\x89PNG"


@dataclass(frozen=True)
class LogoSource:
    sport: Sport
    team_id: str
    url: str
    dark: bool = False

    @property
    def relative_path(self) -> str:
        suffix = "-dark" if self.dark else ""
        return f"{self.sport.value}/{self.team_id}{suffix}.png"


@dataclass
class LogoDownload:
    saved: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def cfb_sources(
    teams: list[dict], resolver: TeamResolver
) -> tuple[list[LogoSource], list[str]]:
    """Light and dark sources for each CFBD team the alias table knows, and the schools it lacks."""
    sources: list[LogoSource] = []
    unmatched: list[str] = []
    for team in teams:
        try:
            team_id = resolver.resolve(team["school"], Sport.CFB)
        except UnknownTeamError:
            unmatched.append(team["school"])
            continue
        logos = team.get("logos") or []
        for dark, folder in ((False, "logos"), (True, "logos-dark")):
            url = next((u for u in logos if f"/{folder}/{CFBD_SIZE}/" in u), None)
            if url is not None:
                sources.append(LogoSource(Sport.CFB, team_id, url, dark))
    return sources, unmatched


def nfl_sources(team_ids: Iterable[str]) -> list[LogoSource]:
    return [
        LogoSource(
            Sport.NFL, team_id, ESPN_NFL.format(code=ESPN_CODES.get(team_id, team_id.lower()))
        )
        for team_id in team_ids
    ]


def download_logos(
    sources: Iterable[LogoSource], logo_dir: Path, fetch: Callable[[str], bytes]
) -> LogoDownload:
    """Save each missing logo; keep any already saved. One failure never stops the rest."""
    outcome = LogoDownload()
    for source in sources:
        path = logo_dir / source.relative_path
        if path.exists():
            outcome.kept.append(source.relative_path)
            continue
        try:
            data = fetch(source.url)
            if not data.startswith(PNG_SIGNATURE):
                raise ValueError("not a PNG image")
            write_atomic(path, data)
        except Exception as exc:  # a missing or broken logo only costs that one team
            outcome.failed.append(f"{source.relative_path}: {exc}")
            continue
        outcome.saved.append(source.relative_path)
    return outcome


def available_logos(logo_dir: Path) -> dict:
    """Team ids with a saved logo, by theme then board, in the shape the page reads."""
    listing: dict[str, dict[str, list[str]]] = {
        "light": {s.value: [] for s in (Sport.CFB, Sport.NFL)},
        "dark": {s.value: [] for s in (Sport.CFB, Sport.NFL)},
    }
    for sport in (Sport.CFB, Sport.NFL):
        folder = logo_dir / sport.value
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.png")):
            dark = path.stem.endswith("-dark")
            team_id = path.stem.removesuffix("-dark")
            listing["dark" if dark else "light"][sport.value].append(team_id)
    return listing


def fetch_logo(url: str) -> bytes:
    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.content
