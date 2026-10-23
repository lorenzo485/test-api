"""Tests for the JSON log formatter."""

import json
import logging

from test_api.logs import JsonFormatter


def test_formatter_merges_fields_into_one_json_line() -> None:
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, None)
    record.fields = {"path": "/checkout", "latency_ms": 12.5}
    line = JsonFormatter().format(record)
    assert "\n" not in line
    assert json.loads(line) == {
        "severity": "ERROR",
        "message": "boom",
        "path": "/checkout",
        "latency_ms": 12.5,
    }
