import pytest
from loguru import logger


@pytest.fixture
def records():
    """Capture loguru records as dicts. Loguru does not route through caplog."""
    captured: list[dict] = []
    logger.remove()
    sink_id = logger.add(lambda message: captured.append(message.record), level="DEBUG")
    yield captured
    logger.remove(sink_id)
