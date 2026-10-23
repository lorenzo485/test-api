"""Structured JSON logging for Cloud Logging.

Cloud Run ships every stdout line to Cloud Logging, which parses a JSON line
into `jsonPayload` and promotes `severity`. Log-based metrics in terraform-sre
read these fields by name, so renaming one silently breaks the SLOs.
"""

import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON line, merging its `fields` extra."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize severity, message, and structured fields.

        Args:
            record: The record to render.

        Returns:
            A single-line JSON document.
        """
        payload = {"severity": record.levelname, "message": record.getMessage()}
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload)


def get_logger(name: str) -> logging.Logger:
    """Return a logger that writes JSON lines to stdout.

    Args:
        name: Logger name, usually the module's `__name__`.

    Returns:
        A logger isolated from uvicorn's plain-text root configuration.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        # shortcut: fixed INFO ignores LOG_LEVEL; wire it in once settings
        # load at startup instead of at import time.
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
