import json

import pytest
from loguru import logger

from pickem.obs.log import configure_logging, run_context

SECRET = "sk-live-abcdef0123456789"


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", SECRET)
    monkeypatch.setenv("PICKEM_LOG_CONSOLE", "off")
    directory = tmp_path / "logs"
    configure_logging("pickem", log_dir=directory)
    yield directory
    logger.remove()


def _rows(log_dir):
    logger.complete()
    return [
        json.loads(line)
        for line in (log_dir / "pickem.jsonl").read_text().splitlines()
        if line.strip()
    ]


def test_creates_the_three_sinks(log_dir):
    # Catches a missing sink or incorrect output filename.
    logger.bind(event="probe").info("hello")
    logger.complete()
    assert (log_dir / "pickem.log").exists()
    assert (log_dir / "pickem.jsonl").exists()
    assert (log_dir / "errors.log").exists()


def test_jsonl_rows_are_flat_with_bound_fields_at_top_level(log_dir):
    # Catches serialize=True/nested JSON and bound fields omitted from queries.
    logger.bind(
        event="edge_decided", game_id="cfb-2026-01-BAY-at-AUB", tier="STRONG", delta=2.0
    ).info(
        "league +3.5 vs market +1.5: 2.0 pts toward away"
    )
    row = next(r for r in _rows(log_dir) if r["event"] == "edge_decided")
    assert "record" not in row  # loguru's nested serialize=True format
    assert row["tier"] == "STRONG"
    assert row["delta"] == 2.0
    assert row["message"] == "league +3.5 vs market +1.5: 2.0 pts toward away"


def test_run_context_correlates_every_record_and_brackets_the_run(log_dir):
    # Catches context bindings lost between run boundaries or missing lifecycle records.
    with run_context("test:probe", sport="cfb", season=2026, week=1) as run_id:
        logger.bind(event="edge_decided").info("picked")
    rows = _rows(log_dir)
    assert {r["run_id"] for r in rows} == {run_id}
    assert rows[0]["event"] == "run_started"
    assert rows[-1]["event"] == "run_finished"
    assert rows[-1]["duration_ms"] >= 0
    assert rows[0]["sport"] == "cfb"


def test_run_context_logs_one_error_and_reraises(log_dir):
    # Catches swallowed exceptions and duplicate error records from log-and-throw.
    with pytest.raises(ValueError), run_context("test:boom"):
        raise ValueError("nope")
    rows = _rows(log_dir)
    failures = [r for r in rows if r["level"] == "ERROR"]
    assert len(failures) == 1
    assert failures[0]["event"] == "run_failed"
    assert failures[0]["error"] == "ValueError"


def test_secret_is_redacted_even_when_it_arrives_via_exception_text(log_dir):
    # Catches traceback rendering outside the redaction pass.
    try:
        raise RuntimeError(f"odds api unreachable: https://api/x?apiKey={SECRET}")
    except RuntimeError as exc:
        logger.bind(event="odds_request_failed").opt(exception=True).error(str(exc))
    logger.complete()
    for name in ("pickem.log", "pickem.jsonl", "errors.log"):
        text = (log_dir / name).read_text()
        assert SECRET not in text
    errors = (log_dir / "errors.log").read_text()
    assert "***REDACTED***" in errors
    assert "Traceback (most recent call last)" in errors


def test_debug_reaches_jsonl_but_not_the_text_sink(log_dir):
    # Catches a DEBUG threshold applied to the machine sink or a noisy text sink.
    logger.bind(event="consensus_computed").debug("median of 6 books")
    logger.complete()
    assert "median of 6 books" not in (log_dir / "pickem.log").read_text()
    assert any(r["event"] == "consensus_computed" for r in _rows(log_dir))


def test_configure_is_idempotent(log_dir, tmp_path):
    # Catches handler accumulation that duplicates records after reconfiguration.
    configure_logging("pickem", log_dir=log_dir)
    logger.bind(event="probe").info("once")
    assert sum(r["event"] == "probe" for r in _rows(log_dir)) == 1


def test_stdlib_records_are_intercepted(log_dir):
    # Catches framework/scheduler records bypassing the configured sinks.
    import logging

    logging.getLogger("apscheduler.executors").info("Running job 'refresh'")
    assert any("Running job" in r["message"] for r in _rows(log_dir))


@pytest.mark.parametrize(
    ("env_name", "secret"),
    [
        ("ODDS_API_KEY", "odds-secret-123456"),
        ("CFBD_API_KEY", "cfbd-secret-123456"),
        ("DISCORD_BOT_TOKEN", "discord-secret-123456"),
    ],
)
def test_each_configured_environment_secret_is_redacted(log_dir, monkeypatch, env_name, secret):
    # Catches an omitted configured environment variable from direct substitution redaction.
    monkeypatch.setenv(env_name, secret)
    configure_logging("pickem", log_dir=log_dir)
    logger.bind(event="credential_seen", credential=secret).error("credential observed")
    logger.complete()
    for name in ("pickem.log", "pickem.jsonl", "errors.log"):
        text = (log_dir / name).read_text()
        assert secret not in text
    assert "***REDACTED***" in (log_dir / "pickem.jsonl").read_text()


def test_nested_bound_values_are_redacted_recursively(log_dir):
    # Catches secrets leaking from dict/list/tuple/set values in bound fields.
    logger.bind(
        event="nested_credentials",
        details={
            "url": f"https://api/x?apiKey={SECRET}",
            "items": [SECRET, {"token": SECRET}],
            "tuple": (SECRET,),
            "set": {SECRET},
        },
    ).error("nested payload")
    logger.complete()
    for name in ("pickem.log", "pickem.jsonl", "errors.log"):
        text = (log_dir / name).read_text()
        assert SECRET not in text
    row = next(r for r in _rows(log_dir) if r["event"] == "nested_credentials")
    assert row["details"]["items"][0] == "***REDACTED***"
    assert row["details"]["items"][1]["token"] == "***REDACTED***"


def test_recognizable_url_and_authorization_values_are_redacted(log_dir):
    # Catches unbound credentials that are not equal to configured environment secrets.
    url_key = "inline-url-key-123456"
    bearer = "inline-bearer-token-123456"
    logger.bind(event="request_sent").info(
        f"GET https://api/x?apiKey={url_key}&page=1 Authorization: Bearer {bearer}"
    )
    logger.complete()
    for name in ("pickem.log", "pickem.jsonl", "errors.log"):
        text = (log_dir / name).read_text()
        assert url_key not in text
        assert bearer not in text
    message = next(r for r in _rows(log_dir) if r["event"] == "request_sent")["message"]
    assert "apiKey=***REDACTED***" in message
    assert "Authorization: Bearer ***REDACTED***" in message


@pytest.mark.parametrize(
    ("env_name", "secret"),
    [
        ("ODDS_API_KEY", "odds-trace-secret-123"),
        ("CFBD_API_KEY", "cfbd-trace-secret-123"),
        ("DISCORD_BOT_TOKEN", "discord-trace-secret-123"),
    ],
)
def test_each_secret_is_redacted_from_exception_tracebacks(log_dir, monkeypatch, env_name, secret):
    # Catches traceback text bypassing redaction for any configured secret.
    monkeypatch.setenv(env_name, secret)
    configure_logging("pickem", log_dir=log_dir)
    try:
        raise RuntimeError(f"request failed with {secret}")
    except RuntimeError as exc:
        logger.bind(event="request_failed").opt(exception=True).error(str(exc))
    logger.complete()
    for name in ("pickem.log", "pickem.jsonl", "errors.log"):
        assert secret not in (log_dir / name).read_text()
