"""Logging configuration. See the logging-standards skill before changing this."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

# Values of these environment variables are redacted wherever they appear.
_SECRET_ENV_VARS = ("ODDS_API_KEY", "CFBD_API_KEY", "DISCORD_BOT_TOKEN")
_REDACTED = "***REDACTED***"
_MIN_SECRET_LEN = 8  # never redact a short value; it would blank ordinary text

_TEXT_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[run_id]} | "
    "{extra[event]} | {message}"
)

# These patterns cover credentials that can reach an exception or log message
# before configuration has seen them in the environment. They intentionally
# preserve the key/scheme so the resulting message remains useful to humans.
_API_KEY_QUERY = re.compile(r"(?i)([?&]apiKey=)([^&#\s\"']+)")
_AUTHORIZATION = re.compile(
    r"(?i)(\bAuthorization\s*(?:[:=]\s*)?(?:(?:Bearer|Basic|Token)\s+)?)([^\s,;\"']+)"
)
_AUTH_SCHEME = re.compile(r"(?i)(\b(?:Bearer|Basic|Token)\s+)([^\s,;\"']+)")


def _secret_values() -> tuple[str, ...]:
    found = (os.environ.get(name) for name in _SECRET_ENV_VARS)
    return tuple(v for v in found if v and len(v) >= _MIN_SECRET_LEN)


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, _REDACTED)

    text = _API_KEY_QUERY.sub(r"\1" + _REDACTED, text)
    text = _AUTHORIZATION.sub(r"\1" + _REDACTED, text)
    return _AUTH_SCHEME.sub(r"\1" + _REDACTED, text)


def _redact_value(value: object, secrets: tuple[str, ...]) -> object:
    """Redact strings inside the standard containers used as bound fields."""
    if isinstance(value, str):
        return _redact(value, secrets)
    if isinstance(value, dict):
        return {
            _redact_value(key, secrets): _redact_value(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, secrets) for item in value)
    if isinstance(value, set):
        return {_redact_value(item, secrets) for item in value}
    return value


def _flatten(record: dict) -> dict:
    payload = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "event": record["extra"].get("event", "-"),
        "message": record["message"],
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
    }
    # Bound fields win the outer keys; underscore-prefixed keys stay internal.
    payload.update(
        {k: v for k, v in record["extra"].items() if not k.startswith("_")}
    )
    return payload


def _make_patcher(secrets: tuple[str, ...]):
    def patch(record: dict) -> None:
        if record["exception"] is not None:
            kind, value, tb = record["exception"]
            rendered = "".join(traceback.format_exception(kind, value, tb))
            record["message"] = f"{record['message']}\n{rendered.rstrip()}"
            # Cleared so loguru does not render it again outside redaction.
            record["exception"] = None

        record["message"] = _redact(record["message"], secrets)
        for key, value in record["extra"].items():
            if not key.startswith("_"):
                record["extra"][key] = _redact_value(value, secrets)

        record["extra"]["_json"] = json.dumps(_flatten(record), default=str)

    return patch


class _InterceptHandler(logging.Handler):
    """Route standard-library records (discord.py, apscheduler, httpx) to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).bind(
            event=f"{record.name.split('.')[0]}_log"
        ).log(level, record.getMessage())


def configure_logging(
    project: str = "pickem",
    *,
    log_dir: Path | str | None = None,
    level: str | None = None,
    console: str | None = None,
) -> Path:
    """Install the standard sinks. Call once, from an entry point only.

    Idempotent: a second call replaces the handlers rather than duplicating
    them. Returns the directory the sinks were written to.
    """
    prefix = project.upper().replace("-", "_")
    directory = Path(
        log_dir
        or os.environ.get(f"{prefix}_LOG_DIR")
        or Path.home() / "LOGS" / project
    ).expanduser()
    directory.mkdir(parents=True, exist_ok=True)

    level = (level or os.environ.get(f"{prefix}_LOG_LEVEL") or "DEBUG").upper()
    console = console or os.environ.get(f"{prefix}_LOG_CONSOLE") or "WARNING"

    logger.remove()
    logger.configure(
        extra={"run_id": "-", "event": "-"},
        patcher=_make_patcher(_secret_values()),
    )

    # backtrace=False because the patcher already folded the traceback into the
    # message; diagnose=False because it would expand locals holding secrets.
    common = {"enqueue": True, "backtrace": False, "diagnose": False, "catch": True}
    rotating = {"rotation": "00:00", "compression": "zip"}

    logger.add(
        directory / f"{project}.log",
        level="INFO",
        format=_TEXT_FORMAT,
        retention="30 days",
        **rotating,
        **common,
    )
    logger.add(
        directory / f"{project}.jsonl",
        level=level,
        format="{extra[_json]}",
        retention="90 days",
        **rotating,
        **common,
    )
    logger.add(
        directory / "errors.log",
        level="ERROR",
        format=_TEXT_FORMAT,
        retention="180 days",
        **rotating,
        **common,
    )
    if console.lower() != "off":
        logger.add(sys.stderr, level=console.upper(), format=_TEXT_FORMAT, **common)

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    return directory


@contextmanager
def run_context(entry: str, **facts: object) -> Iterator[str]:
    """Bind a run identifier and the run's facts to every record beneath it.

    Backed by context variables, so the binding follows the work into asyncio
    tasks and ``asyncio.to_thread`` workers without passing a logger around.
    """
    run_id = uuid.uuid4().hex[:8]
    with logger.contextualize(run_id=run_id, entry=entry, **facts):
        started = time.monotonic()
        logger.bind(event="run_started").info(f"{entry} started")
        try:
            yield run_id
        except Exception as exc:
            logger.bind(
                event="run_failed",
                error=type(exc).__name__,
                duration_ms=round((time.monotonic() - started) * 1000),
            ).opt(exception=True).error(f"{entry} failed: {exc}")
            raise
        logger.bind(
            event="run_finished",
            duration_ms=round((time.monotonic() - started) * 1000),
        ).info(f"{entry} finished")
