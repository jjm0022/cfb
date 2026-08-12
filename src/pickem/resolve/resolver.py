"""Canonical team identity across sources.

Fail-loud by design: an unrecognized name raises rather than guessing. Fuzzy
matching exists only to suggest an alias to a human in the error message, and
never resolves data.
"""

from __future__ import annotations

import difflib
from importlib import resources
from pathlib import Path

import yaml

from pickem.models import Sport


class UnknownTeamError(LookupError):
    """A team name appeared that is not in aliases.yaml."""


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


class TeamResolver:
    def __init__(self, mapping: dict[Sport, dict[str, str]]) -> None:
        # mapping: sport -> normalized alias -> team_id
        self._mapping = mapping

    @classmethod
    def from_text(cls, text: str) -> TeamResolver:
        return cls._build(yaml.safe_load(text))

    @classmethod
    def from_yaml(cls, path: Path) -> TeamResolver:
        return cls.from_text(path.read_text())

    @classmethod
    def default(cls) -> TeamResolver:
        source = resources.files("pickem.resolve").joinpath("aliases.yaml")
        return cls.from_text(source.read_text())

    @classmethod
    def _build(cls, raw: dict) -> TeamResolver:
        mapping: dict[Sport, dict[str, str]] = {}
        for sport_key, teams in (raw or {}).items():
            sport = Sport(sport_key)
            table: dict[str, str] = {}
            for team_id, aliases in teams.items():
                for alias in [team_id, *aliases]:
                    key = _normalize(alias)
                    existing = table.get(key)
                    if existing is not None and existing != team_id:
                        raise ValueError(
                            f"alias {alias!r} is claimed by both {existing} and {team_id} "
                            f"in {sport_key}"
                        )
                    table[key] = team_id
            mapping[sport] = table
        return cls(mapping)

    def resolve(self, name: str, sport: Sport) -> str:
        table = self._mapping.get(sport, {})
        key = _normalize(name)
        team_id = table.get(key)
        if team_id is not None:
            return team_id
        raise UnknownTeamError(self._error_message(name, sport, table))

    @staticmethod
    def _error_message(name: str, sport: Sport, table: dict[str, str]) -> str:
        close = difflib.get_close_matches(_normalize(name), table.keys(), n=3, cutoff=0.6)
        msg = f"unknown {sport.value} team {name!r}"
        if close:
            hints = ", ".join(f"{c!r} -> {table[c]}" for c in close)
            msg += f"; did you mean {hints}? Add the spelling to aliases.yaml"
        else:
            msg += "; add it to aliases.yaml"
        return msg
