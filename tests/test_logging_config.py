import json
import logging

from repolens.logging_config import JsonFormatter


def test_json_formatter_merges_structured_message() -> None:
    record = logging.LogRecord(
        name="repolens.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg='{"event":"tool_call","duration_ms":12}',
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "tool_call"
    assert payload["duration_ms"] == 12
    assert payload["level"] == "INFO"
    assert payload["logger"] == "repolens.test"


def test_json_formatter_handles_plain_message() -> None:
    record = logging.LogRecord(
        name="repolens.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=20,
        msg="plain message",
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "plain message"
