"""
Logging configuration for the trading bot.

Outputs to console and a rotating trading_bot.log file (5MB limit).
Optional request_id via contextvars (set from HTTP middleware or jobs).
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from webapp.redaction import redact_sensitive_text

LOG_FILE = "trading_bot.log"
MAX_BYTES = 5 * 1024 * 1024  # 5MB
BACKUP_COUNT = 3

# Default log dir: same as this module
LOG_DIR = Path(__file__).resolve().parent

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdFilter(logging.Filter):
    """Injects request_id from contextvars into LogRecord for %(request_id)s."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id_var.get()
        record.request_id = rid if rid else "-"
        return True


class RedactionFilter(logging.Filter):
    """Mask token-like payloads before they hit any sink."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_sensitive_text(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: redact_sensitive_text(str(v)) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(redact_sensitive_text(str(v)) for v in record.args)
                else:
                    record.args = (redact_sensitive_text(str(record.args)),)
        except Exception:
            return True
        return True


def setup_logging(
    log_dir: Path | str | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """
    Configure Python logging to console and rotating file.
    Returns the root logger.
    """
    log_path = Path(log_dir or LOG_DIR) / LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(request_id)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    req_filt = RequestIdFilter()
    redact_filt = RedactionFilter()

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    ch.addFilter(req_filt)
    ch.addFilter(redact_filt)
    root.addHandler(ch)

    # Rotating file (5MB)
    fh = RotatingFileHandler(
        log_path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    fh.addFilter(req_filt)
    fh.addFilter(redact_filt)
    root.addHandler(fh)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call setup_logging() first if needed."""
    return logging.getLogger(name)
