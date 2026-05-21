from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# LogRecord attributes that are not user-supplied extras.
_STDLIB_ATTRS = frozenset({
    "name", "msg", "args", "created", "relativeCreated", "levelname", "levelno",
    "pathname", "filename", "module", "funcName", "lineno", "thread", "threadName",
    "process", "processName", "exc_info", "exc_text", "stack_info", "msecs",
    "message", "taskName", "asctime",
})


class _StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.getMessage()  # populate record.message
        payload: dict = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key in _STDLIB_ATTRS or key.startswith("_"):
                continue
            try:
                json.dumps(val)
                payload[key] = val
            except (TypeError, ValueError):
                payload[key] = str(val)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StructuredFormatter())
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers = [handler]


logger = logging.getLogger("trustgraph_cloud")
