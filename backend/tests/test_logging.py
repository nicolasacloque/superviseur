import json
import logging
import sys

from app.common.logging import JsonFormatter


def _record(
    message: str, args: tuple[str, ...] = (), *, exc_info: bool = False
) -> logging.LogRecord:
    return logging.LogRecord(
        "app.test",
        logging.ERROR if exc_info else logging.INFO,
        __file__,
        1,
        message,
        args,
        sys.exc_info() if exc_info else None,
    )


def test_json_formatter_outputs_one_json_line() -> None:
    payload = json.loads(JsonFormatter().format(_record("bonjour %s", ("monde",))))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["msg"] == "bonjour monde"
    assert "ts" in payload


def test_json_formatter_includes_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        line = JsonFormatter().format(_record("échec", exc_info=True))

    assert "ValueError: boom" in json.loads(line)["exc"]
