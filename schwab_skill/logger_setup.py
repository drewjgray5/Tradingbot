"""
Logging configuration for the trading bot.

Outputs to console and a rotating trading_bot.log file (5MB limit).
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE = "trading_bot.log"
MAX_BYTES = 5 * 1024 * 1024  # 5MB
BACKUP_COUNT = 3

# Default log dir: same as this module
LOG_DIR = Path(__file__).resolve().parent


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
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
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
    root.addHandler(fh)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call setup_logging() first if needed."""
    return logging.getLogger(name)
