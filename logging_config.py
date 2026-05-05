"""
Centralised logging configuration for Silicon to Software (S2S).

Usage:
    from logging_config import configure_logging
    configure_logging()          # call once at app startup

Features:
- JSON-structured output in production (parseable by log aggregators)
- Human-readable output in development
- Per-module log levels
- Phase context automatically included via LogRecord extras

Why: agent logs were previously invisible in the Streamlit UI and
scattered across print() calls. Now every log line carries:
  timestamp, level, logger name, phase, project_id, message.
"""

import logging
import os
import sys
from typing import Optional


class _PhaseFormatter(logging.Formatter):
    """Human-readable formatter that surfaces 'extra' context fields."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = []
        for key in ("project_id", "phase", "duration_s", "model"):
            val = getattr(record, key, None)
            if val is not None:
                extras.append(f"{key}={val}")
        if extras:
            return f"{base}  [{' '.join(extras)}]"
        return base


class _JsonFormatter(logging.Formatter):
    """Minimal JSON-lines formatter for log aggregation in production."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("project_id", "phase", "duration_s", "model", "phase_complete"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(
    level: Optional[str] = None,
    json_output: Optional[bool] = None,
) -> None:
    """
    Configure the root logger once. Call at the very start of main.py / app.py.

    Args:
        level: Override log level (defaults to LOG_LEVEL env var, then INFO).
        json_output: True = JSON lines, False = human-readable.
                     Defaults to True when APP_ENV=production.
    """
    env = os.environ.get("APP_ENV", "development")
    level_str = level or os.environ.get("LOG_LEVEL", "INFO")
    numeric_level = getattr(logging, level_str.upper(), logging.INFO)

    if json_output is None:
        json_output = (env == "production")

    formatter: logging.Formatter
    if json_output:
        formatter = _JsonFormatter()
    else:
        formatter = _PhaseFormatter(
            fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Quiet noisy third-party loggers
    for noisy in ("httpx", "httpcore", "anthropic", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("hardware_pipeline").setLevel(numeric_level)
