"""Structured JSON logs, with the correlation id attached automatically.

The rule: **one line, one event, one JSON object.** Nothing else. A log
pipeline that has to guess where a multi-line stack trace starts and ends will
guess wrong, and a traceback split across forty lines in Loki is forty
separate log entries that each look like garbage.

The id is pulled from the ContextVar at format time rather than passed as an
argument, because the caller will forget. An observability layer you have to
remember to use is one that has holes exactly where the incident is.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import traceback

from .correlation import current

# Everything LogRecord carries by default. Anything NOT in here was passed by
# the caller via `extra=`, and should end up in the JSON. Enumerating the
# built-ins is the only reliable way to tell the two apart.
_BUILTIN = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "taskName", "thread", "threadName",
}

# Never log these, whatever key they arrive under. A credential in a log file
# is a credential in every backup of that log file, in your log vendor, and in
# whatever a support engineer pastes into a ticket.
REDACT_KEYS = frozenset(
    {"password", "passwd", "secret", "token", "api_key", "apikey", "authorization",
     "auth", "credential", "credentials", "private_key", "access_token",
     "refresh_token", "webhook_secret", "signature"}
)
REDACTED = "[redacted]"


def _scrub(value, depth: int = 0):
    """Recursively redact anything whose key looks sensitive.

    Depth-limited because a cyclic or deeply nested structure in a log call
    must not blow the stack -- logging is the thing that is supposed to still
    work when everything else is broken.
    """
    if depth > 6:
        return "[too deep]"
    if isinstance(value, dict):
        return {
            key: (REDACTED if key.lower() in REDACT_KEYS else _scrub(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item, depth + 1) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str = "n8n-estate"):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            # RFC 3339 with an explicit offset. A timestamp without a zone is
            # useless the moment you correlate across two hosts.
            "ts": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": current(),
        }

        for key, value in record.__dict__.items():
            if key not in _BUILTIN and not key.startswith("_"):
                payload[key] = _scrub(value)

        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            payload["error"] = {
                "type": exc_type.__name__ if exc_type else None,
                "message": str(exc_value),
                # Joined into ONE string. A traceback spread over 40 lines is
                # 40 log entries that each look like noise.
                "stack": "".join(traceback.format_exception(exc_type, exc_value, exc_tb))[-4000:],
            }

        # default=str so an un-serialisable object degrades to its repr rather
        # than raising inside the logger and losing the event entirely.
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure(level: str = "INFO", service: str = "n8n-estate", stream=None) -> logging.Logger:
    """Idempotent. Calling it twice does not double every log line."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service=service))
    root.addHandler(handler)
    return root
