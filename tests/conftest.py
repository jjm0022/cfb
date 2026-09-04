import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def isolate_cli_logging(tmp_path, monkeypatch):
    """Keep CLI callback sinks inside each test's temporary directory."""
    monkeypatch.setenv("PICKEM_LOG_DIR", str(tmp_path / "pickem-logs"))
    monkeypatch.setenv("PICKEM_LOG_CONSOLE", "off")
    yield
    logger.complete()
    logger.remove()


@pytest.fixture
def records():
    """Capture loguru records as dicts. Loguru does not route through caplog."""
    captured: list[dict] = []
    logger.complete()
    logger.remove()
    sink_id = logger.add(lambda message: captured.append(message.record), level="DEBUG")
    yield captured
    logger.complete()
    logger.remove(sink_id)
